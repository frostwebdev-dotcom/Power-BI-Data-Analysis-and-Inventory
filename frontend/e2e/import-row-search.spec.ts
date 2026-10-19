import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

async function mockImport(page: Page) {
  const requests: URLSearchParams[] = [];
  await page.addInitScript(() => localStorage.setItem("prms.token", "test-credential"));
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/v1", "");
    if (path === "/auth/config") return route.fulfill({ json: { mode: "dev" } });
    if (path === "/auth/me") return route.fulfill({ json: {
      user_id: "user", organization_id: "org", email: "viewer@example.test",
      display_name: "Viewer", roles: ["VIEWER"],
    } });
    if (path === "/health") return route.fulfill({ json: { status: "ok", dependencies: [] } });
    if (path === "/imports/test-job") return route.fulfill({ json: {
      id: "test-job", status: "COMPLETED", current_stage: null, profile_version: 1,
      completed_at: "2026-10-18T20:29:07Z", import_file: {
        original_filename: "PCA_Inventory_2026-10-15.xlsx", uploaded_at: "2026-10-18T20:27:58Z",
        size_bytes: 100, detected_encoding: "utf-8", sha256: "test-hash",
      },
    } });
    if (path === "/imports/test-job/report") return route.fulfill({ json: {
      summary: "3,362 rows imported", counters: { processed: 3362 }, issues_by_code: {},
    } });
    if (path === "/imports/test-job/rows") {
      requests.push(url.searchParams);
      const query = url.searchParams.get("q");
      return route.fulfill({ json: {
        items: query === "missing" ? [] : [{
          id: "row", row_number: 762, status: "OK", vendor_sku: "I0088099",
          normalized_upc: "00031655531908", description: "Nautica Voyage",
          quantity: "1000", unit_cost: "11", availability_status: "AVAILABLE",
          normalized_data: { match: { result: "MATCHED", method: "VENDOR_SKU", priority: 3 } },
        }],
        total: query === "missing" ? 0 : query ? 1 : 3362,
        page: Number(url.searchParams.get("page") ?? 1), page_size: 50,
      } });
    }
    return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 50 } });
  });
  return requests;
}

test("import search finds a source row, resets pagination and keeps the status filter", async ({ page }) => {
  const requests = await mockImport(page);
  await page.goto("/imports?job=test-job");
  await expect(page.getByTestId("import-rows")).toBeVisible();
  await page.getByLabel("Row status", { exact: true }).selectOption("OK");
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect.poll(() => requests.at(-1)?.get("page")).toBe("2");
  const search = page.getByRole("searchbox", { name: "Search import rows" });
  await search.fill("  I0088099  ");
  await search.press("Enter");
  await expect.poll(() => requests.at(-1)?.get("q")).toBe("I0088099");
  expect(requests.at(-1)?.get("page")).toBe("1");
  expect(requests.at(-1)?.get("status")).toBe("OK");
  await expect(page.getByRole("status")).toContainText("1 row matching “I0088099” · ok");
  const row = page.getByTestId("import-rows").getByRole("row").nth(1);
  await expect(row).toContainText("I0088099");
  await expect(row).toContainText("1,000");
  await expect(row.getByRole("cell").nth(6)).toHaveText("11");
  await expect(row).toContainText("vendor sku (p3)");
  await page.getByRole("button", { name: "Clear search", exact: true }).click();
  await expect(search).toHaveValue("");
  await expect(page.getByLabel("Row status", { exact: true })).toHaveValue("OK");
  await expect(page.getByRole("status")).toContainText("3,362 rows · ok");
  await expect(page.getByTestId("import-report")).toContainText("3,362");
});

test("import search button accepts UPC and description and shows a clear empty state", async ({ page }) => {
  const requests = await mockImport(page);
  await page.goto("/imports?job=test-job");
  const search = page.getByRole("searchbox", { name: "Search import rows" });
  for (const query of ["031655531908", "Nautica", "missing"]) {
    await search.fill(query);
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await expect.poll(() => requests.at(-1)?.get("q")).toBe(query);
  }
  await expect(page.getByText("No matching import rows", { exact: true })).toBeVisible();
  await expect(page.getByTestId("import-rows")).toHaveCount(0);
  await page.getByRole("button", { name: "Clear search", exact: true }).click();
  await expect(page.getByTestId("import-rows")).toBeVisible();
});
