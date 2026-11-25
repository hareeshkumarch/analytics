import { test, expect } from "@playwright/test";

test("a paused run shows the question and sends the answer with its revision", async ({ page }) => {
  const sent: unknown[] = [];
  const run = {
    run_id: "run_wait",
    workspace_id: "default",
    question: "Total fees?",
    mode: "quick",
    status: "awaiting_input",
    created_at: new Date().toISOString(),
    clarification_prompt: "Should totals be in INR or converted to USD?",
    clarification_revision: 2,
    answer: null,
  };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/clarify")) {
      sent.push(JSON.parse(route.request().postData() ?? "{}"));
      return route.fulfill({ json: { run_id: "run_wait", status: "queued", revision: 3 } });
    }
    if (path.endsWith("/events")) return route.fulfill({ status: 200, contentType: "text/event-stream", body: ": idle\n\n" });
    if (path.endsWith("/runs")) return route.fulfill({ json: { runs: [run], next_before: null } });
    if (path.endsWith("/pins")) return route.fulfill({ json: { pins: [] } });
    return route.fulfill({ status: 404, json: { detail: "not mocked" } });
  });
  await page.goto("/ask?run=run_wait");
  const answer = page.getByRole("region", { name: "Answer" });
  await expect(answer.getByText("Should totals be in INR or converted to USD?")).toBeVisible();
  const box = answer.getByLabel("Clarification answer");
  await expect(answer.getByRole("button", { name: "Continue" })).toBeDisabled();
  await box.fill("INR please");
  await answer.getByRole("button", { name: "Continue" }).click();
  await expect.poll(() => sent).toEqual([{ revision: 2, answer: "INR please" }]);
  await expect(answer.getByLabel("Clarification answer")).toHaveCount(0);
});
