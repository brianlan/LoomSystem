import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { Projects } from './Projects'
import { errorResp, installFetch, json, restoreFetch, type MockRoute } from '../test/mockFetch'

const AGENTS: MockRoute = {
  match: (url) => url.endsWith('/settings/agent-definitions'),
  respond: () =>
    Promise.resolve(
      json([{ id: 1, name: 'reviewer', prompt_markdown: '', github_identity: 'bot', permissions: {} }]),
    ),
}
const MODELS: MockRoute = {
  match: (url) => url.endsWith('/settings/model-entries'),
  respond: () =>
    Promise.resolve(
      json([{ id: 1, provider_id: 'anthropic', model_id: 'claude', display_name: null, custom_config: null }]),
    ),
}
const IMAGES: MockRoute = {
  match: (url) => url.endsWith('/settings/docker-images'),
  respond: () => Promise.resolve(json([{ id: 1, image_name: 'img:latest' }])),
}

function projectsRoute(body: unknown): MockRoute {
  return { match: (url) => url.endsWith('/api/v1/projects'), respond: () => Promise.resolve(json(body)) }
}

const NO_PROJECTS: MockRoute = projectsRoute([])

const ONE_PROJECT: MockRoute = projectsRoute([
  {
    id: 7,
    name: 'demo',
    repo_url: 'git@github.com:brianlan/demo.git',
    reviewer_config: {},
    implementor_config: {},
    created_at: '2026-01-01',
    updated_at: '2026-01-01',
  },
])

function issuesRoute(projectId: number, body: unknown): MockRoute {
  return {
    match: (url) => url === `/api/v1/projects/${projectId}/github/issues`,
    respond: () => Promise.resolve(json(body)),
  }
}
function prsRoute(projectId: number, body: unknown): MockRoute {
  return {
    match: (url) => url === `/api/v1/projects/${projectId}/github/pulls`,
    respond: () => Promise.resolve(json(body)),
  }
}
function pollingRoute(projectId: number, ok: boolean, error: string | null): MockRoute {
  return {
    match: (url) => url === `/api/v1/projects/${projectId}/github/polling-status`,
    respond: () =>
      Promise.resolve(
        json({ project_id: projectId, last_polled_at: '2026-01-01', last_ok: ok, last_error: error }),
      ),
  }
}

const BASE = [AGENTS, MODELS, IMAGES]

describe('Projects', () => {
  let original: typeof fetch
  beforeEach(() => {
    original = installFetch(BASE)
  })
  afterEach(() => restoreFetch(original))

  it('renders the empty state when no projects exist', async () => {
    original = installFetch([...BASE, NO_PROJECTS])
    render(<Projects />)
    await waitFor(() => expect(screen.getByText(/no projects yet/i)).toBeInTheDocument())
  })

  it('lists an existing project', async () => {
    original = installFetch([...BASE, ONE_PROJECT])
    render(<Projects />)
    await waitFor(() => expect(screen.getByText('demo')).toBeInTheDocument())
    expect(screen.getByText(/git@github.com:brianlan\/demo.git/)).toBeInTheDocument()
  })

  it('validates required name and repo URL on create', async () => {
    original = installFetch([...BASE, NO_PROJECTS])
    render(<Projects />)
    await waitFor(() => expect(screen.getByText(/no projects yet/i)).toBeInTheDocument())
    fireEvent.click(screen.getByText('New project'))
    fireEvent.click(screen.getByRole('button', { name: 'Create project' }))
    await waitFor(() => expect(screen.getByText(/name and repo url are required/i)).toBeInTheDocument())
  })

  it('expands a project and shows open issues and PRs', async () => {
    original = installFetch([
      ...BASE,
      ONE_PROJECT,
      issuesRoute(7, [{ issue_number: 3, title: 'Bug', state: 'open', loom_status: 'unassigned', updated_at: '2026-01-01' }]),
      prsRoute(7, [{ pr_number: 5, title: 'Fix', state: 'open', merged: false, updated_at: '2026-01-01' }]),
      pollingRoute(7, true, null),
    ])
    render(<Projects />)
    await waitFor(() => expect(screen.getByText('demo')).toBeInTheDocument())
    fireEvent.click(screen.getByText('▶'))
    await waitFor(() => expect(screen.getByText(/#3: Bug/)).toBeInTheDocument())
    expect(screen.getByText(/#5: Fix/)).toBeInTheDocument()
  })

  it('shows empty issue/PR lists inside an expanded project', async () => {
    original = installFetch([
      ...BASE,
      ONE_PROJECT,
      issuesRoute(7, []),
      prsRoute(7, []),
      pollingRoute(7, true, null),
    ])
    render(<Projects />)
    await waitFor(() => expect(screen.getByText('demo')).toBeInTheDocument())
    fireEvent.click(screen.getByText('▶'))
    await waitFor(() => expect(screen.getByText(/no open issues/i)).toBeInTheDocument())
    expect(screen.getByText(/no open pull requests/i)).toBeInTheDocument()
  })

  it('surfaces a GitHub polling error inside an expanded project', async () => {
    original = installFetch([
      ...BASE,
      ONE_PROJECT,
      issuesRoute(7, []),
      prsRoute(7, []),
      pollingRoute(7, false, 'bad token'),
    ])
    render(<Projects />)
    await waitFor(() => expect(screen.getByText('demo')).toBeInTheDocument())
    fireEvent.click(screen.getByText('▶'))
    await waitFor(() => expect(screen.getByText(/bad token/i)).toBeInTheDocument())
  })

  it('surfaces an in-use deletion error when deleting a project', async () => {
    const deleteErr: MockRoute = {
      match: (url, init) =>
        url === '/api/v1/projects/7' && init?.method === 'DELETE',
      respond: () => Promise.resolve(errorResp(500, 'cascade failed')),
    }
    original = installFetch([...BASE, ONE_PROJECT, deleteErr])
    render(<Projects />)
    await waitFor(() => expect(screen.getByText('demo')).toBeInTheDocument())
    fireEvent.click(screen.getAllByText('Delete')[0])
    await waitFor(() => expect(screen.getByText(/cascade failed/i)).toBeInTheDocument())
  })
})
