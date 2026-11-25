import { test, expect, type Page } from "@playwright/test";

const RUN = {
  run_id: "run_chart1",
  question: "Sales by region",
  mode: "quick",
  status: "completed",
  created_at: new Date().toISOString(),
  snapshot_id: null,
  answer: {
    answer_id: "ans_1",
    summary_markdown: "North leads with 12.5.",
    charts: [
      {
        chart_id: "chart_abc",
        chart_type: "bar",
        title: "Total amount by region",
        x_field: "region",
        y_fields: ["total"],
        evidence_id: "ev_0123456789ab",
        rows: [["north", 12.5], ["south", 5]],
        truncated: false,
      },
    ],
  },
};

async function mockApi(page: Page, exports: string[]) {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const method = route.request().method();
    if (url.pathname.endsWith("/runs")) return route.fulfill({ json: { runs: [RUN], next_before: null } });
    if (url.pathname.endsWith("/pins")) {
      return route.fulfill({ json: { pins: [{ pin_id: "pin_1", title: "Sales by region", question_text: "Sales by region", run_id: RUN.run_id, version: 1, created_at: RUN.created_at }] } });
    }
    if (url.pathname.endsWith("/exports") && method === "POST") {
      exports.push(JSON.parse(route.request().postData() ?? "{}").format);
      return route.fulfill({ status: 201, json: { artifact_id: "exp_1", format: "pdf" } });
    }
    if (url.pathname.endsWith("/exports/exp_1")) return route.fulfill({ body: "%PDF-1.4", contentType: "application/octet-stream" });
    return route.fulfill({ status: 404, json: { detail: "not mocked" } });
  });
}

test("a finished answer shows its chart, data table and export menu", async ({ page }) => {
  const exports: string[] = [];
  await mockApi(page, exports);
  await page.goto(`/ask?run=${RUN.run_id}`);
  const answer = page.getByRole("region", { name: "Answer" });
  await expect(answer.getByText("North leads with 12.5.")).toBeVisible();
  const chart = answer.getByRole("figure");
  await expect(chart.getByText("Total amount by region")).toBeVisible();
  await expect(chart.getByRole("img", { name: /total by region, 2 points/ })).toBeVisible();
  await chart.getByText("Show data").click();
  await expect(chart.getByRole("cell", { name: "north" })).toBeVisible();

  await answer.getByRole("button", { name: "Export" }).click();
  const menu = page.getByRole("menu", { name: "Export answer" });
  await expect(menu.getByRole("menuitem")).toHaveCount(3);
  await expect(menu.getByRole("menuitem", { name: "PDF" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
  await answer.getByRole("button", { name: "Export" }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("menuitem", { name: "PDF" }).click();
  expect((await download).suggestedFilename()).toBe("sales-by-region.pdf");
  expect(exports).toEqual(["pdf"]);
});

test("pinned answers show their first chart on the dashboard", async ({ page }) => {
  await mockApi(page, []);
  await page.goto("/dashboard");
  await expect(page.getByRole("figure").getByText("Total amount by region")).toBeVisible();
});
