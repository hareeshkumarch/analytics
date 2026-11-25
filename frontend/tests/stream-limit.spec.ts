import { test, expect } from "@playwright/test";

test("Ask keeps at most three live run streams open", async ({ page }) => {
  await page.addInitScript(() => {
    const Native = window.EventSource;
    const w = window as unknown as { openStreams: number; maxStreams: number };
    w.openStreams = 0;
    w.maxStreams = 0;
    class Tracked extends Native {
      private counted = true;
      constructor(url: string | URL, init?: EventSourceInit) {
        super(url, init);
        w.openStreams += 1;
        w.maxStreams = Math.max(w.maxStreams, w.openStreams);
      }
      close() {
        if (this.counted) w.openStreams -= 1;
        this.counted = false;
        super.close();
      }
    }
    window.EventSource = Tracked as typeof EventSource;
  });
  let created = 0;
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    if (path.endsWith("/runs") && method === "POST") {
      created += 1;
      return route.fulfill({ status: 202, json: { run_id: `run_${created}`, status: "queued", status_url: "", events_url: "" } });
    }
    if (path.endsWith("/events")) return route.fulfill({ status: 200, contentType: "text/event-stream", body: ": waiting\n\n" });
    if (path.endsWith("/runs")) return route.fulfill({ json: { runs: [], next_before: null } });
    if (path.endsWith("/providers")) return route.fulfill({ json: { providers: [{ provider_id: "openai", configured: true, key_status: "ready" }], models: [] } });
    if (path.endsWith("/snapshots")) {
      return route.fulfill({ json: { snapshots: [{ snapshot_id: "s1", source_id: "src", version: 1, total_rows: 1, tables: ["t"], approved: true, created_at: new Date().toISOString() }] } });
    }
    if (path.endsWith("/pins")) return route.fulfill({ json: { pins: [] } });
    return route.fulfill({ status: 404, json: { detail: "not mocked" } });
  });
  await page.goto("/ask");
  const box = page.getByLabel("Your question");
  for (let i = 1; i <= 5; i++) {
    await box.fill(`Question ${i}`);
    await box.press("Control+Enter");
    await expect.poll(() => created).toBe(i);
  }
  await expect.poll(() => page.evaluate(() => (window as unknown as { openStreams: number }).openStreams)).toBe(3);
  expect(await page.evaluate(() => (window as unknown as { maxStreams: number }).maxStreams)).toBeLessThanOrEqual(4);
});
