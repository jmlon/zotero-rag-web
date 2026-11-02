import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

/** Each test runs in a fresh browser context: a new browser id, so its own conversations. */

async function ask(page: Page, question: string) {
  const box = page.getByRole("textbox", { name: /ask a question|escribe una pregunta/i });
  await box.fill(question);
  await box.press("Enter");
}

async function lastAnswer(page: Page) {
  const answer = page.locator("article.answer").last();
  await expect(answer.getByRole("button", { name: /copy|copiar/i })).toBeVisible();
  return answer;
}

async function expectAccessible(page: Page) {
  const results = await new AxeBuilder({ page }).analyze();
  const serious = results.violations.filter((v) => ["serious", "critical"].includes(v.impact ?? ""));
  expect(serious.map((v) => `${v.id}: ${v.nodes.map((n) => n.target).join(", ")}`)).toEqual([]);
}

test("asking from the welcome screen streams a cited answer", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "What would you like to know?" })).toBeVisible();
  await expect(page.getByText(/Test Library: 2 items · synced/)).toBeVisible();

  await ask(page, "What is a fog node?");
  await expect(page).toHaveURL(/\/c\/[0-9a-f-]{36}$/);
  const answer = await lastAnswer(page);
  await expect(answer).toContainText("A fog node sits between the edge devices and the cloud.");
  await expect(answer.getByRole("region", { name: "Sources" })).toContainText(
    "What is a Fog Node — Tordera, Eva (2016), p. 1",
  );

  await answer.getByRole("button", { name: "[1] What is a Fog Node" }).click();
  const card = page.getByRole("dialog");
  await expect(card.getByRole("link", { name: /Open in Zotero/ })).toHaveAttribute(
    "href",
    "https://www.zotero.org/groups/1/items/P1/attachment/A1/reader",
  );
  await page.keyboard.press("Escape");

  await answer.getByRole("button", { name: "Copy" }).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain("**Sources**");

  const sidebar = page.getByRole("navigation", { name: "Conversations" });
  await expect(sidebar.getByRole("link", { name: "What is a fog node?" })).toBeVisible();
  await expectAccessible(page);
});

test("a follow-up shows what it searched for", async ({ page }) => {
  await page.goto("/");
  await ask(page, "What is a fog node?");
  await lastAnswer(page);
  await ask(page, "Where does it sit?");
  await expect(page.getByText("Searched for: Where does it sit? (with context)")).toBeVisible();
  await expect(page.locator("article.answer")).toHaveCount(2);
});

test("stop keeps the start of the answer, also after a reload", async ({ page }) => {
  await page.goto("/");
  await ask(page, "What is a fog node? Answer slowly.");
  const answer = page.locator("article.answer").last();
  await expect(answer).toContainText("From the library:");
  await page.getByRole("button", { name: "Stop" }).click();
  await expect(answer).toContainText("Stopped.");
  // Retry appears once the saved answer has replaced the streamed one.
  await expect(answer.getByRole("button", { name: "Retry" })).toBeVisible();
  await page.reload();
  const saved = page.locator("article.answer").last();
  await expect(saved).toContainText("From the library");
  await expect(saved).toContainText("Stopped.");
  await expect(saved).not.toContainText("cloud. [1]");
  await expect(saved.getByRole("button", { name: "Retry" })).toBeVisible();
});

test("filters narrow the search, and can be lifted in one click", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Filters" }).click();
  await page.getByLabel("Collection").selectOption({ label: "Routing" });
  await expect(page.getByRole("button", { name: "Filters (1)" })).toBeVisible();
  await ask(page, "What is a fog node?");
  const answer = await lastAnswer(page);
  await expect(answer).not.toContainText("What is a Fog Node");

  await answer.getByRole("button", { name: "Search the whole library instead" }).click();
  await expect(page.getByRole("separator")).toContainText("Filters changed");
  const widened = await lastAnswer(page);
  await expect(widened).toContainText("A fog node sits between");
  await expect(page.getByRole("button", { name: "Filters", exact: true })).toBeVisible();
});

test("the interface speaks Spanish, and remembers it", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("group", { name: "Language" }).getByRole("button", { name: "ES" }).click();
  await expect(page.getByRole("heading", { name: "¿Qué te gustaría saber?" })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "es");
  await page.reload();
  await expect(page.getByRole("heading", { name: "¿Qué te gustaría saber?" })).toBeVisible();
  await ask(page, "¿Qué es un nodo fog?");
  const answer = await lastAnswer(page);
  await expect(answer.getByRole("region", { name: "Fuentes" })).toBeVisible();
  await expectAccessible(page);
});

test("on a phone the conversations are a drawer @phone", async ({ page }) => {
  await page.goto("/");
  const sidebar = page.getByRole("navigation", { name: "Conversations" });
  await expect(sidebar).toBeHidden();
  await ask(page, "What is a fog node?");
  await lastAnswer(page);
  await page.getByRole("button", { name: "Conversations" }).click();
  await expect(sidebar.getByRole("link", { name: "What is a fog node?" })).toBeVisible();
  await sidebar.getByRole("link", { name: "What is a fog node?" }).click();
  await expect(sidebar).toBeHidden();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
  await expectAccessible(page);
});
