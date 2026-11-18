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
