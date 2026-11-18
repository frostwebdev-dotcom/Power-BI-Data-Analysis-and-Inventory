import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import type { InboundContext, InboundPreview, InboundProposal, InboundRevision } from "../src/lib/inbound";
import type { EvaluationPreview } from "../src/lib/recommendations";
test.setTimeout(30_000);
test.use({ actionTimeout: 10_000 });
const BASE = JSON.parse(readFileSync(join(process.cwd(), "e2e/fixtures/recommendation-v5.json"), "utf8")) as EvaluationPreview;
const ROW = BASE.snapshot.inbound_assessments![0]!.revision, POLICY = ROW.policy_id;
interface Options { role?: string; ambiguous?: boolean; stale?: boolean; existing?: boolean; blocked?: boolean; delayed?: boolean }
async function mock(page: Page, options: Options = {}) {
  const calls: { path: string; method: string; body: Record<string, unknown> | null }[] = [];
  let head: InboundRevision | null = options.existing ? ROW : null, writes = 0, release: (() => void) | undefined;
  const policy = { policy_id: POLICY, head: BASE.snapshot.approved_policy, approved: BASE.snapshot.approved_policy, identity: { catalog_item_number: "SYNTHETIC-INBOUND", product_name: "Synthetic inbound product", vendor_name: "Synthetic supplier", vendor_sku: "SUPPLIER-6" } };
  await page.addInitScript(() => localStorage.setItem("prms.token", "synthetic-browser-credential"));
  await page.route("**/api/v1/**", async route => {
    const req = route.request(), path = new URL(req.url()).pathname.replace("/api/v1", ""), body = req.postData() ? req.postDataJSON() as Record<string, unknown> : null;
    calls.push({ path, method: req.method(), body });
    if (path === "/auth/config") return route.fulfill({ json: { mode: "dev" } });
    if (path === "/auth/me") return route.fulfill({ json: { user_id: "actor", organization_id: "org", email: "staff@example.test", display_name: "Inbound reviewer", roles: [options.role ?? "ADMIN"] } });
    if (path === "/health") return route.fulfill({ json: { status: "ok", dependencies: [] } });
    if (path === "/purchasing-policies/" + POLICY) return route.fulfill({ json: policy });
    if (path === "/purchasing-policies") return route.fulfill({ json: { items: [policy], page: 1, page_size: 25, total: 1 } });
    if (path === "/financial-imports") return route.fulfill({ json: { items: [], page: 1, page_size: 25, total: 0 } });
    if (path.endsWith("/inbound") && req.method() === "GET") return route.fulfill({ json: { items: head ? [head] : [], page: 1, page_size: 25, total: head ? 1 : 0 } });
    const context: InboundContext = { policy_id: POLICY, approved_revision_id: ROW.approved_revision_id, pool_id: "fba", channel: "FBA", evaluated_at: ROW.recorded_at, head_id: head?.id ?? null, source_context: ROW.source_context, source_sha256: ROW.source_sha256, blockers: [] };
    if (path.endsWith("/inbound/context")) return route.fulfill({ json: context });
    if (path.endsWith("/inbound/preview")) {
      if (options.delayed) await new Promise<void>(resolve => { release = resolve; });
      const preview: InboundPreview = { context, proposal: body as unknown as InboundProposal, can_confirm: !options.blocked, blockers: options.blocked ? ["FBA_INBOUND_TOTAL_MISMATCH"] : [], preview_sha256: "e".repeat(64) };
      return route.fulfill({ json: preview });
    }
    if (path.endsWith("/inbound") || path.endsWith("/inbound/revoke")) {
      writes++;
      if (options.stale) return route.fulfill({ status: 409, json: { error: { code: "inbound_evidence_conflict" } } });
      if (options.ambiguous && writes === 1) return route.abort("failed");
      head = path.endsWith("/revoke") ? { ...ROW, id: "55555555-5555-4555-8555-555555555555", kind: "REVOKED", revision_number: 2, previous_revision_id: ROW.id, reason: String(body!.reason) } : { ...ROW, position: (body as unknown as InboundProposal).position, reason: String(body!.reason) };
      return route.fulfill({ status: 201, json: head });
    }
    if (path.endsWith("/recommendation-preview")) return route.fulfill({ json: BASE });
    return route.fulfill({ status: 404, json: { error: { code: "unexpected_mock", message: path } } });
  });
  return { calls, release: () => release?.() };
}
async function begin(page: Page) {
  await page.goto("/inbound-evidence?policy=" + POLICY);
  await page.getByLabel("Stock pool to review", { exact: true }).selectOption("fba");
  await page.getByRole("button", { name: "Load current stock source", exact: true }).click();
}
async function fill(page: Page, zero = false) {
  await page.getByLabel("Declared total base units", { exact: true }).fill(zero ? "0" : "10");
  await page.getByLabel("Position observed at · ISO with timezone", { exact: true }).fill("2026-11-17T20:00:00Z");
  await page.getByLabel("Evidence expires at · ISO with timezone", { exact: true }).fill("2026-11-19T20:00:00Z");
  await page.getByLabel("Evidence reference", { exact: true }).fill("Synthetic complete inbound shipment manifest");
  if (!zero) {
    await page.getByRole("button", { name: "Add arrival", exact: true }).click();
    await page.getByLabel("Shipment reference 1", { exact: true }).fill("SYNTHETIC-ARRIVAL");
    await page.getByLabel("Arrival base units 1", { exact: true }).fill("10");
    await page.getByLabel("Expected arrival 1 · ISO with timezone", { exact: true }).fill("2026-11-18T20:00:00Z");
  }
  await page.getByLabel("This is the complete outstanding inbound position for this stock pool", { exact: true }).check();
  await page.getByLabel("These units are not already included in on-hand stock or another stock pool", { exact: true }).check();
}
async function preview(page: Page) { await page.getByRole("button", { name: "Preview inbound confirmation", exact: true }).click(); await expect(page.getByTestId("inbound-confirmation-preview")).toBeVisible(); }
async function acknowledge(page: Page) { await page.getByLabel("Confirmation reason", { exact: true }).fill("Reviewed complete synthetic position and manifest"); await page.getByLabel("I reviewed the complete position, source snapshot and arrival evidence", { exact: true }).check(); }

test("complete explicit position, acknowledgement and immutable ledger on desktop/mobile", async ({ page }, info) => {
  const { calls } = await mock(page); await page.setViewportSize({ width: 1280, height: 900 }); await begin(page);
  await expect(page.getByLabel("Declared total base units", { exact: true })).toBeEmpty();
  await fill(page); await preview(page);
  const save = page.getByRole("button", { name: "Confirm reviewed inbound", exact: true }); await expect(save).toBeDisabled(); await acknowledge(page);
  await page.evaluate(() => window.scrollTo(0, 0)); await page.screenshot({ path: info.outputPath("inbound-review-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 }); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.evaluate(() => window.scrollTo(0, 0)); await page.screenshot({ path: info.outputPath("inbound-review-mobile.png"), fullPage: true });
  await save.click(); await expect(page.getByRole("heading", { name: /fba · revision 1/ })).toBeVisible();
  const sent = calls.find(c => c.method === "POST" && c.path.endsWith("/inbound"))!.body!;
  expect(sent.expected_source_sha256).toBe(ROW.source_sha256); expect(sent.acknowledge_review).toBe(true); expect(sent.expected_head_id).toBeNull();
  await page.evaluate(() => window.scrollTo(0, 0)); await page.screenshot({ path: info.outputPath("inbound-ledger-mobile.png"), fullPage: true });
});

test("explicit zero is distinct from a blank or unsafe quantity", async ({ page }) => {
  const { calls } = await mock(page); await begin(page); await fill(page, true);
  await page.getByLabel("Declared total base units", { exact: true }).fill("9007199254740992");
  await page.getByRole("button", { name: "Preview inbound confirmation", exact: true }).click(); await expect(page.getByTestId("inbound-position-form")).toContainText("exact display range");
  expect(calls.filter(c => c.path.endsWith("/inbound/preview"))).toHaveLength(0);
  await page.getByLabel("Declared total base units", { exact: true }).fill("0"); await preview(page); await acknowledge(page);
  await page.getByRole("button", { name: "Confirm reviewed inbound", exact: true }).click();
  const body = calls.find(c => c.method === "POST" && c.path.endsWith("/inbound"))!.body as unknown as InboundProposal;
  expect(body.position.declared_total_base_units).toBe(0); expect(body.position.arrivals).toEqual([]);
});

for (const role of ["VIEWER", "DATA_OPERATOR"]) test(role + " reads sources but cannot confirm or revoke", async ({ page }) => {
  const { calls } = await mock(page, { role, existing: true }); await begin(page);
  await expect(page.getByText("You have read-only access. A purchasing manager must confirm or revoke inbound evidence.")).toBeVisible();
  await expect(page.getByTestId("inbound-position-form")).toHaveCount(0); await expect(page.getByText("Revoke this complete position", { exact: true })).toHaveCount(0);
  expect(calls.filter(c => c.method === "POST" && !c.path.endsWith("/context"))).toHaveLength(0);
});

test("ambiguous confirmation retries identical body and UUID", async ({ page }) => {
  const { calls } = await mock(page, { ambiguous: true }); await begin(page); await fill(page); await preview(page); await acknowledge(page);
  await page.getByRole("button", { name: "Confirm reviewed inbound", exact: true }).click();
  await expect(page.getByText(/Confirmation result is unconfirmed/)).toBeVisible(); await expect(page.getByLabel("Stock pool to review", { exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Retry same confirmation", exact: true }).click(); await expect(page.getByRole("heading", { name: /fba · revision 1/ })).toBeVisible();
  const writes = calls.filter(c => c.method === "POST" && c.path.endsWith("/inbound")); expect(writes).toHaveLength(2); expect(writes[0]!.body).toEqual(writes[1]!.body);
});

test("changed source clears preview and forces reload", async ({ page }) => {
  await mock(page, { stale: true }); await begin(page); await fill(page); await preview(page); await acknowledge(page);
  await page.getByRole("button", { name: "Confirm reviewed inbound", exact: true }).click();
  await expect(page.getByText(/Source, approval or head changed/)).toBeVisible(); await expect(page.getByTestId("inbound-confirmation-preview")).toHaveCount(0);
  await page.getByRole("button", { name: "Load current stock source", exact: true }).click(); await expect(page.getByLabel("Declared total base units", { exact: true })).toBeEmpty();
});

test("blocked reconciliation cannot be confirmed and edits clear review", async ({ page }) => {
  await mock(page, { blocked: true }); await begin(page); await fill(page); await preview(page); await acknowledge(page);
  await expect(page.getByRole("button", { name: "Confirm reviewed inbound", exact: true })).toBeDisabled();
  await page.getByLabel("Declared total base units", { exact: true }).fill("12"); await expect(page.getByTestId("inbound-confirmation-preview")).toHaveCount(0);
});

test("late preview response cannot restore a replaced review", async ({ page }) => {
  const controls = await mock(page, { delayed: true }); await begin(page); await fill(page);
  await page.getByRole("button", { name: "Preview inbound confirmation", exact: true }).click();
  await expect.poll(() => controls.calls.filter(c => c.path.endsWith("/inbound/preview")).length).toBe(1);
  await page.getByRole("button", { name: "Reload policy and inbound ledger", exact: true }).click(); controls.release();
  await expect(page.getByRole("button", { name: "Load current stock source", exact: true })).toBeVisible(); await expect(page.getByTestId("inbound-confirmation-preview")).toHaveCount(0);
});

test("reasoned revoke retries exact request and appends a new revision", async ({ page }) => {
  const { calls } = await mock(page, { existing: true, ambiguous: true }); await page.goto("/inbound-evidence?policy=" + POLICY);
  await page.getByText("Revoke this complete position", { exact: true }).click();
  await page.getByLabel("Revocation reason", { exact: true }).fill("Synthetic inbound manifest has been withdrawn");
  await page.getByLabel("I reviewed and want to revoke this inbound position", { exact: true }).check();
  await page.getByRole("button", { name: "Revoke inbound position", exact: true }).click();
  await expect(page.getByText(/Revocation result is unconfirmed/)).toBeVisible(); await page.getByRole("button", { name: "Retry same revocation", exact: true }).click();
  await expect(page.getByRole("heading", { name: /fba · revision 2/ })).toBeVisible(); const writes = calls.filter(c => c.path.endsWith("/revoke")); expect(writes[0]!.body).toEqual(writes[1]!.body);
});

test("replenishment opts into exact inbound revision and shows timing evidence", async ({ page }, info) => {
  const { calls } = await mock(page, { existing: true }); await page.goto("/replenishment-recommendations?policy=" + POLICY);
  await expect(page.getByRole("button", { name: "Preview evaluation", exact: true })).toBeVisible();
  expect(calls.filter(c => c.path.endsWith("/inbound"))).toHaveLength(0);
  await page.getByText("Optional reviewed inbound arrivals · 0 selected", { exact: true }).click();
  await page.getByLabel(/fba · version 1 · confirmed/).check(); await page.getByRole("button", { name: "Preview evaluation", exact: true }).click();
  await expect(page.getByTestId("reviewed-inbound-evidence")).toContainText("Manager reviewed complete position");
  await expect(page.getByRole("region", { name: "Reviewed arrival qualification", exact: true })).toContainText("after stockout");
  expect(calls.find(c => c.path.endsWith("/recommendation-preview"))!.body).toEqual({ expected_approved_revision_id: ROW.approved_revision_id, inbound: { revision_ids: [ROW.id] } });
  await page.evaluate(() => window.scrollTo(0, 0)); await page.screenshot({ path: info.outputPath("inbound-evaluation-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 }); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.evaluate(() => window.scrollTo(0, 0)); await page.screenshot({ path: info.outputPath("inbound-evaluation-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: "Reload inbound choices and clear selection", exact: true }).click(); await expect(page.getByTestId("evaluation-evidence")).toHaveCount(0);
});
