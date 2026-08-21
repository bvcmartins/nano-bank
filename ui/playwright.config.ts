import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: 0,
  use: {
    baseURL: process.env.UI_BASE_URL ?? "http://localhost:3000",
    trace: "on-first-retry",
    video: 'on'
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
