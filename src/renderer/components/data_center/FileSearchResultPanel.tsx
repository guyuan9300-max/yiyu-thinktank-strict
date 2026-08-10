import React, { useMemo, useState } from 'react';

import type { DataCenterSearchHit } from '../../../shared/types';
import {
  buildFileSearchBriefAnswer,
  buildFileSearchDisplayGroups,
  type FileSearchDisplayGroup,
} from '../../../shared/workspaceFileSearchPresentation';

type FileSearchResultPanelProps = {
  searchResult?: { hits?: DataCenterSearchHit[]; selectedHits?: DataCenterSearchHit[] } | null;
  onOpenOriginal?: (hit: DataCenterSearchHit) => void;
  onMarkUseful?: (hit: DataCenterSearchHit) => void;
  onMarkNoise?: (hit: DataCenterSearchHit) => void;
  onMarkNeedsReview?: (hit: DataCenterSearchHit) => void;
};

const DEFAULT_ORIGINAL_GROUPS = 6;
const DEFAULT_SYSTEM_GROUPS = 4;

function supportClass(level: FileSearchDisplayGroup['supportLevel']): string {
  if (level === 'strong') return 'bg-sky-50 text-sky-700 border-sky-100';
  if (level === 'reference') return 'bg-slate-100 text-slate-600 border-slate-200';
  return 'bg-gray-50 text-gray-500 border-gray-100';
}

interface FreshnessDisplay {
  ageLabel: string;
  scoreLabel: string;
  toneClass: string;
  title: string;
}

function formatAgeLabel(createdAt: string): string | null {
  const parsed = new Date(createdAt);
  if (Number.isNaN(parsed.getTime())) return null;
  const now = Date.now();
  const diffMs = now - parsed.getTime();
  if (diffMs < 0) return '刚刚';
  const day = 24 * 60 * 60 * 1000;
  const days = diffMs / day;
  if (days < 1) return '今天';
  if (days < 7) return `${Math.floor(days)} 天前`;
  if (days < 30) return `${Math.floor(days / 7)} 周前`;
  if (days < 365) return `${Math.floor(days / 30)} 个月前`;
  const years = days / 365;
  if (years < 10) {
    return `${years.toFixed(1)} 年前`;
  }
  return `${Math.floor(years)} 年前`;
}

function freshnessTone(score: number): { toneClass: string; descriptor: string } {
  if (score >= 0.8) return { toneClass: 'border-emerald-100 bg-emerald-50 text-emerald-700', descriptor: '新鲜' };
  if (score >= 0.5) return { toneClass: 'border-sky-100 bg-sky-50 text-sky-700', descriptor: '尚新' };
  if (score >= 0.25) return { toneClass: 'border-amber-100 bg-amber-50 text-amber-700', descriptor: '已衰减' };
  return { toneClass: 'border-rose-100 bg-rose-50 text-rose-700', descriptor: '过期' };
}

function describeDocType(docType: string | null | undefined): string {
  if (!docType) return '默认半衰期 90 天';
  const map: Record<string, string> = {
    news: '新闻类 · 半衰期 30 天',
    client_judgment: '客户判断 · 半衰期 90 天',
    meeting_minutes: '会议纪要 · 半衰期 90 天',
    meeting_note: '会议笔记 · 半衰期 90 天',
    meeting_decision: '会议决议 · 半衰期 90 天',
    evidence_artifact: '证据材料 · 半衰期 180 天',
    strategy_doc: '战略文档 · 半衰期 180 天',
    policy_doc: '政策文档 · 半衰期 365 天',
    background: '背景资料 · 不衰减',
    default: '默认半衰期 90 天',
  };
  return map[docType] ?? `类型 ${docType}`;
}

function buildFreshnessDisplay(hit: DataCenterSearchHit): FreshnessDisplay | null {
  const score = typeof hit.freshnessScore === 'number' ? hit.freshnessScore : null;
  const ageLabel = hit.createdAt ? formatAgeLabel(hit.createdAt) : null;
  if (score === null && !ageLabel) return null;
  const safeScore = score === null ? 0.5 : score;
  const { toneClass, descriptor } = freshnessTone(safeScore);
  const scoreLabel = score === null ? '时间未知' : `鲜度 ${Math.round(safeScore * 100)}%`;
  const title = `${descriptor} · ${describeDocType(hit.docType)}${ageLabel ? ` · ${ageLabel}` : ''}`;
  return {
    ageLabel: ageLabel ?? '时间未知',
    scoreLabel,
    toneClass,
    title,
  };
}

function FreshnessBadge({ display }: { display: FreshnessDisplay }) {
  return (
    <span
      className={`rounded-full border px-2 py-0.5 text-[10px] font-bold ${display.toneClass}`}
      title={display.title}
    >
      {display.ageLabel} · {display.scoreLabel}
    </span>
  );
}

interface SemanticMeta {
  label: string;
  tone: string;
  icon: string;
}

const SEMANTIC_META: Record<string, SemanticMeta> = {
  fact: { label: '事实', tone: 'border-emerald-100 bg-emerald-50 text-emerald-700', icon: '✓' },
  judgment: { label: '判断', tone: 'border-violet-100 bg-violet-50 text-violet-700', icon: '💭' },
  opinion: { label: '观点', tone: 'border-slate-100 bg-slate-50 text-slate-600', icon: '💡' },
  action: { label: '行动', tone: 'border-sky-100 bg-sky-50 text-sky-700', icon: '→' },
  question: { label: '提问', tone: 'border-amber-100 bg-amber-50 text-amber-700', icon: '?' },
  conclusion: { label: '结论', tone: 'border-rose-100 bg-rose-50 text-rose-700', icon: '⊕' },
  background: { label: '背景', tone: 'border-gray-100 bg-gray-50 text-gray-500', icon: 'i' },
};

function SemanticBadge({ hit }: { hit: DataCenterSearchHit }) {
  const semanticType = hit.semanticType;
  if (!semanticType || semanticType === 'unclassified') return null;
  const meta = SEMANTIC_META[semanticType];
  if (!meta) return null;
  const conf = typeof hit.semanticConfidence === 'number' ? Math.round(hit.semanticConfidence * 100) : null;
  return (
    <span
      className={`rounded-full border px-2 py-0.5 text-[10px] font-bold ${meta.tone}`}
      title={conf !== null ? `${meta.label}（置信度 ${conf}%）` : meta.label}
    >
      {meta.icon} {meta.label}
    </span>
  );
}

function VersionBadge({ hit }: { hit: DataCenterSearchHit }) {
  const versionNumber = typeof hit.versionNumber === 'number' ? hit.versionNumber : null;
  const chainTotal = typeof hit.chainTotalVersions === 'number' ? hit.chainTotalVersions : 1;
  const status = hit.lifecycleStatus || 'current';
  if (status === 'superseded') {
    return (
      <span
        className="rounded-full border border-rose-100 bg-rose-50 px-2 py-0.5 text-[10px] font-bold text-rose-700"
        title={`此版本已被新版本取代，仅供历史追溯`}
      >
        已被取代
      </span>
    );
  }
  if (versionNumber && versionNumber > 1) {
    const tail = chainTotal > 1 ? ` · 共 ${chainTotal} 版` : '';
    return (
      <span
        className="rounded-full border border-violet-100 bg-violet-50 px-2 py-0.5 text-[10px] font-bold text-violet-700"
        title={`这份资料的第 ${versionNumber} 版${tail}（旧版已自动归档，不进检索）`}
      >
        v{versionNumber} · 最新{tail}
      </span>
    );
  }
  return null;
}

function titleForHit(hit: DataCenterSearchHit): string {
  return hit.title || '未命名资料';
}

function sourceLineForHit(hit: DataCenterSearchHit): string {
  const parts = [
    hit.sectionLabel ? `位置：${hit.sectionLabel}` : null,
    hit.openableKind === 'system_card' ? '系统线索' : null,
    hit.openableKind === 'machine_markdown' ? '机读稿' : null,
    hit.retrievalStage ? String(hit.retrievalStage) : null,
  ].filter(Boolean);
  return parts.join(' · ');
}

function openLabelForHit(hit: DataCenterSearchHit, layer: FileSearchDisplayGroup['layer']): string {
  if (hit.sourceAvailability === 'machine_readable_only' || hit.openableKind === 'machine_markdown') return '打开机读稿';
  if (hit.openableKind === 'system_card') return '打开系统卡片';
  return layer === 'original' ? '打开原文' : '打开系统卡片';
}

function SearchGroupCard({
  group,
  index,
  expanded,
  onToggleExpanded,
  onOpenOriginal,
  onMarkUseful,
  onMarkNoise,
  onMarkNeedsReview,
}: {
  group: FileSearchDisplayGroup;
  index: number;
  expanded: boolean;
  onToggleExpanded: (groupId: string) => void;
  onOpenOriginal?: (hit: DataCenterSearchHit) => void;
  onMarkUseful?: (hit: DataCenterSearchHit) => void;
  onMarkNoise?: (hit: DataCenterSearchHit) => void;
  onMarkNeedsReview?: (hit: DataCenterSearchHit) => void;
}) {
  const hit = group.primaryHit;
  const relatedCount = Math.max(0, group.hits.length - 1);
  const machineReadableFallback = Boolean(hit.machineReadableAvailable || hit.markdownPath || hit.openableKind === 'machine_markdown');
  const openDisabled = Boolean(hit.openOriginalDisabledReason && !machineReadableFallback);
  const openLabel = openLabelForHit(hit, group.layer);
  const freshnessDisplay = buildFreshnessDisplay(hit);
  return (
    <div className="rounded-2xl border border-slate-100 bg-slate-50 px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-white px-2 py-0.5 text-[10px] font-black text-sky-700">{index + 1}</span>
            <span className={`rounded-full border px-2 py-0.5 text-[10px] font-black ${supportClass(group.supportLevel)}`}>
              {group.supportLabel}
            </span>
            <span className="rounded-full border border-emerald-100 bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-700">
              {group.layer === 'original' ? '原始文件' : '系统线索'}
            </span>
            {group.sourceAvailabilityLabel && (
              <span className="rounded-full border border-amber-100 bg-amber-50 px-2 py-0.5 text-[10px] font-bold text-amber-700">
                {group.sourceAvailabilityLabel}
              </span>
            )}
            {freshnessDisplay && <FreshnessBadge display={freshnessDisplay} />}
            <VersionBadge hit={hit} />
            <SemanticBadge hit={hit} />
          </div>
          <p className="mt-2 text-[13px] font-bold leading-snug text-slate-900 line-clamp-2">{titleForHit(hit)}</p>
          {sourceLineForHit(hit) && <p className="mt-1 text-[11px] font-semibold text-slate-400">{sourceLineForHit(hit)}</p>}
          {hit.excerpt && <p className="mt-2 line-clamp-4 text-[12px] leading-5 text-slate-600">{hit.excerpt}</p>}
        </div>
        {onOpenOriginal && (
          <button
            type="button"
            className={`shrink-0 rounded-xl px-3 py-2 text-[11px] font-bold transition ${
              openDisabled
                ? 'cursor-not-allowed bg-slate-100 text-slate-400'
                : 'bg-white text-[#5B7BFE] hover:bg-blue-50'
            }`}
            onClick={() => {
              if (!openDisabled) onOpenOriginal(hit);
            }}
            title={openDisabled ? hit.openOriginalDisabledReason || group.sourceAvailabilityLabel || '原文暂不可打开' : openLabel}
          >
            {openLabel}
          </button>
        )}
      </div>

      {relatedCount > 0 && (
        <button
          type="button"
          className="mt-2 text-[11px] font-bold text-slate-400 hover:text-[#5B7BFE]"
          onClick={() => onToggleExpanded(group.id)}
        >
          {expanded ? '收起相关片段' : `还有 ${relatedCount} 个相关片段`}
        </button>
      )}
      {expanded && relatedCount > 0 && (
        <div className="mt-2 space-y-2">
          {group.hits.slice(1).map((item, itemIndex) => (
            <div key={`${group.id}-snippet-${itemIndex}`} className="rounded-xl border border-white bg-white/80 px-3 py-2">
              {sourceLineForHit(item) && <p className="text-[10px] font-bold text-slate-400">{sourceLineForHit(item)}</p>}
              <p className="mt-1 line-clamp-3 text-[11px] leading-5 text-slate-600">{item.excerpt || item.title || '暂无片段摘要'}</p>
            </div>
          ))}
        </div>
      )}

      {hit.annotationId && (
        <div className="mt-3 flex flex-wrap gap-3">
          {onMarkUseful && <button type="button" className="text-[11px] font-bold text-emerald-600" onClick={() => onMarkUseful(hit)}>有用</button>}
          {onMarkNeedsReview && <button type="button" className="text-[11px] font-bold text-amber-600" onClick={() => onMarkNeedsReview(hit)}>复核</button>}
          {onMarkNoise && <button type="button" className="text-[11px] font-bold text-slate-400" onClick={() => onMarkNoise(hit)}>噪声</button>}
        </div>
      )}
    </div>
  );
}

export function FileSearchResultPanel({
  searchResult,
  onOpenOriginal,
  onMarkUseful,
  onMarkNoise,
  onMarkNeedsReview,
}: FileSearchResultPanelProps) {
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});
  const [showAllResults, setShowAllResults] = useState(false);
  const displayGroups = useMemo(() => buildFileSearchDisplayGroups(searchResult), [searchResult]);
  const originalGroups = showAllResults
    ? displayGroups.originalGroups
    : displayGroups.originalGroups.slice(0, DEFAULT_ORIGINAL_GROUPS);
  const systemGroups = showAllResults
    ? displayGroups.systemGroups
    : displayGroups.systemGroups.slice(0, DEFAULT_SYSTEM_GROUPS);
  const hiddenCount = Math.max(0, displayGroups.originalGroups.length - originalGroups.length)
    + Math.max(0, displayGroups.systemGroups.length - systemGroups.length);

  const toggleExpanded = (groupId: string) => {
    setExpandedGroups((previous) => ({ ...previous, [groupId]: !previous[groupId] }));
  };

  if (displayGroups.originalGroups.length === 0 && displayGroups.systemGroups.length === 0) {
    const emptyBriefAnswer = buildFileSearchBriefAnswer(displayGroups);
    return (
      <div className="space-y-3 rounded-3xl border border-slate-100 bg-white p-4">
        <div className="rounded-2xl border border-sky-100 bg-sky-50/70 px-4 py-3">
          <p className="text-[12px] font-black text-sky-700">{emptyBriefAnswer.title}</p>
          {emptyBriefAnswer.lines.map((line, index) => (
            <p key={`empty-file-search-brief-${index}`} className="mt-1 text-[12px] font-semibold leading-5 text-slate-600">{line}</p>
          ))}
        </div>
        <div className="rounded-2xl border border-slate-100 bg-slate-50 px-4 py-3 text-[12px] text-slate-500">
          当前没有可展示的文件检索结果。
        </div>
      </div>
    );
  }

  const briefAnswer = buildFileSearchBriefAnswer(displayGroups);

  return (
    <div className="space-y-4 rounded-3xl border border-slate-100 bg-white p-4">
      <div className="rounded-2xl border border-sky-100 bg-sky-50/70 px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[12px] font-black text-sky-700">{briefAnswer.title}</p>
          <span className="rounded-full bg-white px-2 py-0.5 text-[10px] font-black text-sky-600">文件优先级</span>
        </div>
        {briefAnswer.lines.map((line, index) => (
          <p key={`file-search-brief-${index}`} className="mt-1.5 text-[12px] font-semibold leading-5 text-slate-700">{line}</p>
        ))}
        <p className="mt-2 text-[11px] font-semibold leading-5 text-slate-400">{briefAnswer.note}</p>
      </div>

      <div>
        <p className="text-[12px] font-bold text-slate-600">文件检索结果</p>
        {displayGroups.hiddenInvalidCount > 0 && (
          <p className="mt-1 text-[11px] font-semibold text-amber-600">
            已隐藏 {displayGroups.hiddenInvalidCount} 条无效资料线索。
          </p>
        )}
      </div>

      {originalGroups.length > 0 && (
        <div className="space-y-2">
          <p className="text-[11px] font-black text-slate-400">原始文件</p>
          {originalGroups.map((group, index) => (
            <SearchGroupCard
              key={group.id}
              group={group}
              index={index}
              expanded={Boolean(expandedGroups[group.id])}
              onToggleExpanded={toggleExpanded}
              onOpenOriginal={onOpenOriginal}
              onMarkUseful={onMarkUseful}
              onMarkNoise={onMarkNoise}
              onMarkNeedsReview={onMarkNeedsReview}
            />
          ))}
        </div>
      )}

      {systemGroups.length > 0 && (
        <div className="space-y-2">
          <div>
            <p className="text-[11px] font-black text-slate-400">系统整理线索</p>
            <p className="mt-0.5 text-[10px] font-semibold text-slate-400">不是原始上传文件，仅作为补充定位。</p>
          </div>
          {systemGroups.map((group, index) => (
            <SearchGroupCard
              key={group.id}
              group={group}
              index={originalGroups.length + index}
              expanded={Boolean(expandedGroups[group.id])}
              onToggleExpanded={toggleExpanded}
              onOpenOriginal={onOpenOriginal}
              onMarkUseful={onMarkUseful}
              onMarkNoise={onMarkNoise}
              onMarkNeedsReview={onMarkNeedsReview}
            />
          ))}
        </div>
      )}

      {hiddenCount > 0 && (
        <button
          type="button"
          className="w-full rounded-2xl border border-dashed border-slate-200 bg-slate-50/70 px-3 py-2.5 text-[11px] font-bold text-slate-500 transition hover:border-[#C7D5FF] hover:bg-blue-50 hover:text-[#4A63CF]"
          onClick={() => setShowAllResults((value) => !value)}
        >
          {showAllResults ? '收起更多结果' : `展开更多 ${hiddenCount} 条结果`}
        </button>
      )}
    </div>
  );
}
