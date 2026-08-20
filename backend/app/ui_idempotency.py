from __future__ import annotations

from typing import Any, Callable, Mapping

from strict_common.ids import canonical_json, sha256_text

from .platform_integrations_local import LocalPlatformOperationRepository


def _operation_suffix(method: str, path: str) -> str:
    return sha256_text(f"{method.upper()}|{path}")[:20]


def replayable_cloud_mutation(
    runtime: Any,
    *,
    idempotency_key: str,
    command_type: str,
    aggregate_type: str,
    aggregate_id: str,
    method: str,
    path: str,
    request_payload: Mapping[str, Any],
    cloud_payload_factory: Callable[[], Mapping[str, Any]],
    refresh_business: bool = True,
) -> dict[str, Any]:
    """Freeze a UI orchestration before performing its cloud mutation.

    A cloud commit can succeed while its response is lost.  Retrying the UI
    request must then reuse the *first* CAS version and payload instead of
    querying a newer version and submitting different content under the same
    idempotency key.  The receipt uses only the existing strict-88 command,
    idempotency and object-manifest objects.
    """

    # Lightweight domain-test doubles and non-desktop callers do not own a
    # local strict sandbox.  They cannot persist a UI receipt, so preserve the
    # former direct behavior there.  Every real Electron runtime has this API.
    if not callable(getattr(runtime, "capture_sandbox_context", None)):
        return runtime.cloud_command(
            method.upper(),
            path,
            payload=dict(cloud_payload_factory()),
            idempotency_key=idempotency_key,
        )

    suffix = _operation_suffix(method, path)
    repository = LocalPlatformOperationRepository(runtime)
    operation_key = f"{idempotency_key}:ui-orchestration:{suffix}"
    cloud_key = f"{idempotency_key}:cloud-mutation:{suffix}"
    stable_intent = {
        "method": method.upper(),
        "path": path,
        "requestPayload": dict(request_payload),
    }
    started = repository.begin(
        idempotency_key=operation_key,
        command_type=command_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=stable_intent,
        initial_result={
            "state": "processing",
            "retryable": True,
            "pollingEnabled": False,
        },
    )
    if str(started.get("state") or "") == "completed":
        response = started.get("cloudResponse")
        return dict(response) if isinstance(response, Mapping) else {}

    operation_id = str(started["operationId"])
    frozen_payload = started.get("cloudPayload")
    if not isinstance(frozen_payload, Mapping):
        frozen_payload = dict(cloud_payload_factory())
        started = repository.update(
            operation_id=operation_id,
            state="processing",
            result_patch={
                "cloudPayload": frozen_payload,
                "cloudPayloadHash": sha256_text(canonical_json(frozen_payload)),
                "cloudIdempotencyKey": cloud_key,
            },
        )
        frozen_payload = started.get("cloudPayload") or frozen_payload

    response = runtime.cloud_command(
        method.upper(),
        path,
        payload=dict(frozen_payload),
        idempotency_key=cloud_key,
        refresh_business=refresh_business,
    )
    settled = repository.update(
        operation_id=operation_id,
        state="completed",
        result_patch={"cloudResponse": dict(response)},
    )
    cloud_response = settled.get("cloudResponse")
    return dict(cloud_response) if isinstance(cloud_response, Mapping) else dict(response)


def replayable_generated_value(
    runtime: Any,
    *,
    idempotency_key: str,
    command_type: str,
    aggregate_type: str,
    aggregate_id: str,
    input_payload: Mapping[str, Any],
    generate: Callable[[], Mapping[str, Any]],
) -> dict[str, Any]:
    """Generate a non-deterministic value once and replay the stored result."""

    if not callable(getattr(runtime, "capture_sandbox_context", None)):
        return dict(generate())

    repository = LocalPlatformOperationRepository(runtime)
    started = repository.begin(
        idempotency_key=f"{idempotency_key}:generation:{command_type}",
        command_type=command_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload=dict(input_payload),
        initial_result={
            "state": "processing",
            "retryable": True,
            "pollingEnabled": False,
        },
    )
    generated = started.get("generatedValue")
    if isinstance(generated, Mapping):
        return dict(generated)
    value = dict(generate())
    stored = repository.update(
        operation_id=str(started["operationId"]),
        state="processing",
        result_patch={"generatedValue": value},
    )
    generated = stored.get("generatedValue")
    return dict(generated) if isinstance(generated, Mapping) else value


__all__ = ["replayable_cloud_mutation", "replayable_generated_value"]
