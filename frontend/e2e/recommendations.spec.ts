import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import type { EvaluationPreview, SavedEvaluation } from "../src/lib/recommendations";

const BASE = JSON.parse(readFileSync(join(process.cwd(), "e2e/fixtures/recommendation-v3.json"), "utf8")) as EvaluationPreview;
const POLICY = BASE.snapshot.policy_id, REPORT = BASE.snapshot.financial!.import_id, TIME = BASE.snapshot.evaluated_at;
const ID = "99999999-9999-4999-8999-999999999999";
interface Options { role?: string; stale?: boolean; ambiguous?: boolean; unsafe?: boolean; noApproval?: boolean; empty?: boolean }
async function mock(page: Page, options: Options = {}) {
  const calls: { path: string; body: Record<string, unknown> | null; params: URLSearchParams }[] = [];
  const preview = structuredClone(BASE);
  if (options.unsafe) preview.snapshot.pools[0]!.result.suggested_units = 9007199254740992;
  let attempts = 0, saved: SavedEvaluation | null = options.empty ? null : { ...preview, id: ID, request_id: ID, policy_id: POLICY, product_id: preview.snapshot.product_id, status: preview.snapshot.status, snapshot_sha256: "c".repeat(64), recorded_by_user_id: "actor", recorded_at: TIME, reason: "Synthetic reviewed evaluation", historical: true, purchasing_eligible: false };
  const policy = { policy_id: POLICY, head: preview.snapshot.approved_policy, approved: options.noApproval ? null : preview.snapshot.approved_policy, identity: { catalog_item_number: "SYNTHETIC-REVIEW", product_name: "Synthetic evaluation product", vendor_name: "Synthetic supplier", vendor_sku: "SUPPLIER-6" } };
  const financial = preview.snapshot.financial!;
  const report = { id: REPORT, ...financial, replaced_by_import_id: null };
  await page.addInitScript(() => localStorage.setItem("prms.token", "synthetic-browser-credential"));
  await page.route("**/api/v1/**", async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname.replace("/api/v1", ""), body = req.postData() ? req.postDataJSON() as Record<string, unknown> : null;
    calls.push({ path, body, params: url.searchParams });
    if (path === "/auth/config") return route.fulfill({ json: { mode: "dev" } });
    if (path === "/auth/me") return route.fulfill({ json: { user_id: "actor", organization_id: "org", email: "staff@example.test", display_name: "Evaluation reviewer", roles: [options.role ?? "ADMIN"] } });
    if (path === "/health") return route.fulfill({ json: { status: "ok", dependencies: [] } });
    if (path === "/purchasing-policies") return route.fulfill({ json: { items: [policy], page: Number(url.searchParams.get("page") ?? 1), page_size: 25, total: 52 } });
    if (path === "/purchasing-policies/" + POLICY) return route.fulfill({ json: policy });
    if (path === "/financial-imports") return route.fulfill({ json: { items: [report], page: 1, page_size: 25, total: 1 } });
    if (path === "/financial-imports/" + REPORT) return route.fulfill({ json: report });
    if (path.endsWith("/binding-revisions")) return route.fulfill({ json: { items: [], page: 1, page_size: 25, total: 0 } });
    if (path.endsWith("/recommendation-preview")) {
      const result = structuredClone(preview);
      if (!body?.financial) { delete result.snapshot.financial; result.snapshot.profitability = "NOT_INTEGRATED"; }
      return route.fulfill({ json: result });
    }
    if (path.endsWith("/recommendations")) {
      attempts++;
      if (options.stale) return route.fulfill({ status: 409, json: { error: { code: "replenishment_recommendation_conflict", details: { code: "RECOMMENDATION_EVIDENCE_CHANGED" } } } });
      if (options.ambiguous && attempts === 1) return route.abort("failed");
      saved = { ...saved!, ...preview, id: ID, policy_id: POLICY, product_id: preview.snapshot.product_id, request_id: String(body!.request_id), status: preview.snapshot.status, reason: String(body!.reason), recorded_by_user_id: "actor", recorded_at: TIME, snapshot_sha256: "c".repeat(64), historical: true, purchasing_eligible: false };
      return route.fulfill({ status: 201, json: saved });
    }
    if (path === "/replenishment-recommendations") return route.fulfill({ json: { items: saved ? [saved] : [], page: Number(url.searchParams.get("page") ?? 1), page_size: 25, total: saved ? 52 : 0 } });
    if (path === "/replenishment-recommendations/" + ID) return route.fulfill({ json: saved });
    return route.fulfill({ status: 404, json: { error: { code: "unexpected_mock", message: path } } });
  });
  return calls;
}
async function begin(page: Page, financial = true) {
  await page.goto("/replenishment-recommendations?policy=" + POLICY);
  await expect(page.getByRole("button", { name: "Preview evaluation", exact: true })).toBeVisible();
  if (financial) { await page.getByRole("button", { name: "Select report", exact: true }).click(); await expect(page.getByTestId("selected-report")).toContainText("synthetic-october.xlsx"); }
  await page.getByRole("button", { name: "Preview evaluation", exact: true }).click();
  await expect(page.getByTestId("evaluation-evidence")).toBeVisible();
}
async function acknowledge(page: Page) {
  await page.getByLabel("Reason for recording", { exact: true }).fill("Reviewed synthetic source evidence and remaining checks.");
  await page.getByLabel("I reviewed the inputs, quantities, unknowns and further purchasing checks", { exact: true }).check();
}

test("explicit financial selection, acknowledgement, save and historical detail on desktop/mobile", async ({ page }, info) => {
  const calls = await mock(page); await page.setViewportSize({ width: 1280, height: 900 }); await begin(page);
  await expect(page.getByTestId("financial-context")).toContainText("Historical financial context");
  await expect(page.getByRole("button", { name: "Record evaluation", exact: true })).toBeDisabled();
  await acknowledge(page);
  await page.screenshot({ path: info.outputPath("recommendation-review-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("recommendation-review-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: "Record evaluation", exact: true }).click();
  await expect(page.getByTestId("saved-evaluation")).toContainText("Reviewed synthetic source evidence");
  expect(calls.find(c => c.path.endsWith("/recommendation-preview"))!.body).toEqual({ expected_approved_revision_id: BASE.snapshot.approved_policy.id, financial: { import_id: REPORT, expected_binding_revision_id: null } });
  const sent = calls.find(c => c.path.endsWith("/recommendations"))!.body!;
  expect(sent.acknowledge_review).toBe(true); expect(sent.expected_evidence_sha256).toBe(BASE.evidence_sha256);
  await page.screenshot({ path: info.outputPath("recommendation-detail-mobile.png"), fullPage: true });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.screenshot({ path: info.outputPath("recommendation-detail-desktop.png"), fullPage: true });
});

test("no financial report is selected automatically and changing it clears preview and acknowledgement", async ({ page }) => {
  const calls = await mock(page); await begin(page, false);
  expect(calls.find(c => c.path.endsWith("/recommendation-preview"))!.body).not.toHaveProperty("financial");
  await expect(page.getByTestId("financial-context")).toHaveCount(0); await acknowledge(page);
  await page.getByRole("button", { name: "Select report", exact: true }).click();
  await expect(page.getByTestId("evaluation-evidence")).toHaveCount(0);
  await page.getByRole("button", { name: "Preview evaluation", exact: true }).click();
  await expect(page.getByLabel("I reviewed the inputs, quantities, unknowns and further purchasing checks", { exact: true })).not.toBeChecked();
});

for (const role of ["VIEWER", "DATA_OPERATOR"]) test(role + " sees permitted recommendation actions", async ({ page }) => {
  const calls = await mock(page, { role }); await begin(page);
  await expect(page.getByRole("button", { name: "Record evaluation", exact: true })).toHaveCount(role === "VIEWER" ? 0 : 1);
  expect(calls.filter(c => c.path.endsWith("/recommendations"))).toHaveLength(0);
});

test("unconfirmed save retries the exact request UUID and body", async ({ page }) => {
  const calls = await mock(page, { ambiguous: true }); await begin(page); await acknowledge(page);
  await page.getByRole("button", { name: "Record evaluation", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("save result is unconfirmed");
  await expect(page.getByRole("button", { name: "Select report", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Retry same save", exact: true }).click();
  await expect(page.getByTestId("saved-evaluation")).toBeVisible();
  const writes = calls.filter(c => c.path.endsWith("/recommendations")); expect(writes).toHaveLength(2); expect(writes[0]!.body).toEqual(writes[1]!.body);
});

test("stale evidence requires a fresh preview and acknowledgement", async ({ page }) => {
  await mock(page, { stale: true }); await begin(page); await acknowledge(page);
  await page.getByRole("button", { name: "Record evaluation", exact: true }).click();
  await expect(page.getByText("Evidence changed. Preview again and review the latest inputs before recording.", { exact: true })).toBeVisible();
  await expect(page.getByTestId("evaluation-evidence")).toHaveCount(0);
  await page.getByRole("button", { name: "Preview evaluation", exact: true }).click();
  await expect(page.getByRole("button", { name: "Record evaluation", exact: true })).toBeDisabled();
});

test("unsafe quantities are never shown as exact and prevent recording", async ({ page }) => {
  await mock(page, { unsafe: true }); await begin(page); await acknowledge(page);
  await expect(page.getByTestId("evaluation-evidence")).toContainText("Outside exact display range");
  await expect(page.getByRole("button", { name: "Record evaluation", exact: true })).toBeDisabled();
  await expect(page.getByTestId("evaluation-evidence")).not.toContainText("9,007,199,254,740,992");
});

test("approval is required and saved history supports bounded status/policy filters", async ({ page }) => {
  const calls = await mock(page, { noApproval: true }); await page.goto("/replenishment-recommendations?policy=" + POLICY);
  await expect(page.getByText("No current approval. Preview requires an approved policy.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Preview evaluation", exact: true })).toHaveCount(0);
  await page.goto("/replenishment-recommendations"); await page.getByLabel("Status", { exact: true }).selectOption("BLOCKED");
  await expect.poll(() => calls.filter(c => c.path === "/replenishment-recommendations").at(-1)?.params.get("status")).toBe("BLOCKED");
  await page.getByRole("button", { name: "Filter history", exact: true }).click();
  await expect.poll(() => calls.filter(c => c.path === "/replenishment-recommendations").at(-1)?.params.get("policy_id")).toBe(POLICY);
});
