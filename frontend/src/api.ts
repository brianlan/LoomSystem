// Thin typed fetch client for the backend API. No axios dependency.

import type {
  AgentDefinition,
  AggregateStatus,
  DockerImage,
  GitHubIssue,
  GitHubPullRequest,
  ModelEntry,
  PollingStatus,
  Project,
  ProxyConfig,
  TriageConfig,
} from './types'

const BASE = '/api/v1'

export class ApiError extends Error {
  status: number
  constructor(status: number, detail: string) {
    super(detail)
    this.status = status
    this.name = 'ApiError'
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!resp.ok) {
    let detail = resp.statusText
    try {
      const body = await resp.json()
      detail = body.detail ?? detail
    } catch {
      // non-JSON error body
    }
    throw new ApiError(resp.status, detail)
  }
  if (resp.status === 204) return undefined as T
  return (await resp.json()) as T
}

// --- Projects ---
export const listProjects = () => req<Project[]>('/projects')
export const getProject = (id: number) => req<Project>(`/projects/${id}`)
export const createProject = (body: unknown) =>
  req<Project>('/projects', { method: 'POST', body: JSON.stringify(body) })
export const updateProject = (id: number, body: unknown) =>
  req<Project>(`/projects/${id}`, { method: 'PATCH', body: JSON.stringify(body) })
export const deleteProject = (id: number) =>
  req<{ message: string }>(`/projects/${id}`, { method: 'DELETE' })

// --- GitHub snapshots ---
export const listIssues = (projectId: number) =>
  req<GitHubIssue[]>(`/projects/${projectId}/github/issues`)
export const listPulls = (projectId: number) =>
  req<GitHubPullRequest[]>(`/projects/${projectId}/github/pulls`)
export const getPollingStatus = (projectId: number) =>
  req<PollingStatus>(`/projects/${projectId}/github/polling-status`)

// --- Aggregate status ---
export const getAggregateStatus = () => req<AggregateStatus>('/status')

// --- Agent definitions ---
export const listAgentDefinitions = () =>
  req<AgentDefinition[]>('/settings/agent-definitions')
export const createAgentDefinition = (body: unknown) =>
  req<AgentDefinition>('/settings/agent-definitions', {
    method: 'POST',
    body: JSON.stringify(body),
  })
export const updateAgentDefinition = (id: number, body: unknown) =>
  req<AgentDefinition>(`/settings/agent-definitions/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
export const deleteAgentDefinition = (id: number) =>
  req<{ message: string }>(`/settings/agent-definitions/${id}`, {
    method: 'DELETE',
  })

// --- Model entries ---
export const listModelEntries = () => req<ModelEntry[]>('/settings/model-entries')
export const createModelEntry = (body: unknown) =>
  req<ModelEntry>('/settings/model-entries', { method: 'POST', body: JSON.stringify(body) })
export const updateModelEntry = (id: number, body: unknown) =>
  req<ModelEntry>(`/settings/model-entries/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
export const deleteModelEntry = (id: number) =>
  req<{ message: string }>(`/settings/model-entries/${id}`, { method: 'DELETE' })

// --- Docker images ---
export const listDockerImages = () => req<DockerImage[]>('/settings/docker-images')
export const createDockerImage = (body: unknown) =>
  req<DockerImage>('/settings/docker-images', { method: 'POST', body: JSON.stringify(body) })
export const updateDockerImage = (id: number, body: unknown) =>
  req<DockerImage>(`/settings/docker-images/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
export const deleteDockerImage = (id: number) =>
  req<{ message: string }>(`/settings/docker-images/${id}`, { method: 'DELETE' })

// --- Singleton settings ---
export const setSshKey = (key_value: string) =>
  req<{ message: string }>('/settings/ssh-key', {
    method: 'PUT',
    body: JSON.stringify({ key_value }),
  })
export const getSshKey = () => req<{ key_value: string }>('/settings/ssh-key')
export const deleteSshKey = () =>
  req<{ message: string }>('/settings/ssh-key', { method: 'DELETE' })

export const setGithubToken = (token_value: string) =>
  req<{ message: string }>('/settings/github-token', {
    method: 'PUT',
    body: JSON.stringify({ token_value }),
  })
export const getGithubToken = () => req<{ token_value: string }>('/settings/github-token')
export const deleteGithubToken = () =>
  req<{ message: string }>('/settings/github-token', { method: 'DELETE' })

export const setTriageConfig = (body: unknown) =>
  req<{ message: string }>('/settings/triage-config', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
export const getTriageConfig = () => req<TriageConfig>('/settings/triage-config')
export const deleteTriageConfig = () =>
  req<{ message: string }>('/settings/triage-config', { method: 'DELETE' })

export const setProxy = (body: unknown) =>
  req<{ message: string }>('/settings/proxy', { method: 'PUT', body: JSON.stringify(body) })
export const getProxy = () => req<ProxyConfig>('/settings/proxy')
export const deleteProxy = () =>
  req<{ message: string }>('/settings/proxy', { method: 'DELETE' })
