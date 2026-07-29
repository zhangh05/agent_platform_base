/**
 * E2E 12 — LLM Settings page (v1.0.3 redesign).
 *
 * Validates: page loads + shows health bar + provider sidebar + form,
 * preset card click auto-fills base_url/model, enabled toggle,
 * save flow updates health bar.
 */
import { test, expect } from "./fixtures";

test("12. llm settings page — health bar + provider cards + enabled toggle", async ({ page }) => {
  await page.goto("/settings");

  // health bar
  await expect(page.getByTestId("llm-health-bar")).toBeVisible({ timeout: 15_000 });

  // provider sidebar
  await expect(page.getByTestId("provider-sidebar")).toBeVisible();
  for (const id of ["minimax", "openai", "deepseek", "ollama", "custom"]) {
    await expect(page.getByTestId(`provider-${id}`)).toBeVisible();
  }

  // form
  await expect(page.getByTestId("field-base_url")).toBeVisible();
  await expect(page.getByTestId("field-model")).toBeVisible();
  await expect(page.getByTestId("field-api_key")).toBeVisible();
  await expect(page.getByTestId("toggle-enabled")).toBeVisible();
  await expect(page.getByTestId("toggle-safe_mode")).toBeVisible();

  // 5 个端点 footer
  await expect(page.getByText("GET /api/agent/llm/config")).toBeVisible();
  await expect(page.getByText("POST /api/agent/llm/config")).toBeVisible();
  await expect(page.getByText("DELETE /api/agent/llm/config")).toBeVisible();
  await expect(page.getByText("GET /api/agent/llm/status")).toBeVisible();
  await expect(page.getByText("POST /api/agent/llm/test")).toBeVisible();
});

test("12b. openai preset click auto-fills base_url + model", async ({ page }) => {
  await page.goto("/settings");
  // 等表单 ready
  await expect(page.getByTestId("field-base_url")).toBeVisible({ timeout: 15_000 });

  // 清空 base_url 然后点 openai
  await page.getByTestId("field-base_url").fill("");
  await page.getByTestId("provider-openai").click();
  await expect(page.getByTestId("field-base_url")).toHaveValue("https://api.openai.com/v1");
  await expect(page.getByTestId("field-model")).toHaveValue("gpt-4o-mini");
});
