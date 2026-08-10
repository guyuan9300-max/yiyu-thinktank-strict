import type { EvidenceItem } from './types.js';

export type EvidenceSupportLevel = 'strong' | 'reference' | 'background';
export type EvidenceOpenableKind = 'original_file' | 'machine_markdown' | 'system_card' | 'unknown';
export type EvidenceCitationRole = 'direct_quote' | 'direct_support' | 'background';

export type EvidenceBusinessTag =
  | 'direct_support'
  | 'background_support'
  | 'strategy_material'
  | 'meeting_material'
  | 'project_material'
  | 'raw_source'
  | 'summary_source'
  | 'index_source'
  | 'needs_review';

export interface EvidenceCitationSnippet {
  id: string;
  title: string;
  excerpt: string;
  sourceType: string;
  documentId?: string | null;
  path?: string | null;
  originalPath?: string | null;
  managedPath?: string | null;
  markdownPath?: string | null;
  openableKind: EvidenceOpenableKind;
  sourceAvailability?: string | null;
  originalAvailable?: boolean | null;
  machineReadableAvailable?: boolean | null;
  openOriginalDisabledReason?: string | null;
  score?: number | null;
  sectionLabel?: string | null;
  retrievalStage?: EvidenceItem['retrievalStage'];
  matchedTerms: string[];
  supportLevel: EvidenceSupportLevel;
  citationRole?: EvidenceCitationRole | string | null;
  citationPriority?: number | null;
  citationReason?: string | null;
  businessTags: EvidenceBusinessTag[];
}

export interface EvidenceCitationCard {
  id: string;
  claimTitle: string;
  sourceTitle: string;
  sourcePath?: string | null;
  openPath?: string | null;
  openActionLabel: string;
  openActionDisabledReason?: string | null;
  openableKind: EvidenceOpenableKind;
  sectionLabel?: string | null;
  supportLevel: EvidenceSupportLevel;
  citationRole?: EvidenceCitationRole | string | null;
  citationPriority?: number | null;
  citationReason?: string | null;
  businessTags: EvidenceBusinessTag[];
  primarySnippet: EvidenceCitationSnippet;
  snippets: EvidenceCitationSnippet[];
  maxScore?: number | null;
}

export const EVIDENCE_SUPPORT_LABELS: Record<EvidenceSupportLevel, string> = {
  strong: '强相关',
  reference: '可参考',
  background: '背景材料',
};

export const EVIDENCE_CITATION_ROLE_LABELS: Record<EvidenceCitationRole, string> = {
  direct_quote: '直接依据',
  direct_support: '直接依据',
  background: '相关背景',
};

export const EVIDENCE_BUSINESS_TAG_LABELS: Record<EvidenceBusinessTag, string> = {
  direct_support: '直接支撑',
  background_support: '背景支撑',
  strategy_material: '战略材料',
  meeting_material: '会议资料',
  project_material: '项目材料',
  raw_source: '原文支撑',
  summary_source: '背景摘要',
  index_source: '资料目录',
  needs_review: '待复核',
};

const STAGE_RANK: Record<string, number> = {
  raw_chunk: 0,
  state_pool: 1,
  surrogate: 2,
  master_index: 3,
};

const SOURCE_TITLE_FALLBACK = '未命名资料';
const CLAIM_TITLE_FALLBACK = '这条资料可作为当前回答的背景依据';

function cleanText(value: unknown): string {
  return String(value || '')
    .replace(/^[\s\-*•·、。]+/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function stableKey(value: unknown): string {
  const cleaned = cleanText(value).toLowerCase();
  return cleaned || 'unknown';
}

function truncateClaim(value: string, maxChars = 42): string {
  const text = cleanText(value);
  if (text.length <= maxChars) return text;
  return `${text.slice(0, maxChars).replace(/[，,、；;：:\s]+$/g, '')}...`;
}

function looksLikeFileName(value: string): boolean {
  return /\.(docx?|pptx?|xlsx?|pdf|md|txt)$/i.test(value.trim());
}

function normalizeOpenableKind(value: unknown): EvidenceOpenableKind {
  if (value === 'original_file' || value === 'machine_markdown' || value === 'system_card') return value;
  return 'unknown';
}

function isMarkdownPath(value: unknown): boolean {
  return cleanText(value).toLowerCase().endsWith('.md');
}

function firstNonMarkdownPath(...values: Array<unknown>): string {
  for (const value of values) {
    const path = cleanText(value);
    if (path && !isMarkdownPath(path)) return path;
  }
  return '';
}

function resolveOpenablePath(item: EvidenceItem): { path: string | null; kind: EvidenceOpenableKind; label: string } {
  if (item.sourceAvailability === 'invalid_source') {
    return { path: null, kind: 'unknown', label: '查看资料' };
  }
  const explicitKind = normalizeOpenableKind(item.openableKind);
  const originalPath = firstNonMarkdownPath(item.originalPath, item.managedPath, item.path);
  if (item.sourceAvailability !== 'machine_readable_only' && explicitKind === 'original_file' && originalPath) {
    return { path: originalPath, kind: 'original_file', label: '查看原文' };
  }
  if (item.sourceAvailability !== 'machine_readable_only' && item.canonicalKind === 'raw_file' && originalPath) {
    return { path: originalPath, kind: 'original_file', label: '查看原文' };
  }
  if (explicitKind === 'system_card') {
    const path = cleanText(item.markdownPath) || cleanText(item.path);
    return { path: path || null, kind: 'system_card', label: '查看系统卡片' };
  }
  const markdownPath = cleanText(item.markdownPath) || (isMarkdownPath(item.path) ? cleanText(item.path) : '');
  if (markdownPath) {
    return { path: markdownPath, kind: 'machine_markdown', label: '查看机读稿' };
  }
  const fallbackPath = cleanText(item.path);
  return { path: fallbackPath || null, kind: explicitKind, label: '查看资料' };
}

function splitInformativeSentences(value: string): string[] {
  const text = cleanText(value);
  if (!text) return [];
  return text
    .split(/[。！？!?；;\n\r]+/)
    .map((part) => cleanText(part))
    .filter((part) => part.length >= 8 && !looksLikeFileName(part));
}

function sourceText(item: EvidenceItem): string {
  return [item.title, item.path, item.sectionLabel, item.sourceType].map(cleanText).join(' ');
}

function addUniqueTag(tags: EvidenceBusinessTag[], tag: EvidenceBusinessTag): void {
  if (!tags.includes(tag)) tags.push(tag);
}

function snippetFromEvidence(item: EvidenceItem): EvidenceCitationSnippet {
  const supportLevel = normalizeEvidenceSupportLevel(item.score);
  const openable = resolveOpenablePath(item);
  return {
    id: item.id,
    title: cleanText(item.title) || SOURCE_TITLE_FALLBACK,
    excerpt: cleanText(item.excerpt),
    sourceType: cleanText(item.sourceType),
    documentId: item.documentId,
    path: item.path,
    originalPath: item.originalPath,
    managedPath: item.managedPath,
    markdownPath: item.markdownPath,
    openableKind: openable.kind,
    sourceAvailability: item.sourceAvailability,
    originalAvailable: item.originalAvailable,
    machineReadableAvailable: item.machineReadableAvailable,
    openOriginalDisabledReason: item.openOriginalDisabledReason,
    score: item.score,
    sectionLabel: item.sectionLabel,
    retrievalStage: item.retrievalStage,
    matchedTerms: Array.isArray(item.matchedTerms) ? item.matchedTerms : [],
    supportLevel,
    citationRole: item.citationRole || null,
    citationPriority: typeof item.citationPriority === 'number' ? item.citationPriority : null,
    citationReason: item.citationReason || null,
    businessTags: deriveEvidenceBusinessTags(item),
  };
}

function rankSnippet(snippet: EvidenceCitationSnippet): [number, number, number, number] {
  const priorityRank = -(typeof snippet.citationPriority === 'number' ? snippet.citationPriority : 0);
  const stageRank = STAGE_RANK[String(snippet.retrievalStage || '')] ?? 9;
  const scoreRank = typeof snippet.score === 'number' ? -snippet.score : 0;
  const excerptRank = -cleanText(snippet.excerpt).length;
  return [priorityRank, stageRank, scoreRank, excerptRank];
}

function compareSnippets(a: EvidenceCitationSnippet, b: EvidenceCitationSnippet): number {
  const left = rankSnippet(a);
  const right = rankSnippet(b);
  for (let index = 0; index < left.length; index += 1) {
    if (left[index] !== right[index]) return left[index] - right[index];
  }
  return cleanText(a.title).localeCompare(cleanText(b.title), 'zh-Hans-CN');
}

function mergeTags(snippets: EvidenceCitationSnippet[], supportLevel: EvidenceSupportLevel): EvidenceBusinessTag[] {
  const tags: EvidenceBusinessTag[] = [];
  addUniqueTag(tags, supportLevel === 'strong' ? 'direct_support' : 'background_support');
  for (const snippet of snippets) {
    for (const tag of snippet.businessTags) {
      if (tag === 'direct_support' || tag === 'background_support') continue;
      addUniqueTag(tags, tag);
    }
  }
  return tags.slice(0, 5);
}

export function normalizeEvidenceSupportLevel(score?: number | null): EvidenceSupportLevel {
  if (typeof score !== 'number' || Number.isNaN(score)) return 'background';
  if (score >= 3) return 'strong';
  if (score >= 1) return 'reference';
  return 'background';
}

export function deriveEvidenceClaimTitle(item: EvidenceItem): string {
  const excerpt = cleanText(item.excerpt);
  const sentences = splitInformativeSentences(excerpt);
  const firstUsefulSentence = sentences[0] || excerpt;
  if (firstUsefulSentence) return truncateClaim(firstUsefulSentence);
  const title = cleanText(item.title);
  return title ? truncateClaim(title) : CLAIM_TITLE_FALLBACK;
}

export function deriveEvidenceBusinessTags(item: EvidenceItem): EvidenceBusinessTag[] {
  const tags: EvidenceBusinessTag[] = [];
  const supportLevel = normalizeEvidenceSupportLevel(item.score);
  addUniqueTag(tags, supportLevel === 'strong' ? 'direct_support' : 'background_support');

  if (item.retrievalStage === 'raw_chunk' && item.openableKind !== 'system_card') addUniqueTag(tags, 'raw_source');
  if (item.retrievalStage === 'surrogate' || item.retrievalStage === 'state_pool') addUniqueTag(tags, 'summary_source');
  if (item.retrievalStage === 'master_index') addUniqueTag(tags, 'index_source');

  const text = sourceText(item);
  if (/战略|规划|陪伴|定位/.test(text)) addUniqueTag(tags, 'strategy_material');
  if (/会议|纪要|访谈|沟通/.test(text)) addUniqueTag(tags, 'meeting_material');
  if (/项目|服务|产品|方案/.test(text)) addUniqueTag(tags, 'project_material');
  if (item.isFallback) addUniqueTag(tags, 'needs_review');
  if (item.openableKind === 'system_card') addUniqueTag(tags, 'needs_review');

  return tags;
}

export function buildEvidenceGroupKey(item: EvidenceItem): string {
  const sectionKey = stableKey(item.sectionLabel);
  if (cleanText(item.documentId)) return `document:${stableKey(item.documentId)}:${sectionKey}`;
  if (cleanText(item.path)) return `path:${stableKey(item.path)}:${sectionKey}`;
  return `title:${stableKey(item.title)}:${sectionKey}`;
}

export function buildEvidenceCitationCards(evidence: EvidenceItem[]): EvidenceCitationCard[] {
  const groups = new Map<string, EvidenceItem[]>();
  for (const item of evidence) {
    if (item.sourceAvailability === 'invalid_source') continue;
    const key = buildEvidenceGroupKey(item);
    const bucket = groups.get(key) || [];
    bucket.push(item);
    groups.set(key, bucket);
  }

  return Array.from(groups.entries())
    .map<EvidenceCitationCard | null>(([id, items]) => {
      const sortedItems = [...items].sort((a, b) => compareSnippets(snippetFromEvidence(a), snippetFromEvidence(b)));
      const snippets = sortedItems.map(snippetFromEvidence);
      const primarySnippet = snippets[0];
      if (!primarySnippet) return null;
      const maxScore = snippets.reduce<number | null>((current, snippet) => {
        if (typeof snippet.score !== 'number' || Number.isNaN(snippet.score)) return current;
        return current === null ? snippet.score : Math.max(current, snippet.score);
      }, null);
      const supportLevel = normalizeEvidenceSupportLevel(maxScore);
      const openable = resolveOpenablePath(sortedItems[0]);
      const citationPriority = typeof primarySnippet.citationPriority === 'number' ? primarySnippet.citationPriority : null;
      const citationRole = primarySnippet.citationRole || null;
      return {
        id,
        claimTitle: deriveEvidenceClaimTitle(sortedItems[0]),
        sourceTitle: primarySnippet?.title || SOURCE_TITLE_FALLBACK,
        sourcePath: primarySnippet?.path,
        openPath: openable.path,
        openActionLabel: openable.label,
        openActionDisabledReason: sortedItems[0].openOriginalDisabledReason || null,
        openableKind: openable.kind,
        sectionLabel: primarySnippet?.sectionLabel,
        supportLevel,
        citationRole,
        citationPriority,
        citationReason: primarySnippet.citationReason || null,
        businessTags: mergeTags(snippets, supportLevel),
        primarySnippet,
        snippets,
        maxScore,
      };
    })
    .filter((card): card is EvidenceCitationCard => card !== null)
    .sort((a, b) => compareSnippets(a.primarySnippet, b.primarySnippet));
}
