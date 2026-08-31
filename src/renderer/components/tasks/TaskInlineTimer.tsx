import React, { useEffect, useState } from 'react';
import { Loader2, Pause, Play, Square, Timer } from 'lucide-react';

import type { TaskTimerSummary } from '../../../shared/types';
import {
  formatTaskTimerDuration,
  projectedTaskTimerSeconds,
} from '../../../shared/taskTimer';
import type { TaskTimerAction } from '../../lib/api';

type Props = {
  taskTitle: string;
  timer?: TaskTimerSummary | null;
  canTrackTime: boolean;
  allowStart?: boolean;
  disabled?: boolean;
  onAction: (action: TaskTimerAction) => Promise<void>;
};

const EMPTY_TIMER: TaskTimerSummary = {
  state: 'idle',
  elapsedSeconds: 0,
  latestRunId: null,
  activeStartedAt: null,
  version: 0,
  observedAt: '',
};

export function TaskInlineTimer({
  taskTitle,
  timer,
  canTrackTime,
  allowStart = true,
  disabled = false,
  onAction,
}: Props) {
  const current = timer || EMPTY_TIMER;
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [busyAction, setBusyAction] = useState<TaskTimerAction | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    setNowMs(Date.now());
    setError('');
    if (current.state !== 'running') return undefined;
    const interval = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(interval);
  }, [current.state, current.observedAt, current.version]);

  const elapsedLabel = formatTaskTimerDuration(
    projectedTaskTimerSeconds(current, nowMs),
  );
  const unavailable = disabled || !canTrackTime || busyAction !== null;
  const canStart = !unavailable && allowStart && current.state !== 'running';
  const canPause = !unavailable && current.state === 'running';
  const canStop = !unavailable && (current.state === 'running' || current.state === 'paused');
  const stateLabel = current.state === 'running'
    ? '正在计时'
    : current.state === 'paused'
      ? '计时已暂停'
      : current.state === 'stopped'
        ? '计时已停止'
        : '尚未计时';
  const compactLabel = current.state === 'running'
    ? `我的 · 计时中 ${elapsedLabel}`
    : current.state === 'paused'
      ? `我的 · 已暂停 ${elapsedLabel}`
      : `我的用时 ${elapsedLabel}`;

  const runAction = async (action: TaskTimerAction) => {
    setBusyAction(action);
    setError('');
    try {
      await onAction(action);
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : '计时操作失败，请重试');
    } finally {
      setBusyAction(null);
    }
  };

  const iconButtonClass = 'inline-flex h-7 w-7 items-center justify-center text-slate-400 transition hover:bg-white hover:text-[#5B7BFE] focus:outline-none focus-visible:z-10 focus-visible:ring-2 focus-visible:ring-[#5B7BFE]/30 disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-slate-400';

  return (
    <div
      className="relative"
      onClick={(event) => event.stopPropagation()}
      onPointerDown={(event) => event.stopPropagation()}
    >
      <div
        className={`inline-flex h-8 items-center overflow-hidden rounded-lg border bg-slate-50/90 ${
          current.state === 'running'
            ? 'border-emerald-200'
            : current.state === 'paused'
              ? 'border-amber-200'
              : 'border-slate-200'
        }`}
        aria-label={`${taskTitle}，${stateLabel}，累计 ${elapsedLabel}`}
      >
        <span
          className="inline-flex min-w-[118px] items-center gap-1.5 px-2 text-[11px] font-semibold tabular-nums text-slate-600"
          title={`我的用时 · ${stateLabel} · 累计 ${elapsedLabel}`}
        >
          {busyAction ? (
            <Loader2 size={13} className="shrink-0 animate-spin text-[#5B7BFE]" aria-hidden="true" />
          ) : (
            <Timer
              size={13}
              className={`shrink-0 ${current.state === 'running' ? 'text-emerald-600' : current.state === 'paused' ? 'text-amber-600' : 'text-slate-400'}`}
              aria-hidden="true"
            />
          )}
          <span>{compactLabel}</span>
        </span>
        <span className="h-4 w-px bg-slate-200" aria-hidden="true" />
        <button
          type="button"
          className={iconButtonClass}
          disabled={!canStart}
          aria-label={`${current.state === 'paused' ? '继续' : '开始'}任务计时：${taskTitle}`}
          title={!canTrackTime ? '仅任务参与成员可以计时' : !allowStart ? '已完成任务不能重新开始计时' : current.state === 'paused' ? '继续计时' : '开始计时'}
          onClick={() => void runAction('start')}
        >
          <Play size={12} fill="currentColor" aria-hidden="true" />
        </button>
        <button
          type="button"
          className={iconButtonClass}
          disabled={!canPause}
          aria-label={`暂停任务计时：${taskTitle}`}
          title="暂停计时"
          onClick={() => void runAction('pause')}
        >
          <Pause size={12} fill="currentColor" aria-hidden="true" />
        </button>
        <button
          type="button"
          className={`${iconButtonClass} hover:text-rose-500`}
          disabled={!canStop}
          aria-label={`停止任务计时：${taskTitle}`}
          title="停止计时"
          onClick={() => void runAction('stop')}
        >
          <Square size={11} fill="currentColor" aria-hidden="true" />
        </button>
      </div>
      {error && (
        <div
          role="alert"
          className="absolute right-0 top-full z-30 mt-1 w-60 rounded-lg border border-rose-100 bg-white px-2.5 py-2 text-left text-[11px] leading-4 text-rose-600 shadow-lg"
        >
          {error}
        </div>
      )}
    </div>
  );
}
