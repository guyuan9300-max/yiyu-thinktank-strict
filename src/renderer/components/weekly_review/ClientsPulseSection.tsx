/**
 * 本周概览顶部「本周客户脉搏」区块.
 *
 * 设计原则 (Phase 1):
 * - 克制 - 每张卡只放: 客户名 / 阶段 / 关键计数 / topSignal (一句话最显著信号).
 * - 不炫耀后台 - 详细数字 (具体多少 evidence/blocker) 点开进客户主页看.
 * - 突出有动态的 - 静默客户折叠展示.
 */

import { useEffect, useState } from 'react';
import { AlertTriangle, FileText, Layers, AlertCircle } from 'lucide-react';
import {
  getClientsPulseSummary,
  type ClientPulseSummary,
} from '../../lib/api';

interface ClientsPulseSectionProps {
  onOpenClient?: (clientId: string) => void;
}

export function ClientsPulseSection({ onOpenClient }: ClientsPulseSectionProps) {
  const [data, setData] = useState<ClientPulseSummary[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showSilent, setShowSilent] = useState(false);

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    setError(null);
    getClientsPulseSummary()
      .then((result) => {
        if (mounted) setData(result.summaries);
      })
      .catch((err) => {
        if (mounted) {
          setError(err instanceof Error ? err.message : '加载失败');
          setData(null);
        }
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, []);

  if (loading) {
    return (
      <section className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5">
        <div className="text-[11px] font-bold text-gray-300 uppercase tracking-[0.15em] mb-2">
          本周客户脉搏
        </div>
        <div className="text-[13px] text-gray-400">读取中...</div>
      </section>
    );
  }

  if (error || !data) {
    return (
      <section className="bg-white rounded-2xl border border-amber-100 shadow-sm p-5">
        <div className="text-[12px] text-amber-700">客户脉搏暂不可用{error ? `（${error}）` : ''}</div>
      </section>
    );
  }

  const active = data.filter((s) => s.hasActivity);
  const silent = data.filter((s) => !s.hasActivity);

  return (
    <section className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <h3 className="text-[11px] font-bold text-gray-300 uppercase tracking-[0.15em]">
            本周客户脉搏
          </h3>
          <p className="mt-1 text-[12px] text-gray-500">
            {active.length} / {data.length} 个客户本周有动态
          </p>
        </div>
      </div>

      {active.length === 0 ? (
        <div className="rounded-xl bg-gray-50/70 border border-dashed border-gray-200 px-5 py-8 text-center text-[13px] text-gray-400">
          本周所有客户都静默 — 没有新文档 / 新任务 / 新事实
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {active.map((client) => (
            <ClientPulseCard
              key={client.clientId}
              client={client}
              onOpen={onOpenClient}
            />
          ))}
        </div>
      )}

      {silent.length > 0 && (
        <div className="mt-3 pt-3 border-t border-gray-100">
          <button
            type="button"
            onClick={() => setShowSilent((v) => !v)}
            className="text-[11px] font-bold text-gray-400 hover:text-gray-600"
          >
            {showSilent ? '⌃ 收起' : '⌄ 展开'} {silent.length} 个本周无动态的客户
          </button>
          {showSilent && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {silent.map((c) => (
                <button
                  key={c.clientId}
                  type="button"
                  onClick={() => onOpenClient?.(c.clientId)}
                  className="text-[11px] font-medium text-gray-400 px-2 py-1 rounded bg-gray-50 hover:bg-gray-100"
                >
                  {c.clientName} <span className="text-gray-300">· {c.clientStage || '未分类'}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

interface ClientPulseCardProps {
  client: ClientPulseSummary;
  onOpen?: (clientId: string) => void;
}

function ClientPulseCard({ client, onOpen }: ClientPulseCardProps) {
  // 显著信号决定卡片色调
  const hasBlocker = client.currentBlockerCount > 0;
  const hasOverdue = client.overdueTodoCount > 0;
  const borderClass = hasOverdue
    ? 'border-l-rose-400'
    : hasBlocker
      ? 'border-l-amber-400'
      : 'border-l-blue-300';
  const signalClass = hasOverdue
    ? 'text-rose-600'
    : hasBlocker
      ? 'text-amber-700'
      : 'text-blue-600';

  const handleClick = () => {
    if (onOpen) onOpen(client.clientId);
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      className={`text-left w-full border border-gray-100 ${borderClass} border-l-4 rounded-xl bg-white hover:bg-gray-50/50 transition px-4 py-3.5`}
    >
      <div className="flex items-start justify-between gap-2 mb-1.5">
        <div className="min-w-0 flex-1">
          <div className="text-[14px] font-bold text-gray-900 truncate">{client.clientName}</div>
          {client.clientStage && (
            <div className="text-[11px] font-medium text-gray-400 mt-0.5">{client.clientStage}</div>
          )}
        </div>
      </div>

      <div className={`text-[12px] font-bold ${signalClass} mb-2`}>{client.topSignal}</div>

      {/* 紧凑数字行: 只显示非零项 */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] font-bold text-gray-500">
        {client.weeklyNewDocumentCount > 0 && (
          <span className="inline-flex items-center gap-1">
            <FileText size={11} />+{client.weeklyNewDocumentCount} 文档
          </span>
        )}
        {client.weeklyNewTaskCount > 0 && (
          <span className="inline-flex items-center gap-1">
            <Layers size={11} />+{client.weeklyNewTaskCount} 任务
          </span>
        )}
        {client.weeklyNewEvidenceCount > 0 && (
          <span className="inline-flex items-center gap-1 text-emerald-600">
            <AlertCircle size={11} />+{client.weeklyNewEvidenceCount} 事实
          </span>
        )}
        {client.currentBlockerCount > 0 && (
          <span className="inline-flex items-center gap-1 text-amber-700">
            <AlertTriangle size={11} />
            {client.currentBlockerCount} 卡点
          </span>
        )}
        {client.overdueTodoCount > 0 && (
          <span className="inline-flex items-center gap-1 text-rose-600">
            ⏰ {client.overdueTodoCount} 项逾期
          </span>
        )}
      </div>
    </button>
  );
}
