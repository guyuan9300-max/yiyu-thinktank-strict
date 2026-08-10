/**
 * AiDelegationsCard · 个人复盘里的"AI 委托"区块 (顾源源 5/25 PM · path C)
 *
 * 双重归属设计:
 *   - 用户视角 (本组件): "本周你布置给 AI 同事的工作"
 *   - 机器人视角 (单独 UI): "庆华本周接的指令"
 *
 * 数据源: GET /api/v2/ui/local/users/{user_id}/ai-delegations?week=...
 */
import { useEffect, useState } from 'react';
import { Bot, CheckCircle2, AlertCircle, Loader2, ExternalLink } from 'lucide-react';
import { getUserAiDelegations, type UserAiDelegationsResponse } from '../../lib/api';

type Props = {
  userId: string;
  weekLabel?: string;  // ISO '2026-W22' 或 '2026-05-25'
};

export function AiDelegationsCard({ userId, weekLabel }: Props) {
  const [data, setData] = useState<UserAiDelegationsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    getUserAiDelegations(userId, weekLabel)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : '加载失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [userId, weekLabel]);

  if (loading) {
    return (
      <div className="rounded-lg border border-gray-200 px-4 py-6 flex items-center gap-2 text-[12px] text-gray-500">
        <Loader2 size={14} className="animate-spin" /> 加载 AI 委托情况...
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-[12px] text-red-700">
        ⚠ {error}
      </div>
    );
  }
  if (!data || data.summary.total_plans === 0) {
    return (
      <div className="rounded-lg border border-gray-200 px-4 py-4 text-[12px] text-gray-500">
        <div className="flex items-center gap-2 text-[12.5px] font-medium text-gray-700 mb-1">
          <Bot size={13} className="text-[#5B7BFE]" /> 本周 AI 委托
        </div>
        本周还没有给 AI 同事布置任务. 用顶部 <span className="text-[#5B7BFE]">✦ AI</span> 按钮发指令.
      </div>
    );
  }

  const s = data.summary;
  const scorePct = Math.round(data.ai_collaboration_score * 100);

  return (
    <div className="rounded-lg border border-[#5B7BFE]/25 bg-gradient-to-br from-[#5B7BFE]/[0.04] to-transparent px-4 py-3">
      {/* Header */}
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="flex items-center gap-1.5">
          <Bot size={14} className="text-[#5B7BFE]" />
          <span className="text-[13px] font-medium text-gray-900">本周 AI 委托</span>
          <span className="text-[10.5px] text-gray-500">
            {data.week_start.slice(0, 10)} → {data.week_end.slice(0, 10)}
          </span>
        </div>
        <div className="text-[10.5px] text-gray-500">
          AI 协同指数{' '}
          <span className="font-medium text-[#3B5BCF]">
            {scorePct}%
          </span>
        </div>
      </div>

      {/* 4 stats */}
      <div className="grid grid-cols-4 gap-2 mb-3">
        <Stat label="布置" value={s.total_plans} color="text-gray-700" />
        <Stat label="执行中" value={s.executing} color="text-blue-600" />
        <Stat label="完成" value={s.completed} color="text-emerald-600" icon={<CheckCircle2 size={10} />} />
        <Stat label="失败" value={s.failed} color="text-amber-600" icon={s.failed > 0 ? <AlertCircle size={10} /> : undefined} />
      </div>

      {/* plan list */}
      <div className="space-y-1.5">
        {data.plans.slice(0, 6).map((p) => (
          <div
            key={p.plan_id}
            className="flex items-start gap-2 px-2 py-1.5 rounded-md bg-white/60 hover:bg-white border border-gray-100"
          >
            <StatusDot status={p.execution_status} />
            <div className="flex-1 min-w-0">
              <div className="text-[12px] text-gray-800 truncate">
                {p.plan_title || '(无标题)'}
              </div>
              <div className="mt-0.5 flex items-center gap-2 text-[10px] text-gray-500">
                <span>{p.bot_name || '(无 bot)'}</span>
                <span>·</span>
                <span>子任务 {p.success_count}/{p.subtask_count}</span>
                <span>·</span>
                <span>{(p.created_at || '').slice(5, 16).replace('T', ' ')}</span>
              </div>
            </div>
            <span className="text-[9.5px] text-gray-400 shrink-0">
              {execStatusLabel(p.execution_status)}
            </span>
          </div>
        ))}
        {data.plans.length > 6 && (
          <div className="text-[10.5px] text-gray-500 text-center pt-1">
            还有 {data.plans.length - 6} 个 · 全部见 AI 任务历史
          </div>
        )}
      </div>

      {/* 提示行 */}
      <div className="mt-3 pt-2 border-t border-gray-100 flex items-center justify-between text-[10px] text-gray-500">
        <span>
          你本周手动任务 {data.user_manual_tasks} · 通过 AI 完成 {s.completed}
          {data.user_manual_tasks === 0 && s.completed > 0 && (
            <span className="ml-1 text-[#3B5BCF]">→ 本周产能 100% 来自 AI 协同</span>
          )}
        </span>
        <a href="#" onClick={(e) => e.preventDefault()} className="text-[#5B7BFE] hover:underline flex items-center gap-0.5">
          看 AI 同事视角 <ExternalLink size={10} />
        </a>
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  color,
  icon,
}: {
  label: string;
  value: number;
  color: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="bg-white/70 rounded px-2 py-1.5 text-center">
      <div className={`text-[16px] font-light leading-tight flex items-center justify-center gap-0.5 ${color}`}>
        {icon}
        {value}
      </div>
      <div className="text-[9.5px] text-gray-500 mt-0.5">{label}</div>
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  const color =
    status === 'success' ? 'bg-emerald-500'
    : status === 'failed' ? 'bg-red-500'
    : status === 'partial' ? 'bg-amber-500'
    : status === 'running' || status === 'pending_execute' ? 'bg-blue-500 animate-pulse'
    : 'bg-gray-300';
  return <span className={`w-1.5 h-1.5 rounded-full mt-1.5 shrink-0 ${color}`} />;
}

function execStatusLabel(s: string) {
  switch (s) {
    case 'success': return '完成';
    case 'failed': return '失败';
    case 'partial': return '部分';
    case 'running': return '执行中';
    case 'pending_execute': return '排队';
    case 'not_started': return '待执行';
    default: return s;
  }
}
