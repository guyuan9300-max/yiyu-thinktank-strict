/**
 * v2.2 F1.6 (B 门最小推进) · ClientFactBadge
 *
 * 服务: V2.2_NORTH_STAR.md
 *  - N1: 跨 view 一致展示当前客户的基础事实数字
 *  - N2: 通过 useClientContext 读 L2 ClientFactBundle 单一通道, 不重复 fetch
 *
 * 用法 (任何在 ClientFactProvider 子树里的 view 都能用):
 *   <ClientFactBadge />        // 默认显示 name + 4 个 counts
 *   <ClientFactBadge variant="compact" />  // 紧凑版只显示 name + total
 *   <ClientFactBadge fields={['eventLines', 'tasks']} />  // 自定义字段
 *
 * 设计选择:
 * - 默认 isLoading 时显示骨架, 不显示零值数字 (防误导)
 * - 当前 client 不存在或 bundle 还没拉时, 默认 render null (优雅降级)
 * - 不接受 clientId props — 跟 ClientFactProvider 一对一, 保证跨 view 一致
 */
import React from 'react';

import { useClientContext } from '../../contexts/ClientFactContext';

export type FactField = 'eventLines' | 'tasks' | 'commitments' | 'dnaDocs' | 'atomicFacts';

interface ClientFactBadgeProps {
  /** 紧凑版 (单行) vs 标准版 (多个数字) */
  variant?: 'compact' | 'standard';
  /** 自定义要显示的字段 (默认全部) */
  fields?: FactField[];
  /** 额外类名 (允许调用方覆盖样式) */
  className?: string;
}

const DEFAULT_FIELDS: FactField[] = ['eventLines', 'tasks', 'commitments', 'dnaDocs'];

const FIELD_LABELS: Record<FactField, string> = {
  eventLines: '事件线',
  tasks: '任务',
  commitments: '承诺',
  dnaDocs: 'DNA 文档',
  atomicFacts: '事实',
};

/**
 * 从 ClientFactBundle.counts 安全取数, 缺字段返回 0。
 * 导出供测试用 + 其他组件复用同样映射。
 */
export const COUNTS_KEY_MAP: Record<FactField, string> = {
  eventLines: 'event_lines',
  tasks: 'tasks',
  commitments: 'commitments',
  dnaDocs: 'dna_documents',
  atomicFacts: 'atomic_facts',
};

export function readCount(counts: Record<string, unknown> | null | undefined, field: FactField): number {
  if (!counts || typeof counts !== 'object') return 0;
  const raw = (counts as Record<string, unknown>)[COUNTS_KEY_MAP[field]];
  if (typeof raw === 'number' && Number.isFinite(raw)) return raw;
  return 0;
}

export function ClientFactBadge({
  variant = 'standard',
  fields = DEFAULT_FIELDS,
  className = '',
}: ClientFactBadgeProps): React.ReactElement | null {
  const { bundle, isLoading, error } = useClientContext();

  // 没 currentClientId / 还没拉 / 出错 → 优雅降级
  if (!bundle) {
    if (isLoading) {
      return (
        <span
          className={`inline-flex items-center gap-2 text-[12px] text-slate-400 ${className}`}
          aria-busy="true"
        >
          <span className="inline-block w-20 h-3 bg-slate-100 rounded animate-pulse" />
        </span>
      );
    }
    if (error) {
      return (
        <span className={`inline-flex items-center text-[12px] text-rose-500 ${className}`}>
          加载失败
        </span>
      );
    }
    return null;
  }

  const clientName = bundle.client?.name || '未命名客户';

  if (variant === 'compact') {
    const total = fields.reduce((sum, f) => sum + readCount(bundle.counts, f), 0);
    return (
      <span
        className={`inline-flex items-center gap-1.5 text-[12px] text-slate-600 ${className}`}
        title={`${clientName} · 共 ${total} 条事实`}
      >
        <span className="font-semibold text-slate-700">{clientName}</span>
        <span className="text-slate-400">·</span>
        <span className="text-slate-500">{total}</span>
      </span>
    );
  }

  return (
    <div
      className={`inline-flex items-center gap-3 text-[12px] text-slate-600 ${className}`}
      data-testid="client-fact-badge"
    >
      <span className="font-semibold text-slate-700">{clientName}</span>
      {fields.map((f) => (
        <span key={f} className="inline-flex items-center gap-1">
          <span className="text-slate-400">{FIELD_LABELS[f]}</span>
          <span className="font-semibold text-slate-700">{readCount(bundle.counts, f)}</span>
        </span>
      ))}
    </div>
  );
}

export default ClientFactBadge;
