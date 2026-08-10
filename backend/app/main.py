import asyncio
import os
from contextlib import asynccontextmanager
from contextlib import suppress
from typing import Annotated, Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from strict_common.contracts import BUSINESS_CAPABILITIES, CONNECTED_CAPABILITIES
from strict_common.ids import new_id

from .bounded_dispatch import (
    BoundedDispatch,
    DispatchBusyError,
    DispatchTimeoutError,
)
from .cloud_client import CloudClientError
from .config import LocalConfig
from .local_ai_scheduler import LocalAiScheduler
from .models import (
    CreateOrganizationRequest,
    EventLineCreateRequest,
    InviteRequest,
    JoinRequest,
    LoginRequest,
    NamedStructureRequest,
    OrganizationAiConfigRequest,
    SwitchWorkspaceRequest,
    TaskCreateRequest,
    TaskInboxHandleRequest,
    TaskTransitionRequest,
    TaskUpdateRequest,
    WorkbenchChatRequest,
)
from .runtime import LocalRuntimeError, WorkspaceRuntime
from .secret_store import SecretStoreError, build_secret_store
from .ui_compat import StrictUiCompatibility


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def _request_sequence(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        sequence = int(value)
    except ValueError as exc:
        raise LocalRuntimeError(422, "request_sequence_invalid", "请求序号格式不正确") from exc
    if sequence <= 0:
        raise LocalRuntimeError(422, "request_sequence_invalid", "请求序号必须为正数")
    return sequence


def create_app(config: LocalConfig | None = None) -> FastAPI:
    resolved = config or LocalConfig.load()
    resolved.data_dir.mkdir(parents=True, exist_ok=True)
    runtime = WorkspaceRuntime(
        resolved.database_path,
        build_secret_store(
            data_dir=resolved.data_dir,
            namespace=resolved.secret_namespace,
            test_mode=resolved.test_mode,
        ),
    )
    ui_compat = StrictUiCompatibility(runtime)
    ui_dispatch = BoundedDispatch(ui_compat.dispatch)
    background_ui_dispatch = BoundedDispatch(
        ui_compat.dispatch,
        max_workers=1,
        max_queued=1,
        deadline_seconds=120.0,
    )
    # 工作台问答和智能编辑都可能等待组织模型；若仍放在普通20秒UI通道，
    # 会出现“模型稍后成功，但界面先报504”的假失败。独立交互通道
    # 保持并发有界，也不会占用健康检查和普通页面请求的工作池。
    interactive_ai_dispatch = BoundedDispatch(
        ui_compat.dispatch,
        max_workers=2,
        max_queued=2,
        deadline_seconds=120.0,
    )
    # 官网研究是“按进展结算”的长任务：它要先盘点站点，再按目标分批
    # 抽取。独立线程池避免它占住普通交互或档案重算通道；较长截止时间
    # 只是死锁保险丝，真正的停止条件由研究器的“目标完成/无新进展”决定。
    research_ui_dispatch = BoundedDispatch(
        ui_compat.dispatch,
        max_workers=1,
        max_queued=1,
        deadline_seconds=900.0,
    )
    # 项目报告通常需要连续生成多个章节。它是有明确终态的交互长任务，
    # 使用独立有界通道，避免占用问答/智能编辑和普通页面请求。
    report_ui_dispatch = BoundedDispatch(
        ui_compat.dispatch,
        max_workers=1,
        max_queued=1,
        deadline_seconds=300.0,
    )
    local_ai_scheduler = LocalAiScheduler(ui_compat.dispatch)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        ui_dispatch.start()
        background_ui_dispatch.start()
        interactive_ai_dispatch.start()
        research_ui_dispatch.start()
        report_ui_dispatch.start()
        runtime.restore_at_startup()
        try:
            yield
        finally:
            ui_dispatch.close()
            background_ui_dispatch.close()
            interactive_ai_dispatch.close()
            research_ui_dispatch.close()
            report_ui_dispatch.close()
            runtime.close()

    app = FastAPI(
        title="益语智库AI（新版）严格本地后端",
        version="2.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    app.state.ui_compat = ui_compat
    app.state.ui_dispatch = ui_dispatch
    app.state.background_ui_dispatch = background_ui_dispatch
    app.state.interactive_ai_dispatch = interactive_ai_dispatch
    app.state.research_ui_dispatch = research_ui_dispatch
    app.state.report_ui_dispatch = report_ui_dispatch
    app.state.config = resolved
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:4188",
            "http://localhost:4188",
            "null",
        ],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "X-Yiyu-Desktop-Token",
            "X-Request-Id",
            "Idempotency-Key",
            "X-Yiyu-Sandbox-Id",
            "X-Yiyu-Request-Seq",
            "X-Yiyu-Client-Mutation-Id",
            "X-Yiyu-Client-Mutation-Session",
            "X-Yiyu-Client-Mutation-Order",
        ],
    )

    @app.middleware("http")
    async def request_identity(request: Request, call_next):
        request_id = request.headers.get("X-Request-Id", "").strip() or new_id()
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Yiyu-Api-Version"] = "v2"
        request_seq = request.headers.get("X-Yiyu-Request-Seq", "").strip()
        if request_seq:
            response.headers["X-Yiyu-Request-Seq"] = request_seq
        return response

    @app.exception_handler(LocalRuntimeError)
    async def runtime_error(_: Request, exc: LocalRuntimeError):
        return _error(exc.status_code, exc.code, exc.message)

    @app.exception_handler(CloudClientError)
    async def cloud_error(_: Request, exc: CloudClientError):
        return _error(exc.status_code, exc.code, exc.message)

    @app.exception_handler(SecretStoreError)
    async def secret_error(_: Request, exc: SecretStoreError):
        return _error(500, "secret_store_error", str(exc))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        return _error(
            422,
            "validation_error",
            str(first.get("msg") or "请求内容不完整"),
        )

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            return _error(
                exc.status_code,
                str(detail.get("code") or "http_error"),
                str(detail.get("message") or "请求失败"),
            )
        return _error(exc.status_code, "http_error", str(detail))

    def desktop_authorized(
        token: Annotated[
            str | None,
            Header(alias="X-Yiyu-Desktop-Token"),
        ] = None,
    ) -> None:
        if token != resolved.desktop_token:
            raise LocalRuntimeError(401, "desktop_authorization_required", "本地请求未获授权")

    @app.get("/api/v2/health")
    def health() -> dict[str, Any]:
        current = runtime.current()
        return {
            "status": "ready",
            "service": "yiyu-strict-local",
            "apiVersion": "v2",
            "runtimeStatus": current["runtimeStatus"],
            "schemaFamily": current["databaseIdentity"]["schemaFamily"],
            "databaseGenerationId": current["databaseIdentity"]["databaseGenerationId"],
        }

    @app.get("/api/v2/handshake", dependencies=[Depends(desktop_authorized)])
    def handshake() -> dict[str, Any]:
        current = runtime.current()
        return {
            "apiVersion": "v2",
            "service": "yiyu-strict-local",
            **current["databaseIdentity"],
            "connectedCapabilities": sorted(CONNECTED_CAPABILITIES),
            "notConnectedCapabilities": sorted(
                BUSINESS_CAPABILITIES - CONNECTED_CAPABILITIES
            ),
        }

    @app.get("/api/v2/workspaces/current", dependencies=[Depends(desktop_authorized)])
    def current_workspace() -> dict[str, Any]:
        return runtime.current()

    @app.get("/api/v2/workspaces", dependencies=[Depends(desktop_authorized)])
    def list_workspaces() -> dict[str, Any]:
        return {"items": runtime.list_workspaces()}

    @app.post("/api/v2/workspaces/restore", dependencies=[Depends(desktop_authorized)])
    def restore_workspace() -> dict[str, Any]:
        return runtime.restore_active()

    @app.post("/api/v2/workspaces/login", dependencies=[Depends(desktop_authorized)])
    def login(
        payload: LoginRequest,
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return runtime.login(
            cloud_api_url=payload.cloud_api_url,
            identifier=payload.identifier,
            password=payload.password,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/workspaces/join", dependencies=[Depends(desktop_authorized)])
    def join(payload: JoinRequest) -> dict[str, Any]:
        return runtime.join(
            cloud_api_url=payload.cloud_api_url,
            invite_code=payload.invite_code,
            display_name=payload.display_name,
            email=payload.email,
            phone=payload.phone,
            password=payload.password,
        )

    @app.post(
        "/api/v2/workspaces/create-organization",
        dependencies=[Depends(desktop_authorized)],
    )
    def create_organization(payload: CreateOrganizationRequest) -> dict[str, Any]:
        return runtime.create_organization(
            cloud_api_url=payload.cloud_api_url,
            bootstrap_token=payload.bootstrap_token,
            organization_name=payload.organization_name,
            display_name=payload.display_name,
            email=payload.email,
            phone=payload.phone,
            password=payload.password,
        )

    @app.post("/api/v2/workspaces/switch", dependencies=[Depends(desktop_authorized)])
    def switch_workspace(
        payload: SwitchWorkspaceRequest,
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
        request_seq: Annotated[
            str | None,
            Header(alias="X-Yiyu-Request-Seq"),
        ] = None,
    ) -> dict[str, Any]:
        return runtime.switch(
            payload.sandbox_id,
            idempotency_key=idempotency_key or new_id(),
            request_seq=_request_sequence(request_seq),
        )

    @app.post(
        "/api/v2/workspaces/local-draft",
        dependencies=[Depends(desktop_authorized)],
    )
    def local_draft() -> dict[str, Any]:
        return runtime.activate_local_draft()

    @app.post("/api/v2/workspaces/logout", dependencies=[Depends(desktop_authorized)])
    def logout(
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return runtime.logout(idempotency_key=idempotency_key or new_id())

    @app.get(
        "/api/v2/organization/snapshot",
        dependencies=[Depends(desktop_authorized)],
    )
    def organization_snapshot() -> dict[str, Any]:
        current = runtime.current()
        snapshot = current.get("sessionSnapshot")
        if not snapshot:
            raise LocalRuntimeError(409, "organization_required", "当前尚未连接组织")
        return snapshot

    @app.get(
        "/api/v2/authorization/current",
        dependencies=[Depends(desktop_authorized)],
    )
    def current_authorization() -> dict[str, Any]:
        return runtime.current_authorization()

    @app.get(
        "/api/v2/business/snapshot",
        dependencies=[Depends(desktop_authorized)],
    )
    def business_snapshot(refresh: bool = False) -> dict[str, Any]:
        return runtime.business_snapshot(refresh=refresh)

    @app.get(
        "/api/v2/projects/{project_id}/knowledge-context",
        dependencies=[Depends(desktop_authorized)],
    )
    def project_knowledge_context(project_id: str) -> dict[str, Any]:
        return runtime.project_knowledge_context(project_id)

    @app.post(
        "/api/v2/event-lines",
        dependencies=[Depends(desktop_authorized)],
        status_code=status.HTTP_201_CREATED,
    )
    def create_event_line(
        payload: EventLineCreateRequest,
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return runtime.create_event_line(
            payload=payload.model_dump(by_alias=True),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get(
        "/api/v2/tasks/{task_id}",
        dependencies=[Depends(desktop_authorized)],
    )
    def task_detail(task_id: str) -> dict[str, Any]:
        return runtime.task_detail(task_id)

    @app.post(
        "/api/v2/tasks",
        dependencies=[Depends(desktop_authorized)],
        status_code=status.HTTP_201_CREATED,
    )
    def create_task(
        payload: TaskCreateRequest,
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return runtime.task_command(
            "create",
            task_id=None,
            payload=payload.model_dump(by_alias=True),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.patch(
        "/api/v2/tasks/{task_id}",
        dependencies=[Depends(desktop_authorized)],
    )
    def update_task(
        task_id: str,
        payload: TaskUpdateRequest,
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return runtime.task_command(
            "update",
            task_id=task_id,
            payload=payload.model_dump(by_alias=True, exclude_unset=True),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post(
        "/api/v2/tasks/{task_id}/complete",
        dependencies=[Depends(desktop_authorized)],
    )
    def complete_task(
        task_id: str,
        payload: TaskTransitionRequest,
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return runtime.task_command(
            "complete",
            task_id=task_id,
            payload=payload.model_dump(by_alias=True),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post(
        "/api/v2/tasks/{task_id}/inbox/handle",
        dependencies=[Depends(desktop_authorized)],
    )
    def handle_task_inbox(
        task_id: str,
        payload: TaskInboxHandleRequest,
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return runtime.task_command(
            "inbox_handle",
            task_id=task_id,
            payload=payload.model_dump(by_alias=True),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post(
        "/api/v2/tasks/{task_id}/restore",
        dependencies=[Depends(desktop_authorized)],
    )
    def restore_task(
        task_id: str,
        payload: TaskTransitionRequest,
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return runtime.task_command(
            "restore",
            task_id=task_id,
            payload=payload.model_dump(by_alias=True),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post(
        "/api/v2/workbench/chat",
        dependencies=[Depends(desktop_authorized)],
    )
    def workbench_chat(
        payload: WorkbenchChatRequest,
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return runtime.workbench_chat(
            project_id=payload.project_id,
            question=payload.question,
            mode=payload.mode,
            source_manifest_extra={"threadId": new_id()},
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post(
        "/api/v2/organization/departments",
        dependencies=[Depends(desktop_authorized)],
        status_code=status.HTTP_201_CREATED,
    )
    def create_department(payload: NamedStructureRequest) -> dict[str, Any]:
        runtime.require_capability("organization.manage")
        return runtime.organization_command(
            "create_department",
            {
                "name": payload.name,
                "expectedOrganizationVersion": payload.expected_organization_version,
            },
        )

    @app.post(
        "/api/v2/organization/management-titles",
        dependencies=[Depends(desktop_authorized)],
        status_code=status.HTTP_201_CREATED,
    )
    def create_management_title(payload: NamedStructureRequest) -> dict[str, Any]:
        runtime.require_capability("organization.manage")
        return runtime.organization_command(
            "create_management_title",
            {
                "name": payload.name,
                "expectedOrganizationVersion": payload.expected_organization_version,
            },
        )

    @app.post(
        "/api/v2/organization/invites",
        dependencies=[Depends(desktop_authorized)],
        status_code=status.HTTP_201_CREATED,
    )
    def create_invite(payload: InviteRequest) -> dict[str, Any]:
        runtime.require_capability("authorization.manage")
        return runtime.organization_command(
            "create_invite",
            {
                "inviteKind": payload.invite_kind,
                "targetId": payload.target_id,
                "expiresAt": payload.expires_at,
            },
        )

    @app.get(
        "/api/v2/settings/org-ai-runtime",
        dependencies=[Depends(desktop_authorized)],
    )
    def ai_runtime() -> dict[str, Any]:
        return runtime.current()["aiRuntime"]

    @app.post(
        "/api/v2/settings/org-ai-runtime/sync",
        dependencies=[Depends(desktop_authorized)],
    )
    def sync_ai_runtime() -> dict[str, Any]:
        return runtime.sync_ai()

    @app.put(
        "/api/v2/settings/org-ai-config",
        dependencies=[Depends(desktop_authorized)],
    )
    def save_ai_config(payload: OrganizationAiConfigRequest) -> dict[str, Any]:
        runtime.require_capability("organization_ai.manage")
        return runtime.save_ai_config(
            provider=payload.provider,
            base_url=payload.base_url,
            model_name=payload.model_name,
            api_key=payload.api_key,
            expected_version=payload.expected_version,
            idempotency_key=payload.idempotency_key or new_id(),
        )

    @app.api_route(
        "/api/v2/ui/{ui_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        dependencies=[Depends(desktop_authorized)],
    )
    async def mature_renderer_compat(
        ui_path: str,
        request: Request,
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
        expected_sandbox_id: Annotated[
            str | None,
            Header(alias="X-Yiyu-Sandbox-Id"),
        ] = None,
        request_seq: Annotated[
            str | None,
            Header(alias="X-Yiyu-Request-Seq"),
        ] = None,
    ) -> Any:
        body: dict[str, Any] = {}
        if request.method not in {"GET", "HEAD"}:
            content_type = request.headers.get("content-type", "")
            if "application/json" in content_type:
                payload = await request.json()
                if isinstance(payload, dict):
                    body = payload
            elif "multipart/form-data" in content_type:
                form = await request.form()
                for key, value in form.multi_items():
                    existing = body.get(key)
                    if existing is None:
                        body[key] = value
                    elif isinstance(existing, list):
                        existing.append(value)
                    else:
                        body[key] = [existing, value]
        query = dict(request.query_params)
        operation_id = idempotency_key or new_id()
        parsed_request_seq = _request_sequence(request_seq)
        workspace_context = ui_compat.capture_dispatch_workspace(
            request.method,
            ui_path,
            query=query,
            body=body,
            idempotency_key=operation_id,
            expected_sandbox_id=(expected_sandbox_id or "").strip() or None,
            request_seq=parsed_request_seq,
        )
        selected_dispatch = (
            research_ui_dispatch
            if request.method == "POST"
            and (
                ui_path.strip("/").endswith("/official-website/refresh")
                or ui_path.strip("/")
                in {
                    "intelligence/refresh",
                    "intelligence/sentiment/refresh",
                }
            )
            else report_ui_dispatch
            if request.method == "POST"
            and ui_path.strip("/").endswith("/draft-sections")
            else interactive_ai_dispatch
            if request.method == "POST"
            and (
                ui_path.strip("/").endswith("/workspace/chat/start")
                or ui_path.strip("/").endswith("/documents/ai-action")
            )
            else background_ui_dispatch
            if request.method == "POST"
            and (
                ui_path.strip("/").endswith("/process-pending")
                or ui_path.strip("/").endswith("/retry-transcription")
                or ui_path.strip("/").endswith("/narrative/regenerate")
                or ui_path.strip("/").endswith("/narrative/clarifications")
                or ui_path.strip("/")
                in {"local-ai/backfill", "local-ai/run-now"}
            )
            else ui_dispatch
        )
        workspace_key = (
            (
                workspace_context.sandbox_id,
                workspace_context.cloud_instance_id,
                workspace_context.organization_id,
            )
            if workspace_context is not None
            else ("unscoped",)
        )
        coalesce_key = (
            workspace_key,
            request.method,
            ui_path.strip("/"),
            tuple(sorted(query.items())),
        ) if (
            request.method == "GET"
            or request.method == "POST"
            and ui_path.strip("/").endswith("/process-pending")
        ) else None
        try:
            return await selected_dispatch.run(
                request.method,
                ui_path,
                query=query,
                body=body,
                idempotency_key=operation_id,
                workspace_context=workspace_context,
                expected_sandbox_id=(expected_sandbox_id or "").strip() or None,
                request_seq=parsed_request_seq,
                coalesce_key=coalesce_key,
            )
        except DispatchBusyError as exc:
            raise LocalRuntimeError(
                503,
                "local_backend_busy",
                str(exc),
            ) from exc
        except DispatchTimeoutError as exc:
            raise LocalRuntimeError(
                504,
                "local_backend_timeout",
                str(exc),
            ) from exc

    @app.get("/api/v2/capabilities", dependencies=[Depends(desktop_authorized)])
    def capabilities() -> dict[str, Any]:
        return {"items": runtime.current()["capabilities"]}

    @app.get(
        "/api/v2/capabilities/{capability_id:path}",
        dependencies=[Depends(desktop_authorized)],
    )
    def capability(capability_id: str):
        match = next(
            (
                item
                for item in runtime.current()["capabilities"]
                if item["id"] == capability_id
            ),
            None,
        )
        if match is None:
            return _error(404, "capability_unknown", "未知功能能力")
        if match["state"] != "connected":
            return _error(501, "capability_not_connected", match["reason"])
        return match

    @app.get("/api/v2/diagnostics", dependencies=[Depends(desktop_authorized)])
    def diagnostics() -> dict[str, Any]:
        return runtime.diagnostics()

    return app


if __name__ == "__main__":
    config = LocalConfig.load()
    uvicorn.run(
        create_app(config),
        host=config.host,
        port=config.port,
        log_level=os.environ.get("YIYU_STRICT_LOG_LEVEL", "info"),
    )
