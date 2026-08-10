from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginRequest(StrictModel):
    cloud_api_url: str = Field(alias="cloudApiUrl", min_length=1, max_length=1024)
    identifier: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class JoinRequest(StrictModel):
    cloud_api_url: str = Field(alias="cloudApiUrl", min_length=1, max_length=1024)
    invite_code: str = Field(alias="inviteCode", min_length=1, max_length=256)
    display_name: str = Field(alias="displayName", min_length=1, max_length=80)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=32)
    password: str = Field(min_length=8, max_length=256)

    @model_validator(mode="after")
    def require_contact(self):
        if not self.email and not self.phone:
            raise ValueError("邮箱或手机号至少填写一项")
        return self


class CreateOrganizationRequest(StrictModel):
    cloud_api_url: str = Field(alias="cloudApiUrl", min_length=1, max_length=1024)
    bootstrap_token: str = Field(alias="bootstrapToken", min_length=1)
    organization_name: str = Field(alias="organizationName", min_length=1, max_length=120)
    display_name: str = Field(alias="displayName", min_length=1, max_length=80)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=32)
    password: str = Field(min_length=8, max_length=256)

    @model_validator(mode="after")
    def require_contact(self):
        if not self.email and not self.phone:
            raise ValueError("邮箱或手机号至少填写一项")
        return self


class SwitchWorkspaceRequest(StrictModel):
    sandbox_id: str = Field(alias="sandboxId", min_length=1, max_length=80)


class OrganizationAiConfigRequest(StrictModel):
    provider: str = Field(min_length=1, max_length=80)
    base_url: str = Field(alias="baseUrl", min_length=1, max_length=1024)
    model_name: str = Field(alias="modelName", min_length=1, max_length=200)
    api_key: str = Field(alias="apiKey", min_length=0, max_length=4096)
    expected_version: int | None = Field(default=None, alias="expectedVersion", ge=0)
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey", max_length=120)


class NamedStructureRequest(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    expected_organization_version: int | None = Field(
        default=None,
        alias="expectedOrganizationVersion",
        ge=1,
    )


class InviteRequest(StrictModel):
    invite_kind: str = Field(alias="inviteKind", pattern="^(department|management_title)$")
    target_id: str = Field(alias="targetId", min_length=1, max_length=80)
    expires_at: str | None = Field(default=None, alias="expiresAt", max_length=64)


class TaskCreateRequest(StrictModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=20000)
    project_id: str | None = Field(default=None, alias="projectId", max_length=80)
    owner_membership_id: str | None = Field(
        default=None,
        alias="ownerMembershipId",
        max_length=80,
    )
    collaborator_membership_ids: list[str] = Field(
        default_factory=list,
        alias="collaboratorMembershipIds",
        max_length=200,
    )
    priority: str = Field(default="normal", pattern="^(low|normal|high|urgent)$")
    visibility_scope: str = Field(
        default="participants",
        alias="visibilityScope",
        pattern="^(organization|department|participants|self)$",
    )
    start_date: str | None = Field(default=None, alias="startDate", max_length=64)
    due_date: str | None = Field(default=None, alias="dueDate", max_length=64)
    scheduled_start_at: str | None = Field(
        default=None,
        alias="scheduledStartAt",
        max_length=64,
    )
    scheduled_end_at: str | None = Field(
        default=None,
        alias="scheduledEndAt",
        max_length=64,
    )
    deadline_at: str | None = Field(default=None, alias="deadlineAt", max_length=64)
    duration_minutes: int = Field(default=60, alias="durationMinutes", ge=0, le=525600)


class TaskUpdateRequest(StrictModel):
    expected_version: int = Field(alias="expectedVersion", ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=20000)
    project_id: str | None = Field(default=None, alias="projectId", max_length=80)
    owner_membership_id: str | None = Field(
        default=None,
        alias="ownerMembershipId",
        max_length=80,
    )
    collaborator_membership_ids: list[str] | None = Field(
        default=None,
        alias="collaboratorMembershipIds",
        max_length=200,
    )
    priority: str | None = Field(
        default=None,
        pattern="^(low|normal|high|urgent)$",
    )
    visibility_scope: str | None = Field(
        default=None,
        alias="visibilityScope",
        pattern="^(organization|department|participants|self)$",
    )
    start_date: str | None = Field(default=None, alias="startDate", max_length=64)
    due_date: str | None = Field(default=None, alias="dueDate", max_length=64)
    scheduled_start_at: str | None = Field(
        default=None,
        alias="scheduledStartAt",
        max_length=64,
    )
    scheduled_end_at: str | None = Field(
        default=None,
        alias="scheduledEndAt",
        max_length=64,
    )
    deadline_at: str | None = Field(default=None, alias="deadlineAt", max_length=64)
    duration_minutes: int | None = Field(
        default=None,
        alias="durationMinutes",
        ge=0,
        le=525600,
    )


class TaskTransitionRequest(StrictModel):
    expected_version: int = Field(alias="expectedVersion", ge=1)
    completion_note: str = Field(
        default="",
        alias="completionNote",
        max_length=20000,
    )


class TaskInboxHandleRequest(StrictModel):
    expected_version: int = Field(alias="expectedVersion", ge=1)


class EventLineCreateRequest(StrictModel):
    project_id: str = Field(alias="projectId", min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=300)
    goal: str = Field(default="", max_length=20000)
    background: str = Field(default="", max_length=40000)
    participant_membership_ids: list[str] = Field(
        default_factory=list,
        alias="participantMembershipIds",
        max_length=200,
    )


class WorkbenchChatRequest(StrictModel):
    project_id: str | None = Field(default=None, alias="projectId", max_length=80)
    question: str = Field(min_length=1, max_length=20000)
    mode: str = Field(default="balanced", pattern="^(balanced|creative)$")
