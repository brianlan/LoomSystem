import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import {
  mockApiError,
  mockConsoleStream,
  mockEmptyAuditEvents,
  mockEmptyNotifications,
  mockImplementorStatus,
  mockProject,
  mockReviewerLifecycle,
} from './fixtures'

const project = {
  id: 1,
  name: 'loom-demo',
  repo_url: 'git@github.com:owner/repo.git',
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
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

const emptyImplementorStatus = {
  project_id: 1,
  state: 'idle',
  running_implementors: 0,
  implementors: [],
}

function reviewerPanel(page: Page) {
  return page.locator('.agent-panel').filter({ hasText: 'Reviewer' })
}

test('reviewer launch shows running status', async ({ page }) => {
  await mockProject(page, project)
  await mockReviewerLifecycle(page, 1)
  await mockImplementorStatus(page, 1, emptyImplementorStatus)
  await mockEmptyNotifications(page, 1)

  await page.goto('/')
  await page.getByRole('button', { name: 'Operator' }).click()
  await page.getByLabel('Project').selectOption('1')

  const panel = reviewerPanel(page)
  await expect(panel.getByText('No reviewers.')).toBeVisible()
  await panel.getByRole('button', { name: 'Launch' }).click()
  await expect(panel.getByText('1/1 running')).toBeVisible()
  await expect(panel.getByRole('button', { name: '#101' })).toBeVisible()
  await expect(panel.getByText('running', { exact: true }).first()).toBeVisible()
})

test('reviewer manual trigger and termination', async ({ page }) => {
  await mockProject(page, project)
  await mockReviewerLifecycle(page, 1, {
    reviewer_cap: 1,
    reviewers: [
      {
        agent_instance_id: 101,
        container_id: 'container-101',
        container_name: 'reviewer-101',
        session_id: 'session-101',
        status: 'running',
      },
    ],
  })
  await mockImplementorStatus(page, 1, emptyImplementorStatus)
  await mockEmptyNotifications(page, 1)

  await page.goto('/')
  await page.getByRole('button', { name: 'Operator' }).click()
  await page.getByLabel('Project').selectOption('1')

  const panel = reviewerPanel(page)
  await panel.getByRole('button', { name: 'Trigger' }).click()
  await expect(panel.getByRole('button', { name: 'Trigger' })).toBeEnabled()

  await panel.getByRole('button', { name: 'Terminate' }).click()
  await expect(panel.getByText('No reviewers.')).toBeVisible()
})

test('console stream renders history then live chunks and surfaces disconnect', async ({ page }) => {
  await mockProject(page, project)
  await mockReviewerLifecycle(page, 1, {
    reviewer_cap: 1,
    reviewers: [
      {
        agent_instance_id: 101,
        container_id: 'container-101',
        container_name: 'reviewer-101',
        session_id: 'session-101',
        status: 'running',
      },
    ],
  })
  await mockImplementorStatus(page, 1, emptyImplementorStatus)
  await mockEmptyNotifications(page, 1)
  await mockEmptyAuditEvents(page, 101)
  await mockConsoleStream(page, 101, [
    { chunk_index: 0, content: 'history chunk', created_at: '2026-01-01T00:00:00Z' },
    { chunk_index: 1, content: 'live chunk one', created_at: '2026-01-01T00:00:01Z' },
    { chunk_index: 2, content: 'live chunk two', created_at: '2026-01-01T00:00:02Z' },
  ])

  await page.goto('/')
  await page.getByRole('button', { name: 'Operator' }).click()
  await page.getByLabel('Project').selectOption('1')

  const panel = reviewerPanel(page)
  await panel.getByRole('button', { name: '#101' }).click()

  const consoleLines = page.locator('.console-lines li')
  await expect(consoleLines).toHaveCount(3)
  await expect(consoleLines.nth(0)).toContainText('[0] history chunk')
  await expect(consoleLines.nth(1)).toContainText('[1] live chunk one')
  await expect(consoleLines.nth(2)).toContainText('[2] live chunk two')
  await expect(page.getByText('Console stream disconnected')).toBeVisible()
})

test('reviewer launch surfaces an API error', async ({ page }) => {
  await mockProject(page, project)
  await mockApiError(page, 'POST', '/api/v1/projects/1/reviewers/launch')
  await mockImplementorStatus(page, 1, emptyImplementorStatus)
  await mockEmptyNotifications(page, 1)

  await page.goto('/')
  await page.getByRole('button', { name: 'Operator' }).click()
  await page.getByLabel('Project').selectOption('1')

  const panel = reviewerPanel(page)
  await panel.getByRole('button', { name: 'Launch' }).click()
  await expect(panel.getByText('Server error')).toBeVisible()
})
