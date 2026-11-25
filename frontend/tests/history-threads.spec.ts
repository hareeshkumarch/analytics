import { test, expect } from "@playwright/test";

const at = (minutesAgo: number) => new Date(Date.now() - minutesAgo * 60_000).toISOString();
const run = (run_id: string, question: string, conversation_id: string, minutesAgo: number) => ({
  run_id,
  workspace_id: "default",
  question,
  conversation_id,
  mode: "quick",
  status: "completed",
  created_at: at(minutesAgo),
  answer: { summary_markdown: `Answer to ${question}` },
});

test("history nests follow-ups under the question they continue", async ({ page }) => {
  const runs = [
    run("run_c", "Unrelated question", "conv_2", 30),
    run("run_b", "And in September?", "conv_1", 5),
    run("run_a", "What were fees in October?", "conv_1", 20),
  ];
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/runs")) return route.fulfill({ json: { runs, next_before: null } });
    if (path.endsWith("/pins")) return route.fulfill({ json: { pins: [] } });
    return route.fulfill({ status: 404, json: { detail: "not mocked" } });
  });
  await page.goto("/ask");
  const history = page.getByRole("navigation", { name: "Question history" });
  const thread = history.getByRole("list", { name: "Conversation: What were fees in October?" });
  await expect(thread.getByRole("button")).toHaveCount(2);
  await expect(thread.getByRole("button").nth(1)).toContainText("Follow-up: And in September?");
  await expect(history.locator("button .line-clamp-2")).toHaveText([
    "What were fees in October?",
    "And in September?",
    "Unrelated question",
  ]);
});
