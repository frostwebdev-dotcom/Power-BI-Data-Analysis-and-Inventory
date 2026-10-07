import { expect, test } from "@playwright/test";

const tenant = "17bbcdfa-a0c5-4dfe-a851-0c7459906680";
const apiClient = "11111111-1111-4111-8111-111111111111";
const browserClient = "22222222-2222-4222-8222-222222222222";
const scope = `api://${apiClient}/access_as_user`;

test("disabled authentication cannot expose an email-only sign-in", async ({ page }) => {
  await page.route("**/api/v1/auth/config", (route) => route.fulfill({ json: { mode: "disabled" } }));
  await page.goto("/");
  await expect(page.getByText("Sign-in is not configured. Contact your administrator.")).toBeVisible();
  await expect(page.getByLabel("Email")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeDisabled();
});

test("configuration failures explain why sign-in is unavailable", async ({ page }) => {
  await page.route("**/api/v1/auth/config", (route) => route.fulfill({
    status: 503, json: { error: { code: "unavailable", message: "Sign-in setup unavailable." } },
  }));
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("Sign-in setup unavailable.");
  await expect(page.getByRole("button", { name: "Sign in", exact: true })).toBeDisabled();
});

test("local development sign-in preserves email normalization", async ({ page }) => {
  await page.route("**/api/v1/auth/config", (route) => route.fulfill({ json: { mode: "dev" } }));
  await page.route("**/api/v1/auth/dev-token", (route) => route.fulfill({
    status: 404, json: { error: { message: "The requested resource does not exist." } },
  }));
  await page.goto("/");
  await page.getByLabel("Email").fill("ADMIN@EXAMPLE.TEST");
  const request = page.waitForRequest("**/api/v1/auth/dev-token");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  expect((await request).postDataJSON()).toEqual({ email: "admin@example.test" });
  await expect(page.getByRole("alert")).toContainText("No active user with that email");
});

test("Microsoft sign-in launches PKCE for the API and never calls dev-token", async ({ page, context }) => {
  let devRequests = 0;
  await context.route("**/api/v1/auth/dev-token", (route) => {
    devRequests += 1;
    return route.abort();
  });
  await page.route("**/api/v1/auth/config", (route) => route.fulfill({
    json: { mode: "entra", tenant_id: tenant, client_id: browserClient, scope },
  }));
  await context.route("https://login.microsoftonline.com/**", (route) => {
    const url = route.request().url();
    if (url.includes(".well-known/openid-configuration")) {
      return route.fulfill({ json: {
        issuer: `https://login.microsoftonline.com/${tenant}/v2.0`,
        authorization_endpoint: `https://login.microsoftonline.com/${tenant}/oauth2/v2.0/authorize`,
        token_endpoint: `https://login.microsoftonline.com/${tenant}/oauth2/v2.0/token`,
        end_session_endpoint: `https://login.microsoftonline.com/${tenant}/oauth2/v2.0/logout`,
        jwks_uri: `https://login.microsoftonline.com/${tenant}/discovery/v2.0/keys`,
      } });
    }
    if (url.includes("/discovery/instance")) {
      return route.fulfill({ json: { metadata: [{
        preferred_network: "login.microsoftonline.com", preferred_cache: "login.windows.net",
        aliases: ["login.microsoftonline.com", "login.windows.net", "login.microsoft.com"],
      }] } });
    }
    return route.fulfill({ contentType: "text/html", body: "<h1>Microsoft test sign-in</h1>" });
  });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Sign in with Microsoft" })).toBeEnabled();
  await expect(page.getByLabel("Email")).toHaveCount(0);
  const popupPromise = page.waitForEvent("popup");
  await page.getByRole("button", { name: "Sign in with Microsoft" }).click();
  const popup = await popupPromise;
  await popup.waitForURL(`https://login.microsoftonline.com/${tenant}/oauth2/v2.0/authorize**`);
  const parameters = new URL(popup.url()).searchParams;
  expect(parameters.get("client_id")).toBe(browserClient);
  expect(parameters.get("scope")).toContain(scope);
  expect(parameters.get("code_challenge_method")).toBe("S256");
  expect(parameters.get("code_challenge")).toBeTruthy();
  expect(parameters.get("redirect_uri")).toContain("/auth/callback.html");
  expect(parameters.get("response_type")).toBe("code");
  expect(devRequests).toBe(0);
  await popup.close();
});
