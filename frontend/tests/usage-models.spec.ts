import { test, expect } from "@playwright/test";

const model = (provider_id: string, model_id: string, calls: number, cost: string) => ({
  provider_id,
  model_id,
  calls,
  unsettled_calls: 0,
  total_tokens: calls * 1000,
  total_cost_usd: cost,
  cost_unknown: false,
  avg_latency_ms: 1200,
});

test("usage breaks model calls down by model and filters by provider", async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/usage")) {
      return route.fulfill({
        json: { workspace_id: "default", run_count: 0, runs: [], models: [model("openai", "gpt-x", 4, "0.40"), model("anthropic", "claude-y", 2, "0.10")] },
      });
    }
    return route.fulfill({ status: 404, json: { detail: "not mocked" } });
  });
  await page.goto("/usage");
  const section = page.getByRole("region", { name: "By model" });
  await expect(section.getByRole("row")).toHaveCount(3);
  await expect(section.getByRole("cell", { name: /gpt-x/ })).toBeVisible();
  await expect(section.getByText("1.2 s").first()).toBeVisible();
  await section.getByRole("group", { name: "Provider" }).getByRole("button", { name: "anthropic" }).click();
  await expect(section.getByRole("row")).toHaveCount(2);
  await expect(page).toHaveURL(/provider=anthropic/);
});
