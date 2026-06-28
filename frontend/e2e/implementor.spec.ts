import { expect, test, type Page } from '@playwright/test'
import {
  mockApiError,
  mockEmptyNotifications,
  mockEmptyReviewerStatus,
  mockImplementorLifecycle,
  mockProjectMutations,
  mockProjects,
  type Project,
} from './fixtures'

const project: Project = {
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
    parallelism: 3,
  },
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
}

function implementorPanel(page: Page) {
  return page.locator('.agent-panel').filter({ hasText: 'Implementor' })
}

async function openOperator(page: Page) {
  await page.goto('/')
  await page.getByRole('button', { name: 'Operator' }).click()
  await page.getByLabel('Project').selectOption('1')
}

test('implementor loop starts and shows N parallel implementors', async ({ page }) => {
  await mockProjects(page, [project])
  await mockEmptyReviewerStatus(page, 1)
  await mockImplementorLifecycle(
    page,
    1,
    { state: 'idle', parallelism: 3, implementors: [] },
    { startIssues: [1, 2, 3] },
  )
  await mockEmptyNotifications(page, 1)

  await openOperator(page)
  const panel = implementorPanel(page)
  await panel.getByRole('button', { name: 'Start loop' }).click()
  await expect(panel.getByText('Loop: running')).toBeVisible()
  await expect(panel.getByText('3/3 running')).toBeVisible()
  await expect(panel.locator('.agent-list li')).toHaveCount(3)
  await expect(panel.getByText('issue #1')).toBeVisible()
  await expect(panel.getByText('issue #2')).toBeVisible()
  await expect(panel.getByText('issue #3')).toBeVisible()
})

test('issue close/refill visible behavior', async ({ page }) => {
  await mockProjects(page, [{ ...project, implementor_config: { ...project.implementor_config, parallelism: 1 } }])
  await mockEmptyReviewerStatus(page, 1)
  const state = await mockImplementorLifecycle(page, 1, {
    state: 'running',
    parallelism: 1,
    implementors: [
      {
        agent_instance_id: 201,
        issue_number: 1,
        container_id: 'container-201',
        container_name: 'implementor-201',
        status: 'running',
      },
    ],
  })
  await mockEmptyNotifications(page, 1)

  await openOperator(page)
  const panel = implementorPanel(page)
  await expect(panel.getByText('issue #1')).toBeVisible()

  await page.route(new RegExp('/api/v1/projects/1/implementors/(\\d+)/terminate'), (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    state.implementors = state.implementors.filter((i) => i.agent_instance_id !== 201)
    state.implementors.push({
      agent_instance_id: 202,
      issue_number: 2,
      container_id: 'container-202',
      container_name: 'implementor-202',
      status: 'running',
    })
    return route.fulfill({ status: 200, body: JSON.stringify({ message: 'Terminated' }) })
  })

  await panel.getByRole('button', { name: 'Terminate' }).click()
  await expect(panel.getByText('issue #1')).not.toBeVisible()
  await expect(panel.getByText('issue #2')).toBeVisible()
})

test('soft stop prevents new launches while existing implementors remain visible', async ({ page }) => {
  await mockProjects(page, [{ ...project, implementor_config: { ...project.implementor_config, parallelism: 1 } }])
  await mockEmptyReviewerStatus(page, 1)
  await mockImplementorLifecycle(page, 1, {
    state: 'running',
    parallelism: 1,
    implementors: [
      {
        agent_instance_id: 201,
        issue_number: 1,
        container_id: 'container-201',
        container_name: 'implementor-201',
        status: 'running',
      },
    ],
  })
  await mockEmptyNotifications(page, 1)

  await openOperator(page)
  const panel = implementorPanel(page)
  await expect(panel.getByText('Loop: running')).toBeVisible()
  await panel.getByRole('button', { name: 'Soft stop' }).click()
  await expect(panel.getByText('Loop: draining')).toBeVisible()
  await expect(panel.getByText('issue #1')).toBeVisible()
})

test('hard stop terminates running implementors', async ({ page }) => {
  await mockProjects(page, [{ ...project, implementor_config: { ...project.implementor_config, parallelism: 1 } }])
  await mockEmptyReviewerStatus(page, 1)
  await mockImplementorLifecycle(page, 1, {
    state: 'running',
    parallelism: 1,
    implementors: [
      {
        agent_instance_id: 201,
        issue_number: 1,
        container_id: 'container-201',
        container_name: 'implementor-201',
        status: 'running',
      },
    ],
  })
  await mockEmptyNotifications(page, 1)

  await openOperator(page)
  const panel = implementorPanel(page)
  await expect(panel.getByText('issue #1')).toBeVisible()
  await panel.getByRole('button', { name: 'Hard stop' }).click()
  await expect(panel.getByText('Loop: idle')).toBeVisible()
  await expect(panel.getByText('0/1 running')).toBeVisible()
  await expect(panel.getByText('No implementors.')).toBeVisible()
})

test('project deletion removes project and running-agent UI state', async ({ page }) => {
  const projects: Project[] = [project]
  await mockProjects(page, projects)
  await mockProjectMutations(page, { projects })
  await mockEmptyReviewerStatus(page, 1)
  await mockImplementorLifecycle(page, 1, {
    state: 'running',
    parallelism: 1,
    implementors: [
      {
        agent_instance_id: 201,
        issue_number: 1,
        container_id: 'container-201',
        container_name: 'implementor-201',
        status: 'running',
      },
    ],
  })
  await mockEmptyNotifications(page, 1)

  await page.goto('/')
  await page.getByRole('button', { name: 'Operator' }).click()
  await page.getByLabel('Project').selectOption('1')
  await expect(implementorPanel(page).getByText('issue #1')).toBeVisible()

  await page.getByRole('button', { name: 'Projects' }).click()
  await page.getByRole('button', { name: 'Delete' }).click()
  await expect(page.getByText('No projects yet. Create one to get started.')).toBeVisible()

  await page.getByRole('button', { name: 'Operator' }).click()
  await expect(page.getByText('No projects yet.')).toBeVisible()
})

test('implementor launch surfaces an API error', async ({ page }) => {
  await mockProjects(page, [{ ...project, implementor_config: { ...project.implementor_config, parallelism: 1 } }])
  await mockEmptyReviewerStatus(page, 1)
  await mockApiError(page, 'POST', '/projects/1/implementors', 'Server error')
  await mockEmptyNotifications(page, 1)

  await openOperator(page)
  const panel = implementorPanel(page)
  await panel.locator('input[placeholder="1"]').fill('42')
  await panel.getByRole('button', { name: 'Launch' }).click()
  await expect(panel.getByText('Server error')).toBeVisible()
})
