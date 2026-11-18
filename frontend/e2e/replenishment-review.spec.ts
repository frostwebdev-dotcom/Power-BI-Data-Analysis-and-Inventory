import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import type { ComparisonRequest, VendorComparison, SavedReviewItem, ReviewSummary } from "../src/lib/replenishment-review";

const FIXTURE = JSON.parse(readFileSync(join(process.cwd(), "e2e/fixtures/vendor-comparison.json"), "utf8")) as { comparison: VendorComparison; review_items: SavedReviewItem[]; summary: ReviewSummary };
const PRODUCT = FIXTURE.comparison.product_id;
const PRODUCT_ROW = { id: PRODUCT, catalog_item_number: "SYNTHETIC", name: "Synthetic product", brand: "Synthetic", status: "ACTIVE", is_active: true };
interface Options { role?: string; stale?: boolean; empty?: boolean; delay?: boolean }
async function mock(page: Page, options: Options = {}) {
  const calls: { path: string; method: string; body: Record<string, unknown> | null; params: URLSearchParams }[] = [];
  let posts = 0, release: (() => void) | undefined;
  await page.addInitScript(() => localStorage.setItem("prms.token", "synthetic-browser-credential"));
  await page.route("**/api/v1/**", async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname.replace("/api/v1", ""), body = req.postData() ? req.postDataJSON() as Record<string, unknown> : null;
    calls.push({ path, method: req.method(), body, params: url.searchParams });
    if (path === "/auth/config") return route.fulfill({ json: { mode: "dev" } });
    if (path === "/auth/me") return route.fulfill({ json: { user_id: "actor", organization_id: "org", email: "staff@example.test", display_name: "Review staff", roles: [options.role ?? "ADMIN"] } });
    if (path === "/health") return route.fulfill({ json: { status: "ok", dependencies: [] } });
    if (path === "/products") return route.fulfill({ json: { items: [PRODUCT_ROW], page: 1, page_size: 10, total: 1 } });
    if (path === "/products/" + PRODUCT) return route.fulfill({ json: PRODUCT_ROW });
    if (path === "/purchasing-policies") return route.fulfill({ json: { items: options.empty ? [] : FIXTURE.comparison.offers.map(offer => ({ policy_id: offer.policy_id, identity: offer.identity, head: offer.snapshot.approved_policy, approved: offer.snapshot.approved_policy })), page: Number(url.searchParams.get("page") ?? 1), page_size: 25, total: options.empty ? 0 : 2 } });
    if (path === "/financial-imports") return route.fulfill({ json: { items: [], page: 1, page_size: 25, total: 0 } });
    if (path === "/vendor-comparisons/preview") {
      posts++;
      if (options.delay && posts === 1) await new Promise<void>(resolve => { release = resolve; });
      if (options.stale) return route.fulfill({ status: 409, json: { error: { code: "purchasing_policy_conflict", details: { code: "POLICY_APPROVAL_CHANGED" } } } });
      const request = body as unknown as ComparisonRequest, result = structuredClone(FIXTURE.comparison);
      result.offers = result.offers.filter(offer => request.choices.some(choice => choice.policy_id === offer.policy_id));
      result.selected_count = result.offers.length; result.comparable_price_count = result.offers.length >= 2 ? result.offers.length : 0;
      result.price_comparison_status = result.offers.length >= 2 ? "AVAILABLE" : "UNAVAILABLE";
      result.requested_order = request.order_by; result.applied_order = result.offers.length >= 2 ? request.order_by : "VENDOR_NAME";
      if (result.applied_order === "VENDOR_NAME") result.offers.sort((a,b) => a.identity.vendor_name.localeCompare(b.identity.vendor_name));
      if (!request.financial) for (const offer of result.offers) { offer.snapshot.financial = null; offer.snapshot.profitability = "NOT_INTEGRATED"; }
      return route.fulfill({ json: result });
    }
    if (path === "/replenishment-review/summary") return route.fulfill({ json: options.empty ? { ...FIXTURE.summary, latest_saved_total: 0, current_approval_total: 0, approval_changed_total: 0, current_approval_blocked: 0, current_approval_review_required: 0, current_approval_no_reorder: 0, current_approval_positive_shortfall: 0 } : FIXTURE.summary });
    if (path === "/replenishment-review") {
      let items = options.empty ? [] : FIXTURE.review_items;
      const approval = url.searchParams.get("approval");
      if (approval === "CURRENT") items = items.filter(item => item.approval_current);
      if (approval === "CHANGED") items = items.filter(item => !item.approval_current);
      if (url.searchParams.get("status")) items = items.filter(item => item.status === url.searchParams.get("status"));
      return route.fulfill({ json: { items, page: Number(url.searchParams.get("page") ?? 1), page_size: 25, total: items.length ? 52 : 0 } });
    }
    if (path === "/dashboard") return route.fulfill({ json: { open_exceptions: 0, watchlist_active: 0, watchlist_in_stock: 0, watchlist_newly_available_7d: 0, newly_available_by_vendor: [], imports_running: 0, last_imports: [], amazon_syncs: [], nineyard_sync: null, generated_at: FIXTURE.summary.generated_at } });
    if (path === "/vendors") return route.fulfill({ json: { items: FIXTURE.comparison.offers.map(o => ({ id: o.identity.vendor_id, name: o.identity.vendor_name, code: o.identity.vendor_code })), page: 1, page_size: 10, total: 2 } });
    const vendor = FIXTURE.comparison.offers.find(o => path === "/vendors/" + o.identity.vendor_id);
    if (vendor) return route.fulfill({ json: { id: vendor.identity.vendor_id, name: vendor.identity.vendor_name, code: vendor.identity.vendor_code } });
    return route.fulfill({ status: 404, json: { error: { code: "unexpected_mock", message: path } } });
  });
  return { calls, release: () => release?.() };
}
async function selectOffers(page: Page) {
  await page.goto("/vendor-comparisons?product=" + PRODUCT);
  await page.getByLabel(/Alpha Supply · SYNTHETIC-SKU · approved version/).check();
  await page.getByLabel(/Zulu Supply · SYNTHETIC-SKU · approved version/).check();
}

test("explicit vendor selection and exact price context are reviewable on desktop/mobile", async ({ page }, info) => {
  const { calls } = await mock(page); await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/vendor-comparisons?product=" + PRODUCT);
  await expect(page.getByText("No offers selected.", { exact: true })).toBeVisible();
  expect(calls.filter(c => c.path === "/vendor-comparisons/preview")).toHaveLength(0);
  await selectOffers(page); await page.getByLabel("Comparison order", { exact: true }).selectOption("BASE_PRICE");
  await page.getByRole("button", { name: "Preview vendor comparison", exact: true }).click();
  await expect(page.getByTestId("vendor-comparison-result")).toContainText("Comparable prices");
  expect(calls.find(c => c.path === "/vendor-comparisons/preview")!.body).not.toHaveProperty("financial");
  expect((calls.find(c => c.path === "/vendor-comparisons/preview")!.body!.choices as unknown[])).toHaveLength(2);
  const rows = page.getByRole("region", { name: "Selected vendor offer comparison", exact: true }).locator("tbody tr");
  await expect(rows.first()).toContainText("Zulu Supply"); await expect(rows.first()).toContainText("blocked");
  await page.evaluate(() => window.scrollTo(0, 0)); await page.screenshot({ path: info.outputPath("vendor-comparison-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 }); await page.evaluate(() => window.scrollTo(0, 0));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("vendor-comparison-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: "Inspect Zulu Supply", exact: true }).click();
  await expect(page.getByTestId("compared-offer-detail")).toContainText("Current supplier commercial checks");
  await expect(page.getByRole("button", { name: "Record evaluation", exact: true })).toHaveCount(0);
});

test("changing selected offers or ordering clears the captured comparison", async ({ page }) => {
  await mock(page); await selectOffers(page); await page.getByRole("button", { name: "Preview vendor comparison", exact: true }).click();
  await expect(page.getByTestId("vendor-comparison-result")).toBeVisible();
  await page.getByLabel("Comparison order", { exact: true }).selectOption("BASE_PRICE");
  await expect(page.getByTestId("vendor-comparison-result")).toHaveCount(0);
  await page.getByRole("button", { name: "Preview vendor comparison", exact: true }).click();
  await expect(page.getByTestId("vendor-comparison-result")).toBeVisible();
  await page.getByLabel(/Alpha Supply · SYNTHETIC-SKU · approved version/).uncheck();
  await expect(page.getByTestId("vendor-comparison-result")).toHaveCount(0);
});

test("out-of-order comparison completion cannot replace a new selection", async ({ page }) => {
  const mocked = await mock(page, { delay: true }); await selectOffers(page);
  await page.getByRole("button", { name: "Preview vendor comparison", exact: true }).click();
  await expect.poll(() => mocked.calls.filter(c => c.path === "/vendor-comparisons/preview").length).toBe(1);
  await page.getByLabel(/Alpha Supply · SYNTHETIC-SKU · approved version/).uncheck(); mocked.release();
  await expect(page.getByTestId("vendor-comparison-result")).toHaveCount(0);
  await page.getByRole("button", { name: "Preview vendor comparison", exact: true }).click();
  await expect(page.getByTestId("vendor-comparison-result")).toContainText("Zulu Supply");
  await expect(page.getByTestId("vendor-comparison-result")).not.toContainText("Alpha Supply");
});

test("stale approval requires new explicit choices", async ({ page }) => {
  await mock(page, { stale: true }); await selectOffers(page); await page.getByRole("button", { name: "Preview vendor comparison", exact: true }).click();
  await expect(page.getByText("The selected approval or source changed. Reload choices and select the offers again.", { exact: true })).toBeVisible();
  await expect(page.getByText("No offers selected.", { exact: true })).toBeVisible();
});

test("saved queue filters status, approvals, literal search and pagination on desktop/mobile", async ({ page }, info) => {
  const { calls } = await mock(page); await page.setViewportSize({ width: 1280, height: 900 }); await page.goto("/replenishment-review");
  await page.getByLabel("Approval relation", { exact: true }).selectOption("ALL");
  await expect(page.getByRole("region", { name: "Latest saved replenishment evaluations", exact: true })).toContainText("Approval changed or revoked");
  await page.getByLabel("Search product, catalogue, vendor or supplier SKU", { exact: true }).fill("%_literal"); await page.getByRole("button", { name: "Search saved evaluations", exact: true }).click();
  await expect.poll(() => calls.filter(c => c.path === "/replenishment-review").at(-1)?.params.get("q")).toBe("%_literal");
  await page.getByLabel("Known positive shortfalls only", { exact: true }).check();
  await expect.poll(() => calls.filter(c => c.path === "/replenishment-review").at(-1)?.params.get("shortfall_only")).toBe("true");
  await page.evaluate(() => window.scrollTo(0, 0)); await page.screenshot({ path: info.outputPath("saved-review-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 }); await page.evaluate(() => window.scrollTo(0, 0));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("saved-review-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect.poll(() => calls.filter(c => c.path === "/replenishment-review").at(-1)?.params.get("page")).toBe("2");
  await page.getByLabel("Saved result", { exact: true }).selectOption("BLOCKED");
  await expect.poll(() => calls.filter(c => c.path === "/replenishment-review").at(-1)?.params.get("page")).toBe("1");
});

test("dashboard cards identify saved evidence and link to scoped review", async ({ page }, info) => {
  await mock(page, { role: "VIEWER" }); await page.setViewportSize({ width: 1280, height: 900 }); await page.goto("/");
  const overview = page.getByTestId("replenishment-overview");
  await expect(overview).toContainText("latest saved evaluation per policy");
  await expect(overview).toContainText("saved evaluations with changed approval");
  await page.evaluate(() => window.scrollTo(0, 0)); await page.screenshot({ path: info.outputPath("replenishment-dashboard-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 }); await page.evaluate(() => window.scrollTo(0, 0));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("replenishment-dashboard-mobile.png"), fullPage: true });
  await overview.getByRole("link", { name: /saved positive shortfalls/ }).click();
  await expect(page.getByLabel("Known positive shortfalls only", { exact: true })).toBeChecked();
});

test("empty comparisons and history remain explicit without automatic evaluation", async ({ page }) => {
  const { calls } = await mock(page, { empty: true }); await page.goto("/vendor-comparisons?product=" + PRODUCT);
  await expect(page.getByText("No policies found", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Preview vendor comparison", exact: true })).toHaveCount(0);
  await page.goto("/replenishment-review"); await expect(page.getByText("No matching saved evaluations", { exact: true })).toBeVisible();
  expect(calls.filter(c => c.method === "POST")).toHaveLength(0);
});
