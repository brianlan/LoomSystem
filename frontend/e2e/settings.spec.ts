import { expect, test } from '@playwright/test'
import {
  goToSettings,
  mockApiError,
  mockEmptySettings,
  mockSettingsMutations,
  mockSettingsRegistries,
} from './fixtures'

test.beforeEach(async ({ page }) => {
  await page.goto('/')
})

test('settings shows empty states before registration', async ({ page }) => {
  await mockEmptySettings(page)
  await goToSettings(page)

  await expect(page.getByText('No agent definitions yet.')).toBeVisible()
  await expect(page.getByText('No model entries yet.')).toBeVisible()
  await expect(page.getByText('No docker images yet.')).toBeVisible()
})

test('settings registration flow for agent, model, docker image, SSH key, GitHub token, and triage config', async ({
  page,
}) => {
  await mockEmptySettings(page)
  await mockSettingsMutations(page)
  await goToSettings(page)

  await page.getByRole('button', { name: 'New agent definition' }).click()
  await page.getByLabel('Name').fill('reviewer-v1')
  await page.getByLabel('GitHub identity').fill('reviewer-bot')
  await page.getByLabel('Prompt markdown').fill('Review code carefully.')
  await page.locator('.modal').getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('reviewer-v1')).toBeVisible()
  await expect(page.getByText('gh: reviewer-bot')).toBeVisible()

  await page.getByPlaceholder('provider_id').fill('openai')
  await page.getByPlaceholder('model_id').fill('gpt-4o')
  await page.getByPlaceholder('credentials').fill('sk-test')
  await page.getByPlaceholder('display_name (optional)').fill('OpenAI GPT-4o')
  await page.locator('.registry', { hasText: 'Model entries' }).getByRole('button', { name: 'Add' }).click()
  await expect(page.getByText('openai/gpt-4o (OpenAI GPT-4o)')).toBeVisible()

  await page.getByPlaceholder('image_name').fill('ghcr.io/example/agent:latest')
  await page.locator('.registry', { hasText: 'Docker images' }).getByRole('button', { name: 'Add' }).click()
  await expect(page.getByText('ghcr.io/example/agent:latest')).toBeVisible()

  await page.locator('.registry', { hasText: 'SSH key' }).getByRole('textbox').fill('-----BEGIN OPENSSH PRIVATE KEY-----')
  await page.locator('.registry', { hasText: 'SSH key' }).getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('SSH key saved.')).toBeVisible()

  await page.locator('.registry', { hasText: 'App GitHub token' }).getByRole('textbox').fill('ghp_test_token')
  await page.locator('.registry', { hasText: 'App GitHub token' }).getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('GitHub token saved.')).toBeVisible()

  await page.locator('.registry', { hasText: 'Triage config' }).getByPlaceholder('endpoint_url').fill('http://localhost:9000')
  await page.locator('.registry', { hasText: 'Triage config' }).getByPlaceholder('model_name').fill('triage-model')
  await page.locator('.registry', { hasText: 'Triage config' }).getByPlaceholder('api_key').fill('triage-key')
  await page.locator('.registry', { hasText: 'Triage config' }).getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('Triage config saved.')).toBeVisible()
})

test('settings surfaces an API error', async ({ page }) => {
  await mockEmptySettings(page)
  await mockSettingsMutations(page)
  await mockApiError(page, 'POST', '/settings/model-entries', 'Model service unavailable')
  await goToSettings(page)

  await page.getByPlaceholder('provider_id').fill('openai')
  await page.getByPlaceholder('model_id').fill('gpt-4o')
  await page.getByPlaceholder('credentials').fill('sk-test')
  await page.locator('.registry', { hasText: 'Model entries' }).getByRole('button', { name: 'Add' }).click()
  await expect(page.getByText('Model service unavailable')).toBeVisible()
})

test('settings pre-fills existing registries', async ({ page }) => {
  await mockSettingsRegistries(page, {
    agents: [{ id: 1, name: 'agent-a', prompt_markdown: '', github_identity: 'a-bot', permissions: {} }],
    models: [{ id: 2, provider_id: 'openai', model_id: 'gpt-4o', display_name: 'GPT-4o', custom_config: null }],
    images: [{ id: 3, image_name: 'node:20' }],
  })
  await goToSettings(page)

  await expect(page.getByText('agent-a')).toBeVisible()
  await expect(page.getByText('openai/gpt-4o (GPT-4o)')).toBeVisible()
  await expect(page.getByText('node:20')).toBeVisible()
})
