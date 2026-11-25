import { test, expect } from "@playwright/test";

test("first-time users can start from sample data and pick an example question", async ({ page }) => {
  let snapshots: object[] = [];
  const sample = {
    snapshot_id: "snap_sample",
    source_id: "src_sample",
    version: 1,
    total_rows: 20,
    tables: ["fees", "orders", "payouts", "refunds"],
    approved: true,
    created_at: new Date().toISOString(),
    source_family: "uploaded_file",
  };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/samples/inr_acceptance/import")) {
      snapshots = [sample];
      return route.fulfill({ status: 201, json: { snapshot: sample, semantics: { approved: true, version: 2 } } });
    }
    if (path.endsWith("/snapshots")) return route.fulfill({ json: { snapshots } });
    if (path.endsWith("/runs")) return route.fulfill({ json: { runs: [], next_before: null } });
    if (path.endsWith("/pins")) return route.fulfill({ json: { pins: [] } });
    return route.fulfill({ status: 404, json: { detail: "not mocked" } });
  });
  await page.goto("/ask");
  await page.getByRole("region", { name: "Setup" }).getByRole("button", { name: "Try with sample data" }).click();
  const examples = page.getByRole("region", { name: "Example questions" });
  await examples.getByRole("button", { name: /net sales in October/ }).click();
  await expect(page.getByLabel("Your question")).toHaveValue("What were net sales in October after refunds?");
  await expect(page.getByLabel("Your question")).toBeFocused();
});
