from pathlib import Path

from cloud_backend.app.repositories.system_governance import SystemGovernanceRepository
from strict_common.schema import runtime_connection, user_tables
from tests.test_gc14_workbench_answer import _repository


def test_verified_backup_activates_strict_recovery_chain(tmp_path: Path) -> None:
    repository, identity, _ = _repository(tmp_path)
    governance = SystemGovernanceRepository(repository)
    created = governance.create_database_backup(
        identity,
        idempotency_key="strict-backup-once",
        retention_days=7,
    )
    replayed = governance.create_database_backup(
        identity,
        idempotency_key="strict-backup-once",
        retention_days=7,
    )
    assert created["status"] == "verified"
    assert created["wholeSystemVerified"] is True
    assert replayed["idempotentReplay"] is True
    gate = governance.decide_release_gate(
        identity,
        candidate_version="candidate-20260809",
        recovery_set_id=created["recoverySetId"],
        evidence_version="strict-targeted-v1",
        evidence_hash="a" * 64,
        decision="passed",
        blocking_reason=None,
        idempotency_key="strict-release-gate-once",
    )
    gate_replay = governance.decide_release_gate(
        identity,
        candidate_version="candidate-20260809",
        recovery_set_id=created["recoverySetId"],
        evidence_version="strict-targeted-v1",
        evidence_hash="a" * 64,
        decision="passed",
        blocking_reason=None,
        idempotency_key="strict-release-gate-once",
    )
    mapping = governance.record_git_mapping(
        identity,
        repository_ref="github:guyuan9300-max/yiyu-thinktank-strict",
        commit_ref="b" * 40,
        remote_receipt="refs/heads/main@" + "b" * 40,
        status="succeeded",
        executed_by_instance_id="local-test-instance",
        idempotency_key="strict-git-mapping-once",
    )
    assert gate["decision"] == "passed"
    assert gate_replay["idempotentReplay"] is True
    assert mapping["status"] == "succeeded"
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert len(user_tables(connection)) == 88
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        for table in ("recovery_sets", "backup_catalog", "recovery_manifests"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM release_gates").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM git_mappings").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM commands WHERE command_type='system.recovery_set.create'"
        ).fetchone()[0] == 1
        backup_ref = connection.execute("SELECT backup_ref FROM backup_catalog").fetchone()[0]
    assert Path(backup_ref).is_file()
