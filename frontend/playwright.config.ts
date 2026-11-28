import { defineConfig } from "@playwright/test";

const port = process.env.PLAYWRIGHT_PORT ?? "8080";
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  use: {
    baseURL,
    headless: true,
  },
  webServer: process.env.CI
    ? {
        command: `npx next start -p ${port} -H 127.0.0.1`,
        url: `${baseURL}/ask`,
        reuseExistingServer: false,
        timeout: 120_000,
      }
    : undefined,
});
