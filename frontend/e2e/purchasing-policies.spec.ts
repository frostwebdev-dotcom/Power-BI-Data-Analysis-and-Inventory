import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import type { PolicyDefinition, PolicyRevision, PolicyReview, PolicySummary, ReviewAction } from "../src/lib/purchasing-policies";

const EXAMPLE = JSON.parse(readFileSync(join(process.cwd(), "../backend/examples/purchasing-policy-request.json"), "utf8")) as { definition: PolicyDefinition; vendor_product_id: string };
const DEFINITION = EXAMPLE.definition, PRODUCT = DEFINITION.amazon.product_id, LISTING = DEFINITION.amazon.listing_units[0]!.listing_id;
const ALIAS = "22222222-2222-4222-8222-222222222223", POLICY = "44444444-4444-4444-8444-444444444444";
const TIME = "2026-11-17T18:00:00Z";
const IDENTITY = { catalog_item_number: "DEMO-POLICY", product_name: "Synthetic policy product", vendor_id: "55555555-5555-4555-8555-555555555555", vendor_name: "Synthetic supplier", vendor_code: "POLICY", vendor_sku: "SUPPLIER-6" };
const PRODUCT_ROW = { id: PRODUCT, catalog_item_number: IDENTITY.catalog_item_number, name: IDENTITY.product_name, brand: "Synthetic", status: "ACTIVE", is_active: true, pack_size: 99, unit_of_measure: "piece", nineyard_last_seen_at: TIME };
const LISTINGS = [LISTING, ALIAS].map((id, i) => ({ id, product_id: PRODUCT, seller_sku: i ? "PACK-6" : "SINGLE-1", asin: "B000000001", marketplace: "AMAZON", marketplace_id: "ATVPDKIKX0DER", mapping_status: "APPROVED", mapping_method: "MANUAL_APPROVAL", approved_at: TIME, approved_by_user_id: "actor", listing_status: "ACTIVE", is_active: true, name: "Synthetic listing" }));
const DEF: PolicyDefinition = { ...DEFINITION, amazon: { ...DEFINITION.amazon, listing_units: [
  { listing_id: LISTING, base_units_per_listing_unit: 1, confirmation_reference: "Synthetic single-unit confirmation" },
  { listing_id: ALIAS, base_units_per_listing_unit: 6, confirmation_reference: "Synthetic pack conversion confirmation" },
], stock_pools: [{ pool_id: "warehouse", channel: "FBM", listing_ids: [LISTING, ALIAS], stock_source_listing_id: LISTING, distinct_stock_reference: "Synthetic shared warehouse stock confirmation" }] } };
function version(number: number, kind: PolicyRevision["kind"] = "DRAFT", definition: PolicyDefinition = DEF): PolicyRevision {
  return { id: number === 1 ? POLICY : "66666666-6666-4666-8666-" + String(number).padStart(12, "0"), policy_id: POLICY, product_id: PRODUCT, vendor_product_id: EXAMPLE.vendor_product_id, marketplace_id: "ATVPDKIKX0DER", revision_number: number, supersedes_revision_id: number === 1 ? null : versionId(number-1), reviewed_revision_id: kind === "APPROVED" ? versionId(number-1) : null, kind, definition: structuredClone(definition), definition_sha256: "a".repeat(64), review_context: null, context_sha256: null, recorded_by_user_id: "actor", recorded_at: TIME, reason: "Synthetic policy version " + number, purchasing_eligible: false };
}
function versionId(number: number) { return number === 1 ? POLICY : "66666666-6666-4666-8666-" + String(number).padStart(12, "0"); }
interface Options { role?: string; empty?: boolean; approvedDraft?: boolean; staleReview?: boolean; staleDraft?: boolean; blocked?: string[]; tooManyListings?: boolean; manyVersions?: boolean; manyPolicies?: boolean }
async function mock(page: Page, options: Options = {}) {
  const calls: { path: string; method: string; params: URLSearchParams; body: Record<string, unknown> | null }[] = [];
  let versions = options.empty ? [] : [version(1)];
  if (options.approvedDraft) versions = [version(3, "DRAFT", { ...DEF, quantity: { ...DEF.quantity, target_days: 21 } }), version(2, "APPROVED"), version(1)];
  if (options.manyVersions) versions = Array.from({ length: 12 }, (_, i) => version(12-i, "DRAFT", { ...DEF, quantity: { ...DEF.quantity, target_days: 12-i } }));
  function summary(): PolicySummary { const decision = versions.find(v => v.kind !== "DRAFT"); return { identity: IDENTITY, policy_id: POLICY, head: versions[0]!, approved: decision?.kind === "APPROVED" ? decision : null, purchasing_eligible: false, current_data_verified: false }; }
  await page.addInitScript(() => localStorage.setItem("prms.token", "synthetic-browser-credential"));
  await page.route("**/api/v1/**", async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname.replace("/api/v1", "");
    const body = request.postData() ? request.postDataJSON() as Record<string, unknown> : null;
    calls.push({ path, method: request.method(), params: url.searchParams, body });
    if (path === "/auth/config") return route.fulfill({ json: { mode: "dev" } });
    if (path === "/auth/me") return route.fulfill({ json: { user_id: "actor", organization_id: "org", email: "staff@example.test", display_name: "Policy reviewer", roles: [options.role ?? "ADMIN"] } });
    if (path === "/health") return route.fulfill({ json: { status: "ok", dependencies: [] } });
    if (path === "/products") return route.fulfill({ json: { items: [PRODUCT_ROW], total: 1, page: 1, page_size: 10 } });
    if (path === "/products/" + PRODUCT) return route.fulfill({ json: PRODUCT_ROW });
    if (path.endsWith("/listings")) return route.fulfill({ json: { items: LISTINGS, total: options.tooManyListings ? 201 : 2, page: 1, page_size: 200 } });
    if (path.endsWith("/vendor-products")) return route.fulfill({ json: { items: [{ id: EXAMPLE.vendor_product_id, product_id: PRODUCT, vendor_id: IDENTITY.vendor_id, vendor_name: IDENTITY.vendor_name, vendor_code: IDENTITY.vendor_code, vendor_sku: IDENTITY.vendor_sku, is_active: true, vendor_is_active: true, mapping_status: "APPROVED", mapping_method: "MANUAL_APPROVAL", approved_by_user_id: "actor", approved_at: TIME }], page: 1, page_size: 10, total: 1 } });
    if (path === "/purchasing-policies" && request.method() === "POST") { versions = [version(1, "DRAFT", body!.definition as PolicyDefinition)]; return route.fulfill({ status: 201, json: summary() }); }
    if (path === "/purchasing-policies") return route.fulfill({ json: { items: versions.length ? [summary()] : [], page: Number(url.searchParams.get("page") ?? 1), page_size: 25, total: versions.length ? options.manyPolicies && !url.searchParams.has("product_id") ? 52 : 1 : 0 } });
    if (path.endsWith("/review-preview")) {
      const action = url.searchParams.get("action") as ReviewAction;
      const target = action === "APPROVE" ? versions[0]! : summary().approved!;
      const json: PolicyReview = { action, policy_id: POLICY, expected_head_id: versions[0]!.id, reviewed_revision_id: target.id, definition: target.definition, definition_sha256: "a".repeat(64), context_sha256: "b".repeat(64), review_context: { product: { id: PRODUCT, catalog_item_number: IDENTITY.catalog_item_number, name: IDENTITY.product_name, is_active: true }, vendor: { id: IDENTITY.vendor_id, is_active: true }, vendor_product: { vendor_sku: IDENTITY.vendor_sku, mapping_status: "APPROVED", mapping_approved_at: TIME }, listings: LISTINGS }, review_allowed: !options.blocked?.length, blocking_reasons: options.blocked ?? [], purchasing_eligible: false };
      return route.fulfill({ json });
    }
    if (path.endsWith("/reviews")) {
      if (options.staleReview) return route.fulfill({ status: 409, json: { error: { code: "purchasing_policy_conflict", details: { code: "POLICY_REVIEW_CHANGED" } } } });
      const target = body!.action === "APPROVE" ? versions[0]! : summary().approved!;
      const next = { ...version(versions[0]!.revision_number + 1, body!.action === "APPROVE" ? "APPROVED" : "REVOKED", target.definition), reviewed_revision_id: target.id, reason: String(body!.reason) };
      versions = [next, ...versions]; return route.fulfill({ status: 201, json: summary() });
    }
    if (path.endsWith("/revisions") && request.method() === "POST") {
      if (options.staleDraft) { versions = [version(versions[0]!.revision_number+1, "DRAFT", { ...DEF, quantity: { ...DEF.quantity, target_days: 28 } }), ...versions]; return route.fulfill({ status: 409, json: { error: { code: "purchasing_policy_conflict", details: { code: "POLICY_HEAD_CHANGED" } } } }); }
      versions = [{ ...version(versions[0]!.revision_number+1, "DRAFT", body!.definition as PolicyDefinition), reason: String(body!.reason) }, ...versions]; return route.fulfill({ status: 201, json: summary() });
    }
    if (path.endsWith("/revisions")) { const p = Number(url.searchParams.get("page") ?? 1); return route.fulfill({ json: { items: versions.slice((p-1)*10, p*10), page: p, page_size: 10, total: versions.length } }); }
    if (path === "/purchasing-policies/" + POLICY) return route.fulfill({ json: summary() });
    return route.fulfill({ json: { items: [], total: 0 } });
  });
  return calls;
}
async function startNew(page: Page) {
  await page.goto("/purchasing-policies?new=1");
  await page.getByLabel("Find a product", { exact: true }).fill("DEMO-POLICY");
  await page.getByRole("button", { name: "Search products", exact: true }).click();
  await page.getByRole("button", { name: "Select", exact: true }).click();
  await page.getByRole("button", { name: "Use SKU SUPPLIER-6", exact: true }).click();
}
async function values(page: Page) {
  await page.getByLabel("Marketplace", { exact: true }).selectOption("ATVPDKIKX0DER");
  await page.getByLabel("Demand basis", { exact: true }).selectOption("FULLY_SHIPPED_ONLY");
  await page.getByLabel("Demand window", { exact: true }).selectOption("14");
  for (const [label, value] of [["Target coverage days", "14"], ["Lead time days", "2"], ["Safety days", "1"], ["Minimum order (base units)", "0"], ["Order multiple (base units)", "6"], ["Maximum sales-data lag (seconds)", "7200"], ["Maximum inventory age (seconds)", "3600"]]) await page.getByLabel(label!, { exact: true }).fill(value!);
  await page.getByLabel("Coverage interpretation", { exact: true }).selectOption("target-plus-lead-plus-safety");
  await page.getByLabel("Inbound handling", { exact: true }).selectOption("CONFIRMED_BEFORE_STOCKOUT_ONLY");
  await page.getByLabel("Reason for this draft", { exact: true }).fill("Record the explicit synthetic policy proposal.");
}

test("new policy starts blank and preserves unknown conversions as null", async ({ page }) => {
  const calls = await mock(page, { empty: true }); await startNew(page);
  for (const label of ["Marketplace", "Demand window", "Target coverage days", "Lead time days", "Maximum inventory age (seconds)", "Base units per supplier order unit"]) await expect(page.getByLabel(label, { exact: true })).toHaveValue("");
  await values(page);
  await expect(page.getByLabel("Base units per listing unit · SINGLE-1", { exact: true })).toHaveValue("");
  await page.getByRole("button", { name: "Save draft", exact: true }).click();
  await expect(page.getByTestId("policy-editing-head")).toContainText("Version 1");
  const sent = calls.find(c => c.method === "POST" && c.path === "/purchasing-policies")!.body!;
  const def = sent.definition as PolicyDefinition;
  expect(def.vendor_base_units_per_order_unit).toBeNull(); expect(def.coverage_confirmation_reference).toBeNull();
  expect(def.amazon.listing_units.map(u => u.base_units_per_listing_unit)).toEqual([null, null]);
  expect(def.amazon.stock_pools).toEqual([]); expect(def.quantity.minimum_order_units).toBe(0);
  expect(calls.find(c => c.path.endsWith("/listings"))!.params.get("marketplace_id")).toBe("ATVPDKIKX0DER");
});

test("shared pool editor saves membership and one authoritative source on desktop and mobile", async ({ page }, info) => {
  const calls = await mock(page, { empty: true }); await page.setViewportSize({ width: 1280, height: 900 }); await startNew(page); await values(page);
  await page.getByLabel("Coverage confirmation", { exact: true }).fill("Synthetic manager confirms target excludes lead and safety.");
  await page.getByLabel("Base units per supplier order unit", { exact: true }).fill("6");
  await page.getByLabel("Supplier unit evidence", { exact: true }).fill("Synthetic supplier case contains six pieces.");
  for (const [sku, factor] of [["SINGLE-1", "1"], ["PACK-6", "6"]]) { await page.getByLabel("Base units per listing unit · " + sku, { exact: true }).fill(factor!); await page.getByLabel("Conversion evidence · " + sku, { exact: true }).fill("Synthetic conversion confirmed for " + sku); }
  await page.getByRole("button", { name: "Add stock pool", exact: true }).click();
  await page.getByLabel("Pool name · 1", { exact: true }).fill("main-warehouse");
  await page.getByLabel("Fulfillment channel · 1", { exact: true }).selectOption("FBM");
  const pool = page.locator('[aria-label="Stock pool 1"]');
  await pool.getByLabel("SINGLE-1", { exact: true }).check(); await pool.getByLabel("PACK-6", { exact: true }).check();
  await page.getByLabel("Authoritative stock listing · 1", { exact: true }).selectOption(LISTING);
  await page.getByLabel("Distinct stock evidence · 1", { exact: true }).fill("Synthetic shared stock: authoritative single-unit listing.");
  await page.screenshot({ path: info.outputPath("policy-editor-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("policy-editor-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: "Save draft", exact: true }).click();
  await expect(page.getByTestId("policy-editing-head")).toBeVisible();
  const def = calls.find(c => c.method === "POST" && c.path === "/purchasing-policies")!.body!.definition as PolicyDefinition;
  expect(def.amazon.stock_pools[0]).toEqual({ pool_id: "main-warehouse", channel: "FBM", listing_ids: [LISTING, ALIAS], stock_source_listing_id: LISTING, distinct_stock_reference: "Synthetic shared stock: authoritative single-unit listing." });
  expect(def.amazon.listing_units.map(u => u.base_units_per_listing_unit)).toEqual([1, 6]);
});

for (const role of ["VIEWER", "DATA_OPERATOR"]) test(role + " sees only permitted policy actions", async ({ page }) => {
  const calls = await mock(page, { role }); await page.goto("/purchasing-policies?policy=" + POLICY);
  await expect(page.getByTestId("policy-selected-definition")).toBeVisible();
  await expect(page.getByTestId("policy-review-panel")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Draft from this version", exact: true })).toHaveCount(role === "VIEWER" ? 0 : 1);
  expect(calls.filter(c => c.method === "POST")).toHaveLength(0);
});

test("approval reviews the current draft and requires a reason and acknowledgement", async ({ page }, info) => {
  const calls = await mock(page, { approvedDraft: true }); await page.setViewportSize({ width: 1280, height: 900 }); await page.goto("/purchasing-policies?policy=" + POLICY);
  await expect(page.getByTestId("policy-editing-head")).toContainText("Version 3"); await expect(page.getByTestId("policy-current-approval")).toContainText("Version 2");
  await page.getByRole("button", { name: "Preview approval of version 3", exact: true }).click();
  const preview = page.getByTestId("policy-review-preview"), save = preview.getByRole("button", { name: "Record policy approval", exact: true });
  await expect(save).toBeDisabled();
  await preview.getByLabel("Reason for manager decision", { exact: true }).fill("Manager reviewed this synthetic coverage change."); await expect(save).toBeDisabled();
  await preview.getByText("Identity and mapping evidence checked for this review", { exact: true }).click();
  await page.screenshot({ path: info.outputPath("policy-review-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 }); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("policy-review-mobile.png"), fullPage: true });
  await preview.getByLabel("I reviewed this complete saved definition and its current mapping evidence", { exact: true }).check(); await save.click();
  await expect(page.getByTestId("policy-current-approval")).toContainText("Version 4");
  expect(calls.find(c => c.path.endsWith("/reviews"))!.body).toMatchObject({ action: "APPROVE", expected_head_id: versionId(3), reviewed_revision_id: versionId(3), definition_sha256: "a".repeat(64), context_sha256: "b".repeat(64), acknowledge_review: true });
});

test("revocation targets the current approval even with a newer draft", async ({ page }) => {
  const calls = await mock(page, { approvedDraft: true }); await page.goto("/purchasing-policies?policy=" + POLICY);
  await page.getByRole("button", { name: "Preview revocation of version 2", exact: true }).click();
  const preview = page.getByTestId("policy-review-preview"); await expect(preview).toContainText("This revokes approved version 2");
  await expect(preview.locator(".kv__row").filter({ hasText: "Target coverage" })).toContainText("14 days");
  await preview.getByLabel("Reason for manager decision", { exact: true }).fill("Withdraw approval until supplier terms are confirmed."); await preview.getByRole("checkbox").check();
  await preview.getByRole("button", { name: "Revoke policy approval", exact: true }).click();
  await expect(page.getByTestId("policy-current-approval")).toContainText("No current approval");
  expect(calls.find(c => c.path.endsWith("/reviews"))!.body).toMatchObject({ action: "REVOKE", expected_head_id: versionId(3), reviewed_revision_id: versionId(2) });
});

test("stale review clears the acknowledgement and requires a fresh preview", async ({ page }) => {
  const calls = await mock(page, { staleReview: true }); await page.goto("/purchasing-policies?policy=" + POLICY);
  await page.getByRole("button", { name: "Preview approval of version 1", exact: true }).click();
  await page.getByLabel("Reason for manager decision", { exact: true }).fill("Review this saved synthetic policy definition."); await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Record policy approval", exact: true }).click();
  await expect(page.getByTestId("policy-review-panel").getByRole("alert")).toContainText("review is no longer current"); await expect(page.getByTestId("policy-review-preview")).toHaveCount(0);
  await page.getByRole("button", { name: "Preview approval of version 1", exact: true }).click();
  await expect(page.getByRole("checkbox")).not.toBeChecked(); await expect(page.getByLabel("Reason for manager decision", { exact: true })).toHaveValue("");
  expect(calls.filter(c => c.path.endsWith("/reviews"))).toHaveLength(1);
});

test("stale draft keeps unsaved fields until the editor is explicitly closed", async ({ page }) => {
  const calls = await mock(page, { staleDraft: true }); await page.goto("/purchasing-policies?policy=" + POLICY);
  await page.getByRole("button", { name: "Draft from this version", exact: true }).click();
  await page.getByLabel("Target coverage days", { exact: true }).fill("21"); await page.getByLabel("Reason for this draft", { exact: true }).fill("Propose a synthetic increase in target coverage.");
  await page.getByRole("button", { name: "Save draft", exact: true }).click(); await expect(page.getByTestId("policy-editor").getByRole("alert")).toContainText("Your draft is still here");
  await expect(page.getByLabel("Target coverage days", { exact: true })).toHaveValue("21"); expect(calls.find(c => c.path.endsWith("/revisions") && c.method === "POST")!.body!.expected_head_id).toBe(POLICY);
  await page.getByRole("button", { name: "Cancel editing", exact: true }).click(); await page.getByRole("button", { name: "Draft from this version", exact: true }).click(); await expect(page.getByLabel("Target coverage days", { exact: true })).toHaveValue("28");
});

test("history selection and reload clear pending manager review", async ({ page }) => {
  await mock(page, { approvedDraft: true }); await page.goto("/purchasing-policies?policy=" + POLICY);
  await page.getByRole("button", { name: "Preview approval of version 3", exact: true }).click(); await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Inspect version 1", exact: true }).click(); await expect(page.getByTestId("policy-review-preview")).toHaveCount(0);
  await expect(page.getByTestId("policy-selected-definition")).toContainText("Selected historical version 1"); await expect(page.getByTestId("policy-current-approval")).toContainText("Version 2");
  await page.getByRole("button", { name: "Preview approval of version 3", exact: true }).click(); await page.getByRole("checkbox").check(); await page.getByRole("button", { name: "Reload policy", exact: true }).click(); await expect(page.getByTestId("policy-review-preview")).toHaveCount(0);
});

test("unknown assumptions explain approval blockers and incomplete listings block editing", async ({ page }) => {
  await mock(page, { blocked: ["UNIT_CONVERSION_UNCONFIRMED", "STOCK_POOLS_UNCONFIRMED"] }); await page.goto("/purchasing-policies?policy=" + POLICY); await page.getByRole("button", { name: "Preview approval of version 1", exact: true }).click();
  await expect(page.getByTestId("policy-review-preview")).toContainText("Confirm every Amazon listing conversion"); await expect(page.getByRole("button", { name: "Record policy approval", exact: true })).toHaveCount(0);
  await page.unroute("**/api/v1/**"); await mock(page, { tooManyListings: true }); await page.reload(); await page.getByRole("button", { name: "Draft from this version", exact: true }).click();
  await expect(page.getByTestId("policy-editor").getByRole("alert")).toContainText("complete Amazon listing set"); await expect(page.getByRole("button", { name: "Save draft", exact: true })).toBeDisabled();
});

test("policy and revision pagination stay bounded and product filter resets the page", async ({ page }) => {
  const calls = await mock(page, { manyVersions: true, manyPolicies: true }); await page.goto("/purchasing-policies"); await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect.poll(() => calls.filter(c => c.path === "/purchasing-policies").at(-1)!.params.get("page")).toBe("2");
  await page.getByText("Filter policies by product", { exact: true }).click(); await page.getByLabel("Find a product", { exact: true }).fill("DEMO"); await page.getByRole("button", { name: "Search products", exact: true }).click(); await page.getByRole("button", { name: "Select", exact: true }).click();
  await expect.poll(() => calls.filter(c => c.path === "/purchasing-policies").at(-1)!.params.get("page")).toBe("1");
  await page.getByTestId("policy-history").getByRole("link").click(); await page.getByTestId("policy-revision-history").getByRole("button", { name: "Next", exact: true }).click();
  await expect.poll(() => calls.filter(c => c.path.endsWith("/revisions")).at(-1)!.params.get("page")).toBe("2"); await expect(page.getByTestId("policy-revision-history")).toContainText("Inspect version 1");
});

test("unsafe or fractional quantities are refused before sending a draft", async ({ page }) => {
  const calls = await mock(page, { empty: true }); await startNew(page); await values(page);
  await page.getByLabel("Order multiple (base units)", { exact: true }).fill("1000000000000000000"); await page.getByRole("button", { name: "Save draft", exact: true }).click();
  await expect(page.getByTestId("policy-editor").getByRole("alert")).toContainText("Order multiple must be between");
  await page.getByLabel("Order multiple (base units)", { exact: true }).fill("1.5"); await page.getByRole("button", { name: "Save draft", exact: true }).click();
  expect(await page.getByLabel("Order multiple (base units)", { exact: true }).evaluate(input => (input as HTMLInputElement).validity.patternMismatch)).toBe(true);
  expect(calls.filter(c => c.method === "POST" && c.path === "/purchasing-policies")).toHaveLength(0);
});

test("synthetic policy draft and revision are retained through the real API @live", async ({ page }) => {
  const base = (process.env.E2E_API_BASE_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "") + "/api/v1";
  const login = await page.request.post(base + "/auth/dev-token", { data: { email: process.env.E2E_ADMIN_EMAIL ?? "admin@example.test" } });
  expect(login.ok()).toBe(true);
  const token = (await login.json() as { access_token: string }).access_token;
  const headers = { Authorization: "Bearer " + token };
  const suffix = Date.now().toString(36).toUpperCase(), sku = "POLICY-" + suffix;
  const vendor = await page.request.post(base + "/vendors", { headers, data: { code: "POL" + suffix, name: "Synthetic policy supplier " + suffix } });
  expect(vendor.ok()).toBe(true); const vendorId = (await vendor.json() as { id: string }).id;
  const profile = await page.request.post(base + "/vendors/" + vendorId + "/import-profiles", { headers, data: { name: "Synthetic policy choices", file_format: "CSV", column_map: { columns: [{ target: "vendor_sku", source: "SKU", required: true }, { target: "upc", source: "UPC" }, { target: "quantity_available", source: "Qty" }] } } });
  expect(profile.ok()).toBe(true); const profileId = (await profile.json() as { id: string }).id;
  const upload = await page.request.post(base + "/imports", { headers, multipart: { vendor_id: vendorId, profile_id: profileId, file: { name: "synthetic-policy.csv", mimeType: "text/csv", buffer: Buffer.from("SKU,UPC,Qty\n" + sku + ",00012345678905,0\n") } } });
  expect(upload.ok()).toBe(true);
  await page.addInitScript(value => localStorage.setItem("prms.token", value), token);
  await page.goto("/purchasing-policies?new=1"); await page.getByLabel("Find a product", { exact: true }).fill("DEMO-001"); await page.getByRole("button", { name: "Search products", exact: true }).click(); await page.getByRole("button", { name: "Select", exact: true }).click();
  await page.getByLabel("Find a supplier or SKU", { exact: true }).fill(sku); await page.getByRole("button", { name: "Search supplier SKUs", exact: true }).click(); await page.getByRole("button", { name: "Use SKU " + sku, exact: true }).click();
  await values(page); await page.getByRole("button", { name: "Save draft", exact: true }).click();
  await expect(page.getByTestId("policy-editing-head")).toContainText("Version 1"); const id = new URL(page.url()).searchParams.get("policy"); expect(id).toBeTruthy();
  const first = await page.request.get(base + "/purchasing-policies/" + id, { headers }); expect(first.ok()).toBe(true);
  const saved = await first.json() as PolicySummary;
  expect(saved.identity.vendor_sku).toBe(sku); expect(saved.approved).toBeNull(); expect(saved.head.definition.vendor_base_units_per_order_unit).toBeNull(); expect(saved.purchasing_eligible).toBe(false);
  await page.getByRole("button", { name: "Preview approval of version 1", exact: true }).click(); await expect(page.getByTestId("policy-review-preview")).toContainText("Approval cannot be recorded"); await expect(page.getByRole("button", { name: "Record policy approval", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Draft from this version", exact: true }).click(); await page.getByLabel("Target coverage days", { exact: true }).fill("21"); await page.getByLabel("Reason for this draft", { exact: true }).fill("Synthetic draft revision keeps unknown units explicit."); await page.getByRole("button", { name: "Save draft", exact: true }).click(); await expect(page.getByTestId("policy-editing-head")).toContainText("Version 2");
  const history = await page.request.get(base + "/purchasing-policies/" + id + "/revisions", { headers }); const revisions = await history.json() as { items: PolicyRevision[]; total: number };
  expect(revisions.total).toBe(2); expect(revisions.items.map(v => v.definition.quantity.target_days)).toEqual([21, 14]); expect(revisions.items.every(v => v.kind === "DRAFT" && !v.purchasing_eligible)).toBe(true);
});
