import { useEffect, useState } from 'react';
import { ArrowUpRight, ArrowDownRight, Minus, ArrowRight } from 'lucide-react';
import { getDepartmentSignals } from '../../lib/api';
import type {
  DepartmentScoreRow,
  DepartmentSignalsResponse,
  ExecutiveDecision,
  ExecutiveHealthIndicator,
} from '../../../shared/types';

interface Props {
  weekLabel?: string | null;
  perspective: 'organization' | 'department' | 'mine';
  departmentId?: string | null;
}

// ─── HealthIndicator ────────────────────────────────────────────────────────
// Stripe/Mercury-style large-number indicator. No card border, divider-only.

function HealthIndicator({ indicator }: { indicator: ExecutiveHealthIndicator }) {
  const trend = indicator.trendDirection;
  const accent = indicator.accent;

  const trendColor =
    accent === 'success'
      ? 'text-emerald-600'
      : accent === 'danger'
        ? 'text-rose-600'
        : accent === 'warning'
          ? 'text-amber-600'
          : 'text-gray-400';

  const accentLineColor =
    accent === 'success'
      ? 'bg-emerald-500'
      : accent === 'danger'
        ? 'bg-rose-500'
        : accent === 'warning'
          ? 'bg-amber-500'
          : 'bg-transparent';

  const TrendIcon =
    trend === 'up' ? ArrowUpRight : trend === 'down' ? ArrowDownRight : Minus;

  return (
    <div className="flex flex-col">
      <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">
        {indicator.label}
      </p>
      <div className="mt-3 flex items-baseline gap-1.5">
        <span className="text-[44px] leading-none font-light tracking-tight text-gray-900">
          {indicator.valueText}
        </span>
        {indicator.unitText && (
          <span className="text-[20px] leading-none font-light text-gray-400">
            {indicator.unitText}
          </span>
        )}
      </div>
      {/* 状态锚线：克制的视觉 highlight，仅在异常/正常时显示 */}
      <div className={`mt-2 h-[2px] w-8 rounded-full ${accentLineColor}`} />
      <div className="mt-2 flex items-center gap-1.5 min-h-[16px]">
        {indicator.deltaText && (
          <>
            <TrendIcon size={12} className={trendColor} strokeWidth={2.5} />
            <span className={`text-[11px] font-medium ${trendColor}`}>{indicator.deltaText}</span>
          </>
        )}
      </div>
      {indicator.helperText && (
        <p className="mt-1 text-[11px] text-gray-400">{indicator.helperText}</p>
      )}
    </div>
  );
}

// ─── DecisionCard ────────────────────────────────────────────────────────────
// Large number index + sectioned 三段式 (situation / decision / cost).

function DecisionCard({ decision }: { decision: ExecutiveDecision }) {
  const accent =
    decision.severity === 'critical'
      ? {
          line: 'bg-rose-500',
          text: 'text-rose-700',
          tagBg: 'bg-rose-50 ring-rose-200',
          tag: '关键',
        }
      : decision.severity === 'important'
        ? {
            line: 'bg-amber-500',
            text: 'text-amber-700',
            tagBg: 'bg-amber-50 ring-amber-200',
            tag: '重要',
          }
        : {
            line: 'bg-blue-500',
            text: 'text-blue-700',
            tagBg: 'bg-blue-50 ring-blue-200',
            tag: '建议',
          };

  const rankText = String(decision.rank).padStart(2, '0');

  return (
    <div className="group relative">
      <div className={`absolute left-0 top-2 bottom-2 w-[2px] rounded-full ${accent.line}`} />
      <div className="pl-8">
        <div className="flex items-start justify-between gap-6 mb-5">
          <div className="flex items-start gap-5 min-w-0 flex-1">
            <span className="text-[42px] leading-none font-extralight tracking-tighter text-gray-200">
              {rankText}
            </span>
            <div className="flex-1 min-w-0 pt-1">
              <span className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-[0.14em] ring-1 ${accent.text} ${accent.tagBg}`}>
                {accent.tag}
              </span>
              <h3 className="mt-2 text-[19px] font-semibold leading-snug text-gray-900">
                {decision.title}
              </h3>
            </div>
          </div>
        </div>

        <dl className="space-y-4 ml-14">
          <DecisionLine label="现状" body={decision.situation} />
          <DecisionLine label="决策" body={decision.decision} emphasis />
          <DecisionLine label="若不行动" body={decision.cost} subdued />
        </dl>

        {decision.actionLabel && (
          <div className="ml-14 mt-5">
            <button
              type="button"
              className="inline-flex items-center gap-1.5 rounded-md bg-gray-900 px-3.5 py-2 text-[12px] font-medium text-white shadow-sm transition-all hover:bg-gray-700 hover:shadow"
            >
              <span>{decision.actionLabel}</span>
              <ArrowRight size={12} strokeWidth={2.2} />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function DecisionLine({
  label,
  body,
  emphasis = false,
  subdued = false,
}: {
  label: string;
  body: string;
  emphasis?: boolean;
  subdued?: boolean;
}) {
  return (
    <div className="grid grid-cols-[80px_1fr] gap-x-6 items-baseline">
      <dt
        className={`text-[10px] font-bold uppercase tracking-[0.18em] ${
          subdued ? 'text-gray-300' : 'text-gray-400'
        }`}
      >
        {label}
      </dt>
      <dd
        className={`text-[14px] leading-relaxed ${
          emphasis
            ? 'text-gray-900 font-medium'
            : subdued
              ? 'text-gray-500'
              : 'text-gray-700'
        }`}
      >
        {body}
      </dd>
    </div>
  );
}

// ─── DepartmentScoreboard ────────────────────────────────────────────────────
// Horizontal comparison: 1 row per metric, 1 column per department.

function ScoreBar({ value, max = 100, accent }: { value: number; max?: number; accent: string }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  const barColor =
    accent === 'success' ? 'bg-emerald-500' : accent === 'danger' ? 'bg-rose-500' : 'bg-gray-900';
  return (
    <div className="flex items-center gap-3">
      <div className="flex-1 h-[3px] bg-gray-100 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${barColor} transition-all duration-700`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-[13px] font-medium text-gray-900 tabular-nums w-10 text-right">
        {value}
      </span>
    </div>
  );
}

function scoreAccent(score: number, threshold: { good: number; bad: number }): string {
  if (score >= threshold.good) return 'success';
  if (score < threshold.bad) return 'danger';
  return 'neutral';
}

function DepartmentScoreboard({ rows }: { rows: DepartmentScoreRow[] }) {
  if (rows.length === 0) return null;

  const metrics: Array<{
    key: keyof DepartmentScoreRow;
    label: string;
    max: number;
    threshold: { good: number; bad: number };
    suffix?: string;
  }> = [
    { key: 'valueProductionScore', label: '价值产出', max: 100, threshold: { good: 60, bad: 30 } },
    {
      key: 'fulfillmentRatePct',
      label: '本周履约',
      max: 100,
      threshold: { good: 70, bad: 30 },
      suffix: '%',
    },
    {
      key: 'monthlyProgressPct',
      label: '月目标进度',
      max: 100,
      threshold: { good: 50, bad: 20 },
      suffix: '%',
    },
    {
      key: 'humanEfficiencyScore',
      label: '人力效率',
      max: 100,
      threshold: { good: 70, bad: 40 },
      suffix: '',
    },
  ];

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse">
        <thead>
          <tr className="border-b border-gray-100">
            <th className="w-32 py-4 text-left text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">
              指标
            </th>
            {rows.map((row) => {
              const statusDot =
                row.status === 'abnormal'
                  ? 'bg-rose-500'
                  : row.status === 'tight'
                    ? 'bg-amber-500'
                    : 'bg-emerald-500';
              return (
                <th key={row.departmentId} className="py-4 px-6 text-left">
                  <div className="flex items-center gap-2">
                    <span className={`h-1.5 w-1.5 rounded-full ${statusDot}`} />
                    <span className="text-[14px] font-semibold text-gray-900">{row.departmentName}</span>
                  </div>
                  <div className="mt-0.5 ml-3.5 text-[11px] text-gray-400">{row.leaderName || '未指定负责人'}</div>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {metrics.map((m) => (
            <tr key={m.key} className="border-b border-gray-50">
              <td className="py-5 text-[12px] font-medium text-gray-500">{m.label}</td>
              {rows.map((row) => {
                const value = Number(row[m.key] ?? 0);
                return (
                  <td key={row.departmentId} className="py-5 px-6">
                    <ScoreBar value={value} max={m.max} accent={scoreAccent(value, m.threshold)} />
                  </td>
                );
              })}
            </tr>
          ))}
          <tr>
            <td className="pt-5 text-[10px] font-bold uppercase tracking-[0.18em] text-gray-300">
              一线读数
            </td>
            {rows.map((row) => (
              <td key={row.departmentId} className="pt-5 px-6">
                <p className="text-[12px] leading-relaxed text-gray-500">
                  {row.headlineInsight || '—'}
                </p>
              </td>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  );
}

// ─── Main view ───────────────────────────────────────────────────────────────

export function DepartmentSignalsView({ weekLabel, perspective, departmentId }: Props) {
  const [data, setData] = useState<DepartmentSignalsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const resp = await getDepartmentSignals({ weekLabel, perspective, departmentId });
        if (!cancelled) setData({
          ...resp,
          healthIndicators: Array.isArray(resp.healthIndicators) ? resp.healthIndicators : [],
          executiveDecisions: Array.isArray(resp.executiveDecisions) ? resp.executiveDecisions : [],
          departmentScoreboard: Array.isArray(resp.departmentScoreboard) ? resp.departmentScoreboard : [],
          actionAlerts: Array.isArray(resp.actionAlerts) ? resp.actionAlerts : [],
          oneOnOneSuggestions: Array.isArray(resp.oneOnOneSuggestions) ? resp.oneOnOneSuggestions : [],
          departmentSnapshots: Array.isArray(resp.departmentSnapshots) ? resp.departmentSnapshots : [],
        });
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [weekLabel, perspective, departmentId]);

  if (loading && !data) {
    return (
      <div className="py-32 text-center text-[13px] text-gray-400">载入经营驾驶舱...</div>
    );
  }

  if (error) {
    return (
      <div className="py-16 max-w-md mx-auto text-center">
        <p className="text-[13px] font-semibold text-rose-700">驾驶舱暂时不可用</p>
        <p className="mt-2 text-[12px] text-gray-500">{error}</p>
      </div>
    );
  }

  if (!data) return null;

  const hasContent =
    data.healthIndicators.length > 0 ||
    data.executiveDecisions.length > 0 ||
    data.departmentScoreboard.length > 0;

  if (!hasContent) {
    return (
      <div className="py-32 text-center max-w-md mx-auto">
        <p className="text-[14px] font-semibold text-gray-700">本周经营驾驶舱无信号</p>
        <p className="mt-3 text-[12px] leading-relaxed text-gray-400">
          建立部门月计划、并把任务挂到计划项上，系统会自动开始计算经营健康度。
        </p>
      </div>
    );
  }

  return (
    <div className="max-w-6xl space-y-16 pt-10 pb-20">
      {/* L1 · Health indicators */}
      {data.healthIndicators.length > 0 && (
        <section>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-x-10 gap-y-8">
            {data.healthIndicators.map((h) => (
              <HealthIndicator key={h.key} indicator={h} />
            ))}
          </div>
        </section>
      )}

      {/* L2 · Executive decisions */}
      {data.executiveDecisions.length > 0 && (
        <section>
          <div className="mb-8 flex items-baseline justify-between">
            <div>
              <h2 className="text-[20px] font-light text-gray-900 tracking-tight">本周经营决策</h2>
              <p className="mt-1 text-[12px] text-gray-400">
                按价值杠杆排序的 {data.executiveDecisions.length} 项行动建议
              </p>
            </div>
          </div>
          <div className="space-y-10">
            {data.executiveDecisions.map((d) => (
              <DecisionCard key={d.id} decision={d} />
            ))}
          </div>
        </section>
      )}

      {/* L3 · Department scoreboard */}
      {data.departmentScoreboard.length > 0 && (
        <section>
          <div className="mb-6 flex items-baseline justify-between">
            <div>
              <h2 className="text-[20px] font-light text-gray-900 tracking-tight">部门经营效率</h2>
              <p className="mt-1 text-[12px] text-gray-400">横向对比各部门本周经营表现</p>
            </div>
          </div>
          <DepartmentScoreboard rows={data.departmentScoreboard} />
        </section>
      )}
    </div>
  );
}
