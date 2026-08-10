from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.app.runtime import LocalRuntimeError
from backend.app.ui_compat import StrictUiCompatibility


class _RuntimeStub:
    def __init__(self, database_path: Path):
        self.database_path = database_path


def _compatibility(
    tmp_path: Path,
    *,
    organization_id: str,
    primary_role: str,
) -> StrictUiCompatibility:
    compatibility = StrictUiCompatibility(_RuntimeStub(tmp_path / "strict-local.db"))  # type: ignore[arg-type]
    user: dict[str, Any] = {
        "id": "member-1",
        "organizationId": organization_id,
        "primaryRole": primary_role,
    }
    compatibility.auth_state = lambda: {  # type: ignore[method-assign]
        "authenticated": True,
        "user": user,
    }
    return compatibility


def test_official_workspace_member_can_toggle_local_maintenance_mode(
    tmp_path: Path,
) -> None:
    compatibility = _compatibility(
        tmp_path,
        organization_id="org_yiyu_default",
        primary_role="employee",
    )

    initial = compatibility.maintenance_mode()
    assert initial["available"] is True
    assert initial["canEnter"] is True
    assert initial["canManagePermissions"] is False

    entered = compatibility.maintenance_mode(active=True)
    assert entered["active"] is True

    exited = compatibility.maintenance_mode(active=False)
    assert exited["active"] is False


def test_other_organization_cannot_toggle_local_maintenance_mode(
    tmp_path: Path,
) -> None:
    compatibility = _compatibility(
        tmp_path,
        organization_id="org_other",
        primary_role="admin",
    )

    status = compatibility.maintenance_mode()
    assert status["canEnter"] is False
    assert status["canManagePermissions"] is True
    assert status["reason"] == "请切换到益语智库工作空间。"

    with pytest.raises(LocalRuntimeError) as error:
        compatibility.maintenance_mode(active=True)
    assert error.value.status_code == 403
    assert error.value.code == "maintenance_official_workspace_required"
