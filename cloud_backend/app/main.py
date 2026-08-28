import hmac
import os
from contextlib import asynccontextmanager
from typing import Annotated, Any

import uvicorn
from fastapi import Body, Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from strict_common.contracts import BUSINESS_CAPABILITIES, CONNECTED_CAPABILITIES
from strict_common.ids import new_id

from .config import CloudConfig
from .domain_routes import register_domain_routes
from .models import (
    AiAnswerSaveRequest,
    BootstrapOrganizationRequest,
    EventLineCreateRequest,
    InviteRequest,
    JoinOrganizationRequest,
    LoginRequest,
    NamedStructureRequest,
    OrganizationAiConfigRequest,
    RefreshRequest,
    TaskCreateRequest,
    TaskInboxHandleRequest,
    TaskTransitionRequest,
    TaskUpdateRequest,
)
from .repository import CloudRepository, RepositoryError, SessionIdentity


bearer = HTTPBearer(auto_error=False)


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def create_app(config: CloudConfig | None = None) -> FastAPI:
    resolved = config or CloudConfig.load()
    resolved.data_dir.mkdir(parents=True, exist_ok=True)
    repository = CloudRepository(
        resolved.database_path,
        cloud_instance_id=resolved.cloud_instance_id,
        master_key=resolved.master_key,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        repository.handshake()
        yield

    app = FastAPI(
        title="益语智库AI（新版）严格云端",
        version="2.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.repository = repository
    app.state.config = resolved

    @app.middleware("http")
    async def request_identity(request: Request, call_next):
        request_id = request.headers.get("X-Request-Id", "").strip() or new_id()
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Yiyu-Api-Version"] = "v2"
        return response

    @app.exception_handler(RepositoryError)
    async def repository_error(_: Request, exc: RepositoryError):
        return _error(exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        message = str(first.get("msg") or "请求内容不完整")
        return _error(422, "validation_error", message)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            code = str(detail.get("code") or "http_error")
            message = str(detail.get("message") or "请求失败")
        else:
            code = "http_error"
            message = str(detail)
        return _error(exc.status_code, code, message)

    def current_identity(
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Depends(bearer),
        ],
    ) -> SessionIdentity:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise RepositoryError(401, "authorization_required", "请先登录组织")
        return repository.session_from_access(credentials.credentials)

    @app.get("/api/v2/health")
    def health() -> dict[str, Any]:
        handshake = repository.handshake()
        return {
            "status": "ready",
            "service": handshake["service"],
            "apiVersion": handshake["apiVersion"],
            "schemaFamily": handshake["schemaFamily"],
            "databaseGenerationId": handshake["databaseGenerationId"],
        }

    @app.get("/api/v2/handshake")
    def handshake() -> dict[str, Any]:
        return repository.handshake()

    @app.get("/api/v2/capabilities")
    def capabilities() -> dict[str, Any]:
        handshake_payload = repository.handshake()
        return {
            "apiVersion": "v2",
            "cloudInstanceId": repository.cloud_instance_id,
            "connected": handshake_payload["capabilities"],
            "notConnected": sorted(BUSINESS_CAPABILITIES - CONNECTED_CAPABILITIES),
        }

    @app.get("/api/v2/capabilities/{capability_id:path}")
    def capability(capability_id: str):
        if capability_id in repository.handshake()["capabilities"]:
            return {"id": capability_id, "state": "connected"}
        return _error(
            501,
            "capability_not_connected",
            "该功能尚未接入严格新版数据合同",
        )

    @app.post("/api/v2/auth/bootstrap-organization")
    def bootstrap_organization(
        payload: BootstrapOrganizationRequest,
    ) -> dict[str, Any]:
        if not hmac.compare_digest(
            payload.bootstrap_token,
            resolved.bootstrap_token,
        ):
            raise RepositoryError(403, "bootstrap_denied", "组织创建授权无效")
        raise RepositoryError(
            501,
            "organization_bootstrap_not_connected",
            "严格新版尚未接通创建组织；请登录已有组织",
        )

    @app.post("/api/v2/auth/login")
    def login(
        payload: LoginRequest,
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return repository.login(
            identifier=payload.identifier,
            password=payload.password,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/auth/join", status_code=status.HTTP_201_CREATED)
    def join(payload: JoinOrganizationRequest) -> dict[str, Any]:
        return repository.join(
            invite_code=payload.invite_code,
            display_name=payload.display_name,
            email=payload.email,
            phone=payload.phone,
            password=payload.password,
        )

    @app.post("/api/v2/auth/refresh")
    def refresh(
        payload: RefreshRequest,
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return repository.refresh(
            payload.refresh_token,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(
        identity: Annotated[SessionIdentity, Depends(current_identity)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> None:
        repository.logout(identity, idempotency_key=idempotency_key or new_id())

    @app.get("/api/v2/session/current")
    def current_session(
        identity: Annotated[SessionIdentity, Depends(current_identity)],
    ) -> dict[str, Any]:
        return {
            "cloudInstanceId": identity.cloud_instance_id,
            "organizationId": identity.organization_id,
            "principalId": identity.principal_id,
            "membershipId": identity.membership_id,
            "sessionSnapshot": repository.organization_snapshot(identity),
        }

    @app.get("/api/v2/organization/snapshot")
    def organization_snapshot(
        identity: Annotated[SessionIdentity, Depends(current_identity)],
    ) -> dict[str, Any]:
        return repository.organization_snapshot(identity)

    @app.get("/api/v2/business/snapshot")
    def business_snapshot(
        identity: Annotated[SessionIdentity, Depends(current_identity)],
    ) -> dict[str, Any]:
        return repository.business_snapshot(identity)

    @app.get("/api/v2/projects/{project_id}/knowledge-context")
    def project_knowledge_context(
        project_id: str,
        identity: Annotated[SessionIdentity, Depends(current_identity)],
    ) -> dict[str, Any]:
        return repository.project_knowledge_context(
            identity,
            project_id=project_id,
        )

    @app.post("/api/v2/event-lines", status_code=status.HTTP_201_CREATED)
    def create_event_line(
        payload: EventLineCreateRequest,
        identity: Annotated[SessionIdentity, Depends(current_identity)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return repository.create_event_line(
            identity,
            payload=payload.model_dump(by_alias=True),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/tasks/{task_id}")
    def task_detail(
        task_id: str,
        identity: Annotated[SessionIdentity, Depends(current_identity)],
    ) -> dict[str, Any]:
        return repository.task_detail(identity, task_id=task_id)

    @app.post("/api/v2/tasks", status_code=status.HTTP_201_CREATED)
    def create_task(
        payload: TaskCreateRequest,
        identity: Annotated[SessionIdentity, Depends(current_identity)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return repository.create_task(
            identity,
            payload=payload.model_dump(by_alias=True),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.patch("/api/v2/tasks/{task_id}")
    def update_task(
        task_id: str,
        payload: TaskUpdateRequest,
        identity: Annotated[SessionIdentity, Depends(current_identity)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return repository.update_task(
            identity,
            task_id=task_id,
            payload=payload.model_dump(by_alias=True, exclude_unset=True),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/tasks/{task_id}/complete")
    def complete_task(
        task_id: str,
        payload: TaskTransitionRequest,
        identity: Annotated[SessionIdentity, Depends(current_identity)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return repository.update_task(
            identity,
            task_id=task_id,
            payload=payload.model_dump(by_alias=True),
            idempotency_key=idempotency_key or new_id(),
            transition="completed",
        )

    @app.post("/api/v2/tasks/{task_id}/inbox/handle")
    def handle_task_inbox(
        task_id: str,
        payload: TaskInboxHandleRequest,
        identity: Annotated[SessionIdentity, Depends(current_identity)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return repository.handle_task_inbox(
            identity,
            task_id=task_id,
            expected_version=payload.expected_version,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/tasks/{task_id}/restore")
    def restore_task(
        task_id: str,
        payload: TaskTransitionRequest,
        identity: Annotated[SessionIdentity, Depends(current_identity)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return repository.update_task(
            identity,
            task_id=task_id,
            payload=payload.model_dump(by_alias=True),
            idempotency_key=idempotency_key or new_id(),
            transition="restored",
        )

    @app.post("/api/v2/workbench/answers", status_code=status.HTTP_201_CREATED)
    def save_workbench_answer(
        payload: AiAnswerSaveRequest,
        identity: Annotated[SessionIdentity, Depends(current_identity)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return repository.save_ai_answer(
            identity,
            payload=payload.model_dump(by_alias=True),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post(
        "/api/v2/workbench/answers/{answer_id}/facts/corrections",
        status_code=status.HTTP_201_CREATED,
    )
    def correct_workbench_answer_fact(
        answer_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(current_identity)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return repository.correct_ai_answer_fact(
            identity,
            answer_id=answer_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/authorization/current")
    def current_authorization(
        identity: Annotated[SessionIdentity, Depends(current_identity)],
    ) -> dict[str, Any]:
        return repository.current_authorization(identity)

    @app.post("/api/v2/organization/departments", status_code=status.HTTP_201_CREATED)
    def create_department(
        payload: NamedStructureRequest,
        identity: Annotated[SessionIdentity, Depends(current_identity)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return repository.create_department(
            identity,
            name=payload.name,
            expected_organization_version=payload.expected_organization_version,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post(
        "/api/v2/organization/management-titles",
        status_code=status.HTTP_201_CREATED,
    )
    def create_management_title(
        payload: NamedStructureRequest,
        identity: Annotated[SessionIdentity, Depends(current_identity)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return repository.create_management_title(
            identity,
            name=payload.name,
            expected_organization_version=payload.expected_organization_version,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/organization/invites", status_code=status.HTTP_201_CREATED)
    def create_invite(
        payload: InviteRequest,
        identity: Annotated[SessionIdentity, Depends(current_identity)],
    ) -> dict[str, Any]:
        return repository.create_invite(
            identity,
            invite_kind=payload.invite_kind,
            target_id=payload.target_id,
            expires_at=payload.expires_at,
        )

    @app.get("/api/v2/settings/org-ai-config")
    def ai_config(
        identity: Annotated[SessionIdentity, Depends(current_identity)],
    ) -> dict[str, Any]:
        return repository.ai_config(identity, include_secret=False)

    @app.put("/api/v2/settings/org-ai-config")
    def save_ai_config(
        payload: OrganizationAiConfigRequest,
        identity: Annotated[SessionIdentity, Depends(current_identity)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return repository.save_ai_config(
            identity,
            provider=payload.provider,
            base_url=payload.base_url,
            model_name=payload.model_name,
            api_key=payload.api_key,
            expected_version=payload.expected_version,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/settings/org-ai-config/runtime-secret")
    def ai_runtime_secret(
        identity: Annotated[SessionIdentity, Depends(current_identity)],
    ) -> dict[str, Any]:
        result = repository.ai_config(identity, include_secret=True)
        if result["status"] != "ready":
            raise RepositoryError(
                409,
                "organization_ai_not_ready",
                "组织尚未配置统一大模型",
            )
        return {
            **result,
            "organizationId": identity.organization_id,
            "cloudInstanceId": identity.cloud_instance_id,
            "source": "organization_direct",
        }

    register_domain_routes(app, repository, current_identity)

    return app


if __name__ == "__main__":
    runtime_config = CloudConfig.load()
    uvicorn.run(
        create_app(runtime_config),
        host=os.environ.get("YIYU_STRICT_CLOUD_HOST", "127.0.0.1"),
        port=int(os.environ.get("YIYU_STRICT_CLOUD_PORT", "47930")),
        log_level="info",
    )
