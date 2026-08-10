from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping

from strict_common.ids import new_id

from .local_input_memory import LocalInputMemoryStore
from .runtime import LocalRuntimeError, WorkspaceRuntime
from .ui_domains import NOT_HANDLED, UiRequest, build_default_registry


YIYU_OFFICIAL_ORGANIZATION_ID = "org_yiyu_default"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _string(value: Any) -> str:
    return str(value or "").strip()


class StrictUiCompatibility:
    """Translate strict v2 facts into the mature renderer's presentation DTOs."""

    def __init__(self, runtime: WorkspaceRuntime):
        self.runtime = runtime
        self.domain_registry = build_default_registry()

    def _current(self) -> dict[str, Any]:
        return self.runtime.current()

    def _snapshot(self, *, refresh: bool = False) -> dict[str, Any]:
        return self.runtime.business_snapshot(refresh=refresh)

    def _session(self) -> dict[str, Any]:
        return self._current().get("sessionSnapshot") or {}

    def _membership_department(
        self,
        session: Mapping[str, Any],
        membership_id: str,
    ) -> tuple[str | None, str | None, bool]:
        for department in session.get("departments") or []:
            for member in department.get("members") or []:
                if _string(member.get("membershipId")) == membership_id:
                    return (
                        _string(department.get("departmentId")) or None,
                        _string(department.get("name")) or None,
                        bool(member.get("isDepartmentLead")),
                    )
        return None, None, False

    def _management_title(
        self,
        session: Mapping[str, Any],
        membership_id: str,
    ) -> tuple[str | None, str | None]:
        for title in session.get("managementTitles") or []:
            if any(
                _string(member.get("membershipId")) == membership_id
                for member in title.get("members") or []
            ):
                return (
                    _string(title.get("titleId")) or None,
                    _string(title.get("name")) or None,
                )
        return None, None

    def auth_state(self) -> dict[str, Any]:
        current = self._current()
        session = current.get("sessionSnapshot") or {}
        membership = session.get("membership") or {}
        principal = session.get("principal") or {}
        organization = session.get("organization") or {}
        membership_id = _string(membership.get("membershipId"))
        authenticated = bool(
            membership_id
            and current.get("runtimeStatus") in {"ready", "sync_degraded"}
        )
        if not authenticated:
            return {
                "authenticated": False,
                "user": None,
                "message": current.get("statusMessage"),
                "sessionMode": "local",
                "requiresLocalIdentitySetup": False,
                "localIdentityStatus": "draft",
            }
        contacts = {
            _string(item.get("type")): _string(item.get("value"))
            for item in principal.get("contacts") or []
        }
        department_id, department_name, is_lead = self._membership_department(
            session,
            membership_id,
        )
        title_id, title_name = self._management_title(session, membership_id)
        system_role = _string(membership.get("systemRole"))
        authorization = session.get("authorization")
        if not isinstance(authorization, Mapping):
            authorization = {
                "state": "failed_retryable",
                "freshness": "unknown",
                "reasonCode": "authorization_projection_missing",
                "retryable": True,
                "surfaces": [],
                "capabilities": [],
            }
        return {
            "authenticated": True,
            "sessionMode": "cloud",
            "degraded": current.get("runtimeStatus") == "sync_degraded",
            "authorization": dict(authorization),
            "user": {
                "id": membership_id,
                "organizationId": _string(organization.get("organizationId")),
                "organizationName": _string(organization.get("name")),
                "email": contacts.get("email", ""),
                "phone": contacts.get("phone") or None,
                "fullName": _string(principal.get("displayName"))
                or _string(membership.get("displayName")),
                "primaryRole": "admin" if system_role == "admin" else "employee",
                "accountStatus": (
                    "active"
                    if _string(membership.get("status")) == "active"
                    else "disabled"
                ),
                "membershipStatus": "approved",
                "departmentId": department_id,
                "departmentName": department_name,
                "isDepartmentLead": is_lead,
                "visibilityScope": membership.get("visibilityScope") or "self",
                "managementTitleId": title_id,
                "managementTitleName": title_name,
            },
        }

    def _workspace_record(self, item: Mapping[str, Any]) -> dict[str, Any]:
        kind = _string(item.get("kind"))
        runtime_status = _string(item.get("runtimeStatus")) or "local_draft"
        cloud_connected = kind == "organization" and runtime_status in {
            "ready",
            "sync_degraded",
        }
        current = self._current()
        snapshot = (
            current.get("sessionSnapshot")
            if item.get("isActive")
            else None
        )
        return {
            "id": item.get("sandboxId"),
            "kind": "organization" if kind == "organization" else "local",
            "name": item.get("displayName") or "本机未登录隔离区",
            "status": "active",
            "cloudApiUrl": item.get("cloudApiUrl") or "",
            "cloudConnected": cloud_connected,
            "cloudConnectionStatus": (
                "failed_retryable"
                if runtime_status == "sync_degraded"
                else (
                    "connected"
                    if cloud_connected
                    else (
                        "needs_login"
                        if runtime_status == "needs_login"
                        else "signed_out"
                    )
                )
            ),
            "cloudNeedsLogin": runtime_status == "needs_login",
            "cloudInstanceId": item.get("cloudInstanceId"),
            "identityState": item.get("identityState") or "unverified",
            "runtimeStatus": runtime_status,
            "statusMessage": current.get("statusMessage") if item.get("isActive") else "",
            "requiresLogin": runtime_status == "needs_login",
            "sessionSnapshot": snapshot or {},
            "organizationId": item.get("organizationId"),
            "organizationName": item.get("displayName"),
            "isLegacyDefault": False,
            "metadata": {},
            "createdAt": item.get("updatedAt") or _now(),
            "updatedAt": item.get("updatedAt") or _now(),
            "lastActiveAt": item.get("updatedAt") if item.get("isActive") else None,
        }

    def workspaces(self) -> dict[str, Any]:
        items = self.runtime.list_workspaces()
        active = next((item for item in items if item.get("isActive")), None)
        organizations = [
            item for item in items if item.get("kind") == "organization"
        ]
        return {
            "activeSandboxId": (
                active.get("sandboxId")
                if active and active.get("kind") == "organization"
                else ""
            ),
            "workspaces": [
                self._workspace_record(item) for item in organizations
            ],
            "localDraftSummary": {
                "available": False,
                "active": bool(active and active.get("kind") == "local_draft"),
                "hasData": False,
                "migrated": False,
                "clients": 0,
                "tasks": 0,
                "taskLists": 0,
                "taskTags": 0,
                "documents": 0,
                "experienceQuotes": 0,
            },
        }

    def current_workspace(self) -> dict[str, Any]:
        items = self.runtime.list_workspaces()
        active = next((item for item in items if item.get("isActive")), None)
        if not active:
            raise LocalRuntimeError(500, "active_workspace_missing", "没有活动工作空间")
        return self._workspace_record(active)

    def health(self) -> dict[str, Any]:
        current = self._current()
        ai = self.runtime.organization_ai_runtime_status()
        return {
            "backend": "online",
            "appName": "益语智库AI（新版）",
            "appVersion": "strict",
            "buildVersion": _string(
                (current.get("databaseIdentity") or {}).get("buildId")
            ),
            "backendSchemaVersion": 2,
            "runtimeMode": "packaged",
            "startedAt": _now(),
            "featureFlags": [
                "strict_v2",
                "mature_renderer",
                "knowledge.vectorize-answer",
                "knowledge.reclass-events",
                "knowledge.search",
                "knowledge.rebuild",
                "chat.general-answer",
                "chat.instant-send",
                "chat.async-status",
            ],
            "dataDir": "",
            "stats": {
                "clients": 0,
                "tasks": 0,
                "topics": 0,
                "handbookEntries": 0,
                "analysisRuns": 0,
            },
            "ai": {
                "provider": ai.get("provider") or "openai_compatible",
                "providerLabel": ai.get("providerLabel") or "组织统一模型",
                "baseUrl": "",
                "model": ai.get("model") or "",
                "ready": ai.get("state") == "ready_direct",
                "detail": ai.get("lastError") or "",
                "credentialSource": "organization_direct",
                "fingerprint": ai.get("fingerprint"),
            },
        }

    def ai_runtime(self) -> dict[str, Any]:
        current = self._current()
        ai = self.runtime.organization_ai_runtime_status()
        sandbox = current.get("sandbox") or {}
        return {
            **ai,
            "sandboxId": sandbox.get("sandboxId") or "",
            "organizationId": sandbox.get("organizationId") or "",
        }

    def feishu_integration(self) -> dict[str, Any]:
        current = self._current()
        sandbox = current.get("sandbox") or {}
        return {
            "organizationId": sandbox.get("organizationId"),
            "organizationName": sandbox.get("displayName"),
            "appId": "",
            "callbackMode": "cloud_relay",
            "customCallbackUrl": "",
            "effectiveCallbackUrl": "",
            "enabled": False,
            "hasAppSecret": False,
            "configuredBy": None,
            "configuredAt": None,
            "updatedAt": sandbox.get("updatedAt") or "",
            "lastValidationStatus": "idle",
            "lastValidationMessage": "严格新版飞书配置尚未接通",
            "authorizationReady": False,
            "authorizationBlockedReason": "严格新版飞书配置尚未接通",
            "recentAudits": [],
        }

    def feishu_delivery_profile(self) -> dict[str, Any]:
        current = self._current()
        sandbox = current.get("sandbox") or {}
        user = self.auth_state().get("user") or {}
        return {
            "userId": user.get("id") or "",
            "organizationId": sandbox.get("organizationId"),
            "organizationName": sandbox.get("displayName"),
            "mobile": user.get("phone") or "",
            "normalizedMobile": None,
            "deliveryStatus": "integration_pending",
            "deliveryStatusLabel": "飞书任务提醒尚未接通",
            "readyForNotifications": False,
            "receiveId": None,
            "lastVerifiedAt": None,
            "lastError": None,
            "blockedReason": "严格新版飞书配置尚未接通",
        }

    def feishu_member_authorization(self) -> dict[str, Any]:
        current = self._current()
        sandbox = current.get("sandbox") or {}
        user = self.auth_state().get("user") or {}
        return {
            "linked": False,
            "readyForAuthorization": False,
            "organizationId": sandbox.get("organizationId"),
            "organizationName": sandbox.get("displayName"),
            "appId": "",
            "userId": user.get("id") or "",
            "openId": None,
            "unionId": None,
            "feishuUserId": None,
            "name": user.get("fullName"),
            "enName": None,
            "avatarUrl": None,
            "email": user.get("email"),
            "tenantKey": None,
            "boundAt": None,
            "lastVerifiedAt": None,
            "lastError": None,
            "blockedReason": "严格新版飞书配置尚未接通",
        }

    def settings(self) -> dict[str, Any]:
        auth = self.auth_state()
        user = auth.get("user") or {}
        current = self._current()
        ai = current.get("aiRuntime") or {}
        model = ai.get("modelName") or ""
        provider = ai.get("provider") or "openai_compatible"
        ready = ai.get("state") == "ready_direct"
        settings = {
            "currentOperatorId": user.get("id") or "",
            "aiProvider": provider,
            "aiProviderLabel": provider,
            "aiBaseUrl": ai.get("baseUrl") or "",
            "aiModel": model,
            "dataDir": "",
            "backupDir": "",
            "cloudApiUrl": (current.get("sandbox") or {}).get("cloudApiUrl") or "",
            "lastBackupAt": None,
            "foldersRootLabel": "项目资料",
            "aiConfigured": ready,
            "aiCredentialSource": "organization_direct",
            "aiFingerprint": ai.get("keyFingerprint"),
            "advancedAiRoutingEnabled": False,
            "aiModelMode": "auto",
            "aiModelProfiles": {},
            "demoDataLoaded": False,
        }
        operators = []
        for member in self._session().get("members") or []:
            operators.append(
                {
                    "id": member.get("membershipId"),
                    "name": member.get("displayName") or "成员",
                    "role": (
                        "admin"
                        if member.get("systemRole") == "admin"
                        else "employee"
                    ),
                    "team": "",
                    "color": "#5B7CFA",
                    "isCurrent": member.get("membershipId") == user.get("id"),
                }
            )
        return {
            "settings": settings,
            "operators": operators,
            "health": self.health(),
            "lastCloudAiSyncStatus": {
                "state": "ready_direct" if ready else "not_ready",
                "at": ai.get("syncedAt"),
                "reason": ai.get("message") or None,
                "provider": provider,
                "providerLabel": provider,
                "model": model,
                "baseUrl": None,
                "hasApiKey": ready,
                "fingerprint": ai.get("keyFingerprint"),
            },
        }

    def _member_names(self) -> dict[str, str]:
        return {
            _string(item.get("membershipId")): _string(item.get("displayName"))
            for item in self._session().get("members") or []
        }

    def clients(self, snapshot: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        data = snapshot or self._snapshot()
        tasks = data.get("tasks") or []
        documents = data.get("documents") or []
        result = []
        for project in data.get("projects") or []:
            project_id = _string(project.get("projectId"))
            result.append(
                {
                    "id": project_id,
                    "name": project.get("name") or "未命名项目",
                    "alias": project.get("alias") or project.get("name") or "",
                    "domain": project.get("domain") or "",
                    "type": "project",
                    "intro": project.get("summary") or "",
                    "stage": project.get("lifecycleState") or "active",
                    "color": project.get("color") or "#5B7CFA",
                    "folderCount": 0,
                    "documentCount": sum(
                        1 for item in documents if item.get("projectId") == project_id
                    ),
                    "taskCount": sum(
                        1 for item in tasks if item.get("projectId") == project_id
                    ),
                    "lastActivityAt": project.get("updatedAt"),
                    "relatedUserIds": [],
                    "isDataCenterIncluded": True,
                    "isDefaultInternalProject": bool(
                        project.get("isDefaultInternalProject")
                    ),
                    "isFrozen": project.get("lifecycleState") != "active",
                    "syncStatus": "synced",
                    "cloudId": project_id,
                }
            )
        return result

    def _task(self, item: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict[str, Any]:
        projects = {
            _string(project.get("projectId")): project
            for project in snapshot.get("projects") or []
        }
        event_lines = {
            _string(line.get("eventLineId")): line
            for line in snapshot.get("eventLines") or []
        }
        names = self._member_names()
        current_member_id = _string(
            (self._session().get("membership") or {}).get("membershipId")
        )
        collaborators = item.get("collaborators") or []
        owner = next(
            (entry for entry in collaborators if entry.get("role") == "owner"),
            None,
        )
        mapped_collaborators = []
        viewer_status = None
        summary = {"pending": 0, "accepted": 0, "returned": 0}
        for index, entry in enumerate(collaborators):
            membership_id = _string(entry.get("membershipId"))
            inbox_state = _string(entry.get("inboxState")) or "accepted"
            if inbox_state not in summary:
                inbox_state = "accepted"
            summary[inbox_state] += 1
            if membership_id == current_member_id:
                viewer_status = inbox_state
            mapped_collaborators.append(
                {
                    "userId": membership_id,
                    "fullName": entry.get("displayName")
                    or names.get(membership_id)
                    or "成员",
                    "email": "",
                    "orderIndex": index,
                    "isOwner": entry.get("role") == "owner",
                    "inboxStatus": inbox_state,
                    "returnReason": entry.get("returnReason") or None,
                    "handledAt": entry.get("handledAt"),
                }
            )
        lifecycle = _string(item.get("lifecycleState"))
        status_map = {
            "completed": "done",
            "done": "done",
            "cancelled": "rejected",
            "rejected": "rejected",
            "doing": "doing",
            "in_progress": "doing",
            "inbox": "inbox",
        }
        status = status_map.get(lifecycle, "todo")
        project_id = _string(item.get("projectId")) or None
        event_line_id = _string(item.get("eventLineId")) or None
        due = (
            item.get("dueDate")
            or item.get("deadlineAt")
            or item.get("scheduledStartAt")
            or ""
        )
        return {
            "id": item.get("taskId"),
            "organizationId": snapshot.get("organizationId"),
            "title": item.get("title") or "未命名任务",
            "desc": item.get("description") or "",
            "status": status,
            "creatorId": item.get("createdByMembershipId"),
            "creatorName": names.get(
                _string(item.get("createdByMembershipId")),
                "",
            ),
            "priority": item.get("priority") or "normal",
            "listId": "strict-default",
            "listName": "全部任务",
            "listColor": "#5B7CFA",
            "ddl": due,
            "startDate": item.get("startDate"),
            "dueDate": item.get("dueDate"),
            "durationMinutes": item.get("durationMinutes") or 60,
            "deadlineAt": item.get("deadlineAt"),
            "scheduledStartAt": item.get("scheduledStartAt"),
            "scheduledEndAt": item.get("scheduledEndAt"),
            "completedAt": item.get("completedAt"),
            "scopeMode": (
                "PERSONAL_ONLY"
                if item.get("visibilityScope") == "self"
                else "COLLAB_SHARED"
            ),
            "clientId": project_id,
            "clientName": (projects.get(project_id or "") or {}).get("name"),
            "eventLineId": event_line_id,
            "eventLineName": (
                event_lines.get(event_line_id or "") or {}
            ).get("name"),
            "ownerId": (owner or {}).get("membershipId"),
            "ownerName": (owner or {}).get("displayName") or "未指定",
            "sourceType": "strict_v2",
            "evidenceCount": 0,
            "tags": [],
            "attachments": [],
            "collaborators": mapped_collaborators,
            "collaborationSummary": summary,
            "viewerInboxStatus": viewer_status,
            "syncStatus": "synced",
            "localMutationSeq": item.get("version") or 1,
            "syncError": None,
            "createdAt": item.get("createdAt") or item.get("updatedAt") or _now(),
            "updatedAt": item.get("updatedAt") or _now(),
            "_strictVersion": item.get("version") or 1,
        }

    def task_board(self) -> dict[str, Any]:
        snapshot = self._snapshot()
        return {
            "tasks": [self._task(item, snapshot) for item in snapshot.get("tasks") or []],
            "lists": [
                {
                    "id": "strict-default",
                    "name": "全部任务",
                    "color": "#5B7CFA",
                    "sortOrder": 0,
                    "isDefault": True,
                    "scope": "org",
                }
            ],
            "tags": [],
        }

    def event_lines(self) -> list[dict[str, Any]]:
        snapshot = self._snapshot()
        projects = {
            _string(project.get("projectId")): project
            for project in snapshot.get("projects") or []
        }
        names = self._member_names()
        current = self.auth_state().get("user") or {}
        current_id = _string(current.get("id"))
        current_department_id = _string(current.get("departmentId"))
        is_management = current.get("primaryRole") == "admin" or (
            current.get("visibilityScope") == "organization"
        )
        result = []
        for item in snapshot.get("eventLines") or []:
            creator_id = _string(item.get("createdByMembershipId"))
            participants = [
                _string(value) for value in item.get("participantMembershipIds") or []
            ]
            can_manage = is_management or creator_id == current_id
            can_contribute = (
                can_manage
                or current_id in participants
                or (
                    _string(item.get("departmentId"))
                    and _string(item.get("departmentId")) == current_department_id
                )
                or item.get("visibilityScope") == "organization"
            )
            project_id = _string(item.get("projectId")) or None
            lifecycle = _string(item.get("lifecycleState"))
            status = lifecycle if lifecycle in {
                "active",
                "blocked",
                "paused",
                "done",
                "archived",
            } else "active"
            missing = []
            if not _string(item.get("goal")):
                missing.append("目标")
            if not _string(item.get("background")):
                missing.append("背景")
            if int(item.get("milestoneCount") or 0) == 0:
                missing.append("里程碑")
            if int(item.get("attachmentCount") or 0) == 0:
                missing.append("证据材料")
            readiness = (
                "substantial"
                if not missing
                else ("general" if len(missing) <= 2 else "incomplete")
            )
            result.append(
                {
                    "id": item.get("eventLineId"),
                    "name": item.get("name") or "未命名事件线",
                    "kind": "custom",
                    "status": status,
                    "visibilityScope": (
                        "project_public"
                        if item.get("visibilityScope") == "organization"
                        else (
                            "department"
                            if item.get("visibilityScope") == "department"
                            else "private"
                        )
                    ),
                    "summary": item.get("background") or "",
                    "intent": item.get("goal") or "",
                    "summaryConfirmedAt": (
                        item.get("updatedAt") if item.get("background") else None
                    ),
                    "intentConfirmedAt": (
                        item.get("updatedAt") if item.get("goal") else None
                    ),
                    "evidenceCount": int(item.get("attachmentCount") or 0),
                    "taskCount": int(item.get("taskCount") or 0),
                    "attachmentCount": int(item.get("attachmentCount") or 0),
                    "activityCount": int(item.get("taskCount") or 0),
                    "ownerId": creator_id or None,
                    "ownerName": names.get(creator_id),
                    "createdByUserId": creator_id or None,
                    "createdByName": names.get(creator_id),
                    "primaryClientId": project_id,
                    "primaryClientName": (
                        projects.get(project_id or "") or {}
                    ).get("name"),
                    "primaryDepartmentId": item.get("departmentId"),
                    "participantIds": participants,
                    "materialRequirements": [],
                    "syncStatus": "synced",
                    "cloudId": item.get("eventLineId"),
                    "readinessLevel": readiness,
                    "readinessMissingItems": missing,
                    "version": int(item.get("version") or 1),
                    "viewerCapabilities": {
                        "canView": True,
                        "canContribute": can_contribute,
                        "canManageStructure": can_manage,
                        "canAssignOwner": False,
                        "canArchive": can_manage,
                        "canReparentProject": can_manage,
                        "canAddParticipants": can_contribute,
                        "canManageParticipants": can_manage,
                        "canSetMilestone": can_contribute,
                    },
                    "createdAt": item.get("createdAt") or item.get("updatedAt") or _now(),
                    "updatedAt": item.get("updatedAt") or _now(),
                }
            )
        return result

    def event_line_detail(self, event_line_id: str) -> dict[str, Any]:
        line = next(
            (item for item in self.event_lines() if item["id"] == event_line_id),
            None,
        )
        if line is None:
            raise LocalRuntimeError(404, "event_line_missing", "事件线不存在")
        snapshot = self._snapshot()
        return {
            "eventLine": line,
            "tasks": [
                self._task(item, snapshot)
                for item in snapshot.get("tasks") or []
                if item.get("eventLineId") == event_line_id
            ],
            "activities": [],
            "memorySnapshot": None,
            "predictionReadiness": None,
            "clarificationNeeds": line["readinessMissingItems"],
        }

    def client_workspace(self, project_id: str) -> dict[str, Any]:
        snapshot = self._snapshot()
        client = next(
            (item for item in self.clients(snapshot) if item["id"] == project_id),
            None,
        )
        if client is None:
            raise LocalRuntimeError(404, "project_missing", "项目不存在")
        knowledge_context = self.runtime.project_knowledge_context(project_id)
        documents = [
            {
                "id": item.get("documentId"),
                "clientId": project_id,
                "folderId": None,
                "title": item.get("title") or "未命名资料",
                "path": "",
                "kind": item.get("documentKind") or "file",
                "source": "strict_v2",
                "excerpt": "",
                "tags": [],
                "importedAt": item.get("updatedAt") or _now(),
            }
            for item in snapshot.get("documents") or []
            if item.get("projectId") == project_id
        ]
        return {
            "client": client,
            "folders": [],
            "documents": documents,
            "documentCards": [],
            "imports": [],
            "knowledgeJobs": [],
            "recentReclassEvents": [],
            "surrogateCount": 0,
            "memoryDocCount": 0,
            "memoryCards": [],
            "threads": [],
            "recentMessages": [],
            "analysisRuns": [],
            "meetings": [],
            "goals": [],
            "dnaModules": [],
            "projectModules": [],
            "projectFlows": [],
            "dnaTerms": [],
            "relatedTasks": [
                self._task(item, snapshot)
                for item in snapshot.get("tasks") or []
                if item.get("projectId") == project_id
            ],
            "latestJudgments": [],
            "latestTopics": [],
            "latestConflicts": [],
            "latestOpenQuestions": [],
            "latestRunLogs": [],
            "knowledgeContext": knowledge_context,
        }

    def employees(self) -> list[dict[str, Any]]:
        session = self._session()
        current = self.auth_state().get("user") or {}
        result = []
        for member in session.get("members") or []:
            membership_id = _string(member.get("membershipId"))
            department_id, department_name, is_lead = self._membership_department(
                session,
                membership_id,
            )
            title_id, title_name = self._management_title(session, membership_id)
            result.append(
                {
                    "id": membership_id,
                    "email": "",
                    "phone": None,
                    "fullName": member.get("displayName") or "成员",
                    "primaryRole": (
                        "admin"
                        if member.get("systemRole") == "admin"
                        else "employee"
                    ),
                    "accountStatus": (
                        "active" if member.get("status") == "active" else "disabled"
                    ),
                    "membershipStatus": "approved",
                    "departmentId": department_id,
                    "departmentName": department_name,
                    "isDepartmentLead": is_lead,
                    "visibilityScope": member.get("visibilityScope") or "self",
                    "managementTitleId": title_id,
                    "managementTitleName": title_name,
                    "createdAt": _now(),
                    "lastLoginAt": _now() if membership_id == current.get("id") else None,
                }
            )
        return result

    def org_membership(self) -> dict[str, Any]:
        session = self._session()
        organization = session.get("organization") or {}
        membership = self.auth_state().get("user") or {}
        return {
            "hasOrganization": bool(organization),
            "organizationId": organization.get("organizationId"),
            "organizationName": organization.get("name"),
            "departmentId": membership.get("departmentId"),
            "departmentName": membership.get("departmentName"),
            "membershipStatus": "approved" if organization else None,
            "organizationWorkspaceClientId": None,
            "organizationInternalProjectId": None,
        }

    def topics(self) -> dict[str, Any]:
        snapshot = self._snapshot()
        candidates = []
        for item in snapshot.get("intelligence") or []:
            candidates.append(
                {
                    "id": item.get("intelligenceId")
                    or item.get("recordId")
                    or new_id(),
                    "radarId": "strict-intelligence",
                    "title": item.get("title") or "情报记录",
                    "summary": item.get("summary")
                    or item.get("content")
                    or item.get("rawText")
                    or "",
                    "source": item.get("sourceName") or item.get("sourceType") or "组织资料",
                    "sourceUrl": item.get("sourceUrl"),
                    "publishedAt": item.get("publishedAt"),
                    "captureMethod": "strict_v2",
                    "status": item.get("lifecycleState") or "inbox",
                    "insightStatus": "ready",
                    "clientId": item.get("projectId"),
                    "createdAt": item.get("updatedAt") or _now(),
                }
            )
        return {
            "radars": [
                {
                    "id": "strict-intelligence",
                    "title": "组织情报",
                    "prompt": "",
                    "timeRange": "all",
                    "preferredSources": [],
                    "createdAt": _now(),
                }
            ],
            "candidates": candidates,
            "intelligenceProfiles": [],
        }

    def handbook(self) -> dict[str, Any]:
        entries = []
        for item in self._snapshot().get("experienceQuotes") or []:
            entries.append(
                {
                    "id": item.get("experienceQuoteId"),
                    "title": item.get("category") or "经验记录",
                    "summary": item.get("quoteText") or "",
                    "tags": [item.get("category")] if item.get("category") else [],
                    "sourceType": item.get("sourceType") or "strict_v2",
                    "authorUserId": item.get("authorMembershipId"),
                    "sourceObjectId": item.get("sourceId"),
                    "sourceTitle": item.get("sourceExcerpt"),
                    "abilityKeys": [],
                    "evidenceRefs": [],
                    "contextSummary": item.get("sourceExcerpt") or "",
                    "reuseCount": 0,
                    "linkedContexts": [],
                    "createdAt": item.get("updatedAt") or _now(),
                }
            )
        return {"entries": entries}

    def review_dashboard(self, query: Mapping[str, str]) -> dict[str, Any]:
        reviews = self._snapshot().get("weeklyReviews") or []
        requested = _string(query.get("weekLabel"))
        current = next(
            (item for item in reviews if item.get("weekLabel") == requested),
            reviews[0] if reviews else None,
        )
        week = requested or (current or {}).get("weekLabel") or ""
        return {
            "weekLabel": week,
            "resolvedWeekLabel": week,
            "currentReview": (
                {
                    "id": current.get("weeklyReviewId"),
                    "userId": current.get("membershipId"),
                    "weekLabel": current.get("weekLabel"),
                    "workProgress": current.get("workProgress") or "",
                    "workBlocker": current.get("workBlocker") or "",
                    "workDirection": current.get("workDirection") or "",
                    "workFreeNote": current.get("workFreeNote") or "",
                    "personalGrowthNote": current.get("personalGrowthNote") or "",
                    "supportNeeded": current.get("supportNeeded") or "",
                    "nextWeekFocus": current.get("nextWeekFocus") or "",
                    "createdAt": current.get("updatedAt") or _now(),
                    "updatedAt": current.get("updatedAt") or _now(),
                }
                if current
                else None
            ),
            "workItems": [],
            "personalItems": [],
            "availablePerspectives": [
                {"key": "mine", "label": "我的视角"},
                {"key": "department", "label": "部门视角"},
            ],
            "activePerspective": query.get("perspective") or "mine",
            "departmentReports": [],
            "agentDepartmentDigests": [],
            "agentDepartmentPlans": [],
            "plans": [],
        }

    def task_settings(self) -> dict[str, Any]:
        return {
            "defaultListId": "strict-default",
            "defaultPriority": "normal",
            "defaultDueDatePreset": "today",
            "defaultViewMode": "calendar",
            "listSortMode": "dueDate",
            "showCompletedTasks": True,
            "defaultReviewScope": "work",
            "autoAssignSelf": True,
            "updatedAt": _now(),
        }

    def maintenance_mode(self, *, active: bool | None = None) -> dict[str, Any]:
        auth = self.auth_state()
        user = auth.get("user") or {}
        organization_id = _string(user.get("organizationId"))
        user_id = _string(user.get("id"))
        is_official_workspace = organization_id == YIYU_OFFICIAL_ORGANIZATION_ID
        can_manage_permissions = user.get("primaryRole") == "admin"
        can_enter = bool(organization_id and user_id and is_official_workspace)
        state_path = self.runtime.database_path.parent / "strict-maintenance-mode.json"
        saved: dict[str, Any] = {}
        try:
            saved = json.loads(state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            saved = {}
        if active is not None:
            if not organization_id or not user_id:
                raise LocalRuntimeError(
                    401,
                    "maintenance_login_required",
                    "请先登录组织，再打开协作同步。",
                )
            if not is_official_workspace:
                raise LocalRuntimeError(
                    403,
                    "maintenance_official_workspace_required",
                    "请切换到益语智库工作空间后再打开协作同步。",
                )
            saved = {
                "formatVersion": 1,
                "active": active,
                "organizationId": organization_id,
                "userId": user_id,
                "updatedAt": _now(),
            }
            state_path.write_text(
                f"{json.dumps(saved, ensure_ascii=False, indent=2)}\n",
                encoding="utf-8",
            )
            try:
                state_path.chmod(0o600)
            except OSError:
                pass
        is_active = bool(
            saved.get("active")
            and saved.get("organizationId") == organization_id
            and saved.get("userId") == user_id
        )
        return {
            "available": bool(organization_id and user_id),
            "active": is_active,
            "canEnter": can_enter,
            "canManagePermissions": can_manage_permissions,
            "organizationId": organization_id or None,
            "userId": user_id or None,
            "reason": (
                None
                if can_enter
                else (
                    "请切换到益语智库工作空间。"
                    if organization_id and user_id
                    else "请先登录组织。"
                )
            ),
        }

    def _not_connected(self, path: str) -> None:
        raise LocalRuntimeError(
            501,
            "capability_not_connected",
            f"该操作界面已保留，严格新版数据链路尚待接通：{path}",
        )

    def dispatch(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str],
        body: Mapping[str, Any],
        idempotency_key: str,
        workspace_context: Any | None = None,
        expected_sandbox_id: str | None = None,
        request_seq: int | None = None,
    ) -> Any:
        path = path.strip("/")
        request = UiRequest(
            method=method,
            path=path,
            query=query,
            body=body,
            idempotency_key=idempotency_key,
            expected_sandbox_id=expected_sandbox_id,
            request_seq=request_seq,
        )
        if workspace_context is not None:
            with self.runtime.prebound_sandbox_context(workspace_context):
                return self._dispatch_request(request)
        return self._dispatch_request(request)

    def capture_dispatch_workspace(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, str],
        body: Mapping[str, Any],
        idempotency_key: str,
        expected_sandbox_id: str | None = None,
        request_seq: int | None = None,
    ) -> Any | None:
        request = UiRequest(
            method=method,
            path=path.strip("/"),
            query=query,
            body=body,
            idempotency_key=idempotency_key,
            expected_sandbox_id=expected_sandbox_id,
            request_seq=request_seq,
        )
        workspace_neutral = (
            request.path in {"system/health", "workspaces", "workspaces/current"}
            or bool(re.fullmatch(r"workspaces/[^/]+/activate", request.path))
            or request.path in {
                "auth/login",
                "auth/logout",
                "auth/join",
                "auth/create-organization",
                "auth/select-organization",
            }
        )
        if workspace_neutral and not self.domain_registry.requires_workspace_pin(request):
            return None
        return self.runtime.capture_sandbox_context(
            expected_sandbox_id=expected_sandbox_id,
            request_seq=request_seq,
        )

    def _dispatch_request(self, request: UiRequest) -> Any:
        method = request.method
        path = request.path
        query = request.query
        body = request.body
        domain_result = self.domain_registry.dispatch(self, request)
        if domain_result is not NOT_HANDLED:
            return domain_result
        if method == "GET" and path == "system/health":
            return self.health()
        if method == "GET" and path == "auth/me":
            return self.auth_state()
        if method == "GET" and path == "settings":
            return self.settings()
        if method == "GET" and path == "workspaces":
            return self.workspaces()
        if method == "GET" and path == "workspaces/current":
            return self.current_workspace()
        if method == "GET" and path == "settings/org-ai-runtime":
            return self.ai_runtime()
        if method == "POST" and path == "settings/org-ai-runtime/sync":
            if bool(body.get("automatic")):
                return self.ai_runtime()
            self.runtime.sync_ai()
            return self.ai_runtime()
        if method == "GET" and path == "maintenance-mode/status":
            return self.maintenance_mode()
        if method == "POST" and path == "maintenance-mode/enter":
            return self.maintenance_mode(active=True)
        if method == "POST" and path == "maintenance-mode/exit":
            return self.maintenance_mode(active=False)
        if method == "GET" and path == "me/org-membership":
            return {
                **self.org_membership(),
                "applicationState": "none",
                "applicationMessage": None,
            }
        if method == "GET" and path == "me/org-membership/admin-claim-status":
            user = self.auth_state().get("user") or {}
            return {
                "canClaim": False,
                "claimed": user.get("primaryRole") == "admin",
                "message": "",
            }
        if method == "GET" and path == "auth/department-options" and not query.get("inviteCode"):
            return [
                {
                    "id": item.get("departmentId"),
                    "name": item.get("name") or "未命名部门",
                    "color": item.get("color") or "#5B7CFA",
                }
                for item in self._session().get("departments") or []
                if isinstance(item, Mapping) and item.get("departmentId")
            ]
        if method == "GET" and path == "org-integrations/feishu":
            return self.feishu_integration()
        if method == "GET" and path == "me/feishu-delivery-profile":
            return self.feishu_delivery_profile()
        if method == "GET" and path == "me/feishu-authorization":
            return self.feishu_member_authorization()
        if method == "GET" and path == "admin/employees":
            return self.employees()
        if method == "GET" and path == "employees/mention-candidates":
            term = _string(query.get("q")).lower()
            current_id = _string((self.auth_state().get("user") or {}).get("id"))
            return [
                {
                    "id": item["id"],
                    "fullName": item["fullName"],
                    "email": item.get("email") or "",
                    "primaryRole": item["primaryRole"],
                    "isSelf": item["id"] == current_id,
                }
                for item in self.employees()
                if not term or term in _string(item.get("fullName")).lower()
            ]
        if method == "GET" and path == "clients":
            return self.clients()
        match = re.fullmatch(r"clients/([^/]+)/workspace", path)
        if method == "GET" and match:
            return self.client_workspace(match.group(1))
        match = re.fullmatch(r"clients/([^/]+)/knowledge-context", path)
        if method == "GET" and match:
            return self.runtime.project_knowledge_context(match.group(1))
        if method == "GET" and path == "tasks":
            return self.task_board()
        if method == "GET" and path == "event-lines":
            return self.event_lines()
        match = re.fullmatch(r"event-lines/([^/]+)", path)
        if method == "GET" and match:
            return self.event_line_detail(match.group(1))
        if method == "GET" and path == "topics":
            return self.topics()
        if method == "GET" and path == "handbook":
            return self.handbook()
        if method == "GET" and path == "reviews":
            return self.review_dashboard(query)
        if method == "GET" and path == "reviews/history":
            return {
                "items": [
                    {
                        "weekLabel": item.get("weekLabel"),
                        "submittedAt": item.get("updatedAt") or _now(),
                        "workItemCount": 0,
                        "personalItemCount": 0,
                    }
                    for item in self._snapshot().get("weeklyReviews") or []
                ]
            }
        if method == "GET" and path == "settings/tasks":
            return self.task_settings()
        if method == "GET" and path == "settings/client-workspace":
            return {
                "meetingPublishDefaultListId": "strict-default",
                "meetingPublishDefaultPriority": "normal",
                "defaultGoalQuarter": "",
                "defaultMeetingTitlePrefix": "",
                "clientDnaModeLabel": "",
                "updatedAt": _now(),
            }
        if method == "GET" and path == "settings/topics":
            return {
                "chineseOnly": True,
                "requireInsightBeforeActions": True,
                "defaultTaskOwnerMode": "self",
                "defaultTimeRange": "30d",
                "defaultSourceStrategy": "all",
                "updatedAt": _now(),
            }
        if method == "GET" and path == "settings/handbook":
            return {
                "defaultTags": [],
                "defaultCategory": "工作经验",
                "allowTaskSource": True,
                "allowAnalysisSource": True,
                "visibilityBoundary": "organization",
                "updatedAt": _now(),
            }
        if method == "GET" and path == "settings/system-admin":
            return {
                "allowBusinessSettingsForEmployees": False,
                "allowOrgDnaForEmployees": False,
                "protectEmployeeAdmin": True,
                "protectAiAndCloud": True,
                "protectCloudSecurity": True,
                "updatedAt": _now(),
            }
        if method == "GET" and path == "local-input-memory":
            store = LocalInputMemoryStore(self.runtime.secret_store)
            return {
                **store.read(),
                "authorityState": "ready",
                "authorityMessage": "",
                "publicPreferencePersisted": False,
                "secretPersistedLocally": True,
                "retryable": False,
            }
        if method == "POST" and path == "local-input-memory/cloud-auth":
            store = LocalInputMemoryStore(self.runtime.secret_store)
            public = store.cloud_auth_public(store.cached_public(), body)
            store.apply_cloud_auth_secret(body)
            store.cache_public(public)
            store.cache_device_cloud_auth(public)
            return {
                **store.read(public),
                "authorityState": "ready",
                "authorityMessage": "",
                "publicPreferencePersisted": False,
                "secretPersistedLocally": True,
                "retryable": False,
            }
        if method == "POST" and path == "auth/login":
            self.runtime.login(
                cloud_api_url=_string(body.get("cloudApiUrl")),
                identifier=_string(body.get("identifier") or body.get("email")),
                password=_string(body.get("password")),
                idempotency_key=request.idempotency_key,
            )
            return self.auth_state()
        if method == "POST" and path == "auth/logout":
            self.runtime.logout(idempotency_key=request.idempotency_key)
            return self.auth_state()
        match = re.fullmatch(r"workspaces/([^/]+)/activate", path)
        if method == "POST" and match:
            self.runtime.switch(
                match.group(1),
                idempotency_key=request.idempotency_key,
                request_seq=request.request_seq,
            )
            return self.workspaces()
        if method == "POST" and path == "workspaces/current/repair-task-mirrors":
            current = self._current()
            return {
                "sandboxId": (current.get("sandbox") or {}).get("sandboxId"),
                "buildFingerprint": "strict-v2",
                "status": "skipped",
                "ran": False,
                "scannedCount": 0,
                "removedCount": 0,
                "preservedCount": 0,
                "reason": "严格新版投影不需要旧镜像清理",
            }
        if method == "POST" and path == "tasks":
            result = self.runtime.task_command(
                "create",
                task_id=None,
                payload={
                    "title": body.get("title"),
                    "description": body.get("desc") or "",
                    "projectId": body.get("clientId"),
                    "ownerMembershipId": body.get("ownerId"),
                    "collaboratorMembershipIds": body.get("collaboratorIds") or [],
                    "priority": body.get("priority") or "normal",
                    "visibilityScope": (
                        "self"
                        if body.get("scopeMode") == "PERSONAL_ONLY"
                        else "participants"
                    ),
                    "startDate": body.get("startDate"),
                    "dueDate": body.get("dueDate") or body.get("ddl"),
                    "scheduledStartAt": body.get("scheduledStartAt"),
                    "scheduledEndAt": body.get("scheduledEndAt"),
                    "deadlineAt": body.get("deadlineAt"),
                    "durationMinutes": body.get("durationMinutes") or 60,
                },
                idempotency_key=idempotency_key,
            )
            task_id = _string((result.get("task") or {}).get("taskId"))
            return next(
                item for item in self.task_board()["tasks"] if item["id"] == task_id
            )
        match = re.fullmatch(r"tasks/([^/]+)", path)
        if method == "PATCH" and match:
            task_id = match.group(1)
            current_task = self.runtime.task_detail(task_id).get("task") or {}
            requested_status = _string(body.get("status") or body.get("progressStatus"))
            current_lifecycle = _string(current_task.get("lifecycleState"))
            if requested_status == "done" and current_lifecycle != "completed":
                self.runtime.task_command(
                    "complete",
                    task_id=task_id,
                    payload={
                        "expectedVersion": current_task.get("version") or 1,
                        "completionNote": "",
                    },
                    idempotency_key=idempotency_key,
                )
            elif requested_status in {"todo", "doing"} and current_lifecycle == "completed":
                self.runtime.task_command(
                    "restore",
                    task_id=task_id,
                    payload={
                        "expectedVersion": current_task.get("version") or 1,
                        "completionNote": "",
                    },
                    idempotency_key=idempotency_key,
                )
            else:
                self.runtime.task_command(
                    "update",
                    task_id=task_id,
                    payload={
                        "expectedVersion": current_task.get("version") or 1,
                        **(
                            {"title": body.get("title")}
                            if "title" in body
                            else {}
                        ),
                        **(
                            {"description": body.get("desc")}
                            if "desc" in body
                            else {}
                        ),
                        **(
                            {"projectId": body.get("clientId")}
                            if "clientId" in body
                            else {}
                        ),
                        **(
                            {"ownerMembershipId": body.get("ownerId")}
                            if "ownerId" in body
                            else {}
                        ),
                        **(
                            {
                                "collaboratorMembershipIds": body.get(
                                    "collaboratorIds"
                                )
                                or []
                            }
                            if "collaboratorIds" in body
                            else {}
                        ),
                        **(
                            {"priority": body.get("priority")}
                            if "priority" in body
                            else {}
                        ),
                        **(
                            {"startDate": body.get("startDate")}
                            if "startDate" in body
                            else {}
                        ),
                        **(
                            {"dueDate": body.get("dueDate") or body.get("ddl")}
                            if "dueDate" in body or "ddl" in body
                            else {}
                        ),
                        **(
                            {"scheduledStartAt": body.get("scheduledStartAt")}
                            if "scheduledStartAt" in body
                            else {}
                        ),
                        **(
                            {"scheduledEndAt": body.get("scheduledEndAt")}
                            if "scheduledEndAt" in body
                            else {}
                        ),
                        **(
                            {"deadlineAt": body.get("deadlineAt")}
                            if "deadlineAt" in body
                            else {}
                        ),
                        **(
                            {"durationMinutes": body.get("durationMinutes")}
                            if "durationMinutes" in body
                            else {}
                        ),
                    },
                    idempotency_key=idempotency_key,
                )
            return next(
                item for item in self.task_board()["tasks"] if item["id"] == task_id
            )
        match = re.fullmatch(r"tasks/([^/]+)/complete-with-review", path)
        if method == "POST" and match:
            task_id = match.group(1)
            current_task = self.runtime.task_detail(task_id).get("task") or {}
            self.runtime.task_command(
                "complete",
                task_id=task_id,
                payload={
                    "expectedVersion": current_task.get("version") or 1,
                    "completionNote": body.get("reviewNote") or "",
                },
                idempotency_key=idempotency_key,
            )
            return next(
                item for item in self.task_board()["tasks"] if item["id"] == task_id
            )
        if method == "POST" and path == "event-lines":
            result = self.runtime.create_event_line(
                payload={
                    "projectId": body.get("primaryClientId"),
                    "name": body.get("name"),
                    "goal": body.get("intent") or "",
                    "background": body.get("summary") or "",
                    "participantMembershipIds": body.get("participantIds") or [],
                },
                idempotency_key=idempotency_key,
            )
            event_line_id = _string(
                (result.get("eventLine") or {}).get("eventLineId")
            )
            return next(
                item for item in self.event_lines() if item["id"] == event_line_id
            )
        match = re.fullmatch(r"clients/([^/]+)/workspace/chat/start", path)
        if method == "POST" and match:
            project_id = match.group(1)
            prompt = _string(body.get("prompt"))
            mode = (
                "creative"
                if body.get("creativityMode") == "creative"
                else "balanced"
            )
            saved = self.runtime.workbench_chat(
                project_id=project_id,
                question=prompt,
                mode=mode,
            )
            answer = saved.get("answer") or {}
            now = answer.get("createdAt") or _now()
            thread_id = _string(body.get("threadId")) or new_id()
            user_message_id = new_id()
            assistant_message_id = _string(answer.get("answerId")) or new_id()
            user_message = {
                "id": user_message_id,
                "threadId": thread_id,
                "role": "user",
                "content": prompt,
                "createdAt": now,
                "status": "success",
                "evidence": [],
            }
            assistant_message = {
                "id": assistant_message_id,
                "threadId": thread_id,
                "role": "assistant",
                "content": answer.get("answerMarkdown") or "",
                "createdAt": now,
                "status": "success",
                "modelRoute": answer.get("modelName"),
                "llmInvoked": True,
                "providerUsed": "organization_direct",
                "answerMode": "general_answer",
                "evidenceStatus": "none",
                "evidence": [],
                "creativityMode": body.get("creativityMode") or "balanced",
            }
            return {
                "threadId": thread_id,
                "userMessage": user_message,
                "assistantMessage": assistant_message,
                "analysisRun": {
                    "id": new_id(),
                    "clientId": project_id,
                    "threadId": thread_id,
                    "userMessageId": user_message_id,
                    "assistantMessageId": assistant_message_id,
                    "question": prompt,
                    "status": "completed",
                    "phase": "completed",
                    "progress": 100,
                    "progressFloor": 100,
                    "progressCeiling": 100,
                    "elapsedMs": 0,
                    "evidenceSummary": {
                        "summaryText": "",
                        "masterHitCount": 0,
                        "surrogateHitCount": 0,
                        "rawChunkHitCount": 0,
                        "drillthroughUsed": False,
                        "coveredCategories": [],
                        "missingCategories": [],
                        "evidenceList": [],
                    },
                    "longAnswerStatus": "ready",
                    "summaryStatus": "ready",
                    "longAnswer": assistant_message["content"],
                    "answerMode": "general_answer",
                    "llmInvoked": True,
                    "providerUsed": "organization_direct",
                    "timing": {},
                    "assistantMessage": assistant_message,
                    "createdAt": now,
                    "updatedAt": now,
                },
            }
        self._not_connected(path)
