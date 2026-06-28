import type { Page, Route } from '@playwright/test'

export const API_PREFIX = '/api/v1'

export function json(body: unknown, status = 200) {
  return (route: Route) =>
    route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

export async function mockApiError(page: Page, method: string, urlPattern: string | RegExp, status = 500) {
  await page.route(urlPattern, (route) => {
    if (route.request().method() === method) {
      return route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Server error' }),
      })
    }
    return route.continue()
  })
}

export type ProjectFixture = {
  id: number
  name: string
  repo_url: string
  reviewer_config: Record<string, unknown>
  implementor_config: Record<string, unknown>
  created_at: string
  updated_at: string
}

export async function mockProjectList(page: Page, initial: ProjectFixture[]) {
  const projects = [...initial]

  await page.route(`${API_PREFIX}/projects`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(projects),
    }),
  )

  await page.route(new RegExp(`${API_PREFIX}/projects/(\\d+)`), (route) => {
    if (route.request().method() !== 'DELETE') return route.continue()
    const match = route.request().url().match(/\/projects\/(\d+)$/)
    const id = match ? Number(match[1]) : 0
    const index = projects.findIndex((p) => p.id === id)
    if (index === -1) {
      return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'Not found' }) })
    }
    projects.splice(index, 1)
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ message: 'Deleted' }) })
  })

  return { projects }
}

export async function mockEmptyReviewerStatus(page: Page, projectId: number) {
  await page.route(`${API_PREFIX}/projects/${projectId}/reviewers/status`, json({ project_id: projectId, reviewer_cap: 1, running_reviewers: 0, reviewers: [] }))
}

export type ImplementorInstance = {
  agent_instance_id: number
  issue_number: number | null
  container_id: string
  container_name: string
  status: string
}

export type ImplementorState = {
  state: string
  parallelism: number
  implementors: ImplementorInstance[]
}

export async function mockImplementorLifecycle(
  page: Page,
  projectId: number,
  initial: ImplementorState = { state: 'idle', parallelism: 1, implementors: [] },
  options: { startIssues?: number[] } = {},
) {
  const state = { ...initial, implementors: [...initial.implementors] }
  let nextId = 201

  await page.route(`${API_PREFIX}/projects/${projectId}/implementors/status`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        project_id: projectId,
        state: state.state,
        running_implementors: state.implementors.filter((i) => i.status === 'running').length,
        implementors: state.implementors,
      }),
    }),
  )

  await page.route(`${API_PREFIX}/projects/${projectId}/implementors/loop/start`, (route) => {
    state.state = 'running'
    if (options.startIssues) {
      for (const issue of options.startIssues) {
        const instanceId = nextId++
        state.implementors.push({
          agent_instance_id: instanceId,
          issue_number: issue,
          container_id: `container-${instanceId}`,
          container_name: `implementor-${instanceId}`,
          status: 'running',
        })
      }
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ message: 'Loop started' }) })
  })

  await page.route(`${API_PREFIX}/projects/${projectId}/implementors/loop/soft-stop`, (route) => {
    state.state = 'draining'
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ message: 'Soft stop requested' }) })
  })

  await page.route(`${API_PREFIX}/projects/${projectId}/implementors/loop/hard-stop`, (route) => {
    state.state = 'idle'
    state.implementors = []
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ message: 'Hard stop requested' }) })
  })

  await page.route(`${API_PREFIX}/projects/${projectId}/implementors`, (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    const instanceId = nextId++
    const instance: ImplementorInstance = {
      agent_instance_id: instanceId,
      issue_number: null,
      container_id: `container-${instanceId}`,
      container_name: `implementor-${instanceId}`,
      status: 'running',
    }
    let parsed: { issue_number?: number } = {}
    try {
      parsed = JSON.parse(route.request().postData() ?? '{}') as { issue_number?: number }
    } catch {
      parsed = {}
    }
    if (parsed.issue_number != null) instance.issue_number = parsed.issue_number
    state.implementors.push(instance)
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ agent_instance_id: instanceId, container_id: instance.container_id, container_name: instance.container_name }),
    })
  })

  await page.route(new RegExp(`${API_PREFIX}/projects/${projectId}/implementors/(\\d+)/terminate`), (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    const match = route.request().url().match(/\/implementors\/(\d+)\/terminate/)
    const id = match ? Number(match[1]) : 0
    state.implementors = state.implementors.filter((i) => i.agent_instance_id !== id)
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ message: 'Terminated' }) })
  })

  return state
}

export async function mockEmptyNotifications(page: Page, projectId: number) {
  await page.route(`${API_PREFIX}/projects/${projectId}/notifications?**`, json([]))
}
