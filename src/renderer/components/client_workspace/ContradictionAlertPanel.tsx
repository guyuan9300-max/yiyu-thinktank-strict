import React, { useEffect, useMemo, useState } from 'react';

import type { FactContradiction } from '../../../shared/types';
import { getClientContradictions, reviewContradiction } from '../../lib/api';

type ContradictionAlertPanelProps = {
  clientId: string;
  refreshKey?: number;
};

const SEVERITY_ACCENT: Record<string, string> = {
  high: 'before:bg-rose-400',
  medium: 'before:bg-amber-400',
  low: 'before:bg-gray-300',
};

const SEVERITY_LABEL: Record<string, string> = {
  high: '高',
  medium: '中',
  low: '低',
};

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return '时间未知';
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return '';
  const diff = Date.now() - parsed.getTime();
  const day = 24 * 60 * 60 * 1000;
  if (diff < day) return '今天';
  if (diff < 7 * day) return `${Math.floor(diff / day)} 天前`;
  if (diff < 30 * day) return `${Math.floor(diff / (7 * day))} 周前`;
  return `${Math.floor(diff / (30 * day))} 个月前`;
}

function formatBytes(bytes: number | null | undefined): string {
  if (!bytes || bytes <= 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function pairKey(item: FactContradiction): string {
  const a = item.docAOriginalPath || item.docAFileName || item.factAId;
  const b = item.docBOriginalPath || item.docBFileName || item.factBId;
  return [a, b].sort().join('|||');
}

interface DocSide {
  fileName: string | null | undefined;
  importedAt: string | null | undefined;
  sizeBytes: number | null | undefined;
  path: string | null | undefined;
}

interface PairGroup {
  key: string;
  docA: DocSide;
  docB: DocSide;
  items: FactContradiction[];
  severity: 'low' | 'medium' | 'high';
}

function highestSeverity(items: FactContradiction[]): 'low' | 'medium' | 'high' {
  if (items.some((it) => it.severity === 'high')) return 'high';
  if (items.some((it) => it.severity === 'medium')) return 'medium';
  return 'low';
}

function groupByPair(items: FactContradiction[]): PairGroup[] {
  const map = new Map<string, FactContradiction[]>();
  for (const it of items) {
    const key = pairKey(it);
    const list = map.get(key) ?? [];
    list.push(it);
    map.set(key, list);
  }
  const groups: PairGroup[] = [];
  for (const [key, list] of map) {
    const head = list[0];
    groups.push({
      key,
      docA: {
        fileName: head.docAFileName,
        importedAt: head.docAImportedAt,
        sizeBytes: head.docASizeBytes,
        path: head.docAOriginalPath,
      },
      docB: {
        fileName: head.docBFileName,
        importedAt: head.docBImportedAt,
        sizeBytes: head.docBSizeBytes,
        path: head.docBOriginalPath,
      },
      items: list,
      severity: highestSeverity(list),
    });
  }
  return groups;
}

export function ContradictionAlertPanel({ clientId, refreshKey = 0 }: ContradictionAlertPanelProps) {
  const [items, setItems] = useState<FactContradiction[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [expandedDetail, setExpandedDetail] = useState<Record<string, boolean>>({});

  useEffect(() => {
    let cancelled = false;
    if (!clientId) {
      setItems([]);
      return;
    }
    setLoading(true);
    setError(null);
    getClientContradictions(clientId, { status: 'pending', limit: 100 })
      .then((result) => {
        if (cancelled) return;
        setItems(result.contradictions);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : '加载矛盾告警失败';
        setError(message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [clientId, refreshKey, reload]);

  const groups = useMemo(() => groupByPair(items), [items]);

  const handleAcceptSide = async (group: PairGroup, side: 'a' | 'b') => {
    try {
      await Promise.all(
        group.items.map((item) =>
          reviewContradiction(clientId, item.id, {
            reviewStatus: 'resolved',
            acceptedFactId: side === 'a' ? item.factAId : item.factBId,
            resolutionNote: `整份资料以 ${side === 'a' ? '左侧' : '右侧'} 为数据口径`,
          }),
        ),
      );
      setReload((v) => v + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败');
    }
  };

  const handleKeepBothGroup = async (group: PairGroup) => {
    try {
      await Promise.all(
        group.items.map((item) =>
          reviewContradiction(clientId, item.id, {
            reviewStatus: 'dismissed',
            resolutionNote: '两份口径都保留（不视为矛盾）',
          }),
        ),
      );
      setReload((v) => v + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败');
    }
  };

  const handleAcceptPerField = async (item: FactContradiction, side: 'a' | 'b') => {
    try {
      await reviewContradiction(clientId, item.id, {
        reviewStatus: 'resolved',
        acceptedFactId: side === 'a' ? item.factAId : item.factBId,
        resolutionNote: `逐项判定 · 选 ${side === 'a' ? '左' : '右'}`,
      });
      setReload((v) => v + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败');
    }
  };

  if (!clientId) return null;
  if (items.length === 0) {
    if (error) {
      return (
        <p className="text-[11px] text-rose-600">矛盾告警加载失败：{error}</p>
      );
    }
    return null;
  }

  return (
    <div className="space-y-4 rounded-2xl border border-gray-100 bg-white p-5">
      <div className="space-y-1">
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className="text-[9px] font-semibold uppercase tracking-[0.18em] text-rose-500">
              Fact Contradiction
            </div>
            <div className="mt-0.5 text-[13px] font-medium tracking-tight text-gray-800">
              矛盾告警 · 两份资料对同一口径给出了不同的数值
            </div>
          </div>
          <span className="shrink-0 rounded-full px-2.5 py-1 text-[10px] font-medium text-rose-700 ring-1 ring-inset ring-rose-200">
            {groups.length} 组 / {items.length} 个口径
          </span>
        </div>
        <p className="text-[10.5px] leading-snug text-gray-500">
          你选的是<span className="text-gray-700">数据口径</span>,不是删文档 — 文档本身始终保留。
          你的选择只影响 AI 未来回答时<span className="text-gray-700">用哪一份数值</span>做判断。
        </p>
      </div>

      {error && <p className="text-[11px] font-medium text-rose-600">{error}</p>}

      <div className="space-y-3">
        {groups.map((group) => {
          const expanded = Boolean(expandedDetail[group.key]);
          const accent = SEVERITY_ACCENT[group.severity] || SEVERITY_ACCENT.medium;
          return (
            <div
              key={group.key}
              className={`relative rounded-xl bg-white px-4 py-3 ring-1 ring-inset ring-gray-100 before:absolute before:left-0 before:top-3 before:bottom-3 before:w-[3px] before:rounded-r-full ${accent}`}
            >
              <div className="mb-2 flex items-center justify-between gap-2">
                <p className="text-[11.5px] font-medium text-gray-700">
                  这两份资料在 <span className="text-rose-700 font-semibold">{group.items.length}</span> 个口径上有差异
                  <span className="ml-2 text-[10px] text-gray-400">严重度 {SEVERITY_LABEL[group.severity]}</span>
                </p>
              </div>

              <ul className="space-y-2">
                {group.items.slice(0, expanded ? group.items.length : 3).map((it) => (
                  <li
                    key={it.id}
                    className="rounded-lg bg-[#FAFAFA] px-3 py-2 text-[11px] ring-1 ring-inset ring-gray-100"
                  >
                    <div className="mb-1.5 text-[12px] font-medium text-gray-800">
                      {it.subjectText} · {it.attribute}
                    </div>
                    <div className="grid grid-cols-2 items-center gap-2">
                      <div className="flex min-w-0 items-center justify-between gap-2 rounded-md bg-white px-2.5 py-1.5 ring-1 ring-inset ring-emerald-200/70">
                        <button
                          type="button"
                          disabled={!group.docA.path}
                          onClick={() => {
                            if (group.docA.path) {
                              void window.yiyuWorkbench.openPath(group.docA.path).catch(() => undefined);
                            }
                          }}
                          className="cursor-pointer truncate text-left text-[10px] text-gray-500 hover:text-emerald-700 hover:underline disabled:cursor-not-allowed disabled:no-underline"
                          title={group.docA.path ? `点击打开:${group.docA.fileName || ''}` : (group.docA.fileName || '')}
                        >
                          {group.docA.fileName || '资料 A'}
                        </button>
                        <span className="shrink-0 font-semibold text-emerald-700">
                          {it.valueA}
                        </span>
                      </div>
                      <div className="flex min-w-0 items-center justify-between gap-2 rounded-md bg-white px-2.5 py-1.5 ring-1 ring-inset ring-sky-200/70">
                        <span className="shrink-0 font-semibold text-sky-700">
                          {it.valueB}
                        </span>
                        <button
                          type="button"
                          disabled={!group.docB.path}
                          onClick={() => {
                            if (group.docB.path) {
                              void window.yiyuWorkbench.openPath(group.docB.path).catch(() => undefined);
                            }
                          }}
                          className="cursor-pointer truncate text-right text-[10px] text-gray-500 hover:text-sky-700 hover:underline disabled:cursor-not-allowed disabled:no-underline"
                          title={group.docB.path ? `点击打开:${group.docB.fileName || ''}` : (group.docB.fileName || '')}
                        >
                          {group.docB.fileName || '资料 B'}
                        </button>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>

              {group.items.length > 3 && (
                <button
                  type="button"
                  className="mt-2 text-[10px] font-medium text-gray-500 hover:text-gray-700"
                  onClick={() =>
                    setExpandedDetail((m) => ({ ...m, [group.key]: !m[group.key] }))
                  }
                >
                  {expanded ? '收起' : `查看全部 ${group.items.length} 个口径`}
                </button>
              )}

              <div className="mt-3 grid grid-cols-3 gap-1.5">
                <button
                  type="button"
                  className="rounded-md bg-emerald-50 px-2 py-1.5 text-[11px] font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200 hover:bg-emerald-100/70 transition-colors"
                  onClick={() => void handleAcceptSide(group, 'a')}
                  title="把这份资料里全部 N 个数值都设为数据中心当前真值;另一份对应数值进入归档"
                >
                  以 A 为准 <span className="opacity-60">({group.items.length})</span>
                </button>
                <button
                  type="button"
                  className="rounded-md bg-emerald-50 px-2 py-1.5 text-[11px] font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200 hover:bg-emerald-100/70 transition-colors"
                  onClick={() => void handleAcceptSide(group, 'b')}
                  title="把这份资料里全部 N 个数值都设为数据中心当前真值;另一份对应数值进入归档"
                >
                  以 B 为准 <span className="opacity-60">({group.items.length})</span>
                </button>
                <button
                  type="button"
                  className="rounded-md bg-white px-2 py-1.5 text-[11px] font-medium text-gray-700 ring-1 ring-inset ring-gray-200 hover:bg-gray-50 transition-colors"
                  onClick={() => void handleKeepBothGroup(group)}
                  title="两份口径都保留(视为合理并存,AI 回答时会同时引用两份)"
                >
                  两份都保留
                </button>
              </div>

              {group.items.length > 1 && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-[10px] font-medium text-gray-500 hover:text-gray-700">
                    分别判定每个口径…
                  </summary>
                  <div className="mt-2 space-y-1.5">
                    {group.items.map((it) => (
                      <div
                        key={`detail-${it.id}`}
                        className="rounded-md bg-[#FAFAFA] px-2 py-1.5 ring-1 ring-inset ring-gray-100"
                      >
                        <p className="text-[10.5px] font-medium text-gray-600">
                          {it.subjectText} · {it.attribute}
                        </p>
                        <div className="mt-1 flex gap-2">
                          <button
                            type="button"
                            className="flex-1 rounded-md bg-emerald-50 px-2 py-1 text-[10px] font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200 hover:bg-emerald-100/70"
                            onClick={() => void handleAcceptPerField(it, 'a')}
                          >
                            A: {it.valueA}
                          </button>
                          <button
                            type="button"
                            className="flex-1 rounded-md bg-sky-50 px-2 py-1 text-[10px] font-medium text-sky-700 ring-1 ring-inset ring-sky-200 hover:bg-sky-100/70"
                            onClick={() => void handleAcceptPerField(it, 'b')}
                          >
                            B: {it.valueB}
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                </details>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
