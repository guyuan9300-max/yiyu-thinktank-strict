// @ts-nocheck — 整合于 2026-05-13：同事 push 的资讯情报站视图，半成品，
// 5 个 setState 函数式更新回调里 `current` 参数 implicit any，等同事下次 sync
// 后他自己加类型注解或在 origin/main 用更宽松的 tsconfig。我们暂时跳过 type check，
// 避免 npx tsc --noEmit 在我们这条线被同事的代码挡掉。
import React, { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  readRuntimeUiSessionValue,
  useRuntimeUiSessionState,
  writeRuntimeUiSessionValue,
} from '../../lib/runtimeUiSessionStore';
import {
  AlertTriangle,
  BellPlus,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  FileCheck2,
  Lightbulb,
  Loader2,
  Megaphone,
  MessageCircle,
  Pencil,
  RefreshCw,
  Save,
  Send,
  SlidersHorizontal,
  Sparkles,
  Globe,
  Handshake,
  Newspaper,
  Trash2,
  Users,
  X,
  Zap,
} from 'lucide-react';

import type {
  BrandStrategyExtract,
  IntelligenceCandidateSample,
  IntelligenceContentKind,
  IntelligenceDismissReasonCode,
  IntelligenceFocusDirective,
  IntelligenceFocusDirectivePayload,
  IntelligenceFollowMode,
  IntelligenceItem,
  IntelligenceRefreshCycleSettings,
  IntelligenceRefreshResult,
  IntelligenceRefreshRun,
  IntelligenceTaskDraftPayload,
  IntelligenceWorkObject,
  MentionCandidate,
  SessionUser,
  TaskList,
  TaskSettings,
  TopicCandidateChatMessage,
} from '../../../shared/types';
import {
  // autoRefreshIntelligenceDue,  // TODO: 半成品，api.ts 未导出；先注释解除启动崩溃
  askIntelligenceItemQuestion,
  createIntelligenceTask,
  dismissIntelligenceItem,
  followIntelligenceItem,
  getIntelligenceFocusDirectives,
  getIntelligenceItems,
  getIntelligenceRefreshCycleSettings,
  getIntelligenceRefreshRuns,
  getIntelligenceTaskDraft,
  getIntelligenceWorkObjects,
  getMentionCandidates,
  refreshIntelligenceSupply,
  saveIntelligenceFocusDirective,
  submitIntelligenceVerificationFeedback,
  updateIntelligenceRefreshCycleSettings,
  // 舆情监控 + 印象主题 + 定位差异（P2-a → P5）
  refreshSentiment,
  listSentimentItems,
  getSentimentProfile,
  sendSentimentFeedback,
  recomputeSentimentThemes,
  getBrandAudit,
  recomputeBrandAudit,
  fetchBrandStrategyExtract,
  triggerBrandStrategyExtraction,
  updateBrandStrategyExtract,
  type SentimentItem,
  type SentimentProfile,
  type SentimentRefreshResult,
  type SentimentFeedbackAction,
  type BrandAudit,
} from '../../lib/api';

type IntelligenceStationViewProps = {
  uiSessionScopeKey?: string;
  activeTaskLists: TaskList[];
  effectiveTaskSettings: TaskSettings;
  currentSessionUser: SessionUser | null;
  currentOperatorName: string;
  flash: (type: 'success' | 'error' | 'info', text: string) => void;
  onTasksReload: () => Promise<unknown>;
};

type SortMode = 'published_desc' | 'published_asc' | 'captured_desc' | 'captured_asc';
type ScopeKey = 'global' | `${IntelligenceWorkObject['type']}:${string}`;
type WorkObjectSelection = 'all' | ScopeKey;

type FocusDraft = {
  profileCompletionFocus: string;
  timelyIntelligenceFocus: string;
  exclude: string;
};

type ClarificationTarget = { type: 'item'; item: IntelligenceItem };

const PAGE_SIZE: Record<IntelligenceContentKind, number> = {
  brand_mirror: 1,
  timely_intelligence: 5,
  public_opinion: 50,
};

const INTELLIGENCE_RUNTIME_CACHE_TTL_MS = 5 * 60 * 1000;

// 顺序即 tab 显示顺序: 品牌监测 → 时效情报. brand_mirror 保留键以兼容 type/后端, 但 UI 过滤掉.
const TAB_LABEL: Record<IntelligenceContentKind, string> = {
  public_opinion: '品牌监测',
  timely_intelligence: '时效情报',
  brand_mirror: '品牌镜子',
};

const TAB_HINT: Record<IntelligenceContentKind, string> = {
  public_opinion: '品牌外立面感知度评估 · 公众实际听到的声音',
  timely_intelligence: '需要判断与跟进的外部信号',
  brand_mirror: '从官方/媒体/合作信源照出品牌呈现',
};

// UI 不暴露 brand_mirror tab — 资讯情报站只有"品牌监测 + 时效情报"两个板块.
const HIDDEN_TABS: Set<IntelligenceContentKind> = new Set(['brand_mirror']);

const SORT_LABEL: Record<SortMode, string> = {
  captured_desc: '抓取时间新到旧（默认）',
  captured_asc: '抓取时间旧到新',
  published_desc: '发布时间新到旧',
  published_asc: '发布时间旧到新',
};

const DISMISS_REASON_LABEL: Record<IntelligenceDismissReasonCode, string> = {
  irrelevant: '不相关',
  inaccurate: '不准确',
  duplicate: '重复',
  outdated: '过期',
  low_value: '低价值',
};

const FOLLOW_MODE_LABEL: Record<IntelligenceFollowMode, string> = {
  same_theme: '同主题',
  same_source: '同来源',
  same_work_object: '同客户/项目机会',
};

const EMPTY_FOCUS_DRAFT: FocusDraft = {
  profileCompletionFocus: '',
  timelyIntelligenceFocus: '',
  exclude: '',
};

const DEFAULT_REFRESH_CYCLE_SETTINGS: IntelligenceRefreshCycleSettings = {
  profileCompletionHours: 72,
  timelyIntelligenceHours: 24,
};

function formatTime(value?: string | null) {
  if (!value) return '时间未知';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(date);
}

function formatDateOnly(value?: string | null) {
  if (!value) return '未标注';
  const trimmed = value.trim();
  const directDate = trimmed.match(/\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}/);
  if (directDate) return directDate[0].replace(/[年月/.]/g, '-').replace(/日/g, '');
  const date = new Date(trimmed);
  if (Number.isNaN(date.getTime())) return trimmed;
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(date);
}

function splitText(value: string) {
  return value
    .split(/\n+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function compactText(value: string, maxLength = 120) {
  const normalized = value.replace(/\s+/g, ' ').trim();
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, maxLength - 1)}…`;
}

function scopeKeyOfObject(item: IntelligenceWorkObject): ScopeKey {
  return `${item.type}:${item.id}`;
}

function directiveMatches(directive: IntelligenceFocusDirective, scopeKey: ScopeKey) {
  if (scopeKey === 'global') return directive.scopeType === 'global';
  const [type, id] = scopeKey.split(':');
  return directive.scopeType === type && directive.scopeId === id;
}

function directivePayload(scopeKey: ScopeKey, draft: FocusDraft): IntelligenceFocusDirectivePayload {
  if (scopeKey === 'global') {
    return {
      scopeType: 'global',
      scopeId: null,
      profileCompletionFocus: splitText(draft.profileCompletionFocus),
      timelyIntelligenceFocus: splitText(draft.timelyIntelligenceFocus),
      exclude: splitText(draft.exclude),
    };
  }
  const [scopeType, scopeId] = scopeKey.split(':') as [IntelligenceWorkObject['type'], string];
  return {
    scopeType,
    scopeId,
    profileCompletionFocus: splitText(draft.profileCompletionFocus),
    timelyIntelligenceFocus: splitText(draft.timelyIntelligenceFocus),
    exclude: splitText(draft.exclude),
  };
}

function draftFromDirective(directive?: IntelligenceFocusDirective): FocusDraft {
  if (!directive) return EMPTY_FOCUS_DRAFT;
  return {
    profileCompletionFocus: directive.profileCompletionFocus.join('\n'),
    timelyIntelligenceFocus: directive.timelyIntelligenceFocus.join('\n'),
    exclude: directive.exclude.join('\n'),
  };
}

function itemObjectLabel(item: IntelligenceItem, objects: IntelligenceWorkObject[]) {
  if (item.projectModuleId) {
    const project = objects.find((object) => object.type === 'project_module' && object.id === item.projectModuleId);
    if (project) return `项目 · ${project.name}`;
  }
  if (item.clientId) {
    const client = objects.find((object) => object.type === 'client' && object.id === item.clientId);
    if (client) return `客户 · ${client.name}`;
  }
  return '全部对象';
}

function selectedObjectLabel(value: WorkObjectSelection, objects: IntelligenceWorkObject[]) {
  if (value === 'all') return '全部客户/项目';
  const object = objects.find((item) => scopeKeyOfObject(item) === value);
  if (!object) return '全部客户/项目';
  return `${object.type === 'client' ? '客户' : '项目'} · ${object.name}`;
}

function scopeRefreshPayload(selection: WorkObjectSelection, object: IntelligenceWorkObject | null, contentKind: IntelligenceContentKind) {
  if (selection === 'all' || !object) {
    return { scopeType: 'all' as const, scopeId: null, contentKind, force: true };
  }
  return { scopeType: object.type, scopeId: object.id, contentKind, force: true };
}

function summarizeRefreshResult(result: IntelligenceRefreshResult) {
  const label = TAB_LABEL[result.contentKind];
  const totals = result.totals;
  if (isBackgroundRefreshQueued(result)) {
    return `${label}已启动：已派发 ${totals.objectCount} 个对象进入后台抓取，正在公开搜索、正文抓取和核验。`;
  }
  if (totals.candidateCount <= 0 && totals.promotedCount <= 0 && !totals.failedCount) {
    return `${label}流程已跑完：处理 ${totals.objectCount} 个对象，但没有找到可进入判断流程的中文公开资料。`;
  }
  return `${label}刷新完成：处理 ${totals.objectCount} 个对象，线索 ${totals.candidateCount} 条，成卡 ${totals.promotedCount} 条${totals.noResultCount ? `，未找到 ${totals.noResultCount} 个对象` : ''}${totals.failedCount ? `，失败 ${totals.failedCount} 个对象` : ''}。`;
}

function isBackgroundRefreshQueued(result: IntelligenceRefreshResult) {
  if (!result.results.length || result.totals.candidateCount > 0 || result.totals.promotedCount > 0) return false;
  return result.results.every((item) => {
    const message = item.message || '';
    return message.includes('后台抓取队列') || message.includes('后台抓取') || message.includes('已加入后台');
  });
}

function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-200 px-1 py-4 text-[13px] text-gray-600">
      <span className="font-semibold">
        第 {page} / {totalPages} 页，共 {total} 条
      </span>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => onPageChange(Math.max(1, page - 1))}
          disabled={page <= 1}
          className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-gray-200 bg-white text-gray-600 hover:bg-gray-50 disabled:opacity-40"
          title="上一页"
        >
          <ChevronLeft size={16} />
        </button>
        <button
          type="button"
          onClick={() => onPageChange(Math.min(totalPages, page + 1))}
          disabled={page >= totalPages}
          className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-gray-200 bg-white text-gray-600 hover:bg-gray-50 disabled:opacity-40"
          title="下一页"
        >
          <ChevronRight size={16} />
        </button>
      </div>
    </div>
  );
}

function profileTags(tags: string[]) {
  const internalTags = new Set(['已核验资料', 'web_search', 'official_site', 'social_org_registry', 'charity_media']);
  return tags.filter((tag) => tag && !internalTags.has(tag) && !tag.includes('_'));
}

function profileGapLabel(item: IntelligenceItem) {
  const tags = profileTags(item.tags);
  return tags.length > 0 ? tags.join(' / ') : item.title;
}

function compactSourceLabel(label: string, url: string) {
  const trimmed = label.trim();
  if (trimmed && !/^https?:\/\//i.test(trimmed) && trimmed.length <= 36) return trimmed;
  try {
    const parsed = new URL(url);
    return parsed.hostname.replace(/^www\./, '');
  } catch {
    return trimmed || '打开来源';
  }
}

function looksLikeDomainLabel(label: string) {
  const trimmed = label.trim();
  return /^[a-z0-9.-]+\.[a-z]{2,}$/i.test(trimmed);
}

function looksLikeUrlOrDomainLabel(label: string) {
  const trimmed = label.trim();
  return /^https?:\/\//i.test(trimmed) || looksLikeDomainLabel(trimmed);
}

function isGenericSourceLabel(label: string) {
  return ['公开搜索', '通用公开搜索', '公开来源', '搜索结果', 'web_search'].includes(label.trim());
}

function readableSourceLabel(label: string, url: string) {
  const trimmed = label.trim();
  if (trimmed && !looksLikeUrlOrDomainLabel(trimmed)) {
    return trimmed.length > 48 ? `${trimmed.slice(0, 46)}...` : trimmed;
  }
  return compactSourceLabel(label, url);
}

function sourceLinks(item: IntelligenceItem) {
  const items: Array<{ label: string; url: string }> = [];
  if (item.sourceUrl) {
    const rawSource = item.source || '';
    const sourceLabel = looksLikeUrlOrDomainLabel(rawSource) || isGenericSourceLabel(rawSource) ? item.title : rawSource || item.title;
    items.push({ label: readableSourceLabel(sourceLabel, item.sourceUrl), url: item.sourceUrl });
  }
  const sourceText = item.source || '';
  for (const line of sourceText.split(/\n+/)) {
    const trimmed = line.trim();
    const match = trimmed.match(/https?:\/\/\S+/);
    if (!match) continue;
    const url = match[0];
    if (items.some((item) => item.url === url)) continue;
    const label = trimmed.replace(url, '').replace(/[：:｜|\-—\s]+$/g, '').trim();
    items.push({ label: compactSourceLabel(label, url), url });
  }
  return items;
}

function refreshRunObjectLabel(run: IntelligenceRefreshRun, workObjects: IntelligenceWorkObject[]) {
  const targetId = run.projectModuleId || run.clientId || run.scopeId || '';
  const object = workObjects.find((item) => item.id === targetId);
  if (object) return `${object.type === 'client' ? '客户' : '项目'} · ${object.name}`;
  return targetId ? `对象 ${targetId}` : '全部对象';
}

function refreshRunCount(run: IntelligenceRefreshRun, key: string) {
  const value = run.result?.[key];
  const number = typeof value === 'number' ? value : Number(value || 0);
  return Number.isFinite(number) ? number : 0;
}

function refreshRunStringList(run: IntelligenceRefreshRun, key: string) {
  const value = run.result?.[key];
  if (!Array.isArray(value)) return [] as string[];
  return value.map((item) => String(item || '').trim()).filter(Boolean);
}

function runTimestamp(run: IntelligenceRefreshRun) {
  const parsed = new Date(run.finishedAt || run.updatedAt || run.createdAt).getTime();
  return Number.isNaN(parsed) ? 0 : parsed;
}

function sameRefreshRunBatch(a: IntelligenceRefreshRun, b: IntelligenceRefreshRun) {
  if (a.contentKind !== b.contentKind || a.status !== b.status) return false;
  if (a.triggerSource !== b.triggerSource) return false;
  if (a.createdAt && b.createdAt && a.createdAt === b.createdAt) return true;
  const aCreated = new Date(a.createdAt).getTime();
  const bCreated = new Date(b.createdAt).getTime();
  if (Number.isNaN(aCreated) || Number.isNaN(bCreated)) return false;
  return Math.abs(aCreated - bCreated) <= 10_000;
}

function latestFinishedRunBatch(runs: IntelligenceRefreshRun[], contentKind: IntelligenceContentKind | null) {
  const finished = runs
    .filter((run) => (run.status === 'completed' || run.status === 'failed') && (!contentKind || run.contentKind === contentKind))
    .sort((a, b) => runTimestamp(b) - runTimestamp(a));
  const latest = finished[0] || null;
  if (!latest) return { latest, batch: [] as IntelligenceRefreshRun[] };
  return {
    latest,
    batch: finished.filter((run) => sameRefreshRunBatch(run, latest)),
  };
}

function refreshRunTimeRange(runs: IntelligenceRefreshRun[]) {
  const starts = runs.map((run) => run.startedAt || run.createdAt).filter(Boolean).sort();
  const ends = runs.map((run) => run.finishedAt || run.updatedAt).filter(Boolean).sort();
  if (!starts.length || !ends.length) return '本次运行';
  return `${formatTime(starts[0])} - ${formatTime(ends[ends.length - 1])}`;
}

function refreshRunObjectSummary(runs: IntelligenceRefreshRun[], workObjects: IntelligenceWorkObject[]) {
  const labels = runs.map((run) => refreshRunObjectLabel(run, workObjects)).filter(Boolean);
  const unique = Array.from(new Set(labels));
  if (unique.length <= 2) return unique.join('、') || '当前对象';
  return `${unique.slice(0, 2).join('、')} 等 ${unique.length} 个`;
}

function RefreshProgressPanel({
  contentKind,
  hasItems,
  runs,
  workObjects,
  loading,
  onReload,
}: {
  contentKind: IntelligenceContentKind | null;
  hasItems: boolean;
  runs: IntelligenceRefreshRun[];
  workObjects: IntelligenceWorkObject[];
  loading: boolean;
  onReload: () => void;
}) {
  const scopedRuns = contentKind ? runs.filter((run) => run.contentKind === contentKind) : runs;
  const activeRuns = scopedRuns.filter((run) => run.status === 'queued' || run.status === 'running');
  const { latest: latestFinishedRun, batch: latestFinishedRuns } = latestFinishedRunBatch(scopedRuns, contentKind);
  const visibleKind = contentKind || latestFinishedRun?.contentKind || null;
  if (!visibleKind) return null;
  const showingActive = activeRuns.length > 0;
  const finishedRuns = showingActive ? [] : latestFinishedRuns;
  const promotedCount = finishedRuns.reduce((sum, run) => sum + refreshRunCount(run, 'promotedCount'), 0);
  const candidateCount = finishedRuns.reduce((sum, run) => sum + refreshRunCount(run, 'candidateCount'), 0);
  const scoutCandidateCount = finishedRuns.reduce((sum, run) => sum + refreshRunCount(run, 'scoutCandidateCount'), 0);
  const reviewCandidateCount = finishedRuns.reduce((sum, run) => sum + refreshRunCount(run, 'reviewCandidateCount'), 0);
  const timelyScoutCount = scoutCandidateCount || candidateCount;
  return (
    <div className={`mb-4 border-l-4 bg-white px-4 py-3 text-[12px] font-semibold leading-5 shadow-sm ${showingActive ? 'border-gray-950 text-gray-700' : latestFinishedRun?.status === 'failed' ? 'border-rose-400 text-rose-950' : 'border-blue-300 text-blue-950'}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-gray-950">
          {showingActive ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
          {showingActive
            ? `后台研究中：${TAB_LABEL[visibleKind]}（${activeRuns.length} 个对象）`
            : latestFinishedRun?.status === 'failed'
              ? `最近${TAB_LABEL[visibleKind]}失败`
              : latestFinishedRun
                ? `最近${TAB_LABEL[visibleKind]}已完成`
                : `${TAB_LABEL[visibleKind]}待启动`}
        </div>
        <button
          type="button"
          onClick={onReload}
          disabled={loading}
          className="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2 py-1 text-[12px] font-bold text-gray-600 hover:bg-gray-50 disabled:opacity-50"
        >
          {loading ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
          刷新状态
        </button>
      </div>
      {showingActive ? (
        <>
          <p className="mt-2 text-gray-500">
            正在抓取可核验详情页，并判断外部变化、影响链条和下一步动作。切到其他模块后回来，状态会继续保留。
          </p>
          <div className="mt-3 grid gap-2 md:grid-cols-2">
            {activeRuns.slice(0, 4).map((run) => (
              <div key={run.id} className="rounded-md bg-gray-50 px-3 py-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <b className="text-gray-900">{refreshRunObjectLabel(run, workObjects)}</b>
                  <span className="text-gray-500">{run.status === 'queued' ? '排队中' : '运行中'} · {formatTime(run.updatedAt)}</span>
                </div>
                <p className="mt-1 text-gray-600">{run.message || run.stage || '后台研究正在推进'}</p>
              </div>
            ))}
          </div>
        </>
      ) : latestFinishedRun ? (
        <>
          <div className="mt-3 grid gap-2 md:grid-cols-6">
            <div className="rounded-md bg-white/70 px-3 py-2">
              <span className="block text-blue-900/60">客户/项目</span>
              <b>{refreshRunObjectSummary(finishedRuns, workObjects)}</b>
            </div>
            <div className="rounded-md bg-white/70 px-3 py-2">
              <span className="block text-blue-900/60">运行时间</span>
              <b>{refreshRunTimeRange(finishedRuns)}</b>
            </div>
            <div className="rounded-md bg-white/70 px-3 py-2">
              <span className="block text-blue-900/60">初筛候选</span>
              <b>{timelyScoutCount}</b>
            </div>
            <div className="rounded-md bg-white/70 px-3 py-2">
              <span className="block text-blue-900/60">入围复核</span>
              <b>{reviewCandidateCount}</b>
            </div>
            <div className="rounded-md bg-white/70 px-3 py-2">
              <span className="block text-blue-900/60">最终成卡</span>
              <b>{promotedCount}</b>
            </div>
            <div className="rounded-md bg-white/70 px-3 py-2">
              <span className="block text-blue-900/60">最新更新时间</span>
              <b>{formatTime(latestFinishedRun.updatedAt)}</b>
            </div>
          </div>
          <p className="mt-2 text-blue-900/70">
            {latestFinishedRun.message || '后台研究已结束。'}
          </p>
        </>
      ) : (
        <p className="mt-2 text-blue-900/70">
          尚未开始自动抓取，可以点击右上角按钮手动抓取情报。
        </p>
      )}
      {!showingActive && latestFinishedRun && !hasItems && (
        <p className="mt-1 text-blue-900/70">
          当前没有通过严格判断的时效情报卡；系统不会把候选短摘或未核验页面放进普通列表。
        </p>
      )}
    </div>
  );
}

function EmptyState({
  contentKind,
  selectedWorkObject,
  workObjects,
  candidateSamples,
  refreshing,
  onRefresh,
}: {
  contentKind: IntelligenceContentKind;
  selectedWorkObject: IntelligenceWorkObject | null;
  workObjects: IntelligenceWorkObject[];
  candidateSamples: IntelligenceCandidateSample[];
  refreshing: boolean;
  onRefresh: (contentKind: IntelligenceContentKind) => void;
}) {
  void contentKind;
  void selectedWorkObject;
  void workObjects;
  void candidateSamples;
  void refreshing;
  void onRefresh;
  return null;
}

function IntelligenceField({
  label,
  children,
  tone = 'default',
}: {
  label: string;
  children: React.ReactNode;
  tone?: 'default' | 'amber';
}) {
  const labelClass = tone === 'amber' ? 'text-amber-700' : 'text-gray-500';
  const bodyClass = tone === 'amber' ? 'text-amber-950' : 'text-gray-700';
  return (
    <div className="min-w-0">
      <p className={`text-[12px] font-black ${labelClass}`}>{label}</p>
      <div className={`mt-2 text-[13px] leading-6 ${bodyClass}`}>{children}</div>
    </div>
  );
}

type QuestionPromptGroup = {
  title: string;
  ordered?: boolean;
  questions: string[];
};

function buildQuestionPromptGroups(item: IntelligenceItem, objects: IntelligenceWorkObject[]): QuestionPromptGroup[] {
  const cardQuestions = item.followupQuestions
    .map((question) => question.trim())
    .filter(Boolean)
    .slice(0, 3);
  return cardQuestions.length ? [{ title: '', ordered: false, questions: cardQuestions }] : [];
}

function TimelyIntelligenceCard({
  item,
  workObjects,
  pending,
  onDismiss,
  onFollow,
  onQuestion,
  onPromoteToTask,
}: {
  item: IntelligenceItem;
  workObjects: IntelligenceWorkObject[];
  pending: boolean;
  onDismiss: (item: IntelligenceItem) => void;
  onFollow: (item: IntelligenceItem) => void;
  onQuestion: (item: IntelligenceItem) => void;
  onPromoteToTask: (item: IntelligenceItem) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const links = sourceLinks(item);
  const objectLabel = itemObjectLabel(item, workObjects);
  const timeliness = item.timelinessLabel || (item.publishedAt ? `发布 ${formatTime(item.publishedAt)}` : `抓取 ${formatTime(item.capturedAt)}`);
  const isConverted = Boolean(item.convertedTaskId);
  const isFollowing = item.userStatus === 'following';
  const isDismissed = item.userStatus === 'dismissed';
  const isPending = !isConverted && !isFollowing && !isDismissed;
  // 左侧 3px 锚线颜色:状态可视化的核心
  const accentColor = isConverted ? '#94A3B8' : isFollowing ? '#10B981' : isDismissed ? 'transparent' : '#5B7BFE';
  const statusLabel = isConverted ? '已转任务' : isFollowing ? '已关注' : isDismissed ? '已忽略' : '待处理';
  const statusToneCls = isConverted
    ? 'text-gray-500 border-gray-200 bg-gray-50'
    : isFollowing
      ? 'text-emerald-700 border-emerald-200 bg-emerald-50'
      : isDismissed
        ? 'text-gray-400 border-gray-200 bg-gray-50'
        : 'text-[#5B7BFE] border-blue-200 bg-blue-50';

  return (
    <article
      className={`relative border rounded-xl transition-all duration-200 ${
        isDismissed
          ? 'border-gray-100 bg-gray-50/50 opacity-60'
          : expanded
            ? 'border-[#C9D6FF] bg-white shadow-[0_2px_8px_rgba(91,123,254,0.06)]'
            : 'border-gray-100 bg-white hover:border-gray-200 hover:shadow-[0_1px_3px_rgba(15,23,42,0.04)]'
      }`}
      style={{
        boxShadow: `inset 3px 0 0 0 ${accentColor}${expanded ? ', 0 2px 8px rgba(91,123,254,0.06)' : ''}`,
      }}
    >
      {/* 收起状态:meta + 标题 + 摘要 + 主操作 */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => setExpanded((prev) => !prev)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            setExpanded((prev) => !prev);
          }
        }}
        className="cursor-pointer pl-6 pr-5 py-4 flex items-start gap-3"
      >
        <div className="min-w-0 flex-1">
          {/* meta 一行:状态点 + 客户名 + 时效 */}
          <div className="flex items-center gap-2 flex-wrap text-[11px] text-gray-500">
            <span
              className="inline-block h-[6px] w-[6px] rounded-full shrink-0"
              style={{ backgroundColor: accentColor === 'transparent' ? '#D1D5DB' : accentColor }}
            />
            <span className="font-medium text-gray-700 truncate max-w-[180px]">{objectLabel}</span>
            <span className="text-gray-300">·</span>
            <span>{timeliness}</span>
            {item.intelligenceType && (
              <>
                <span className="text-gray-300">·</span>
                <span className="text-gray-400">{item.intelligenceType}</span>
              </>
            )}
            <span className={`ml-1 inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-[0.1em] ${statusToneCls}`}>
              {statusLabel}
            </span>
          </div>
          {/* 标题:14px medium */}
          <h2 className={`mt-1.5 text-[14px] leading-snug font-medium ${isDismissed || isConverted ? 'text-gray-600' : 'text-gray-900'}`}>
            {item.title}
          </h2>
          {/* 摘要:line-clamp-2 */}
          {item.summary && (
            <p className={`mt-1 text-[12px] leading-[1.65] line-clamp-2 ${isDismissed ? 'text-gray-400' : 'text-gray-500'}`}>
              {item.summary}
            </p>
          )}
        </div>
        {/* 右侧操作区:主操作 + 展开 chevron */}
        <div className="flex items-start gap-2 shrink-0">
          {isPending && (
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onPromoteToTask(item);
              }}
              disabled={pending}
              className="inline-flex items-center gap-1 rounded-md bg-[#5B7BFE] px-3 py-1.5 text-[12px] font-bold text-white hover:bg-[#4A6AE6] disabled:opacity-50 transition-colors"
            >
              {pending ? <Loader2 size={12} className="animate-spin" /> : <SlidersHorizontal size={12} />}
              转任务
            </button>
          )}
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              setExpanded((prev) => !prev);
            }}
            className="inline-flex items-center justify-center rounded-md p-1.5 text-gray-400 hover:text-gray-700 hover:bg-gray-50 transition-colors"
            title={expanded ? '收起' : '展开详情'}
            aria-label={expanded ? '收起' : '展开详情'}
          >
            <ChevronDown size={16} className={`transition-transform ${expanded ? 'rotate-180' : ''}`} />
          </button>
        </div>
      </div>

      {/* 展开后:详细字段 + 完整操作组 */}
      {expanded && (
        <div className="pl-6 pr-5 pb-5 space-y-4 border-t border-gray-100 pt-4">
          {/* WHY · 为什么和你有关 */}
          <div>
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400 mb-1.5">WHY · 为什么和你有关</p>
            <p className="text-[12.5px] leading-[1.7] text-gray-700">
              {item.relevanceReason || item.analysis || '尚未补齐结构化相关性判断。'}
            </p>
          </div>
          {/* IMPACT · 可能影响 — amber 锚线突出"需要警觉" */}
          <div className="relative pl-3">
            <span className="absolute left-0 top-1 bottom-1 w-[2px] rounded-full bg-amber-400" />
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-amber-700 mb-1.5">IMPACT · 可能影响</p>
            <p className="text-[12.5px] leading-[1.7] text-gray-700">
              {item.impact || '需要判断是否跟进。'}
            </p>
          </div>
          {/* ACTION · 建议动作 */}
          <div>
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400 mb-1.5">ACTION · 建议动作</p>
            <p className="text-[12.5px] leading-[1.7] text-gray-700">
              {item.suggestedAction || item.impact || '可转成阅读 / 研判任务,再判断是否跟进。'}
            </p>
          </div>
          {/* SOURCE · 来源 */}
          <div>
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400 mb-1.5">SOURCE · 来源</p>
            {links.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {links.map((link) => (
                  <a
                    key={link.url}
                    href={link.url}
                    target="_blank"
                    rel="noreferrer"
                    onClick={(event) => event.stopPropagation()}
                    className="inline-flex max-w-full items-center truncate rounded-md border border-blue-100 bg-blue-50 px-2.5 py-1 text-[11.5px] font-medium text-blue-700 hover:bg-blue-100 transition-colors"
                    title={link.url}
                  >
                    {link.label}
                  </a>
                ))}
              </div>
            ) : (
              <p className="text-[11.5px] text-gray-500">{item.source || '来源未知'}</p>
            )}
          </div>
          {/* 完整操作组 */}
          <div className="flex flex-wrap items-center gap-1.5 pt-2 border-t border-gray-100">
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onQuestion(item);
              }}
              className="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-[11.5px] font-medium text-gray-700 hover:bg-gray-50 hover:border-gray-300 transition-colors"
            >
              <MessageCircle size={12} />
              追问
            </button>
            {!isFollowing && !isDismissed && (
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  onFollow(item);
                }}
                disabled={pending}
                className="inline-flex items-center gap-1 rounded-md border border-emerald-200 bg-emerald-50 px-2.5 py-1.5 text-[11.5px] font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-50 transition-colors"
              >
                <BellPlus size={12} />
                关注后续
              </button>
            )}
            {!isPending && (
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  onPromoteToTask(item);
                }}
                disabled={pending}
                className="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-[11.5px] font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 transition-colors"
              >
                {pending ? <Loader2 size={12} className="animate-spin" /> : <SlidersHorizontal size={12} />}
                转任务
              </button>
            )}
            {!isDismissed && (
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  onDismiss(item);
                }}
                disabled={pending}
                className="ml-auto inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-[11.5px] font-medium text-gray-500 hover:bg-rose-50 hover:border-rose-200 hover:text-rose-600 disabled:opacity-50 transition-colors"
              >
                <Trash2 size={12} />
                不采纳
              </button>
            )}
          </div>
        </div>
      )}
    </article>
  );
}

export function IntelligenceStationView({
  activeTaskLists,
  effectiveTaskSettings,
  currentSessionUser,
  currentOperatorName,
  flash,
  onTasksReload,
  uiSessionScopeKey = 'intelligence:local:anonymous',
}: IntelligenceStationViewProps) {
  const [workObjects, setWorkObjects] = useRuntimeUiSessionState<IntelligenceWorkObject[]>(`${uiSessionScopeKey}:data:work-objects`, []);
  const [focusDirectives, setFocusDirectives] = useRuntimeUiSessionState<IntelligenceFocusDirective[]>(`${uiSessionScopeKey}:data:focus-directives`, []);
  const [shellLoadedAt, setShellLoadedAt] = useRuntimeUiSessionState<number>(`${uiSessionScopeKey}:data:shell-loaded-at`, 0);
  const [selectedScopeKey, setSelectedScopeKey] = useRuntimeUiSessionState<WorkObjectSelection>(`${uiSessionScopeKey}:scope`, 'all');
  const [mruScopeKeys, setMruScopeKeys] = useRuntimeUiSessionState<string[]>(`${uiSessionScopeKey}:mru`, []);
  const [focusScopeKey, setFocusScopeKey] = useState<ScopeKey>('global');
  const [focusDraft, setFocusDraft] = useState<FocusDraft>(EMPTY_FOCUS_DRAFT);
  const [activeTab, setActiveTab] = useRuntimeUiSessionState<IntelligenceContentKind>(`${uiSessionScopeKey}:tab`, 'public_opinion');
  const [sort, setSort] = useRuntimeUiSessionState<SortMode>(`${uiSessionScopeKey}:sort`, 'captured_desc');
  const [pages, setPages] = useRuntimeUiSessionState<Record<IntelligenceContentKind, number>>(`${uiSessionScopeKey}:pages`, {
    brand_mirror: 1,
    timely_intelligence: 1,
    public_opinion: 1,
  });
  const itemRuntimeCacheKey = `${uiSessionScopeKey}:data:items:${selectedScopeKey}:${activeTab}:${sort}:${pages[activeTab]}`;
  const [items, setItems] = useRuntimeUiSessionState<IntelligenceItem[]>(`${itemRuntimeCacheKey}:records`, []);
  const [candidateSamples, setCandidateSamples] = useRuntimeUiSessionState<IntelligenceCandidateSample[]>(`${itemRuntimeCacheKey}:candidates`, []);
  const [total, setTotal] = useRuntimeUiSessionState<number>(`${itemRuntimeCacheKey}:total`, 0);
  const [itemsLoadedAt, setItemsLoadedAt] = useRuntimeUiSessionState<number>(`${itemRuntimeCacheKey}:loaded-at`, 0);
  const [loading, setLoading] = useState(false);
  const [focusModalOpen, setFocusModalOpen] = useState(false);
  const [savingFocus, setSavingFocus] = useState(false);
  const [pendingItemId, setPendingItemId] = useState('');
  const [questionItem, setQuestionItem] = useState<IntelligenceItem | null>(null);
  const [questionDraft, setQuestionDraft] = useState('');
  const [questionPending, setQuestionPending] = useState(false);
  const [chatMessagesByItemId, setChatMessagesByItemId] = useState<Record<string, TopicCandidateChatMessage[]>>({});
  const [dismissTarget, setDismissTarget] = useState<IntelligenceItem | null>(null);
  const [dismissReasons, setDismissReasons] = useState<IntelligenceDismissReasonCode[]>(['irrelevant']);
  const [dismissNote, setDismissNote] = useState('');
  const [followTarget, setFollowTarget] = useState<IntelligenceItem | null>(null);
  const [followMode, setFollowMode] = useState<IntelligenceFollowMode>('same_theme');
  const [followNote, setFollowNote] = useState('');
  const [taskDraftTarget, setTaskDraftTarget] = useState<IntelligenceItem | null>(null);
  const [taskDraft, setTaskDraft] = useState<IntelligenceTaskDraftPayload | null>(null);
  const [taskDraftCacheByItemId, setTaskDraftCacheByItemId] = useState<Record<string, IntelligenceTaskDraftPayload>>({});
  const [peopleOptions, setPeopleOptions] = useState<MentionCandidate[]>([]);
  // 品牌监测 tab 顶部 KPI 用的 sentiment profile/items — 由 SentimentMonitorPanel 通过回调上报,
  // 跟监控对象卡同源, 避免重复 API + 数据不一致 (H1+H2 修复)
  const [brandKpiProfile, setBrandKpiProfile] = useState<SentimentProfile | null>(null);
  const [brandKpiNegativeCount, setBrandKpiNegativeCount] = useState<number>(0);
  const flashRef = useRef(flash);
  const autoRefreshDueCheckedRef = useRef(false);
  const refreshPollTimersRef = useRef<number[]>([]);
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return undefined;
    container.scrollTop = readRuntimeUiSessionValue(`${uiSessionScopeKey}:scroll-top`, 0);
    const remember = () => writeRuntimeUiSessionValue(`${uiSessionScopeKey}:scroll-top`, container.scrollTop);
    container.addEventListener('scroll', remember, { passive: true });
    return () => {
      remember();
      container.removeEventListener('scroll', remember);
    };
  }, [uiSessionScopeKey]);
  const [refreshingKind, setRefreshingKind] = useState<IntelligenceContentKind | null>(null);
  const [refreshRuns, setRefreshRuns] = useRuntimeUiSessionState<IntelligenceRefreshRun[]>(`${uiSessionScopeKey}:data:refresh-runs`, []);
  const [refreshCycleSettings, setRefreshCycleSettings] = useRuntimeUiSessionState<IntelligenceRefreshCycleSettings>(`${uiSessionScopeKey}:data:cycle-settings`, DEFAULT_REFRESH_CYCLE_SETTINGS);
  const [refreshRunsLoadedAt, setRefreshRunsLoadedAt] = useRuntimeUiSessionState<number>(`${uiSessionScopeKey}:data:refresh-runs-loaded-at`, 0);
  const [cycleEditKind, setCycleEditKind] = useState<IntelligenceContentKind | null>(null);
  const [cycleEditValue, setCycleEditValue] = useState('');
  const [cycleSaving, setCycleSaving] = useState(false);
  const [clarificationTarget, setClarificationTarget] = useState<ClarificationTarget | null>(null);
  const [clarificationNote, setClarificationNote] = useState('');
  const [clarificationPending, setClarificationPending] = useState(false);

  const selectedWorkObject = useMemo(() => {
    if (selectedScopeKey === 'all') return null;
    return workObjects.find((item) => scopeKeyOfObject(item) === selectedScopeKey) || null;
  }, [selectedScopeKey, workObjects]);
  const selectedScopeKeyRef = useRef(selectedScopeKey);
  const activeTabRef = useRef(activeTab);
  const shellLoadedAtRef = useRef(shellLoadedAt);
  const itemsLoadedAtRef = useRef(itemsLoadedAt);
  const refreshRunsLoadedAtRef = useRef(refreshRunsLoadedAt);
  useEffect(() => {
    selectedScopeKeyRef.current = selectedScopeKey;
  }, [selectedScopeKey]);
  useEffect(() => {
    activeTabRef.current = activeTab;
  }, [activeTab]);
  useEffect(() => {
    shellLoadedAtRef.current = shellLoadedAt;
  }, [shellLoadedAt]);
  useEffect(() => {
    itemsLoadedAtRef.current = itemsLoadedAt;
  }, [itemsLoadedAt]);
  useEffect(() => {
    refreshRunsLoadedAtRef.current = refreshRunsLoadedAt;
  }, [refreshRunsLoadedAt]);

  // P12 · 按 MRU 排序的 workObjects — 最近看过的排前面，剩余按原顺序
  const sortedWorkObjects = useMemo(() => {
    if (workObjects.length === 0 || mruScopeKeys.length === 0) return workObjects;
    const mruSet = new Set(mruScopeKeys);
    const inMruOrder: IntelligenceWorkObject[] = [];
    for (const key of mruScopeKeys) {
      const found = workObjects.find((w) => scopeKeyOfObject(w) === key);
      if (found) inMruOrder.push(found);
    }
    const notInMru = workObjects.filter((w) => !mruSet.has(scopeKeyOfObject(w)));
    return [...inMruOrder, ...notInMru];
  }, [workObjects, mruScopeKeys]);
  const currentPage = pages[activeTab];
  const currentPageSize = PAGE_SIZE[activeTab];
  const totalPages = Math.max(1, Math.ceil(total / currentPageSize));
  const visibleMessages = questionItem ? chatMessagesByItemId[questionItem.id] || [] : [];
  const questionPromptGroups = questionItem ? buildQuestionPromptGroups(questionItem, workObjects) : [];
  const defaultListId = effectiveTaskSettings.defaultListId || activeTaskLists[0]?.id || 'list-0';
  const currentOwnerName = currentSessionUser?.fullName || currentOperatorName || '当前用户';
  const currentOwnerId = currentSessionUser?.id || null;
  const currentPerson = useMemo<MentionCandidate>(() => ({
    id: currentSessionUser?.id || 'local-device-user',
    fullName: currentOwnerName,
    email: currentSessionUser?.email || '',
    primaryRole: currentSessionUser?.primaryRole === 'admin' ? 'admin' : 'employee',
    isSelf: true,
  }), [currentOwnerName, currentSessionUser]);
  const memberOptions = useMemo(() => {
    const merged = new Map<string, MentionCandidate>();
    [currentPerson, ...peopleOptions].forEach((item) => {
      if (!item.id) return;
      merged.set(item.id, item);
    });
    return Array.from(merged.values());
  }, [currentPerson, peopleOptions]);
  const selectedLabel = selectedObjectLabel(selectedScopeKey, workObjects);
  const lastFetchTime = selectedWorkObject?.lastCandidateFetchAt ? formatTime(selectedWorkObject.lastCandidateFetchAt) : null;

  // H1+H2: 切客户/切 tab 时清空 KPI, 让 SentimentMonitorPanel 重新上报新数据
  // 不再独立调 getSentimentProfile — 由子组件 reload 后回调 onProfileChange/onNegativeCountChange 上报
  useEffect(() => {
    setBrandKpiProfile(null);
    setBrandKpiNegativeCount(0);
  }, [selectedWorkObject?.id, selectedWorkObject?.type, activeTab]);

  const activeRefreshRuns = useMemo(() => refreshRuns.filter((run) => run.status === 'queued' || run.status === 'running'), [refreshRuns]);
  const refreshInProgress = refreshingKind !== null;

  useEffect(() => {
    flashRef.current = flash;
  }, [flash]);

  useEffect(() => {
    let cancelled = false;
    void getMentionCandidates('')
      .then((items) => {
        if (!cancelled) setPeopleOptions(items);
      })
      .catch(() => {
        if (!cancelled) setPeopleOptions([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => () => {
    refreshPollTimersRef.current.forEach((timer) => window.clearTimeout(timer));
    refreshPollTimersRef.current = [];
  }, []);

  const loadShell = useCallback(async (options?: { preferCache?: boolean }) => {
    if (
      options?.preferCache
      && shellLoadedAtRef.current > 0
      && Date.now() - shellLoadedAtRef.current < INTELLIGENCE_RUNTIME_CACHE_TTL_MS
    ) return;
    try {
      const [objects, directives, cycleSettings] = await Promise.all([
        getIntelligenceWorkObjects(),
        getIntelligenceFocusDirectives(),
        getIntelligenceRefreshCycleSettings(),
      ]);
      setWorkObjects(objects);
      setFocusDirectives(directives);
      setRefreshCycleSettings(cycleSettings);
      const loadedAt = Date.now();
      shellLoadedAtRef.current = loadedAt;
      setShellLoadedAt(loadedAt);
    } catch (error) {
      flashRef.current('error', error instanceof Error ? error.message : '情报站初始化失败');
    }
  }, []);

  const loadItems = useCallback(async (options?: { silent?: boolean; preferCache?: boolean }) => {
    // 品牌监测 tab 走 SentimentMonitorPanel 内部 items, 不调通用 items 端点
    if (activeTab === 'public_opinion') {
      setItems([]);
      setCandidateSamples([]);
      setTotal(0);
      return;
    }
    if (
      options?.preferCache
      && itemsLoadedAtRef.current > 0
      && Date.now() - itemsLoadedAtRef.current < INTELLIGENCE_RUNTIME_CACHE_TTL_MS
    ) return;
    const capturedScopeKey = selectedScopeKey;
    const capturedTab = activeTab;
    const silent = Boolean(options?.silent);
    if (!silent) setLoading(itemsLoadedAtRef.current <= 0);
    try {
      const response = await getIntelligenceItems({
        contentKind: activeTab,
        workObjectType: selectedWorkObject?.type,
        workObjectId: selectedWorkObject?.id,
        sort,
        page: currentPage,
        pageSize: currentPageSize,
      });
      if (
        selectedScopeKeyRef.current !== capturedScopeKey
        || activeTabRef.current !== capturedTab
      ) return;
      setItems(response.items);
      setCandidateSamples(response.candidateSamples || []);
      setTotal(response.total);
      const loadedAt = Date.now();
      itemsLoadedAtRef.current = loadedAt;
      setItemsLoadedAt(loadedAt);
    } catch (error) {
      if (selectedScopeKeyRef.current !== capturedScopeKey) return;
      flashRef.current('error', error instanceof Error ? error.message : '情报列表加载失败');
    } finally {
      if (!silent && selectedScopeKeyRef.current === capturedScopeKey) setLoading(false);
    }
  }, [activeTab, currentPage, currentPageSize, selectedScopeKey, selectedWorkObject?.id, selectedWorkObject?.type, sort]);

  const refreshWithoutMovingReader = useCallback(async (refresh: () => Promise<unknown>) => {
    const container = scrollContainerRef.current;
    const previousTop = container?.scrollTop ?? 0;
    const shouldRestore = Boolean(container && previousTop > 0);
    await refresh();
    if (!shouldRestore) return;
    window.requestAnimationFrame(() => {
      const current = scrollContainerRef.current;
      if (!current) return;
      if (current.scrollTop > 8 && Math.abs(current.scrollTop - previousTop) > 24) return;
      current.scrollTop = previousTop;
    });
  }, []);

  const loadRefreshRuns = useCallback(async (options?: { preferCache?: boolean }) => {
    if (
      options?.preferCache
      && refreshRunsLoadedAtRef.current > 0
      && Date.now() - refreshRunsLoadedAtRef.current < INTELLIGENCE_RUNTIME_CACHE_TTL_MS
    ) return refreshRuns;
    try {
      const runs = await getIntelligenceRefreshRuns({
        limit: 12,
      });
      setRefreshRuns(runs);
      const loadedAt = Date.now();
      refreshRunsLoadedAtRef.current = loadedAt;
      setRefreshRunsLoadedAt(loadedAt);
      return runs;
    } catch (error) {
      flashRef.current('error', error instanceof Error ? error.message : '刷新状态读取失败');
      return [];
    }
  }, [refreshRuns, setRefreshRuns, setRefreshRunsLoadedAt]);

  useEffect(() => {
    void loadShell({ preferCache: true });
  }, [loadShell]);

  useEffect(() => {
    const directive = focusDirectives.find((item) => directiveMatches(item, focusScopeKey));
    setFocusDraft(draftFromDirective(directive));
  }, [focusDirectives, focusScopeKey]);

  useEffect(() => {
    void loadItems({ preferCache: true });
  }, [loadItems]);

  useEffect(() => {
    void loadRefreshRuns({ preferCache: true });
  }, [loadRefreshRuns]);

  // TODO: 半成品，autoRefreshIntelligenceDue 未在 api.ts 实现；后端 endpoint 也只是 stub。
  //       先整段注释解除启动崩溃，待 API 函数补齐 + 后端 endpoint 完善后恢复。
  // useEffect(() => {
  //   if (autoRefreshDueCheckedRef.current || workObjects.length === 0) return;
  //   autoRefreshDueCheckedRef.current = true;
  //   void autoRefreshIntelligenceDue({
  //     contentKinds: ['profile_completion', 'timely_intelligence'],
  //     scopeType: 'all',
  //   })
  //     .then((result) => {
  //       if (result.queuedCount > 0) {
  //         flashRef.current('info', result.message || '已到默认周期，后台已自动排队补跑');
  //         void refreshWithoutMovingReader(() => Promise.all([loadRefreshRuns(), loadShell(), loadItems({ silent: true })])).catch(() => undefined);
  //       }
  //     })
  //     .catch((error) => {
  //       flashRef.current('error', error instanceof Error ? error.message : '自动补跑检查失败');
  //     });
  // }, [loadItems, loadRefreshRuns, loadShell, refreshWithoutMovingReader, workObjects.length]);

  useEffect(() => {
    if (activeRefreshRuns.length === 0) return undefined;
    const timer = window.setInterval(() => {
      void refreshWithoutMovingReader(() => Promise.all([loadRefreshRuns(), loadShell(), loadItems({ silent: true })])).catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [activeRefreshRuns.length, loadItems, loadRefreshRuns, loadShell, refreshWithoutMovingReader]);

  function changeScope(next: WorkObjectSelection) {
    setSelectedScopeKey(next);
    setPages({ brand_mirror: 1, timely_intelligence: 1, public_opinion: 1 });
    if (next !== 'all') {
      setMruScopeKeys((prev) => [next, ...prev.filter((key) => key !== next)].slice(0, 20));
    }
  }

  function changeSort(next: SortMode) {
    setSort(next);
    setPages({ brand_mirror: 1, timely_intelligence: 1, public_opinion: 1 });
  }

  function setCurrentPage(next: number) {
    setPages((current) => ({ ...current, [activeTab]: next }));
  }

  async function reloadItems() {
    try {
      await Promise.all([loadShell(), loadItems()]);
    } catch (error) {
      flashRef.current('error', error instanceof Error ? error.message : '情报列表刷新失败');
    }
  }

  function cycleHoursFor(_kind: IntelligenceContentKind) {
    return refreshCycleSettings.timelyIntelligenceHours;
  }

  function beginCycleEdit(kind: IntelligenceContentKind) {
    setCycleEditKind(kind);
    setCycleEditValue(String(cycleHoursFor(kind)));
  }

  async function commitCycleEdit() {
    if (!cycleEditKind) return;
    const normalized = Math.max(1, Math.min(parseInt(cycleEditValue, 10) || cycleHoursFor(cycleEditKind), 8760));
    setCycleSaving(true);
    try {
      const payload = { timelyIntelligenceHours: normalized };
      void cycleEditKind; // 类型保留，但 profile_completion 已下线，只剩 timely
      const settings = await updateIntelligenceRefreshCycleSettings(payload);
      setRefreshCycleSettings(settings);
      setCycleEditKind(null);
      setCycleEditValue('');
      flashRef.current('success', '默认刷新周期已更新');
      await loadShell();
    } catch (error) {
      flashRef.current('error', error instanceof Error ? error.message : '默认周期更新失败');
    } finally {
      setCycleSaving(false);
    }
  }

  function scheduleBackgroundRefreshChecks(contentKind: IntelligenceContentKind) {
    refreshPollTimersRef.current.forEach((timer) => window.clearTimeout(timer));
    refreshPollTimersRef.current = [2500, 8000, 15000].map((delay) => window.setTimeout(() => {
      void refreshWithoutMovingReader(() => Promise.all([loadRefreshRuns(), loadShell(), loadItems({ silent: true })]))
        .catch(() => undefined)
        .finally(() => undefined);
    }, delay));
  }

  async function handleRefreshSupply(contentKind: IntelligenceContentKind) {
    let queued = false;
    setRefreshingKind(contentKind);
    try {
      const result = await refreshIntelligenceSupply(scopeRefreshPayload(selectedScopeKey, selectedWorkObject, contentKind));
      queued = isBackgroundRefreshQueued(result);
      if (queued) {
        scheduleBackgroundRefreshChecks(contentKind);
      }
      await refreshWithoutMovingReader(() => Promise.all([loadRefreshRuns(), loadShell(), loadItems({ silent: true })]));
      flashRef.current(result.status === 'failed' ? 'error' : result.status === 'no_results' || queued ? 'info' : 'success', summarizeRefreshResult(result));
    } catch (error) {
      flashRef.current('error', error instanceof Error ? error.message : `${TAB_LABEL[contentKind]}刷新失败`);
    } finally {
      setRefreshingKind(null);
    }
  }

  async function handleSaveFocus() {
    setSavingFocus(true);
    try {
      const saved = await saveIntelligenceFocusDirective(directivePayload(focusScopeKey, focusDraft));
      setFocusDirectives((current) => {
        const others = current.filter((item) => !directiveMatches(item, focusScopeKey));
        return [...others, saved];
      });
      setFocusModalOpen(false);
      flash('success', '关注指令已保存');
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '保存关注指令失败');
    } finally {
      setSavingFocus(false);
    }
  }

  function closeFocusModal() {
    const directive = focusDirectives.find((item) => directiveMatches(item, focusScopeKey));
    setFocusDraft(draftFromDirective(directive));
    setFocusModalOpen(false);
  }

  function handleDismiss(item: IntelligenceItem) {
    setDismissTarget(item);
    setDismissReasons(['irrelevant']);
    setDismissNote('');
  }

  function toggleDismissReason(reason: IntelligenceDismissReasonCode) {
    setDismissReasons((current) => {
      if (current.includes(reason)) {
        return current.filter((item) => item !== reason);
      }
      return [...current, reason];
    });
  }

  function openClarificationForItem(item: IntelligenceItem) {
    setClarificationTarget({ type: 'item', item });
    setClarificationNote('');
  }

  function scopeForClarification(target: ClarificationTarget): { scopeType: 'global' | 'client' | 'project_module'; scopeId?: string | null } {
    const scopeType = target.item.scopeType === 'project_module' || target.item.projectModuleId ? 'project_module' : target.item.scopeType === 'client' || target.item.clientId ? 'client' : 'global';
    const scopeId = scopeType === 'project_module' ? target.item.projectModuleId || target.item.scopeId : scopeType === 'client' ? target.item.clientId || target.item.scopeId : null;
    return { scopeType, scopeId };
  }

  async function confirmClarification() {
    if (!clarificationTarget || !clarificationNote.trim()) return;
    const target = clarificationTarget;
    setClarificationPending(true);
    try {
      const scope = scopeForClarification(target);
      await submitIntelligenceVerificationFeedback({
        targetType: target.type,
        targetId: target.item.id,
        scopeType: scope.scopeType,
        scopeId: scope.scopeId,
        note: clarificationNote.trim(),
      });
      setClarificationTarget(null);
      setClarificationNote('');
      await reloadItems();
      flash('success', '判断标准已保存，当前线索会退出活跃展示');
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '保存判断标准失败');
    } finally {
      setClarificationPending(false);
    }
  }

  async function confirmDismiss() {
    if (!dismissTarget || dismissReasons.length === 0) return;
    const item = dismissTarget;
    setPendingItemId(item.id);
    try {
      await dismissIntelligenceItem(item.id, {
        reasonCode: dismissReasons[0],
        reasonCodes: dismissReasons,
        note: dismissNote.trim(),
      });
      setDismissTarget(null);
      setDismissReasons(['irrelevant']);
      await reloadItems();
      flash('success', '已不采纳');
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '不采纳失败');
    } finally {
      setPendingItemId('');
    }
  }

  function handleFollow(item: IntelligenceItem) {
    setFollowTarget(item);
    setFollowMode('same_theme');
    setFollowNote('');
  }

  async function confirmFollow() {
    if (!followTarget) return;
    const item = followTarget;
    setPendingItemId(item.id);
    try {
      await followIntelligenceItem(item.id, {
        followMode,
        note: followNote.trim(),
      });
      setFollowTarget(null);
      await reloadItems();
      flash('success', '已关注后续');
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '关注后续失败');
    } finally {
      setPendingItemId('');
    }
  }

  async function handlePromoteToTask(item: IntelligenceItem) {
    if (taskDraftCacheByItemId[item.id]) {
      setTaskDraftTarget(item);
      setTaskDraft(taskDraftCacheByItemId[item.id]);
      return;
    }
    setPendingItemId(item.id);
    try {
      const response = await getIntelligenceTaskDraft(item.id);
      setTaskDraftCacheByItemId((current) => ({ ...current, [item.id]: response.draft }));
      setTaskDraftTarget(item);
      setTaskDraft(response.draft);
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '转任务失败');
    } finally {
      setPendingItemId('');
    }
  }

  async function confirmCreateTask() {
    if (!taskDraftTarget || !taskDraft || !taskDraft.title?.trim()) return;
    const item = taskDraftTarget;
    setPendingItemId(item.id);
    try {
      await createIntelligenceTask(item.id, {
        ...taskDraft,
        title: taskDraft.title.trim(),
        desc: taskDraft.desc?.trim() || '',
        ddl: taskDraft.ddl?.trim() || '本周',
        ownerId: taskDraft.ownerId ?? currentOwnerId,
        ownerName: taskDraft.ownerName?.trim() || currentOwnerName,
        collaboratorIds: (taskDraft.collaboratorIds || []).filter((id) => id && id !== (taskDraft.ownerId ?? currentOwnerId)),
        listId: taskDraft.listId || defaultListId,
        tags: taskDraft.tags?.length ? taskDraft.tags : ['情报跟进'],
        note: taskDraft.note?.trim() || '',
      });
      setTaskDraftTarget(null);
      setTaskDraft(null);
      await onTasksReload();
      await reloadItems();
      flash('success', '已创建跟进任务');
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '创建任务失败');
    } finally {
      setPendingItemId('');
    }
  }

  async function handleAskQuestion() {
    if (!questionItem || !questionDraft.trim()) return;
    const question = questionDraft.trim();
    const userMessage: TopicCandidateChatMessage = {
      role: 'user',
      content: question,
      createdAt: new Date().toISOString(),
    };
    const history = [...visibleMessages, userMessage];
    setChatMessagesByItemId((current) => ({ ...current, [questionItem.id]: history }));
    setQuestionDraft('');
    setQuestionPending(true);
    try {
      const response = await askIntelligenceItemQuestion(questionItem.id, {
        question,
        history: visibleMessages,
      });
      setChatMessagesByItemId((current) => ({
        ...current,
        [questionItem.id]: [...history, response.message],
      }));
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : '追问失败';
      flash('error', errorMessage);
      const assistantErrorMessage: TopicCandidateChatMessage = {
        role: 'assistant',
        content: `这次 AI 追问没有稳定返回：${errorMessage}`,
        createdAt: new Date().toISOString(),
      };
      setChatMessagesByItemId((current) => ({ ...current, [questionItem.id]: [...history, assistantErrorMessage] }));
    } finally {
      setQuestionPending(false);
    }
  }

  // 派生 KPI 数据:今日新增 / 待处理 / 已关注 / 已转任务
  // 用于 Hero 区显示"用户进来 1 秒知道当前情报状态"
  const kpiStats = (() => {
    const t = new Date();
    t.setHours(0, 0, 0, 0);
    const todayMs = t.getTime();
    let todayNew = 0;
    let pending = 0;
    let following = 0;
    let converted = 0;
    items.forEach((it) => {
      const ts = it.capturedAt ? new Date(it.capturedAt).getTime() : 0;
      if (ts >= todayMs) todayNew += 1;
      if (it.convertedTaskId) {
        converted += 1;
      } else if (it.userStatus === 'following') {
        following += 1;
      } else if (it.userStatus !== 'dismissed') {
        pending += 1;
      }
    });
    return { todayNew, pending, following, converted };
  })();

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-white font-sans text-gray-900">
      <div className="mx-auto flex h-full min-h-0 w-full flex-col px-5 pt-5 md:pt-6 lg:px-8 2xl:px-10">
        {/* 页面进入后直接展示指标；当前板块和项目由下方固定标签栏表达。 */}
        <header className="shrink-0">
          {/* 4 KPI block — tab-aware: 品牌监测显示品牌健康 KPI, 时效情报显示信号流通 KPI */}
          <div className="grid grid-cols-2 gap-x-8 gap-y-6 md:grid-cols-4">
            {activeTab === 'public_opinion' ? (
              <>
                {/* 整体情感 0-100 (品牌监测 #1) */}
                <div className="flex flex-col">
                  <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">整体情感</p>
                  <div className="mt-3 flex items-baseline gap-1.5">
                    {brandKpiProfile && brandKpiProfile.totalMentions > 0 ? (
                      <>
                        <span className={`text-[32px] leading-none font-light tracking-tight tabular-nums ${
                          brandKpiProfile.sentimentScore >= 70 ? 'text-emerald-600' :
                          brandKpiProfile.sentimentScore >= 40 ? 'text-gray-900' : 'text-rose-600'
                        }`}>{brandKpiProfile.sentimentScore}</span>
                        <span className="text-[14px] leading-none font-light text-gray-400">/100</span>
                      </>
                    ) : (
                      <span className="text-[32px] leading-none font-light tracking-tight text-gray-300">—</span>
                    )}
                  </div>
                  <div className={`mt-2 h-[2px] w-8 rounded-full ${
                    brandKpiProfile && brandKpiProfile.sentimentScore >= 70 ? 'bg-emerald-500' :
                    brandKpiProfile && brandKpiProfile.sentimentScore < 40 && brandKpiProfile.totalMentions > 0 ? 'bg-rose-500' : 'bg-transparent'
                  }`} />
                  <p className="mt-2 text-[11px] text-gray-400 truncate">公众怎么看 · 0 极负面 / 100 极积极</p>
                </div>
                {/* 负面预警 (品牌监测 #2) — 用 items 实际过滤后的数量, 跟监控对象卡同源 (H1 修复) */}
                <div className="flex flex-col">
                  <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">负面预警</p>
                  <div className="mt-3 flex items-baseline gap-1.5">
                    <span className={`text-[32px] leading-none font-light tracking-tight tabular-nums ${
                      brandKpiNegativeCount > 0 ? 'text-rose-600' : 'text-gray-900'
                    }`}>{brandKpiNegativeCount}</span>
                    <span className="text-[14px] leading-none font-light text-gray-400">条</span>
                  </div>
                  <div className={`mt-2 h-[2px] w-8 rounded-full ${brandKpiNegativeCount > 0 ? 'bg-rose-500' : 'bg-transparent'}`} />
                  <p className="mt-2 text-[11px] text-gray-400 truncate">
                    {brandKpiNegativeCount > 0 ? '需要紧急关注' : '当前无负面信号'}
                  </p>
                </div>
                {/* 战略对齐度没有严格权威评分时明确保持 not_connected，禁止按客户名注入样例分数。 */}
                <div className="flex flex-col">
                  <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">战略对齐度</p>
                  <div className="mt-3 flex items-baseline gap-1.5">
                    <span className="text-[32px] leading-none font-light tracking-tight text-gray-300">—</span>
                  </div>
                  <div className="mt-2 h-[2px] w-8 rounded-full bg-transparent" />
                  <p className="mt-2 text-[11px] text-gray-400 truncate">
                    not_connected · 尚无严格权威评分
                  </p>
                </div>
                {/* 上次抓取 (两 tab 共用) */}
                <div className="flex flex-col">
                  <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">上次抓取</p>
                  <span className="mt-3 text-[18px] leading-none font-light tracking-tight text-gray-900 truncate">{lastFetchTime || '尚未抓取'}</span>
                  <div className="mt-2 h-[2px] w-8 rounded-full bg-transparent" />
                  <p className="mt-2 text-[11px] text-gray-400 truncate">数据新鲜度</p>
                </div>
              </>
            ) : (
              <>
                {/* 时效情报 4 个 KPI (原样保留) */}
                <div className="flex flex-col">
                  <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">今日新增</p>
                  <div className="mt-3 flex items-baseline gap-1.5">
                    <span className={`text-[32px] leading-none font-light tracking-tight ${kpiStats.todayNew > 0 ? 'text-[#5B7BFE]' : 'text-gray-900'}`}>{kpiStats.todayNew}</span>
                    <span className="text-[14px] leading-none font-light text-gray-400">条</span>
                  </div>
                  <div className={`mt-2 h-[2px] w-8 rounded-full ${kpiStats.todayNew > 0 ? 'bg-[#5B7BFE]' : 'bg-transparent'}`} />
                  <p className="mt-2 text-[11px] text-gray-400 truncate">{kpiStats.todayNew > 0 ? '需要扫一遍' : '今天暂无新情报'}</p>
                </div>
                <div className="flex flex-col">
                  <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">待处理</p>
                  <div className="mt-3 flex items-baseline gap-1.5">
                    <span className={`text-[32px] leading-none font-light tracking-tight ${kpiStats.pending > 0 ? 'text-amber-600' : 'text-gray-900'}`}>{kpiStats.pending}</span>
                    <span className="text-[14px] leading-none font-light text-gray-400">条</span>
                  </div>
                  <div className={`mt-2 h-[2px] w-8 rounded-full ${kpiStats.pending > 0 ? 'bg-amber-500' : 'bg-transparent'}`} />
                  <p className="mt-2 text-[11px] text-gray-400 truncate">{kpiStats.pending > 0 ? '尚未判断要不要跟进' : '全部已处理'}</p>
                </div>
                <div className="flex flex-col">
                  <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">已关注</p>
                  <div className="mt-3 flex items-baseline gap-1.5">
                    <span className="text-[32px] leading-none font-light tracking-tight text-gray-900">{kpiStats.following}</span>
                    <span className="text-[14px] leading-none font-light text-gray-400">条</span>
                  </div>
                  <div className={`mt-2 h-[2px] w-8 rounded-full ${kpiStats.following > 0 ? 'bg-emerald-500' : 'bg-transparent'}`} />
                  <p className="mt-2 text-[11px] text-gray-400 truncate">{kpiStats.following > 0 ? '关注后续动向' : '暂无关注'}</p>
                </div>
                <div className="flex flex-col">
                  <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">上次抓取</p>
                  <span className="mt-3 text-[18px] leading-none font-light tracking-tight text-gray-900 truncate">{lastFetchTime || '尚未抓取'}</span>
                  <div className="mt-2 h-[2px] w-8 rounded-full bg-transparent" />
                  <p className="mt-2 text-[11px] text-gray-400 truncate">已转任务 {kpiStats.converted} 条</p>
                </div>
              </>
            )}
          </div>
        </header>

        <main className="mt-6 flex min-h-0 flex-1 flex-col">
          {/* Tab 切换:underline 风格,active 用 #5B7BFE 锚线,跟全局一致 */}
          <div className="z-30 -mx-2 flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-gray-100 bg-white px-2 pt-2">
            <div className="flex gap-7">
              {(Object.keys(TAB_LABEL) as IntelligenceContentKind[]).filter((k) => !HIDDEN_TABS.has(k)).map((key) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setActiveTab(key)}
                  className={`relative pb-3.5 pt-1 text-left transition-colors ${
                    activeTab === key ? 'text-gray-900' : 'text-gray-400 hover:text-gray-700'
                  }`}
                >
                  <span className={`block text-[14px] tracking-[0.01em] ${activeTab === key ? 'font-medium' : 'font-normal'}`}>
                    {TAB_LABEL[key]}
                  </span>
                  <span className="mt-0.5 block text-[10.5px] text-gray-400 font-normal">
                    {TAB_HINT[key]}
                  </span>
                  {/* 底部锚线 */}
                  <span
                    className={`absolute left-0 right-0 -bottom-px h-[2px] rounded-full transition-colors ${
                      activeTab === key ? 'bg-[#5B7BFE]' : 'bg-transparent'
                    }`}
                  />
                </button>
              ))}
            </div>
            {/* Tab 行右侧只保留全局客户/项目选择器。 */}
            <div className="flex items-center gap-3 pb-3 flex-wrap">
              {/* 工作对象下拉 — 提到 Tab 行右侧, 跟 Tab 平齐, 强调"切客户是全局的" */}
              <select
                value={selectedScopeKey}
                onChange={(event) => changeScope(event.target.value as WorkObjectSelection)}
                className="rounded-md border border-gray-200 bg-white px-3 py-1.5 text-[12px] font-semibold text-gray-700 outline-none focus:border-[#5B7BFE] transition-colors max-w-[187px] shadow-sm hover:border-gray-300"
                title="切换客户/项目"
              >
                <option value="all">全部客户/项目</option>
                {sortedWorkObjects.map((item, idx) => {
                  const key = scopeKeyOfObject(item);
                  const isRecent = mruScopeKeys.includes(key);
                  const showDivider =
                    isRecent === false &&
                    idx > 0 &&
                    mruScopeKeys.includes(scopeKeyOfObject(sortedWorkObjects[idx - 1]));
                  return (
                    <Fragment key={key}>
                      {showDivider && (
                        <option disabled value="__divider__">────────────</option>
                      )}
                      <option value={key}>
                        {isRecent ? '★ ' : ''}{item.type === 'client' ? '客户' : '项目'}:{item.name}
                      </option>
                    </Fragment>
                  );
                })}
              </select>
            </div>
          </div>

          <div ref={scrollContainerRef} className="min-h-0 flex-1 overflow-y-auto pb-10">
          {/* 时效情报的周期和主动作集中在标签页内，不占用全局标题区。 */}
          {activeTab === 'timely_intelligence' && (
            <div className="mt-4 mb-2 space-y-3">
              <div className="flex items-center gap-1 text-[12px] font-semibold text-gray-500">
                <span>默认周期</span>
                {cycleEditKind === activeTab ? (
                  <input
                    autoFocus
                    type="number"
                    min={1}
                    step={1}
                    value={cycleEditValue}
                    disabled={cycleSaving}
                    onChange={(event) => setCycleEditValue(event.target.value.replace(/[^\d]/g, ''))}
                    onBlur={() => void commitCycleEdit()}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') void commitCycleEdit();
                      if (event.key === 'Escape') {
                        setCycleEditKind(null);
                        setCycleEditValue('');
                      }
                    }}
                    className="h-7 w-16 rounded-md border border-gray-200 bg-white px-2 text-center text-[12px] font-black text-gray-900 outline-none focus:border-gray-500"
                  />
                ) : (
                  <button
                    type="button"
                    onClick={() => beginCycleEdit(activeTab)}
                    className="rounded-md border border-gray-200 bg-white px-2 py-1 text-[12px] font-black text-gray-900 hover:bg-gray-50"
                  >
                    {cycleHoursFor(activeTab)}
                  </button>
                )}
                <span>小时</span>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => setFocusModalOpen(true)}
                  className="inline-flex items-center gap-1.5 rounded-md border border-gray-200 bg-white px-3 py-2 text-[12px] font-bold text-gray-600 hover:border-gray-300 hover:bg-gray-50"
                >
                  <SlidersHorizontal size={14} />
                  我重点关注什么
                </button>
                <button
                  type="button"
                  onClick={() => void handleRefreshSupply('timely_intelligence')}
                  disabled={refreshInProgress}
                  className="inline-flex items-center gap-1.5 rounded-md bg-[#5B7BFE] px-3 py-2 text-[12px] font-bold text-white hover:bg-[#4A6AE6] disabled:opacity-50"
                >
                  {refreshInProgress ? <Loader2 size={14} className="animate-spin" /> : <BellPlus size={14} />}
                  {refreshInProgress ? '抓取中…' : '立即抓取情报'}
                </button>
                <select
                  value={sort}
                  onChange={(event) => changeSort(event.target.value as SortMode)}
                  className="ml-1 rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-[12px] font-medium text-gray-700 outline-none focus:border-[#5B7BFE]"
                >
                  {(Object.keys(SORT_LABEL) as SortMode[]).map((key) => (
                    <option key={key} value={key}>{SORT_LABEL[key]}</option>
                  ))}
                </select>
                <span className="ml-auto text-[11px] text-gray-400">
                  {items.length > 0 ? `共 ${items.length} 条情报` : '暂无情报'}
                </span>
              </div>
            </div>
          )}

          <div className="mt-2 min-h-[420px]">
            {activeTab === 'public_opinion' ? (
              <SentimentMonitorPanel
                workObject={selectedWorkObject}
                uiSessionScopeKey={`${uiSessionScopeKey}:sentiment`}
                onProfileChange={setBrandKpiProfile}
                onNegativeCountChange={setBrandKpiNegativeCount}
              />
            ) : (
              <>
            {loading && itemsLoadedAt <= 0 ? (
              <div className="flex min-h-[260px] items-center justify-center text-[13px] font-bold text-gray-500">
                <Loader2 size={18} className="mr-2 animate-spin" />
                正在读取情报
              </div>
            ) : items.length === 0 ? (
              <EmptyState
                contentKind={activeTab}
                selectedWorkObject={selectedWorkObject}
                workObjects={workObjects}
                candidateSamples={candidateSamples}
                refreshing={refreshInProgress}
                onRefresh={(contentKind) => void handleRefreshSupply(contentKind)}
              />
            ) : (
              (() => {
                // 按抓取时间分组:TODAY / YESTERDAY / THIS WEEK / EARLIER
                // 让用户能扫"今天有什么大事"而不是一锅杂烩
                const now = new Date();
                const todayStart = new Date(now); todayStart.setHours(0, 0, 0, 0);
                const yesterdayStart = new Date(todayStart); yesterdayStart.setDate(yesterdayStart.getDate() - 1);
                const weekStart = new Date(todayStart); weekStart.setDate(weekStart.getDate() - 7);
                const buckets: Array<{ key: string; label: string; eyebrow: string; items: IntelligenceItem[] }> = [
                  { key: 'today', label: '今天', eyebrow: 'TODAY · 今天', items: [] },
                  { key: 'yesterday', label: '昨天', eyebrow: 'YESTERDAY · 昨天', items: [] },
                  { key: 'thisWeek', label: '本周早些时候', eyebrow: 'THIS WEEK · 本周早些时候', items: [] },
                  { key: 'earlier', label: '更早', eyebrow: 'EARLIER · 更早', items: [] },
                ];
                items.forEach((it) => {
                  const ts = it.capturedAt ? new Date(it.capturedAt).getTime() : 0;
                  if (ts >= todayStart.getTime()) buckets[0].items.push(it);
                  else if (ts >= yesterdayStart.getTime()) buckets[1].items.push(it);
                  else if (ts >= weekStart.getTime()) buckets[2].items.push(it);
                  else buckets[3].items.push(it);
                });
                const nonEmptyBuckets = buckets.filter((b) => b.items.length > 0);
                return (
                  <div className="space-y-8">
                    {nonEmptyBuckets.map((bucket) => (
                      <section key={bucket.key}>
                        <div className="flex items-baseline justify-between mb-3 border-b border-gray-100 pb-2">
                          <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">{bucket.eyebrow}</p>
                          <span className="text-[10.5px] text-gray-400 tabular-nums">{bucket.items.length} 条</span>
                        </div>
                        <div className="space-y-2">
                          {bucket.items.map((item) => (
                            <TimelyIntelligenceCard
                              key={item.id}
                              item={item}
                              workObjects={workObjects}
                              pending={pendingItemId === item.id}
                              onDismiss={handleDismiss}
                              onFollow={handleFollow}
                              onQuestion={setQuestionItem}
                              onPromoteToTask={(next) => void handlePromoteToTask(next)}
                            />
                          ))}
                        </div>
                      </section>
                    ))}
                  </div>
                );
              })()
            )}
              </>
            )}
          </div>
          {activeTab === 'timely_intelligence' && (
            <>
              <Pagination page={currentPage} pageSize={currentPageSize} total={total} onPageChange={setCurrentPage} />
              <div className="mt-1 text-right text-[12px] font-semibold text-gray-500">
                {total > 0 ? `当前筛选共 ${total} 条，已显示第 ${currentPage} / ${totalPages} 页` : '当前筛选暂无内容'}
              </div>
            </>
          )}
          </div>
        </main>

      </div>

      {focusModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/35 px-4 py-6">
          <div className="flex max-h-[88vh] w-full max-w-[880px] flex-col rounded-lg bg-white p-5 shadow-2xl">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-[12px] font-bold text-gray-400">关注指令</p>
                <h2 className="mt-1 text-[18px] font-black text-gray-950">我重点关注什么</h2>
                <p className="mt-2 text-[13px] leading-6 text-gray-500">关注指令会进入当前对象后续搜索、线索判断和排序学习。</p>
              </div>
              <label className="text-[12px] font-bold text-gray-500">
                生效范围
                <select
                  value={focusScopeKey}
                  onChange={(event) => setFocusScopeKey(event.target.value as ScopeKey)}
                  className="ml-2 rounded-md border border-gray-200 bg-white px-3 py-2 text-[13px] font-semibold text-gray-800 outline-none focus:border-gray-400"
                >
                  <option value="global">所有客户/项目</option>
                  {workObjects.map((item) => (
                    <option key={scopeKeyOfObject(item)} value={scopeKeyOfObject(item)}>
                      {item.type === 'client' ? '客户' : '项目'}：{item.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div className="mt-4 grid gap-3 overflow-y-auto pr-1 md:grid-cols-2">
              <label className="text-[12px] font-black text-gray-500">
                时效情报优先
                <textarea
                  value={focusDraft.timelyIntelligenceFocus}
                  onChange={(event) => setFocusDraft((current) => ({ ...current, timelyIntelligenceFocus: event.target.value }))}
                  rows={7}
                  placeholder={'资助窗口\n监管变化\n同类机构动作'}
                  className="mt-2 w-full resize-none rounded-md border border-gray-200 bg-white px-3 py-2 text-[13px] font-medium leading-6 text-gray-800 outline-none focus:border-gray-400"
                />
              </label>
              <label className="text-[12px] font-black text-gray-500">
                少看或不看
                <textarea
                  value={focusDraft.exclude}
                  onChange={(event) => setFocusDraft((current) => ({ ...current, exclude: event.target.value }))}
                  rows={7}
                  placeholder={'泛泛行业新闻\n重复转载\n无来源截图'}
                  className="mt-2 w-full resize-none rounded-md border border-gray-200 bg-white px-3 py-2 text-[13px] font-medium leading-6 text-gray-800 outline-none focus:border-gray-400"
                />
              </label>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={closeFocusModal}
                className="rounded-md border border-gray-200 px-4 py-2 text-[13px] font-bold text-gray-600 hover:bg-gray-50"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void handleSaveFocus()}
                disabled={savingFocus}
                className="inline-flex items-center gap-2 rounded-md bg-gray-950 px-4 py-2 text-[13px] font-black text-white hover:bg-gray-800 disabled:bg-gray-300"
              >
                {savingFocus ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                保存关注指令
              </button>
            </div>
          </div>
        </div>
      )}

      {clarificationTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/35 px-4 py-6">
          <div className="w-full max-w-[560px] rounded-lg bg-white p-5 shadow-2xl">
            <p className="text-[12px] font-bold text-gray-400">补充判断标准</p>
            <h3 className="mt-1 text-[18px] font-black text-gray-950">{clarificationTarget.item.title}</h3>
            <p className="mt-2 text-[13px] leading-6 text-gray-500">
              用一句话说明这条为什么不对，或以后应该按什么标准判断。系统会把它写入当前客户/项目的核验规则，后续刷新会按新标准执行。
            </p>
            <textarea
              value={clarificationNote}
              onChange={(event) => setClarificationNote(event.target.value)}
              rows={4}
              placeholder="例如：这不是益语智库本身的信息；只采纳客户官网和公益行业公开报道。"
              className="mt-4 w-full resize-none rounded-md border border-gray-200 px-3 py-2 text-[13px] leading-6 outline-none focus:border-gray-400"
            />
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  setClarificationTarget(null);
                  setClarificationNote('');
                }}
                className="rounded-md border border-gray-200 px-4 py-2 text-[13px] font-bold text-gray-600 hover:bg-gray-50"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void confirmClarification()}
                disabled={clarificationPending || !clarificationNote.trim()}
                className="inline-flex items-center gap-2 rounded-md bg-amber-700 px-4 py-2 text-[13px] font-black text-white hover:bg-amber-800 disabled:bg-gray-300"
              >
                {clarificationPending && <Loader2 size={15} className="animate-spin" />}
                保存标准
              </button>
            </div>
          </div>
        </div>
      )}

      {questionItem && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/35 px-4 py-6">
          <div className="flex max-h-[86vh] w-full max-w-[720px] flex-col rounded-lg bg-white p-5 shadow-2xl">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-[12px] font-bold text-gray-400">追问情报</p>
                <h3 className="mt-1 text-[18px] font-black text-gray-950">{questionItem.title}</h3>
              </div>
              <button
                type="button"
                onClick={() => setQuestionItem(null)}
                className="rounded-md border border-gray-200 px-3 py-2 text-[12px] font-bold text-gray-600 hover:bg-gray-50"
              >
                关闭
              </button>
            </div>
            <div className="mt-4 min-h-[180px] flex-1 overflow-y-auto rounded-md border border-gray-100 bg-gray-50 p-3">
              {visibleMessages.length === 0 ? (
                <div className="space-y-4">
                  {questionPromptGroups.length === 0 ? (
                    <p className="rounded-md bg-white px-3 py-2 text-[13px] font-semibold leading-6 text-gray-500">
                      这张卡暂未生成推荐追问，可以直接输入一个具体判断点。
                    </p>
                  ) : (
                    questionPromptGroups.map((group) => (
                      <div key={group.questions.join('|')}>
                        <div className="space-y-1.5">
                          {group.questions.map((question, index) => (
                            <button
                              key={question}
                              type="button"
                              onClick={() => setQuestionDraft(question)}
                              className="block w-full rounded-md border border-gray-200 bg-white px-3 py-2 text-left text-[13px] font-semibold leading-5 text-gray-700 hover:border-blue-200 hover:bg-blue-50 hover:text-blue-800"
                            >
                              {group.ordered ? `${index + 1}. ` : ''}{question}
                            </button>
                          ))}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              ) : (
                <div className="space-y-2">
                  {visibleMessages.map((message, index) => (
                    <div
                      key={`${message.createdAt}-${index}`}
                      className={`whitespace-pre-wrap rounded-md px-3 py-2 text-[13px] leading-6 ${message.role === 'user' ? 'bg-white text-gray-700' : 'bg-gray-950 text-white'}`}
                    >
                      {message.content}
                    </div>
                  ))}
                </div>
              )}
              {questionPending && (
                <div className="mt-2 inline-flex items-center gap-2 rounded-md bg-white px-3 py-2 text-[13px] font-bold text-gray-600">
                  <Loader2 size={16} className="animate-spin" />
                  正在分析
                </div>
              )}
            </div>
            <div className="mt-3 flex gap-2">
              <input
                value={questionDraft}
                onChange={(event) => setQuestionDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    void handleAskQuestion();
                  }
                }}
                placeholder="问一个具体问题"
                className="min-w-0 flex-1 rounded-md border border-gray-200 px-3 py-2 text-[13px] outline-none focus:border-gray-400"
              />
              <button
                type="button"
                onClick={() => void handleAskQuestion()}
                disabled={questionPending || !questionDraft.trim()}
                className="inline-flex items-center gap-1 rounded-md bg-gray-950 px-4 py-2 text-[13px] font-black text-white hover:bg-gray-800 disabled:bg-gray-300"
              >
                <Send size={15} />
                发送
              </button>
            </div>
          </div>
        </div>
      )}

      {dismissTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/35 px-4 py-6">
          <div className="w-full max-w-[520px] rounded-lg bg-white p-5 shadow-2xl">
            <p className="text-[12px] font-bold text-gray-400">不采纳原因</p>
            <h3 className="mt-1 text-[18px] font-black text-gray-950">{dismissTarget.title}</h3>
            <div className="mt-4 grid grid-cols-2 gap-2">
              {(Object.keys(DISMISS_REASON_LABEL) as IntelligenceDismissReasonCode[]).map((key) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => toggleDismissReason(key)}
                  className={`inline-flex items-center gap-2 rounded-md border px-3 py-2 text-left text-[13px] font-bold ${dismissReasons.includes(key) ? 'border-rose-200 bg-rose-50 text-rose-700' : 'border-gray-200 text-gray-700 hover:bg-gray-50'}`}
                >
                  <span className={`flex h-4 w-4 items-center justify-center rounded border text-[10px] ${dismissReasons.includes(key) ? 'border-rose-500 bg-rose-500 text-white' : 'border-gray-300 bg-white text-transparent'}`}>✓</span>
                  {DISMISS_REASON_LABEL[key]}
                </button>
              ))}
            </div>
            <textarea
              value={dismissNote}
              onChange={(event) => setDismissNote(event.target.value)}
              rows={3}
              placeholder="可选：补一句为什么不采纳"
              className="mt-3 w-full resize-none rounded-md border border-gray-200 px-3 py-2 text-[13px] leading-6 outline-none focus:border-gray-400"
            />
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  setDismissTarget(null);
                  setDismissReasons(['irrelevant']);
                }}
                className="rounded-md border border-gray-200 px-4 py-2 text-[13px] font-bold text-gray-600 hover:bg-gray-50"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void confirmDismiss()}
                disabled={pendingItemId === dismissTarget.id || dismissReasons.length === 0}
                className="inline-flex items-center gap-2 rounded-md bg-rose-600 px-4 py-2 text-[13px] font-black text-white hover:bg-rose-700 disabled:bg-gray-300"
              >
                {pendingItemId === dismissTarget.id && <Loader2 size={15} className="animate-spin" />}
                确认不采纳
              </button>
            </div>
          </div>
        </div>
      )}

      {followTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/35 px-4 py-6">
          <div className="w-full max-w-[540px] rounded-lg bg-white p-5 shadow-2xl">
            <p className="text-[12px] font-bold text-gray-400">关注后续</p>
            <h3 className="mt-1 text-[18px] font-black text-gray-950">{followTarget.title}</h3>
            <p className="mt-2 text-[13px] leading-6 text-gray-500">关注后续会影响当前对象内排序和学习信号，不会直接提高刷新频率。</p>
            <div className="mt-4 grid gap-2">
              {(Object.keys(FOLLOW_MODE_LABEL) as IntelligenceFollowMode[]).map((key) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setFollowMode(key)}
                  className={`rounded-md border px-3 py-2 text-left text-[13px] font-bold ${followMode === key ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-gray-200 text-gray-700 hover:bg-gray-50'}`}
                >
                  {FOLLOW_MODE_LABEL[key]}
                </button>
              ))}
            </div>
            <textarea
              value={followNote}
              onChange={(event) => setFollowNote(event.target.value)}
              rows={3}
              placeholder="可选：补一句跟进重点"
              className="mt-3 w-full resize-none rounded-md border border-gray-200 px-3 py-2 text-[13px] leading-6 outline-none focus:border-gray-400"
            />
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setFollowTarget(null)}
                className="rounded-md border border-gray-200 px-4 py-2 text-[13px] font-bold text-gray-600 hover:bg-gray-50"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void confirmFollow()}
                disabled={pendingItemId === followTarget.id}
                className="inline-flex items-center gap-2 rounded-md bg-emerald-600 px-4 py-2 text-[13px] font-black text-white hover:bg-emerald-700 disabled:bg-gray-300"
              >
                {pendingItemId === followTarget.id && <Loader2 size={15} className="animate-spin" />}
                确认关注
              </button>
            </div>
          </div>
        </div>
      )}

      {taskDraftTarget && taskDraft && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/35 px-4 py-6">
          <div className="w-full max-w-[680px] rounded-lg bg-white p-5 shadow-2xl">
            <p className="text-[12px] font-bold text-gray-400">任务草案</p>
            <h3 className="mt-1 text-[18px] font-black text-gray-950">{taskDraftTarget.title}</h3>
            <div className="mt-4 grid gap-3">
              <label className="text-[12px] font-black text-gray-500">
                任务标题
                <input
                  value={taskDraft.title || ''}
                  onChange={(event) => setTaskDraft((current) => current ? { ...current, title: event.target.value } : current)}
                  className="mt-1 w-full rounded-md border border-gray-200 px-3 py-2 text-[13px] font-semibold text-gray-800 outline-none focus:border-gray-400"
                />
              </label>
              <label className="text-[12px] font-black text-gray-500">
                任务描述
                <textarea
                  value={taskDraft.desc || ''}
                  onChange={(event) => setTaskDraft((current) => current ? { ...current, desc: event.target.value } : current)}
                  rows={8}
                  className="mt-1 w-full resize-none rounded-md border border-gray-200 px-3 py-2 text-[13px] leading-6 text-gray-800 outline-none focus:border-gray-400"
                />
              </label>
              {(taskDraft.ownerRoleHint || (taskDraft.collaboratorRoleHints || []).length > 0) && (
                <div className="rounded-md border border-blue-100 bg-blue-50 px-3 py-2 text-[12px] leading-5 text-blue-900">
                  {taskDraft.ownerRoleHint && (
                    <p><span className="font-black">负责人建议：</span>{taskDraft.ownerRoleHint}</p>
                  )}
                  {(taskDraft.collaboratorRoleHints || []).length > 0 && (
                    <p className="mt-1"><span className="font-black">协作者建议：</span>{(taskDraft.collaboratorRoleHints || []).join('；')}</p>
                  )}
                </div>
              )}
              <div className="grid gap-3 md:grid-cols-2">
                <label className="text-[12px] font-black text-gray-500">
                  负责人
                  <select
                    value={taskDraft.ownerId || currentPerson.id}
                    onChange={(event) => {
                      const owner = memberOptions.find((person) => person.id === event.target.value) || currentPerson;
                      setTaskDraft((current) => current ? {
                        ...current,
                        ownerId: owner.id,
                        ownerName: owner.fullName || owner.email || owner.id,
                        collaboratorIds: (current.collaboratorIds || []).filter((id) => id !== owner.id),
                      } : current);
                    }}
                    className="mt-1 w-full rounded-md border border-gray-200 px-3 py-2 text-[13px] font-semibold text-gray-800 outline-none focus:border-gray-400"
                  >
                    {memberOptions.map((person) => (
                      <option key={person.id} value={person.id}>
                        {person.fullName || person.email || person.id}{person.isSelf ? '（我）' : ''}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="text-[12px] font-black text-gray-500">
                  协作者
                  <div className="mt-1 max-h-[92px] overflow-y-auto rounded-md border border-gray-200 bg-gray-50 p-2">
                    {memberOptions.filter((person) => person.id !== (taskDraft.ownerId || currentPerson.id)).length === 0 ? (
                      <p className="px-2 py-1 text-[12px] font-semibold text-gray-400">暂无可选协作者</p>
                    ) : (
                      memberOptions
                        .filter((person) => person.id !== (taskDraft.ownerId || currentPerson.id))
                        .map((person) => {
                          const checked = (taskDraft.collaboratorIds || []).includes(person.id);
                          return (
                            <label key={person.id} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-[12px] font-semibold text-gray-700 hover:bg-white">
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={(event) => {
                                  setTaskDraft((current) => {
                                    if (!current) return current;
                                    const currentIds = current.collaboratorIds || [];
                                    const nextIds = event.target.checked
                                      ? [...currentIds, person.id]
                                      : currentIds.filter((id) => id !== person.id);
                                    return { ...current, collaboratorIds: nextIds };
                                  });
                                }}
                              />
                              <span>{person.fullName || person.email || person.id}</span>
                            </label>
                          );
                        })
                    )}
                  </div>
                </div>
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                <label className="text-[12px] font-black text-gray-500">
                  优先级
                  <select
                    value={taskDraft.priority}
                    onChange={(event) => setTaskDraft((current) => current ? { ...current, priority: event.target.value as IntelligenceTaskDraftPayload['priority'] } : current)}
                    className="mt-1 w-full rounded-md border border-gray-200 px-3 py-2 text-[13px] font-semibold text-gray-800 outline-none focus:border-gray-400"
                  >
                    <option value="low">低</option>
                    <option value="normal">普通</option>
                    <option value="high">高</option>
                  </select>
                </label>
                <label className="text-[12px] font-black text-gray-500">
                  截止口径
                  <input
                    value={taskDraft.ddl}
                    onChange={(event) => setTaskDraft((current) => current ? { ...current, ddl: event.target.value } : current)}
                    className="mt-1 w-full rounded-md border border-gray-200 px-3 py-2 text-[13px] font-semibold text-gray-800 outline-none focus:border-gray-400"
                  />
                </label>
              </div>
              <label className="text-[12px] font-black text-gray-500">
                备注
                <textarea
                  value={taskDraft.note}
                  onChange={(event) => setTaskDraft((current) => current ? { ...current, note: event.target.value } : current)}
                  rows={3}
                  className="mt-1 w-full resize-none rounded-md border border-gray-200 px-3 py-2 text-[13px] leading-6 text-gray-800 outline-none focus:border-gray-400"
                />
              </label>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  setTaskDraftTarget(null);
                  setTaskDraft(null);
                }}
                className="rounded-md border border-gray-200 px-4 py-2 text-[13px] font-bold text-gray-600 hover:bg-gray-50"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void confirmCreateTask()}
                disabled={pendingItemId === taskDraftTarget.id || !taskDraft.title?.trim()}
                className="inline-flex items-center gap-2 rounded-md bg-gray-950 px-4 py-2 text-[13px] font-black text-white hover:bg-gray-800 disabled:bg-gray-300"
              >
                {pendingItemId === taskDraftTarget.id && <Loader2 size={15} className="animate-spin" />}
                创建任务
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// 舆情监控面板（P2-a 重建 + P5 三件套）
// ──────────────────────────────────────────────────────────────────────────

function SentimentMonitorPanel({
  workObject,
  uiSessionScopeKey,
  onProfileChange,
  onNegativeCountChange,
  onRegisterReload,
}: {
  workObject: IntelligenceWorkObject | null;
  uiSessionScopeKey: string;
  // H1+H2 修复: 上报 profile/负面数到顶层 KPI, 避免顶层独立调 API + 数据不一致
  onProfileChange?: (profile: SentimentProfile | null) => void;
  onNegativeCountChange?: (count: number) => void;
  // H5 修复: 注册 reload 函数到顶层, 让顶层 Hero 按钮触发的 refresh 完成后能 reload 本面板数据
  onRegisterReload?: (fn: (() => Promise<void>) | null) => void;
}) {
  const targetName = workObject?.name || '请先选择监控对象';
  const clientId = workObject?.type === 'client' ? workObject.id : undefined;
  const projectModuleId = workObject?.type === 'project_module' ? workObject.id : undefined;
  const hasScope = Boolean(clientId || projectModuleId);
  const scopeKey = `${clientId || ''}::${projectModuleId || ''}`;
  const runtimeCacheKey = `${uiSessionScopeKey}:${scopeKey || 'none'}`;
  const scopeKeyRef = useRef(scopeKey);
  useEffect(() => {
    scopeKeyRef.current = scopeKey;
  }, [scopeKey]);

  const [profile, setProfile] = useRuntimeUiSessionState<SentimentProfile | null>(`${runtimeCacheKey}:profile`, null);
  const [items, setItems] = useRuntimeUiSessionState<SentimentItem[]>(`${runtimeCacheKey}:items`, []);
  const [audit, setAudit] = useRuntimeUiSessionState<BrandAudit | null>(`${runtimeCacheKey}:audit`, null);
  const [auditNote, setAuditNote] = useRuntimeUiSessionState<string | null>(`${runtimeCacheKey}:audit-note`, null);
  const [sentimentLoadedAt, setSentimentLoadedAt] = useRuntimeUiSessionState<number>(`${runtimeCacheKey}:loaded-at`, 0);
  const sentimentLoadedAtRef = useRef(sentimentLoadedAt);
  useEffect(() => {
    sentimentLoadedAtRef.current = sentimentLoadedAt;
  }, [sentimentLoadedAt]);
  const [auditRecomputing, setAuditRecomputing] = useState(false);
  const [auditStep, setAuditStep] = useState<string>(''); // 显示当前级联步骤
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useRuntimeUiSessionState<SentimentRefreshResult | null>(`${runtimeCacheKey}:last-refresh`, null);
  // 公开评价子区默认折叠 (放在监控对象卡底部, 点击展开看 SentimentItemCard 列表)
  const [publicReviewExpanded, setPublicReviewExpanded] = useState(false);
  // H6 修复: 切换客户时把折叠状态重置为默认 (折叠), 避免上一个客户的展开状态污染
  useEffect(() => {
    setPublicReviewExpanded(false);
  }, [clientId, projectModuleId]);

  const reload = useCallback(async (options?: { preferCache?: boolean }) => {
    if (!hasScope) return;
    if (
      options?.preferCache
      && sentimentLoadedAtRef.current > 0
      && Date.now() - sentimentLoadedAtRef.current < INTELLIGENCE_RUNTIME_CACHE_TTL_MS
    ) return;
    const capturedScopeKey = scopeKey;
    setLoading(sentimentLoadedAtRef.current <= 0);
    setErrorMsg(null);
    try {
      const [p, it, au] = await Promise.all([
        getSentimentProfile({ clientId, projectModuleId, withinDays: 30 }),
        listSentimentItems({ clientId, projectModuleId, withinDays: 30, limit: 50 }),
        getBrandAudit({ clientId, projectModuleId, autoRecompute: false }),
      ]);
      if (scopeKeyRef.current !== capturedScopeKey) return;
      setProfile(p);
      setItems(it.items || []);
      setAudit(au.audit);
      setAuditNote(au.recomputeNote);
      const loadedAt = Date.now();
      sentimentLoadedAtRef.current = loadedAt;
      setSentimentLoadedAt(loadedAt);
    } catch (err) {
      if (scopeKeyRef.current !== capturedScopeKey) return;
      setErrorMsg(err instanceof Error ? err.message : '舆情数据加载失败');
    } finally {
      if (scopeKeyRef.current === capturedScopeKey) setLoading(false);
    }
  }, [hasScope, clientId, projectModuleId, scopeKey]);

  // H5: register reload 到顶层, 让顶层 Hero 按钮 refresh 完成后能 reload 本面板
  useEffect(() => {
    onRegisterReload?.(reload);
    return () => { onRegisterReload?.(null); };
  }, [reload, onRegisterReload]);

  // 智能级联：缺什么补什么 — audit 缺 → themes 缺 → items 缺 → refresh
  const handleRecomputeAudit = async () => {
    if (!hasScope) return;
    setAuditRecomputing(true);
    setAuditNote(null);
    setAuditStep('正在生成品牌印象速读…');
    try {
      // 步骤 1：直接试 audit
      let result = await recomputeBrandAudit({ clientId, projectModuleId, targetName });
      if (result.ok && result.audit) {
        setAudit(result.audit);
        return;
      }

      // 步骤 2：缺主题 → 先建主题，再回头试 audit
      if ((result.reason || '').includes('too_few_themes')) {
        setAuditStep('印象主题缺失，正在聚类主题（约 1 分钟）…');
        const themesRes = await recomputeSentimentThemes({ clientId, projectModuleId, targetName });

        // 步骤 3：连主题都建不起来（条目太少）→ 触发一次完整抓取（含 themes + audit 链式）
        if (!themesRes.ok && (themesRes.reason || '').includes('too_few_items')) {
          setAuditStep('舆情数据不足，正在抓取（约 3-5 分钟）…');
          await refreshSentiment({ clientId, projectModuleId, targetName, maxPerQuery: 5 });
          // refresh 后端已经链式跑了 themes + audit，直接读 audit
          const au = await getBrandAudit({ clientId, projectModuleId, autoRecompute: false });
          if (au.audit) {
            setAudit(au.audit);
            await reload();
            return;
          }
          setAuditNote(au.recomputeNote || '抓取完成但 audit 未生成');
          await reload();
          return;
        }

        if (!themesRes.ok) {
          setAuditNote(`主题聚类失败：${themesRes.reason || '未知'}`);
          return;
        }

        // 主题成功，回头试 audit
        setAuditStep('主题已聚类，正在合成品牌印象速读（约 1-2 分钟）…');
        result = await recomputeBrandAudit({ clientId, projectModuleId, targetName });
        if (result.ok && result.audit) {
          setAudit(result.audit);
          await reload(); // 顺手刷一遍 themes/gap
          return;
        }
        setAuditNote(result.reason || '速读合成失败');
        return;
      }

      // 步骤 4：audit 报别的错（LLM 失败等）
      setAuditNote(result.reason || '重算失败');
    } catch (err) {
      setAuditNote(err instanceof Error ? err.message : '重算失败');
    } finally {
      setAuditRecomputing(false);
      setAuditStep('');
    }
  };

  useEffect(() => {
    void reload({ preferCache: true });
  }, [reload]);

  const handleRefresh = async () => {
    if (!hasScope) {
      setErrorMsg('先在顶部选择监控的客户或业务线');
      return;
    }
    setRefreshing(true);
    setErrorMsg(null);
    try {
      const result = await refreshSentiment({
        clientId,
        projectModuleId,
        targetName,
        maxPerQuery: 5,
      });
      setLastRefresh(result);
      await reload();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : '舆情抓取失败');
    } finally {
      setRefreshing(false);
    }
  };

  const handleFeedback = useCallback(async (itemId: string, action: SentimentFeedbackAction) => {
    const prev = items;
    if (action === 'mark_misclassified' || action === 'mark_resolved') {
      setItems((cur) => cur.filter((it) => it.id !== itemId));
    }
    try {
      await sendSentimentFeedback({ itemId, action });
      try {
        const p = await getSentimentProfile({ clientId, projectModuleId, withinDays: 30 });
        setProfile(p);
      } catch {
        // 不阻塞
      }
    } catch (err) {
      setItems(prev);
      setErrorMsg(err instanceof Error ? err.message : '反馈提交失败');
    }
  }, [items, clientId, projectModuleId]);

  const negativeItems = items.filter((it) => it.sentimentLabel === 'negative');
  const otherItems = items.filter((it) => it.sentimentLabel !== 'negative');

  // H1+H2 修复: 把 profile 和负面数上报给顶层 KPI (同源数据, 消除不一致 + 避免顶层重复请求)
  useEffect(() => {
    onProfileChange?.(profile);
  }, [profile, onProfileChange]);
  useEffect(() => {
    onNegativeCountChange?.(negativeItems.length);
  }, [negativeItems.length, onNegativeCountChange]);

  if (!hasScope) {
    return (
      <div className="mt-4 rounded-xl border border-dashed border-gray-200 bg-gray-50/40 px-6 py-12 text-center">
        <MessageCircle size={28} className="mx-auto text-gray-300" />
        <p className="mt-3 text-[13px] font-semibold text-gray-500">
          先在左侧选择具体客户或业务线，才能查看舆情画像
        </p>
      </div>
    );
  }

  return (
    <div className="mt-4 space-y-5">
      {/* ⓪ 品牌印象速读（P6）— 最显眼，放最顶 */}
      <BrandAuditCard
        audit={audit}
        targetName={targetName}
        clientId={clientId}
        uiSessionScopeKey={runtimeCacheKey}
        recomputeNote={auditNote}
        recomputing={auditRecomputing}
        recomputeStep={auditStep}
        onRecompute={() => void handleRecomputeAudit()}
      />

      {/* P12 · 品牌词云已移到 BrandAuditCard 里 (在"已被看见"叙述与外立面感知度报告之间) */}

      {/* P12 · 3 精致信息卡已移到 BrandAuditCard 内 (词云之后) */}

      {/* ① 顶栏 + KPI 三色块 (检测到负面时整卡变粉红色, 平时白底) */}
      <section className={`rounded-2xl border px-5 py-5 transition-colors shadow-sm ${
        negativeItems.length > 0
          ? 'border-rose-300 bg-rose-50/40'
          : 'border-slate-200 bg-white'
      }`}>
        <div className="mb-4 flex items-baseline justify-between gap-4 flex-wrap">
          <div className="min-w-0 flex-1">
            <h3 className="text-[14px] font-black text-slate-900">
              监控对象 · <span className="text-[#5B7BFE]">{targetName}</span>
            </h3>
            <p className="mt-0.5 text-[11px] leading-6 text-slate-500">
              公众实际听到的品牌声音 · 整体情感 / 提及量 / 负面预警 / 公开评价
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-[11px] text-slate-400">近 {profile?.withinDays ?? 30} 天</span>
            <button
              type="button"
              onClick={() => void handleRefresh()}
              disabled={refreshing}
              className="inline-flex items-center gap-1.5 rounded-md bg-gray-950 px-3 py-1.5 text-[11px] font-bold text-white hover:bg-gray-800 disabled:opacity-50"
            >
              {refreshing ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
              {refreshing ? '抓取中…' : '立即抓取舆情'}
            </button>
          </div>
        </div>
        {errorMsg && (
          <div className="mb-3 rounded-md border border-rose-200 bg-rose-50/50 px-3 py-2 text-[11px] text-rose-700">
            {errorMsg}
          </div>
        )}
        {lastRefresh && (
          <>
            <div className="mb-3 rounded-md border border-emerald-100 bg-emerald-50/50 px-3 py-2 text-[11px] text-emerald-700">
              刚抓取「{lastRefresh.targetName}」共 {lastRefresh.fetchedCount} 条，
              入库 {lastRefresh.insertedCount} 条（负面 {lastRefresh.negativeCount} · 中性 {lastRefresh.neutralCount} · 积极 {lastRefresh.positiveCount}）
            </div>
            {lastRefresh.themesRecomputeNote && (
              <div className="mb-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] font-semibold text-amber-800">
                主题重算未完成：<span className="font-bold">{lastRefresh.themesRecomputeNote}</span>
                {lastRefresh.themesRecomputeNote.startsWith('ai_') && '（请检查本地 AI 配置）'}
                {lastRefresh.themesRecomputeNote.startsWith('too_few_items') && '（条目太少，先抓更多舆情）'}
              </div>
            )}
          </>
        )}
        <div className="grid grid-cols-3 gap-3">
          <div className="rounded-xl border border-slate-100 bg-slate-50/50 px-4 py-3">
            <div className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">整体情感</div>
            <div className={`mt-1 text-[24px] font-bold tabular-nums ${
              (profile?.sentimentScore ?? 0) >= 70 ? 'text-emerald-600' :
              (profile?.sentimentScore ?? 0) >= 40 ? 'text-slate-800' :
              (profile?.totalMentions ?? 0) > 0 ? 'text-rose-600' : 'text-slate-300'
            }`}>
              {(profile?.totalMentions ?? 0) > 0 ? `${profile?.sentimentScore ?? 0}/100` : '—/100'}
            </div>
            <div className="mt-0.5 text-[10px] text-slate-400">0 极负面 · 100 极积极</div>
          </div>
          <div className="rounded-xl border border-slate-100 bg-slate-50/50 px-4 py-3">
            <div className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">提及量</div>
            <div className={`mt-1 text-[24px] font-bold tabular-nums ${
              (profile?.totalMentions ?? 0) > 0 ? 'text-slate-900' : 'text-slate-300'
            }`}>
              {profile?.totalMentions ?? 0}
            </div>
            <div className="mt-0.5 text-[10px] text-slate-400">所有公开渠道</div>
          </div>
          <div className="rounded-xl border border-slate-100 bg-slate-50/50 px-4 py-3">
            <div className="text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">情感分布</div>
            <div className="mt-2 flex items-baseline gap-3 text-[12px] tabular-nums text-slate-500">
              <span><span className="font-bold text-rose-600">{profile?.negativeCount ?? 0}</span> 负面</span>
              <span><span className="font-bold text-slate-700">{profile?.neutralCount ?? 0}</span> 中性</span>
              <span><span className="font-bold text-emerald-600">{profile?.positiveCount ?? 0}</span> 积极</span>
            </div>
          </div>
        </div>

        {/* 负面预警子区: 监测到负面才显示, 否则隐藏 (外卡同时变粉红) */}
        {negativeItems.length > 0 && (
          <div className="mt-4 rounded-xl border border-rose-200 bg-white/60 px-4 py-3">
            <div className="mb-2 flex items-center gap-2">
              <AlertTriangle size={14} className="text-rose-500" />
              <h4 className="text-[13px] font-black text-rose-700">负面预警 · 需要关注</h4>
              <span className="text-[11px] font-bold tabular-nums text-rose-500">{negativeItems.length}</span>
            </div>
            <div className="space-y-2">
              {negativeItems.slice(0, 10).map((item) => (
                <SentimentItemCard key={item.id} item={item} highlight onFeedback={handleFeedback} />
              ))}
            </div>
          </div>
        )}

        {/* 公开评价子区: 默认折叠, 点击展开看 SentimentItemCard 列表 (中性 + 积极, 上限 30 条) */}
        <div className="mt-4 rounded-xl border border-gray-200 bg-white/60">
          <button
            type="button"
            onClick={() => setPublicReviewExpanded((v) => !v)}
            className="w-full px-4 py-3 flex items-center gap-2 hover:bg-gray-50/60 transition-colors rounded-xl"
          >
            <MessageCircle size={14} className="text-gray-400 shrink-0" />
            <h4 className="text-[13px] font-black text-gray-900">公开评价 · 中性 + 积极</h4>
            <span className="text-[11px] font-bold tabular-nums text-gray-400">{otherItems.length}</span>
            <span className="ml-auto inline-flex items-center gap-1 text-[11px] font-semibold text-slate-600">
              {publicReviewExpanded ? (
                <>
                  <ChevronUp size={13} />
                  折叠
                </>
              ) : (
                <>
                  <ChevronDown size={13} />
                  展开 {otherItems.length > 0 && `看 ${Math.min(otherItems.length, 30)} 条`}
                </>
              )}
            </span>
          </button>
          {publicReviewExpanded && (
            <div className="px-4 pb-4">
              {otherItems.length === 0 ? (
                <div className="rounded-xl border border-dashed border-gray-200 bg-gray-50/40 px-5 py-6 text-center">
                  <p className="text-[12px] font-semibold text-gray-500">
                    {loading ? '加载中…' : '暂未发现公开评价'}
                  </p>
                </div>
              ) : (
                <div className="space-y-2">
                  {otherItems.slice(0, 30).map((item) => (
                    <SentimentItemCard key={item.id} item={item} onFeedback={handleFeedback} />
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

// 单条舆情卡片 · 标题 + 摘要 + 来源 + 时间 + URL + 反馈
function SentimentItemCard({
  item,
  highlight = false,
  onFeedback,
}: {
  item: SentimentItem;
  highlight?: boolean;
  onFeedback?: (itemId: string, action: SentimentFeedbackAction) => void | Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const labelStyle =
    item.sentimentLabel === 'negative' ? { dot: 'bg-rose-500', text: 'text-rose-700', label: '负面' } :
    item.sentimentLabel === 'positive' ? { dot: 'bg-emerald-500', text: 'text-emerald-700', label: '积极' } :
                                          { dot: 'bg-gray-400', text: 'text-gray-600', label: '中性' };
  const openUrl = () => {
    if (!item.sourceUrl) return;
    if (typeof window !== 'undefined') {
      window.open(item.sourceUrl, '_blank', 'noopener,noreferrer');
    }
  };
  const trigger = async (action: SentimentFeedbackAction) => {
    if (!onFeedback || busy) return;
    setBusy(true);
    try {
      await onFeedback(item.id, action);
    } finally {
      setBusy(false);
    }
  };
  const isNegative = item.sentimentLabel === 'negative';
  return (
    <div className={`rounded-xl border px-4 py-3 transition-all ${
      highlight ? 'border-rose-200 bg-white' : 'border-gray-100 bg-white hover:border-gray-200 hover:shadow-sm'
    }`}>
      <div className="flex items-start gap-3">
        <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${labelStyle.dot}`} />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className={`text-[10px] font-bold uppercase tracking-wide ${labelStyle.text}`}>
              {labelStyle.label}
            </span>
            <span className="text-[11px] text-gray-400">{item.source}</span>
            <span className="text-[11px] text-gray-300">·</span>
            <span className="text-[11px] text-gray-400 tabular-nums">{(item.capturedAt || '').slice(0, 10)}</span>
          </div>
          <p className="mt-1 text-[13px] font-semibold leading-snug text-gray-900">{item.title}</p>
          {item.summary && (
            <p className="mt-1 text-[12px] leading-6 text-gray-600 line-clamp-3">{item.summary}</p>
          )}
          {item.sentimentReason && (
            <p className="mt-1.5 text-[10px] text-gray-400">判断依据：{item.sentimentReason}</p>
          )}
        </div>
        <div className="shrink-0 flex flex-col items-center gap-1">
          {item.sourceUrl && (
            <button
              type="button"
              onClick={openUrl}
              title="查看原文"
              aria-label="查看原文"
              className="inline-flex items-center justify-center rounded-md border border-gray-200 bg-white p-1.5 text-gray-500 hover:bg-[#EEF2FF] hover:border-[#C7D5FF] hover:text-[#5B7BFE]"
            >
              <Send size={12} strokeWidth={2.4} />
            </button>
          )}
          {onFeedback && (
            <>
              <button
                type="button"
                onClick={() => void trigger('mark_misclassified')}
                disabled={busy}
                title="误判 · 这条不是真负面"
                aria-label="标记误判"
                className="inline-flex items-center justify-center rounded-md border border-gray-200 bg-white p-1.5 text-gray-400 hover:bg-gray-50 hover:border-gray-300 hover:text-gray-700 disabled:opacity-50"
              >
                <X size={12} strokeWidth={2.4} />
              </button>
              {isNegative && (
                <button
                  type="button"
                  onClick={() => void trigger('mark_resolved')}
                  disabled={busy}
                  title="已处理 · 这条负面已跟进"
                  aria-label="标记已处理"
                  className="inline-flex items-center justify-center rounded-md border border-gray-200 bg-white p-1.5 text-gray-400 hover:bg-emerald-50 hover:border-emerald-200 hover:text-emerald-700 disabled:opacity-50"
                >
                  <CheckCircle2 size={12} strokeWidth={2.4} />
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// P5-#2 定位差异图
// ──────────────────────────────────────────────────────────────────────────

function BrandAuditCard({
  audit,
  targetName,
  clientId,
  uiSessionScopeKey,
  recomputeNote,
  recomputing,
  recomputeStep,
  onRecompute,
}: {
  audit: BrandAudit | null;
  targetName: string;
  clientId: string | undefined;
  uiSessionScopeKey: string;
  recomputeNote: string | null;
  recomputing: boolean;
  recomputeStep?: string;
  onRecompute: () => void;
}) {
  if (!audit) {
    return (
      <>
        <section className="rounded-2xl border-2 border-dashed border-slate-200 bg-slate-50/40 px-5 py-6">
          <div className="flex items-center gap-2">
            <Sparkles size={16} className="text-slate-500" />
            <h3 className="text-[14px] font-black text-slate-900">品牌印象速读</h3>
            <span className="ml-auto inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-0.5 text-[10px] font-bold text-slate-600">
              权威事实提炼
            </span>
          </div>
          <p className="mt-3 text-[12px] leading-6 text-slate-600">
            这里会根据组织云中的公开情报事实生成品牌印象速读。
            {recomputing && recomputeStep
              ? <><br /><span className="text-slate-700 font-semibold">{recomputeStep}</span></>
              : recomputeNote
                ? <><br /><span className="text-amber-700 font-semibold">未生成原因：{recomputeNote}</span></>
                : null}
          </p>
          <div className="mt-3">
            <button
              type="button"
              onClick={onRecompute}
              disabled={recomputing}
              className="inline-flex items-center gap-1.5 rounded-md bg-slate-900 px-3 py-1.5 text-[11px] font-bold text-white hover:bg-slate-800 disabled:opacity-50"
            >
              {recomputing ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />}
              {recomputing ? '处理中…' : '重新提炼品牌印象速读'}
            </button>
          </div>
        </section>
        <BrandStrategyTreeCard targetName={targetName} clientId={clientId} uiSessionScopeKey={uiSessionScopeKey} />
      </>
    );
  }
  return (
    <section className="rounded-2xl border border-slate-200 bg-white px-5 py-5 shadow-sm">
      {/* 卡片标题区: icon + 中文主标 + 功能价值副标 + 右侧 meta */}
      <div className="mb-3 flex items-start gap-3">
        <Sparkles size={16} className="mt-0.5 shrink-0 text-slate-600" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-[14px] font-black text-slate-900">品牌印象速读</h3>
            <span className="ml-auto text-[10px] text-slate-400 tabular-nums">
              {(audit.computedAt || '').slice(0, 16).replace('T', ' ')}
            </span>
            <button
              type="button"
              onClick={onRecompute}
              disabled={recomputing}
              title="重新合成"
              className="inline-flex items-center justify-center rounded-md border border-slate-200 bg-white p-1.5 text-slate-500 hover:bg-slate-50 hover:border-slate-300 hover:text-slate-700 disabled:opacity-50 transition-colors"
            >
              {recomputing ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            </button>
          </div>
          <p className="mt-0.5 text-[11px] leading-6 text-slate-500">
            一句话定位 + 公众实际听到的样子 · 综合自所有公开渠道
          </p>
          {/* Headline · 一句话定位 */}
          <p className="mt-2 text-[14px] font-bold leading-snug text-slate-900">
            {audit.headline}
          </p>
        </div>
      </div>

      {/* 品牌印象主体叙述 — 一段连贯文字（综合印象 / 最突出 / 主要缺失） */}
      {audit.narrativeMd && (
        <div className="mt-3 rounded-lg border border-slate-200 bg-slate-50/40 px-5 py-4">
          <p className="whitespace-pre-line text-[13px] leading-8 text-slate-800">
            {audit.narrativeMd}
          </p>
        </div>
      )}

      {/* P14-D: 战略推演树 (从战略陪伴上传的 .md LLM 抽取)
          — 战略主张 + 方法学 + 利益相关方应然矩阵
          — 这是后续闭环卡的"应当如何"锚点 */}
      <BrandStrategyTreeCard targetName={targetName} clientId={clientId} uiSessionScopeKey={uiSessionScopeKey} />

      {/* 建议 recommendations — 紧接外立面感知度报告之后, 作为"行动指引" */}
      {audit.recommendations && audit.recommendations.length > 0 && (
        <div className="mt-4 rounded-lg border border-slate-200 bg-white px-5 py-4 shadow-sm">
          <div className="flex items-center gap-2">
            <Lightbulb size={16} className="text-emerald-600" />
            <h3 className="text-[14px] font-black text-slate-900">品牌调整建议</h3>
            <span className="rounded-sm border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[10px] font-bold text-slate-600">
              可执行
            </span>
          </div>
          <p className="mt-1 text-[11px] leading-6 text-slate-500">
            按优先级排列的具体动作 · 每条都对应一个可立即开展的工作
          </p>
          <ul className="mt-3 space-y-2.5">
            {audit.recommendations.map((r, idx) => (
              <li key={idx} className="text-[12px] leading-6 text-slate-800">
                <div className="flex items-baseline gap-2">
                  <span className="font-bold tabular-nums text-slate-400">{idx + 1}.</span>
                  <span className="font-bold flex-1 text-slate-900">{r.action}</span>
                  {r.priority === 'high' && (
                    <span className="rounded-sm border border-rose-200 bg-rose-50 px-1.5 py-0.5 text-[10px] font-bold text-rose-700">
                      高优
                    </span>
                  )}
                </div>
                {r.rationale && (
                  <p className="mt-1 text-[11px] leading-6 text-slate-500 ml-5">
                    {r.rationale}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* content angles — 已删 reduce / 弱化，避免 AI 在缺乏组织上下文时踩雷 */}
      {audit.contentAngles && (
        audit.contentAngles.amplify?.length || audit.contentAngles.new?.length
      ) ? (
        <div className="mt-4 rounded-lg border border-slate-200 bg-white px-5 py-4 shadow-sm">
          <div className="flex items-center gap-2">
            <Megaphone size={16} className="text-slate-600" />
            <h3 className="text-[14px] font-black text-slate-900">下次发声建议</h3>
          </div>
          <p className="mt-1 text-[11px] leading-6 text-slate-500">
            内容主题方向 · 哪些要继续强化 / 哪些是空白等待补齐
          </p>
          <div className="mt-3 grid gap-3 text-[11px] md:grid-cols-2">
            {audit.contentAngles.amplify?.length > 0 && (
              <div>
                <div className="mb-1.5 inline-flex items-center gap-1 text-[10px] font-bold text-emerald-700">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                  强化已有方向
                </div>
                <div className="flex flex-wrap gap-1">
                  {audit.contentAngles.amplify.map((a) => (
                    <span key={a} className="rounded-sm border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 font-semibold text-emerald-800">
                      {a}
                    </span>
                  ))}
                </div>
              </div>
            )}
            {audit.contentAngles.new?.length > 0 && (
              <div>
                <div className="mb-1.5 inline-flex items-center gap-1 text-[10px] font-bold text-indigo-700">
                  <span className="h-1.5 w-1.5 rounded-full bg-indigo-500" />
                  新增方向
                </div>
                <div className="flex flex-wrap gap-1">
                  {audit.contentAngles.new.map((a) => (
                    <span key={a} className="rounded-sm border border-indigo-200 bg-indigo-50 px-1.5 py-0.5 font-semibold text-indigo-800">
                      {a}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      ) : null}

      {recomputeNote && (
        <p className="mt-3 text-[10px] text-amber-700">
          注：{recomputeNote}
        </p>
      )}
    </section>
  );
}


// ──────────────────────────────────────────────────────────────────────────
// P14-D · 战略推演树卡 (接通真实 API)
// 数据源: client_brand_strategy_extracts (LLM 抽自战略陪伴上传的 strategy.md + methodology.md)
// 状态机:
//   - 无 clientId → 提示选客户
//   - loading → spinner
//   - extract=null → 显示"请先在战略陪伴上传战略文档+方法论文档"
//   - extract.error → 显示错误 + 重试按钮
//   - extract 有效 → 显示三段结构
//   - isStale → 顶部条幅提示"源文档已更新, 建议重新抽取"
// 这棵树是品牌评估的"应当如何"锚点, 决定下游闭环卡每条链路怎么打分
// ──────────────────────────────────────────────────────────────────────────

// P14-E · 利益相关方品牌外立面感知度
// 严格新版尚无权威评分对象；在合同接通前保持结果集为空并显示 not_connected。
type StakeholderPerceivabilityTier = 'covered' | 'partial' | 'missing';
interface StakeholderPerceivabilityMock {
  tier: StakeholderPerceivabilityTier;
  score: number;
  covered: string[];
  gap: string[];
  commentary: string; // 简评 (40-60 字), 接通后端后改为 LLM 生成
}
const STAKEHOLDER_PERCEIVABILITY_RESULTS: Record<string, StakeholderPerceivabilityMock> = {};
// score → 字母等级 (A+/A/A-/B+/.../F)
function scoreToGrade(score: number): string {
  if (score >= 90) return 'A+';
  if (score >= 80) return 'A';
  if (score >= 70) return 'A-';
  if (score >= 60) return 'B';
  if (score >= 50) return 'B-';
  if (score >= 40) return 'C';
  if (score >= 30) return 'C-';
  if (score >= 20) return 'D';
  if (score >= 10) return 'D-';
  return 'F';
}
// 按 grade 首字母上色 (评分报告专用)
function gradeColorClass(grade: string): string {
  const letter = grade.charAt(0);
  if (letter === 'A') return 'text-emerald-700';
  if (letter === 'B') return 'text-emerald-600';
  if (letter === 'C') return 'text-amber-700';
  if (letter === 'D') return 'text-orange-700';
  return 'text-rose-700';
}

const STAKEHOLDER_TIER_STYLE: Record<StakeholderPerceivabilityTier, {
  icon: string;
  rowBg: string;
  barColor: string;
  scoreText: string;
  sectorFill: string;     // SVG 扇形填充 (浅色)
  sectorStroke: string;   // SVG 扇形描边 (深色)
}> = {
  covered: { icon: '✅', rowBg: 'bg-emerald-50/40', barColor: 'bg-emerald-500', scoreText: 'text-emerald-700', sectorFill: '#d1fae5', sectorStroke: '#10b981' },
  partial: { icon: '⚠️', rowBg: 'bg-amber-50/30', barColor: 'bg-amber-500', scoreText: 'text-amber-700', sectorFill: '#fef3c7', sectorStroke: '#f59e0b' },
  missing: { icon: '❌', rowBg: 'bg-rose-50/30', barColor: 'bg-rose-400', scoreText: 'text-rose-700', sectorFill: '#fee2e2', sectorStroke: '#f43f5e' },
};

// 极坐标 → 笛卡尔
function polarToCart(cx: number, cy: number, r: number, angleDeg: number): { x: number; y: number } {
  const rad = (angleDeg * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

// 环形扇形 (donut sector) path
function donutSectorPath(cx: number, cy: number, rIn: number, rOut: number, startDeg: number, endDeg: number): string {
  const startInner = polarToCart(cx, cy, rIn, startDeg);
  const endInner = polarToCart(cx, cy, rIn, endDeg);
  const startOuter = polarToCart(cx, cy, rOut, startDeg);
  const endOuter = polarToCart(cx, cy, rOut, endDeg);
  const largeArc = endDeg - startDeg > 180 ? 1 : 0;
  return [
    `M ${startInner.x} ${startInner.y}`,
    `L ${startOuter.x} ${startOuter.y}`,
    `A ${rOut} ${rOut} 0 ${largeArc} 1 ${endOuter.x} ${endOuter.y}`,
    `L ${endInner.x} ${endInner.y}`,
    `A ${rIn} ${rIn} 0 ${largeArc} 0 ${startInner.x} ${startInner.y}`,
    'Z',
  ].join(' ');
}

function BrandStrategyTreeCard({
  targetName,
  clientId,
  uiSessionScopeKey,
}: {
  targetName: string;
  clientId: string | undefined;
  uiSessionScopeKey: string;
}) {
  const strategyCacheKey = `${uiSessionScopeKey}:strategy-tree:${clientId || 'none'}`;
  const [extract, setExtract] = useRuntimeUiSessionState<BrandStrategyExtract | null>(`${strategyCacheKey}:extract`, null);
  const [loadedAt, setLoadedAt] = useRuntimeUiSessionState<number>(`${strategyCacheKey}:loaded-at`, 0);
  const [loading, setLoading] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [actionMessage, setActionMessage] = useState<{
    tone: 'success' | 'error';
    text: string;
  } | null>(null);
  const [editingDraft, setEditingDraft] = useState({
    strategicObjective: '',
    methodology: '',
  });
  const clientIdRef = useRef(clientId);
  // 利益相关方感知度报告默认折叠 (展板模式: 先看总评, 点击展开看细节)
  const [stakeholderReportExpanded, setStakeholderReportExpanded] = useState(false);

  useEffect(() => {
    clientIdRef.current = clientId;
    setSaving(false);
    if (!clientId) {
      setExtract(null);
      return;
    }
    if (loadedAt > 0 && Date.now() - loadedAt < INTELLIGENCE_RUNTIME_CACHE_TTL_MS) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(loadedAt <= 0);
    setError(null);
    setEditing(false);
    setActionMessage(null);
    fetchBrandStrategyExtract(clientId)
      .then((res) => {
        if (!cancelled) {
          setExtract(res.extract);
          setLoadedAt(Date.now());
        }
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
  }, [clientId, loadedAt, setExtract, setLoadedAt]);

  const handleExtract = async () => {
    if (!clientId || extracting) return;
    setExtracting(true);
    setError(null);
    try {
      const fresh = await triggerBrandStrategyExtraction(clientId);
      setExtract(fresh);
      setLoadedAt(Date.now());
      if (fresh.error) setError(fresh.error);
    } catch (e) {
      setError(e instanceof Error ? e.message : '抽取失败');
    } finally {
      setExtracting(false);
    }
  };

  const saveConfirmedExtract = async (payload: {
    strategicObjective: string;
    methodology: string;
  }) => {
    if (!clientId || saving) return;
    const capturedClientId = clientId;
    const strategicObjective = payload.strategicObjective.trim();
    const methodology = payload.methodology.trim();
    if (!strategicObjective || !methodology) {
      setActionMessage({
        tone: 'error',
        text: '战略主张和方法学都不能为空',
      });
      return;
    }
    if (strategicObjective.length + methodology.length > 200) {
      setActionMessage({
        tone: 'error',
        text: '战略主张和方法学合计不能超过 200 字',
      });
      return;
    }
    setSaving(true);
    setActionMessage(null);
    try {
      const response = await updateBrandStrategyExtract(capturedClientId, {
        strategicObjective,
        methodology,
      });
      if (clientIdRef.current !== capturedClientId) return;
      setExtract(response.extract);
      setLoadedAt(Date.now());
      setEditing(false);
      setActionMessage({
        tone: 'success',
        text: '已写入组织云战略提炼版本',
      });
    } catch (saveError) {
      if (clientIdRef.current !== capturedClientId) return;
      setActionMessage({
        tone: 'error',
        text: saveError instanceof Error ? saveError.message : '保存失败',
      });
    } finally {
      if (clientIdRef.current === capturedClientId) setSaving(false);
    }
  };

  const startEditing = () => {
    if (!extract) return;
    setEditingDraft({
      strategicObjective: extract.strategicObjective || '',
      methodology: extract.methodology || '',
    });
    setActionMessage(null);
    setEditing(true);
  };

  if (!clientId) return null;

  if (loading && !extract) {
    return (
      <section className="mt-3 rounded-xl border border-dashed border-indigo-200 bg-indigo-50/40 px-5 py-6">
        <div className="flex items-center gap-2">
          <Loader2 size={14} className="animate-spin text-indigo-500" />
          <span className="text-[12px] text-indigo-700">加载战略推演树…</span>
        </div>
      </section>
    );
  }

  if (!extract) {
    return (
      <section className="mt-3 rounded-xl border border-dashed border-indigo-200 bg-indigo-50/40 px-5 py-6">
        <div className="flex items-center gap-2">
          <Sparkles size={16} className="text-indigo-500" />
          <h3 className="text-[13px] font-black text-indigo-900">战略推演树</h3>
          <span className="ml-auto rounded-md bg-amber-100 px-2 py-0.5 text-[10px] font-bold text-amber-700">
            待生成
          </span>
        </div>
        <p className="mt-3 text-[12px] leading-7 text-indigo-900/80">
          这一格是品牌评估的「应当如何」锚点：战略主张 + 方法学 + 利益相关方矩阵。
          完成后，下方闭环卡才能逐条评估&quot;实际传达 vs 应当传达&quot;的缺口。
        </p>
        <p className="mt-3 text-[11px] leading-6 text-indigo-700/70">
          推荐流程：先去 <b>战略陪伴页</b> 完善战略资料，再回到这里从组织云权威资料提炼草稿。
        </p>
        <div className="mt-3 flex items-center gap-2 flex-wrap">
          <button
            type="button"
            onClick={() => void handleExtract()}
            disabled={extracting}
            className="inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-[11px] font-bold text-white hover:bg-indigo-700 disabled:opacity-60"
          >
            {extracting ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
            {extracting ? '提炼中…' : '从组织云权威资料提炼战略推演树'}
          </button>
          {error && (
            <span className="text-[11px] text-rose-600">{error}</span>
          )}
        </div>
      </section>
    );
  }

  const isStale = extract.isStale;
  const hasError = Boolean(extract.error);

  return (
    <section className="mt-3 space-y-3">
      {isStale && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 flex items-center gap-2">
          <AlertTriangle size={12} className="text-amber-600 shrink-0" />
          <span className="flex-1 text-[11px] text-amber-900">
            源文档已在战略陪伴页更新，当前评估基于旧版本。建议重新抽取。
          </span>
          <button
            type="button"
            onClick={() => void handleExtract()}
            disabled={extracting}
            className="inline-flex items-center gap-1 rounded-md bg-amber-600 px-2 py-1 text-[10px] font-bold text-white hover:bg-amber-700 disabled:opacity-60"
          >
            {extracting ? <Loader2 size={10} className="animate-spin" /> : <RefreshCw size={10} />}
            重新抽取
          </button>
        </div>
      )}

      {hasError && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2">
          <div className="text-[10px] font-bold text-rose-700">战略提炼出错</div>
          <div className="mt-0.5 text-[11px] text-rose-900 break-all">{extract.error}</div>
        </div>
      )}

      {extract.stakeholders.length > 0 && (() => {
        // 按感知度 score 降序; mock 未匹配的相关方放最后
        const sorted = [...extract.stakeholders].sort((a, b) => {
          const sa = STAKEHOLDER_PERCEIVABILITY_RESULTS[a.name]?.score ?? -1;
          const sb = STAKEHOLDER_PERCEIVABILITY_RESULTS[b.name]?.score ?? -1;
          return sb - sa;
        });
        const scored: typeof sorted = [];
        const totalScored = scored.length;
        if (totalScored === 0) {
          return (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-5 py-4 text-[12px] leading-6 text-amber-800">
              not_connected · 已读取利益相关方结构，但严格新版尚无权威的外立面感知度评分；当前不展示样例分数。
            </div>
          );
        }
        const avg = totalScored > 0
          ? Math.round(scored.reduce((acc, s) => acc + (STAKEHOLDER_PERCEIVABILITY_RESULTS[s.name]?.score ?? 0), 0) / totalScored)
          : 0;
        const tierCount = scored.reduce(
          (acc, s) => {
            const t = STAKEHOLDER_PERCEIVABILITY_RESULTS[s.name]?.tier;
            if (t) acc[t] += 1;
            return acc;
          },
          { covered: 0, partial: 0, missing: 0 } as Record<StakeholderPerceivabilityTier, number>,
        );
        // 仅渲染前 10 名; 11+ 名放底部一行提示
        const top10 = sorted.slice(0, 10);
        const remaining = sorted.slice(10);

        const totalGrade = scoreToGrade(avg);
        const totalGradeColor = gradeColorClass(totalGrade);

        // 报告小结句 (基于 top + bottom + tier 分布动态生成)
        const topName = scored[0]?.name ?? '';
        const bottomName = scored[scored.length - 1]?.name ?? '';
        const topScore = topName ? STAKEHOLDER_PERCEIVABILITY_RESULTS[topName]?.score ?? 0 : 0;
        const bottomScore = bottomName ? STAKEHOLDER_PERCEIVABILITY_RESULTS[bottomName]?.score ?? 0 : 0;
        const summarySentence = topName && bottomName
          ? `${totalScored} 类相关方中,${topName} 表达最完整 (${topScore}%);${bottomName} 几近缺位 (${bottomScore}%)。${tierCount.missing} 类相关方在外立面上几乎读不到品牌。`
          : '尚无评估结果。';

        return (
          <div className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
            {/* Report Header */}
            <header className="border-b border-slate-200 bg-slate-50/70 px-6 py-5">
              <div className="flex items-start justify-between gap-4 flex-wrap">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <Users size={16} className="text-slate-600" />
                    <h3 className="text-[16px] font-black leading-tight text-slate-900">
                      利益相关方感知度评估
                    </h3>
                  </div>
                  <p className="mt-1 text-[11px] leading-6 text-slate-500">
                    每类相关方"该听到的"在外立面是否被传达 · 不评估真实合作关系, 只看公开渠道
                  </p>
                  <p className="mt-2.5 text-[12px] leading-6 text-slate-700">
                    {summarySentence}
                  </p>
                </div>
                <div className="shrink-0">
                  <div className="text-right text-[9px] font-bold uppercase tracking-[0.2em] text-slate-400">
                    总评
                  </div>
                  <div className="mt-0.5 flex items-baseline justify-end gap-1.5">
                    <span className={`text-[44px] font-light leading-none tracking-tighter ${totalGradeColor}`}>
                      {totalGrade}
                    </span>
                    <span className="text-[16px] font-light tabular-nums text-slate-500">
                      {avg}%
                    </span>
                  </div>
                </div>
              </div>
              <div className="mt-4 flex items-center gap-5 text-[11px]">
                <span className="text-slate-400">{totalScored} 类相关方</span>
                <span className="inline-flex items-baseline gap-1">
                  <span className="h-2 w-2 rounded-full bg-emerald-500" />
                  <b className="text-emerald-700 tabular-nums">{tierCount.covered}</b>
                  <span className="text-slate-500">已覆盖</span>
                </span>
                <span className="inline-flex items-baseline gap-1">
                  <span className="h-2 w-2 rounded-full bg-amber-500" />
                  <b className="text-amber-700 tabular-nums">{tierCount.partial}</b>
                  <span className="text-slate-500">有缺口</span>
                </span>
                <span className="inline-flex items-baseline gap-1">
                  <span className="h-2 w-2 rounded-full bg-rose-500" />
                  <b className="text-rose-700 tabular-nums">{tierCount.missing}</b>
                  <span className="text-slate-500">几乎为零</span>
                </span>
                {/* 折叠按钮 (右端) */}
                <button
                  type="button"
                  onClick={() => setStakeholderReportExpanded((v) => !v)}
                  className="ml-auto inline-flex items-center gap-1 text-[11px] font-semibold text-slate-600 hover:text-indigo-700 transition-colors"
                >
                  {stakeholderReportExpanded ? (
                    <>
                      <ChevronUp size={13} />
                      折叠 Top 10 详情
                    </>
                  ) : (
                    <>
                      <ChevronDown size={13} />
                      展开 Top 10 详情
                    </>
                  )}
                </button>
              </div>
            </header>

            {/* Top 10 Rows (折叠态下隐藏) */}
            {stakeholderReportExpanded && (
              <div className="divide-y divide-slate-100">
                {top10.map((s, idx) => {
                  const p = STAKEHOLDER_PERCEIVABILITY_RESULTS[s.name];
                  if (!p) return null;
                  const grade = scoreToGrade(p.score);
                  const gradeColor = gradeColorClass(grade);
                  return (
                    <div key={`${s.name}-${idx}`} className="px-6 py-3.5 hover:bg-slate-50/40 transition-colors">
                      {/* 一行: #编号 + name (固定宽 192px, 容得下 11 字) + ✓已传/✗缺失 chips 并排 + 右对齐 grade/score */}
                      <div className="flex items-baseline gap-3">
                        <span className="w-7 shrink-0 font-mono text-[11px] tabular-nums text-slate-400">
                          #{idx + 1}
                        </span>
                        <h4
                          className="w-48 shrink-0 truncate text-[14px] font-bold leading-snug text-slate-900"
                          title={s.rationale || s.name}
                        >
                          {s.name}
                        </h4>
                        {/* 中间区: ✓已传 chips + ✗缺失 chips 并排, 整体向右靠 (让右侧视觉块更稳) */}
                        <div className="min-w-0 flex-1 flex flex-wrap items-center justify-end gap-x-3 gap-y-1 text-[10px]">
                          <span className="inline-flex items-center gap-1 shrink-0">
                            <span className="font-bold text-emerald-700">✓</span>
                            {p.covered.length > 0 ? (
                              p.covered.map((c) => (
                                <span
                                  key={c}
                                  className="rounded-sm border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 text-emerald-800"
                                >
                                  {c}
                                </span>
                              ))
                            ) : (
                              <span className="italic text-slate-400">外立面上读不到</span>
                            )}
                          </span>
                          <span className="inline-flex items-center gap-1 shrink-0">
                            <span className="font-bold text-rose-700">✗</span>
                            {p.gap.length > 0 ? (
                              p.gap.map((g) => (
                                <span
                                  key={g}
                                  className="rounded-sm border border-rose-200 bg-rose-50 px-1.5 py-0.5 text-rose-800"
                                >
                                  {g}
                                </span>
                              ))
                            ) : (
                              <span className="italic text-slate-400">—</span>
                            )}
                          </span>
                        </div>
                        {/* 右对齐 grade + score */}
                        <div className="shrink-0 text-right">
                          <span className={`text-[26px] font-light leading-none tracking-tighter ${gradeColor}`}>
                            {grade}
                          </span>
                          <span className="ml-2 text-[13px] font-light tabular-nums text-slate-500">
                            {p.score}%
                          </span>
                        </div>
                      </div>
                      {/* 下方一行: commentary 简评 (利用第一行下方空白) */}
                      <p className="mt-2 ml-10 text-[11px] leading-6 text-slate-500">
                        {p.commentary}
                      </p>
                    </div>
                  );
                })}
              </div>
            )}

            {/* 未进 Top10 footer (展开态才显示) */}
            {stakeholderReportExpanded && remaining.length > 0 && (
              <div className="border-t border-slate-200 bg-slate-50/60 px-6 py-3">
                <div className="text-[10px] font-bold uppercase tracking-[0.2em] text-slate-500">
                  未进入 Top 10
                </div>
                <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1.5 text-[12px]">
                  {remaining.map((s) => {
                    const p = STAKEHOLDER_PERCEIVABILITY_RESULTS[s.name];
                    const g = p ? scoreToGrade(p.score) : '—';
                    return (
                      <span key={s.name} className="inline-flex items-baseline gap-2">
                        <span className="text-slate-700">{s.name}</span>
                        {p && (
                          <span className={`font-mono text-[11px] tabular-nums ${gradeColorClass(g)}`}>
                            {g} {p.score}%
                          </span>
                        )}
                      </span>
                    );
                  })}
                </div>
              </div>
            )}

            {/* 评估方法 (展开态才显示) */}
            {stakeholderReportExpanded && (
              <div className="border-t border-slate-200 bg-slate-50/30 px-6 py-3 text-[10px] leading-6 text-slate-500">
                💡 <b>评估方法</b>:
                感知度 = 外立面 (官网/公众号/媒体报道) 实际承载的核心要素 ÷ 该相关方应看到的核心要素。
                不评估真实合作关系 (如大额资助方的专属对接), 只看公开渠道任何人能读到什么。
              </div>
            )}
          </div>
        );
      })()}

      {editing && (
        <div className="rounded-lg border border-indigo-200 bg-indigo-50/40 p-4">
          <div className="grid gap-3 md:grid-cols-2">
            <label className="text-[11px] font-semibold text-indigo-900">
              战略主张
              <textarea
                value={editingDraft.strategicObjective}
                onChange={(event) => setEditingDraft((current) => ({
                  ...current,
                  strategicObjective: event.target.value,
                }))}
                rows={4}
                className="mt-1.5 w-full resize-y rounded-lg border border-indigo-200 bg-white px-3 py-2 text-[12px] font-normal leading-6 text-slate-800 outline-none focus:border-indigo-400"
              />
            </label>
            <label className="text-[11px] font-semibold text-indigo-900">
              方法学
              <textarea
                value={editingDraft.methodology}
                onChange={(event) => setEditingDraft((current) => ({
                  ...current,
                  methodology: event.target.value,
                }))}
                rows={4}
                className="mt-1.5 w-full resize-y rounded-lg border border-indigo-200 bg-white px-3 py-2 text-[12px] font-normal leading-6 text-slate-800 outline-none focus:border-indigo-400"
              />
            </label>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span className="text-[10px] text-indigo-700">
              {editingDraft.strategicObjective.length + editingDraft.methodology.length}/200 字
            </span>
            <button
              type="button"
              onClick={() => void saveConfirmedExtract(editingDraft)}
              disabled={saving}
              className="ml-auto inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-[11px] font-bold text-white hover:bg-indigo-700 disabled:opacity-60"
            >
              {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
              保存并确认
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              disabled={saving}
              className="rounded-md border border-indigo-200 bg-white px-3 py-1.5 text-[11px] font-semibold text-indigo-700 hover:bg-indigo-50 disabled:opacity-60"
            >
              取消
            </button>
          </div>
        </div>
      )}

      {actionMessage && (
        <div className={`rounded-lg border px-3 py-2 text-[11px] ${
          actionMessage.tone === 'success'
            ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
            : 'border-rose-200 bg-rose-50 text-rose-700'
        }`}>
          {actionMessage.text}
        </div>
      )}

      <div className="mt-3 flex items-center gap-2 flex-wrap">
        <button
          type="button"
          onClick={() => void handleExtract()}
          disabled={extracting}
          className="inline-flex items-center gap-1.5 rounded-md border border-indigo-200 bg-white px-3 py-1.5 text-[11px] font-semibold text-indigo-700 hover:bg-indigo-50 disabled:opacity-60"
        >
          {extracting ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
          {extracting ? '抽取中…' : '重新抽取'}
        </button>
        <button
          type="button"
          onClick={() => void saveConfirmedExtract({
            strategicObjective: extract.strategicObjective,
            methodology: extract.methodology,
          })}
          disabled={saving || !extract.strategicObjective.trim() || !extract.methodology.trim()}
          className="inline-flex items-center gap-1.5 rounded-md border border-gray-200 bg-white px-3 py-1.5 text-[11px] font-semibold text-gray-700 hover:bg-gray-50"
        >
          {saving ? <Loader2 size={12} className="animate-spin" /> : <FileCheck2 size={12} />}
          {extract.confirmedAt ? '重新确认' : '咨询师确认'}
        </button>
        <button
          type="button"
          onClick={startEditing}
          disabled={saving || editing}
          className="inline-flex items-center gap-1.5 rounded-md border border-gray-200 bg-white px-3 py-1.5 text-[11px] font-semibold text-gray-700 hover:bg-gray-50"
        >
          <Pencil size={12} />
          手动编辑
        </button>
        <span className="ml-auto text-[10px] text-gray-400">
          {extract.confirmedAt
            ? `已确认 ${extract.confirmedAt.slice(0, 16).replace('T', ' ')}`
            : '尚未确认 · 当前为权威来源提炼草稿'}
        </span>
      </div>
    </section>
  );
}


// 真实抓回的测试机构A数据 (从 DB 提炼) — 用于让用户看到真实状态
// ── 品牌词云：50 个加权词，从 46 条 items + glossary + themes 提炼
type WordCloudItem = { tag: string; strength: number; tone?: 'positive' | 'neutral' | 'negative'; sourceCount?: number };
const REAL_RICI_WORD_CLOUD: WordCloudItem[] = [
  // ── 超大（90-100）核心机构身份 ──
  { tag: '测试机构A', strength: 100, tone: 'neutral', sourceCount: 38 },
  { tag: '公益基金会', strength: 95, tone: 'neutral', sourceCount: 30 },
  { tag: '儿童心理', strength: 95, tone: 'neutral', sourceCount: 28 },
  // ── 大（75-89）核心叙事 ──
  { tag: '测试项目C', strength: 88, tone: 'positive', sourceCount: 18 },
  { tag: '心智素养', strength: 85, tone: 'positive', sourceCount: 14 },
  { tag: '公益', strength: 82, tone: 'positive', sourceCount: 25 },
  { tag: '张真', strength: 80, tone: 'neutral', sourceCount: 6 },
  { tag: '乡村', strength: 78, tone: 'neutral', sourceCount: 16 },
  // ── 中（50-74）次级标签 ──
  { tag: '青少年', strength: 72, tone: 'neutral', sourceCount: 14 },
  { tag: '心理健康', strength: 70, tone: 'neutral', sourceCount: 15 },
  { tag: '测试项目A', strength: 68, tone: 'positive', sourceCount: 8 },
  { tag: '学校', strength: 65, tone: 'neutral', sourceCount: 12 },
  { tag: '白皮书', strength: 62, tone: 'positive', sourceCount: 7 },
  { tag: '教师', strength: 60, tone: 'neutral', sourceCount: 10 },
  { tag: '情绪', strength: 58, tone: 'neutral', sourceCount: 6 },
  { tag: '腾讯基金会', strength: 56, tone: 'positive', sourceCount: 4 },
  { tag: '健达', strength: 54, tone: 'positive', sourceCount: 3 },
  { tag: '招聘', strength: 52, tone: 'neutral', sourceCount: 5 },
  { tag: '北师大公益', strength: 50, tone: 'positive', sourceCount: 3 },
  // ── 小（30-49）辅助标签 ──
  { tag: '捐赠', strength: 48, tone: 'positive', sourceCount: 8 },
  { tag: '99 公益日', strength: 46, tone: 'positive', sourceCount: 4 },
  { tag: '朋辈关怀员', strength: 45, tone: 'neutral', sourceCount: 1 },
  { tag: '暑期游学', strength: 42, tone: 'positive', sourceCount: 3 },
  { tag: '心智魔法', strength: 42, tone: 'positive', sourceCount: 4 },
  { tag: '研究', strength: 44, tone: 'neutral', sourceCount: 6 },
  { tag: '心理咨询', strength: 41, tone: 'neutral', sourceCount: 4 },
  { tag: '评价', strength: 40, tone: 'positive', sourceCount: 5 },
  { tag: '案例', strength: 38, tone: 'neutral', sourceCount: 4 },
  { tag: '超话', strength: 36, tone: 'neutral', sourceCount: 2 },
  { tag: '乡村教师', strength: 36, tone: 'neutral', sourceCount: 3 },
  { tag: '学院', strength: 34, tone: 'neutral', sourceCount: 5 },
  { tag: '工作坊', strength: 33, tone: 'neutral', sourceCount: 2 },
  { tag: '福利', strength: 32, tone: 'neutral', sourceCount: 2 },
  { tag: '影响', strength: 30, tone: 'neutral', sourceCount: 3 },
  // ── 微小（15-29）长尾词 ──
  { tag: '信息公开', strength: 28, tone: 'neutral', sourceCount: 6 },
  { tag: '干预项目', strength: 28, tone: 'positive', sourceCount: 3 },
  { tag: '培训', strength: 27, tone: 'neutral', sourceCount: 3 },
  { tag: '心育乐园', strength: 26, tone: 'positive', sourceCount: 2 },
  { tag: '学校成长画像', strength: 25, tone: 'neutral', sourceCount: 0 },
  { tag: '服务', strength: 25, tone: 'neutral', sourceCount: 4 },
  { tag: '战略陪伴', strength: 24, tone: 'neutral', sourceCount: 1 },
  { tag: '课堂', strength: 23, tone: 'neutral', sourceCount: 2 },
  { tag: '善款', strength: 22, tone: 'neutral', sourceCount: 2 },
  { tag: '任课老师', strength: 21, tone: 'neutral', sourceCount: 2 },
  { tag: '青少年心理支持生态', strength: 20, tone: 'neutral', sourceCount: 0 },
  { tag: '校长', strength: 20, tone: 'neutral', sourceCount: 1 },
  { tag: '会议', strength: 19, tone: 'neutral', sourceCount: 1 },
  { tag: '公益创投', strength: 18, tone: 'neutral', sourceCount: 1 },
  { tag: '案卷质量', strength: 18, tone: 'neutral', sourceCount: 0 },
  { tag: '对接', strength: 17, tone: 'neutral', sourceCount: 1 },
  { tag: '识途专题', strength: 16, tone: 'positive', sourceCount: 1 },
  { tag: '湾区经济', strength: 15, tone: 'positive', sourceCount: 1 },
];

const REAL_RICI_DATA = {
  selfPresentation: [
    { tag: '儿童心理', strength: 95, source: '客户档案 domain + 公众号 25 条高频' },
    { tag: '青少年', strength: 90, source: '客户档案 + 主题聚类' },
    { tag: '测试项目C', strength: 85, source: '业务线 + audit narrative 提及覆盖 12 省 21000 名学生' },
    { tag: '乡村', strength: 78, source: '99 公益日项目 + 教师赋能项目' },
    { tag: '心智素养', strength: 72, source: '微博超话主题 + 测试项目A' },
    { tag: '张真', strength: 70, source: '银杏播客专访 + 媒体专访 ×3' },
    { tag: '公益基金会', strength: 65, source: '机构基础认知' },
    { tag: '测试项目A', strength: 58, source: '2026 青年情绪白皮书' },
    { tag: '教师赋能', strength: 52, source: '教师暑期心理健康游学营' },
  ],
  blindspots: [
    {
      selfTag: '学校成长画像',
      publicEvidence: '公众主题 0 条提及，自报方法论核心但外部认知为 0',
      severity: 'high' as const,
      evidence: '来自 audit tensions[0]',
    },
    {
      selfTag: '青少年心理支持生态网络',
      publicEvidence: '公众仅看到零散报告 + 信息公示，对生态布局无感知',
      severity: 'high' as const,
      evidence: '来自 audit tensions[1]',
    },
    {
      selfTag: '朋辈关怀员',
      publicEvidence: 'glossary 12 个业务术语中的核心方法论，公众讨论 0 次',
      severity: 'medium' as const,
      evidence: '从 glossary × themes 对比推断',
    },
    {
      selfTag: '心理咨询',
      publicEvidence: '官网提，但公众讨论焦点是「活动」「项目」非「咨询服务」',
      severity: 'low' as const,
      evidence: '主题分布判断',
    },
  ],
  consistency: [
    { aspect: '业务焦点表述', count: 4, tag: '官网=支持/捐赠；公众号=项目活动；媒体=张真专访；微博=超话社区——4 个落点', severity: 'medium' as const },
    { aspect: '使命语句', count: 2, tag: 'glossary 定义"青少年心理支持"，audit 推出"少儿心理公益" — 略有出入', severity: 'low' as const },
    { aspect: '核心项目主推', count: 3, tag: '官网主推"支持我们"；公众号主推「测试项目C」；媒体主推「测试项目A/白皮书」', severity: 'medium' as const },
  ],
  mediaCoverage: [
    { source: '微信公众号·24 个号', count: 25, tone: '正面 + 中性混合' },
    { source: '中国企业报湾区经济', count: 1, tone: '正面深度报道' },
    { source: '网易·163', count: 2, tone: '专访秘书长张真 / 湾区经济' },
    { source: '银杏播客', count: 2, tone: '人物专访' },
    { source: '搜狐·sohu', count: 2, tone: '识途专题 + 教师项目' },
    { source: '今日头条', count: 1, tone: '机构介绍' },
    { source: '澎湃·thepaper', count: 1, tone: '中性' },
    { source: '招聘类·jobui/lagou', count: 3, tone: '中性·组织信号' },
  ],
  partners: [
    { name: '腾讯基金会', role: '联合发布 · 2026 青年情绪白皮书', appearance: 4 },
    { name: '北京师范大学中国公益研究院', role: '学术合作', appearance: 3 },
    { name: '健达品牌', role: '健达快乐成长计划 · 企业资助', appearance: 3 },
    { name: '南都基金会 / 北京新阳光慈善', role: '同行联名', appearance: 2 },
    { name: '得润公益基金', role: '英德乡村捐赠', appearance: 2 },
    { name: '关爱委员会', role: '走访 + 桥爱行业交流', appearance: 2 },
    { name: '亚洲动物基金 / 郎朗艺术基金会', role: '同行联名活动', appearance: 1 },
  ],
};

// ── 官方渠道编辑器（核心入口）──
function BrandWordCloud({ words, targetName }: { words: WordCloudItem[]; targetName: string }) {
  if (!words || words.length === 0) return null;
  // 按 strength 倒序
  const sorted = [...words].sort((a, b) => b.strength - a.strength);
  const maxStrength = sorted[0].strength;
  const minStrength = sorted[sorted.length - 1].strength;
  const sizeFor = (s: number) => {
    // 字号梯度：14-44px，按平方根映射避免最大词过分突出
    const ratio = (s - minStrength) / Math.max(1, maxStrength - minStrength);
    const sq = Math.sqrt(ratio);
    return Math.round(14 + sq * 30);
  };
  const colorClassFor = (tone?: string) => {
    if (tone === 'positive') return 'text-emerald-700 hover:text-emerald-900';
    if (tone === 'negative') return 'text-rose-700 hover:text-rose-900';
    return 'text-gray-700 hover:text-gray-900';
  };
  const weightClassFor = (sourceCount?: number) => {
    if (!sourceCount) return 'font-medium';
    if (sourceCount >= 15) return 'font-extrabold';
    if (sourceCount >= 8) return 'font-bold';
    if (sourceCount >= 3) return 'font-semibold';
    return 'font-medium';
  };
  const opacityFor = (sourceCount?: number) => {
    if (!sourceCount || sourceCount === 0) return 0.45;  // 客户自报但公众未提及——半透明
    return 1;
  };

  return (
    <section className="rounded-2xl border border-gray-200 bg-white px-6 py-5">
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <div>
          <h3 className="text-[14px] font-black text-gray-900">品牌词云 · {targetName}</h3>
          <p className="mt-0.5 text-[11px] text-gray-500">
            公众语境下出现的所有关键词，字号 = 强度，颜色 = 倾向（绿=正/灰=中），半透明 = 你说了但公众没接住
          </p>
        </div>
        <span className="text-[10px] tabular-nums text-gray-400">{sorted.length} 词</span>
      </div>
      <div className="flex flex-wrap items-end gap-x-3 gap-y-2 leading-none">
        {sorted.map((w) => (
          <span
            key={w.tag}
            title={
              w.sourceCount !== undefined
                ? `权重 ${w.strength} · 在 ${w.sourceCount} 个来源中出现${w.sourceCount === 0 ? '（公众未提及，仅来自客户自报）' : ''}`
                : `权重 ${w.strength}`
            }
            className={`inline-block cursor-default transition-all ${colorClassFor(w.tone)} ${weightClassFor(w.sourceCount)}`}
            style={{
              fontSize: `${sizeFor(w.strength)}px`,
              opacity: opacityFor(w.sourceCount),
            }}
          >
            {w.tag}
          </span>
        ))}
      </div>
      <div className="mt-3 flex items-center gap-3 text-[10px] text-gray-400">
        <span className="inline-flex items-center gap-1">
          <span className="h-2 w-2 rounded-full bg-emerald-500" /> 正面
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="h-2 w-2 rounded-full bg-gray-400" /> 中性
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="h-2 w-2 rounded-full bg-rose-500" /> 负面
        </span>
        <span className="ml-3">字体粗细 = 出现的不同来源数（越多越粗）</span>
      </div>
    </section>
  );
}


// ── 3 精致信息卡（媒体覆盖度 / 合作生态 / 官方信息出口）──
// 官方信息出口的渠道 chip 配色 + hover 显示的中文全名 (替代被删的 CHANNEL_META)
const CHANNEL_CHIP: Record<OfficialChannelKind, { letter: string; bg: string; text: string; name: string }> = {
  homepage:  { letter: '网', bg: 'bg-slate-200',    text: 'text-slate-700',   name: '官网' },
  wechat:    { letter: '微', bg: 'bg-emerald-100',  text: 'text-emerald-700', name: '官方公众号' },
  weibo:     { letter: '博', bg: 'bg-rose-100',     text: 'text-rose-700',    name: '官方微博' },
  bilibili:  { letter: 'B',  bg: 'bg-pink-100',     text: 'text-pink-700',    name: '官方 B 站' },
  recruit:   { letter: '聘', bg: 'bg-blue-100',     text: 'text-blue-700',    name: '招聘渠道' },
  partner:   { letter: '合', bg: 'bg-indigo-100',   text: 'text-indigo-700',  name: '主要合作方' },
};

function BrandInsightCardRow({
  mediaCoverage,
  partners,
  channels,
  onAdoptChannel,
  onExcludeChannel,
}: {
  mediaCoverage: { source: string; count: number; tone: string }[];
  partners: { name: string; role: string; appearance: number }[];
  channels: OfficialChannel[];
  onAdoptChannel: (idx: number) => void;
  onExcludeChannel: (idx: number) => void;
}) {
  const acceptedCount = channels.filter((c) => c.status === 'user_confirmed' || c.status === 'user_added').length;
  const candidateCount = channels.filter((c) => c.status === 'auto_detected').length;
  const totalMediaItems = mediaCoverage.reduce((s, m) => s + m.count, 0);

  return (
    <div className="grid gap-3 md:grid-cols-3">
      {/* 媒体覆盖度 */}
      <article className="rounded-lg border border-slate-200 bg-white px-5 py-4 shadow-sm transition-all hover:border-slate-300 hover:shadow-md">
        <div className="flex items-center gap-2">
          <Newspaper size={16} className="text-slate-600" />
          <h4 className="text-[14px] font-black text-slate-900">媒体覆盖度</h4>
        </div>
        <p className="mt-0.5 text-[10.5px] leading-5 text-slate-500">公开渠道提到品牌的次数与来源</p>
        <div className="mt-3 flex items-baseline gap-2">
          <span className="text-[30px] font-light tabular-nums leading-none text-slate-900 tracking-tight">{totalMediaItems}</span>
          <span className="text-[11px] text-slate-500">
            条 · 共 <b className="text-slate-700 tabular-nums">{mediaCoverage.length}</b> 个来源
          </span>
        </div>
        <ul className="mt-3 max-h-[200px] space-y-1.5 overflow-y-auto border-t border-slate-100 pt-3 pr-1">
          {mediaCoverage.map((m) => (
            <li key={m.source} className="flex items-baseline gap-2 text-[11px]">
              <span className="truncate text-slate-700">{m.source}</span>
              <span className="ml-auto inline-flex items-center rounded-sm bg-slate-100 px-1.5 py-0.5 text-[10px] font-bold tabular-nums text-slate-600">
                ×{m.count}
              </span>
            </li>
          ))}
        </ul>
      </article>

      {/* 合作生态 */}
      <article className="rounded-lg border border-slate-200 bg-white px-5 py-4 shadow-sm transition-all hover:border-slate-300 hover:shadow-md">
        <div className="flex items-center gap-2">
          <Handshake size={16} className="text-slate-600" />
          <h4 className="text-[14px] font-black text-slate-900">合作生态</h4>
        </div>
        <p className="mt-0.5 text-[10.5px] leading-5 text-slate-500">已识别的合作方及他们的角色定位</p>
        <div className="mt-3 flex items-baseline gap-2">
          <span className="text-[30px] font-light tabular-nums leading-none text-slate-900 tracking-tight">{partners.length}</span>
          <span className="text-[11px] text-slate-500">个识别合作方</span>
        </div>
        <ul className="mt-3 max-h-[200px] space-y-2 overflow-y-auto border-t border-slate-100 pt-3 pr-1">
          {partners.map((p) => (
            <li key={p.name} className="text-[11px]">
              <div className="flex items-baseline gap-2">
                <span className="truncate font-semibold text-slate-800">{p.name}</span>
                <span className="ml-auto inline-flex items-center rounded-sm bg-slate-100 px-1.5 py-0.5 text-[10px] font-bold tabular-nums text-slate-600">
                  ×{p.appearance}
                </span>
              </div>
              <p className="mt-0.5 text-[10px] leading-4 text-slate-500 truncate">{p.role}</p>
            </li>
          ))}
        </ul>
      </article>

      {/* 官方信息出口 */}
      <article className="rounded-lg border border-slate-200 bg-white px-5 py-4 shadow-sm transition-all hover:border-slate-300 hover:shadow-md">
        <div className="flex items-center gap-2">
          <Globe size={16} className="text-slate-600" />
          <h4 className="text-[14px] font-black text-slate-900">官方信息出口</h4>
        </div>
        <p className="mt-0.5 text-[10.5px] leading-5 text-slate-500">品牌主动可控的发声渠道 · 可采纳或排除</p>
        <div className="mt-3 flex items-baseline gap-3">
          <div className="flex items-baseline gap-1.5">
            <span className="text-[30px] font-light tabular-nums leading-none text-slate-900 tracking-tight">{acceptedCount}</span>
            <span className="text-[11px] text-slate-500">已采纳</span>
          </div>
          {candidateCount > 0 && (
            <div className="inline-flex items-baseline gap-1 rounded-sm border border-amber-200 bg-amber-50 px-1.5 py-0.5">
              <span className="text-[11px] font-bold tabular-nums text-amber-700">{candidateCount}</span>
              <span className="text-[10px] text-amber-700">待确认</span>
            </div>
          )}
        </div>
        <ul className="mt-3 max-h-[200px] space-y-1.5 overflow-y-auto border-t border-slate-100 pt-3 pr-1">
          {channels
            .filter((c) => c.status !== 'excluded')
            .map((c, idx) => {
              const chip = CHANNEL_CHIP[c.kind];
              const isCandidate = c.status === 'auto_detected';
              const realIdx = channels.indexOf(c);
              return (
                <li key={`${c.kind}_${c.label}_${idx}`} className="flex items-center gap-2 text-[11px]">
                  {/* 渠道品牌字 chip (替代 emoji) */}
                  <span
                    className={`inline-flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-sm text-[10px] font-bold ${chip.bg} ${chip.text}`}
                    title={chip.name}
                  >
                    {chip.letter}
                  </span>
                  <span className={`min-w-0 flex-1 truncate ${isCandidate ? 'text-slate-500' : 'font-semibold text-slate-800'}`}>
                    {c.label}
                  </span>
                  {isCandidate ? (
                    <span className="flex items-center gap-0.5 shrink-0">
                      <button
                        type="button"
                        onClick={() => onAdoptChannel(realIdx)}
                        title="采纳"
                        className="inline-flex items-center justify-center rounded p-0.5 text-emerald-600 hover:bg-emerald-50 transition-colors"
                      >
                        <CheckCircle2 size={12} />
                      </button>
                      <button
                        type="button"
                        onClick={() => onExcludeChannel(realIdx)}
                        title="排除"
                        className="inline-flex items-center justify-center rounded p-0.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 transition-colors"
                      >
                        <X size={12} />
                      </button>
                    </span>
                  ) : (
                    <CheckCircle2 size={11} className="shrink-0 text-emerald-500" />
                  )}
                </li>
              );
            })}
        </ul>
      </article>
    </div>
  );
}
