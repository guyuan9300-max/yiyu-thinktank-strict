from __future__ import annotations

import platform
import re
import subprocess
from dataclasses import dataclass
from typing import Literal


_IS_MACOS = platform.system() == "Darwin"
_PROBE_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class MachineHealth:
    thermal_state: int = -1
    cpu_speed_limit: int = 100
    user_idle_seconds: float = -1.0
    battery_percent: int = -1
    on_ac_power: bool = True
    memory_pressure: str = "unknown"


@dataclass(frozen=True)
class GovernorDecision:
    verdict: Literal["go", "wait"]
    reason: str
    retry_after_seconds: int


def _run_command(arguments: list[str]) -> str:
    try:
        completed = subprocess.run(
            arguments,
            capture_output=True,
            check=False,
            text=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout if completed.returncode == 0 else ""


def _thermal_state() -> int:
    if not _IS_MACOS:
        return -1
    raw = _run_command(
        ["sysctl", "-n", "machdep.xcpm.cpu_thermal_state"]
    ).strip()
    try:
        return int(raw)
    except ValueError:
        thermal = _run_command(["pmset", "-g", "therm"]).lower()
        if "no thermal warning" in thermal:
            return 0
        if "thermal warning level" in thermal:
            return 3
        return -1


def _cpu_speed_limit() -> int:
    if not _IS_MACOS:
        return 100
    match = re.search(
        r"CPU_Speed_Limit\s*=\s*(\d+)",
        _run_command(["pmset", "-g", "therm"]),
    )
    return int(match.group(1)) if match else 100


def _idle_seconds() -> float:
    if not _IS_MACOS:
        return -1.0
    match = re.search(
        r'"HIDIdleTime"\s*=\s*(\d+)',
        _run_command(["ioreg", "-c", "IOHIDSystem"]),
    )
    return int(match.group(1)) / 1_000_000_000 if match else -1.0


def _battery() -> tuple[int, bool]:
    if not _IS_MACOS:
        return -1, True
    raw = _run_command(["pmset", "-g", "batt"])
    percent = re.search(r"(\d+)%", raw)
    return (
        int(percent.group(1)) if percent else -1,
        "AC Power" in raw or "charged" in raw.lower(),
    )


def _memory_pressure() -> str:
    if not _IS_MACOS:
        return "unknown"
    raw = _run_command(["memory_pressure"])
    lowered = raw.lower()
    for level in ("critical", "warn", "normal"):
        if f"memory pressure: {level}" in lowered:
            return level
    total = re.search(r"\((\d+) pages", raw)
    free = re.search(r"Pages free:\s*(\d+)", raw)
    if total and free and int(total.group(1)) > 0:
        free_percent = int(free.group(1)) / int(total.group(1)) * 100
        if free_percent < 5:
            return "critical"
        if free_percent < 15:
            return "warn"
        return "normal"
    return "unknown"


def collect_machine_health() -> MachineHealth:
    battery_percent, on_ac_power = _battery()
    return MachineHealth(
        thermal_state=_thermal_state(),
        cpu_speed_limit=_cpu_speed_limit(),
        user_idle_seconds=_idle_seconds(),
        battery_percent=battery_percent,
        on_ac_power=on_ac_power,
        memory_pressure=_memory_pressure(),
    )


def decide_machine_run(
    health: MachineHealth,
    *,
    require_ac_power: bool,
    min_idle_seconds: float,
    max_thermal_state: int = 3,
) -> GovernorDecision:
    if (
        health.thermal_state >= 0
        and health.thermal_state >= max_thermal_state
    ):
        return GovernorDecision(
            "wait",
            "设备正在散热，本机深度解析已让位",
            120,
        )
    if health.cpu_speed_limit < 100:
        return GovernorDecision(
            "wait",
            "系统正在限速散热，本机深度解析已让位",
            120,
        )
    if health.memory_pressure == "critical":
        return GovernorDecision(
            "wait",
            "系统内存压力较高，本机深度解析已让位",
            120,
        )
    if require_ac_power and not health.on_ac_power:
        return GovernorDecision(
            "wait",
            "当前未接通电源，本机深度解析按设置暂停",
            60,
        )
    if (
        min_idle_seconds > 0
        and health.user_idle_seconds >= 0
        and health.user_idle_seconds < min_idle_seconds
    ):
        return GovernorDecision(
            "wait",
            "用户正在操作电脑，本机深度解析按设置暂停",
            60,
        )
    return GovernorDecision("go", "设备资源允许执行本机深度解析", 0)
