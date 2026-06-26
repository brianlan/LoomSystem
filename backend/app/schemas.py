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
