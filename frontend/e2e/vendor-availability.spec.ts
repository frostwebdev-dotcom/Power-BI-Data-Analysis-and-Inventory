import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const vendor = {
  id: "pca", code: "PCA", name: "Perfume Center", is_active: true, status: "ACTIVE",
  currency: "USD", contact_email: "sales@example.test", contact_phone: "123",
  timezone: "America/New_York", default_lead_time_days: 7, notes: "Keep saved notes",
  minimum_order_quantity: 12, minimum_order_value: "100.00",
  purchasing_terms: { payment: "Net 30", freight: "FOB", inventory_cadence: "daily", special: "Keep special terms" },
};
const entry = {
  id: "watch", vendor_id: null, product: { id: "product", name: "Fragrance", catalog_item_number: "FR-1" },
  current_status: "IN_STOCK", priority: "NORMAL", is_active: true,
  available_vendors: [{ id: "pca", code: "PCA", name: "Perfume Center" }, { id: "qkd", code: "QKD", name: "QKD Supplies" }],
};

async function mockApp(page: Page, role = "ADMIN") {
  const calls: { path: string; method: string; params: URLSearchParams; body: Record<string, unknown> | null }[] = [];
  await page.addInitScript(() => localStorage.setItem("prms.token", "test-credential"));
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/v1", "");
    const body = request.postData() ? request.postDataJSON() as Record<string, unknown> : null;
    calls.push({ path, method: request.method(), params: url.searchParams, body });
    if (path === "/auth/config") return route.fulfill({ json: { mode: "dev" } });
    if (path === "/auth/me") return route.fulfill({ json: {
      user_id: "user", organization_id: "org", email: "user@example.test", display_name: "User", roles: [role],
    } });
    if (path === "/health") return route.fulfill({ json: { status: "ok", dependencies: [] } });
    if (path === "/vendors") return route.fulfill({ json: { items: [vendor], total: 1, page: 1, page_size: 25 } });
    if (path === "/vendors/pca") return route.fulfill({ json: { ...vendor, ...body } });
    if (path === "/vendors/bulk-setup") {
      const invalid = String(body?.csv_text).includes("Duplicate");
      return route.fulfill({ json: {
        ready: !invalid, applied: !invalid && body?.apply === true, created: body?.apply ? 1 : 0, existing: 1,
        rows: [
          { row_number: 2, code: "PCA", name: "Perfume Center", action: "existing", issues: ["Existing settings preserved."] },
          { row_number: 3, code: "NYB", name: "NYB International", action: invalid ? "invalid" : "create", issues: invalid ? ["Duplicate vendor code or name in this file."] : [] },
        ],
      } });
    }
    if (path === "/dashboard") return route.fulfill({ json: {
      open_exceptions: 0, watchlist_active: 1, watchlist_in_stock: 1, watchlist_newly_available_7d: 2,
      imports_running: 0, last_imports: [], amazon_syncs: [], nineyard_sync: null,
      newly_available_by_vendor: [{ vendor_id: "pca", vendor_code: "PCA", vendor_name: "Perfume Center", event_count: 2 }],
    } });
    if (path === "/availability/events") return route.fulfill({ json: {
      items: [{ id: "event", vendor_id: "qkd", vendor_code: "QKD", vendor_name: "QKD Supplies", vendor_sku: "Q-1", product_name: "Fragrance", event_type: "BECAME_AVAILABLE", detected_at: "2026-10-22T12:00:00Z", previous_quantity: "0", new_quantity: "8" }],
      total: 1, page: 1, page_size: 50,
    } });
    if (path === "/watchlist") return route.fulfill({ json: { items: [entry], total: 1, page: 1, page_size: 25 } });
    if (path === "/watchlist/watch/history") return route.fulfill({ json: {
      entry, history: [{ id: "history", previous_status: "OUT_OF_STOCK", new_status: "IN_STOCK", vendor_name: "QKD Supplies", vendor_code: "QKD", changed_at: "2026-10-22T12:00:00Z" }],
    } });
    return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 50 } });
  });
  return calls;
}

test("restock links filter the feed and source names do not depend on the vendor dropdown", async ({ page }) => {
  const calls = await mockApp(page, "VIEWER");
  await page.goto("/");
  await expect(page.getByTestId("restock-vendors")).toContainText("Perfume Center");
  await page.getByTestId("restock-vendors").getByRole("link", { name: "View items" }).click();
  await expect(page.getByLabel("Vendor", { exact: true })).toHaveValue("pca");
  await expect(page.getByTestId("watchlist-only")).toBeChecked();
  await expect.poll(() => calls.filter((call) => call.path === "/availability/events").at(-1)?.params.get("vendor_id")).toBe("pca");
  await expect(page.getByTestId("availability-events")).toContainText("Available from QKD Supplies");
});

test("any-vendor watches list available suppliers and history names the source", async ({ page }) => {
  await mockApp(page, "VIEWER");
  await page.goto("/watchlist");
  const row = page.getByTestId("watch-row-FR-1");
  await expect(row).toContainText("any vendor");
  await expect(row).toContainText("Perfume Center");
  await expect(row).toContainText("QKD Supplies");
  await row.getByRole("button", { name: "History" }).click();
  await expect(page.getByTestId("watch-history")).toContainText("Source vendor");
  await expect(page.getByTestId("watch-history")).toContainText("QKD Supplies");
});

test("vendor roster previews before creating and preserves existing vendors", async ({ page }) => {
  const calls = await mockApp(page);
  await page.goto("/vendors");
  await page.getByRole("button", { name: "Set up vendor list" }).click();
  await page.getByLabel("Vendor list CSV").setInputFiles({ name: "vendors.csv", mimeType: "text/csv", buffer: Buffer.from("code,name\nPCA,Perfume Center\nNYB,NYB International\n") });
  expect(calls.filter((call) => call.path === "/vendors/bulk-setup")).toHaveLength(0);
  await page.getByRole("button", { name: "Preview vendor list" }).click();
  await expect(page.getByTestId("vendor-roster-preview")).toContainText("Already configured");
  expect(calls.filter((call) => call.path === "/vendors/bulk-setup").at(-1)?.body?.apply).toBe(false);
  await page.getByRole("button", { name: "Create 1 vendors" }).click();
  await expect(page.getByText("1 vendors created; 1 existing vendors preserved.")).toBeVisible();
  expect(calls.filter((call) => call.path === "/vendors/bulk-setup").at(-1)?.body?.apply).toBe(true);
  await expect(page.getByRole("button", { name: "Create 1 vendors" })).toBeDisabled();
});

test("invalid roster blocks creation and viewer cannot access setup controls", async ({ page }) => {
  await mockApp(page);
  await page.goto("/vendors");
  await page.getByRole("button", { name: "Set up vendor list" }).click();
  await page.getByLabel("Vendor list CSV").setInputFiles({ name: "vendors.csv", mimeType: "text/csv", buffer: Buffer.from("code,name\nNYB,Duplicate\n") });
  await page.getByRole("button", { name: "Preview vendor list" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "No vendors were created" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Create 0 vendors" })).toBeDisabled();
  await page.unroute("**/api/v1/**");
  await mockApp(page, "VIEWER");
  await page.goto("/vendors");
  await expect(page.getByRole("heading", { name: "Vendors", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Set up vendor list" })).toHaveCount(0);
});

test("editing a vendor preserves lead time, notes and saved purchasing terms", async ({ page }) => {
  const calls = await mockApp(page);
  await page.goto("/vendors");
  await page.getByTestId("vendor-row-PCA").getByRole("button", { name: "Edit", exact: true }).click();
  await expect(page.getByLabel("Default lead time (days)")).toHaveValue("7");
  await expect(page.getByLabel("Notes")).toHaveValue("Keep saved notes");
  await page.getByLabel("Name", { exact: true }).fill("Perfume Center Updated");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect.poll(() => calls.find((call) => call.path === "/vendors/pca")?.body?.name).toBe("Perfume Center Updated");
  const body = calls.find((call) => call.path === "/vendors/pca")!.body!;
  expect(body.default_lead_time_days).toBe(7);
  expect(body.notes).toBe("Keep saved notes");
  expect(body.purchasing_terms).toEqual(vendor.purchasing_terms);
});
