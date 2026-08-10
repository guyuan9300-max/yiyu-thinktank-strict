import React, { useEffect, useMemo, useState } from 'react';
import { ArrowRight, BookOpen, CopyPlus, FileClock, Search, Sparkles, X } from 'lucide-react';

import { getHandbookEntry, markHandbookEntryReused } from '../../lib/api';
import type { GrowthContextLink, HandbookEntry, HandbookEntryDetail, XpLedgerEntry } from '../../../shared/types';

type FlashLevel = 'success' | 'error';

type GrowthAssetLibraryDrawerProps = {
  open: boolean;
  entries: HandbookEntry[];
  recentEntries: XpLedgerEntry[];
  flash: (level: FlashLevel, message: string) => void;
  onClose: () => void;
  onRefresh: () => Promise<void>;
  onOpenComposer: () => void;
  onNavigate?: (tab: string) => void;
  onOpenContext?: (context: GrowthContextLink) => void;
};

function cx(...classNames: Array<string | false | null | undefined>) {
  return classNames.filter(Boolean).join(' ');
}

function formatDateLabel(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date);
}

function sourceLabel(sourceType: string) {
  if (sourceType === 'manual') return '手动沉淀';
  if (sourceType === 'meeting') return '会议结论';
  if (sourceType === 'topic_candidate') return '情报候选';
  if (sourceType === 'task') return '任务复盘';
  if (sourceType === 'analysis') return '分析学习';
  return sourceType || '未分类';
}

function typeLabel(sourceType: string) {
  if (sourceType === 'meeting') return '结论';
  if (sourceType === 'topic_candidate') return '判断';
  if (sourceType === 'analysis') return '方法';
  if (sourceType === 'task') return '复盘';
  return '经验';
}

function isMethodLike(entry: HandbookEntry) {
  const normalized = `${entry.title} ${entry.summary} ${entry.tags.join(' ')}`.toLowerCase();
  return ['模板', '方法', '清单', '复用', '机制'].some((keyword) => normalized.includes(keyword));
}

export function GrowthAssetLibraryDrawer({
  open,
  entries,
  recentEntries,
  flash,
  onClose,
  onRefresh,
  onOpenComposer,
  onNavigate,
  onOpenContext,
}: GrowthAssetLibraryDrawerProps) {
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [markingId, setMarkingId] = useState<string | null>(null);
  const [entryDetail, setEntryDetail] = useState<HandbookEntryDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const sortedEntries = useMemo(
    () => [...entries].sort((left, right) => new Date(right.createdAt).getTime() - new Date(left.createdAt).getTime()),
    [entries],
  );
  const filteredEntries = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return sortedEntries;
    return sortedEntries.filter((entry) => [entry.title, entry.summary, entry.tags.join(' '), sourceLabel(entry.sourceType)].join(' ').toLowerCase().includes(normalized));
  }, [query, sortedEntries]);

  useEffect(() => {
    if (!open) return;
    if (!filteredEntries.length) {
      setSelectedId(null);
      return;
    }
    if (!selectedId || !filteredEntries.some((entry) => entry.id === selectedId)) {
      setSelectedId(filteredEntries[0].id);
    }
  }, [filteredEntries, open, selectedId]);

  const selectedEntry = filteredEntries.find((entry) => entry.id === selectedId) || null;
  const selectedEntryDetail = entryDetail && selectedEntry && entryDetail.id === selectedEntry.id ? entryDetail : null;
  const selectedRelatedEntries = selectedEntryDetail?.relatedLedgerEntries || recentEntries.filter((entry) => entry.handbookEntryId === selectedEntry?.id).slice(0, 4);

  useEffect(() => {
    if (!open || !selectedId) {
      setEntryDetail(null);
      return;
    }
    let cancelled = false;
    const load = async () => {
      setDetailLoading(true);
      try {
        const detail = await getHandbookEntry(selectedId);
        if (!cancelled) {
          setEntryDetail(detail);
        }
      } catch (error) {
        if (!cancelled) {
          setEntryDetail(null);
          flash('error', error instanceof Error ? error.message : '成长资产详情加载失败');
        }
      } finally {
        if (!cancelled) {
          setDetailLoading(false);
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [flash, open, selectedId]);

  const handleMarkReused = async () => {
    const targetEntry = selectedEntryDetail || selectedEntry;
    if (!targetEntry) return;
    setMarkingId(targetEntry.id);
    try {
      const reuseContext = preferredUsageContext;
      const result = await markHandbookEntryReused(targetEntry.id, {
        note: `从经验资产库标记复用：${targetEntry.title}`,
        sourceType: reuseContext?.objectType || targetEntry.sourceObjectType || 'handbook_manual_reuse',
        sourceId: reuseContext?.objectId || targetEntry.sourceObjectId || targetEntry.eventLineId || targetEntry.clientId || targetEntry.id,
        sourceLabel: reuseContext?.label || targetEntry.sourceTitle || targetEntry.title,
        contextSummary: reuseContext?.subtitle || targetEntry.contextSummary || targetEntry.summary,
        linkedContexts: reuseContext ? [reuseContext] : targetEntry.linkedContexts,
      });
      await onRefresh();
      flash('success', result.duplicate ? '本周已经记录过这条复用，未重复加分' : `已记录方法复用，新增 ${result.gainedXp} XP`);
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '记录复用失败');
    } finally {
      setMarkingId(null);
    }
  };

  const preferredUsageContext = useMemo(() => {
    const contexts = selectedEntryDetail?.linkedContexts || selectedEntry?.linkedContexts || [];
    return (
      contexts.find((context) => context.objectType === 'task')
      || contexts.find((context) => context.objectType === 'event_line')
      || contexts.find((context) => context.objectType === 'client')
      || contexts[0]
      || null
    );
  }, [selectedEntry, selectedEntryDetail]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-slate-900/30 backdrop-blur-sm">
      <div className="flex h-full w-full max-w-[1120px] flex-col bg-white shadow-2xl animate-in slide-in-from-right duration-300">
        <div className="flex items-center justify-between border-b border-slate-100 px-6 py-4">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">经验资产</div>
            <h2 className="mt-1 text-[22px] font-semibold tracking-tight text-slate-900">成长手册资产库</h2>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={onOpenComposer}
              className="inline-flex items-center gap-2 rounded-full bg-[#335CFF] px-4 py-2 text-[13px] font-medium text-white shadow-sm transition-colors hover:bg-[#2C50E0]"
            >
              <Sparkles className="h-4 w-4" />
              新增沉淀
            </button>
            <button type="button" onClick={onClose} className="rounded-full p-2 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700">
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[360px_minmax(0,1fr)]">
          <div className="border-r border-slate-100 bg-slate-50/55 p-5">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索标题、摘要或标签"
                className="w-full rounded-2xl border border-slate-200 bg-white py-3 pl-10 pr-4 text-[13px] font-medium text-slate-700 placeholder:text-slate-400 focus:border-[#C9D7FF] focus:outline-none"
              />
            </div>

            <div className="mt-4 space-y-2 overflow-y-auto pb-4">
              {filteredEntries.length ? (
                filteredEntries.map((entry) => (
                  <button
                    key={entry.id}
                    type="button"
                    onClick={() => setSelectedId(entry.id)}
                    className={cx(
                      'w-full rounded-[22px] border p-4 text-left transition',
                      selectedId === entry.id ? 'border-[#C9D7FF] bg-white shadow-sm' : 'border-transparent bg-transparent hover:border-slate-200 hover:bg-white/90',
                    )}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="text-[14px] font-semibold text-slate-900">{entry.title}</div>
                        <div className="mt-2 flex flex-wrap gap-2">
                          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-slate-500">{typeLabel(entry.sourceType)}</span>
                          <span className="rounded-full bg-[#EDF2FF] px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-[#335CFE]">{sourceLabel(entry.sourceType)}</span>
                        </div>
                      </div>
                      <div className="text-[11px] font-medium text-slate-400">{formatDateLabel(entry.createdAt)}</div>
                    </div>
                    <p className="mt-3 line-clamp-2 text-[12px] leading-6 text-slate-500">{entry.summary}</p>
                  </button>
                ))
              ) : (
                <div className="rounded-[22px] border border-dashed border-slate-200 bg-white px-4 py-8 text-center text-[13px] font-medium text-slate-400">
                  当前筛选条件下没有经验资产
                </div>
              )}
            </div>
          </div>

          <div className="min-h-0 overflow-y-auto p-6">
            {selectedEntry ? (
              <div className="space-y-8">
                <div className="flex flex-col gap-4 border-b border-slate-100 pb-6 lg:flex-row lg:items-start lg:justify-between">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[10px] font-medium uppercase tracking-[0.18em] text-slate-500">
                        {typeLabel(selectedEntry.sourceType)}
                      </span>
                      <span className="rounded-full bg-[#EBF0FF] px-2.5 py-1 text-[10px] font-medium uppercase tracking-[0.18em] text-[#335CFE]">
                        {sourceLabel(selectedEntry.sourceType)}
                      </span>
                      {isMethodLike(selectedEntry) ? (
                        <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-[10px] font-medium uppercase tracking-[0.18em] text-emerald-700">可复用方法</span>
                      ) : null}
                    </div>
                    <h3 className="mt-3 text-[28px] font-semibold tracking-tight text-slate-900">{selectedEntry.title}</h3>
                    <p className="mt-3 max-w-3xl text-[14px] leading-7 text-slate-600">{selectedEntry.summary}</p>
                  </div>
                  <div className="flex shrink-0 flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        onClose();
                        if (preferredUsageContext && onOpenContext) {
                          onOpenContext(preferredUsageContext);
                          return;
                        }
                        onNavigate?.('tasks');
                      }}
                      className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-4 py-2 text-[13px] font-medium text-slate-700 transition hover:bg-slate-50"
                    >
                      去任务页使用
                      <ArrowRight className="h-3.5 w-3.5" />
                    </button>
                    {isMethodLike(selectedEntryDetail || selectedEntry) ? (
                      <button
                        type="button"
                        onClick={() => void handleMarkReused()}
                        disabled={markingId === selectedEntry.id}
                        className="inline-flex items-center gap-2 rounded-full bg-[#335CFF] px-4 py-2 text-[13px] font-medium text-white shadow-sm transition hover:bg-[#2C50E0] disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        <CopyPlus className="h-4 w-4" />
                        {markingId === selectedEntry.id ? '记录中...' : '标记本次已复用'}
                      </button>
                    ) : null}
                  </div>
                </div>

                <section className="grid gap-4 lg:grid-cols-3">
                  <div className="rounded-[24px] border border-slate-100 bg-slate-50/70 p-4">
                    <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">沉淀来源</div>
                    <div className="mt-3 text-[13px] font-semibold text-slate-800">{sourceLabel(selectedEntry.sourceType)}</div>
                    <div className="mt-2 text-[12px] leading-6 text-slate-500">
                      {selectedEntryDetail?.sourceTitle || selectedEntryDetail?.contextSummary || '系统会根据来源类型，把这条经验归入会议、复盘、情报或分析资产。'}
                    </div>
                  </div>
                  <div className="rounded-[24px] border border-slate-100 bg-slate-50/70 p-4">
                    <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">标签与能力</div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {selectedEntry.tags.length ? (
                        selectedEntry.tags.map((tag) => (
                          <span key={tag} className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-[11px] font-medium text-slate-600">
                            {tag}
                          </span>
                        ))
                      ) : (
                        <span className="text-[12px] text-slate-400">暂无标签</span>
                      )}
                      {selectedEntryDetail?.abilityKeys?.map((abilityKey) => (
                        <span key={abilityKey} className="rounded-full border border-[#D9E3FF] bg-[#F6F8FF] px-2.5 py-1 text-[11px] font-medium text-[#335CFE]">
                          {abilityKey}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div className="rounded-[24px] border border-slate-100 bg-slate-50/70 p-4">
                    <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">复用状态</div>
                    <div className="mt-3 text-[13px] font-semibold text-slate-800">{selectedEntryDetail?.reuseCount ?? 0} 次复用</div>
                    <div className="mt-2 text-[12px] leading-6 text-slate-500">
                      {selectedEntryDetail?.lastReusedAt ? `最近复用：${formatDateLabel(selectedEntryDetail.lastReusedAt)}` : '还没有真实复用记录，后续在任务里使用后会累积证据。'}
                    </div>
                  </div>
                </section>

                <section>
                  <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">
                    <BookOpen className="h-3.5 w-3.5" />
                    首次来源与适用场景
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {(selectedEntryDetail?.originContexts || selectedEntryDetail?.linkedContexts || selectedEntry.linkedContexts || []).length ? (
                      (selectedEntryDetail?.originContexts || selectedEntryDetail?.linkedContexts || selectedEntry.linkedContexts || []).map((context) => (
                        <button
                          key={`${context.objectType}:${context.objectId}`}
                          type="button"
                          onClick={() => {
                            onClose();
                            if (onOpenContext) {
                              onOpenContext(context);
                              return;
                            }
                            onNavigate?.(context.tab === 'growth' ? 'growth_handbook' : context.tab);
                          }}
                          className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-[12px] font-medium text-slate-600 transition hover:border-[#C9D7FF] hover:text-[#335CFE]"
                        >
                          {context.label}
                          {context.subtitle ? <span className="ml-1 text-slate-400">· {context.subtitle}</span> : null}
                        </button>
                      ))
                    ) : (
                      <div className="rounded-[20px] border border-dashed border-slate-200 bg-slate-50 px-4 py-4 text-[12px] font-medium text-slate-400">
                        当前还没有可展示的来源对象回链
                      </div>
                    )}
                  </div>
                </section>

                <section>
                  <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">
                    <CopyPlus className="h-3.5 w-3.5" />
                    最近复用场景
                  </div>
                  <div className="mt-3 space-y-3">
                    {selectedEntryDetail?.reuseHistory?.length ? (
                      selectedEntryDetail.reuseHistory.map((item) => (
                        <div key={item.id} className="rounded-[20px] border border-slate-100 bg-slate-50/70 p-4">
                          <div className="flex items-start justify-between gap-4">
                            <div>
                              <div className="text-[13px] font-semibold text-slate-900">{item.sourceLabel}</div>
                              <div className="mt-1 text-[12px] leading-6 text-slate-500">
                                {item.contextSummary || item.note || '这条方法已经在真实工作里被继续使用。'}
                              </div>
                            </div>
                            <div className="text-right">
                              <div className="text-[14px] font-semibold text-[#335CFE]">+{item.gainedXp} XP</div>
                              <div className="text-[11px] text-slate-400">{formatDateLabel(item.createdAt)}</div>
                            </div>
                          </div>
                          {item.linkedContexts.length ? (
                            <div className="mt-3 flex flex-wrap gap-2">
                              {item.linkedContexts.map((context) => (
                                <button
                                  key={`${item.id}-${context.objectType}-${context.objectId}`}
                                  type="button"
                                  onClick={() => {
                                    onClose();
                                    onOpenContext?.(context);
                                  }}
                                  className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-[11px] font-medium text-slate-600 transition hover:border-[#C9D7FF] hover:text-[#335CFE]"
                                >
                                  {context.label}
                                </button>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      ))
                    ) : (
                      <div className="rounded-[20px] border border-dashed border-slate-200 bg-slate-50 px-4 py-6 text-[12px] font-medium text-slate-400">
                        还没有真实复用场景。后续从任务、会议或事件线里再次使用时，这里会自动累计证据。
                      </div>
                    )}
                  </div>
                </section>

                <section>
                  <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">
                    <BookOpen className="h-3.5 w-3.5" />
                    适用边界
                  </div>
                  <div className="mt-3 rounded-[24px] border border-slate-100 bg-white p-5 shadow-sm">
                    <p className="text-[13px] leading-7 text-slate-600">
                      {selectedEntryDetail?.contextSummary || `适用于「${selectedEntry.tags.slice(0, 2).join(' / ') || '当前工作场景'}」这类需要明确边界、沉淀方法或减少返工的场景。`}
                      {' '}如果只是一次性结果记录，而没有复用价值，就不应该把它当成方法资产。
                    </p>
                    {selectedEntryDetail?.evidenceRefs?.length ? (
                      <div className="mt-4 flex flex-wrap gap-2">
                        {selectedEntryDetail.evidenceRefs.map((ref) => (
                          <span key={ref} className="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] font-medium text-slate-500">
                            {ref}
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </section>

                <section>
                  <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">
                    <FileClock className="h-3.5 w-3.5" />
                    对应成长账本
                  </div>
                  <div className="mt-3 space-y-3">
                    {detailLoading ? (
                      <div className="rounded-[20px] border border-dashed border-slate-200 bg-slate-50 px-4 py-6 text-[12px] font-medium text-slate-400">
                        正在加载对应 XP 账本...
                      </div>
                    ) : null}
                    {selectedRelatedEntries.length ? (
                      selectedRelatedEntries.map((entry) => (
                        <div key={entry.id} className="rounded-[20px] border border-slate-100 bg-slate-50/70 p-4">
                          <div className="flex items-center justify-between gap-4">
                            <div>
                              <div className="text-[13px] font-semibold text-slate-900">{entry.sourceTitle || entry.reason}</div>
                              <div className="mt-1 text-[12px] leading-5 text-slate-500">
                                {entry.abilityLabel} · 基础 +{entry.baseXp} / 溢价 +{entry.premiumXp}
                              </div>
                              {entry.contextSummary ? <div className="mt-1 text-[11px] leading-5 text-slate-400">{entry.contextSummary}</div> : null}
                            </div>
                            <div className="text-right">
                              <div className="text-[14px] font-semibold text-[#335CFE]">+{entry.totalXp} XP</div>
                              <div className="text-[11px] text-slate-400">{formatDateLabel(entry.createdAt)}</div>
                            </div>
                          </div>
                        </div>
                      ))
                    ) : (
                      <div className="rounded-[20px] border border-dashed border-slate-200 bg-slate-50 px-4 py-6 text-[12px] font-medium text-slate-400">
                        这条资产的回流账本还不多。你可以在真实任务里复用它，系统就会继续给它累计证据。
                      </div>
                    )}
                  </div>
                </section>
              </div>
            ) : (
              <div className="flex h-full items-center justify-center rounded-[24px] border border-dashed border-slate-200 bg-slate-50 text-[13px] font-medium text-slate-400">
                选择一条经验资产后，可以查看详情、来源和复用动作。
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default GrowthAssetLibraryDrawer;
