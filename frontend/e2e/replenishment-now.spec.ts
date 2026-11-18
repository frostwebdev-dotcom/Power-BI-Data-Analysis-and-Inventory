import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import type { CurrentCheck, CurrentPolicyChoice, CurrentRequest } from "../src/lib/replenishment-now";

test.setTimeout(30_000);
const FIXTURE = JSON.parse(readFileSync(join(process.cwd(), "e2e/fixtures/replenishment-now.json"), "utf8")) as { choices: CurrentPolicyChoice[]; response: CurrentCheck };
interface Options { role?: string; stale?: boolean; empty?: boolean; delayed?: boolean; paginated?: boolean; secondUnknown?: boolean }
async function mock(page: Page, options: Options = {}) {
  const calls: { path: string; method: string; body: Record<string, unknown> | null; params: URLSearchParams }[] = [];
  let posts = 0, release: (() => void) | undefined;
  await page.addInitScript(() => localStorage.setItem("prms.token", "synthetic-browser-credential"));
  await page.route("**/api/v1/**", async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname.replace("/api/v1", ""), body = req.postData() ? req.postDataJSON() as Record<string, unknown> : null;
    calls.push({ path, method: req.method(), body, params: url.searchParams });
    if (path === "/auth/config") return route.fulfill({ json: { mode: "dev" } });
    if (path === "/auth/me") return route.fulfill({ json: { user_id: "actor", organization_id: "org", email: "staff@example.test", display_name: "Current reviewer", roles: [options.role ?? "ADMIN"] } });
    if (path === "/health") return route.fulfill({ json: { status: "ok", dependencies: [] } });
    if (path === "/replenishment-now/policies") {
      const pageNumber = Number(url.searchParams.get("page") ?? 1), size = options.paginated ? 2 : 25;
      const all = options.empty ? [] : FIXTURE.choices;
      return route.fulfill({ json: { items: all.slice((pageNumber - 1) * size, pageNumber * size), page: pageNumber, page_size: size, total: all.length } });
    }
    if (path === "/replenishment-now/preview") {
      posts++;
      if (options.delayed && posts === 1) await new Promise<void>(resolve => { release = resolve; });
      if (options.stale) return route.fulfill({ status: 409, json: { error: { code: "purchasing_policy_conflict", details: { code: "POLICY_APPROVAL_CHANGED" } } } });
      const selected = (body as unknown as CurrentRequest).choices, result = structuredClone(FIXTURE.response);
      result.items = result.items.filter(item => selected.some(c => c.policy_id === item.policy_id));
      if (options.secondUnknown && posts > 1) {
        for (const item of result.items) {
          item.urgency = "UNKNOWN"; item.opportunity = "UNCONFIRMED"; item.reasons = ["SOURCE_DATA_STALE"]; item.snapshot.status = "BLOCKED";
          item.snapshot.readiness.status = "BLOCKED";
          if (Array.isArray(item.snapshot.readiness.pools)) for (const pool of item.snapshot.readiness.pools) { if (pool && typeof pool === "object") pool.quantity_input_ready = false; }
          for (const pool of item.pools) { pool.urgency = "UNKNOWN"; pool.required_units = null; pool.net_shortfall_units = null; pool.supply_days_numerator = null; pool.supply_days_denominator = null; pool.reasons = ["SOURCE_DATA_STALE"]; }
        }
      }
      result.summary = { checked_policies: result.items.length, urgent: result.items.filter(i => i.urgency === "URGENT").length, replenish: result.items.filter(i => i.urgency === "REPLENISH").length, no_reorder: result.items.filter(i => i.urgency === "NO_REORDER").length, unknown: result.items.filter(i => i.urgency === "UNKNOWN").length, review_opportunities: result.items.filter(i => i.opportunity === "REVIEW_OPPORTUNITY").length };
      return route.fulfill({ json: result });
    }
    return route.fulfill({ status: 404, json: { error: { code: "unexpected_mock", message: path } } });
  });
  return { calls, release: () => release?.() };
}
const checkButton = (page: Page) => page.getByRole("button", { name: "Check selected policies now", exact: true });
async function select(page: Page, ...names: string[]) {
  for (const name of names) await page.getByLabel(new RegExp(name + " · Synthetic supplier")).check();
}

test("fresh explicit checks separate urgency and supplier review on desktop/mobile", async ({ page }, info) => {
  const { calls } = await mock(page); await page.setViewportSize({ width: 1280, height: 900 }); await page.goto("/replenishment-now");
  await expect(page.getByText("No policies selected.", { exact: true })).toBeVisible(); expect(calls.filter(c => c.method === "POST")).toHaveLength(0);
  await select(page, "A-URGENT", "B-SHORTFALL", "C-UNKNOWN", "D-STOCKED"); await checkButton(page).click();
  const result = page.getByTestId("current-replenishment-result"); await expect(result).toContainText("Policies checked");
  const rows = page.getByRole("region", { name: "Current selected-policy replenishment results", exact: true }).locator("tbody tr");
  await expect(rows).toHaveCount(4); await expect(rows.first()).toContainText("Urgent at check"); await expect(rows.first()).toContainText("Supplier opportunity unavailable");
  await expect(rows.nth(1)).toContainText("Supplier review opportunity"); await expect(rows.nth(2)).toContainText("Current inputs unknown");
  expect(Object.keys(calls.find(c => c.path === "/replenishment-now/preview")!.body!)).toEqual(["choices"]);
  await page.evaluate(() => window.scrollTo(0, 0)); await page.screenshot({ path: info.outputPath("current-replenishment-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 }); await page.evaluate(() => window.scrollTo(0, 0));
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("current-replenishment-mobile.png"), fullPage: true });
  await page.getByRole("button", { name: "Inspect A-URGENT Synthetic supplier A", exact: true }).click();
  await expect(page.getByTestId("current-replenishment-detail")).toContainText("Current supplier commercial checks");
  await expect(page.getByRole("button", { name: "Record evaluation", exact: true })).toHaveCount(0);
});

test("captured display filters do not trigger another current evaluation", async ({ page }) => {
  const { calls } = await mock(page); await page.goto("/replenishment-now"); await select(page, "A-URGENT", "B-SHORTFALL", "C-UNKNOWN"); await checkButton(page).click();
  await expect(page.getByTestId("current-replenishment-result")).toBeVisible();
  await page.getByLabel("Show captured urgency", { exact: true }).selectOption("UNKNOWN");
  const rows = page.getByRole("region", { name: "Current selected-policy replenishment results", exact: true }).locator("tbody tr");
  await expect(rows).toHaveCount(1); await expect(rows.first()).toContainText("C-UNKNOWN");
  await page.getByLabel("Supplier review opportunities only", { exact: true }).check(); await expect(page.getByText("No checked results match these filters", { exact: true })).toBeVisible();
  await page.getByLabel("Show captured urgency", { exact: true }).selectOption("ALL"); await expect(rows).toHaveCount(1); await expect(rows.first()).toContainText("B-SHORTFALL");
  expect(calls.filter(c => c.path === "/replenishment-now/preview")).toHaveLength(1);
});

test("choice changes and late completion cannot replace new evidence", async ({ page }) => {
  const mocked = await mock(page, { delayed: true }); await page.goto("/replenishment-now"); await select(page, "A-URGENT", "B-SHORTFALL"); await checkButton(page).click();
  await expect.poll(() => mocked.calls.filter(c => c.path === "/replenishment-now/preview").length).toBe(1);
  await page.getByLabel(/A-URGENT · Synthetic supplier/).uncheck(); mocked.release();
  await expect(page.getByTestId("current-replenishment-result")).toHaveCount(0); await checkButton(page).click();
  await expect(page.getByTestId("current-replenishment-result")).toContainText("B-SHORTFALL"); await expect(page.getByTestId("current-replenishment-result")).not.toContainText("A-URGENT");
  await page.getByLabel(/B-SHORTFALL · Synthetic supplier/).uncheck(); await expect(page.getByTestId("current-replenishment-result")).toHaveCount(0);
});

test("stale approval clears the selected set and requires new choices", async ({ page }) => {
  await mock(page, { stale: true }); await page.goto("/replenishment-now"); await select(page, "A-URGENT"); await checkButton(page).click();
  await expect(page.getByText("A selected approval changed. Reload choices and select the policies again.", { exact: true })).toBeVisible();
  await expect(page.getByText("No policies selected.", { exact: true })).toBeVisible();
});

test("choices persist across bounded pages and literal searches reset the candidate page", async ({ page }) => {
  const { calls } = await mock(page, { paginated: true }); await page.goto("/replenishment-now"); await select(page, "A-URGENT");
  await page.getByRole("button", { name: "Next", exact: true }).click(); await select(page, "C-UNKNOWN"); await checkButton(page).click();
  await expect(page.getByTestId("current-replenishment-result")).toContainText("A-URGENT"); await expect(page.getByTestId("current-replenishment-result")).toContainText("C-UNKNOWN");
  await page.getByLabel("Find product, catalogue, vendor or supplier SKU", { exact: true }).fill("%_literal"); await page.getByRole("button", { name: "Search approved policies", exact: true }).click();
  await expect.poll(() => calls.filter(c => c.path === "/replenishment-now/policies").at(-1)?.params.get("page")).toBe("1");
  expect(calls.filter(c => c.path === "/replenishment-now/policies").at(-1)?.params.get("q")).toBe("%_literal");
  await expect(page.getByRole("list", { name: "Policies selected for current check", exact: true })).toContainText("C-UNKNOWN");
});

test("viewer can explicitly recheck and stale sources replace previous urgency", async ({ page }) => {
  const { calls } = await mock(page, { role: "VIEWER", secondUnknown: true }); await page.goto("/replenishment-now"); await select(page, "A-URGENT"); await checkButton(page).click();
  const region = page.getByRole("region", { name: "Current selected-policy replenishment results", exact: true }); await expect(region).toContainText("Urgent at check");
  await checkButton(page).click(); await expect(region).toContainText("Current inputs unknown"); await expect(region).not.toContainText("Urgent at check");
  expect(calls.filter(c => c.path === "/replenishment-now/preview")).toHaveLength(2);
});

test("empty policy list never creates an automatic check", async ({ page }) => {
  const { calls } = await mock(page, { empty: true }); await page.goto("/replenishment-now"); await expect(page.getByText("No current approved policies found", { exact: true })).toBeVisible();
  await expect(checkButton(page)).toHaveCount(0); expect(calls.filter(c => c.method === "POST")).toHaveLength(0);
});
