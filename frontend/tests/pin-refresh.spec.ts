import { test, expect } from "@playwright/test";

test("pins show their latest re-run and can opt into automatic re-runs", async ({ page }) => {
  const now = new Date().toISOString();
  const patches: unknown[] = [];
  const pins = [
    { pin_id: "pin_a", title: "Revenue", question_text: "Revenue", run_id: "run_old", version: 1, created_at: now, auto_refresh: false, latest_run_id: "run_new" },
    { pin_id: "pin_b", title: "Fees", question_text: "Fees", run_id: "run_fees", version: 1, created_at: now, auto_refresh: true, latest_run_id: "run_busy" },
  ];
  const SNAPSHOT = { snapshot_id: "snap_1", source_id: "src_1", version: 1, total_rows: 3, tables: ["orders"], approved: true, created_at: now };
  const runs = [
    { run_id: "run_old", snapshot_id: "snap_1", status: "completed", answer: { summary_markdown: "Old answer." } },
    { run_id: "run_new", snapshot_id: "snap_1", status: "completed", answer: { summary_markdown: "Fresh answer." } },
    { run_id: "run_fees", snapshot_id: "snap_1", status: "completed", answer: { summary_markdown: "Fees were 100." } },
    { run_id: "run_busy", status: "running", answer: null },
  ];
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === "PATCH") {
      patches.push(JSON.parse(route.request().postData() ?? "{}"));
      return route.fulfill({ json: { ...pins[0], auto_refresh: true } });
    }
    if (path.endsWith("/pins")) return route.fulfill({ json: { pins } });
    if (path.endsWith("/runs")) return route.fulfill({ json: { runs, next_before: null } });
    if (path.endsWith("/snapshots")) return route.fulfill({ json: { snapshots: [SNAPSHOT] } });
    return route.fulfill({ status: 404, json: { detail: "not mocked" } });
  });
  await page.goto("/dashboard");
  const revenue = page.getByRole("listitem").filter({ hasText: "Revenue" });
  await expect(revenue.getByText("Fresh answer.")).toBeVisible();
  await expect(revenue.getByText("Old answer.")).toHaveCount(0);
  const fees = page.getByRole("listitem").filter({ hasText: "Fees" });
  await expect(fees.getByText("Updating")).toBeVisible();
  await expect(fees.getByText("Fees were 100.")).toBeVisible();
  await expect(fees.getByRole("checkbox", { name: /Re-run automatically/ })).toBeChecked();
  await revenue.getByRole("checkbox", { name: /Re-run automatically/ }).click();
  await expect.poll(() => patches).toEqual([{ auto_refresh: true }]);
});
