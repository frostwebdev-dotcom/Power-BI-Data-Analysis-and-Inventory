import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

/**
 * The Milestone 1 exit-gate walk-through (docs/acceptance-criteria.md, exit
 * criteria 3–10), driven through the admin interface exactly as a person would:
 *
 *   sign in → create vendor → create profile → upload the fixture CSV → see the
 *   report → open the exception → approve → re-upload → see it match
 *   automatically → add to watchlist → upload the "now available" fixture → see
 *   the availability event.
 *
 * Runs against a seeded stack (`python -m app.cli.seed_dev`): the admin user
 * and one demo product ("Blue Widget 12 pack", DEMO-001). Every name the test
 * creates carries a per-run suffix so it can run repeatedly against one
 * database.
 */

const ADMIN_EMAIL = process.env.E2E_ADMIN_EMAIL ?? "admin@example.test";
const RUN = Date.now().toString(36).toUpperCase().slice(-6);
const VENDOR_CODE = `E2E${RUN}`;
const VENDOR_NAME = `E2E Vendor ${RUN}`;
const PROFILE_NAME = `walkthrough-${RUN}`;
const SKU = `E2E-${RUN}-1`;
const HEADERS = ["SKU", "UPC", "Description", "Qty"];

function csv(headers: string[], rows: string[][]): Buffer {
  const lines = [headers, ...rows].map((row) => row.join(","));
  return Buffer.from(lines.join("\r\n") + "\r\n", "utf8");
}

// No identifier: nothing can match automatically; the description names the
// demo product, so the queue offers it as a suggestion.
const FIXTURE_OUT_OF_STOCK = csv(HEADERS, [[SKU, "", "Blue Widget 12 pack", "0"]]);
// Same rows, different bytes (header case), so the upload is not a duplicate.
const FIXTURE_REIMPORT = csv(
  HEADERS.map((h) => h.toLowerCase()),
  [[SKU, "", "Blue Widget 12 pack", "0"]],
);
const FIXTURE_NOW_AVAILABLE = csv(HEADERS, [[SKU, "", "Blue Widget 12 pack", "40"]]);

async function signIn(page: Page) {
  await page.goto("/");
  await page.getByLabel("Email").fill(ADMIN_EMAIL);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Purchasing & Replenishment" })).toBeVisible();
}

async function upload(page: Page, fileName: string, content: Buffer) {
  await page.goto("/imports");
  await page.getByTestId("upload-vendor").selectOption({ label: `${VENDOR_CODE} — ${VENDOR_NAME}` });
  await expect(page.getByTestId("upload-profile")).toContainText(PROFILE_NAME);
  await page.getByTestId("upload-file").setInputFiles({ name: fileName, mimeType: "text/csv", buffer: content });
  await page.getByTestId("upload-submit").click();
  const link = page.getByTestId("upload-result-link");
  await expect(link).toBeVisible();
  await link.click();
  await expect(page.getByRole("heading", { name: fileName })).toBeVisible();
}

test.describe.configure({ mode: "serial" });

test("Milestone 1 walk-through", async ({ page }) => {
  await test.step("sign in", async () => {
    await signIn(page);
  });

  await test.step("create a vendor", async () => {
    await page.goto("/vendors");
    await page.getByRole("button", { name: "New vendor" }).click();
    const form = page.getByTestId("vendor-form");
    await form.locator('input[name="code"]').fill(VENDOR_CODE);
    await form.locator('input[name="name"]').fill(VENDOR_NAME);
    await form.locator('input[name="contact_email"]').fill(`sales@${VENDOR_CODE.toLowerCase()}.test`);
    await form.getByRole("button", { name: "Create vendor" }).click();
    await expect(page.getByTestId(`vendor-row-${VENDOR_CODE}`)).toBeVisible();
  });

  await test.step("create an import profile from the rule schemas", async () => {
    await page.getByTestId(`vendor-row-${VENDOR_CODE}`).getByRole("link", { name: "Profiles" }).click();
    await expect(page.getByTestId("profile-vendor")).toHaveValue(/.+/);
    await page.getByRole("button", { name: "New profile" }).click();
    const form = page.getByTestId("profile-form");
    await form.locator('input[name="name"]').fill(PROFILE_NAME);
    const table = page.getByTestId("column_map-columns-table");
    const targets: [string, string][] = [
      ["vendor_sku", "SKU"],
      ["upc", "UPC"],
      ["description", "Description"],
      ["quantity_available", "Qty"],
    ];
    for (const [index, [target, source]] of targets.entries()) {
      await form.getByRole("button", { name: "Add column" }).click();
      const row = table.locator("tbody tr").nth(index);
      await row.getByLabel("target").selectOption(target);
      await row.getByLabel("source").fill(source);
    }
    // Validate against the fixture: the preview shows the row and the header can be pinned.
    await page.getByTestId("sample-file").setInputFiles({ name: "sample.csv", mimeType: "text/csv", buffer: FIXTURE_OUT_OF_STOCK });
    await form.getByRole("button", { name: "Validate", exact: true }).click();
    await expect(page.getByTestId("validation-preview")).toContainText(SKU);
    await form.getByRole("button", { name: "Create profile" }).click();
    await expect(page.getByTestId(`profile-row-${PROFILE_NAME}`)).toBeVisible();
  });

  await test.step("upload the fixture and read the report", async () => {
    await upload(page, "monday.csv", FIXTURE_OUT_OF_STOCK);
    const report = page.getByTestId("import-report");
    await expect(report).toContainText("exception");
    await expect(page.getByTestId("import-rows")).toContainText(SKU);
    await page.getByTestId("row-status-filter").selectOption("OK");
    await expect(page.getByTestId("import-rows")).toContainText(SKU);
    await expect(page.getByRole("button", { name: "Download raw file" })).toBeVisible();
  });

  await test.step("open the exception and approve the suggested product", async () => {
    await page.goto("/exceptions");
    const row = page.getByTestId(`exception-row-${SKU}`);
    await expect(row).toBeVisible();
    await expect(row).toContainText("suggestion only");
    await row.getByRole("button", { name: "Review" }).click();
    await expect(page.getByTestId("candidate").first()).toContainText("Blue Widget 12 pack");
    await page.getByTestId("approve-candidate").first().click();
    await expect(page.getByTestId("decision-outcome")).toContainText("Approved");
    await expect(page.getByTestId("watch-this-product")).toBeVisible();
    await page.getByRole("button", { name: "Close" }).click();
    await expect(page.getByTestId(`exception-row-${SKU}`)).toHaveCount(0);
  });

  await test.step("re-upload: the row matches automatically at priority 3, no new exception", async () => {
    await upload(page, "tuesday.csv", FIXTURE_REIMPORT);
    await expect(page.getByTestId("import-rows")).toContainText("vendor sku mapping (p3)");
    await page.goto("/exceptions");
    await expect(page.getByTestId(`exception-row-${SKU}`)).toHaveCount(0);
  });

  await test.step("add the product to the watchlist from the approved exception", async () => {
    await page.goto("/exceptions");
    await page.getByLabel("Status").selectOption("APPROVED");
    await page.getByTestId(`exception-row-${SKU}`).getByRole("button", { name: "Open" }).click();
    await page.getByTestId("watch-this-product").click();
    const form = page.getByTestId("watch-form");
    await expect(form.locator('input[name="product_id"]')).toHaveValue(/[0-9a-f-]{36}/);
    // Watch it at this vendor specifically, so the status reflects this vendor's line.
    await form.locator('select[name="vendor_id"]').selectOption({ label: `${VENDOR_CODE} — ${VENDOR_NAME}` });
    await form.locator('input[name="reason"]').fill("Walk-through");
    await form.locator('input[name="max_unit_cost"]').fill("10");
    await page.getByTestId("watch-submit").click();
    const watchRow = page.getByTestId("watch-row-DEMO-001").filter({ hasText: VENDOR_CODE });
    await expect(watchRow).toBeVisible();
    await expect(watchRow).toContainText("out of stock");
  });

  await test.step("upload the now-available fixture and see the availability event", async () => {
    await upload(page, "wednesday.csv", FIXTURE_NOW_AVAILABLE);
    await expect(page.getByTestId("import-report")).toContainText("matched");

    await page.goto("/availability");
    await page.getByTestId("watchlist-only").check();
    const event = page.getByTestId(`event-BECAME_AVAILABLE-${SKU}`);
    await expect(event).toBeVisible();
    await expect(event).toContainText("watched");
    await expect(event).toContainText("Blue Widget 12 pack");
    await expect(event).toContainText("0 → 40");

    await page.goto("/watchlist");
    await expect(page.getByTestId("watch-row-DEMO-001").filter({ hasText: VENDOR_CODE })).toContainText("in stock");

    await page.goto("/");
    await expect(page.getByTestId("dashboard-counts")).toBeVisible();
    await expect(page.getByTestId("count-newly-available")).not.toHaveText("0");
  });

  await test.step("the audit log records the decisions", async () => {
    await page.goto("/audit");
    await page.getByLabel("Action").selectOption("mapping_exception.approved");
    await expect(page.getByTestId("audit-events")).toContainText("mapping_exception.approved");
    await page.getByTestId("audit-events").getByRole("button", { name: "Diff" }).first().click();
    await expect(page.getByTestId("audit-diff")).toContainText("status");
  });
});
