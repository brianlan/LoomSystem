from typing import Any

from pydantic import BaseModel, Field


class AgentDefinitionCreate(BaseModel):
    name: str
    prompt_markdown: str
    github_identity: str
    permissions: dict[str, Any] = Field(default_factory=dict)


class AgentDefinitionRead(BaseModel):
    id: int
    name: str
    prompt_markdown: str
    github_identity: str
    permissions: dict[str, Any]


class AgentDefinitionUpdate(BaseModel):
    name: str | None = None
    prompt_markdown: str | None = None
    github_identity: str | None = None
    permissions: dict[str, Any] | None = None


class ModelEntryCreate(BaseModel):
    provider_id: str
    model_id: str
    credentials: str
    display_name: str | None = None
    custom_config: dict[str, Any] | None = None


class ModelEntryRead(BaseModel):
    id: int
    provider_id: str
    model_id: str
    display_name: str | None
    custom_config: dict[str, Any] | None


class ModelEntryUpdate(BaseModel):
    provider_id: str | None = None
    model_id: str | None = None
    credentials: str | None = None
    display_name: str | None = None
    custom_config: dict[str, Any] | None = None


class DockerImageCreate(BaseModel):
    image_name: str


class DockerImageRead(BaseModel):
    id: int
    image_name: str


class DockerImageUpdate(BaseModel):
    image_name: str


class SSHKeyPayload(BaseModel):
    key_value: str


class GitHubTokenPayload(BaseModel):
    token_value: str


class TriageConfigPayload(BaseModel):
    endpoint_url: str
    model_name: str
    api_key: str
    headers: dict[str, str] = Field(default_factory=dict)


class TriageConfigRead(BaseModel):
    endpoint_url: str
    model_name: str
    headers: dict[str, str]


class ProxyPayload(BaseModel):
    http_proxy: str | None = None
    https_proxy: str | None = None


class MessageResponse(BaseModel):
    message: str


class ReviewerConfig(BaseModel):
    agent_definition_id: int
    model_entry_id: int
    docker_image_id: int
    trigger_interval_minutes: int = 15
    reviewer_cap: int = 1


class ImplementorConfig(BaseModel):
    agent_definition_id: int
    model_entry_id: int
    docker_image_id: int
    trigger_interval_minutes: int = 15
    parallelism: int = 1


class ProjectCreate(BaseModel):
    name: str
    repo_url: str
    reviewer_config: ReviewerConfig | None = None
    implementor_config: ImplementorConfig | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    repo_url: str | None = None
    reviewer_config: ReviewerConfig | None = None
    implementor_config: ImplementorConfig | None = None


class ProjectRead(BaseModel):
    id: int
    name: str
    repo_url: str
    reviewer_config: dict[str, Any]
    implementor_config: dict[str, Any]
    created_at: str
    updated_at: str


class ReviewerLaunchResult(BaseModel):
    agent_instance_id: int
    container_id: str
    container_name: str


class ReviewerInstanceStatus(BaseModel):
    agent_instance_id: int
    container_id: str | None
    container_name: str | None
    session_id: str | None
    status: str


class ReviewerStatus(BaseModel):
    project_id: int
    reviewer_cap: int
    running_reviewers: int
    reviewers: list[ReviewerInstanceStatus]
