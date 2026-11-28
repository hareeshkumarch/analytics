import { expect, test } from "@playwright/test";

const SNAPSHOT = {
  snapshot_id: "snap_1",
  workspace_id: "default",
  source_id: "src_orders",
  version: 1,
  tables: ["orders"],
  total_rows: 120,
  total_normalized_bytes: 4096,
  approved: true,
  semantic_version_id: "sem_1",
  display_name: "Orders",
  source_family: "uploaded_file",
  created_at: new Date().toISOString(),
  table_details: [{ name: "orders", row_count: 120, columns: ["order_id", "payload", "amount"] }],
};

test("ask shows full multi-turn thread inline with limitations, removed dataset pill, and single bottom composer", async ({ page }) => {
  const runs = [
    {
      run_id: "run_t1",
      conversation_id: "conv_1",
      question: "Total revenue?",
      mode: "quick",
      status: "completed",
      snapshot_id: "snap_deleted",
      created_at: "2026-11-26T10:00:00Z",
      answer: {
        answer_id: "ans_1",
        summary_markdown: "Total revenue was **$12,400**.",
        charts: [],
        outcome: "success",
        criteria: [],
        limitations: ["Excludes pending refunds from the last 24 hours."],
        follow_ups: ["Break down by region"],
      },
    },
    {
      run_id: "run_t2",
      conversation_id: "conv_1",
      question: "And by region?",
      mode: "deep",
      status: "completed",
      snapshot_id: "snap_deleted",
      created_at: "2026-11-26T10:02:00Z",
      answer: {
        answer_id: "ans_2",
        summary_markdown: "North contributed **$7,200**.",
        charts: [],
        outcome: "success",
        criteria: [],
        limitations: ["Regional tags missing for 2% of legacy rows."],
        follow_ups: [],
      },
    },
  ];

  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/workspaces")) return route.fulfill({ json: { workspaces: [{ workspace_id: "default", name: "Default Workspace" }] } });
    if (path.endsWith("/health")) return route.fulfill({ json: { status: "ok", input_limits: { max_question_chars: 1000 } } });
    if (path.endsWith("/providers")) return route.fulfill({ json: { providers: [{ provider_id: "openai", configured: true }], models: [] } });
    if (path.endsWith("/routing")) return route.fulfill({ json: { preset: "balanced", fallback_enabled: true, policy_version: 1, role_routes: {} } });
    if (path.endsWith("/snapshots")) return route.fulfill({ json: { snapshots: [SNAPSHOT] } });
    if (path.endsWith("/runs")) return route.fulfill({ json: { runs } });
    if (path.endsWith("/events")) return route.fulfill({ status: 200, contentType: "text/event-stream", body: "" });
    if (path.endsWith("/pins")) return route.fulfill({ json: { pins: [] } });
    return route.fulfill({ status: 404, json: { detail: "not mocked" } });
  });

  await page.goto("/ask?run=run_t2");
  await expect(page.getByText("Total revenue was $12,400.")).toBeVisible();
  await expect(page.getByText("North contributed $7,200.")).toBeVisible();
  await expect(page.getByText("Excludes pending refunds from the last 24 hours.")).toBeVisible();
  await expect(page.getByText("Regional tags missing for 2% of legacy rows.")).toBeVisible();
  await expect(page.getByText("Removed dataset").first()).toBeVisible();
  // Unified composer: top new-question textarea is hidden while viewing a conversation thread.
  await expect(page.getByLabel("Your question")).toHaveCount(0);
  await expect(page.getByLabel("Ask a follow-up")).toBeVisible();
  await expect(page.getByRole("region", { name: "Answer", exact: true })).toHaveCount(1);
});

test("workspace switcher, left rail stability on 1024px, and mobile input font sizing", async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  const workspaces = [{ workspace_id: "default", name: "Default Workspace", business_timezone: "UTC" }];

  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === "/api/v1/workspaces" && req.method() === "GET") return route.fulfill({ json: { workspaces } });
    if (path === "/api/v1/workspaces" && req.method() === "POST") {
      const body = JSON.parse(req.postData() ?? "{}");
      const created = { workspace_id: "finance_ops", name: body.name, business_timezone: "UTC" };
      workspaces.push(created);
      return route.fulfill({ status: 201, json: created });
    }
    if (path.endsWith("/health")) return route.fulfill({ json: { status: "ok" } });
    if (path.endsWith("/providers")) return route.fulfill({ json: { providers: [{ provider_id: "openai", configured: true }], models: [] } });
    if (path.endsWith("/routing")) return route.fulfill({ json: { preset: "balanced", fallback_enabled: true, policy_version: 1, role_routes: {} } });
    if (path.endsWith("/snapshots")) return route.fulfill({ json: { snapshots: [] } });
    if (path.endsWith("/runs")) return route.fulfill({ json: { runs: [] } });
    if (path.endsWith("/pins")) return route.fulfill({ json: { pins: [] } });
    return route.fulfill({ status: 404, json: {} });
  });

  await page.goto("/ask");
  const railBox = await page.locator("aside[aria-label='Workspace Navigation']").boundingBox();
  expect(railBox?.width).toBeGreaterThanOrEqual(200);
  expect(railBox?.width).toBeLessThanOrEqual(230);

  const switchBtn = page.getByRole("button", { name: /Switch workspace/ });
  const labelTruncated = await switchBtn.locator("span.truncate").evaluate((el) => el.scrollWidth > el.clientWidth + 1);
  expect(labelTruncated).toBe(false);

  await page.getByRole("radio", { name: "Dark" }).click();
  await switchBtn.click();
  await page.getByRole("menuitem", { name: "New workspace" }).click();
  const wsDialog = page.getByRole("dialog", { name: "Create workspace" });
  await expect(wsDialog).toBeVisible();
  const wsBg = await wsDialog.evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(wsBg).toBe("rgb(48, 46, 43)");
  await page.getByLabel("Workspace name").fill("Finance Ops");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page.getByRole("button", { name: /Workspace: Finance Ops/ })).toBeVisible();

  await page.setViewportSize({ width: 375, height: 812 });
  const fontSize = await page.getByLabel("Your question").evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
  expect(fontSize).toBeGreaterThanOrEqual(16);
});

test("data definitions can be filtered and edited, preview cells open modal, and file/paste tabs isolate drafts", async ({ page }) => {
  let semantics = {
    semantic_version_id: "sem_1",
    snapshot_id: "snap_1",
    version: 1,
    approved: false,
    metrics: [
      { metric_id: "m_rev", display_name: "Gross Revenue", expression: "SUM(amount)", grain: "orders", unit: "USD", currency: "USD" },
      { metric_id: "m_cnt", display_name: "Order Count", expression: "COUNT(*)", grain: "orders", unit: "orders", currency: null },
    ],
  };

  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path.endsWith("/workspaces")) return route.fulfill({ json: { workspaces: [{ workspace_id: "default", name: "Default Workspace" }] } });
    if (path.endsWith("/health")) return route.fulfill({ json: { status: "ok", limits: { max_upload_bytes: 1000000 } } });
    if (path.endsWith("/snapshots")) return route.fulfill({ json: { snapshots: [{ ...SNAPSHOT, approved: false }] } });
    if (path.endsWith("/semantics/snap_1") && req.method() === "GET") return route.fulfill({ json: semantics });
    if (path.endsWith("/semantics/snap_1") && req.method() === "PATCH") {
      const body = JSON.parse(req.postData() ?? "{}");
      semantics = {
        ...semantics,
        version: 2,
        metrics: semantics.metrics.map((m) => {
          const edit = (body.metrics ?? []).find((e: { metric_id: string }) => e.metric_id === m.metric_id);
          return edit ? { ...m, display_name: edit.display_name, unit: edit.unit, currency: edit.currency } : m;
        }),
      };
      return route.fulfill({ json: semantics });
    }
    if (path.endsWith("/preview")) {
      return route.fulfill({
        json: {
          columns: ["order_id", "payload"],
          rows: [{ order_id: "ORD-1", payload: '{"gateway":"stripe","attempt":2,"note":"very long details"}' }],
          row_count: 1,
        },
      });
    }
    if (path.endsWith("/connectors")) return route.fulfill({ json: { connectors: [] } });
    return route.fulfill({ status: 404, json: {} });
  });

  await page.goto("/data?snapshot=snap_1");
  await page.getByLabel("Filter metrics").fill("Revenue");
  await expect(page.getByText("Gross Revenue")).toBeVisible();
  await expect(page.getByText("Order Count")).toHaveCount(0);
  await page.getByLabel("Filter metrics").fill("");

  await page.getByRole("button", { name: "Edit definitions" }).click();
  await page.getByLabel("Metric name for Gross Revenue").fill("Net Revenue");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Net Revenue")).toBeVisible();

  await page.getByRole("tab", { name: "Preview" }).click();
  await page.getByRole("button", { name: /stripe/ }).click();
  const previewDialog = page.getByRole("dialog");
  await expect(previewDialog).toBeVisible();
  const previewBg = await previewDialog.evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(previewBg).not.toBe("rgba(0, 0, 0, 0)");
  expect(previewBg).not.toBe("transparent");
  await expect(previewDialog).toContainText('"gateway": "stripe"');
  await page.getByRole("button", { name: "Close" }).click();

  await page.getByRole("button", { name: "Add data" }).click();
  await page.getByRole("button", { name: /Upload a file/ }).click();
  await page.getByRole("tab", { name: /Paste CSV/ }).click();
  await page.getByLabel("Paste CSV text").fill("a,b\n1,2");
  await page.getByRole("tab", { name: /Upload file/ }).click();
  await page.getByRole("tab", { name: /Paste CSV/ }).click();
  await expect(page.getByLabel("Paste CSV text")).toHaveValue("a,b\n1,2");
});

test("dashboard supports rename, in-place rerun, and export; usage decouples model metric and gap-fills; settings revokes key", async ({ page }) => {
  let pins = [
    {
      pin_id: "pin_1",
      title: "Daily Sales",
      question_text: "Daily sales?",
      run_id: "run_1",
      version: 1,
      created_at: "2026-11-26T10:00:00Z",
      auto_refresh: false,
      latest_run_status: null as string | null,
      schedule: "daily",
      snapshot: SNAPSHOT,
      shown_run: { run_id: "run_1", status: "completed", snapshot_id: "snap_1", answer: { summary_markdown: "Sales **$500**." } },
    },
  ];
  let configured = true;
  let exportPosted = false;

  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path.endsWith("/workspaces")) return route.fulfill({ json: { workspaces: [{ workspace_id: "default", name: "Default Workspace" }] } });
    if (path.endsWith("/health")) return route.fulfill({ json: { status: "ok", input_limits: { max_pin_title_chars: 120 } } });
    if (path.endsWith("/pins") && req.method() === "GET") return route.fulfill({ json: { pins } });
    if (path.endsWith("/pins/pin_1") && req.method() === "PATCH") {
      const body = JSON.parse(req.postData() ?? "{}");
      pins = pins.map((p) => ({ ...p, ...body }));
      return route.fulfill({ json: pins[0] });
    }
    if (path.endsWith("/pins/pin_1/refresh") && req.method() === "POST") {
      pins = pins.map((p) => ({ ...p, latest_run_status: "running" }));
      return route.fulfill({ status: 202, json: { pin_id: "pin_1", refresh_run_id: "run_2", status: "running", already_running: false } });
    }
    if (path.endsWith("/exports") && req.method() === "POST") {
      exportPosted = true;
      return route.fulfill({ json: { artifact_id: "exp_1" } });
    }
    if (path.endsWith("/exports/exp_1")) return route.fulfill({ status: 200, contentType: "application/pdf", body: "%PDF-1.4" });
    if (path.endsWith("/usage")) {
      return route.fulfill({
        json: {
          workspace_id: "default",
          run_count: 1,
          matched_count: 1,
          runs: [{ run_id: "run_1", question: "Sales?", status: "completed", mode: "quick", total_tokens: 100, total_cost_usd: "0.01", used_attempts: 1, incomplete: false, created_at: new Date().toISOString() }],
          buckets: [{ start: new Date().toISOString(), runs: 1, tokens: 100, cost_usd: "0.01" }],
          models: [{ provider_id: "openai", model_id: "gpt-4o", calls: 3, total_tokens: 100, total_cost_usd: "0.01", cost_unknown: false, avg_latency_ms: 500 }],
          tools: [],
        },
      });
    }
    if (path.endsWith("/providers")) return route.fulfill({ json: { providers: [{ provider_id: "openai", configured, key_status: configured ? "ready" : "missing" }], models: [] } });
    if (path.endsWith("/settings/provider-key/openai") && req.method() === "DELETE") {
      configured = false;
      return route.fulfill({ json: { provider_id: "openai", status: "removed" } });
    }
    if (path.endsWith("/routing")) return route.fulfill({ json: { preset: "balanced", fallback_enabled: true, policy_version: 1, role_routes: {} } });
    if (path.endsWith("/settings")) return route.fulfill({ json: { workspace_id: "default", business_timezone: "UTC" } });
    if (path.endsWith("/ops/switches")) return route.fulfill({ json: { switches: [], available: [], history: [] } });
    return route.fulfill({ status: 404, json: {} });
  });

  await page.goto("/dashboard");
  const dailyCard = page.getByRole("listitem").filter({ hasText: "Daily Sales" });
  await dailyCard.getByRole("button", { name: "More actions" }).click();
  await page.getByRole("menuitem", { name: "Rename title…" }).click();
  await page.getByLabel("Rename Daily Sales").fill("Weekly Sales");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("heading", { name: "Weekly Sales" })).toBeVisible();

  const weeklyCard = page.getByRole("listitem").filter({ hasText: "Weekly Sales" });
  await weeklyCard.getByRole("button", { name: "Re-run Weekly Sales" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
  await expect(weeklyCard.getByText("Updating")).toBeVisible();

  await weeklyCard.getByRole("button", { name: "More actions" }).click();
  await page.getByRole("menuitem", { name: "Export as PDF" }).click();
  await expect.poll(() => exportPosted).toBe(true);

  await page.goto("/usage");
  await expect(page.locator("section[aria-labelledby='usage-chart'] .recharts-bar-rectangle")).toHaveCount(7);
  const modelGroup = page.getByRole("region", { name: "By model" });
  await expect(modelGroup.getByRole("group", { name: "Model metric" })).toBeVisible();
  await modelGroup.getByRole("group", { name: "Model metric" }).getByRole("button", { name: "Cost" }).click();
  await expect(modelGroup.getByText("Bars show cost.")).toBeVisible();

  await page.goto("/settings");
  await page.getByRole("button", { name: "Remove key" }).click();
  await page.getByRole("button", { name: "Confirm remove" }).click();
  await expect(page.getByRole("button", { name: "Remove key" })).toHaveCount(0);
});

