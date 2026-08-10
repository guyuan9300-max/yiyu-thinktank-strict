import React, { useEffect, useMemo, useState } from 'react';
import { Filter, Sparkles, X } from 'lucide-react';

import { getGrowthLedger } from '../../lib/api';
import type { GrowthAbilityKey, GrowthContextLink, GrowthOverview, XpLedgerEntry } from '../../../shared/types';

type FlashLevel = 'success' | 'error';

type GrowthLedgerDrawerProps = {
  open: boolean;
  growthOverview: GrowthOverview | null;
  flash: (level: FlashLevel, message: string) => void;
  onClose: () => void;
  initialAbilityKey?: GrowthAbilityKey | null;
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
  if (sourceType === 'badge_unlock') return '勋章点亮';
  if (sourceType === 'handbook_entry') return '经验沉淀';
  if (sourceType === 'handbook_reuse') return '方法复用';
  if (sourceType === 'weekly_review' || sourceType === 'weekly_review_task_entry' || sourceType === 'weekly_review_note') return '周复盘';
  if (sourceType === 'meeting_publish') return '会议发布';
  if (sourceType === 'meeting_action_item_publish') return '会议行动项';
  if (sourceType === 'strategic_confirm') return '战略确认';
  if (sourceType === 'strategic_meeting_apply') return '战略作战包';
  return sourceType || '成长事件';
}

function validationLabel(state: string) {
  if (state === 'candidate') return '候选信号';
  if (state === 'confirmed') return '已确认';
  if (state === 'observed') return '已观察';
  if (state === 'validated') return '已验证';
  if (state === 'institutionalized') return '已机制化';
  if (!state) return '待确认';
  return state;
}

export function GrowthLedgerDrawer({ open, growthOverview, flash, onClose, initialAbilityKey = null, onOpenContext }: GrowthLedgerDrawerProps) {
  const [entries, setEntries] = useState<XpLedgerEntry[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [scope, setScope] = useState<'current' | 'all'>('current');
  const [abilityKey, setAbilityKey] = useState<GrowthAbilityKey | 'all'>(initialAbilityKey || 'all');
  const currentWeekLabel = useMemo(() => growthOverview?.recentEntries.find((entry) => entry.weekLabel)?.weekLabel || '', [growthOverview]);

  useEffect(() => {
    if (!open) return;
    setAbilityKey(initialAbilityKey || 'all');
  }, [initialAbilityKey, open]);

  useEffect(() => {
    if (!open) return;
    const load = async () => {
      setIsLoading(true);
      try {
        const response = await getGrowthLedger({
          abilityKey: abilityKey === 'all' ? undefined : abilityKey,
          weekLabel: scope === 'current' && currentWeekLabel ? currentWeekLabel : undefined,
        });
        setEntries(response.entries);
      } catch (error) {
        flash('error', error instanceof Error ? error.message : 'XP 账本加载失败');
      } finally {
        setIsLoading(false);
      }
    };
    void load();
  }, [abilityKey, currentWeekLabel, flash, open, scope]);

  const totals = useMemo(
    () =>
      entries.reduce(
        (acc, entry) => {
          acc.total += entry.totalXp;
          acc.base += entry.baseXp;
          acc.premium += entry.premiumXp;
          return acc;
        },
        { total: 0, base: 0, premium: 0 },
      ),
    [entries],
  );

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-slate-900/30 backdrop-blur-sm">
      <div className="flex h-full w-full max-w-[920px] flex-col bg-white shadow-2xl animate-in slide-in-from-right duration-300">
        <div className="flex items-center justify-between border-b border-slate-100 px-6 py-4">
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">XP 账本</div>
            <h2 className="mt-1 text-[22px] font-semibold tracking-tight text-slate-900">成长分数从哪里来</h2>
          </div>
          <button type="button" onClick={onClose} className="rounded-full p-2 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="border-b border-slate-100 px-6 py-4">
          <div className="grid gap-3 md:grid-cols-3">
            <div className="rounded-[20px] border border-slate-100 bg-slate-50/70 p-4">
              <div className="text-[12px] font-medium text-slate-400">总经验</div>
              <div className="mt-2 text-[28px] font-semibold tracking-tight text-slate-900">+{totals.total}</div>
            </div>
            <div className="rounded-[20px] border border-slate-100 bg-slate-50/70 p-4">
              <div className="text-[12px] font-medium text-slate-400">基础经验</div>
              <div className="mt-2 text-[28px] font-semibold tracking-tight text-slate-900">+{totals.base}</div>
            </div>
            <div className="rounded-[20px] border border-slate-100 bg-slate-50/70 p-4">
              <div className="text-[12px] font-medium text-slate-400">组织溢价</div>
              <div className="mt-2 text-[28px] font-semibold tracking-tight text-[#335CFE]">+{totals.premium}</div>
            </div>
          </div>

          <div className="mt-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={() => setScope('current')} className={cx('rounded-full px-3 py-1.5 text-[12px] font-medium transition', scope === 'current' ? 'bg-[#335CFE] text-white' : 'bg-slate-100 text-slate-500 hover:bg-slate-200')}>
                本周
              </button>
              <button type="button" onClick={() => setScope('all')} className={cx('rounded-full px-3 py-1.5 text-[12px] font-medium transition', scope === 'all' ? 'bg-[#335CFE] text-white' : 'bg-slate-100 text-slate-500 hover:bg-slate-200')}>
                全部
              </button>
              {currentWeekLabel && scope === 'current' ? (
                <span className="inline-flex items-center rounded-full border border-[#D9E3FF] bg-[#F6F8FF] px-3 py-1.5 text-[12px] font-medium text-[#335CFE]">
                  <Sparkles className="mr-1.5 h-3.5 w-3.5" />
                  {currentWeekLabel}
                </span>
              ) : null}
            </div>

            <div className="flex items-center gap-2">
              <Filter className="h-4 w-4 text-slate-400" />
              <select
                value={abilityKey}
                onChange={(event) => setAbilityKey(event.target.value as GrowthAbilityKey | 'all')}
                className="rounded-full border border-slate-200 bg-white px-3 py-2 text-[12px] font-medium text-slate-600 outline-none focus:border-[#C9D7FF]"
              >
                <option value="all">全部能力</option>
                {(growthOverview?.abilities || []).map((ability) => (
                  <option key={ability.abilityKey} value={ability.abilityKey}>
                    {ability.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-6">
          {isLoading ? <div className="rounded-[24px] border border-dashed border-slate-200 bg-slate-50 px-4 py-12 text-center text-[13px] font-medium text-slate-400">XP 账本加载中...</div> : null}
          {!isLoading && !entries.length ? <div className="rounded-[24px] border border-dashed border-slate-200 bg-slate-50 px-4 py-12 text-center text-[13px] font-medium text-slate-400">当前筛选条件下没有 XP 记录</div> : null}

          {!isLoading ? (
            <div className="space-y-3">
              {entries.map((entry) => (
                <div key={entry.id} className="rounded-[22px] border border-slate-100 bg-white p-4 shadow-sm">
                  <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="rounded-full bg-[#EBF0FF] px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.18em] text-[#335CFE]">{entry.abilityLabel}</span>
                        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.18em] text-slate-500">{sourceLabel(entry.sourceType)}</span>
                        {entry.contributionTags.length ? <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.18em] text-emerald-700">组织贡献 +{entry.premiumXp}</span> : null}
                      </div>
                      <div className="mt-3 text-[15px] font-semibold text-slate-900">{entry.sourceTitle || entry.reason}</div>
                      <div className="mt-1 text-[12px] leading-6 text-slate-500">{entry.reason}</div>
                    </div>
                    <div className="shrink-0 text-right">
                      <div className="text-[18px] font-semibold tracking-tight text-slate-900">+{entry.totalXp} XP</div>
                      <div className="mt-1 text-[11px] text-slate-400">{formatDateLabel(entry.createdAt)}</div>
                    </div>
                  </div>

                  <div className="mt-4 grid gap-2 md:grid-cols-3">
                    <div className="rounded-2xl bg-slate-50 px-3 py-2 text-[12px] text-slate-600">基础经验 <span className="font-semibold text-slate-900">+{entry.baseXp}</span></div>
                    <div className="rounded-2xl bg-slate-50 px-3 py-2 text-[12px] text-slate-600">组织溢价 <span className="font-semibold text-[#335CFE]">+{entry.premiumXp}</span></div>
                    <div className="rounded-2xl bg-slate-50 px-3 py-2 text-[12px] text-slate-600">验证状态 <span className="font-semibold text-slate-900">{validationLabel(entry.validationState)}</span></div>
                  </div>

                  <div className="mt-3 flex flex-wrap gap-2">
                    {entry.clientName ? (
                      <span className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-[11px] font-medium text-slate-600">客户 · {entry.clientName}</span>
                    ) : null}
                    {entry.eventLineName ? (
                      <span className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-[11px] font-medium text-slate-600">事件线 · {entry.eventLineName}</span>
                    ) : null}
                    {entry.projectStage ? (
                      <span className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-[11px] font-medium text-slate-600">阶段 · {entry.projectStage}</span>
                    ) : null}
                    {entry.businessCategory ? (
                      <span className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-[11px] font-medium text-slate-600">业务 · {entry.businessCategory}</span>
                    ) : null}
                  </div>

                  {entry.sourceRoute.length ? (
                    <div className="mt-3">
                      <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-400">来源路径</div>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {entry.sourceRoute.map((segment, index) => (
                          <span key={`${entry.id}-route-${segment}-${index}`} className="rounded-full bg-[#EEF3FF] px-2.5 py-1 text-[11px] font-medium text-[#335CFE]">
                            {segment}
                          </span>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {entry.contextSummary ? (
                    <div className="mt-3 rounded-2xl border border-slate-100 bg-slate-50/80 px-3 py-3 text-[12px] leading-6 text-slate-600">
                      {entry.contextSummary}
                    </div>
                  ) : null}

                  {(entry.strategicLink || entry.evidenceRefs.length || entry.linkedContexts.length) ? (
                    <div className="mt-3 grid gap-3 lg:grid-cols-3">
                      <div className="rounded-2xl border border-slate-100 bg-white px-3 py-3">
                        <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-400">战略呼应</div>
                        <div className="mt-2 text-[12px] leading-6 text-slate-600">{entry.strategicLink || '当前没有直接绑定战略线'}</div>
                      </div>
                      <div className="rounded-2xl border border-slate-100 bg-white px-3 py-3">
                        <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-400">证据数量</div>
                        <div className="mt-2 text-[12px] leading-6 text-slate-600">
                          {entry.evidenceRefs.length ? entry.evidenceRefs.join(' / ') : '当前没有独立证据引用'}
                        </div>
                      </div>
                      <div className="rounded-2xl border border-slate-100 bg-white px-3 py-3">
                        <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-400">上下文回链</div>
                        <div className="mt-2 flex flex-wrap gap-2">
                          {entry.linkedContexts.length ? (
                            entry.linkedContexts.slice(0, 3).map((context) => (
                              <button
                                key={`${entry.id}-${context.objectType}-${context.objectId}`}
                                type="button"
                                onClick={() => {
                                  onClose();
                                  if (onOpenContext) {
                                    onOpenContext(context);
                                  }
                                }}
                                className={cx(
                                  'rounded-full px-2.5 py-1 text-[11px] font-medium transition',
                                  onOpenContext ? 'bg-[#EEF3FF] text-[#335CFE] hover:bg-[#E1E9FF]' : 'bg-slate-100 text-slate-600',
                                )}
                              >
                                {context.label}
                              </button>
                            ))
                          ) : (
                            <span className="text-[12px] text-slate-400">暂无回链</span>
                          )}
                        </div>
                      </div>
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

export default GrowthLedgerDrawer;
