import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const productId = "11111111-1111-4111-8111-111111111111";
const exceptionId = "22222222-2222-4222-8222-222222222222";
const product = {
  id: productId, catalog_item_number: "456164", name: "Catalogue perfume", brand: "Test brand",
  manufacturer: null, description: null, pack_size: 1, unit_of_measure: "each", status: "ACTIVE",
  is_active: true, nineyard_last_seen_at: "2026-10-07T08:00:00Z",
  created_at: "2026-10-07T08:00:00Z", updated_at: "2026-10-07T08:00:00Z",
};
const listing = {
  id: "33333333-3333-4333-8333-333333333333", product_id: null, marketplace: "AMAZON",
  marketplace_id: "ATVPDKIKX0DER", seller_sku: "4-456164-SC-NK", asin: "B0TESTASIN",
  name: "Source perfume", listing_status: "ACTIVE", mapping_status: "UNMAPPED",
  mapping_method: null, approved_by_user_id: null, approved_at: null, is_active: true,
};
const exception = {
  id: exceptionId, source: "amazon", seller_sku: listing.seller_sku, vendor_sku: null,
  description: listing.name, reason: "NO_MATCH", status: "PENDING", vendor_id: null,
  row_number: null, age_hours: 1, deferred_until: null, candidates: [], listing,
  source_row: null, vendor_line: null, match_evaluations: { rules: [] },
  resolved_at: null, resolved_product: null, resolved_product_id: null, resolution_note: null,
};

async function mockApi(page: Page, role = "ADMIN") {
  await page.addInitScript(() => localStorage.setItem("prms.token", "test-credential"));
  let approved = false;
  const decisions: unknown[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/v1", "");
    const current = approved ? { ...exception, status: "APPROVED", resolved_product_id: productId, resolved_product: product } : exception;
    if (path === "/auth/config") return route.fulfill({ json: { mode: "dev" } });
    if (path === "/auth/me") return route.fulfill({ json: { user_id: productId, organization_id: productId, email: "admin@example.test", display_name: "Test user", roles: [role] } });
    if (path === "/health") return route.fulfill({ json: { status: "ok", service: "PRMS API", environment: "test", dependencies: [{ name: "postgresql", status: "ok" }] } });
    if (path === "/products") {
      const query = url.searchParams.get("q");
      return route.fulfill({ json: { items: query === "missing" ? [] : [product], total: query === "missing" ? 0 : 1, page: 1, page_size: 25 } });
    }
    if (path === `/products/${productId}`) return route.fulfill({ json: product });
    if (path === `/products/${productId}/identifiers`) return route.fulfill({ json: { items: [{ id: "upc", identifier_type: "UPC", raw_value: "012345678905", normalized_value: "00012345678905", source_system: "NINEYARD", is_active: true }], total: 1, page: 1, page_size: 25 } });
    if (path === `/products/${productId}/listings`) return route.fulfill({ json: { items: [{ ...listing, product_id: productId }], total: 1, page: 1, page_size: 25 } });
    if (path === `/exceptions/${exceptionId}/approve`) {
      decisions.push(request.postDataJSON());
      approved = true;
      return route.fulfill({ json: { exception: { ...current, status: "APPROVED" }, superseded_product_id: null } });
    }
    if (path === `/exceptions/${exceptionId}`) return route.fulfill({ json: current });
    if (path === "/exceptions") return route.fulfill({ json: { items: approved ? [] : [current], total: approved ? 0 : 1, page: 1, page_size: 25 } });
    return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 25 } });
  });
  return decisions;
}

test("catalogue lookup exposes identifiers and listings, and supplies watch selection", async ({ page }) => {
  await mockApi(page);
  await page.goto("/products");
  await page.getByLabel("Search catalogue").fill("012345678905");
  const lookup = page.waitForRequest((request) => new URL(request.url()).searchParams.get("q") === "012345678905");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  await lookup;
  await page.getByTestId("product-row-456164").getByRole("button", { name: "Details" }).click();
  await expect(page.getByTestId("product-identifiers")).toContainText("00012345678905");
  await expect(page.getByTestId("product-listings")).toContainText(listing.seller_sku);
  await page.getByRole("link", { name: "Watch this product" }).click();
  await expect(page.getByTestId("selected-product")).toContainText(product.name);
  await expect(page.getByTestId("watch-submit")).toBeEnabled();
});

test("Amazon no-match review requires a deliberate product selection before approval", async ({ page }) => {
  const decisions = await mockApi(page);
  await page.goto("/exceptions");
  const row = page.getByTestId(`exception-row-${listing.seller_sku}`);
  await expect(row).toContainText("amazon");
  await expect(row).toContainText(listing.name);
  await row.getByRole("button", { name: "Review" }).click();
  await expect(page.getByText("B0TESTASIN", { exact: true })).toBeVisible();
  const approve = page.getByTestId("approve-selected-product");
  await expect(approve).toBeDisabled();
  const picker = page.getByTestId("product-picker");
  await picker.getByLabel("Find a product").fill("missing");
  await picker.getByRole("button", { name: "Search products" }).click();
  await expect(picker.getByText("No products found")).toBeVisible();
  await expect(approve).toBeDisabled();
  await picker.getByLabel("Find a product").fill("456164");
  await picker.getByRole("button", { name: "Search products" }).click();
  await expect(picker.getByText(product.name)).toBeVisible();
  await expect(approve).toBeDisabled();
  expect(decisions).toHaveLength(0);
  await picker.getByRole("button", { name: "Select", exact: true }).click();
  await approve.click();
  await expect(page.getByTestId("decision-outcome")).toContainText("Approved");
  expect(decisions).toEqual([{ product_id: productId, supersede: false, note: null }]);
});

test("viewers can inspect Amazon context without approval controls", async ({ page }) => {
  await mockApi(page, "VIEWER");
  await page.goto("/exceptions");
  await page.getByTestId(`exception-row-${listing.seller_sku}`).getByRole("button", { name: "Open" }).click();
  await expect(page.getByText("B0TESTASIN", { exact: true })).toBeVisible();
  await expect(page.getByTestId("product-picker")).toHaveCount(0);
  await expect(page.getByTestId("approve-selected-product")).toHaveCount(0);
  await page.goto(`/watchlist?product_id=${productId}`);
  await expect(page.getByRole("heading", { name: "Watchlist", exact: true })).toBeVisible();
  await expect(page.getByTestId("watch-form")).toHaveCount(0);
});
