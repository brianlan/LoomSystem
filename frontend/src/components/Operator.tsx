import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as api from '../api'
import type {
  AuditEvent,
  ConsoleChunk,
  ImplementorInstanceStatus,
  Notification,
  Project,
  ReviewerInstanceStatus,
} from '../types'
import { useAsync } from '../useAsync'
import { EmptyState, ErrorBanner, Loading, Section, Status } from './ui'

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

type AsyncButtonProps = {
  label: string
  onClick: () => Promise<void>
  disabled?: boolean
  variant?: 'primary' | 'danger'
}

function AsyncButton({ label, onClick, disabled, variant = 'primary' }: AsyncButtonProps) {
  const [busy, setBusy] = useState(false)
  return (
    <button
      className={variant === 'danger' ? 'danger' : undefined}
      disabled={disabled || busy}
      onClick={async () => {
        setBusy(true)
        try {
          await onClick()
        } finally {
          setBusy(false)
        }
      }}
    >
      {busy ? `${label}…` : label}
    </button>
  )
}

function ProjectSelector({
  projectId,
  onChange,
}: {
  projectId: number | null
  onChange: (id: number) => void
}) {
  const { state } = useAsync(api.listProjects, [])
  if (state.status === 'loading') return <Loading />
  if (state.status === 'error') return <ErrorBanner message={state.error} />
  const projects = state.data
  if (projects.length === 0) return <EmptyState>No projects yet.</EmptyState>
  return (
    <label className="project-selector">
      Project
      <select
        value={projectId ?? ''}
        onChange={(e) => onChange(Number(e.target.value))}
      >
        <option value="">— select —</option>
        {projects.map((p: Project) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
    </label>
  )
}

function ReviewerPanel({
  projectId,
  onSelect,
}: {
  projectId: number
  onSelect: (id: number) => void
}) {
  const { state, refetch } = useAsync(() => api.getReviewerStatus(projectId), [projectId])
  const [error, setError] = useState('')

  const act = useCallback(
    async (fn: () => Promise<unknown>) => {
      setError('')
      try {
        await fn()
        await refetch()
      } catch (e) {
        setError(message(e))
      }
    },
    [refetch],
  )

  return (
    <div className="agent-panel">
      <h3>Reviewer</h3>
      {error && <ErrorBanner message={error} />}
      <div className="controls">
        <AsyncButton label="Launch" onClick={() => act(() => api.launchReviewer(projectId))} />
        <AsyncButton
          label="Refresh"
          onClick={() => act(async () => refetch())}
          variant="primary"
        />
      </div>
      {state.status === 'loading' && <Loading />}
      {state.status === 'error' && <ErrorBanner message={state.error} />}
      {state.status === 'success' && (
        <>
          <p>
            <Status kind={state.data.running_reviewers > 0 ? 'running' : 'idle'}>
              {state.data.running_reviewers}/{state.data.reviewer_cap}
            </Status>{' '}
            running
          </p>
          {state.data.reviewers.length === 0 ? (
            <EmptyState>No reviewers.</EmptyState>
          ) : (
            <ul className="agent-list">
              {state.data.reviewers.map((r: ReviewerInstanceStatus) => (
                <li key={r.agent_instance_id}>
                  <button className="link" onClick={() => onSelect(r.agent_instance_id)}>
                    #{r.agent_instance_id}
                  </button>{' '}
                  <Status kind={r.status}>{r.status}</Status>
                  {r.container_name && <span className="muted"> {r.container_name}</span>}
                  <div className="row-actions">
                    <AsyncButton
                      label="Trigger"
                      onClick={() =>
                        act(() => api.triggerReviewer(projectId, r.agent_instance_id))
                      }
                      disabled={r.status !== 'running'}
                    />
                    <AsyncButton
                      label="Terminate"
                      onClick={() =>
                        act(() => api.terminateReviewer(projectId, r.agent_instance_id))
                      }
                      disabled={r.status !== 'running'}
                      variant="danger"
                    />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}

function ImplementorPanel({
  projectId,
  project,
  onSelect,
}: {
  projectId: number
  project: Project | null
  onSelect: (id: number) => void
}) {
  const { state, refetch } = useAsync(() => api.getImplementorStatus(projectId), [projectId])
  const [error, setError] = useState('')
  const [issueNumber, setIssueNumber] = useState('')

  const act = useCallback(
    async (fn: () => Promise<unknown>) => {
      setError('')
      try {
        await fn()
        await refetch()
      } catch (e) {
        setError(message(e))
      }
    },
    [refetch],
  )

  const parallel = project?.implementor_config?.parallelism ?? 1

  return (
    <div className="agent-panel">
      <h3>Implementor</h3>
      {error && <ErrorBanner message={error} />}
      <div className="controls">
        <AsyncButton label="Start loop" onClick={() => act(() => api.startImplementorLoop(projectId))} />
        <AsyncButton label="Soft stop" onClick={() => act(() => api.softStopImplementorLoop(projectId))} />
        <AsyncButton
          label="Hard stop"
          onClick={() => act(() => api.hardStopImplementorLoop(projectId))}
          variant="danger"
        />
        <AsyncButton
          label="Refresh"
          onClick={() => act(async () => refetch())}
          variant="primary"
        />
      </div>
      <div className="launch-row">
        <label>
          Issue #
          <input
            type="number"
            min={1}
            value={issueNumber}
            onChange={(e) => setIssueNumber(e.target.value)}
            placeholder="1"
          />
        </label>
        <AsyncButton
          label="Launch"
          disabled={!issueNumber}
          onClick={() =>
            act(async () => {
              await api.launchImplementor(projectId, { issue_number: Number(issueNumber) })
              setIssueNumber('')
            })
          }
        />
      </div>
      {state.status === 'loading' && <Loading />}
      {state.status === 'error' && <ErrorBanner message={state.error} />}
      {state.status === 'success' && (
        <>
          <p>
            Loop: <Status kind={state.data.state}>{state.data.state}</Status> ·{' '}
            {state.data.running_implementors}/{parallel} running
          </p>
          {state.data.implementors.length === 0 ? (
            <EmptyState>No implementors.</EmptyState>
          ) : (
            <ul className="agent-list">
              {state.data.implementors.map((i: ImplementorInstanceStatus) => (
                <li key={i.agent_instance_id}>
                  <button className="link" onClick={() => onSelect(i.agent_instance_id)}>
                    #{i.agent_instance_id}
                  </button>{' '}
                  {i.issue_number !== null && <>issue #{i.issue_number} </>}
                  <Status kind={i.status}>{i.status}</Status>
                  <div className="row-actions">
                    <AsyncButton
                      label="Terminate"
                      onClick={() =>
                        act(() => api.terminateImplementor(projectId, i.agent_instance_id))
                      }
                      disabled={i.status !== 'running'}
                      variant="danger"
                    />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}

export function ConsoleView({ instanceId }: { instanceId: number }) {
  const [chunks, setChunks] = useState<ConsoleChunk[]>([])
  const [connected, setConnected] = useState(false)
  const [streamError, setStreamError] = useState('')
  const listRef = useRef<HTMLUListElement>(null)

  useEffect(() => {
    setChunks([])
    setStreamError('')
    setConnected(false)
    const url = `/api/v1/agents/${instanceId}/console/stream`
    const es = new EventSource(url)
    es.onopen = () => setConnected(true)
    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as ConsoleChunk
        setChunks((prev) => [...prev, data])
      } catch {
        // ignore malformed event data
      }
    }
    es.onerror = () => {
      setConnected(false)
      setStreamError('Console stream disconnected')
      es.close()
    }
    return () => {
      es.close()
    }
  }, [instanceId])

  useEffect(() => {
    listRef.current?.lastElementChild?.scrollIntoView?.({ behavior: 'smooth' })
  }, [chunks])

  return (
    <div className="console-view">
      <h4>Console #{instanceId}</h4>
      {!connected && !streamError && <p className="muted">Connecting…</p>}
      {streamError && <ErrorBanner message={streamError} />}
      {connected && chunks.length === 0 ? (
        <EmptyState>No console output yet.</EmptyState>
      ) : (
        <ul className="console-lines" ref={listRef}>
          {chunks.map((c, idx) => (
            <li key={idx}>
              <span className="muted">[{c.chunk_index}]</span> {c.content}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function AuditView({ instanceId }: { instanceId: number }) {
  const { state } = useAsync(() => api.listAgentAuditEvents(instanceId), [instanceId])
  return (
    <div className="audit-view">
      <h4>Audit #{instanceId}</h4>
      {state.status === 'loading' && <Loading />}
      {state.status === 'error' && <ErrorBanner message={state.error} />}
      {state.status === 'success' &&
        (state.data.length === 0 ? (
          <EmptyState>No audit events.</EmptyState>
        ) : (
          <ul className="audit-list">
            {state.data.map((e: AuditEvent) => (
              <li key={e.id}>
                <span className="muted">{e.created_at}</span>{' '}
                <Status kind={e.event_type}>{e.event_type}</Status>
                {Object.keys(e.payload).length > 0 && (
                  <pre className="payload">{JSON.stringify(e.payload, null, 2)}</pre>
                )}
              </li>
            ))}
          </ul>
        ))}
    </div>
  )
}

function NotificationView({ projectId }: { projectId: number }) {
  const { state, refetch } = useAsync(() => api.listProjectNotifications(projectId), [projectId])
  const [error, setError] = useState('')

  async function markRead(id: number) {
    setError('')
    try {
      await api.markNotificationRead(id)
      await refetch()
    } catch (e) {
      setError(message(e))
    }
  }

  return (
    <div className="notification-view">
      <h4>Notifications</h4>
      {error && <ErrorBanner message={error} />}
      {state.status === 'loading' && <Loading />}
      {state.status === 'error' && <ErrorBanner message={state.error} />}
      {state.status === 'success' &&
        (state.data.length === 0 ? (
          <EmptyState>No notifications.</EmptyState>
        ) : (
          <ul className="notification-list">
            {state.data.map((n: Notification) => (
              <li key={n.id} className={n.is_read ? 'read' : undefined}>
                <span className="muted">{n.created_at}</span> {n.message}
                {!n.is_read && (
                  <button className="link" onClick={() => markRead(n.id)}>
                    Mark read
                  </button>
                )}
              </li>
            ))}
          </ul>
        ))}
    </div>
  )
}

export function Operator() {
  const [projectId, setProjectId] = useState<number | null>(null)
  const [instanceId, setInstanceId] = useState<number | null>(null)
  const { state: projectsState } = useAsync(api.listProjects, [])
  const selectedProject = useMemo(() => {
    if (projectsState.status !== 'success' || projectId == null) return null
    return projectsState.data.find((p: Project) => p.id === projectId) ?? null
  }, [projectsState, projectId])

  return (
    <Section title="Operator">
      <ProjectSelector projectId={projectId} onChange={setProjectId} />
      {projectId && (
        <div className="operator-grid">
          <div className="operator-panels">
            <ReviewerPanel projectId={projectId} onSelect={setInstanceId} />
            <ImplementorPanel
              projectId={projectId}
              project={selectedProject}
              onSelect={setInstanceId}
            />
          </div>
          {instanceId && (
            <div className="operator-detail">
              <ConsoleView instanceId={instanceId} />
              <AuditView instanceId={instanceId} />
            </div>
          )}
          <NotificationView projectId={projectId} />
        </div>
      )}
    </Section>
  )
}
