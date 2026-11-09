import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

const source = {
  id: "source", name: "Universal inventory", vendor_id: "vendor", vendor_code: "UNIVERSAL",
  profile_id: "profile", profile_name: "Universal stock", profile_version: 2,
  sender_addresses: ["stock@vendor.example"], subject_contains: null, filename_pattern: "*.xlsx",
  mode: "ATTACHMENT", download_hosts: [], download_path_prefix: null, download_filename: null,
  stale_after_hours: null, is_active: true, starts_at: "2026-11-06T10:00:00Z",
  last_received_at: "2026-11-06T11:00:00Z", last_success_at: null, freshness: "awaiting_import",
};

async function mockApp(page: Page, role = "ADMIN", populated = false, connection: Record<string, unknown> = {}) {
  const calls: { path: string; method: string; body: Record<string, unknown> | null }[] = [];
  await page.addInitScript(() => localStorage.setItem("prms.token", "test-credential"));
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1", "");
    const body = request.postData() ? request.postDataJSON() as Record<string, unknown> : null;
    calls.push({ path, method: request.method(), body });
    if (path === "/auth/config") return route.fulfill({ json: { mode: "dev" } });
    if (path === "/auth/me") return route.fulfill({ json: { user_id: "user", organization_id: "org", email: "user@example.test", display_name: "User", roles: [role] } });
    if (path === "/health") return route.fulfill({ json: { status: "ok", dependencies: [] } });
    if (path === "/vendor-mail/status") return route.fulfill({ json: {
      provider: "microsoft365", enabled: false, configured: false, mailbox: null, poll_minutes: 5,
      missing_settings: ["VENDOR_MAIL_MAILBOX"], last_poll_at: null, last_success_at: null, error_code: null, ...connection,
    } });
    if (path === "/vendors") return route.fulfill({ json: { items: [{ id: "vendor", code: "UNIVERSAL", name: "Universal", is_active: true }], total: 1 } });
    if (path === "/vendors/vendor/import-profiles") return route.fulfill({ json: { items: [{ id: "profile", name: "Universal stock", version: 2, is_active: true }], total: 1 } });
    if (path === "/vendor-mail/sources" && request.method() === "POST") return route.fulfill({ status: 201, json: { ...source, ...body } });
    if (path === "/vendor-mail/sources") return route.fulfill({ json: { items: populated ? [source] : [], total: populated ? 1 : 0 } });
    if (path === "/vendor-mail/deliveries") return route.fulfill({ json: { items: populated ? [{ id: "delivery", source_id: "source", filename: "inventory.xlsx", received_at: source.last_received_at, status: "NEEDS_REVIEW", attempts: 1, next_attempt_at: null, error_code: "DOWNLOAD_LOGIN_REQUIRED", import_job_id: "job" }] : [], total: populated ? 1 : 0, page: 1, page_size: 25 } });
    return route.fulfill({ json: { items: [], total: 0 } });
  });
  return calls;
}

test("Gmail identifies the mailbox and explains a wrong-account grant without enabling checks", async ({ page }) => {
  await mockApp(page, "ADMIN", false, { provider: "gmail", mailbox: "inventory@example.test", error_code: "GMAIL_MAILBOX_MISMATCH" });
  await page.goto("/automatic-imports");
  await expect(page.getByText("Gmail · Read-only access", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Gmail data use and privacy" })).toHaveAttribute("href", "/privacy.html");
  await expect(page.getByText("inventory@example.test", { exact: true })).toBeVisible();
  await expect(page.getByText("The authorized Google account does not match the inventory mailbox.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Check mailbox now" })).toBeDisabled();
});

test("unconfigured mailbox cannot be polled and source preparation defaults to paused", async ({ page }) => {
  const calls = await mockApp(page);
  await page.goto("/automatic-imports");
  await expect(page.getByText(/Mailbox connection required/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Check mailbox now" })).toBeDisabled();
  await page.getByRole("button", { name: "Add email source" }).click();
  await page.getByLabel("Source name", { exact: true }).fill("Universal inventory");
  await page.getByLabel("Vendor", { exact: true }).selectOption("vendor");
  await page.getByLabel("Import profile", { exact: true }).selectOption("profile");
  await page.getByLabel("Approved sender addresses").fill("stock@vendor.example");
  await expect(page.getByLabel("Enable this source for new emails")).not.toBeChecked();
  await page.getByRole("button", { name: "Save source" }).click();
  await expect.poll(() => calls.find((c) => c.method === "POST" && c.path === "/vendor-mail/sources")?.body).toMatchObject({ is_active: false, stale_after_hours: null, profile_id: "profile", sender_addresses: ["stock@vendor.example"] });
});

test("viewer sees source, failure reason and report without modification controls", async ({ page }) => {
  await mockApp(page, "VIEWER", true);
  await page.goto("/automatic-imports");
  await expect(page.getByText("Universal stock v2")).toBeVisible();
  await expect(page.getByRole("link", { name: "View report" })).toHaveAttribute("href", "/imports?job=job");
  await expect(page.getByRole("button", { name: "Add email source" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Retry", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Edit Universal inventory" })).toHaveCount(0);
});

test("retry keeps pinned profile unless operator explicitly chooses current profile", async ({ page }) => {
  const calls = await mockApp(page, "ADMIN", true);
  await page.goto("/automatic-imports");
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect.poll(() => calls.filter((c) => c.path.endsWith("/retry")).at(-1)?.body).toEqual({ use_current_profile: false });
  await page.getByRole("button", { name: "Retry with source’s current profile" }).click();
  await expect.poll(() => calls.filter((c) => c.path.endsWith("/retry")).at(-1)?.body).toEqual({ use_current_profile: true });
});
