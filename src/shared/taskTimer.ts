import type { TaskTimerSummary } from './types';

function wholeSeconds(value: number): number {
  return Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0;
}

export function formatTaskTimerDuration(seconds: number): string {
  const total = wholeSeconds(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainingSeconds = total % 60;
  return [hours, minutes, remainingSeconds]
    .map((part) => String(part).padStart(2, '0'))
    .join(':');
}

export function projectedTaskTimerSeconds(
  timer: TaskTimerSummary | null | undefined,
  nowMs: number = Date.now(),
): number {
  const confirmed = wholeSeconds(Number(timer?.elapsedSeconds || 0));
  if (timer?.state !== 'running') return confirmed;
  const observedAtMs = Date.parse(String(timer.observedAt || ''));
  if (!Number.isFinite(observedAtMs) || !Number.isFinite(nowMs)) return confirmed;
  return confirmed + wholeSeconds((nowMs - observedAtMs) / 1000);
}
