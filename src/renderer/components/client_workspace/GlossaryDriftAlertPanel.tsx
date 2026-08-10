import React, { useEffect, useState } from 'react';

import {
  listGlossaryDriftAlerts,
  resolveGlossaryDriftAlert,
  type GlossaryDriftAlertRecord,
} from '../../lib/api';

type GlossaryDriftAlertPanelProps = {
  clientId: string;
  refreshKey?: number;
};

const SEVERITY_LABEL: Record<string, string> = {
  high: '严重',
  medium: '中',
  low: '低',
};

/** P-A.4: 字典权威值漂移告警
 *
 * 当新文件抽出的事实和字典 verified 值不一致时, 这里显示。
 * 优先级**高于** ContradictionAlertPanel — 因为字典 verified 是用户已审过的
 * 金标准，新文件来"挑战"它需要用户重新决策。
 *
 * 操作:
 * - 「保持字典」(dismiss) — 字典权威值不变, 新文件值进归档
 * - 「用新值更新字典」(update_glossary) — 新文件值覆盖字典 verified 值
 */
export function GlossaryDriftAlertPanel({ clientId, refreshKey = 0 }: GlossaryDriftAlertPanelProps) {
  const [alerts, setAlerts] = useState<GlossaryDriftAlertRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [busyId, setBusyId] = useState<string>('');

  useEffect(() => {
    let cancelled = false;
    if (!clientId) {
      setAlerts([]);
      return;
    }
    setLoading(true);
    setError(null);
    listGlossaryDriftAlerts(clientId, 'pending')
      .then((result) => {
        if (cancelled) return;
        setAlerts(result.alerts);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : '加载字典漂移告警失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [clientId, refreshKey, reload]);

  const handleResolve = async (alert: GlossaryDriftAlertRecord, action: 'update_glossary' | 'dismiss') => {
    setBusyId(alert.id);
    try {
      await resolveGlossaryDriftAlert(clientId, alert.id, action);
      setReload((v) => v + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败');
    } finally {
      setBusyId('');
    }
  };

  if (!clientId) return null;
  if (alerts.length === 0) {
    if (error) {
      return <p className="text-[11px] text-rose-600">字典漂移告警加载失败：{error}</p>;
    }
    return null;
  }

  return (
    <div className="space-y-3 rounded-3xl border border-orange-200 bg-orange-50/30 p-4">
      <div className="space-y-1">
        <div className="flex items-center justify-between gap-2">
          <p className="text-[12px] font-black text-orange-700">
            🚨 字典权威值漂移 · 新文件给出了和字典 verified 值不同的数值
          </p>
          <span className="rounded-full bg-orange-200 px-2 py-0.5 text-[10px] font-bold text-orange-900">
            {alerts.length} 条待决策
          </span>
        </div>
        <p className="text-[10px] font-semibold leading-4 text-slate-500">
          💡 字典 verified 值是你已审过的金标准，新文件来挑战它。你来定哪个对：
          保持字典 / 用新值更新字典。
        </p>
      </div>

      <div className="space-y-2">
        {alerts.map((alert) => {
          const busy = busyId === alert.id;
          return (
            <div
              key={alert.id}
              className={`rounded-2xl border px-3 py-2.5 ${
                alert.severity === 'high'
                  ? 'border-orange-300 bg-white'
                  : alert.severity === 'medium'
                  ? 'border-amber-200 bg-amber-50/40'
                  : 'border-slate-200 bg-white'
              }`}
            >
              {/* 标题：term · attribute */}
              <div className="flex items-baseline justify-between gap-2 mb-2">
                <div className="text-[13px] font-bold text-slate-800">
                  <span className="text-orange-700">{alert.term}</span>
                  <span className="mx-1 text-slate-400">·</span>
                  <span className="text-orange-700">{alert.attribute_name}</span>
                </div>
                <span
                  className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold ${
                    alert.severity === 'high'
                      ? 'bg-orange-200 text-orange-900'
                      : alert.severity === 'medium'
                      ? 'bg-amber-100 text-amber-800'
                      : 'bg-slate-100 text-slate-600'
                  }`}
                >
                  {SEVERITY_LABEL[alert.severity] || '一般'}
                </span>
              </div>

              {/* 字典权威值 vs 新文件值 */}
              <div className="grid grid-cols-2 gap-2 mb-3">
                <div className="rounded-md border border-emerald-200 bg-emerald-50/60 px-2.5 py-1.5">
                  <p className="text-[9px] font-bold text-emerald-700 mb-0.5">字典权威值（已审）</p>
                  <p className="text-[12px] font-bold text-emerald-800 break-all">{alert.verified_value_text}</p>
                  {(alert.scope || alert.as_of_date) && (
                    <p className="text-[10px] text-slate-500 mt-0.5">
                      {alert.scope && <>scope: {alert.scope}</>}
                      {alert.scope && alert.as_of_date && ' · '}
                      {alert.as_of_date && <>截至 {alert.as_of_date}</>}
                    </p>
                  )}
                </div>
                <div className="rounded-md border border-sky-200 bg-sky-50/60 px-2.5 py-1.5">
                  <p className="text-[9px] font-bold text-sky-700 mb-0.5">新文件给出</p>
                  <p className="text-[12px] font-bold text-sky-800 break-all">{alert.new_value_text}</p>
                </div>
              </div>

              {/* 操作 */}
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void handleResolve(alert, 'dismiss')}
                  className="rounded-md bg-emerald-100 px-2 py-1 text-[11px] font-bold text-emerald-800 hover:bg-emerald-200 disabled:opacity-50"
                  title="字典 verified 值不变；新文件值进归档"
                >
                  保持字典
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void handleResolve(alert, 'update_glossary')}
                  className="rounded-md bg-orange-200 px-2 py-1 text-[11px] font-bold text-orange-900 hover:bg-orange-300 disabled:opacity-50"
                  title="新文件值覆盖字典 verified 值（认为字典过时了）"
                >
                  用新值更新字典
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
