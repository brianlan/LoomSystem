import { useState } from 'react'
import * as api from '../api'
import type { AgentDefinition, DockerImage, GitHubIssue, GitHubPullRequest, ModelEntry, Project } from '../types'
import { useAsync } from '../useAsync'
import { ErrorBanner, Loading, Section, Status } from './ui'

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

// Loads registries once for config-form dropdowns.
function useRegistries() {
  const agents = useAsync(api.listAgentDefinitions, [])
  const models = useAsync(api.listModelEntries, [])
  const images = useAsync(api.listDockerImages, [])
  return { agents, models, images }
}

function ConfigForm({
  label,
  config,
  agents,
  models,
  images,
  onChange,
}: {
  label: string
  config: { agent_definition_id?: number; model_entry_id?: number; docker_image_id?: number; trigger_interval_minutes?: number; reviewer_cap?: number; parallelism?: number }
  agents: AgentDefinition[]
  models: ModelEntry[]
  images: DockerImage[]
  onChange: (next: typeof config) => void
}) {
  return (
    <fieldset className="config-form">
      <legend>{label}</legend>
      <label>
        Agent definition
        <select
          value={config.agent_definition_id ?? ''}
          onChange={(e) => onChange({ ...config, agent_definition_id: numOrUndef(e.target.value) })}
        >
          <option value="">—</option>
          {agents.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        Model entry
        <select
          value={config.model_entry_id ?? ''}
          onChange={(e) => onChange({ ...config, model_entry_id: numOrUndef(e.target.value) })}
        >
          <option value="">—</option>
          {models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.provider_id}/{m.model_id}
            </option>
          ))}
        </select>
      </label>
      <label>
        Docker image
        <select
          value={config.docker_image_id ?? ''}
          onChange={(e) => onChange({ ...config, docker_image_id: numOrUndef(e.target.value) })}
        >
          <option value="">—</option>
          {images.map((d) => (
            <option key={d.id} value={d.id}>
              {d.image_name}
            </option>
          ))}
        </select>
      </label>
      <label>
        Trigger interval (min)
        <input
          type="number"
          min={1}
          value={config.trigger_interval_minutes ?? 15}
          onChange={(e) => onChange({ ...config, trigger_interval_minutes: Number(e.target.value) })}
        />
      </label>
      {'reviewer_cap' in config && (
        <label>
          Reviewer cap
          <input
            type="number"
            min={1}
            value={config.reviewer_cap ?? 1}
            onChange={(e) => onChange({ ...config, reviewer_cap: Number(e.target.value) })}
          />
        </label>
      )}
      {'parallelism' in config && (
        <label>
          Parallelism
          <input
            type="number"
            min={1}
            value={config.parallelism ?? 1}
            onChange={(e) => onChange({ ...config, parallelism: Number(e.target.value) })}
          />
        </label>
      )}
    </fieldset>
  )
}

function numOrUndef(v: string): number | undefined {
  return v === '' ? undefined : Number(v)
}

function ProjectForm({
  initial,
  agents,
  models,
  images,
  onSubmit,
  onCancel,
  submitLabel,
}: {
  initial: Project | null
  agents: AgentDefinition[]
  models: ModelEntry[]
  images: DockerImage[]
  onSubmit: (name: string, repoUrl: string, reviewer: Record<string, unknown>, implementor: Record<string, unknown>) => Promise<void>
  onCancel: () => void
  submitLabel: string
}) {
  const [name, setName] = useState(initial?.name ?? '')
  const [repoUrl, setRepoUrl] = useState(initial?.repo_url ?? '')
  const [reviewer, setReviewer] = useState(initial?.reviewer_config ?? {})
  const [implementor, setImplementor] = useState(initial?.implementor_config ?? {})
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  async function submit() {
    setError('')
    if (!name || !repoUrl) {
      setError('Name and repo URL are required')
      return
    }
    setSaving(true)
    try {
      await onSubmit(name, repoUrl, reviewer, implementor)
    } catch (e) {
      setError(message(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal">
      <h4>{submitLabel}</h4>
      {error && <ErrorBanner message={error} />}
      <label>
        Name
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label>
        Repo URL
        <input value={repoUrl} onChange={(e) => setRepoUrl(e.target.value)} placeholder="git@github.com:owner/repo.git" />
      </label>
      <ConfigForm
        label="Reviewer config"
        config={reviewer}
        agents={agents}
        models={models}
        images={images}
        onChange={setReviewer}
      />
      <ConfigForm
        label="Implementor config"
        config={implementor}
        agents={agents}
        models={models}
        images={images}
        onChange={setImplementor}
      />
      <button onClick={submit} disabled={saving}>
        {saving ? 'Saving…' : submitLabel}
      </button>
      <button onClick={onCancel}>Cancel</button>
    </div>
  )
}

function GitHubLists({ projectId }: { projectId: number }) {
  const issues = useAsync(() => api.listIssues(projectId), [projectId])
  const prs = useAsync(() => api.listPulls(projectId), [projectId])
  const polling = useAsync(() => api.getPollingStatus(projectId), [projectId])

  return (
    <div className="github-lists">
      {polling.state.status === 'success' && !polling.state.data.last_ok && (
        <ErrorBanner message={`GitHub polling error: ${polling.state.data.last_error ?? 'unknown'}`} />
      )}
      <h4>Open issues</h4>
      {issues.state.status === 'loading' && <Loading />}
      {issues.state.status === 'error' && <ErrorBanner message={issues.state.error} />}
      {issues.state.status === 'success' &&
        (issues.state.data.length === 0 ? (
          <p className="empty">No open issues.</p>
        ) : (
          <ul className="issue-list">
            {issues.state.data.map((i: GitHubIssue) => (
              <li key={i.issue_number}>
                <Status kind={i.loom_status}>{i.loom_status}</Status> #{i.issue_number}: {i.title}
              </li>
            ))}
          </ul>
        ))}
      <h4>Open pull requests</h4>
      {prs.state.status === 'loading' && <Loading />}
      {prs.state.status === 'error' && <ErrorBanner message={prs.state.error} />}
      {prs.state.status === 'success' &&
        (prs.state.data.length === 0 ? (
          <p className="empty">No open pull requests.</p>
        ) : (
          <ul className="pr-list">
            {prs.state.data.map((p: GitHubPullRequest) => (
              <li key={p.pr_number}>
                <Status kind={p.merged ? 'merged' : 'open'}>{p.merged ? 'merged' : p.state}</Status> #{p.pr_number}: {p.title}
              </li>
            ))}
          </ul>
        ))}
    </div>
  )
}

export function Projects() {
  const { state, refetch } = useAsync(api.listProjects, [])
  const regs = useRegistries()
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<Project | null>(null)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [error, setError] = useState('')

  const agents = regs.agents.state.status === 'success' ? regs.agents.state.data : []
  const models = regs.models.state.status === 'success' ? regs.models.state.data : []
  const images = regs.images.state.status === 'success' ? regs.images.state.data : []

  async function create(name: string, repoUrl: string, reviewer: Record<string, unknown>, implementor: Record<string, unknown>) {
    await api.createProject({ name, repo_url: repoUrl, reviewer_config: reviewer, implementor_config: implementor })
    setCreating(false)
    refetch()
  }

  async function update(name: string, repoUrl: string, reviewer: Record<string, unknown>, implementor: Record<string, unknown>) {
    if (!editing) return
    await api.updateProject(editing.id, { name, repo_url: repoUrl, reviewer_config: reviewer, implementor_config: implementor })
    setEditing(null)
    refetch()
  }

  async function remove(id: number) {
    setError('')
    try {
      await api.deleteProject(id)
      refetch()
    } catch (e) {
      setError(message(e))
    }
  }

  return (
    <Section title="Projects">
      <button onClick={() => setCreating(true)}>New project</button>
      {error && <ErrorBanner message={error} />}
      {state.status === 'loading' && <Loading />}
      {state.status === 'error' && <ErrorBanner message={state.error} />}
      {state.status === 'success' &&
        (state.data.length === 0 ? (
          <p className="empty">No projects yet. Create one to get started.</p>
        ) : (
          <ul className="project-list">
            {state.data.map((p: Project) => (
              <li key={p.id}>
                <div className="project-row">
                  <button className="expand" onClick={() => setExpanded(expanded === p.id ? null : p.id)}>
                    {expanded === p.id ? '▼' : '▶'}
                  </button>
                  <strong>{p.name}</strong>
                  <span className="muted">{p.repo_url}</span>
                  <button onClick={() => setEditing(p)}>Edit</button>
                  <button onClick={() => remove(p.id)}>Delete</button>
                </div>
                {expanded === p.id && (
                  <div className="project-detail">
                    <GitHubLists projectId={p.id} />
                  </div>
                )}
              </li>
            ))}
          </ul>
        ))}
      {creating && (
        <ProjectForm
          initial={null}
          agents={agents}
          models={models}
          images={images}
          onSubmit={create}
          onCancel={() => setCreating(false)}
          submitLabel="Create project"
        />
      )}
      {editing && (
        <ProjectForm
          initial={editing}
          agents={agents}
          models={models}
          images={images}
          onSubmit={update}
          onCancel={() => setEditing(null)}
          submitLabel="Update project"
        />
      )}
    </Section>
  )
}
