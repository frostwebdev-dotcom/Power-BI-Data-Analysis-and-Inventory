import { expect, test } from "@playwright/test";

test("Google app information and privacy are public without JavaScript or an API session", async ({ browser, baseURL }) => {
  const context = await browser.newContext({ javaScriptEnabled: false, baseURL });
  const page = await context.newPage();
  const apiRequests: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname.startsWith("/api/v1/")) apiRequests.push(request.url());
  });
  try {
    const homepage = await page.goto("/app-info.html");
    expect(homepage?.status()).toBe(200);
    await expect(page.getByRole("heading", { level: 1, name: "Global Supplies PRMS" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Read-only Gmail inventory imports" })).toBeVisible();
    await page.getByRole("navigation").getByRole("link", { name: "Privacy policy" }).click();
    await expect(page).toHaveURL(/\/privacy\.html$/);
    await expect(page.getByRole("heading", { level: 1, name: "Privacy policy" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Retention and deletion requests" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Disconnecting Gmail" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Google API Services User Data Policy" })).toBeVisible();
    expect(apiRequests).toEqual([]);
  } finally {
    await context.close();
  }
});

test("public information never grants an anonymous visitor access to operational screens", async ({ page }) => {
  await page.route("**/api/v1/auth/config", (route) => route.fulfill({ json: { mode: "disabled" } }));
  await page.goto("/app-info.html");
  await page.getByRole("navigation").getByRole("link", { name: "Sign in to PRMS" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Sign in", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Privacy policy", exact: true })).toHaveAttribute("href", "/privacy.html");
  for (const path of ["/products", "/automatic-imports"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1, name: "Sign in", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Add email source" })).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "Main" })).toHaveCount(0);
  }
});
