// Shared API response types, mirroring backend schemas.

export interface AgentDefinition {
  id: number
  name: string
  prompt_markdown: string
  github_identity: string
  permissions: Record<string, unknown>
}

export interface ModelEntry {
  id: number
  provider_id: string
  model_id: string
  display_name: string | null
  custom_config: Record<string, unknown> | null
}

export interface DockerImage {
  id: number
  image_name: string
}

export interface ReviewerConfig {
  agent_definition_id: number
  model_entry_id: number
  docker_image_id: number
  trigger_interval_minutes: number
  reviewer_cap: number
}

export interface ImplementorConfig {
  agent_definition_id: number
  model_entry_id: number
  docker_image_id: number
  trigger_interval_minutes: number
  parallelism: number
}

export interface Project {
  id: number
  name: string
  repo_url: string
  reviewer_config: Partial<ReviewerConfig>
  implementor_config: Partial<ImplementorConfig>
  created_at: string
  updated_at: string
}

export interface GitHubIssue {
  issue_number: number
  title: string
  state: string
  loom_status: string
  updated_at: string
}

export interface GitHubPullRequest {
  pr_number: number
  title: string
  state: string
  merged: boolean
  updated_at: string
}

export interface PollingStatus {
  project_id: number
  last_polled_at: string | null
  last_ok: boolean
  last_error: string | null
}

export interface AggregateStatus {
  running_reviewers: number
  running_implementors: number
  backlog_size: number
  recent_failures: Array<{
    kind: string
    id: number
    project_id: number
    created_at: string
    error?: string | null
    agent_type?: string
  }>
}

export interface TriageConfig {
  endpoint_url: string
  model_name: string
  headers: Record<string, string>
}

export interface ProxyConfig {
  http_proxy: string | null
  https_proxy: string | null
}
