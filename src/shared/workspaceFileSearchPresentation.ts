import type { DataCenterSearchHit } from './types.js';

export type FileSearchSupportLevel = 'strong' | 'reference' | 'background';
export type FileSearchDisplayLayer = 'original' | 'system';

export interface FileSearchDisplayGroup {
  id: string;
  layer: FileSearchDisplayLayer;
  primaryHit: DataCenterSearchHit;
  hits: DataCenterSearchHit[];
  supportLevel: FileSearchSupportLevel;
  supportLabel: string;
  sourceAvailabilityLabel?: string | null;
}

export interface FileSearchDisplayGroups {
  originalGroups: FileSearchDisplayGroup[];
  systemGroups: FileSearchDisplayGroup[];
  hiddenInvalidCount: number;
}

export interface FileSearchBriefAnswer {
  title: string;
  lines: string[];
  note: string;
}

const SUPPORT_LABELS: Record<FileSearchSupportLevel, string> = {
  strong: '强相关',
  reference: '可参考',
  background: '背景线索',
};

function cleanText(value: unknown): string {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function compactText(value: unknown, maxChars = 34): string {
  const text = cleanText(value);
  if (text.length <= maxChars) return text;
  return `${text.slice(0, maxChars - 1)}…`;
}

function stableKey(value: unknown): string {
  return cleanText(value).toLowerCase() || 'unknown';
}

function isMarkdownPath(value: unknown): boolean {
  return cleanText(value).toLowerCase().endsWith('.md');
}

function firstPath(...values: Array<unknown>): string {
  for (const value of values) {
    const path = cleanText(value);
    if (path) return path;
  }
  return '';
}

function firstNonMarkdownPath(...values: Array<unknown>): string {
  for (const value of values) {
    const path = cleanText(value);
    if (path && !isMarkdownPath(path)) return path;
  }
  return '';
}

function excerptLength(hit: DataCenterSearchHit): number {
  return cleanText(hit.excerpt).length;
}

function scoreValue(hit: DataCenterSearchHit): number {
  return typeof hit.score === 'number' && Number.isFinite(hit.score) ? hit.score : -1;
}

export function normalizeSearchSupportLevel(score?: number | null): FileSearchSupportLevel {
  if (typeof score !== 'number' || Number.isNaN(score)) return 'background';
  if (score >= 3) return 'strong';
  if (score >= 1) return 'reference';
  return 'background';
}

export function isOriginalFileSearchHit(hit: DataCenterSearchHit): boolean {
  if (hit.openableKind !== 'original_file') return false;
  if (hit.sourceAvailability !== 'original_available') return false;
  if (hit.originalAvailable !== true) return false;
  return Boolean(firstNonMarkdownPath(hit.originalPath, hit.managedPath, hit.path));
}

function sourceAvailabilityLabel(hit: DataCenterSearchHit): string | null {
  if (hit.sourceAvailability === 'machine_readable_only') return '原文缺失，仅有机读稿';
  if (hit.sourceAvailability === 'invalid_source') return '资料无效，已隐藏';
  if (hit.originalAvailable === false && hit.machineReadableAvailable) return '原文缺失，仅有机读稿';
  return null;
}

export function buildSearchGroupKey(hit: DataCenterSearchHit): string {
  const sectionKey = stableKey(hit.sectionLabel);
  if (cleanText(hit.documentId)) return `document:${stableKey(hit.documentId)}:${sectionKey}`;
  const path = firstPath(hit.originalPath, hit.path, hit.managedPath, hit.markdownPath);
  if (path) return `path:${stableKey(path)}:${sectionKey}`;
  return `title:${stableKey(hit.title)}:${sectionKey}`;
}

export function pickPrimarySearchHit(hits: DataCenterSearchHit[]): DataCenterSearchHit {
  return [...hits].sort((left, right) => {
    const leftOriginal = isOriginalFileSearchHit(left) ? 1 : 0;
    const rightOriginal = isOriginalFileSearchHit(right) ? 1 : 0;
    if (leftOriginal !== rightOriginal) return rightOriginal - leftOriginal;
    const leftSelected = left.selectedForAnswer ? 1 : 0;
    const rightSelected = right.selectedForAnswer ? 1 : 0;
    if (leftSelected !== rightSelected) return rightSelected - leftSelected;
    const scoreDiff = scoreValue(right) - scoreValue(left);
    if (scoreDiff !== 0) return scoreDiff;
    const excerptDiff = excerptLength(right) - excerptLength(left);
    if (excerptDiff !== 0) return excerptDiff;
    return cleanText(left.title).localeCompare(cleanText(right.title), 'zh-Hans-CN');
  })[0];
}

function compareGroups(left: FileSearchDisplayGroup, right: FileSearchDisplayGroup): number {
  const leftOriginal = left.layer === 'original' ? 1 : 0;
  const rightOriginal = right.layer === 'original' ? 1 : 0;
  if (leftOriginal !== rightOriginal) return rightOriginal - leftOriginal;
  const supportRank: Record<FileSearchSupportLevel, number> = { strong: 0, reference: 1, background: 2 };
  const supportDiff = supportRank[left.supportLevel] - supportRank[right.supportLevel];
  if (supportDiff !== 0) return supportDiff;
  const scoreDiff = scoreValue(right.primaryHit) - scoreValue(left.primaryHit);
  if (scoreDiff !== 0) return scoreDiff;
  return cleanText(left.primaryHit.title).localeCompare(cleanText(right.primaryHit.title), 'zh-Hans-CN');
}

export function buildFileSearchDisplayGroups(searchResult?: { hits?: DataCenterSearchHit[]; selectedHits?: DataCenterSearchHit[] } | null): FileSearchDisplayGroups {
  const rawHits = (searchResult?.selectedHits?.length ? searchResult.selectedHits : searchResult?.hits) || [];
  const visibleHits = rawHits.filter(isOriginalFileSearchHit);
  const groups = new Map<string, DataCenterSearchHit[]>();

  for (const hit of visibleHits) {
    const key = `original:${buildSearchGroupKey(hit)}`;
    const bucket = groups.get(key) || [];
    bucket.push(hit);
    groups.set(key, bucket);
  }

  const displayGroups = Array.from(groups.entries())
    .map<FileSearchDisplayGroup | null>(([id, hits]) => {
      const primaryHit = pickPrimarySearchHit(hits);
      if (!primaryHit) return null;
      const maxScore = hits.reduce<number | null>((current, hit) => {
        const score = typeof hit.score === 'number' && Number.isFinite(hit.score) ? hit.score : null;
        if (score === null) return current;
        return current === null ? score : Math.max(current, score);
      }, null);
      const supportLevel = normalizeSearchSupportLevel(maxScore);
      return {
        id,
        layer: 'original',
        primaryHit,
        hits: [...hits].sort((left, right) => {
          const scoreDiff = scoreValue(right) - scoreValue(left);
          if (scoreDiff !== 0) return scoreDiff;
          return excerptLength(right) - excerptLength(left);
        }),
        supportLevel,
        supportLabel: SUPPORT_LABELS[supportLevel],
        sourceAvailabilityLabel: sourceAvailabilityLabel(primaryHit),
      };
    })
    .filter((group): group is FileSearchDisplayGroup => group !== null)
    .sort(compareGroups);

  return {
    originalGroups: displayGroups,
    systemGroups: [],
    hiddenInvalidCount: 0,
  };
}

export function buildFileSearchBriefAnswer(displayGroups: FileSearchDisplayGroups): FileSearchBriefAnswer {
  const { originalGroups, systemGroups } = displayGroups;
  const rankedOriginals = originalGroups.slice(0, 3).map((group, index) => `${index + 1}. ${compactText(group.primaryHit.title || '未命名资料')}`);
  const rankedSystems = systemGroups.slice(0, 3).map((group, index) => `${index + 1}. ${compactText(group.primaryHit.title || '未命名线索')}`);

  if (rankedOriginals.length > 0) {
    return {
      title: '简要排序',
      lines: [
        `我先按相关性和可打开性排了一个优先级：${rankedOriginals.join('；')}。`,
        systemGroups.length > 0 ? '后面的系统整理线索只作为补充定位，优先核对原始文件。' : '下面是对应文件卡片，可以打开原文继续核对。',
      ].filter(Boolean),
      note: '这不是最终结论，而是当前资料检索结果的阅读顺序。',
    };
  }

  if (rankedSystems.length > 0) {
    return {
      title: '简要排序',
      lines: [
        `暂时没有可直接优先展示的原始文件，先列出系统整理线索：${rankedSystems.join('；')}。`,
        '如果需要可编辑或可核对的原文件，建议回到资料区补齐原始文档。',
      ],
      note: '系统线索可帮助定位方向，但不等同于原始上传文件。',
    };
  }

  return {
    title: '简要排序',
    lines: ['这次没有找到足够匹配的文件。可以换一个文件名、项目名或关键词再试。'],
    note: '没有可展示的文件卡片。',
  };
}
