import React, { useEffect, useMemo, useState } from 'react';
import {
  BookOpen,
  BrainCircuit,
  Eye,
  Flame,
  GitMerge,
  PenTool,
  PlusCircle,
  Rocket,
  ShieldAlert,
  Sparkles,
  Users,
  X,
} from 'lucide-react';

import { acceptGrowthRecommendation, dismissGrowthRecommendation, getGrowthOverview, getGrowthWorkbench, updateGrowthPendingCapture } from '../../lib/api';
import { useGrowthOverviewState } from '../growth/GrowthContext';
import { GrowthAssetLibraryDrawer } from './GrowthAssetLibraryDrawer';
import { GrowthBadgeWall } from './GrowthBadgeWall';
import { GrowthLedgerDrawer } from './GrowthLedgerDrawer';
import { GrowthLearningWorkbench } from './GrowthLearningWorkbench';
import type { GrowthAbilityGap, GrowthAbilityKey, GrowthContextLink, GrowthFocusAction, GrowthOverview, GrowthPendingCapture, GrowthProjectHighlight, GrowthRank, GrowthWorkbenchSnapshot, HandbookEntry, HandbookEntryPayload, HandbookSettings, LearningRecommendation, Task } from '../../../shared/types';

type FlashLevel = 'success' | 'error';
type GrowthHandbookTab = 'overview' | 'records' | 'learning' | 'map';

type GrowthHandbookViewProps = {
  entries: HandbookEntry[];
  settings: HandbookSettings;
  currentClientId?: string | null;
  tasks?: Task[];
  onCreateEntry: (payload: HandbookEntryPayload) => Promise<HandbookEntry | void>;
  onTasksReload?: () => Promise<unknown> | void;
  onNavigate?: (tab: string) => void;
  onOpenContext?: (context: GrowthContextLink) => void;
  flash: (level: FlashLevel, message: string) => void;
};

type ExperienceCard = {
  id: string;
  title: string;
  summary: string;
  source: string;
  tags: string[];
  xp: number;
  dateLabel: string;
  type: string;
  isMethod: boolean;
};

type DailyDrop = {
  id: string;
  task: string;
  time: string;
  createdAt: string;
  xp: number;
  baseXp?: number;
  premiumXp?: number;
  premiumRate?: number;
  type: string;
  isSpecial: boolean;
  abilityLabels: string[];
  entryCount: number;
};

type LearningCard = {
  id: string;
  theme: string;
  reason: string;
  whyNow?: string;
  learnContent: {
    type: string;
    title: string;
    icon: React.ComponentType<{ className?: string }>;
  };
  practiceTask: string;
  isUrgent: boolean;
  xpReward: number;
  questType: string;
  recommendationId?: string | null;
  linkedTaskId?: string | null;
  clientName?: string | null;
  eventLineName?: string | null;
  projectStage?: string | null;
  triggerNode?: string | null;
  linkedContexts?: GrowthContextLink[];
};

type AbilityCard = {
  id: string;
  name: string;
  currentScore: number;
  previousScore: number;
  requiredScore: number;
  stage: string;
  nextStage: string;
  icon: React.ComponentType<{ className?: string }>;
  iconClassName: string;
  bgClassName: string;
  numericInc: number;
  evidence: string;
  gapReason?: string;
  gapSourceLabel?: string;
  gapSourceType?: string;
  gapSourceId?: string;
};

type DraftState = {
  title: string;
  summary: string;
  tags: string;
  sourceType: string;
};

type RankTier = {
  key: string;
  name: string;
  minXp: number;
  accent: string;
  accentSoft: string;
  accentDeep: string;
  glow: string;
  metal: string;
  ribbon: string;
  showDivision?: boolean;
};

type RankMeta = {
  key: string;
  name: string;
  tier: RankTier;
  divisionLabel: string | null;
  fullLabel: string;
  nextLabel: string | null;
  xpToNextTier: number;
  progress: number;
};

const SOURCE_XP_MAP: Record<string, number> = {
  manual: 15,
  meeting: 20,
  topic_candidate: 25,
  task: 15,
  analysis: 25,
};

const SOURCE_LABEL_MAP: Record<string, string> = {
  manual: '手动沉淀',
  meeting: '会议结论',
  topic_candidate: '情报候选',
  task: '任务复盘',
  analysis: '分析学习',
};

const SOURCE_TYPE_MAP: Record<string, string> = {
  manual: '经验',
  meeting: '结论',
  topic_candidate: '判断',
  task: '复盘',
  analysis: '方法',
};

const ABILITY_VISUALS: Record<string, Pick<AbilityCard, 'icon' | 'iconClassName' | 'bgClassName'>> = {
  exec: { icon: Rocket, iconClassName: 'text-[#5B7BFE]', bgClassName: 'bg-[#5B7BFE]/10' },
  collab: { icon: Users, iconClassName: 'text-[#5B7BFE]', bgClassName: 'bg-[#5B7BFE]/10' },
  analyze: { icon: BrainCircuit, iconClassName: 'text-gray-500', bgClassName: 'bg-gray-100' },
  insight: { icon: Eye, iconClassName: 'text-emerald-500', bgClassName: 'bg-emerald-50' },
  risk: { icon: ShieldAlert, iconClassName: 'text-orange-500', bgClassName: 'bg-orange-50' },
  write: { icon: PenTool, iconClassName: 'text-[#5B7BFE]', bgClassName: 'bg-[#5B7BFE]/10' },
};

const RANK_DIVISIONS = ['III', 'II', 'I'] as const;

const RANK_TIERS: RankTier[] = [
  {
    key: 'bronze',
    name: '倔强青铜',
    minXp: 0,
    accent: '#B67A48',
    accentSoft: '#F7D7B4',
    accentDeep: '#6E4324',
    glow: '#F4E0CC',
    metal: '#E6B889',
    ribbon: '#8D552E',
  },
  {
    key: 'silver',
    name: '秩序白银',
    minXp: 120,
    accent: '#BAC8D8',
    accentSoft: '#F5F8FF',
    accentDeep: '#6E7C90',
    glow: '#E9EEF6',
    metal: '#DCE4EF',
    ribbon: '#8896AA',
  },
  {
    key: 'gold',
    name: '荣耀黄金',
    minXp: 260,
    accent: '#D7A63A',
    accentSoft: '#FFF1B8',
    accentDeep: '#8A5A17',
    glow: '#FFF2CC',
    metal: '#F0D36F',
    ribbon: '#A46C1A',
  },
  {
    key: 'platinum',
    name: '尊贵铂金',
    minXp: 460,
    accent: '#43C3BA',
    accentSoft: '#DAFBF4',
    accentDeep: '#1D7A74',
    glow: '#D8F6F0',
    metal: '#8CE6D8',
    ribbon: '#2F8F86',
  },
  {
    key: 'diamond',
    name: '永恒钻石',
    minXp: 720,
    accent: '#5E7CFF',
    accentSoft: '#DDE6FF',
    accentDeep: '#3149B7',
    glow: '#E0E8FF',
    metal: '#AFC0FF',
    ribbon: '#3B58CB',
  },
  {
    key: 'star',
    name: '至尊星耀',
    minXp: 1040,
    accent: '#1EA8D8',
    accentSoft: '#D8F5FF',
    accentDeep: '#0D5F8F',
    glow: '#D8F1FB',
    metal: '#8AD7F0',
    ribbon: '#167AA7',
  },
  {
    key: 'king',
    name: '最强王者',
    minXp: 1420,
    accent: '#E06445',
    accentSoft: '#FFE1D6',
    accentDeep: '#8D331A',
    glow: '#FFE5DC',
    metal: '#F3B28D',
    ribbon: '#AA4425',
  },
  {
    key: 'glory',
    name: '荣耀王者',
    minXp: 1900,
    accent: '#D84A4A',
    accentSoft: '#FFE0E0',
    accentDeep: '#8B1F2D',
    glow: '#FFE1E1',
    metal: '#F2A1A1',
    ribbon: '#A92839',
  },
  {
    key: 'legend',
    name: '传奇王者',
    minXp: 2600,
    accent: '#D79D2F',
    accentSoft: '#FFF1C5',
    accentDeep: '#7A4012',
    glow: '#FFF1D9',
    metal: '#F0CF77',
    ribbon: '#A8601D',
    showDivision: false,
  },
];

function cx(...classNames: Array<string | false | null | undefined>) {
  return classNames.filter(Boolean).join(' ');
}

function buildDraft(defaultTagsText: string, sourceType = 'manual'): DraftState {
  return {
    title: '',
    summary: '',
    tags: defaultTagsText,
    sourceType,
  };
}

function formatRelativeDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const diffMs = Date.now() - date.getTime();
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));
  if (diffDays <= 0) return '今天';
  if (diffDays === 1) return '昨天';
  if (diffDays < 7) return `${diffDays}天前`;
  return date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
}

function formatRelativeMoment(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const diffMs = Date.now() - date.getTime();
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
  if (diffHours < 1) return '刚刚';
  if (diffHours < 24) return `${diffHours}小时前`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays === 1) return '昨天';
  if (diffDays < 7) return `${diffDays}天前`;
  return date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
}

function parseTags(value: string) {
  return value
    .split(/[，,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function isMethodEntry(entry: HandbookEntry) {
  const normalized = `${entry.title} ${entry.summary} ${entry.tags.join(' ')}`.toLowerCase();
  return ['模板', '方法', '清单', '复用', '机制'].some((keyword) => normalized.includes(keyword.toLowerCase()));
}

function getSourceLabel(sourceType: string) {
  return SOURCE_LABEL_MAP[sourceType] || sourceType || '手动沉淀';
}

function getTypeLabel(sourceType: string) {
  return SOURCE_TYPE_MAP[sourceType] || '经验';
}

function getSourceXp(sourceType: string) {
  return SOURCE_XP_MAP[sourceType] || 10;
}

function buildFallbackRank(score: number): GrowthRank {
  const currentTier = [...RANK_TIERS].reverse().find((tier) => score >= tier.minXp) || RANK_TIERS[0];
  const currentIndex = RANK_TIERS.findIndex((tier) => tier.key === currentTier.key);
  const nextTier = RANK_TIERS[currentIndex + 1] || null;
  const tierSpan = nextTier ? Math.max(1, nextTier.minXp - currentTier.minXp) : 600;
  const tierOffset = Math.max(0, score - currentTier.minXp);
  const progress = nextTier ? Math.max(0, Math.min(1, tierOffset / tierSpan)) : 1;
  const bucket = currentTier.showDivision === false ? -1 : Math.min(RANK_DIVISIONS.length - 1, Math.floor(progress * RANK_DIVISIONS.length));
  const divisionLabel = currentTier.showDivision === false ? null : RANK_DIVISIONS[Math.max(0, bucket)];
  return {
    key: currentTier.key,
    name: currentTier.name,
    division: divisionLabel,
    fullLabel: divisionLabel ? `${currentTier.name} ${divisionLabel}` : currentTier.name,
    nextName: nextTier ? nextTier.name : null,
    xpToNext: nextTier ? Math.max(0, nextTier.minXp - score) : 0,
    progress,
  };
}

function decorateRank(rank: GrowthRank): RankMeta {
  const tier = RANK_TIERS.find((item) => item.key === rank.key) || RANK_TIERS[0];
  return {
    key: rank.key,
    name: rank.name,
    tier,
    divisionLabel: rank.division || null,
    fullLabel: rank.fullLabel,
    nextLabel: rank.nextName || null,
    xpToNextTier: rank.xpToNext,
    progress: rank.progress,
  };
}

function RankBadge({ rank }: { rank: RankMeta }) {
  const suffix = rank.tier.key;
  const ringId = `rank-ring-${suffix}`;
  const wingId = `rank-wing-${suffix}`;
  const shieldId = `rank-shield-${suffix}`;
  const gemId = `rank-gem-${suffix}`;
  const ribbonId = `rank-ribbon-${suffix}`;
  const crownId = `rank-crown-${suffix}`;
  const circumference = 2 * Math.PI * 45;
  const progressOffset = circumference * (1 - rank.progress);

  return (
    <div className="relative h-[96px] w-[96px]">
      <svg className="h-full w-full overflow-visible" viewBox="0 0 100 100" aria-hidden="true">
        <defs>
          <linearGradient id={ringId} x1="10%" y1="10%" x2="90%" y2="90%">
            <stop offset="0%" stopColor={rank.tier.accentSoft} />
            <stop offset="100%" stopColor={rank.tier.accent} />
          </linearGradient>
          <linearGradient id={wingId} x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor={rank.tier.metal} />
            <stop offset="100%" stopColor={rank.tier.accentDeep} />
          </linearGradient>
          <linearGradient id={shieldId} x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor={rank.tier.accentSoft} />
            <stop offset="55%" stopColor={rank.tier.accent} />
            <stop offset="100%" stopColor={rank.tier.accentDeep} />
          </linearGradient>
          <linearGradient id={gemId} x1="50%" y1="0%" x2="50%" y2="100%">
            <stop offset="0%" stopColor="#FFFFFF" />
            <stop offset="30%" stopColor={rank.tier.accentSoft} />
            <stop offset="100%" stopColor={rank.tier.accent} />
          </linearGradient>
          <linearGradient id={ribbonId} x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor={rank.tier.ribbon} />
            <stop offset="50%" stopColor={rank.tier.accentDeep} />
            <stop offset="100%" stopColor={rank.tier.ribbon} />
          </linearGradient>
          <linearGradient id={crownId} x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#FFF5D7" />
            <stop offset="100%" stopColor={rank.tier.metal} />
          </linearGradient>
        </defs>

        <circle cx="50" cy="50" r="46" fill={rank.tier.glow} opacity="0.62" />
        <circle cx="50" cy="50" r="45" fill="none" stroke="#EEF2FF" strokeWidth="2.6" />
        <circle
          cx="50"
          cy="50"
          r="45"
          fill="none"
          stroke={`url(#${ringId})`}
          strokeWidth="4"
          strokeDasharray={circumference}
          strokeDashoffset={progressOffset}
          strokeLinecap="round"
          className="transition-all duration-1000 ease-out"
          transform="rotate(-90 50 50)"
        />

        <path d="M31 44 C21 39, 12 41, 7 49 C14 52, 18 56, 21 63 C26 58, 31 55, 37 54 L35 47 Z" fill={`url(#${wingId})`} opacity="0.88" />
        <path d="M69 44 C79 39, 88 41, 93 49 C86 52, 82 56, 79 63 C74 58, 69 55, 63 54 L65 47 Z" fill={`url(#${wingId})`} opacity="0.88" />

        <path d="M50 16 L67 25 L71 47 L50 74 L29 47 L33 25 Z" fill={`url(#${shieldId})`} stroke={rank.tier.metal} strokeWidth="1.6" />
        <path d="M38 21 L43 31 L50 25 L57 31 L62 21 L60 35 L40 35 Z" fill={`url(#${crownId})`} stroke={rank.tier.accentDeep} strokeWidth="0.8" />
        <polygon points="50,30 61,43 50,57 39,43" fill={`url(#${gemId})`} stroke="#FFFFFF" strokeOpacity="0.7" strokeWidth="1.2" />
        <path d="M44 43 L50 35 L56 43 L50 49 Z" fill={rank.tier.accentDeep} opacity="0.34" />

        <g opacity="0.9">
          <circle cx="35" cy="38" r="2" fill="#FFFFFF" fillOpacity="0.4" />
          <circle cx="65" cy="38" r="2" fill="#FFFFFF" fillOpacity="0.4" />
        </g>

        <rect x="32" y="66" width="36" height="11" rx="5.5" fill={`url(#${ribbonId})`} />
        <text x="50" y="74" textAnchor="middle" fontSize="8.5" fontWeight="700" letterSpacing="1.6" fill="#FFFFFF">
          {rank.divisionLabel || '巅峰'}
        </text>
      </svg>
    </div>
  );
}

function recommendationContentLabel(type: LearningRecommendation['contentType']) {
  if (type === 'method_card') return '方法卡';
  if (type === 'correction_card') return '纠偏卡';
  return '练习卡';
}

function recommendationQuestLabel(recommendation: LearningRecommendation) {
  if (recommendation.priority === 'high') return '推荐修炼';
  if (recommendation.contentType === 'correction_card') return '纠偏练习';
  if (recommendation.contentType === 'method_card') return '方法进阶';
  return '日常进阶';
}

function recommendationXpReward(recommendation: LearningRecommendation) {
  if (recommendation.contentType === 'correction_card') return 18;
  if (recommendation.contentType === 'method_card') return 20;
  return recommendation.priority === 'high' ? 28 : 24;
}

function entryTypeLabel(xpType: string) {
  if (xpType === 'codification') return '经验沉淀';
  if (xpType === 'reuse') return '方法复用';
  if (xpType === 'improvement') return '成长改进';
  return '复盘反思';
}

function contextTabLabel(tab?: string | null) {
  if (tab === 'tasks') return '任务与日历';
  if (tab === 'client_workspace') return '客户工作台';
  if (tab === 'strategic_accompaniment') return '战略陪伴';
  if (tab === 'growth_handbook' || tab === 'growth') return '成长手册';
  return '相关模块';
}

function ComposerModal({
  open,
  draft,
  setDraft,
  sourceOptions,
  saving,
  onClose,
  onSave,
}: {
  open: boolean;
  draft: DraftState;
  setDraft: React.Dispatch<React.SetStateAction<DraftState>>;
  sourceOptions: Array<{ value: string; label: string; helper: string }>;
  saving: boolean;
  onClose: () => void;
  onSave: () => Promise<void>;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-900/30 px-4 backdrop-blur-sm">
      <div
        className="w-full max-w-[720px] rounded-[28px] border border-white/70 bg-white p-6 shadow-[0_24px_80px_rgba(15,23,42,0.18)]"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start gap-4">
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 text-slate-400 transition-colors hover:bg-slate-50 hover:text-slate-600"
            aria-label="关闭新增沉淀"
          >
            <X className="h-4 w-4" />
          </button>
          <div className="flex-1">
            <p className="text-[11px] font-medium uppercase tracking-[0.24em] text-slate-400">新增沉淀</p>
            <h3 className="mt-2 text-[24px] font-semibold tracking-tight text-slate-900">记录一条能复用的经验</h3>
            <p className="mt-2 text-[13px] leading-6 text-slate-500">
              把结论、适用场景、为什么成立，以及以后如何复用一起写清楚。
            </p>
          </div>
        </div>

        <div className="mt-6 grid gap-5 lg:grid-cols-[1.1fr_0.9fr]">
          <div className="space-y-4">
            <label className="block">
              <span className="mb-2 block text-[12px] font-semibold uppercase tracking-[0.18em] text-slate-400">标题</span>
              <input
                value={draft.title}
                onChange={(event) => setDraft((prev) => ({ ...prev, title: event.target.value }))}
                placeholder="这次沉淀要记住什么？"
                className="w-full rounded-[18px] border border-slate-200 bg-slate-50 px-4 py-3 text-[14px] font-medium text-slate-700 outline-none transition-colors placeholder:text-slate-400 focus:border-[#5B7BFE]/40 focus:bg-white"
              />
            </label>
            <label className="block">
              <span className="mb-2 block text-[12px] font-semibold uppercase tracking-[0.18em] text-slate-400">摘要</span>
              <textarea
                value={draft.summary}
                onChange={(event) => setDraft((prev) => ({ ...prev, summary: event.target.value }))}
                placeholder="把结论、适用场景、为什么成立，以及以后怎么复用写清楚。"
                className="min-h-[220px] w-full rounded-[22px] border border-slate-200 bg-slate-50 px-4 py-4 text-[14px] leading-7 text-slate-700 outline-none transition-colors placeholder:text-slate-400 focus:border-[#5B7BFE]/40 focus:bg-white"
              />
            </label>
            <label className="block">
              <span className="mb-2 block text-[12px] font-semibold uppercase tracking-[0.18em] text-slate-400">标签</span>
              <input
                value={draft.tags}
                onChange={(event) => setDraft((prev) => ({ ...prev, tags: event.target.value }))}
                placeholder="标签，多个用逗号分隔"
                className="w-full rounded-[18px] border border-slate-200 bg-slate-50 px-4 py-3 text-[14px] font-medium text-slate-700 outline-none transition-colors placeholder:text-slate-400 focus:border-[#5B7BFE]/40 focus:bg-white"
              />
            </label>
          </div>

          <div className="space-y-3">
            <p className="text-[12px] font-semibold uppercase tracking-[0.18em] text-slate-400">来源归类</p>
            {sourceOptions.map((option) => {
              const active = option.value === draft.sourceType;
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setDraft((prev) => ({ ...prev, sourceType: option.value }))}
                  className={cx(
                    'w-full rounded-[20px] border px-4 py-4 text-left transition-colors',
                    active ? 'border-[#8EB3FF] bg-[#EEF4FF]' : 'border-slate-200 bg-white hover:border-slate-300',
                  )}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-[14px] font-semibold text-slate-800">{option.label}</div>
                      <div className="mt-1 text-[12px] leading-5 text-slate-500">{option.helper}</div>
                    </div>
                    <div className={cx('h-5 w-5 rounded-full border', active ? 'border-[#335CFF] bg-white' : 'border-slate-300')}>
                      <div className={cx('m-[3px] h-2.5 w-2.5 rounded-full', active ? 'bg-[#335CFF]' : 'bg-transparent')} />
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        <div className="mt-6 flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-slate-200 bg-white px-4 py-2 text-[13px] font-medium text-slate-600 transition-colors hover:bg-slate-50"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => void onSave()}
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-full bg-[#335CFF] px-4 py-2 text-[13px] font-medium text-white transition-colors hover:bg-[#2C50E0] disabled:cursor-not-allowed disabled:opacity-60"
          >
            <PlusCircle className="h-4 w-4" />
            {saving ? '保存中...' : '写入成长手册'}
          </button>
        </div>
      </div>
    </div>
  );
}

export function GrowthHandbookView({
  entries,
  settings,
  currentClientId,
  tasks = [],
  onCreateEntry,
  onTasksReload,
  onNavigate,
  onOpenContext,
  flash,
}: GrowthHandbookViewProps) {
  const [activeTab, setActiveTab] = useState<GrowthHandbookTab>('overview');
  const defaultTagsText = useMemo(() => settings.defaultTags.join(', '), [settings.defaultTags]);
  const sourceOptions = useMemo(
    () => [
      { value: 'manual', label: '手动沉淀', helper: '自己整理经验、判断或结论' },
      { value: 'meeting', label: '会议结论', helper: '把会议共识和行动准则沉淀下来' },
      { value: 'topic_candidate', label: '情报候选', helper: '记录情报站里的观察与启发' },
      ...(settings.allowTaskSource ? [{ value: 'task', label: '任务复盘', helper: '补写任务推进中的方法和复盘' }] : []),
      ...(settings.allowAnalysisSource ? [{ value: 'analysis', label: '分析学习', helper: '承接测试工作台里的学习点' }] : []),
    ],
    [settings.allowAnalysisSource, settings.allowTaskSource],
  );
  const [draft, setDraft] = useState<DraftState>(() => buildDraft(defaultTagsText, sourceOptions[0]?.value || 'manual'));
  const [isComposerOpen, setIsComposerOpen] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [composerCaptureId, setComposerCaptureId] = useState<string | null>(null);
  const growthState = useGrowthOverviewState();
  const [fallbackGrowthOverview, setFallbackGrowthOverview] = useState<GrowthOverview | null>(null);
  const [fallbackGrowthLoading, setFallbackGrowthLoading] = useState(false);
  const [schedulingRecommendationId, setSchedulingRecommendationId] = useState<string | null>(null);
  const [dismissingRecommendationId, setDismissingRecommendationId] = useState<string | null>(null);
  const [updatingCaptureId, setUpdatingCaptureId] = useState<string | null>(null);
  const [isAssetDrawerOpen, setIsAssetDrawerOpen] = useState(false);
  const [isLedgerDrawerOpen, setIsLedgerDrawerOpen] = useState(false);
  const [ledgerAbilityFocus, setLedgerAbilityFocus] = useState<GrowthAbilityKey | null>(null);
  const [learningWorkbenchSnapshot, setLearningWorkbenchSnapshot] = useState<GrowthWorkbenchSnapshot | null>(null);
  const growthOverview = growthState?.growthOverview ?? fallbackGrowthOverview;
  const isGrowthLoading = growthState?.isGrowthLoading ?? fallbackGrowthLoading;

  const loadGrowthState = async () => {
    try {
      if (growthState) {
        await Promise.all([
          growthState.refreshGrowthOverview(),
          getGrowthWorkbench()
            .then((snapshot) => setLearningWorkbenchSnapshot(snapshot))
            .catch(() => undefined),
        ]);
        return;
      }
      setFallbackGrowthLoading(true);
      const [response, snapshot] = await Promise.all([
        getGrowthOverview(),
        getGrowthWorkbench().catch(() => null),
      ]);
      setFallbackGrowthOverview(response);
      setLearningWorkbenchSnapshot(snapshot);
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '成长数据加载失败');
    } finally {
      if (!growthState) {
        setFallbackGrowthLoading(false);
      }
    }
  };

  useEffect(() => {
    setDraft((prev) => {
      if (prev.title.trim() || prev.summary.trim()) return prev;
      return buildDraft(defaultTagsText, sourceOptions[0]?.value || 'manual');
    });
  }, [defaultTagsText, sourceOptions]);

  useEffect(() => {
    if (!growthState) {
      void loadGrowthState();
    }
  }, [growthState]);

  useEffect(() => {
    if (sourceOptions.some((option) => option.value === draft.sourceType)) return;
    setDraft((prev) => ({ ...prev, sourceType: sourceOptions[0]?.value || 'manual' }));
  }, [draft.sourceType, sourceOptions]);

  const experienceCards = useMemo<ExperienceCard[]>(() => {
    return [...entries]
      .sort((left, right) => new Date(right.createdAt).getTime() - new Date(left.createdAt).getTime())
      .map((entry) => ({
        id: entry.id,
        title: entry.title,
        summary: entry.summary,
        source: getSourceLabel(entry.sourceType),
        tags: entry.tags.length ? entry.tags : ['未分类'],
        xp: getSourceXp(entry.sourceType),
        dateLabel: formatRelativeDate(entry.createdAt),
        type: getTypeLabel(entry.sourceType),
        isMethod: isMethodEntry(entry),
      }));
  }, [entries]);

  const dailyDrops = useMemo<DailyDrop[]>(() => {
    if (growthOverview?.recentEntries.length) {
      const grouped = new Map<string, DailyDrop>();
      growthOverview.recentEntries.forEach((entry) => {
        const key = [
          entry.sourceType,
          entry.sourceId || '',
          entry.taskId || '',
          entry.reviewId || '',
          entry.meetingId || '',
          entry.handbookEntryId || '',
          entry.createdAt,
        ].join('|');
        const existing = grouped.get(key);
        if (existing) {
          existing.xp += entry.totalXp || entry.delta;
          existing.baseXp = (existing.baseXp || 0) + (entry.baseXp || 0);
          existing.premiumXp = (existing.premiumXp || 0) + (entry.premiumXp || 0);
          existing.isSpecial = existing.isSpecial || entry.premiumXp > 0 || entry.xpType !== 'reflection' || entry.delta >= 14;
          existing.entryCount += 1;
          if (entry.abilityLabel && !existing.abilityLabels.includes(entry.abilityLabel)) {
            existing.abilityLabels.push(entry.abilityLabel);
          }
          return;
        }
        grouped.set(key, {
          id: key,
          task: entry.sourceTitle || entry.reason || entry.abilityLabel,
          time: formatRelativeMoment(entry.createdAt),
          createdAt: entry.createdAt,
          xp: entry.totalXp || entry.delta,
          baseXp: entry.baseXp,
          premiumXp: entry.premiumXp,
          premiumRate: entry.premiumRate,
          type: entryTypeLabel(entry.xpType),
          isSpecial: entry.premiumXp > 0 || entry.xpType !== 'reflection' || entry.delta >= 14,
          abilityLabels: entry.abilityLabel ? [entry.abilityLabel] : [],
          entryCount: 1,
        });
      });
      return [...grouped.values()]
        .sort((left, right) => new Date(right.createdAt).getTime() - new Date(left.createdAt).getTime())
        .slice(0, 5);
    }
    return [];
  }, [entries, growthOverview]);

  const weeklyXp = useMemo(() => {
    if (growthOverview) return growthOverview.weeklyXp;
    if (!entries.length) return 0;
    return entries.reduce((sum, entry) => {
      const ageMs = Date.now() - new Date(entry.createdAt).getTime();
      if (Number.isNaN(ageMs) || ageMs > 7 * 24 * 60 * 60 * 1000) return sum;
      return sum + getSourceXp(entry.sourceType);
    }, 0);
  }, [entries, growthOverview]);
  const abilityCards = useMemo<AbilityCard[]>(() => {
    if (growthOverview?.abilities.length) {
      const gapMap = new Map(growthOverview.abilityGaps.map((gap) => [gap.abilityKey, gap]));
      return growthOverview.abilities.map((ability) => {
        const visual = ABILITY_VISUALS[ability.abilityKey] || ABILITY_VISUALS.exec;
        const gap = gapMap.get(ability.abilityKey);
        return {
          id: ability.abilityKey,
          name: ability.label,
          currentScore: ability.currentScore,
          previousScore: ability.previousScore,
          requiredScore: gap?.requiredScore ?? ability.currentScore,
          stage: ability.stage,
          nextStage: ability.nextStage,
          icon: visual.icon,
          iconClassName: visual.iconClassName,
          bgClassName: visual.bgClassName,
          numericInc: ability.weeklyXp,
          evidence: ability.evidence,
          gapReason: gap?.reason,
          gapSourceLabel: gap?.sourceLabel,
          gapSourceType: gap?.sourceType,
          gapSourceId: gap?.sourceId,
        };
      });
    }
    return [];
  }, [growthOverview]);

  const learningCards = useMemo<LearningCard[]>(() => {
    if (growthOverview?.recommendations.length) {
      return growthOverview.recommendations.map((recommendation) => ({
        id: recommendation.id,
        theme: recommendation.title,
        reason: recommendation.reason || recommendation.summary,
        learnContent: {
          type: recommendationContentLabel(recommendation.contentType),
          title: recommendation.summary || recommendation.abilityLabel,
          icon: ABILITY_VISUALS[recommendation.abilityKey]?.icon || BookOpen,
        },
        practiceTask: recommendation.practiceTask || recommendation.body,
        isUrgent: recommendation.priority === 'high',
        xpReward: recommendationXpReward(recommendation),
        questType: recommendationQuestLabel(recommendation),
        recommendationId: recommendation.id,
        linkedTaskId: recommendation.linkedTaskId,
        clientName: recommendation.clientName,
        eventLineName: recommendation.eventLineName,
        projectStage: recommendation.projectStage,
        triggerNode: recommendation.triggerNode,
        whyNow: recommendation.whyNow,
        linkedContexts: recommendation.linkedContexts,
      }));
    }
    return [];
  }, [growthOverview]);

  const totalScore = useMemo(() => {
    if (growthOverview) return growthOverview.totalXp;
    if (!experienceCards.length) return 0;
    const tagBonus = new Set(experienceCards.flatMap((item) => item.tags)).size * 8;
    return experienceCards.length * 48 + weeklyXp + tagBonus;
  }, [experienceCards, growthOverview, weeklyXp]);
  const rankMeta = useMemo(() => decorateRank(growthOverview?.rank ?? buildFallbackRank(totalScore)), [growthOverview, totalScore]);
  const displayName = growthOverview?.userName?.trim() || '继续沉淀';

  const handleSave = async () => {
    if (!draft.title.trim() || !draft.summary.trim()) {
      flash('error', '请先填写标题和摘要');
      return;
    }
    setIsSaving(true);
    try {
      const createdEntry = await onCreateEntry({
        title: draft.title.trim(),
        summary: draft.summary.trim(),
        tags: parseTags(draft.tags),
        sourceType: draft.sourceType,
        clientId: currentClientId || undefined,
      });
      if (composerCaptureId) {
        await updateGrowthPendingCapture(composerCaptureId, {
          status: 'promoted',
          reason: '已从待放大的成长信号沉淀为经验资产',
          handbookEntryId: createdEntry?.id || null,
        });
      }
      setDraft(buildDraft(defaultTagsText, sourceOptions[0]?.value || 'manual'));
      setComposerCaptureId(null);
      setIsComposerOpen(false);
      setActiveTab('records');
      await loadGrowthState();
      flash('success', '已写入成长手册');
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '保存失败');
    } finally {
      setIsSaving(false);
    }
  };

  const handleScheduleRecommendation = async (recommendationId?: string | null) => {
    if (!recommendationId) {
      flash('success', '练习卡模板已展示，后续可以继续接更细的学习库');
      return;
    }
    setSchedulingRecommendationId(recommendationId);
    try {
      const response = await acceptGrowthRecommendation(recommendationId);
      if (onTasksReload) {
        await onTasksReload();
      }
      await loadGrowthState();
      flash('success', response.task ? `已排入日程：${response.task.title}` : '已排入日程');
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '排入日程失败');
    } finally {
      setSchedulingRecommendationId(null);
    }
  };

  const handleDismissRecommendation = async (recommendationId?: string | null) => {
    if (!recommendationId) {
      flash('success', '当前练习卡没有可忽略的推荐记录');
      return;
    }
    setDismissingRecommendationId(recommendationId);
    try {
      await dismissGrowthRecommendation(recommendationId, { reason: '当前优先处理别的任务，先从学习导航中移除' });
      await loadGrowthState();
      flash('success', '已忽略这条推荐，后续会根据新成长信号重新生成');
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '忽略推荐失败');
    } finally {
      setDismissingRecommendationId(null);
    }
  };

  const openLedgerForAbility = (abilityKey: GrowthAbilityKey) => {
    setLedgerAbilityFocus(abilityKey);
    setIsLedgerDrawerOpen(true);
  };

  const openContextLink = (context?: GrowthContextLink | null) => {
    if (!context) return;
    if (onOpenContext) {
      onOpenContext(context);
      flash('success', `已定位到「${context.label}」`);
      return;
    }
    const targetTab = context.tab === 'growth' ? 'growth_handbook' : context.tab;
    onNavigate?.(targetTab);
    flash('success', `已切到${contextTabLabel(targetTab)}，继续查看「${context.label}」`);
  };

  const openSeededComposer = (seed: { title: string; summary: string; sourceType?: string }) => {
    setDraft({
      title: seed.title,
      summary: seed.summary,
      tags: defaultTagsText,
      sourceType: seed.sourceType && sourceOptions.some((option) => option.value === seed.sourceType)
        ? seed.sourceType
        : settings.allowTaskSource
          ? 'task'
        : sourceOptions[0]?.value || 'manual',
    });
    setIsComposerOpen(true);
    flash('success', `已带着「${seed.title}」打开成长沉淀`);
  };

  const openBlankComposer = () => {
    setComposerCaptureId(null);
    setIsComposerOpen(true);
  };

  const openCaptureAsEntry = (capture: GrowthPendingCapture) => {
    setComposerCaptureId(capture.id);
    openSeededComposer({
      title: capture.title,
      summary: capture.summary || capture.nextActionText || '',
      sourceType: settings.allowTaskSource ? 'task' : sourceOptions[0]?.value || 'manual',
    });
  };

  const handleCaptureState = async (capture: GrowthPendingCapture, status: 'dismissed' | 'reviewed') => {
    setUpdatingCaptureId(capture.id);
    try {
      await updateGrowthPendingCapture(capture.id, {
        status,
        reason: status === 'dismissed' ? '当前先不放大这条成长信号' : '已经查看过这条成长信号，先标记为已处理',
      });
      await loadGrowthState();
      flash('success', status === 'dismissed' ? '已从待处理列表中移除这条成长信号' : '已标记为已处理');
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '更新成长信号状态失败');
    } finally {
      setUpdatingCaptureId(null);
    }
  };

  const openCaptureReview = (capture: GrowthPendingCapture) => {
    const reviewContext = capture.linkedContexts.find((context) => context.objectType === 'review');
    const taskContext = capture.linkedContexts.find((context) => context.objectType === 'task');
    if (reviewContext) {
      openContextLink(reviewContext);
      return;
    }
    if (taskContext) {
      openContextLink(taskContext);
      flash('success', '已回到任务，可继续补周复盘或闭环说明');
      return;
    }
    onNavigate?.('tasks');
    flash('success', '已切到任务与日程，可继续补周复盘');
  };

  const pendingCaptureActions = (capture: GrowthPendingCapture) => {
    const isUpdating = updatingCaptureId === capture.id;
    const actions: Array<{ key: string; label: string; onClick: () => void; disabled?: boolean }> = [];
    const primaryContext = capture.linkedContexts.find((context) => ['task', 'event_line', 'project_flow', 'project_module', 'meeting', 'client'].includes(context.objectType));
    if (primaryContext) {
      actions.push({
        key: 'source',
        label: '回到源对象',
        onClick: () => openContextLink(primaryContext),
        disabled: isUpdating,
      });
    }
    if (capture.missingReasons.some((reason) => reason.includes('复盘') || reason.includes('解释'))) {
      actions.push({
        key: 'review',
        label: '去补复盘',
        onClick: () => openCaptureReview(capture),
        disabled: isUpdating,
      });
    }
    if (capture.missingReasons.some((reason) => reason.includes('沉淀')) || capture.sourceType === 'task_context_candidate' || capture.sourceType === 'task_attachment_candidate') {
      actions.push({
        key: 'handbook',
        label: '沉淀为经验',
        onClick: () => openCaptureAsEntry(capture),
        disabled: isUpdating,
      });
    }
    actions.push({
      key: 'reviewed',
      label: isUpdating ? '处理中...' : '标记已处理',
      onClick: () => void handleCaptureState(capture, 'reviewed'),
      disabled: isUpdating,
    });
    actions.push({
      key: 'dismiss',
      label: isUpdating ? '处理中...' : '先不提醒',
      onClick: () => void handleCaptureState(capture, 'dismissed'),
      disabled: isUpdating,
    });
    if (!actions.length) {
      actions.push({
        key: 'default',
        label: '继续补动作',
        onClick: () => openCaptureReview(capture),
        disabled: isUpdating,
      });
    }
    return actions;
  };

  const growthHighlights = useMemo<GrowthProjectHighlight[]>(
    () => [...(growthOverview?.projectGrowthHighlights || []), ...(growthOverview?.eventLineGrowthHighlights || []), ...(growthOverview?.strategicAlignmentHighlights || [])],
    [growthOverview],
  );

  const topAbilityGaps = useMemo<GrowthAbilityGap[]>(() => (growthOverview?.abilityGaps || []).slice(0, 3), [growthOverview]);
  const pendingCaptures = useMemo<GrowthPendingCapture[]>(() => growthOverview?.pendingCaptures || [], [growthOverview]);
  const currentFocusActions = useMemo<GrowthFocusAction[]>(() => growthOverview?.currentFocusActions || [], [growthOverview]);

  const TabItem = ({ label, id }: { label: string; id: GrowthHandbookTab }) => {
    const isActive = activeTab === id;
    return (
      <button
        type="button"
        onClick={() => setActiveTab(id)}
        className={cx(
          'rounded-2xl px-4 py-1.5 text-[13px] font-medium transition-colors',
          isActive ? 'bg-white text-[#5B7BFE] shadow-sm' : 'text-gray-500 hover:text-gray-700',
        )}
      >
        {label}
      </button>
    );
  };

  const OverviewView = () => (
    <div className="animate-in space-y-6 fade-in duration-300">
      <div className="flex flex-col items-center justify-between gap-6 rounded-[24px] border border-gray-100 bg-white p-6 shadow-sm lg:flex-row lg:p-8">
        <div className="flex items-center space-x-6">
          <RankBadge rank={rankMeta} />

          <div className="space-y-2">
            <h1 className="flex items-center text-[20px] font-semibold tracking-tight text-gray-800 lg:text-[22px]">
              下午好，{displayName}
              <span
                className="ml-3 rounded-full px-2.5 py-0.5 text-[10px] font-medium uppercase tracking-widest"
                style={{ backgroundColor: `${rankMeta.tier.accent}1A`, color: rankMeta.tier.accentDeep }}
              >
                {rankMeta.fullLabel}
              </span>
            </h1>
            <p className="text-[13px] font-medium leading-5 text-gray-500">
              成长手册 · 当前总经验 <span className="font-semibold text-gray-700">{totalScore} XP</span>
              {rankMeta.nextLabel ? (
                <>
                  ，距离 <span className="font-semibold text-gray-700">{rankMeta.nextLabel}</span> 还需{' '}
                  <span className="font-semibold text-gray-700">{rankMeta.xpToNextTier} XP</span>
                </>
              ) : (
                <>，已进入最高段位序列</>
              )}
            </p>
            <div className="flex items-center space-x-2 pt-1.5">
              <span className="flex items-center rounded-full border border-orange-100/50 bg-orange-50 px-2 py-0.5 text-[11px] font-medium tracking-wide text-orange-600">
                <Flame className="mr-1 h-3 w-3" /> 连续 {Math.max(1, Math.min(6, experienceCards.length))} 周沉淀
              </span>
              <span className="flex items-center rounded-full bg-[#5B7BFE]/10 px-2 py-0.5 text-[11px] font-medium tracking-wide text-[#5B7BFE]">
                <Sparkles className="mr-1 h-3 w-3" /> 本周总增量 {weeklyXp} XP
              </span>
              <span
                className="flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium tracking-wide"
                style={{ backgroundColor: `${rankMeta.tier.accent}14`, color: rankMeta.tier.accentDeep }}
              >
                段位进度 {Math.round(rankMeta.progress * 100)}%
              </span>
            </div>
          </div>
        </div>

        <div className="flex w-full pr-4 md:w-auto">
          <div className="flex min-w-[80px] flex-col justify-center">
            <div className="text-[26px] font-semibold leading-none tracking-tighter text-gray-800 lg:text-[32px]">+{weeklyXp}</div>
            <div className="mt-2 text-[11px] font-medium uppercase tracking-widest text-gray-400">本周增量</div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <div className="space-y-3">
            <div className="flex items-end justify-between px-1 pb-1">
              <h2 className="text-[16px] font-semibold text-gray-800">近期心得与复盘</h2>
              <span className="text-[11px] font-medium tracking-wider text-gray-400">完成任务不加 XP，沉淀才加</span>
            </div>

            {dailyDrops.length ? (
              <div className="overflow-hidden rounded-[24px] border border-gray-100 bg-white py-1 shadow-sm">
                {dailyDrops.map((drop, index) => (
                  <div
                    key={drop.id}
                    className={cx(
                      'flex items-center justify-between px-6 py-4 transition-colors hover:bg-gray-50/50',
                      index !== dailyDrops.length - 1 && 'border-b border-gray-50',
                    )}
                  >
                    <div className="flex min-w-0 items-center space-x-4">
                      <div className="min-w-0">
                        <div className="truncate text-[13px] font-medium leading-5 text-gray-700">{drop.task}</div>
                        {drop.abilityLabels.length > 1 ? (
                          <div className="mt-1 text-[10px] font-medium tracking-wide text-gray-400">
                            {drop.abilityLabels.join(' / ')}
                          </div>
                        ) : null}
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <span
                          className={cx(
                            'rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider',
                            drop.isSpecial ? 'bg-orange-50 text-orange-600' : 'bg-gray-100 text-gray-500',
                          )}
                        >
                          {drop.type}
                        </span>
                        {drop.entryCount > 1 ? (
                          <span className="rounded-full bg-[#EEF3FF] px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-[#335CFE]">
                            {drop.entryCount} 项能力
                          </span>
                        ) : null}
                      </div>
                    </div>
                    <div className="flex items-center space-x-4">
                      <span className="text-[11px] font-medium text-gray-400">{drop.time}</span>
                      <div className="text-right">
                        <div className={cx('text-[13px] font-semibold tracking-tight', drop.isSpecial ? 'text-orange-600' : 'text-[#5B7BFE]')}>
                          +{drop.xp} XP
                        </div>
                        {drop.premiumXp ? (
                          <div className="text-[10px] font-medium tracking-wide text-gray-400">
                            基础 +{drop.baseXp || Math.max(0, drop.xp - drop.premiumXp)} / 溢价 +{drop.premiumXp}
                          </div>
                        ) : null}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-[24px] border border-dashed border-gray-200 bg-white px-5 py-10 text-center">
                <div className="mx-auto mb-3 w-10 h-10 rounded-full bg-indigo-50 flex items-center justify-center">
                  <BookOpen className="w-5 h-5 text-indigo-500" />
                </div>
                <p className="text-[14px] font-bold text-gray-600 mb-1">成长账本还是空的</p>
                <p className="text-[13px] text-gray-400 max-w-md mx-auto">完成一条任务并写下复盘心得，或者在周复盘中留下反思，系统就会自动生成第一条成长记录。</p>
              </div>
            )}
          </div>

          <div className="space-y-3">
            <div className="flex items-end justify-between px-1 pb-1">
              <h2 className="text-[16px] font-semibold text-gray-800">成长呼应关系</h2>
              <span className="text-[11px] font-medium tracking-wider text-gray-400">任务 / 会议 / 项目 / 战略正在如何带动成长</span>
            </div>
            {growthHighlights.length ? (
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                {growthHighlights.slice(0, 4).map((highlight) => (
                  <div key={`${highlight.type}-${highlight.id}`} className="rounded-[24px] border border-gray-100 bg-white p-5 shadow-sm">
                    <div className="flex items-center justify-between gap-3">
                      <span className="rounded-full bg-[#EEF3FF] px-2 py-0.5 text-[10px] font-medium uppercase tracking-widest text-[#335CFE]">{highlight.type}</span>
                      <span className="text-[13px] font-semibold tracking-tight text-[#335CFE]">+{highlight.weeklyXp} XP</span>
                    </div>
                    <h3 className="mt-3 text-[15px] font-semibold text-slate-900">{highlight.label}</h3>
                    <p className="mt-2 text-[12px] leading-6 text-slate-500">{highlight.summary}</p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {highlight.abilityKeys.map((abilityKey) => (
                        <span key={`${highlight.id}-${abilityKey}`} className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.18em] text-slate-500">
                          {abilityKey}
                        </span>
                      ))}
                    </div>
                    {highlight.contexts.length ? (
                      <div className="mt-4 flex flex-wrap gap-2">
                        {highlight.contexts.slice(0, 3).map((context) => (
                          <button
                            key={`${highlight.id}-${context.objectType}-${context.objectId}`}
                            type="button"
                            onClick={() => openContextLink(context)}
                            className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-[11px] font-medium text-slate-600 transition hover:border-[#C9D7FF] hover:text-[#335CFE]"
                          >
                            {context.label}
                          </button>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-[24px] border border-dashed border-gray-200 bg-white px-5 py-10 text-center">
                <div className="mx-auto mb-3 w-10 h-10 rounded-full bg-purple-50 flex items-center justify-center">
                  <GitMerge className="w-5 h-5 text-purple-500" />
                </div>
                <p className="text-[14px] font-bold text-gray-600 mb-1">还没有形成成长呼应</p>
                <p className="text-[13px] text-gray-400 max-w-md mx-auto">当你的任务、会议和事件线开始联动时，系统会自动识别哪些行动带来了真实成长，并在这里展示呼应关系。</p>
              </div>
            )}
          </div>

          <div className="space-y-3">
            <div className="flex items-end justify-between px-1 pb-1">
              <h2 className="text-[16px] font-semibold text-gray-800">本周成长来自哪里</h2>
              <span className="text-[11px] font-medium tracking-wider text-gray-400">按模块、客户与事件线聚合</span>
            </div>
            {growthOverview ? (
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                {[
                  { label: '任务信号', value: growthOverview.sourceCoverage.taskSignals },
                  { label: '会议信号', value: growthOverview.sourceCoverage.meetingSignals },
                  { label: '战略信号', value: growthOverview.sourceCoverage.strategicSignals },
                  { label: '周判断', value: growthOverview.sourceCoverage.reviewSignals },
                  { label: '手册信号', value: growthOverview.sourceCoverage.handbookSignals },
                  { label: '涉及客户', value: growthOverview.sourceCoverage.clientCount },
                  { label: '事件线', value: growthOverview.sourceCoverage.eventLineCount },
                  { label: '推荐动作', value: currentFocusActions.length },
                ].map((item) => (
                  <div key={item.label} className="rounded-[22px] border border-gray-100 bg-white p-4 shadow-sm">
                    <div className="text-[12px] font-medium text-gray-400">{item.label}</div>
                    <div className="mt-2 text-[24px] font-semibold tracking-tight text-slate-900">{item.value}</div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-[24px] border border-dashed border-gray-200 bg-white px-5 py-10 text-center">
                <p className="text-[14px] font-bold text-gray-600 mb-1">成长来源数据待积累</p>
                <p className="text-[13px] text-gray-400 max-w-md mx-auto">随着你完成任务、参加会议和提交复盘，这里会按来源分类展示你的成长信号分布。</p>
              </div>
            )}
          </div>

          <div className="space-y-3">
            <div className="flex items-end justify-between px-1 pb-1">
              <h2 className="text-[16px] font-semibold text-gray-800">系统已看到但还没放大的成长</h2>
              <span className="text-[11px] font-medium tracking-wider text-gray-400">缺资料 / 缺闭环 / 缺复盘时会先停在这里</span>
            </div>
            {pendingCaptures.length ? (
              <div className="space-y-3">
                {pendingCaptures.slice(0, 4).map((capture) => (
                  <div key={capture.id} className="rounded-[24px] border border-gray-100 bg-white p-5 shadow-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded-full bg-orange-50 px-2 py-0.5 text-[10px] font-medium uppercase tracking-widest text-orange-600">{capture.sourceType}</span>
                      {capture.clientName ? <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium uppercase tracking-widest text-slate-500">{capture.clientName}</span> : null}
                      {capture.eventLineName ? <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium uppercase tracking-widest text-slate-500">{capture.eventLineName}</span> : null}
                    </div>
                    <h3 className="mt-3 text-[15px] font-semibold text-slate-900">{capture.title}</h3>
                    <p className="mt-2 text-[12px] leading-6 text-slate-500">{capture.summary}</p>
                    <div className="mt-3 rounded-2xl bg-slate-50 px-3 py-3 text-[12px] leading-6 text-slate-600">{capture.nextActionText}</div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      {capture.missingReasons.map((reason) => (
                        <span key={`${capture.id}-${reason}`} className="rounded-full border border-orange-100 bg-orange-50 px-2.5 py-1 text-[11px] font-medium text-orange-700">
                          {reason}
                        </span>
                      ))}
                    </div>
                    {capture.linkedContexts.length ? (
                      <div className="mt-4 flex flex-wrap gap-2">
                        {capture.linkedContexts.map((context) => (
                          <button
                            key={`${capture.id}-${context.objectType}-${context.objectId}`}
                            type="button"
                            onClick={() => openContextLink(context)}
                            className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-[11px] font-medium text-slate-600 transition hover:border-[#C9D7FF] hover:text-[#335CFE]"
                          >
                            {context.label}
                          </button>
                        ))}
                      </div>
                    ) : null}
                    <div className="mt-4 flex flex-wrap gap-2">
                      {pendingCaptureActions(capture).map((action) => (
                        <button
                          key={`${capture.id}-${action.key}`}
                          type="button"
                          onClick={action.onClick}
                          disabled={action.disabled}
                          className={cx(
                            'rounded-full px-3 py-1.5 text-[11px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60',
                            action.key === 'handbook'
                              ? 'bg-[#335CFE] text-white hover:bg-[#2746C7]'
                              : 'border border-slate-200 bg-white text-slate-600 hover:border-[#C9D7FF] hover:text-[#335CFE]',
                          )}
                        >
                          {action.label}
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-[24px] border border-dashed border-gray-200 bg-white px-5 py-10 text-center">
                <p className="text-[14px] font-bold text-gray-600 mb-1">暂无待放大的信号</p>
                <p className="text-[13px] text-gray-400 max-w-md mx-auto">当系统检测到你的某些行动有成长潜力但还缺少闭环时，会在这里提醒你补充。</p>
              </div>
            )}
          </div>

          <div className="space-y-3">
            <div className="flex items-end justify-between px-1 pb-1">
              <h2 className="text-[16px] font-semibold text-gray-800">下一步最值得补的动作</h2>
              <button type="button" className="text-[12px] font-medium text-[#5B7BFE] transition-colors hover:text-[#335CFF]" onClick={() => setActiveTab('learning')}>
                去学习导航
              </button>
            </div>
            {currentFocusActions.length ? (
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                {currentFocusActions.slice(0, 4).map((focus) => (
                  <div key={focus.id} className="group flex flex-col justify-between rounded-[24px] border border-gray-100 bg-white p-5 shadow-sm transition-colors hover:border-[#5B7BFE]/30">
                    <div>
                      <div className="flex flex-wrap items-center gap-2">
                        {focus.clientName ? <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium uppercase tracking-widest text-slate-500">{focus.clientName}</span> : null}
                        {focus.eventLineName ? <span className="rounded-full bg-[#EEF3FF] px-2 py-0.5 text-[10px] font-medium uppercase tracking-widest text-[#335CFE]">{focus.eventLineName}</span> : null}
                        {focus.projectStage ? <span className="rounded-full bg-orange-50 px-2 py-0.5 text-[10px] font-medium uppercase tracking-widest text-orange-600">{focus.projectStage}</span> : null}
                      </div>
                      <h3 className="mt-3 text-[14px] font-semibold leading-snug text-gray-800">{focus.title}</h3>
                      <p className="mt-2 text-[12px] leading-6 text-gray-500">{focus.summary}</p>
                      <div className="mt-3 rounded-2xl bg-slate-50 px-3 py-3 text-[12px] leading-6 text-slate-600">{focus.whyNow}</div>
                    </div>
                    <div className="mt-4 flex flex-wrap gap-2">
                      {focus.linkedContexts.length ? (
                        focus.linkedContexts.slice(0, 3).map((context) => (
                          <button
                            key={`${focus.id}-${context.objectType}-${context.objectId}`}
                            type="button"
                            onClick={() => openContextLink(context)}
                            className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-[11px] font-medium text-slate-600 transition hover:border-[#C9D7FF] hover:text-[#335CFE]"
                          >
                            {context.label}
                          </button>
                        ))
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            ) : learningCards.length ? (
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                {learningCards.slice(0, 2).map((quest) => (
                  <div key={quest.id} className="rounded-[24px] border border-gray-100 bg-white p-5 shadow-sm">
                    <div className="flex items-center gap-2">
                      <span className={cx('rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-widest', quest.isUrgent ? 'bg-orange-50 text-orange-600' : 'bg-gray-100 text-gray-500')}>
                        {quest.questType}
                      </span>
                    </div>
                    <h3 className="mt-3 text-[14px] font-semibold text-slate-900">{quest.theme}</h3>
                    <p className="mt-2 text-[12px] leading-6 text-slate-500">{quest.whyNow || quest.reason}</p>
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-[24px] border border-dashed border-gray-200 bg-white px-5 py-10 text-center">
                <p className="text-[14px] font-bold text-gray-600 mb-1">暂无动作推荐</p>
                <p className="text-[13px] text-gray-400 max-w-md mx-auto">当项目或事件线上出现能力缺口时，系统会在这里推荐最值得补的下一步动作。</p>
              </div>
            )}
          </div>
        </div>

        <div className="space-y-3">
          <div className="flex items-end justify-between px-1 pb-1">
            <h2 className="text-[16px] font-semibold text-gray-800">近期成长最快</h2>
          </div>

          <div className="space-y-6 rounded-[24px] border border-gray-100 bg-white p-6 shadow-sm">
            {[...abilityCards].sort((left, right) => right.numericInc - left.numericInc).slice(0, 4).map((ability) => {
              const AbilityIcon = ability.icon;
              return (
                <div key={ability.id} className="group">
                  <div className="mb-2.5 flex items-center justify-between">
                    <div className="flex items-center space-x-2">
                      <div className={cx('rounded-lg p-1.5', ability.bgClassName)}>
                        <AbilityIcon className={cx('h-[15px] w-[15px]', ability.iconClassName)} />
                      </div>
                      <span className="text-[13px] font-semibold leading-5 text-gray-800">{ability.name}</span>
                    </div>
                    <span className="text-[13px] font-semibold tracking-tight text-[#5B7BFE]">+{ability.numericInc}</span>
                  </div>

                  <div className="h-1.5 w-full overflow-hidden rounded-full bg-gray-50">
                    <div className="h-full rounded-full bg-[#5B7BFE]" style={{ width: `${ability.currentScore}%` }} />
                  </div>
                  <div className="mt-1.5 flex items-center justify-between">
                    <div className="text-[10px] font-medium uppercase tracking-widest text-gray-400">{ability.stage}期</div>
                    <button
                      type="button"
                      onClick={() => openLedgerForAbility(ability.id as GrowthAbilityKey)}
                      className="text-[11px] font-medium text-[#335CFE] transition-colors hover:text-[#2746C7]"
                    >
                      查看账本
                    </button>
                  </div>
                </div>
              );
            })}
            {!abilityCards.length ? (
              <div className="text-center py-4">
                <p className="text-[13px] font-bold text-gray-500 mb-1">能力雷达待激活</p>
                <p className="text-[12px] text-gray-400">完成任务并写下复盘后，六维能力分布会在这里自动生成。</p>
              </div>
            ) : null}
          </div>

          <div className="space-y-4 rounded-[24px] border border-gray-100 bg-white p-6 shadow-sm">
            <div className="flex items-center justify-between">
              <h2 className="text-[16px] font-semibold text-gray-800">当前能力差距</h2>
              <span className="text-[11px] font-medium tracking-wider text-gray-400">当前项目 / 事件线要求 vs 我的能力</span>
            </div>
            {topAbilityGaps.length ? (
              topAbilityGaps.map((gap) => (
                <button
                  key={`${gap.sourceType}-${gap.sourceId}-${gap.abilityKey}`}
                  type="button"
                  onClick={() => {
                    if (gap.sourceId && ['task', 'event_line', 'client', 'project_module', 'project_flow', 'strategic_focus', 'meeting'].includes(gap.sourceType)) {
                      openContextLink({
                        objectType: gap.sourceType,
                        objectId: gap.sourceId,
                        label: gap.sourceLabel || gap.label,
                        subtitle: gap.reason,
                        tab: gap.sourceType === 'client' ? 'client_workspace' : gap.sourceType === 'strategic_focus' ? 'strategic_accompaniment' : 'tasks',
                        statusLabel: '能力差距',
                      });
                      return;
                    }
                    openLedgerForAbility(gap.abilityKey);
                  }}
                  className="w-full rounded-[20px] border border-gray-100 bg-slate-50/70 p-4 text-left transition hover:border-[#D4DEFF]"
                >
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-[13px] font-semibold text-slate-900">{gap.label}</div>
                      <div className="mt-1 text-[12px] text-slate-500">{gap.sourceLabel}</div>
                    </div>
                    <div className="text-right">
                      <div className="text-[13px] font-semibold text-[#335CFE]">差距 {gap.gap}</div>
                      <div className="text-[10px] uppercase tracking-widest text-slate-400">{gap.currentScore} / {gap.requiredScore}</div>
                    </div>
                  </div>
                  <div className="mt-3 text-[12px] leading-6 text-slate-600">{gap.reason}</div>
                </button>
              ))
            ) : (
              <div className="text-center py-2">
                <p className="text-[13px] font-bold text-gray-500 mb-1">没有明显的能力差距</p>
                <p className="text-[12px] text-gray-400">当前项目和事件线对你的能力要求都在覆盖范围内。</p>
              </div>
            )}
          </div>

          <button
            type="button"
            onClick={() => setActiveTab('map')}
            className="flex w-full items-center justify-center space-x-2 rounded-[24px] border border-gray-100 bg-white p-4 text-[12px] font-medium tracking-wide text-gray-600 shadow-sm transition-colors hover:bg-gray-50"
          >
            <BrainCircuit className="h-4 w-4 text-gray-400" />
            <span>查看完整能力图谱</span>
          </button>
        </div>
      </div>
    </div>
  );

  const MapView = () => {
    const radarSize = 280;
    const center = radarSize / 2;
    const radius = 90;
    const numSides = abilityCards.length;

    const getPolygonPoints = (key: 'currentScore' | 'previousScore' | 'requiredScore') =>
      abilityCards.map((item, index) => {
        const angle = (Math.PI * 2 * index) / numSides - Math.PI / 2;
        const r = (item[key] / 100) * radius;
        return `${center + r * Math.cos(angle)},${center + r * Math.sin(angle)}`;
      }).join(' ');

    const gridLevels = [20, 40, 60, 80, 100];

    return (
      <div className="animate-in flex flex-col gap-6 fade-in duration-300 lg:flex-row">
        <div className="flex flex-col items-center rounded-[24px] border border-gray-100 bg-white p-8 shadow-sm lg:w-1/2">
          <h3 className="mb-6 w-full text-center text-[16px] font-semibold text-gray-800">核心 6 项能力分布</h3>

          <div className="relative flex w-full justify-center">
            <svg width={radarSize} height={radarSize} className="overflow-visible">
              {gridLevels.map((levelValue) => (
                <polygon
                  key={`grid-${levelValue}`}
                  points={abilityCards.map((_, index) => {
                    const angle = (Math.PI * 2 * index) / numSides - Math.PI / 2;
                    const r = (levelValue / 100) * radius;
                    return `${center + r * Math.cos(angle)},${center + r * Math.sin(angle)}`;
                  }).join(' ')}
                  fill="none"
                  stroke="#F3F4F6"
                  strokeWidth="1"
                />
              ))}

              {abilityCards.map((_, index) => {
                const angle = (Math.PI * 2 * index) / numSides - Math.PI / 2;
                return (
                  <line
                    key={`axis-${index}`}
                    x1={center}
                    y1={center}
                    x2={center + radius * Math.cos(angle)}
                    y2={center + radius * Math.sin(angle)}
                    stroke="#F3F4F6"
                    strokeWidth="1"
                  />
                );
              })}

              <polygon points={getPolygonPoints('previousScore')} fill="#9CA3AF" fillOpacity="0.08" stroke="#D1D5DB" strokeWidth="1.5" strokeDasharray="3 3" />
              <polygon points={getPolygonPoints('requiredScore')} fill="#F59E0B" fillOpacity="0.04" stroke="#F59E0B" strokeWidth="1.5" strokeDasharray="6 4" />
              <polygon points={getPolygonPoints('currentScore')} fill="#5B7BFE" fillOpacity="0.1" stroke="#5B7BFE" strokeWidth="2" strokeLinejoin="round" />

              {abilityCards.map((item, index) => {
                const angle = (Math.PI * 2 * index) / numSides - Math.PI / 2;
                const r = (item.currentScore / 100) * radius;
                const cxPoint = center + r * Math.cos(angle);
                const cyPoint = center + r * Math.sin(angle);
                return <circle key={`dot-${item.id}`} cx={cxPoint} cy={cyPoint} r="3.5" fill="#5B7BFE" />;
              })}

              {abilityCards.map((item, index) => {
                const angle = (Math.PI * 2 * index) / numSides - Math.PI / 2;
                const labelRadius = radius + 25;
                const x = center + labelRadius * Math.cos(angle);
                const y = center + labelRadius * Math.sin(angle);
                let textAnchor: 'start' | 'middle' | 'end' = 'middle';
                if (Math.abs(Math.cos(angle)) > 0.1) textAnchor = Math.cos(angle) > 0 ? 'start' : 'end';
                return (
                  <text
                    key={`label-${item.id}`}
                    x={x}
                    y={y + 4}
                    textAnchor={textAnchor}
                    fontSize="11"
                    fill="#6B7280"
                    fontWeight="500"
                    className="uppercase tracking-wide"
                  >
                    {item.name}
                  </text>
                );
              })}
            </svg>
          </div>

          <div className="mt-8 flex flex-wrap items-center gap-5 text-[11px] font-medium uppercase tracking-widest text-gray-400">
            <div className="flex items-center">
              <div className="mr-2 h-2.5 w-2.5 rounded-full bg-[#5B7BFE]" />
              当前水平
            </div>
            <div className="flex items-center">
              <div className="mr-2 h-2.5 w-2.5 rounded-full border border-gray-300 bg-white" />
              30天前
            </div>
            <div className="flex items-center">
              <div className="mr-2 h-2.5 w-2.5 rounded-full bg-amber-400" />
              当前项目要求
            </div>
          </div>
        </div>

        <div className="space-y-4 lg:w-1/2">
          <h2 className="border-b border-gray-100 pb-2 text-[16px] font-semibold text-gray-800">能力明细账</h2>
          <div className="grid grid-cols-1 gap-3">
            {abilityCards.map((ability) => (
              <button
                key={ability.id}
                type="button"
                onClick={() => {
                  if (ability.gapSourceType && ability.gapSourceId && ['task', 'event_line', 'client', 'project_module', 'project_flow', 'strategic_focus', 'meeting'].includes(ability.gapSourceType)) {
                    openContextLink({
                      objectType: ability.gapSourceType,
                      objectId: ability.gapSourceId,
                      label: ability.gapSourceLabel || ability.name,
                      subtitle: ability.gapReason || '',
                      tab: ability.gapSourceType === 'client' ? 'client_workspace' : ability.gapSourceType === 'strategic_focus' ? 'strategic_accompaniment' : 'tasks',
                      statusLabel: '能力差距',
                    });
                    return;
                  }
                  openLedgerForAbility(ability.id as GrowthAbilityKey);
                }}
                className="flex items-center justify-between rounded-[20px] border border-gray-100 bg-white p-4 text-left shadow-sm transition-colors hover:border-gray-200"
              >
                <div>
                  <div className="mb-1 flex items-center space-x-2">
                    <h4 className="text-[13px] font-semibold leading-5 text-gray-800">{ability.name}</h4>
                    <span className="rounded-md border border-gray-100 bg-gray-50 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-widest text-gray-400">
                      {ability.stage}期
                    </span>
                  </div>
                  <div className="text-[11px] font-medium text-gray-400">
                    当前 {ability.currentScore}% · 要求 {ability.requiredScore}%
                  </div>
                  {ability.gapReason ? (
                    <div className="mt-2 text-[11px] leading-5 text-slate-500">{ability.gapReason}</div>
                  ) : null}
                </div>
                <div className="text-right">
                  <div className="text-[13px] font-semibold tracking-tight text-[#5B7BFE]">+{ability.currentScore - ability.previousScore} XP</div>
                  <div className="mt-0.5 text-[10px] font-medium uppercase tracking-widest text-gray-400">
                    {ability.requiredScore > ability.currentScore ? `差距 ${ability.requiredScore - ability.currentScore}` : '已达当前要求'}
                  </div>
                </div>
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto bg-[#F9FAFB] text-gray-800">
      <ComposerModal
        open={isComposerOpen}
        draft={draft}
        setDraft={setDraft}
        sourceOptions={sourceOptions}
        saving={isSaving}
        onClose={() => {
          setComposerCaptureId(null);
          setIsComposerOpen(false);
        }}
        onSave={handleSave}
      />

      <GrowthAssetLibraryDrawer
        open={isAssetDrawerOpen}
        entries={entries}
        recentEntries={growthOverview?.recentEntries || []}
        flash={flash}
        onClose={() => setIsAssetDrawerOpen(false)}
        onRefresh={loadGrowthState}
        onOpenComposer={openBlankComposer}
        onNavigate={onNavigate}
        onOpenContext={onOpenContext}
      />

      <GrowthLedgerDrawer
        open={isLedgerDrawerOpen}
        growthOverview={growthOverview || null}
        flash={flash}
        onClose={() => setIsLedgerDrawerOpen(false)}
        initialAbilityKey={ledgerAbilityFocus}
        onOpenContext={onOpenContext}
      />

      <header className="mx-auto flex w-full max-w-7xl flex-col gap-4 px-5 pb-6 pt-6 md:flex-row md:items-center md:justify-between lg:px-8">
        <div className="flex w-full flex-col gap-3 md:flex-row md:items-center md:gap-6">
          <h1 className="text-[20px] font-semibold tracking-tight text-gray-800 lg:text-[22px]">成长手册</h1>

          <nav className="flex max-w-full items-center overflow-x-auto rounded-2xl bg-gray-100/80 p-1">
            <TabItem label="成长总览" id="overview" />
            <TabItem label="成长勋章" id="records" />
            <TabItem label="学习导航" id="learning" />
            <TabItem label="能力图谱" id="map" />
          </nav>
        </div>

        <div className="flex flex-wrap items-center gap-2 md:justify-end">
          <button
            type="button"
            onClick={() => setIsAssetDrawerOpen(true)}
            className="rounded-full border border-slate-200 bg-white px-4 py-2 text-[13px] font-medium text-slate-600 transition-colors hover:bg-slate-50"
          >
            经验资产
          </button>
          <button
            type="button"
            onClick={() => {
              setLedgerAbilityFocus(null);
              setIsLedgerDrawerOpen(true);
            }}
            className="rounded-full border border-slate-200 bg-white px-4 py-2 text-[13px] font-medium text-slate-600 transition-colors hover:bg-slate-50"
          >
            XP账本
          </button>
          <button
            type="button"
            onClick={openBlankComposer}
            className="flex items-center space-x-1.5 rounded-full bg-[#16A34A] px-4 py-2 text-[13px] font-medium text-white shadow-sm transition-colors hover:bg-[#15803D]"
          >
            <PlusCircle className="h-[16px] w-[16px]" />
            <span className="whitespace-nowrap">记录经验</span>
          </button>
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-7xl flex-1 flex-col px-5 pb-20 lg:px-8">
        {isGrowthLoading && !growthOverview ? (
          <div className="mb-4 rounded-[20px] border border-[#DCE7FF] bg-white px-4 py-3 text-[12px] font-medium text-[#5B7BFE] shadow-sm">
            成长引擎正在同步最近的复盘、沉淀和推荐练习...
          </div>
        ) : null}
        {activeTab === 'overview' && <OverviewView />}
        {activeTab === 'records' && <GrowthBadgeWall flash={flash} onNavigate={onNavigate} onOpenContext={onOpenContext} />}
        {activeTab === 'learning' && (
          <GrowthLearningWorkbench
            learningCards={learningCards}
            abilityCards={abilityCards}
            dailyDrops={dailyDrops}
            workbenchSnapshot={learningWorkbenchSnapshot}
            currentFocusActions={currentFocusActions}
            pendingCaptures={pendingCaptures}
            tasks={tasks}
            flash={flash}
            onScheduleRecommendation={handleScheduleRecommendation}
            onDismissRecommendation={handleDismissRecommendation}
            schedulingRecommendationId={schedulingRecommendationId}
            dismissingRecommendationId={dismissingRecommendationId}
            onOpenComposer={openBlankComposer}
            onSeedComposer={openSeededComposer}
            onNavigate={onNavigate}
            onOpenContext={onOpenContext}
          />
        )}
        {activeTab === 'map' && <MapView />}
      </main>
    </div>
  );
}

export default GrowthHandbookView;
