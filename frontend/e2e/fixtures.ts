import type { Page, Route } from '@playwright/test'

export type AgentDefinition = {
  id: number
  name: string
  prompt_markdown: string
  github_identity: string
  permissions: Record<string, unknown>
}

export type ModelEntry = {
  id: number
  provider_id: string
  model_id: string
  display_name: string | null
  custom_config?: Record<string, unknown> | null
}

export type DockerImage = {
  id: number
  image_name: string
}

export type Project = {
  id: number
  name: string
  repo_url: string
  reviewer_config: Record<string, unknown>
  implementor_config: Record<string, unknown>
  created_at: string
  updated_at: string
}

export type GitHubIssue = {
  issue_number: number
  title: string
  state: string
  loom_status: string
  updated_at: string
}

export type GitHubPullRequest = {
  pr_number: number
  title: string
  state: string
  merged: boolean
  updated_at: string
}

export type PollingStatus = {
  project_id: number
  last_polled_at: string | null
  last_ok: boolean
  last_error: string | null
}

const BASE = '/api/v1'

function route(page: Page, method: string, path: string, handler: (route: Route) => void) {
  return page.route(`${BASE}${path}`, (route, request) => {
    if (request.method() !== method) {
      route.fallback()
      return
    }
    handler(route)
  })
}

export async function mockEmptySettings(page: Page) {
  await route(page, 'GET', '/settings/agent-definitions', (route) =>
    route.fulfill({ status: 200, body: JSON.stringify([]) }),
  )
  await route(page, 'GET', '/settings/model-entries', (route) =>
    route.fulfill({ status: 200, body: JSON.stringify([]) }),
  )
  await route(page, 'GET', '/settings/docker-images', (route) =>
    route.fulfill({ status: 200, body: JSON.stringify([]) }),
  )
  await route(page, 'GET', '/settings/ssh-key', (route) =>
    route.fulfill({ status: 404, body: JSON.stringify({ detail: 'SSH key not set' }) }),
  )
  await route(page, 'GET', '/settings/github-token', (route) =>
    route.fulfill({ status: 404, body: JSON.stringify({ detail: 'GitHub token not set' }) }),
  )
  await route(page, 'GET', '/settings/triage-config', (route) =>
    route.fulfill({ status: 404, body: JSON.stringify({ detail: 'Triage config not set' }) }),
  )
  await route(page, 'GET', '/settings/proxy', (route) =>
    route.fulfill({ status: 200, body: JSON.stringify({ http_proxy: null, https_proxy: null }) }),
  )
}

export async function mockSettingsRegistries(
  page: Page,
  options: { agents?: AgentDefinition[]; models?: ModelEntry[]; images?: DockerImage[] } = {},
) {
  const agents = options.agents ?? []
  const models = options.models ?? []
  const images = options.images ?? []
  await route(page, 'GET', '/settings/agent-definitions', (route) =>
    route.fulfill({ status: 200, body: JSON.stringify(agents) }),
  )
  await route(page, 'GET', '/settings/model-entries', (route) =>
    route.fulfill({ status: 200, body: JSON.stringify(models) }),
  )
  await route(page, 'GET', '/settings/docker-images', (route) =>
    route.fulfill({ status: 200, body: JSON.stringify(images) }),
  )
}

export async function mockSettingsMutations(page: Page) {
  const agents: AgentDefinition[] = []
  const models: ModelEntry[] = []
  const images: DockerImage[] = []

  await route(page, 'GET', '/settings/agent-definitions', (route) =>
    route.fulfill({ status: 200, body: JSON.stringify(agents) }),
  )
  await route(page, 'GET', '/settings/model-entries', (route) =>
    route.fulfill({ status: 200, body: JSON.stringify(models) }),
  )
  await route(page, 'GET', '/settings/docker-images', (route) =>
    route.fulfill({ status: 200, body: JSON.stringify(images) }),
  )

  await route(page, 'POST', '/settings/agent-definitions', async (route) => {
    const body = await route.request().postDataJSON()
    const created: AgentDefinition = {
      id: agents.length + 1,
      name: body.name,
      prompt_markdown: body.prompt_markdown,
      github_identity: body.github_identity,
      permissions: body.permissions ?? {},
    }
    agents.push(created)
    route.fulfill({ status: 201, body: JSON.stringify(created) })
  })

  await route(page, 'POST', '/settings/model-entries', async (route) => {
    const body = await route.request().postDataJSON()
    const created: ModelEntry = {
      id: models.length + 1,
      provider_id: body.provider_id,
      model_id: body.model_id,
      display_name: body.display_name ?? null,
      custom_config: null,
    }
    models.push(created)
    route.fulfill({ status: 201, body: JSON.stringify(created) })
  })

  await route(page, 'POST', '/settings/docker-images', async (route) => {
    const body = await route.request().postDataJSON()
    const created: DockerImage = { id: images.length + 1, image_name: body.image_name }
    images.push(created)
    route.fulfill({ status: 201, body: JSON.stringify(created) })
  })

  await route(page, 'PUT', '/settings/ssh-key', (route) =>
    route.fulfill({ status: 200, body: JSON.stringify({ message: 'SSH key saved.' }) }),
  )
  await route(page, 'PUT', '/settings/github-token', (route) =>
    route.fulfill({ status: 200, body: JSON.stringify({ message: 'GitHub token saved.' }) }),
  )
  await route(page, 'PUT', '/settings/triage-config', (route) =>
    route.fulfill({ status: 200, body: JSON.stringify({ message: 'Triage config saved.' }) }),
  )
}

export async function mockEmptyProjects(page: Page) {
  await route(page, 'GET', '/projects', (route) =>
    route.fulfill({ status: 200, body: JSON.stringify([]) }),
  )
}

export async function mockProjects(page: Page, projects: Project[]) {
  await route(page, 'GET', '/projects', (route) =>
    route.fulfill({ status: 200, body: JSON.stringify(projects) }),
  )
}

export async function mockProjectMutations(page: Page, options: { projects?: Project[] } = {}) {
  const projects = options.projects ?? []
  let nextId = projects.length > 0 ? Math.max(...projects.map((p) => p.id)) + 1 : 1

  await route(page, 'POST', '/projects', async (route) => {
    const body = await route.request().postDataJSON()
    const created: Project = {
      id: nextId++,
      name: body.name,
      repo_url: body.repo_url,
      reviewer_config: body.reviewer_config ?? {},
      implementor_config: body.implementor_config ?? {},
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }
    projects.push(created)
    route.fulfill({ status: 201, body: JSON.stringify(created) })
  })

  await route(page, 'PATCH', '/projects/**', async (route) => {
    const url = new URL(route.request().url())
    const id = Number(url.pathname.split('/').pop())
    const body = await route.request().postDataJSON()
    const idx = projects.findIndex((p) => p.id === id)
    if (idx === -1) {
      route.fulfill({ status: 404, body: JSON.stringify({ detail: 'Project not found' }) })
      return
    }
    projects[idx] = { ...projects[idx], ...body, updated_at: new Date().toISOString() }
    route.fulfill({ status: 200, body: JSON.stringify(projects[idx]) })
  })

  await route(page, 'DELETE', '/projects/**', async (route) => {
    const url = new URL(route.request().url())
    const id = Number(url.pathname.split('/').pop())
    const idx = projects.findIndex((p) => p.id === id)
    if (idx === -1) {
      route.fulfill({ status: 404, body: JSON.stringify({ detail: 'Project not found' }) })
      return
    }
    projects.splice(idx, 1)
    route.fulfill({ status: 200, body: JSON.stringify({ message: 'Project deleted.' }) })
  })
}

export async function mockGitHubLists(
  page: Page,
  projectId: number,
  options: { issues?: GitHubIssue[]; pulls?: GitHubPullRequest[]; polling?: PollingStatus } = {},
) {
  const issues = options.issues ?? []
  const pulls = options.pulls ?? []
  const polling = options.polling ?? {
    project_id: projectId,
    last_polled_at: new Date().toISOString(),
    last_ok: true,
    last_error: null,
  }
  await route(page, 'GET', `/projects/${projectId}/github/issues`, (route) =>
    route.fulfill({ status: 200, body: JSON.stringify(issues) }),
  )
  await route(page, 'GET', `/projects/${projectId}/github/pulls`, (route) =>
    route.fulfill({ status: 200, body: JSON.stringify(pulls) }),
  )
  await route(page, 'GET', `/projects/${projectId}/github/polling-status`, (route) =>
    route.fulfill({ status: 200, body: JSON.stringify(polling) }),
  )
}

export async function mockApiError(page: Page, method: string, path: string, detail: string, status = 500) {
  await route(page, method, path, (route) =>
    route.fulfill({ status, body: JSON.stringify({ detail }) }),
  )
}

export async function goToSettings(page: Page) {
  await page.goto('/')
  await page.getByRole('button', { name: 'Settings' }).click()
}

export async function goToProjects(page: Page) {
  await page.goto('/')
  await page.getByRole('button', { name: 'Projects' }).click()
}
