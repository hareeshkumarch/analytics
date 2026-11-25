import { test, expect } from "@playwright/test";

test("navigates the main views in journey order", async ({ page }) => {
  await page.goto("/ask");
  await expect(page.getByRole("heading", { name: "What would you like to know?" })).toBeVisible();
  const deep = page.getByRole("group", { name: "Run mode" }).getByRole("button", { name: "deep" });
  await deep.click();
  await expect(deep).toHaveAttribute("aria-pressed", "true");

  const nav = page.getByRole("navigation", { name: "Primary Navigation" }).last();
  await nav.getByRole("link", { name: "Data" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Data" })).toBeVisible();
  await nav.getByRole("link", { name: "Dashboard" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Dashboard" })).toBeVisible();
  await nav.getByRole("link", { name: "Settings" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible();
});

test("connector catalog search, category filter and selection", async ({ page }) => {
  await page.goto("/data");
  const catalog = page.getByRole("list", { name: "Connectors" });

  await page.getByRole("searchbox", { name: "Search connectors" }).fill("stripe");
  await expect(catalog.getByRole("button")).toHaveCount(1);
  await catalog.getByRole("button", { name: /Stripe/ }).click();
  await expect(page.getByRole("heading", { name: "Stripe" })).toBeVisible();
  await page.getByRole("button", { name: "Change source" }).click();

  await page.getByRole("searchbox", { name: "Search connectors" }).fill("");
  await page.getByRole("group", { name: "Connector category" }).getByRole("button", { name: /Databases/ }).click();
  await expect(catalog.getByRole("button", { name: /PostgreSQL/ }).first()).toBeVisible();
  await expect(catalog.getByRole("button", { name: /Upload a file/ })).toHaveCount(0);
});

test("unknown pages show a way back", async ({ page }) => {
  await page.goto("/does-not-exist");
  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
  await page.getByRole("link", { name: "Go to Ask" }).click();
  await expect(page).toHaveURL(/\/ask/);
});

test("usage filters survive a reload through the URL", async ({ page }) => {
  await page.goto("/usage");
  const windows = page.getByRole("group", { name: "Time window" });
  await windows.getByRole("button", { name: "30 days" }).click();
  await expect(page).toHaveURL(/window=30d/);
  await page.reload();
  await expect(page.getByRole("group", { name: "Time window" }).getByRole("button", { name: "30 days" })).toHaveAttribute("aria-pressed", "true");
});

test("theme radiogroup moves selection with arrow keys", async ({ page }) => {
  await page.goto("/ask");
  const group = page.getByRole("radiogroup", { name: "Theme" });
  const checked = group.getByRole("radio", { checked: true });
  await checked.focus();
  const before = await checked.textContent();
  await page.keyboard.press("ArrowRight");
  const after = group.getByRole("radio", { checked: true });
  await expect(after).not.toHaveText(before ?? "");
  await expect(after).toBeFocused();
  await expect(group.getByRole("radio", { checked: false }).first()).toHaveAttribute("tabindex", "-1");
});
