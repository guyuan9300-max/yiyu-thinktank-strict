from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI

from ..repository import CloudRepository, SessionIdentity
from .project_materials import register_gc07_routes
from .workbench_outputs import (
    register_strategic_profile_routes,
    register_strategic_support_routes,
)
from .agent_skills import register_agent_skill_routes
from .gc09_reports import register_gc09_report_routes
from .gc08_meetings import register_gc08_routes
from .gc13_growth import register_gc13_growth_routes
from .gc14_proposals import register_gc14_proposal_routes
from .gc15_lifecycle import register_gc15_lifecycle_routes
from .system_governance import register_system_governance_routes
from .platform_integrations import register_routes as register_platform_integration_routes
from .gc04_tasks import register_gc04_task_routes
from .gc06_planning import register_gc06_planning_routes
from .data_center_support_88 import register_data_center_support_routes
from .organization_access import register_routes as register_organization_access_routes
from .mobile_sync import register_mobile_sync_routes
from .mobile_consult import register_mobile_consult_routes
from .mobile_devices import register_mobile_device_routes
from .mobile_link_transfers import register_mobile_link_transfer_routes
from ..repositories.gc06_task_command_port import GC04_FORMAL_TASK_COMMAND_PORT


IdentityDependency = Callable[..., SessionIdentity]


def register_domain_routes(
    app: FastAPI,
    repository: CloudRepository,
    identity_dependency: IdentityDependency,
) -> None:
    # GC-07 phase 2: only the project root and local-material metadata surface
    # is connected. The remaining v4 registrars stay detached until their
    # approved 88-table golden-chain traces are implemented.
    register_gc07_routes(app, repository, identity_dependency)
    register_strategic_profile_routes(app, repository, identity_dependency)
    register_strategic_support_routes(app, repository, identity_dependency)
    register_agent_skill_routes(app, repository, identity_dependency)
    register_gc09_report_routes(app, repository, identity_dependency)
    register_gc08_routes(app, repository, identity_dependency)
    register_gc13_growth_routes(app, repository, identity_dependency)
    register_gc14_proposal_routes(app, repository, identity_dependency)
    register_gc15_lifecycle_routes(app, repository, identity_dependency)
    register_system_governance_routes(app, repository, identity_dependency)
    register_platform_integration_routes(app, repository, identity_dependency)
    register_organization_access_routes(app, repository, identity_dependency)
    register_gc04_task_routes(app, repository, identity_dependency)
    register_gc06_planning_routes(
        app,
        repository,
        identity_dependency,
        task_command_port=GC04_FORMAL_TASK_COMMAND_PORT,
    )
    register_data_center_support_routes(app, repository, identity_dependency)
    register_mobile_sync_routes(app, repository, identity_dependency)
    register_mobile_consult_routes(app, repository, identity_dependency)
    register_mobile_device_routes(app, repository, identity_dependency)
    register_mobile_link_transfer_routes(app, repository, identity_dependency)
