import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "@playwright/test";

for (const path of ["/ask", "/data", "/dashboard", "/usage", "/settings"]) {
  test(`${path} has no serious accessibility violations`, async ({ page }) => {
    await page.goto(path);
    await expect(page.getByRole("main")).toBeVisible();
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
    const serious = results.violations
      .filter((v) => v.impact === "serious" || v.impact === "critical")
      .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(", ")}`);
    expect(serious).toEqual([]);
  });
}
