import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  use: {
    baseURL: "http://127.0.0.1:8080",
    headless: true,
  },
  webServer: process.env.CI
    ? {
        command: "npx next start -p 8080 -H 127.0.0.1",
        url: "http://127.0.0.1:8080/ask",
        reuseExistingServer: false,
        timeout: 120_000,
      }
    : undefined,
});
