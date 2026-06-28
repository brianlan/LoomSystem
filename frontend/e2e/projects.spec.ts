import { expect, test } from '@playwright/test'
import {
  goToProjects,
  mockApiError,
  mockEmptyProjects,
  mockEmptySettings,
  mockGitHubLists,
  mockProjectMutations,
  mockProjects,
  mockSettingsRegistries,
} from './fixtures'

test.beforeEach(async ({ page }) => {
  await page.goto('/')
})

const registries = {
  agents: [{ id: 1, name: 'reviewer-v1', prompt_markdown: '', github_identity: 'reviewer-bot', permissions: {} }],
  models: [{ id: 2, provider_id: 'openai', model_id: 'gpt-4o', display_name: 'GPT-4o', custom_config: null }],
  images: [{ id: 3, image_name: 'ghcr.io/example/agent:latest' }],
}

test('project list shows empty state', async ({ page }) => {
  await mockEmptySettings(page)
  await mockEmptyProjects(page)
  await goToProjects(page)

  await expect(page.getByText('No projects yet. Create one to get started.')).toBeVisible()
})

test('project CRUD with mocked registries', async ({ page }) => {
  const projects: import('./fixtures').Project[] = []
  await mockEmptySettings(page)
  await mockSettingsRegistries(page, registries)
  await mockProjects(page, projects)
  await mockProjectMutations(page, { projects })
  await goToProjects(page)

  await page.getByRole('button', { name: 'New project' }).click()
  await page.getByLabel('Name').fill('LoomSystem')
  await page.getByLabel('Repo URL').fill('git@github.com:brianlan/LoomSystem.git')
  await page.locator('fieldset', { hasText: 'Reviewer config' }).getByLabel('Agent definition').selectOption('1')
  await page.locator('fieldset', { hasText: 'Reviewer config' }).getByLabel('Model entry').selectOption('2')
  await page.locator('fieldset', { hasText: 'Reviewer config' }).getByLabel('Docker image').selectOption('3')
  await page.getByRole('button', { name: 'Create project' }).click()

  await expect(page.getByText('LoomSystem')).toBeVisible()
  await expect(page.getByText('git@github.com:brianlan/LoomSystem.git')).toBeVisible()

  await page.getByRole('button', { name: 'Edit' }).click()
  await page.getByLabel('Name').fill('LoomSystem v2')
  await page.getByRole('button', { name: 'Update project' }).click()
  await expect(page.getByText('LoomSystem v2')).toBeVisible()

  await page.getByRole('button', { name: 'Delete' }).click()
  await expect(page.getByText('No projects yet. Create one to get started.')).toBeVisible()
})

test('issue and PR lists render deterministic GitHub data', async ({ page }) => {
  const projects: import('./fixtures').Project[] = [
    {
      id: 1,
      name: 'LoomSystem',
      repo_url: 'git@github.com:brianlan/LoomSystem.git',
      reviewer_config: {},
      implementor_config: {},
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    },
  ]
  await mockEmptySettings(page)
  await mockProjects(page, projects)
  await mockGitHubLists(page, 1, {
    issues: [
      { issue_number: 7, title: 'Add E2E harness', state: 'open', loom_status: 'unassigned', updated_at: new Date().toISOString() },
      { issue_number: 8, title: 'Fix flaky test', state: 'open', loom_status: 'in-progress', updated_at: new Date().toISOString() },
    ],
    pulls: [
      { pr_number: 12, title: 'Feature branch', state: 'open', merged: false, updated_at: new Date().toISOString() },
      { pr_number: 13, title: 'Hotfix', state: 'closed', merged: true, updated_at: new Date().toISOString() },
    ],
  })
  await goToProjects(page)

  await page.getByRole('button', { name: '▶' }).click()

  await expect(page.getByText('#7: Add E2E harness')).toBeVisible()
  await expect(page.getByText('#8: Fix flaky test')).toBeVisible()
  await expect(page.getByText('#12: Feature branch')).toBeVisible()
  await expect(page.getByText('#13: Hotfix')).toBeVisible()
  await expect(page.getByText('merged', { exact: true })).toBeVisible()
})

test('project list surfaces an API error', async ({ page }) => {
  await mockEmptySettings(page)
  await mockApiError(page, 'GET', '/projects', 'Projects API unavailable')
  await goToProjects(page)

  await expect(page.getByText('Projects API unavailable')).toBeVisible()
})
