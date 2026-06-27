import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Operator } from './Operator'
import { errorResp, installFetch, json, restoreFetch, type MockRoute } from '../test/mockFetch'

const PROJECTS: MockRoute = {
  match: (url) => url.endsWith('/api/v1/projects'),
  respond: () =>
    Promise.resolve(
      json([
        {
          id: 7,
          name: 'demo',
          repo_url: 'git@github.com:brianlan/demo.git',
          reviewer_config: { reviewer_cap: 1 },
          implementor_config: { parallelism: 2 },
          created_at: '2026-01-01',
          updated_at: '2026-01-01',
        },
      ]),
    ),
}

const statusState = {
  reviewerRunning: 0,
  implementorState: 'idle',
  implementorRunning: 0,
  hasReviewer: false,
  hasImplementor: false,
}

function resetStatus() {
  statusState.reviewerRunning = 0
  statusState.implementorState = 'idle'
  statusState.implementorRunning = 0
  statusState.hasReviewer = false
  statusState.hasImplementor = false
}

const REVIEWER_STATUS: MockRoute = {
  match: (url) => url === '/api/v1/projects/7/reviewers/status',
  respond: () =>
    Promise.resolve(
      json({
        project_id: 7,
        reviewer_cap: 1,
        running_reviewers: statusState.reviewerRunning,
        reviewers: statusState.hasReviewer
          ? [
              {
                agent_instance_id: 11,
                container_id: 'c11',
                container_name: 'reviewer-11',
                session_id: null,
                status: 'running',
              },
            ]
          : [],
      }),
    ),
}

const IMPLEMENTOR_STATUS: MockRoute = {
  match: (url) => url === '/api/v1/projects/7/implementors/status',
  respond: () =>
    Promise.resolve(
      json({
        project_id: 7,
        state: statusState.implementorState,
        running_implementors: statusState.implementorRunning,
        implementors: statusState.hasImplementor
          ? [
              {
                agent_instance_id: 21,
                issue_number: 3,
                container_id: 'c21',
                container_name: 'implementor-21',
                status: 'running',
              },
            ]
          : [],
      }),
    ),
}

const AUDIT: MockRoute = {
  match: (url) => url === '/api/v1/agents/11/audit',
  respond: () =>
    Promise.resolve(
      json([
        {
          id: 1,
          project_id: 7,
          agent_instance_id: 11,
          event_type: 'reviewer_launch',
          payload: { container_id: 'c11' },
          created_at: '2026-01-01',
        },
      ]),
    ),
}

const NOTIFICATIONS: MockRoute = {
  match: (url) => url === '/api/v1/projects/7/notifications?unread_only=false',
  respond: () =>
    Promise.resolve(
      json([
        {
          id: 1,
          project_id: 7,
          agent_instance_id: null,
          message: 'Backlog drained',
          is_read: false,
          created_at: '2026-01-01',
        },
      ]),
    ),
}

class FakeEventSource {
  static instances: FakeEventSource[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  url: string
  closed = false

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
    setTimeout(() => this.onopen?.(), 0)
  }

  emit(data: object) {
    this.onmessage?.({ data: JSON.stringify(data) })
  }

  close() {
    this.closed = true
  }
}

describe('Operator', () => {
  let original: typeof fetch
  beforeEach(() => {
    resetStatus()
    original = installFetch([PROJECTS])
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    globalThis.EventSource = FakeEventSource as any
    FakeEventSource.instances = []
    Element.prototype.scrollIntoView = vi.fn()
  })
  afterEach(() => {
    restoreFetch(original)
    FakeEventSource.instances = []
  })

  it('renders the project selector and selects a project', async () => {
    original = installFetch([
      PROJECTS,
      REVIEWER_STATUS,
      IMPLEMENTOR_STATUS,
      NOTIFICATIONS,
    ])
    render(<Operator />)
    await waitFor(() => expect(screen.getByText('demo')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Project'), { target: { value: '7' } })
    await waitFor(() => expect(screen.getByText('Reviewer')).toBeInTheDocument())
    expect(screen.getByText('Implementor')).toBeInTheDocument()
  })

  it('launches a reviewer', async () => {
    const launch: MockRoute = {
      match: (url, init) =>
        url === '/api/v1/projects/7/reviewers/launch' && init?.method === 'POST',
      respond: () => {
        statusState.reviewerRunning = 1
        statusState.hasReviewer = true
        return Promise.resolve(
          json({ agent_instance_id: 11, container_id: 'c11', container_name: 'r11' }),
        )
      },
    }
    original = installFetch([
      PROJECTS,
      REVIEWER_STATUS,
      IMPLEMENTOR_STATUS,
      NOTIFICATIONS,
      launch,
    ])
    render(<Operator />)
    await waitFor(() => expect(screen.getByText('demo')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Project'), { target: { value: '7' } })
    await waitFor(() => expect(screen.getByText('No reviewers.')).toBeInTheDocument())
    fireEvent.click(screen.getAllByText('Launch')[0])
    await waitFor(() => expect(screen.getByText('#11')).toBeInTheDocument())
  })

  it('starts and soft-stops the implementor loop', async () => {
    const start: MockRoute = {
      match: (url, init) =>
        url === '/api/v1/projects/7/implementors/loop/start' && init?.method === 'POST',
      respond: () => {
        statusState.implementorState = 'running'
        return Promise.resolve(json({ message: 'started' }))
      },
    }
    const soft: MockRoute = {
      match: (url, init) =>
        url === '/api/v1/projects/7/implementors/loop/soft-stop' && init?.method === 'POST',
      respond: () => {
        statusState.implementorState = 'draining'
        return Promise.resolve(json({ message: 'draining' }))
      },
    }
    original = installFetch([
      PROJECTS,
      REVIEWER_STATUS,
      IMPLEMENTOR_STATUS,
      NOTIFICATIONS,
      start,
      soft,
    ])
    render(<Operator />)
    await waitFor(() => expect(screen.getByText('demo')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Project'), { target: { value: '7' } })
    await waitFor(() => expect(screen.getByText('idle')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Start loop'))
    await waitFor(() => expect(screen.getByText('running')).toBeInTheDocument())
    fireEvent.click(screen.getByText('Soft stop'))
    await waitFor(() => expect(screen.getByText('draining')).toBeInTheDocument())
  })

  it('shows console history and live stream for a selected agent', async () => {
    statusState.hasReviewer = true
    statusState.reviewerRunning = 1
    // Console history is replayed over SSE; no separate GET history fetch.
    original = installFetch([
      PROJECTS,
      REVIEWER_STATUS,
      IMPLEMENTOR_STATUS,
      NOTIFICATIONS,
      AUDIT,
    ])
    render(<Operator />)
    await waitFor(() => expect(screen.getByText('demo')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Project'), { target: { value: '7' } })
    await waitFor(() => expect(screen.getByText('#11')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#11'))

    const es = FakeEventSource.instances.find((i) => i.url.endsWith('/agents/11/console/stream'))
    expect(es).toBeDefined()
    // SSE replays persisted history first.
    es?.emit({ chunk_index: 0, content: 'hello', created_at: '2026-01-01' })
    await waitFor(() => expect(screen.getByText(/hello/)).toBeInTheDocument())
    // Then live chunks arrive.
    es?.emit({ chunk_index: 1, content: 'world', created_at: '2026-01-01' })
    await waitFor(() => expect(screen.getByText(/world/)).toBeInTheDocument())
    // History must not be duplicated by a separate fetch.
    expect(screen.getAllByText(/hello/).length).toBe(1)
  })

  it('shows audit events and notifications', async () => {
    statusState.hasReviewer = true
    statusState.reviewerRunning = 1
    original = installFetch([
      PROJECTS,
      REVIEWER_STATUS,
      IMPLEMENTOR_STATUS,
      NOTIFICATIONS,
      AUDIT,
    ])
    render(<Operator />)
    await waitFor(() => expect(screen.getByText('demo')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Project'), { target: { value: '7' } })
    await waitFor(() => expect(screen.getByText('#11')).toBeInTheDocument())
    fireEvent.click(screen.getByText('#11'))
    await waitFor(() => expect(screen.getByText('reviewer_launch')).toBeInTheDocument())
    expect(screen.getByText('Backlog drained')).toBeInTheDocument()
  })

  it('surfaces API errors in controls', async () => {
    const err: MockRoute = {
      match: (url, init) =>
        url === '/api/v1/projects/7/reviewers/launch' && init?.method === 'POST',
      respond: () => Promise.resolve(errorResp(409, 'cap reached')),
    }
    original = installFetch([
      PROJECTS,
      REVIEWER_STATUS,
      IMPLEMENTOR_STATUS,
      NOTIFICATIONS,
      err,
    ])
    render(<Operator />)
    await waitFor(() => expect(screen.getByText('demo')).toBeInTheDocument())
    fireEvent.change(screen.getByLabelText('Project'), { target: { value: '7' } })
    await waitFor(() => expect(screen.getByText('No reviewers.')).toBeInTheDocument())
    fireEvent.click(screen.getAllByText('Launch')[0])
    await waitFor(() => expect(screen.getByText(/cap reached/i)).toBeInTheDocument())
  })
})
