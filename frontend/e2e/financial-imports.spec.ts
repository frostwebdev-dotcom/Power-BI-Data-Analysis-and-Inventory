import { readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const ID = "00000000-0000-0000-0000-000000000042";
const REPLACEMENT_ID = "00000000-0000-0000-0000-000000000045";
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
const REVISION = {
  id: "00000000-0000-0000-0000-000000000043", import_id: ID, revision_number: 1,
  supersedes_revision_id: null, recorded_by_user_id: "operator", created_at: "2026-11-17T14:00:00Z",
  reason: "Apply approved SKU links", status: "REVIEW_REQUIRED", row_count: 52,
  linked_rows: 52, review_rows: 0, changed_rows: 1,
};

interface MockOptions {
  role?: string;
  duplicate?: boolean;
  conflict?: boolean;
  downloadFails?: boolean;
  batch?: unknown;
  rows?: unknown[];
  total?: number;
  bindingHistory?: typeof REVISION[];
  bindingChanges?: boolean;
  bindingStale?: boolean;
  replacementStale?: boolean;
}
async function mockFinancial(page: Page, options: MockOptions = {}) {
  const calls: { path: string; method: string; params: URLSearchParams; body: string }[] = [];
  let revisions = [...(options.bindingHistory ?? [])];
  let replacement: object | null = null;
  const batch = options.batch ?? (options.bindingChanges ? { ...BATCH, linked_rows: 51, review_rows: 1 } : BATCH);
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
      return route.fulfill({ status: options.duplicate ? 200 : 201, json: { batch, duplicate: options.duplicate ?? false } });
    }
    if (path === "/financial-imports") return route.fulfill({ json: { items: [batch], page: 1, page_size: 25, total: 1 } });
    if (path === "/financial-imports/" + ID) return route.fulfill({ json: replacement ? { ...(batch as object), replaced_by_import_id: REPLACEMENT_ID } : batch });
    if (path === "/financial-imports/" + REPLACEMENT_ID) return route.fulfill({ json: replacement ?? { ...BATCH, id: REPLACEMENT_ID, supersedes_import_id: ID, replacement_reason: "Correct reported product costs." } });
    if (path === "/financial-imports/" + ID + "/replacement-preview") {
      const number = Number(url.searchParams.get("page") ?? "1");
      return route.fulfill({ json: { preview_only: true, source_import_id: ID, preview_sha256: "d".repeat(64), original_filename: "corrected.xlsx", status: "REVIEW_REQUIRED", previous_row_count: 52, row_count: 51, linked_rows: 50, review_rows: 1, added_skus: 0, removed_skus: 1, page: number, page_size: 25,
        controls: BATCH.report_snapshot.controls.map((control) => ({ field: control.field, previous_total: control.reported_total, replacement_total: control.field === "cost" || control.field === "profit" ? "45" : control.reported_total, difference: control.field === "cost" ? "5" : control.field === "profit" ? "-5" : "0", replacement_detail_total: control.field === "cost" ? null : control.reported_total, replacement_check: control.field === "cost" ? "INCOMPLETE" : "MATCH" })),
        issues: BATCH.report_snapshot.issues, rows: [{ ...ROW, source_row_number: number === 1 ? 2 : 27, cost: null, computed_profit: null, review_reasons: ["MISSING_AMOUNT"], review_required: true }],
      } });
    }
    if (path === "/financial-imports/" + ID + "/replacements") {
      if (options.replacementStale) return route.fulfill({ status: 409, json: { error: { code: "financial_import_conflict", message: "Changed", details: { code: "REPLACEMENT_PROPOSAL_CHANGED" } } } });
      replacement = { ...BATCH, id: REPLACEMENT_ID, original_filename: "corrected.xlsx", supersedes_import_id: ID, replacement_reason: "Correct reported product costs." };
      return route.fulfill({ status: 201, json: { batch: replacement, duplicate: false } });
    }
    if (path === "/financial-imports/" + ID + "/binding-revisions/preview") return route.fulfill({ json: {
      import_id: ID, expected_revision_id: revisions[0]?.id ?? null, bindings_sha256: "c".repeat(64),
      status: "REVIEW_REQUIRED", row_count: 52, linked_rows: 52, review_rows: 0, purchasing_eligible: false,
      changed_rows: options.bindingChanges ? 1 : 0, page: 1, page_size: 25,
      changes: options.bindingChanges ? [{ source_row_number: 2, seller_sku: "0000123", before: { ...ROW, mapping_status: "UNRESOLVED", product_id: null, listing_id: null, mapping_snapshot: {}, review_reasons: ["MAPPING_UNRESOLVED"] }, after: ROW }] : [],
    } });
    if (path === "/financial-imports/" + ID + "/binding-revisions" && request.method() === "POST") {
      if (options.bindingStale) return route.fulfill({ status: 409, json: { error: { code: "financial_import_conflict", message: "Changed", details: { code: "BINDING_PROPOSAL_CHANGED" } } } });
      const revision = { ...REVISION, reason: (request.postDataJSON() as { reason: string }).reason };
      revisions = [revision, ...revisions];
      return route.fulfill({ status: 201, json: revision });
    }
    if (path === "/financial-imports/" + ID + "/binding-revisions") return route.fulfill({ json: { items: revisions, page: 1, page_size: 25, total: revisions.length } });
    if (path === "/financial-imports/" + ID + "/rows") {
      const number = Number(url.searchParams.get("page") ?? "1");
      const originalRow = { ...ROW, ...(options.bindingChanges ? { mapping_status: "UNRESOLVED", listing_id: null, product_id: null, review_required: true } : {}), source_row_number: number === 1 ? 2 : 27 };
      return route.fulfill({ json: { items: url.searchParams.has("binding_revision_id") ? [{ ...ROW, binding_revision_id: url.searchParams.get("binding_revision_id") }] : options.rows ?? [originalRow], page: number, page_size: 25, total: options.total ?? 52 } });
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
  await expect(page.locator(".financial-imports").getByRole("alert")).toContainText("A report already covers this period");
  await expect(page).not.toHaveURL(/import=/);
});

test("download errors are shown without losing the report", async ({ page }) => {
  await mockFinancial(page, { downloadFails: true });
  await page.goto("/financial-imports?import=" + ID);
  await page.getByRole("button", { name: "Download original workbook" }).click();
  await expect(page.locator(".financial-imports").getByRole("alert")).toContainText("The original file could not be verified");
  await expect(page.getByTestId("financial-controls")).toBeVisible();
});

test("invalid time zone is rejected before upload and mobile form fits the viewport", async ({ page }) => {
  const calls = await mockFinancial(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/financial-imports");
  await fillUpload(page);
  await page.getByLabel("Report time zone").fill("Definitely/Invalid");
  await page.getByRole("button", { name: "Import financial report", exact: true }).click();
  await expect(page.locator(".financial-imports").getByRole("alert")).toContainText("reporting time zone used in ConnectBooks");
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
    await expect(page.locator(".financial-imports").getByRole("alert")).toContainText("nonempty Excel .xlsx file up to 8 MiB");
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

test("reviewed SKU link changes require a reason and acknowledgement before recording", async ({ page }, testInfo) => {
  const calls = await mockFinancial(page, { bindingChanges: true });
  await page.goto("/financial-imports?import=" + ID);
  await page.getByRole("button", { name: "Preview current SKU links", exact: true }).click();
  const preview = page.getByTestId("financial-binding-preview");
  await expect(preview).toContainText("1 changed rows");
  const record = preview.getByRole("button", { name: "Record SKU link revision", exact: true });
  await expect(record).toBeDisabled();
  await page.getByLabel("Reason for SKU link revision").fill("Apply the mappings approved after upload.");
  await expect(record).toBeDisabled();
  await page.getByLabel("I have reviewed the proposed SKU link changes").check();
  await expect(record).toBeEnabled();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: testInfo.outputPath("financial-binding-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: testInfo.outputPath("financial-binding-mobile.png"), fullPage: true });
  await record.click();
  await expect(page.getByTestId("financial-selected-revision")).toContainText("Selected revision 1");
  await expect(preview).toHaveCount(0);
  const saved = calls.find((call) => call.method === "POST" && call.path.endsWith("/binding-revisions"));
  expect(JSON.parse(saved?.body ?? "{}")).toEqual({ expected_revision_id: null, bindings_sha256: "c".repeat(64), reason: "Apply the mappings approved after upload." });
  await expect.poll(() => calls.some((call) => call.params.get("binding_revision_id") === REVISION.id)).toBeTruthy();
  await expect(page.getByTestId("financial-batch-checks")).toContainText("Advertising cost coverage has not been confirmed");
});

test("stale SKU link preview is discarded and requires a fresh preview", async ({ page }) => {
  const calls = await mockFinancial(page, { bindingChanges: true, bindingStale: true });
  await page.goto("/financial-imports?import=" + ID);
  await page.getByRole("button", { name: "Preview current SKU links", exact: true }).click();
  await page.getByLabel("Reason for SKU link revision").fill("Apply reviewed approved links.");
  await page.getByLabel("I have reviewed the proposed SKU link changes").check();
  await page.getByRole("button", { name: "Record SKU link revision", exact: true }).click();
  await expect(page.locator(".financial-imports").getByRole("alert")).toContainText("Preview the current links again");
  await expect(page.getByTestId("financial-binding-preview")).toHaveCount(0);
  expect(calls.filter((call) => call.method === "POST" && call.path.endsWith("/binding-revisions"))).toHaveLength(1);
});

test("unchanged links cannot create a revision", async ({ page }) => {
  await mockFinancial(page);
  await page.goto("/financial-imports?import=" + ID);
  await page.getByRole("button", { name: "Preview current SKU links", exact: true }).click();
  await expect(page.getByTestId("financial-binding-preview")).toContainText("No SKU links have changed");
  await expect(page.getByRole("button", { name: "Record SKU link revision", exact: true })).toHaveCount(0);
});

test("viewers inspect historical links with row filters reset when changing revision", async ({ page }) => {
  const calls = await mockFinancial(page, { role: "VIEWER", bindingHistory: [REVISION], rows: [{ ...ROW, mapping_status: "UNRESOLVED", product_id: null, listing_id: null, review_required: true }] });
  await page.goto("/financial-imports?import=" + ID);
  await expect(page.getByRole("button", { name: "Preview current SKU links", exact: true })).toHaveCount(0);
  await page.getByLabel("Only rows needing review").check();
  await page.getByLabel("SKU link status").selectOption("UNRESOLVED");
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await page.getByRole("button", { name: "Show revision 1", exact: true }).click();
  await expect(page.getByTestId("financial-selected-revision")).toContainText("This revision still requires review");
  await expect(page.getByLabel("Only rows needing review")).not.toBeChecked();
  await expect(page.getByLabel("SKU link status")).toHaveValue("");
  await expect.poll(() => calls.filter((call) => call.path.endsWith("/rows")).at(-1)?.params.get("page")).toBe("1");
  expect(calls.filter((call) => call.path.endsWith("/rows")).at(-1)?.params.get("binding_revision_id")).toBe(REVISION.id);
  await expect(page.getByTestId("financial-rows").getByText("linked", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Show original links", exact: true }).click();
  await expect(page.getByTestId("financial-binding-revisions")).toContainText("Showing original import links");
  // Returning to the original query can reuse its immutable cached rows.
  await expect(page.getByTestId("financial-rows").getByText("unresolved", { exact: true })).toBeVisible();
  expect(calls.some((call) => call.method === "POST")).toBeFalsy();
});

async function correctedFile(page: Page, name = "corrected.xlsx") {
  await page.getByLabel("Corrected source workbook").setInputFiles({ name, mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: FIXTURE });
}

test("corrected source preview preserves context and requires reviewed reason before replacement", async ({ page }, testInfo) => {
  const calls = await mockFinancial(page);
  await page.goto("/financial-imports?import=" + ID);
  await correctedFile(page);
  await page.getByRole("button", { name: "Preview corrected report", exact: true }).click();
  const preview = page.getByTestId("financial-replacement-preview");
  await expect(preview).toContainText("52 previous rows → 51 replacement rows");
  await expect(preview).toContainText("1 removed seller SKUs");
  await expect(preview).toContainText("Unknown");
  await expect(preview).toContainText("Advertising cost coverage has not been confirmed");
  await expect(page.getByTestId("financial-replacement-controls").getByRole("row")).toHaveCount(12);
  const save = preview.getByRole("button", { name: "Record corrected report", exact: true });
  await expect(save).toBeDisabled();
  await page.getByLabel("Reason for source report replacement").fill("Correct reported product costs.");
  await expect(save).toBeDisabled();
  await page.getByLabel("I reviewed the changed totals, removed SKUs and remaining warnings").check();
  await expect(save).toBeEnabled();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: testInfo.outputPath("financial-replacement-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.screenshot({ path: testInfo.outputPath("financial-replacement-mobile.png"), fullPage: true });
  await save.click();
  await expect(page).toHaveURL(new RegExp("import=" + REPLACEMENT_ID));
  await expect(page.getByText("This report replaces an earlier source", { exact: false })).toBeVisible();
  const body = calls.find((call) => call.path.endsWith("/replacements") && call.method === "POST")?.body ?? "";
  expect(body).toContain('name="preview_sha256"'); expect(body).toContain("d".repeat(64));
  expect(body).toContain("Correct reported product costs."); expect(body).not.toContain('name="profile_json"');
  await page.getByRole("link", { name: "View the previous report", exact: true }).click();
  await expect(page.getByText("Historical report: this source was replaced", { exact: false })).toBeVisible();
  await expect(page.getByTestId("financial-source-replacement")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Preview current SKU links", exact: true })).toHaveCount(0);
});

test("changing a corrected file or receiving a stale preview clears the reviewed proposal", async ({ page }) => {
  const calls = await mockFinancial(page, { replacementStale: true });
  await page.goto("/financial-imports?import=" + ID);
  await correctedFile(page);
  await page.getByRole("button", { name: "Preview corrected report", exact: true }).click();
  await expect(page.getByTestId("financial-replacement-preview")).toBeVisible();
  await correctedFile(page, "another-correction.xlsx");
  await expect(page.getByTestId("financial-replacement-preview")).toHaveCount(0);
  await page.getByRole("button", { name: "Preview corrected report", exact: true }).click();
  await page.getByLabel("Reason for source report replacement").fill("Correct reported product costs.");
  await page.getByLabel("I reviewed the changed totals, removed SKUs and remaining warnings").check();
  await page.getByRole("button", { name: "Record corrected report", exact: true }).click();
  await expect(page.getByTestId("financial-source-replacement").getByRole("alert")).toContainText("Preview the corrected workbook again");
  await expect(page.getByTestId("financial-replacement-preview")).toHaveCount(0);
  expect(calls.filter((call) => call.path.endsWith("/replacements") && call.method === "POST")).toHaveLength(1);
});

test("viewer can filter to current sources and navigate superseded report history", async ({ page }) => {
  const calls = await mockFinancial(page, { role: "VIEWER", batch: { ...BATCH, replaced_by_import_id: REPLACEMENT_ID } });
  await page.goto("/financial-imports");
  await page.getByLabel("Only current source reports").check();
  await expect.poll(() => calls.filter((call) => call.path === "/financial-imports").at(-1)?.params.get("current_only")).toBe("true");
  await page.getByRole("link", { name: BATCH.original_filename, exact: true }).click();
  await expect(page.getByText("Historical report: this source was replaced", { exact: false })).toBeVisible();
  await expect(page.getByTestId("financial-source-replacement")).toHaveCount(0);
  await page.getByRole("link", { name: "Open its replacement", exact: true }).click();
  await expect(page).toHaveURL(new RegExp("import=" + REPLACEMENT_ID));
  await expect(page.getByRole("link", { name: "View the previous report", exact: true })).toBeVisible();
  expect(calls.some((call) => call.method === "POST")).toBeFalsy();
});

test("invalid corrected workbooks do not reach preview API", async ({ page }) => {
  const calls = await mockFinancial(page);
  await page.goto("/financial-imports?import=" + ID);
  for (const file of [{ name: "empty.xlsx", buffer: Buffer.alloc(0) }, { name: "report.csv", buffer: Buffer.from("x") }, { name: "huge.xlsx", buffer: Buffer.alloc(8 * 1024 * 1024 + 1) }]) {
    await page.getByLabel("Corrected source workbook").setInputFiles({ ...file, mimeType: "application/octet-stream" });
    await page.getByRole("button", { name: "Preview corrected report", exact: true }).click();
    await expect(page.getByTestId("financial-source-replacement").getByRole("alert")).toContainText("nonempty corrected Excel .xlsx file up to 8 MiB");
  }
  expect(calls.some((call) => call.method === "POST")).toBeFalsy();
});
