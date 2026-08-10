from backend.app.local_ai_governor import (
    MachineHealth,
    decide_machine_run,
)


def test_local_ai_governor_enforces_device_preferences() -> None:
    unplugged = decide_machine_run(
        MachineHealth(on_ac_power=False),
        require_ac_power=True,
        min_idle_seconds=0,
    )
    assert unplugged.verdict == "wait"
    assert unplugged.retry_after_seconds == 60

    active = decide_machine_run(
        MachineHealth(user_idle_seconds=10),
        require_ac_power=False,
        min_idle_seconds=60,
    )
    assert active.verdict == "wait"

    ready = decide_machine_run(
        MachineHealth(
            thermal_state=0,
            cpu_speed_limit=100,
            user_idle_seconds=120,
            on_ac_power=True,
            memory_pressure="normal",
        ),
        require_ac_power=True,
        min_idle_seconds=60,
    )
    assert ready.verdict == "go"


def test_local_ai_governor_prioritizes_thermal_and_memory_safety() -> None:
    hot = decide_machine_run(
        MachineHealth(thermal_state=3),
        require_ac_power=False,
        min_idle_seconds=0,
    )
    assert hot.verdict == "wait"
    assert hot.retry_after_seconds == 120

    pressured = decide_machine_run(
        MachineHealth(memory_pressure="critical"),
        require_ac_power=False,
        min_idle_seconds=0,
    )
    assert pressured.verdict == "wait"
