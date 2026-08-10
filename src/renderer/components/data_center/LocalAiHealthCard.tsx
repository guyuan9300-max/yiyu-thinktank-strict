/**
 * Phase 3：本地推理硬件健康卡片。
 *
 * 嵌入到 DataCenterOpsPanel.tsx 现有「后台数据优化」卡片下方。
 * 每 30 秒轮询一次 /api/v2/ui/local-ai/health，显示 governor 当前判定 + 健康快照。
 *
 * 目的：让用户直接看到「我的 Mac 现在能不能跑下一条任务」+ 为什么。
 */
import { useEffect, useState, useCallback } from 'react';
import { alertWithLog } from '../../lib/clientErrorReport';
import {
  getLocalAiHealth,
  getLocalAiQueue,
  runLocalAiNow,
  type LocalAiHealthRecord,
  type LocalAiQueueResponse,
} from '../../lib/api';

interface LocalAiHealthCardProps {
  refreshIntervalMs?: number;
}

function verdictBadge(verdict: LocalAiHealthRecord['verdict']): {
  label: string;
  className: string;
} {
  if (verdict === 'go') {
    return {
      label: '可执行',
      className: 'bg-emerald-100 text-emerald-800 border-emerald-300',
    };
  }
  if (verdict === 'wait') {
    return {
      label: '暂停中',
      className: 'bg-amber-100 text-amber-800 border-amber-300',
    };
  }
  return {
    label: '跳过',
    className: 'bg-gray-100 text-gray-700 border-gray-300',
  };
}

function memoryPressureLabel(p: LocalAiHealthRecord['memory_pressure']): string {
  if (p === 'normal') return '正常';
  if (p === 'warn') return '偏紧';
  if (p === 'critical') return '紧迫';
  return '未知';
}

export function LocalAiHealthCard({
  refreshIntervalMs = 30000,
}: LocalAiHealthCardProps): JSX.Element {
  const [health, setHealth] = useState<LocalAiHealthRecord | null>(null);
  const [queue, setQueue] = useState<LocalAiQueueResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runningNow, setRunningNow] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [h, q] = await Promise.all([
        getLocalAiHealth(),
        getLocalAiQueue({ limit: 1 }),
      ]);
      setHealth(h);
      setQueue(q);
      setError(null);
      return h.pollingEnabled === true || q.pollingEnabled === true;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return false;
    }
  }, []);

  useEffect(() => {
    let active = true;
    let id: number | null = null;
    void refresh().then((pollingEnabled) => {
      if (active && pollingEnabled) {
        id = window.setInterval(() => void refresh(), refreshIntervalMs);
      }
    });
    return () => {
      active = false;
      if (id !== null) window.clearInterval(id);
    };
  }, [refresh, refreshIntervalMs]);

  const handleRunNow = useCallback(async () => {
    setRunningNow(true);
    try {
      const result = await runLocalAiNow(false);
      const detail = result.governor_reason
        ? `${result.status} — ${result.governor_reason}`
        : `${result.status}: 处理 ${result.processed} 成功 / ${result.failed} 失败`;
      window.setTimeout(() => alert(`本地推理触发：${detail}`), 0);
      await refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // 同时上报到 backend 日志,让此类失败可事后追溯
      window.setTimeout(() => alertWithLog(`触发失败：${msg}`, { feature: 'local_ai_run_now' }), 0);
    } finally {
      setRunningNow(false);
    }
  }, [refresh]);

  if (error) {
    return (
      <div className="mt-3 rounded-2xl border border-rose-200 bg-rose-50/70 px-3 py-3">
        <p className="text-[11px] font-black text-rose-900">本地 AI 加速</p>
        <p className="mt-1 text-[11px] font-semibold leading-5 text-rose-700">
          状态获取失败：{error}
        </p>
      </div>
    );
  }

  if (!health) {
    return (
      <div className="mt-3 rounded-2xl border border-slate-200 bg-slate-50/70 px-3 py-3">
        <p className="text-[11px] font-semibold text-slate-600">加载本地 AI 状态…</p>
      </div>
    );
  }

  const badge = verdictBadge(health.verdict);
  const queuedCount = queue?.totalByStatus.queued ?? 0;
  const runningCount = queue?.totalByStatus.running ?? 0;
  const failedCount = queue?.totalByStatus.failed ?? 0;
  const completedCount = queue?.totalByStatus.completed ?? 0;
  const aboutToBeIdle = health.user_idle_seconds >= 0
    ? `${Math.round(health.user_idle_seconds)}s`
    : '未知';
  const battery = health.battery_percent >= 0
    ? `${health.battery_percent}% / ${health.on_ac_power ? '插电' : '电池'}`
    : '无电池';

  return (
    <div className="mt-3 rounded-2xl border border-sky-200 bg-sky-50/70 px-3 py-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <p className="text-[11px] font-black text-sky-900">本地 AI 加速</p>
            <span
              className={`rounded-full border px-2 py-0.5 text-[10px] font-black ${badge.className}`}
            >
              {badge.label}
            </span>
            {!health.enabled && (
              <span className="rounded-full border border-gray-300 bg-gray-100 px-2 py-0.5 text-[10px] font-semibold text-gray-700">
                未启用
              </span>
            )}
            {health.paused && (
              <span className="rounded-full border border-amber-300 bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-700">
                手动暂停
              </span>
            )}
          </div>
          <p className="mt-1 text-[11px] font-semibold leading-5 text-sky-800">
            {health.summary}
          </p>
          {health.reason && (
            <p className="mt-1 text-[11px] font-medium leading-5 text-amber-700">
              {health.reason}
              {health.retry_after_seconds > 0
                ? ` · ${health.retry_after_seconds}s 后重试`
                : ''}
            </p>
          )}
          <p className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[10px] font-medium leading-4 text-sky-700">
            <span>温度档位 {health.thermal_state >= 0 ? `${health.thermal_state}/5` : '未知'}</span>
            <span>CPU {health.cpu_speed_limit}%</span>
            <span>空闲 {aboutToBeIdle}</span>
            <span>电池 {battery}</span>
            <span>内存 {memoryPressureLabel(health.memory_pressure)}</span>
            <span>Ollama {health.ollama_reachable ? '✓' : '✗'}</span>
          </p>
          <p className="mt-1 text-[11px] font-medium leading-5 text-sky-700">
            队列：等待 {queuedCount} · 执行中 {runningCount} · 已完成 {completedCount}
            {failedCount > 0 ? ` · 失败 ${failedCount}` : ''}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="rounded-full bg-sky-600 px-3 py-1.5 text-[11px] font-black text-white shadow-sm disabled:opacity-60"
            onClick={() => void handleRunNow()}
            disabled={runningNow || !health.enabled}
          >
            {runningNow ? '处理中…' : '立即处理一条'}
          </button>
          <button
            type="button"
            className="rounded-full border border-sky-300 bg-white px-3 py-1.5 text-[11px] font-black text-sky-700 disabled:opacity-60"
            onClick={() => void refresh()}
            disabled={runningNow}
          >
            刷新
          </button>
        </div>
      </div>
    </div>
  );
}
