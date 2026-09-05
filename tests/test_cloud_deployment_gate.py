from __future__ import annotations

import subprocess
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT_SCRIPT = REPOSITORY_ROOT / "scripts" / "deploy_strict_cloud_release.sh"


def _deployment_source() -> str:
    return DEPLOYMENT_SCRIPT.read_text(encoding="utf-8")


def test_deployment_script_has_valid_bash_syntax() -> None:
    subprocess.run(["bash", "-n", str(DEPLOYMENT_SCRIPT)], check=True)


def test_runtime_python_comes_from_systemd_or_explicit_override() -> None:
    deployment = _deployment_source()

    assert "--runtime-python" in deployment
    assert "runtime Python override must be a safe absolute path" in deployment
    assert 'systemctl show "$service_name" --property=ExecStart --value' in deployment
    assert "runtime Python is not an executable absolute path" in deployment
    assert "runtime Python failed validation" in deployment
    assert '"$release_base/venvs"/*/bin/python' not in deployment

    resolve_runtime = deployment.index('service_exec_start="$(systemctl show')
    verify_release = deployment.index(
        '"$runtime_python" "$release_dir/scripts/strict_cloud_release.py"'
    )
    assert resolve_runtime < verify_release


def test_schedule_assistant_route_gate_precedes_success_cleanup() -> None:
    deployment = _deployment_source()

    health_gate = deployment.index('if [[ "$healthy" != 1 ]]')
    route_path = deployment.index("/api/v2/ui/tasks/schedule-assistant/ask")
    success_cleanup = deployment.index('rm -f "$remote_archive"')

    assert health_gate < route_path < success_cleanup
    assert "--request POST" in deployment
    assert "--header 'Content-Type: application/json'" in deployment
    assert (
        "--data-binary "
        "'{\"question\":\"__route_probe__\",\"viewerName\":\"route-probe\"}'"
        in deployment
    )
    assert '[[ "$route_status" == "401" ]]' in deployment
    assert 'error.get("code") == "authorization_required"' in deployment

    route_probe = deployment[route_path:success_cleanup]
    assert "Authorization:" not in route_probe


def test_route_gate_failure_uses_atomic_rollback_and_stops_deployment() -> None:
    deployment = _deployment_source()

    restore_function = deployment.index("restore_previous_release()")
    rollback_function = deployment.index("rollback_after_gate_failure()")
    route_failure = deployment.index('if [[ "$route_ready" != 1 ]]')
    success_cleanup = deployment.index('rm -f "$remote_archive"')
    restore_function_block = deployment[restore_function:rollback_function]
    route_failure_block = deployment[route_failure:success_cleanup]

    assert 'ln -sfn "$previous_release" "$next_link"' in restore_function_block
    assert 'mv -Tf "$next_link" "$current_link"' in restore_function_block
    assert 'systemctl restart "$service_name"' in restore_function_block
    assert "rollback_after_gate_failure" in route_failure_block
    assert "schedule-assistant route gate failed" in route_failure_block
    assert "exit 6" in route_failure_block
