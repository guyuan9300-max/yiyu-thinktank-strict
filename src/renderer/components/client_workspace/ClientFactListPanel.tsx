import React, { useMemo, useState } from 'react';

import { useClientFact } from '../../hooks/useClientFact';
import type { AtomicFactRef } from '../../lib/clientFactTypes';

/**
 * S2b-min · 客户事实清单面板
 *
 * 工作台此前只显示矛盾/字典告警(侧面),没有任何组件渲染正面的"客户事实"。
 * 本面板从 L2 ClientFactBundle 的 atomic_facts 渲染事实三元组 + 把握度 + 有效/过期状态
 * + 可展开的原文出处(evidence_text),让用户看到真实、可溯源的客户事实。
 *
 * 自带 clientId 走 useClientFact(不依赖 ClientFactProvider 的挂载位置)。
 */
interface ClientFactListPanelProps {
  clientId: string;
}

const ACTIVE_STATUS = 'active';

function confidenceBadge(c: number): { label: string; cls: string } {
  if (c >= 0.75) return { label: '高', cls: 'text-emerald-700 ring-emerald-200 bg-emerald-50/70' };
  if (c >= 0.5) return { label: '中', cls: 'text-amber-700 ring-amber-200 bg-amber-50/70' };
  return { label: '低', cls: 'text-gray-500 ring-gray-200 bg-gray-50' };
}

export function ClientFactListPanel({ clientId }: ClientFactListPanelProps) {
  const { bundle, isLoading, error } = useClientFact({ clientId });
  const [showStale, setShowStale] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const { active, stale } = useMemo(() => {
    const facts = bundle?.atomic_facts ?? [];
    const a: AtomicFactRef[] = [];
    const s: AtomicFactRef[] = [];
    for (const f of facts) {
      if (f.status === ACTIVE_STATUS) {
        a.push(f);
      } else {
        s.push(f);
      }
    }
    a.sort((x, y) => y.confidence - x.confidence);
    return { active: a, stale: s };
  }, [bundle]);

  if (!clientId) {
    return null;
  }

  const toggle = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const renderRow = (f: AtomicFactRef, dim: boolean) => {
    const conf = confidenceBadge(f.confidence);
    const hasEvidence = !!(f.evidence_text && f.evidence_text.trim());
    const isOpen = expanded.has(f.id);
    return (
      <div
        key={f.id}
        className={`rounded-xl px-3 py-2.5 ring-1 ring-inset ${dim ? 'bg-gray-50/60 ring-gray-100' : 'bg-white ring-gray-100'}`}
      >
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1 leading-5">
            <span className="text-[12px] font-medium text-gray-800">{f.subject_text}</span>
            <span className="mx-1 text-gray-300">·</span>
            <span className="text-[11px] text-gray-500">{f.attribute}</span>
            <span className="mx-0.5 text-gray-300">：</span>
            <span className="text-[12px] text-gray-700">{f.value_text}</span>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            {dim && (
              <span className="rounded-full px-1.5 py-0.5 text-[9px] font-medium text-gray-400 ring-1 ring-inset ring-gray-200">
                已过期
              </span>
            )}
            <span
              className={`rounded-full px-1.5 py-0.5 text-[9px] font-medium ring-1 ring-inset ${conf.cls}`}
              title={`把握度 ${(f.confidence * 100).toFixed(0)}%`}
            >
              {conf.label}
            </span>
          </div>
        </div>
        {hasEvidence && (
          <button
            type="button"
            className="mt-1 text-[10px] text-[#5B7BFE] hover:underline"
            onClick={() => toggle(f.id)}
          >
            {isOpen ? '收起出处' : '看出处'}
          </button>
        )}
        {hasEvidence && isOpen && (
          <p
            className="mt-1 rounded-lg bg-gray-50 px-2.5 py-2 text-[11px] leading-5 text-gray-500"
            title="该事实抽取自的原文片段"
          >
            {f.evidence_text}
          </p>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-3 rounded-2xl border border-gray-100 bg-white p-5">
      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="text-[9px] font-semibold uppercase tracking-[0.18em] text-gray-400">
            Client Facts
          </div>
          <div className="mt-0.5 text-[13px] font-medium tracking-tight text-gray-800">
            客户事实 <span className="ml-1 text-gray-400">{active.length}</span>
          </div>
        </div>
      </div>

      {isLoading && <div className="text-[11px] text-gray-400">加载中…</div>}
      {error && <div className="text-[11px] text-rose-500">加载失败：{error.message}</div>}

      {!isLoading && !error && active.length === 0 && stale.length === 0 && (
        <div className="text-[11px] text-gray-400">该客户暂无已抽取的事实</div>
      )}

      {active.length > 0 && <div className="space-y-2">{active.map((f) => renderRow(f, false))}</div>}

      {stale.length > 0 && (
        <div className="space-y-2">
          <button
            type="button"
            className="text-[10px] font-medium text-gray-400 transition-colors hover:text-gray-600"
            onClick={() => setShowStale((v) => !v)}
          >
            {showStale ? '收起已过期' : `已过期/被取代 ${stale.length} 条`}
          </button>
          {showStale && <div className="space-y-2">{stale.map((f) => renderRow(f, true))}</div>}
        </div>
      )}
    </div>
  );
}
