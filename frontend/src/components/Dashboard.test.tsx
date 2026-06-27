import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { Dashboard } from './Dashboard'
import { errorResp, installFetch, json, restoreFetch, type MockRoute } from '../test/mockFetch'

const STATUS_OK: MockRoute = {
  match: (url) => url.endsWith('/api/v1/status'),
  respond: () =>
    Promise.resolve(
      json({
        running_reviewers: 2,
        running_implementors: 1,
        backlog_size: 5,
        recent_failures: [
          { kind: 'triage', id: 9, project_id: 1, created_at: '2026-01-01', error: 'boom' },
        ],
      }),
    ),
}

describe('Dashboard', () => {
  let original: typeof fetch
  beforeEach(() => {
    original = installFetch([STATUS_OK])
  })
  afterEach(() => restoreFetch(original))

  it('renders running counts and backlog size on success', async () => {
    render(<Dashboard />)
    await waitFor(() => expect(screen.getByText('2')).toBeInTheDocument())
    expect(screen.getByText('Running reviewers')).toBeInTheDocument()
    expect(screen.getByText('Running implementors')).toBeInTheDocument()
    expect(screen.getByText('Backlog size')).toBeInTheDocument()
    expect(screen.getByText(/boom/)).toBeInTheDocument()
  })

  it('renders an error banner when the status API fails', async () => {
    const err: MockRoute = {
      match: (url) => url.endsWith('/api/v1/status'),
      respond: () => Promise.resolve(errorResp(500, 'server down')),
    }
    original = installFetch([err])
    render(<Dashboard />)
    await waitFor(() => expect(screen.getByText(/server down/i)).toBeInTheDocument())
  })

  it('renders the empty-failures state', async () => {
    const empty: MockRoute = {
      match: (url) => url.endsWith('/api/v1/status'),
      respond: () =>
        Promise.resolve(
          json({
            running_reviewers: 0,
            running_implementors: 0,
            backlog_size: 0,
            recent_failures: [],
          }),
        ),
    }
    original = installFetch([empty])
    render(<Dashboard />)
    await waitFor(() => expect(screen.getByText(/no recent failures/i)).toBeInTheDocument())
  })
})
