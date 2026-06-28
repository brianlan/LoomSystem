import type { Page } from '@playwright/test'
import { expect, test } from '@playwright/test'
import {
  mockEmptyNotifications,
  mockImplementorLaunchError,
  mockImplementorStatus,
  mockProjects,
  mockReviewerLaunchError,
  mockReviewerStatus,
  mockSettingsRegistries,
} from './fixtures'

const project = {
  id: 1,
  name: 'demo',
  repo_url: 'git@github.com:brianlan/demo.git',
  reviewer_config: {
    agent_definition_id: 1,
    model_entry_id: 1,
    docker_image_id: 1,
    trigger_interval_minutes: 15,
    reviewer_cap: 1,
  },
  implementor_config: {
    agent_definition_id: 1,
    model_entry_id: 1,
    docker_image_id: 1,
    trigger_interval_minutes: 15,
    parallelism: 1,
  },
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
}

const agents = [
  {
    id: 1,
    name: 'reviewer',
    prompt_markdown: '# Reviewer',
    github_identity: 'bot',
    permissions: {},
  },
]

const models = [
  { id: 1, provider_id: 'anthropic', model_id: 'claude-3', display_name: null },
]

const images = [{ id: 1, image_name: 'loomsystem/runtime:latest' }]

const emptyReviewerStatus = {
  project_id: project.id,
  reviewer_cap: 1,
  running_reviewers: 0,
  reviewers: [],
}

const emptyImplementorStatus = {
  project_id: project.id,
  state: 'stopped',
  running_implementors: 0,
  implementors: [],
}

async function goToOperator(page: Page) {
  await page.goto('/')
  await page.getByRole('button', { name: 'Operator' }).click()
  await page.locator('.project-selector select').selectOption(String(project.id))
}

test.beforeEach(async ({ page }) => {
  await mockSettingsRegistries(page, { agents, models, images })
  await mockProjects(page, [project])
  await mockReviewerStatus(page, project.id, emptyReviewerStatus)
  await mockImplementorStatus(page, project.id, emptyImplementorStatus)
  await mockEmptyNotifications(page, project.id)
})

test('reviewer launch failure surfaces a clear error and leaves no running reviewer', async ({
  page,
}) => {
  await mockReviewerLaunchError(
    page,
    project.id,
    'Image loomsystem/runtime:latest not available: simulated failure',
  )

  await goToOperator(page)

  await page.locator('.agent-panel').first().getByRole('button', { name: 'Launch' }).click()

  const reviewerPanel = page.locator('.agent-panel').first()
  await expect(reviewerPanel.locator('.error-banner')).toContainText('not available')
  await expect(reviewerPanel).toContainText('0/1 running')
  await expect(reviewerPanel).toContainText('No reviewers.')
})

test('implementor launch failure surfaces a clear error and leaves no running implementor', async ({
  page,
}) => {
  await mockImplementorLaunchError(
    page,
    project.id,
    "Credential missing for provider 'openai'",
  )

  await goToOperator(page)

  const implementorPanel = page.locator('.agent-panel').nth(1)
  await implementorPanel.locator('input[type="number"]').fill('1')
  await implementorPanel.getByRole('button', { name: 'Launch' }).click()

  await expect(implementorPanel.locator('.error-banner')).toContainText('Credential missing')
  await expect(implementorPanel).toContainText('0/1 running')
  await expect(implementorPanel).toContainText('No implementors.')
})
