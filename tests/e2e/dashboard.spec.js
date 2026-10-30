import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await page.locator("#username").fill("alice");
  await page.locator("#password").fill("test-password-long-enough");
  await page.locator("#login-button").click();
  await expect(page.locator("#workspace-view")).toBeVisible();
  const account = await (await page.request.get("/api/auth/me")).json();
  const deleted = await page.request.delete("/api/conversations", { headers: {
    Origin: "http://127.0.0.1:8765", "X-CSRF-Token": account.csrf_token,
  } });
  expect(deleted.status()).toBe(204);
  await page.reload();
  await expect(page.locator("#provider-badge")).toHaveText("gpt-4.1-mini");
});

test("server history survives an empty browser storage and a new login", async ({ page, browser }) => {
  await page.locator("#question").fill("Подготовь план запуска");
  await page.locator("#submit-button").click();
  await expect(page.locator("#answer-result")).toBeVisible();
  await expect(page.locator(".history-item")).toHaveCount(1);
  const context = await browser.newContext();
  const other = await context.newPage();
  await other.goto("http://127.0.0.1:8765");
  await other.locator("#username").fill("alice");
  await other.locator("#password").fill("test-password-long-enough");
  await other.locator("#login-button").click();
  await expect(other.locator(".history-item")).toHaveCount(1);
  await expect(other.locator("#answer-question")).toHaveText("Подготовь план запуска");
  await context.close();
});

test("continues a conversation and copies the response", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  for (const question of ["Подготовь план запуска", "Какие у этого плана риски?"]) {
    await page.locator("#question").fill(question);
    await page.locator("#submit-button").click();
    await expect(page.locator("#answer-question")).toHaveText(question);
    await expect(page.locator("#answer-result")).toBeVisible();
  }
  await expect(page.locator(".history-item")).toHaveCount(2);
  await expect(page.locator(".transcript-turn")).toHaveCount(1);
  await page.locator("#copy-button").click();
  await expect(page.locator("#copy-label")).toHaveText("Скопировано ✓");
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain("Уточнить цель");
});

test("reload restores an accepted queued request", async ({ page }) => {
  await page.locator("#question").fill("Подготовь план запуска");
  const accepted = page.waitForResponse(response => response.url().endsWith("/api/ask") && response.status() === 202);
  await page.locator("#submit-button").click();
  await accepted;
  await page.reload();
  await expect(page.locator("#answer-result")).toBeVisible();
  await expect(page.locator("#answer-question")).toHaveText("Подготовь план запуска");
});

test("provider errors remain visible after reload", async ({ page }) => {
  await page.locator("#question").fill("provider-error проверка ошибки");
  await page.locator("#submit-button").click();
  await expect(page.locator("#error-panel")).toBeVisible();
  await expect(page.locator("#error-message")).toContainText("лимит провайдера");
  await page.reload();
  await expect(page.locator("#error-panel")).toBeVisible();
  await expect(page.locator("#question")).toHaveValue("provider-error проверка ошибки");
});

test("model HTML is text, mobile fits viewport, logout clears private view", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator("#question").fill("xss-test проверка форматирования");
  await page.locator("#submit-button").click();
  await expect(page.locator("#answer-content")).toContainText("<script>");
  await expect(page.locator("#answer-content script, #answer-content img")).toHaveCount(0);
  expect(await page.evaluate(() => window.pwned)).toBeUndefined();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.locator("#logout-button").click();
  await expect(page.locator("#login-screen")).toBeVisible();
  await expect(page.locator("#workspace-view")).toBeHidden();
  await expect(page.locator("#answer-content")).toBeEmpty();
});
