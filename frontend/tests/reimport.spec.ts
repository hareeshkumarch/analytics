import { test, expect } from "@playwright/test";

const SNAPSHOT = {
  snapshot_id: "snap_1",
  source_id: "src_abc_123",
  version: 3,
  total_rows: 10,
  tables: ["orders"],
  approved: true,
  created_at: new Date().toISOString(),
  source_family: "mysql",
  reimport: {
    connector_id: "mysql",
    values: { host: "db.example.com", port: "3306", database: "shop", tls_mode: "verify-full" },
    selected_tables: ["orders"],
  },
};

test("a dataset can be imported again with its saved settings", async ({ page }) => {
  const seen: { body: Record<string, unknown> | null } = { body: null };
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/snapshots")) return route.fulfill({ json: { snapshots: [SNAPSHOT] } });
    if (path.endsWith("/connectors/extract")) {
      seen.body = JSON.parse(route.request().postData() ?? "{}");
      return route.fulfill({ status: 500, json: { detail: "stop here" } });
    }
    if (path.endsWith("/connectors/probe")) return route.fulfill({ json: { ok: true, family: "mysql", message: "ok" } });
    if (path.endsWith("/connectors/discover")) {
      const table = (name: string) => ({ name, estimated_rows: 5, columns: [] });
      return route.fulfill({ json: { tables: [table("orders"), table("customers")] } });
    }
    return route.fulfill({ status: 404, json: { detail: "not mocked" } });
  });
  await page.goto("/data");
  await expect(page.getByRole("region", { name: "Add data" })).toHaveCount(0);
  await page.getByRole("button", { name: "Import new version" }).click();
  const add = page.getByRole("region", { name: "Add data" });
  await expect(add.getByText(/New version of/)).toBeVisible();
  await expect(add.getByLabel("Host")).toHaveValue("db.example.com");
  await expect(add.getByLabel("Database")).toHaveValue("shop");
  await expect(add.getByLabel("Password", { exact: true })).toHaveValue("");
  await add.getByLabel("Username").fill("reader");
  await add.getByLabel("Password", { exact: true }).fill("s3cret");
  await add.getByRole("button", { name: "Test connection" }).click();
  await add.getByRole("button", { name: "Import 1 table" }).click();
  await expect.poll(() => seen.body).not.toBeNull();
  expect(seen.body).toMatchObject({
    source_id: "src_abc_123",
    selected_tables: ["orders"],
    credentials: { username: "reader", password: "s3cret" },
    form: { connector_id: "mysql", values: { host: "db.example.com", database: "shop" } },
  });
  expect(JSON.stringify(seen.body?.form)).not.toContain("s3cret");
  await add.getByRole("button", { name: "Change source" }).click();
  await expect(add.getByRole("heading", { name: "Add data" })).toBeVisible();
});
