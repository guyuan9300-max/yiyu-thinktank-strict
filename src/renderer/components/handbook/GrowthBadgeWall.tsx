import React, { useEffect, useMemo, useState } from 'react';
import {
  ArrowRight,
  BookOpen,
  Briefcase,
  CalendarClock,
  CircleDashed,
  FileStack,
  Flag,
  Gauge,
  HandHelping,
  Handshake,
  Layers3,
  Lightbulb,
  Radar,
  Search,
  ShieldCheck,
  Sparkles,
  Target,
  Users,
  Wrench,
  X,
  type LucideIcon,
} from 'lucide-react';

import { getGrowthBadges } from '../../lib/api';
import { useGrowthOverviewState } from '../growth/GrowthContext';
import type { BadgeBoard, BadgeProgress, BadgeState, GrowthContextLink } from '../../../shared/types';

type FlashLevel = 'success' | 'error';

type GrowthBadgeWallProps = {
  flash: (level: FlashLevel, message: string) => void;
  onNavigate?: (tab: string) => void;
  onOpenContext?: (context: GrowthContextLink) => void;
};

type BadgeFilter = 'all' | 'lit' | 'progress' | 'locked';
type BadgeConnectivityFilter = 'all' | 'connected' | 'unsupported';

const STATE_LABELS: Record<BadgeState, string> = {
  locked: '未点亮',
  progress: '进行中',
  ready: '即将点亮',
  lit: '已点亮',
  mastered: '已精进',
};

const MOTIF_ICON_MAP: Record<string, LucideIcon> = {
  meeting_ring: Users,
  report_arrow: ArrowRight,
  chat_bolt: Sparkles,
  linked_rings: Users,
  handoff: HandHelping,
  radar_ping: Radar,
  search_chat: Search,
  stack_docs: Layers3,
  path_nodes: Target,
  handshake_seal: Handshake,
  blueprint_flag: Flag,
  grid_blocks: Layers3,
  summit_flag: Flag,
  shield_ping: ShieldCheck,
  seal_box: Briefcase,
  calendar_lines: CalendarClock,
  dashboard_gauge: Gauge,
  manual_stack: BookOpen,
  stamp_flow: CircleDashed,
  loop_note: ArrowRight,
  invoice_shield: ShieldCheck,
  wallet_gate: Briefcase,
  scroll_seal: FileStack,
  bill_return: ArrowRight,
  cart_checklist: Briefcase,
  mentor_orbit: Users,
  idea_burst: Lightbulb,
  cards_spark: Sparkles,
  path_flag: Flag,
  wrench_up: Wrench,
};

function cx(...classNames: Array<string | false | null | undefined>) {
  return classNames.filter(Boolean).join(' ');
}

function formatDateLabel(value?: string | null) {
  if (!value) return '未记录';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric' }).format(date);
}

function badgePalette(state: BadgeState) {
  if (state === 'lit' || state === 'mastered') {
    return {
      ring: '#335CFE',
      glow: '0 16px 40px rgba(51, 92, 254, 0.18)',
      center: 'linear-gradient(180deg, rgba(83,121,255,0.98) 0%, rgba(44,78,233,0.98) 100%)',
      outer: 'linear-gradient(180deg, rgba(246,249,255,0.98) 0%, rgba(225,233,255,0.98) 100%)',
      icon: 'text-white',
      border: 'rgba(113, 144, 255, 0.26)',
      chip: 'bg-[#335CFE]/10 text-[#335CFE]',
    };
  }
  if (state === 'ready') {
    return {
      ring: '#5B7BFE',
      glow: '0 12px 30px rgba(91, 123, 254, 0.16)',
      center: 'linear-gradient(180deg, rgba(239,244,255,1) 0%, rgba(221,231,255,1) 100%)',
      outer: 'linear-gradient(180deg, rgba(255,255,255,1) 0%, rgba(240,244,255,1) 100%)',
      icon: 'text-[#335CFE]',
      border: 'rgba(113, 144, 255, 0.22)',
      chip: 'bg-[#5B7BFE]/10 text-[#335CFE]',
    };
  }
  if (state === 'progress') {
    return {
      ring: '#8FA4FF',
      glow: '0 8px 20px rgba(143, 164, 255, 0.08)',
      center: 'linear-gradient(180deg, rgba(255,255,255,1) 0%, rgba(243,246,253,1) 100%)',
      outer: 'linear-gradient(180deg, rgba(255,255,255,1) 0%, rgba(246,248,252,1) 100%)',
      icon: 'text-[#5B7BFE]',
      border: 'rgba(203, 213, 225, 0.8)',
      chip: 'bg-slate-100 text-slate-600',
    };
  }
  return {
    ring: '#D5DCE8',
    glow: 'none',
    center: 'linear-gradient(180deg, rgba(255,255,255,1) 0%, rgba(245,247,250,1) 100%)',
    outer: 'linear-gradient(180deg, rgba(255,255,255,1) 0%, rgba(246,248,251,1) 100%)',
    icon: 'text-slate-400',
    border: 'rgba(226, 232, 240, 0.9)',
    chip: 'bg-slate-100 text-slate-500',
  };
}

function BadgeToken({ badge, size = 'md' }: { badge: BadgeProgress; size?: 'md' | 'lg' }) {
  const palette = badgePalette(badge.state);
  const Icon = MOTIF_ICON_MAP[badge.iconMotif] || Sparkles;
  const diameter = size === 'lg' ? 116 : 84;
  const radius = size === 'lg' ? 44 : 31;
  const stroke = size === 'lg' ? 5 : 4;
  const circumference = 2 * Math.PI * radius;
  const dashoffset = circumference - (circumference * badge.progressPercent) / 100;

  return (
    <div
      className="relative flex items-center justify-center"
      style={{ width: diameter, height: diameter, filter: `drop-shadow(${palette.glow})` }}
    >
      <div
        className="absolute inset-0 rounded-full border backdrop-blur-[2px]"
        style={{ background: palette.outer, borderColor: palette.border }}
      />
      <svg className="absolute inset-0 -rotate-90" viewBox={`0 0 ${diameter} ${diameter}`}>
        <circle cx={diameter / 2} cy={diameter / 2} r={radius} fill="none" stroke="rgba(226,232,240,0.66)" strokeWidth={stroke} />
        <circle
          cx={diameter / 2}
          cy={diameter / 2}
          r={radius}
          fill="none"
          stroke={palette.ring}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={dashoffset}
        />
      </svg>
      <div
        className="relative flex items-center justify-center rounded-full border"
        style={{
          width: size === 'lg' ? 76 : 56,
          height: size === 'lg' ? 76 : 56,
          background: palette.center,
          borderColor: palette.border,
        }}
      >
        <Icon className={cx(size === 'lg' ? 'h-8 w-8' : 'h-6 w-6', palette.icon)} strokeWidth={1.9} />
      </div>
      {badge.state === 'ready' ? <div className="absolute right-2 top-1.5 h-2.5 w-2.5 rounded-full bg-[#335CFE] shadow-[0_0_0_4px_rgba(51,92,254,0.12)]" /> : null}
      {badge.state === 'mastered' ? <div className="absolute bottom-0 rounded-full bg-[#111827] px-2 py-0.5 text-[10px] font-semibold tracking-[0.18em] text-white">V2</div> : null}
    </div>
  );
}

function stateMatchesFilter(state: BadgeState, filter: BadgeFilter) {
  if (filter === 'all') return true;
  if (filter === 'lit') return state === 'lit' || state === 'mastered';
  if (filter === 'progress') return state === 'progress' || state === 'ready';
  return state === 'locked';
}

function badgeNeedsModuleConnection(badge: BadgeProgress) {
  return badge.missingSignals.some((signal) => signal.includes('当前模块未接通'));
}

export function GrowthBadgeWall({ flash, onNavigate, onOpenContext }: GrowthBadgeWallProps) {
  const growthState = useGrowthOverviewState();
  const [board, setBoard] = useState<BadgeBoard | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<BadgeFilter>('all');
  const [connectivityFilter, setConnectivityFilter] = useState<BadgeConnectivityFilter>('connected');
  const [categoryId, setCategoryId] = useState<string>('all');
  const [selectedBadgeId, setSelectedBadgeId] = useState<string | null>(null);

  const loadBadges = async () => {
    setIsLoading(true);
    try {
      const response = await getGrowthBadges();
      setBoard(response);
      if (growthState) {
        void growthState.refreshGrowthOverview();
      }
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '成长勋章加载失败');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void loadBadges();
  }, []);

  const allBadges = useMemo(() => board?.categories.flatMap((category) => category.badges) || [], [board]);
  const filteredCategories = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return (board?.categories || [])
      .map((category) => ({
        ...category,
        badges: category.badges.filter((badge) => {
          if (categoryId !== 'all' && category.id !== categoryId) return false;
          if (!stateMatchesFilter(badge.state, filter)) return false;
          if (connectivityFilter === 'connected' && badgeNeedsModuleConnection(badge)) return false;
          if (connectivityFilter === 'unsupported' && !badgeNeedsModuleConnection(badge)) return false;
          if (!normalizedQuery) return true;
          return [badge.name, badge.description, badge.categoryLabel, badge.whyItMatters]
            .join(' ')
            .toLowerCase()
            .includes(normalizedQuery);
        }),
      }))
      .filter((category) => category.badges.length > 0);
  }, [board, categoryId, connectivityFilter, filter, query]);

  useEffect(() => {
    if (!filteredCategories.length) {
      setSelectedBadgeId(null);
      return;
    }
    const stillExists = filteredCategories.some((category) => category.badges.some((badge) => badge.id === selectedBadgeId));
    if (!selectedBadgeId || !stillExists) {
      setSelectedBadgeId(filteredCategories[0].badges[0]?.id || null);
    }
  }, [filteredCategories, selectedBadgeId]);

  const selectedBadge = useMemo(
    () => filteredCategories.flatMap((category) => category.badges).find((badge) => badge.id === selectedBadgeId) || null,
    [filteredCategories, selectedBadgeId],
  );

  const upcomingNames = useMemo(() => {
    const ids = new Set(board?.overview.upcomingBadgeIds || []);
    return allBadges.filter((badge) => ids.has(badge.id)).map((badge) => badge.name);
  }, [allBadges, board]);
  const unsupportedBadgeCount = useMemo(() => allBadges.filter((badge) => badgeNeedsModuleConnection(badge)).length, [allBadges]);
  const connectedBadgeCount = useMemo(() => allBadges.length - unsupportedBadgeCount, [allBadges.length, unsupportedBadgeCount]);

  const handleAction = (tab: string, label: string) => {
    if (onNavigate) {
      onNavigate(tab);
      return;
    }
    flash('success', `${label} 已准备好，后续可继续接更深的页面跳转`);
  };

  const handleContextAction = (context: GrowthContextLink) => {
    if (onOpenContext) {
      onOpenContext(context);
      return;
    }
    handleAction(context.tab === 'growth' ? 'growth_handbook' : context.tab, context.label);
  };

  return (
    <div className="animate-in space-y-6 fade-in duration-300">
      <div className="rounded-[28px] border border-[#DDE6FF] bg-[radial-gradient(circle_at_top_left,_rgba(51,92,254,0.08),_transparent_34%),linear-gradient(180deg,#FFFFFF_0%,#FAFBFF_100%)] p-6 shadow-[0_24px_70px_rgba(15,23,42,0.04)]">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div className="space-y-3">
            <div className="inline-flex items-center rounded-full border border-[#D6E1FF] bg-white px-3 py-1 text-[12px] font-medium text-[#335CFE] shadow-sm">
              <Sparkles className="mr-1.5 h-3.5 w-3.5" /> 成长勋章会根据真实业务行为自动点亮
            </div>
            <div>
              <h2 className="text-[28px] font-semibold tracking-tight text-slate-900">成长勋章</h2>
              <p className="mt-2 max-w-3xl text-[14px] leading-7 text-slate-500">
                系统会从会议、任务、复盘、知识沉淀和成长练习里自动识别你的工作行为。每一枚勋章都能解释为什么没亮、离点亮还差什么，以及由哪些真实证据触发。
              </p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {[
              { label: '已点亮', value: board?.overview.litBadges ?? 0 },
              { label: '本月新增', value: board?.overview.monthlyNewBadges ?? 0 },
              { label: '勋章 XP', value: board?.overview.totalXp ?? 0 },
              { label: '即将点亮', value: board?.overview.readyBadges ?? 0 },
            ].map((item) => (
              <div key={item.label} className="min-w-[118px] rounded-[22px] border border-white/80 bg-white/88 p-4 shadow-[0_18px_40px_rgba(148,163,184,0.08)] backdrop-blur">
                <div className="text-[12px] font-medium text-slate-400">{item.label}</div>
                <div className="mt-3 text-[30px] font-semibold tracking-tight text-slate-900">{item.value}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-2 text-[12px] text-slate-500">
          <span className="rounded-full border border-slate-200 bg-white px-3 py-1 font-medium text-slate-500">
            部分勋章依赖 CRM / 审批 / 财务等模块事件，未接通前会保持灰色但不会误算
          </span>
          <button
            type="button"
            onClick={() => setConnectivityFilter('connected')}
            className={cx(
              'rounded-full border px-3 py-1 font-medium transition-colors',
              connectivityFilter === 'connected' ? 'border-[#D9E3FF] bg-white text-[#4B63D9]' : 'border-slate-200 bg-white text-slate-500 hover:border-slate-300',
            )}
          >
            已接通 {connectedBadgeCount}
          </button>
          <button
            type="button"
            onClick={() => setConnectivityFilter('unsupported')}
            className={cx(
              'rounded-full border px-3 py-1 font-medium transition-colors',
              connectivityFilter === 'unsupported' ? 'border-amber-200 bg-amber-50 text-amber-700' : 'border-slate-200 bg-white text-slate-500 hover:border-slate-300',
            )}
          >
            待接通 {unsupportedBadgeCount}
          </button>
          {upcomingNames.length ? (
            upcomingNames.map((name) => (
              <span key={name} className="rounded-full border border-[#D9E3FF] bg-white px-3 py-1 font-medium text-[#4B63D9]">
                即将点亮：{name}
              </span>
            ))
          ) : (
            <span className="rounded-full border border-slate-200 bg-white px-3 py-1 font-medium text-slate-500">继续沉淀真实业务行为，系统会自动更新勋章进度</span>
          )}
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="space-y-5">
          <div className="rounded-[24px] border border-gray-100 bg-white p-4 shadow-sm">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="搜索勋章、说明或下一步动作..."
                  className="w-full rounded-2xl border border-slate-200 bg-slate-50/70 py-3 pl-10 pr-4 text-[13px] font-medium text-slate-700 placeholder:text-slate-400 focus:border-[#C9D7FF] focus:bg-white focus:outline-none"
                />
              </div>
              <div className="flex flex-wrap gap-2">
                {[
                  ['all', '全部'],
                  ['lit', '已点亮'],
                  ['progress', '进行中'],
                  ['locked', '未点亮'],
                ].map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setFilter(value as BadgeFilter)}
                    className={cx(
                      'rounded-2xl px-3.5 py-2 text-[12px] font-medium transition-colors',
                      filter === value ? 'bg-[#335CFE] text-white' : 'bg-slate-100 text-slate-500 hover:bg-slate-200/80',
                    )}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              {[
                ['connected', '已接通'],
                ['unsupported', '待接通'],
                ['all', '全部'],
              ].map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setConnectivityFilter(value as BadgeConnectivityFilter)}
                  className={cx(
                    'rounded-full px-3 py-1.5 text-[12px] font-medium transition-colors',
                    connectivityFilter === value ? 'bg-[#EBF0FF] text-[#335CFE]' : 'bg-slate-50 text-slate-500 hover:bg-slate-100',
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => setCategoryId('all')}
                className={cx(
                  'rounded-full px-3 py-1.5 text-[12px] font-medium transition-colors',
                  categoryId === 'all' ? 'bg-[#EBF0FF] text-[#335CFE]' : 'bg-slate-50 text-slate-500 hover:bg-slate-100',
                )}
              >
                全部分组
              </button>
              {(board?.categories || []).map((category) => (
                <button
                  key={category.id}
                  type="button"
                  onClick={() => setCategoryId(category.id)}
                  className={cx(
                    'rounded-full px-3 py-1.5 text-[12px] font-medium transition-colors',
                    categoryId === category.id ? 'bg-[#EBF0FF] text-[#335CFE]' : 'bg-slate-50 text-slate-500 hover:bg-slate-100',
                  )}
                >
                  {category.label}
                </button>
              ))}
            </div>
          </div>

          {isLoading ? (
            <div className="rounded-[24px] border border-dashed border-slate-200 bg-white p-10 text-center text-[13px] font-medium text-slate-400">成长勋章加载中...</div>
          ) : null}

          {!isLoading && !filteredCategories.length ? (
            <div className="rounded-[24px] border border-dashed border-slate-200 bg-white p-10 text-center text-[13px] font-medium text-slate-400">
              当前筛选条件下没有匹配的勋章
            </div>
          ) : null}

          {filteredCategories.map((category) => (
            <section key={category.id} className="space-y-3">
              <div className="flex items-center justify-between px-1">
                <div>
                  <h3 className="text-[17px] font-semibold text-slate-900">{category.label}</h3>
                  <p className="mt-1 text-[12px] text-slate-400">
                    {category.litCount} / {category.totalCount} 已点亮 · 映射能力：{category.abilityLabel}
                  </p>
                </div>
              </div>
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
                {category.badges.map((badge) => (
                  <button
                    key={badge.id}
                    type="button"
                    onClick={() => setSelectedBadgeId(badge.id)}
                    className={cx(
                      'group rounded-[26px] border bg-white p-5 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-[0_18px_48px_rgba(15,23,42,0.08)]',
                      selectedBadgeId === badge.id ? 'border-[#C9D7FF] shadow-[0_20px_50px_rgba(51,92,254,0.08)]' : 'border-slate-100',
                    )}
                  >
                    <div className="flex items-start justify-between gap-4">
                      <BadgeToken badge={badge} />
                      <div className={cx('rounded-full px-2.5 py-1 text-[11px] font-semibold', badgePalette(badge.state).chip)}>{STATE_LABELS[badge.state]}</div>
                    </div>
                    <div className="mt-5">
                      <div className="flex items-center justify-between">
                        <h4 className="text-[16px] font-semibold tracking-tight text-slate-900">{badge.name}</h4>
                        <span className="text-[12px] font-semibold text-[#335CFE]">+{badge.xp} XP</span>
                      </div>
                      <p className="mt-2 min-h-[40px] text-[13px] leading-6 text-slate-500">{badge.description}</p>
                    </div>
                    <div className="mt-4">
                      <div className="flex items-center justify-between text-[11px] font-medium text-slate-400">
                        <span>{badge.progressText}</span>
                        <span>{badge.progressPercent}%</span>
                      </div>
                      <div className="mt-2 h-2 overflow-hidden rounded-full bg-slate-100">
                        <div className="h-full rounded-full bg-[#335CFE] transition-all" style={{ width: `${badge.progressPercent}%` }} />
                      </div>
                      <p className="mt-3 line-clamp-2 text-[12px] leading-5 text-slate-500">{badge.nextActionText}</p>
                      {badgeNeedsModuleConnection(badge) ? (
                        <div className="mt-3 rounded-full border border-amber-100 bg-amber-50 px-2.5 py-1 text-[10px] font-medium text-amber-700">
                          当前依赖模块未接通
                        </div>
                      ) : null}
                      {badge.missingSignals.length ? (
                        <div className="mt-3 flex flex-wrap gap-2">
                          {badge.missingSignals.slice(0, 2).map((signal) => (
                            <span key={`${badge.id}-${signal}`} className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[10px] font-medium text-slate-500">
                              {signal}
                            </span>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  </button>
                ))}
              </div>
            </section>
          ))}
        </div>

        <aside className="rounded-[28px] border border-slate-100 bg-white shadow-sm">
          {selectedBadge ? (
            <div className="flex h-full flex-col">
              <div className="flex items-start justify-between border-b border-slate-100 p-6">
                <div className="flex items-center gap-4">
                  <BadgeToken badge={selectedBadge} size="lg" />
                  <div>
                    <div className={cx('inline-flex rounded-full px-2.5 py-1 text-[11px] font-semibold', badgePalette(selectedBadge.state).chip)}>
                      {STATE_LABELS[selectedBadge.state]}
                    </div>
                    <h3 className="mt-3 text-[24px] font-semibold tracking-tight text-slate-900">{selectedBadge.name}</h3>
                    <p className="mt-1 text-[13px] text-slate-500">{selectedBadge.categoryLabel} · +{selectedBadge.xp} XP</p>
                  </div>
                </div>
                <button type="button" onClick={() => setSelectedBadgeId(null)} className="rounded-full p-2 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-700">
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="space-y-6 overflow-y-auto p-6">
                <section>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">这个勋章代表什么</div>
                  <p className="mt-3 text-[14px] leading-7 text-slate-600">{selectedBadge.whyItMatters}</p>
                </section>

                <section>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">你现在的进度</div>
                  <div className="mt-3 rounded-[22px] border border-slate-100 bg-slate-50/80 p-4">
                    <div className="flex items-center justify-between">
                      <div>
                        <div className="text-[13px] font-semibold text-slate-900">{selectedBadge.progressText}</div>
                        <div className="mt-1 text-[12px] text-slate-500">当前进度 {selectedBadge.progressPercent}%</div>
                      </div>
                      <div className="text-[22px] font-semibold tracking-tight text-[#335CFE]">{selectedBadge.progressPercent}%</div>
                    </div>
                    <div className="mt-3 h-2 overflow-hidden rounded-full bg-white">
                      <div className="h-full rounded-full bg-[#335CFE]" style={{ width: `${selectedBadge.progressPercent}%` }} />
                    </div>
                  </div>
                </section>

                <section>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">离点亮还差什么</div>
                  <p className="mt-3 rounded-[22px] border border-[#DDE6FF] bg-[#F6F8FF] p-4 text-[13px] font-medium leading-6 text-[#335CFE]">{selectedBadge.nextActionText}</p>
                  {badgeNeedsModuleConnection(selectedBadge) ? (
                    <div className="mt-3 rounded-[18px] border border-amber-100 bg-amber-50 p-4 text-[12px] leading-6 text-amber-700">
                      这枚勋章依赖的模块事件还没接通。当前灰色不代表你没有做到，而是系统还没法稳定识别。
                    </div>
                  ) : null}
                  {selectedBadge.missingSignals.length ? (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {selectedBadge.missingSignals.map((signal) => (
                        <span key={`${selectedBadge.id}-missing-${signal}`} className="rounded-full border border-orange-100 bg-orange-50 px-2.5 py-1 text-[11px] font-medium text-orange-700">
                          {signal}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </section>

                <section>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">马上去做</div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {selectedBadge.actionLinks.map((action) => (
                      <button
                        key={`${selectedBadge.id}-${action.label}`}
                        type="button"
                        onClick={() => handleAction(action.tab, action.label)}
                        className="inline-flex items-center rounded-full border border-slate-200 bg-white px-3 py-2 text-[12px] font-medium text-slate-700 transition-colors hover:bg-slate-50"
                      >
                        {action.label}
                        <ArrowRight className="ml-1.5 h-3.5 w-3.5" />
                      </button>
                    ))}
                  </div>
                </section>

                <section>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">系统怎么识别</div>
                  <p className="mt-3 text-[13px] leading-6 text-slate-500">{selectedBadge.systemHowText}</p>
                </section>

                <section>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">主要触发场景</div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {selectedBadge.linkedContexts.length ? (
                      selectedBadge.linkedContexts.map((context) => (
                        <button
                          key={`${selectedBadge.id}-${context.objectType}-${context.objectId}`}
                          type="button"
                          onClick={() => handleContextAction(context)}
                          className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-[12px] font-medium text-slate-600 transition hover:bg-slate-50"
                        >
                          {context.label}
                        </button>
                      ))
                    ) : (
                      <div className="rounded-[18px] border border-dashed border-slate-200 px-4 py-4 text-[12px] font-medium text-slate-400">
                        当前还没有足够上下文，说明这枚勋章依赖的业务事件源尚未完整接通。
                      </div>
                    )}
                  </div>
                </section>

                <section>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">最近触发的证据</div>
                  <div className="mt-3 space-y-3">
                    {selectedBadge.evidence.length ? (
                      selectedBadge.evidence.map((evidence) => (
                        <div key={evidence.id} className="rounded-[18px] border border-slate-100 bg-slate-50/70 p-4">
                          <div className="flex items-center justify-between gap-3">
                            <div>
                              <div className="text-[13px] font-semibold text-slate-900">{evidence.title}</div>
                              <div className="mt-1 text-[12px] leading-5 text-slate-500">{evidence.subtitle}</div>
                            </div>
                            <div className="shrink-0 text-[11px] font-medium text-slate-400">{formatDateLabel(evidence.occurredAt)}</div>
                          </div>
                        </div>
                      ))
                    ) : (
                      <div className="rounded-[18px] border border-dashed border-slate-200 p-4 text-[12px] font-medium text-slate-400">
                        系统暂时还没有识别到足够的业务证据。你不需要手动领取，只要继续在真实工作流里完成对应动作即可。
                      </div>
                    )}
                  </div>
                </section>

                <section>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-slate-400">对应经验记录</div>
                  <div className="mt-3 rounded-[22px] border border-slate-100 bg-white p-4 shadow-[inset_0_1px_0_rgba(255,255,255,0.8)]">
                    {selectedBadge.unlockedAt ? (
                      <>
                        <div className="text-[13px] font-semibold text-slate-900">
                          你已于 {formatDateLabel(selectedBadge.unlockedAt)} 点亮【{selectedBadge.name}】
                        </div>
                        <div className="mt-2 text-[12px] leading-6 text-slate-500">系统已自动增加 +{selectedBadge.xp} XP，并同步写入成长总览与近期经验流。</div>
                        {selectedBadge.historical ? <div className="mt-3 inline-flex rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-medium text-slate-500">历史达成</div> : null}
                      </>
                    ) : (
                      <div className="text-[12px] leading-6 text-slate-500">点亮后会自动写入经验记录，不需要手动领取。</div>
                    )}
                  </div>
                </section>
              </div>
            </div>
          ) : (
            <div className="flex h-full min-h-[480px] items-center justify-center px-8 text-center text-[13px] font-medium leading-6 text-slate-400">
              从左侧勋章墙选择一枚勋章，系统会解释它代表什么、当前进度、下一步动作以及对应证据。
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
