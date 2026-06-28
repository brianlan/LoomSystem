import type { Page, Route } from '@playwright/test'

export const API_PREFIX = '/api/v1'

export function json(body: unknown, status = 200) {
  return (route: Route) =>
    route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

export function mockApiError(page: Page, method: string, urlPattern: string | RegExp, status = 500) {
  return page.route(urlPattern, (route) => {
    if (route.request().method() === method) {
      return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ detail: 'Server error' }) })
    }
    return route.continue()
  })
}

export async function mockProject(page: Page, project: Record<string, unknown>) {
  await page.route(`${API_PREFIX}/projects`, json([project]))
}

export async function mockEmptyProjectList(page: Page) {
  await page.route(`${API_PREFIX}/projects`, json([]))
}

export interface ReviewerState {
  reviewer_cap: number
  reviewers: Array<{
    agent_instance_id: number
    container_id: string
    container_name: string
    session_id: string | null
    status: string
  }>
}

export async function mockReviewerLifecycle(
  page: Page,
  projectId: number,
  initial: ReviewerState = { reviewer_cap: 1, reviewers: [] },
) {
  const state = { ...initial, reviewers: [...initial.reviewers] }
  let nextId = 101

  await page.route(`${API_PREFIX}/projects/${projectId}/reviewers/status`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        project_id: projectId,
        reviewer_cap: state.reviewer_cap,
        running_reviewers: state.reviewers.filter((r) => r.status === 'running').length,
        reviewers: state.reviewers,
      }),
    }),
  )

  await page.route(`${API_PREFIX}/projects/${projectId}/reviewers/launch`, (route) => {
    const instanceId = nextId++
    const reviewer = {
      agent_instance_id: instanceId,
      container_id: `container-${instanceId}`,
      container_name: `reviewer-${instanceId}`,
      session_id: `session-${instanceId}`,
      status: 'running',
    }
    state.reviewers.push(reviewer)
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        agent_instance_id: instanceId,
        container_id: reviewer.container_id,
        container_name: reviewer.container_name,
      }),
    })
  })

  await page.route(new RegExp(`${API_PREFIX}/projects/${projectId}/reviewers/(\\d+)/trigger`), (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ message: 'Triggered' }) }),
  )

  await page.route(new RegExp(`${API_PREFIX}/projects/${projectId}/reviewers/(\\d+)/terminate`), (route) => {
    const match = route.request().url().match(/\/reviewers\/(\d+)\/terminate/)
    const id = match ? Number(match[1]) : 0
    state.reviewers = state.reviewers.filter((r) => r.agent_instance_id !== id)
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ message: 'Terminated' }),
    })
  })

  return state
}

export async function mockImplementorStatus(page: Page, projectId: number, body: unknown) {
  await page.route(`${API_PREFIX}/projects/${projectId}/implementors/status`, json(body))
}

export async function mockEmptyNotifications(page: Page, projectId: number) {
  await page.route(`${API_PREFIX}/projects/${projectId}/notifications?**`, json([]))
}

export async function mockEmptyAuditEvents(page: Page, instanceId: number) {
  await page.route(`${API_PREFIX}/agents/${instanceId}/audit`, json([]))
}

export interface ConsoleChunk {
  chunk_index: number
  content: string
  created_at: string
}

export async function mockConsoleStream(page: Page, instanceId: number, chunks: ConsoleChunk[]) {
  await page.route(`${API_PREFIX}/agents/${instanceId}/console/stream`, (route) => {
    const body = chunks.map((c) => `data: ${JSON.stringify(c)}\n\n`).join('') + '\n'
    return route.fulfill({ status: 200, contentType: 'text/event-stream', body })
  })
}
