import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "@playwright/test";

for (const theme of ["light", "dark"] as const) {
  for (const path of ["/ask", "/data", "/dashboard", "/usage", "/settings"]) {
    test(`${path} (${theme}) has no serious accessibility violations`, async ({ page }) => {
      await page.addInitScript((t) => localStorage.setItem("ledgerline.theme", t), theme);
      await page.goto(path);
      await expect(page.getByRole("main")).toBeVisible();
      const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
      const serious = results.violations
        .filter((v) => v.impact === "serious" || v.impact === "critical")
        .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")}`);
      expect(serious).toEqual([]);
    });
  }
}

test("dark Ask active view has no contrast violations and phone Data has no horizontal overflow", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("ledgerline.theme", "dark"));
  const snap = {
    snapshot_id: "snap_1",
    source_id: "src_1",
    version: 1,
    total_rows: 10,
    tables: ["orders"],
    approved: true,
    created_at: new Date().toISOString(),
  };
  const run = {
    run_id: "run_1",
    workspace_id: "default",
    question: "Net sales?",
    mode: "quick",
    status: "completed",
    created_at: new Date().toISOString(),
    snapshot_id: "snap_1",
    answer: { answer_id: "ans_1", summary_markdown: "Net sales were **$100**." },
  };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/snapshots")) return route.fulfill({ json: { snapshots: [snap] } });
    if (path.includes("/semantics/")) {
      return route.fulfill({
        json: {
          semantic_version_id: "sem_1",
          snapshot_id: "snap_1",
          version: 1,
          approved: false,
          metrics: [{ metric_id: "m1", display_name: "Amount", expression: "SUM(amount)", grain: "orders", unit: "USD" }],
        },
      });
    }
    if (path.endsWith("/runs")) return route.fulfill({ json: { runs: [run], next_before: null } });
    if (path.endsWith("/events")) return route.fulfill({ status: 200, contentType: "text/event-stream", body: ": idle\n\n" });
    if (path.endsWith("/providers")) {
      return route.fulfill({ json: { workspace_id: "default", providers: [{ provider_id: "openai", configured: true, key_status: "ready" }], models: [] } });
    }
    return route.fulfill({ json: { pins: [], status: "ok" } });
  });

  await page.goto("/ask?run=run_1");
  await expect(page.getByRole("region", { name: "Answer" })).toBeVisible();
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const serious = axe.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious).toEqual([]);

  await page.locator("aside nav a[href='/ask']").click();
  await expect(page.getByRole("heading", { name: "What would you like to know?" })).toBeVisible();

  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/data");
  await expect(page.getByRole("table", { name: "Proposed metric definitions" })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
