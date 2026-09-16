import { defineConfig } from "@playwright/test";

/**
 * End-to-end tests run against an already-running stack: the web app at
 * E2E_BASE_URL (default http://localhost:3000) talking to the API it was built
 * for. In CI that is the Docker Compose stack after `seed_dev` has run; locally
 * it is `next dev` plus `uvicorn` against the development database.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
