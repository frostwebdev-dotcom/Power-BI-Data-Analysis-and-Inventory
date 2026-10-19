import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

async function mockAvailability(page: Page) {
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
    if (path === "/vendors") return route.fulfill({ json: {
      items: [{ id: "pca", code: "PCA", name: "PCA" }], total: 1, page: 1, page_size: 200,
    } });
    if (path === "/availability/events") {
      requests.push(url.searchParams);
      const query = url.searchParams.get("q");
      return route.fulfill({ json: {
        items: query === "missing" ? [] : [{
          id: "event", vendor_id: "pca", vendor_sku: "I0088099",
          event_type: "BECAME_AVAILABLE", detected_at: "2026-10-18T20:29:07Z",
          product_name: "Nautica Voyage", catalog_item_number: "NAU-531908",
          previous_quantity: "0", new_quantity: "1000",
          oos_watchlist_id: null, over_max_unit_cost: null,
        }],
        total: query === "missing" ? 0 : query ? 1 : 51,
        page: Number(url.searchParams.get("page") ?? 1), page_size: 50,
      } });
    }
    return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 50 } });
  });
  return requests;
}

test("availability search resets pagination and preserves vendor, watch and date filters", async ({ page }) => {
  const requests = await mockAvailability(page);
  await page.goto("/availability");
  await expect(page.getByTestId("availability-events")).toBeVisible();
  await page.getByLabel("Vendor", { exact: true }).selectOption("pca");
  await page.getByTestId("watchlist-only").check();
  await page.getByLabel("Since", { exact: true }).fill("2026-10-15T09:00");
  await expect.poll(() => requests.at(-1)?.get("since")).toBeTruthy();
  const since = requests.at(-1)?.get("since");
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect.poll(() => requests.at(-1)?.get("page")).toBe("2");

  await page.getByRole("searchbox", { name: "Search availability" }).fill("  NAU-531908  ");
  await page.getByRole("searchbox", { name: "Search availability" }).press("Enter");
  await expect.poll(() => requests.at(-1)?.get("q")).toBe("NAU-531908");
  const search = requests.at(-1)!;
  expect(search.get("page")).toBe("1");
  expect(search.get("vendor_id")).toBe("pca");
  expect(search.get("watchlist_only")).toBe("true");
  expect(search.get("since")).toBe(since);
  await expect(page.getByTestId("availability-events")).toContainText("I0088099");
  await expect(page.getByText("1 availability change matching “NAU-531908”")).toBeVisible();

  await page.getByRole("button", { name: "Clear search", exact: true }).click();
  await expect(page.getByRole("searchbox", { name: "Search availability" })).toHaveValue("");
  await expect(page.getByRole("button", { name: "Next", exact: true })).toBeVisible();
  await expect(page.getByText("51 availability changes", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Vendor", { exact: true })).toHaveValue("pca");
  await expect(page.getByTestId("watchlist-only")).toBeChecked();
});

test("search button supports vendor SKU and UPC and explains an empty event history", async ({ page }) => {
  const requests = await mockAvailability(page);
  await page.goto("/availability");
  const search = page.getByRole("searchbox", { name: "Search availability" });
  for (const query of ["I0088099", "031655531908", "Nautica", "missing"]) {
    await search.fill(query);
    await page.getByRole("button", { name: "Search", exact: true }).click();
    await expect.poll(() => requests.at(-1)?.get("q")).toBe(query);
  }
  await expect(page.getByText("No matching availability changes", { exact: true })).toBeVisible();
  await expect(page.getByTestId("availability-events")).toHaveCount(0);
  await expect(page.getByText(/A product may have inventory without a recorded availability change/)).toBeVisible();
  await page.getByRole("button", { name: "Clear search", exact: true }).click();
  await expect(page.getByTestId("availability-events")).toBeVisible();
});
