import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const ID = "00000000-0000-0000-0000-000000000042";
const FIXTURE = Buffer.from(readFileSync(join(process.cwd(), "e2e/fixtures/financial-report.base64"), "utf8").trim(), "base64");
const PROFILE = {
  layout_version: "connectbooks-signed-pnl-xlsx-v1", sheet_name: "Main sheet",
  store_name: "Demo Store-Amazon", source_store_name: "Synthetic Store",
  marketplace_id: "ATVPDKIKX0DER", currency: "USD", reporting_month: 10,
  reporting_year: 2026, reporting_timezone: "UTC", advertising_coverage_confirmed: false,
};
const BATCH = {
  id: ID, original_filename: "synthetic-report.xlsx", raw_sha256: "a".repeat(64), profile_sha256: "b".repeat(64),
  marketplace_id: PROFILE.marketplace_id, store_name: PROFILE.store_name, currency: "USD",
  period_start: "2026-10-01T00:00:00Z", period_end: "2026-11-01T00:00:00Z",
  received_at: "2026-11-17T12:00:00Z", status: "REVIEW_REQUIRED", row_count: 52, linked_rows: 52,
  review_rows: 0, purchasing_eligible: false, profile_snapshot: PROFILE,
  report_snapshot: {
    issues: [{ code: "ADVERTISING_COVERAGE_UNCONFIRMED", field: "ad_spend", severity: "REVIEW" }],
    unallocated_rows: 0,
    controls: [["sales_qty", "10"], ["refund_qty", "-2"], ["total_units", "8"], ["sales", "100"],
      ["refunds", "-20"], ["adjustments", "30"], ["net_sales", "110"], ["fees", "-15"],
      ["ad_spend", "5"], ["cost", "40"], ["profit", "50"]].map(([field, value]) => ({
        field, detail_total: value, reported_total: value, difference: "0", status: "MATCH",
      })),
  },
};
const ROW = {
  id: "row-2", import_id: ID, source_row_number: 2, seller_sku: "0000123", row_kind: "SKU",
  mapping_status: "LINKED", listing_id: "listing", product_id: "product", match_priority: 4,
  mapping_snapshot: { approved_at: "2026-11-17T00:00:00Z", approved_by_user_id: "approver", mapping_method: "MANUAL_APPROVAL" },
  source_snapshot: {
    source_item: "0000123", asin: "B000000001",
    amounts: { sales_qty: "10", refund_qty: "-2", total_units: "8", net_sales: "110", cost: "40", fees: "-15", ad_spend: "5", profit: "50" },
    issues: [], reported_margin_percent: "45", reported_roi_percent: "125",
    computed_margin_percent: "45.4545454545454545", computed_roi_percent: "125",
  },
  review_reasons: [], review_required: false, purchasing_eligible: false,
  net_sales: "110.0000000000000000", fees: "-15.0000000000000000", cost: "40.0000000000000000",
  ad_spend: "5.0000000000000000", reported_profit: "50.0000000000000000", computed_profit: "50.0000000000000000",
};

interface MockOptions {
  role?: string;
  duplicate?: boolean;
  conflict?: boolean;
  downloadFails?: boolean;
  batch?: unknown;
  rows?: unknown[];
  total?: number;
}
async function mockFinancial(page: Page, options: MockOptions = {}) {
  const calls: { path: string; method: string; params: URLSearchParams; body: string }[] = [];
  await page.addInitScript(() => localStorage.setItem("prms.token", "synthetic-browser-credential"));
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/v1", "");
    calls.push({ path, method: request.method(), params: url.searchParams, body: request.postData() ?? "" });
    if (path === "/auth/config") return route.fulfill({ json: { mode: "dev" } });
    if (path === "/auth/me") return route.fulfill({ json: { user_id: "user", organization_id: "org", email: "staff@example.test", display_name: "Staff", roles: [options.role ?? "ADMIN"] } });
    if (path === "/health") return route.fulfill({ json: { status: "ok", dependencies: [] } });
    if (path === "/financial-imports" && request.method() === "POST") {
      if (options.conflict) return route.fulfill({ status: 409, json: { error: { code: "financial_import_conflict", message: "Conflict", details: { code: "OVERLAPPING_REPORTING_PERIOD" } } } });
      return route.fulfill({ status: options.duplicate ? 200 : 201, json: { batch: options.batch ?? BATCH, duplicate: options.duplicate ?? false } });
    }
    if (path === "/financial-imports") return route.fulfill({ json: { items: [options.batch ?? BATCH], page: 1, page_size: 25, total: 1 } });
    if (path === "/financial-imports/" + ID) return route.fulfill({ json: options.batch ?? BATCH });
    if (path === "/financial-imports/" + ID + "/rows") {
      const number = Number(url.searchParams.get("page") ?? "1");
      return route.fulfill({ json: { items: options.rows ?? [{ ...ROW, source_row_number: number === 1 ? 2 : 27 }], page: number, page_size: 25, total: options.total ?? 52 } });
    }
    if (path === "/financial-imports/" + ID + "/raw") {
      if (options.downloadFails) return route.fulfill({ status: 409, json: { error: { code: "financial_import_conflict", details: { code: "RAW_INTEGRITY_MISMATCH" }, message: "Integrity check failed" } } });
      return route.fulfill({ body: FIXTURE, contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers: { "content-disposition": "attachment; filename=\"synthetic-report.xlsx\"" } });
    }
    return route.fulfill({ json: { items: [], total: 0 } });
  });
  return calls;
}

async function fillUpload(page: Page) {
  await page.getByLabel("Store name in PRMS").fill(PROFILE.store_name);
  await page.getByLabel("Store value in report").fill(PROFILE.source_store_name);
  await page.getByLabel("Marketplace").selectOption(PROFILE.marketplace_id);
  await page.getByLabel("Report month").selectOption("10");
  await page.getByLabel("Report year").fill("2026");
  await page.getByLabel("Report time zone").fill("UTC");
  await page.getByLabel("Original Excel workbook").setInputFiles({ name: "synthetic-report.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: FIXTURE });
}

test("financial upload starts without guessed dates and sends explicit context", async ({ page }) => {
  const calls = await mockFinancial(page);
  await page.goto("/financial-imports");
  await expect(page.getByLabel("Report year")).toHaveValue("");
  await expect(page.getByLabel("Report month")).toHaveValue("");
  await expect(page.getByLabel("Report time zone")).toHaveValue("");
  await expect(page.getByLabel("Advertising costs in this report are complete")).not.toBeChecked();
  await fillUpload(page);
  await page.getByRole("button", { name: "Import financial report", exact: true }).click();
  await expect(page).toHaveURL(new RegExp("import=" + ID));
  await expect(page.getByTestId("financial-controls")).toBeVisible();
  const body = calls.find((call) => call.path === "/financial-imports" && call.method === "POST")?.body;
  expect(body).toContain('"reporting_year":2026');
  expect(body).toContain('"reporting_month":10');
  expect(body).toContain('"reporting_timezone":"UTC"');
  expect(body).toContain('"advertising_coverage_confirmed":false');
  expect(body).toContain('name="file"');
  expect(calls.filter((call) => call.method === "POST")).toHaveLength(1);
});

test("batch review remains visible when row review count is zero and viewer cannot upload", async ({ page }, testInfo) => {
  await mockFinancial(page, { role: "VIEWER" });
  await page.goto("/financial-imports");
  await expect(page.getByTestId("financial-upload")).toHaveCount(0);
  await page.getByRole("link", { name: BATCH.original_filename, exact: true }).click();
  await expect(page.getByText("Review required. Check batch warnings", { exact: false })).toBeVisible();
  await expect(page.getByTestId("financial-batch-checks")).toContainText("Advertising cost coverage has not been confirmed");
  await expect(page.getByTestId("financial-summary").locator(".kv__row").filter({ hasText: "Rows needing review" })).toContainText("0");
  await expect(page.getByTestId("financial-controls").getByRole("row")).toHaveCount(12);
  await page.screenshot({ path: testInfo.outputPath("financial-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("heading", { name: "Financial totals", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: testInfo.outputPath("financial-mobile.png"), fullPage: true });
});

test("unknown amounts, signed scientific decimals, exact large values and unallocated rows remain distinct", async ({ page }) => {
  const financialRow = { ...ROW, cost: null, net_sales: "123456789012345678.0000000000000001", fees: "-1.234E-5", ad_spend: "0E-16",
    reported_profit: null, computed_profit: null, review_required: true, review_reasons: ["MISSING_AMOUNT"],
    source_snapshot: { ...ROW.source_snapshot, computed_roi_percent: null, reported_roi_percent: "0",
      amounts: { ...ROW.source_snapshot.amounts, cost: null }, issues: [{ code: "MISSING_AMOUNT", field: "cost", severity: "REVIEW" }] } };
  const unallocated = { ...ROW, id: "unallocated", source_row_number: 3, seller_sku: null, row_kind: "UNALLOCATED", mapping_status: "UNALLOCATED", product_id: null, listing_id: null, match_priority: null, review_required: true, review_reasons: ["MAPPING_UNALLOCATED"] };
  const zeroCost = { ...ROW, id: "zero-cost", source_row_number: 4, cost: "0E-16", computed_profit: "90", reported_profit: "90", review_required: true, review_reasons: ["ZERO_COST_WITH_POSITIVE_SALES"],
    source_snapshot: { ...ROW.source_snapshot, amounts: { ...ROW.source_snapshot.amounts, cost: "0" }, computed_roi_percent: null, reported_roi_percent: "0", issues: [{ code: "ZERO_COST_WITH_POSITIVE_SALES", field: "cost", severity: "REVIEW" }] } };
  await mockFinancial(page, { rows: [financialRow, unallocated, zeroCost], total: 3 });
  await page.goto("/financial-imports?import=" + ID);
  const row = page.getByTestId("financial-rows").getByRole("row").nth(1);
  await expect(row).toContainText("0000123");
  await expect(row).toContainText("123,456,789,012,345,678.0000000000000001");
  await expect(row).toContainText("-0.00001234");
  await expect(row.getByRole("cell").nth(4)).toHaveText("Unknown");
  await expect(row.getByRole("cell").nth(6)).toHaveText("0.00");
  await expect(page.getByText("Unitemized (unallocated)", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Review source row 2" }).click();
  const drawer = page.getByRole("dialog", { name: "Source row 2" });
  await expect(drawer.locator(".kv__row").filter({ hasText: "Calculated ROI (%)" })).toContainText("Unknown");
  await expect(drawer.locator(".kv__row").filter({ hasText: "Reported ROI (%)" })).toContainText("0");
  await drawer.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByRole("button", { name: "Review source row 4" }).click();
  const zeroDrawer = page.getByRole("dialog", { name: "Source row 4" });
  await expect(zeroDrawer).toContainText("Positive net sales with zero reported cost");
  await expect(zeroDrawer.locator(".kv__row").filter({ hasText: "Calculated ROI (%)" })).toContainText("Unknown");
});

test("review filters reset pagination and retain both filter values", async ({ page }) => {
  const calls = await mockFinancial(page);
  await page.goto("/financial-imports?import=" + ID);
  await expect(page.getByTestId("financial-rows")).toBeVisible();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect.poll(() => calls.filter((c) => c.path.endsWith("/rows")).at(-1)?.params.get("page")).toBe("2");
  await page.getByLabel("Only rows needing review").check();
  await expect.poll(() => calls.filter((c) => c.path.endsWith("/rows")).at(-1)?.params.get("page")).toBe("1");
  await page.getByLabel("SKU link status").selectOption("UNRESOLVED");
  await expect.poll(() => calls.filter((c) => c.path.endsWith("/rows")).at(-1)?.params.get("mapping_status")).toBe("UNRESOLVED");
  expect(calls.filter((c) => c.path.endsWith("/rows")).at(-1)?.params.get("review_only")).toBe("true");
});

test("duplicate upload opens existing record and overlap errors explain next action", async ({ page }) => {
  await mockFinancial(page, { duplicate: true });
  await page.goto("/financial-imports");
  await fillUpload(page);
  await page.getByRole("button", { name: "Import financial report", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "This file was already imported" })).toBeVisible();
  await page.unroute("**/api/v1/**");
  await mockFinancial(page, { conflict: true });
  await page.goto("/financial-imports");
  await fillUpload(page);
  await page.getByRole("button", { name: "Import financial report", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("A report already covers this period");
  await expect(page).not.toHaveURL(/import=/);
});

test("download errors are shown without losing the report", async ({ page }) => {
  await mockFinancial(page, { downloadFails: true });
  await page.goto("/financial-imports?import=" + ID);
  await page.getByRole("button", { name: "Download original workbook" }).click();
  await expect(page.getByRole("alert")).toContainText("The original file could not be verified");
  await expect(page.getByTestId("financial-controls")).toBeVisible();
});

test("invalid time zone is rejected before upload and mobile form fits the viewport", async ({ page }) => {
  const calls = await mockFinancial(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/financial-imports");
  await fillUpload(page);
  await page.getByLabel("Report time zone").fill("Definitely/Invalid");
  await page.getByRole("button", { name: "Import financial report", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("reporting time zone used in ConnectBooks");
  expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
});

test("empty, oversized and non-XLSX files are refused before a request", async ({ page }) => {
  const calls = await mockFinancial(page);
  await page.goto("/financial-imports");
  await fillUpload(page);
  for (const file of [{ name: "report.txt", buffer: Buffer.from("text") },
    { name: "empty.xlsx", buffer: Buffer.alloc(0) },
    { name: "large.xlsx", buffer: Buffer.alloc(8 * 1024 * 1024 + 1) }]) {
    await page.getByLabel("Original Excel workbook").setInputFiles({ ...file, mimeType: "application/octet-stream" });
    await page.getByRole("button", { name: "Import financial report", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("nonempty Excel .xlsx file up to 8 MiB");
  }
  expect(calls.filter((call) => call.method === "POST")).toHaveLength(0);
});

test("synthetic financial report uploads through the real API and replays without duplication @live", async ({ page }) => {
  await page.goto("/financial-imports");
  await page.getByLabel("Email", { exact: true }).fill(process.env.E2E_ADMIN_EMAIL ?? "admin@example.test");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Financial imports", exact: true })).toBeVisible();
  await fillUpload(page);
  await page.getByRole("button", { name: "Import financial report", exact: true }).click();
  await expect(page).toHaveURL(/financial-imports\?import=[a-f0-9-]+(?:&duplicate=1)?$/);
  await expect(page.getByTestId("financial-controls").getByRole("row")).toHaveCount(12);
  await expect(page.getByTestId("financial-rows")).toContainText("0000123");
  await expect(page.getByTestId("financial-batch-checks")).toContainText("Advertising cost coverage has not been confirmed");
  const firstIdentifier = new URL(page.url()).searchParams.get("import");
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download original workbook" }).click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toBe("synthetic-report.xlsx");
  const downloadedPath = await download.path();
  if (!downloadedPath) throw new Error("The original workbook was not downloaded.");
  expect(readFileSync(downloadedPath)).toEqual(FIXTURE);
  await page.getByRole("link", { name: "All financial imports", exact: true }).click();
  await fillUpload(page);
  await page.getByRole("button", { name: "Import financial report", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "This file was already imported" })).toBeVisible();
  expect(new URL(page.url()).searchParams.get("import")).toBe(firstIdentifier);
  expect(new URL(page.url()).searchParams.get("duplicate")).toBe("1");
});
