import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  X,
  Download,
  Paperclip,
  Clock,
  Users,
  FileBadge,
  FileText,
  Image,
  ExternalLink,
  Sparkles,
  RefreshCw,
  AlertTriangle,
  ArrowRight,
  Check,
  Link2,
  Search,
  ChevronDown,
} from 'lucide-react';
import type {
  EventLineReportSnapshot,
  EventLineReportAttachment,
  EventLineActivity,
  EventLineTimelineNode as BackendEventLineTimelineNode,
  EventLineTimelineNodeKind as BackendEventLineTimelineNodeKind,
  EventLineTaskCandidate,
  EventLineDraft,
  ReportArtifactSummary,
  ReportFileFormat,
  ReportRunSummary,
  Task,
} from '../../../shared/types.js';
import {
  draftEventLineBackground,
  getEventLineTaskCandidates,
  linkTaskToEventLine,
  getEventLineReportSnapshot,
  getEventLineReportDraft,
  getEventLineTimelineNarrative,
  listEventLineReportArtifacts,
  listLegacyEventLineReportRuns,
  polishEventLineGoal,
  regenerateEventLineTimelineNarrative,
  renderReportArtifact,
  retryEventLineAttachmentParse,
  retryFailedEventLineAttachments,
  saveReport,
  setEventLineTaskMilestone,
  updateEventLine,
  uploadEventLineAttachment,
} from '../../lib/api.js';
import type { EventLineTimelineNarrative, EventLineNarrativeNode } from '../../../shared/types';
import AIReportGeneratorModal from '../reports/AIReportGeneratorModal.js';

const EVENT_LINE_READINESS_DIMENSIONS = ['目标', '背景', '人工里程碑', '推进事实', '关键证据', '时间顺序'];

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

type EditableActivity = EventLineActivity & {
  /** 用户在本地编辑过的标题 */
  editedTitle?: string;
  /** 用户在本地编辑过的摘要 */
  editedSummary?: string;
  /** 用户标记为隐藏（不纳入导出） */
  hidden?: boolean;
};

type ReportDraft = {
  eventLineName: string;
  summary: string;
  activities: EditableActivity[];
  attachments: EventLineReportAttachment[];
  tasks: Task[];
  participantNames: string[];
  snapshotAt: string;
  timelineNodes?: EventLineTimelineNode[];
};

type EventLineMaterialGroupKey = 'core' | 'review' | 'supplement' | 'system';
type EventLineMaterialBundleKind = 'task' | 'activity' | 'loose' | 'system';

type EventLineMaterialAttachmentGroup = {
  id: string;
  title: string;
  familyLabel: string;
  isImage: boolean;
  primary: EventLineReportAttachment;
  attachments: EventLineReportAttachment[];
  duplicateCount?: number;
  versionCount?: number;
  hasTest: boolean;
  missingDownload: boolean;
};

type EventLineMaterialBundle = {
  id: string;
  group: EventLineMaterialGroupKey;
  kind: EventLineMaterialBundleKind;
  title: string;
  summary: string;
  sourceLabel: string;
  happenedAt: string;
  actorName?: string | null;
  statusLabel?: string;
  tags: string[];
  warnings: string[];
  attachments: EventLineReportAttachment[];
  attachmentGroups: EventLineMaterialAttachmentGroup[];
  duplicateCount?: number;
  versionCount?: number;
  testAttachmentCount?: number;
  missingDownloadCount?: number;
};

type EventLineMaterialAttachmentAnalysis = {
  attachmentGroups: EventLineMaterialAttachmentGroup[];
  duplicateAttachmentCount: number;
  versionConflictCount: number;
  testAttachmentCount: number;
  missingDownloadCount: number;
  imageCount: number;
  docCount: number;
  totalCount: number;
  hasAnyImage: boolean;
  hasOnlyTestAttachments: boolean;
  familyLabels: string[];
};

type EventLineMaterialModel = {
  groups: Record<EventLineMaterialGroupKey, EventLineMaterialBundle[]>;
  gaps: string[];
  duplicateAttachmentCount: number;
  testAttachmentCount: number;
  looseAttachmentCount: number;
};

type LegacyEventLineTimelineNodeKind = 'project_milestone' | 'task_bundle' | 'meeting_material' | 'attachment_bundle' | 'admin_material' | 'system';
type EventLineTimelineNodeKind = BackendEventLineTimelineNodeKind | LegacyEventLineTimelineNodeKind;

type EventLineTimelineNode = Omit<BackendEventLineTimelineNode, 'kind'> & {
  id: string;
  kind: EventLineTimelineNodeKind;
  title: string;
  time: string;
  summary: string;
  sourceTaskId?: string;
  sourceTaskIds?: string[];
  sourceActivityIds: string[];
  attachments: EventLineReportAttachment[];
  materialCount?: number;
  includeInReport?: boolean;
  evidenceSummary: string;
  warnings: string[];
  tags: string[];
  actorName?: string | null;
  ownerName?: string | null;
};

type EventLineTimelineModel = {
  mainNodes: EventLineTimelineNode[];
  reviewNodes: EventLineTimelineNode[];
  systemNodes: EventLineTimelineNode[];
};

type EvidenceTaskRow = {
  task: Task;
  attachments: EventLineReportAttachment[];
  isMilestone: boolean;
  happenedAt: string;
};

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const SOURCE_TYPE_LABELS: Record<string, string> = {
  task_activity: '任务',
  meeting: '会议',
  meeting_minute: '会议纪要',
  support_request: '支持请求',
  review: '复核',
  attachment: '附件',
  manual_note: '备注',
  document_ingest: '历史系统推荐',
  atomic_fact: '历史系统推荐',
};

const EVENT_LINE_KIND_LABELS: Record<string, string> = {
  project_line: '项目线',
  issue_line: '议题线',
  coordination_line: '协同线',
  case_line: '案例线',
  custom: '事件线',
};

const EVENT_LINE_STATUS_LABELS: Record<string, string> = {
  active: '推进中',
  blocked: '存在阻点',
  paused: '暂缓中',
  done: '已完成',
  archived: '已归档',
};

const EVENT_LINE_STATUS_TONE: Record<string, string> = {
  active: 'border-emerald-300/30 bg-emerald-400/15 text-emerald-50',
  blocked: 'border-rose-300/30 bg-rose-400/15 text-rose-50',
  paused: 'border-amber-300/30 bg-amber-400/15 text-amber-50',
  done: 'border-sky-300/30 bg-sky-400/15 text-sky-50',
  archived: 'border-white/20 bg-white/10 text-white/75',
};

const TASK_STATUS_LABELS: Record<string, string> = {
  inbox: '待确认',
  todo: '待办',
  doing: '推进中',
  done: '已完成',
  rejected: '已取消',
};

const TIMELINE_KIND_LABELS: Record<EventLineTimelineNodeKind, string> = {
  project_start: '事件线建立',
  material_intake: '材料入库',
  project_review: '项目复盘',
  continuing_task: '持续推进',
  admin_archive: '行政归档',
  needs_review: '待确认',
  system_trace: '系统痕迹',
  project_milestone: '里程碑',
  task_bundle: '任务节点',
  meeting_material: '会议材料',
  attachment_bundle: '附件材料',
  admin_material: '行政材料',
  system: '系统痕迹',
};

const MATERIAL_CORE_KEYWORDS = /(会议纪要|纪要|方案|报告|清单|复盘|提纲|设计|输出|交付|诊断|汇报|关键决策|决策|证据|资助方|成果|项目设计|合同|协议|报销|票据|发票|凭证|回签|结项)/u;
const MATERIAL_ACTIVITY_KEYWORDS = /(会议|沟通|拜访|访谈|讨论|复盘|澄清|确认|决策|判断|补充说明|说明)/u;
const SYSTEM_TRACE_KEYWORDS = /(创建事件线|更新事件线|结束事件线|事件线已归档|上传附件|新增任务|任务更新|已归档到任务附件|已归档到事件线附件)/u;

/** Key events: task created, manual note (review content), attachment upload.
 *  Uses backend-computed `isKey` flag; falls back to heuristic for older data. */
function isKeyActivity(activity: { sourceType: string; title: string; summary: string; isKey?: boolean; metadata?: Record<string, unknown> }): boolean {
  if (activity.isKey !== undefined) return activity.isKey;
  // Fallback for activities without backend isKey flag
  if (['manual_note', 'attachment'].includes(activity.sourceType)) return true;
  if (activity.sourceType === 'task_activity' && activity.metadata?.eventType === 'created') return true;
  return false;
}

function isBootstrapActivity(activity: EditableActivity): boolean {
  const metadata = activity.metadata || {};
  const eventType = String((metadata as Record<string, unknown>).eventType || '').toLowerCase();
  if (activity.sourceType === 'task_activity' && eventType === 'created') return true;
  if (eventType === 'event_line_created' || eventType === 'line_created') return true;
  const text = `${previewActivityTitle(activity)} ${previewActivitySummary(activity)}`.toLowerCase();
  return text.includes('创建事件线') || text.includes('created event line');
}

function formatTs(iso: string) {
  if (!iso) return '';
  return iso.slice(0, 16).replace('T', ' ');
}

function formatDateLabel(iso?: string | null) {
  if (!iso) return '待补充';
  return iso.slice(0, 10).replace(/-/g, '.');
}

function taskBusinessDate(task: Task) {
  return normalizeText(
    task.scheduledStartAt
    || task.startDate
    || task.dueDate
    || task.deadlineAt
    || task.ddl,
  );
}

function compareTasksByBusinessDate(left: Task, right: Task) {
  const leftDate = taskBusinessDate(left);
  const rightDate = taskBusinessDate(right);
  if (leftDate && rightDate) {
    const compared = leftDate.localeCompare(rightDate);
    if (compared !== 0) return compared;
  } else if (leftDate) {
    return -1;
  } else if (rightDate) {
    return 1;
  }
  return normalizeText(left.title).localeCompare(normalizeText(right.title), 'zh-CN');
}

function truncateText(value: string | null | undefined, maxLength: number) {
  const normalized = (value || '').replace(/\s+/g, ' ').trim();
  if (!normalized) return '';
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, Math.max(0, maxLength - 1)).trim()}…`;
}

function normalizeText(value: string | null | undefined) {
  return (value || '').replace(/\s+/g, ' ').trim();
}

function dedupeStrings(items: Array<string | null | undefined>) {
  return Array.from(
    new Set(items.map((item) => normalizeText(item)).filter(Boolean)),
  );
}

function previewActivityTitle(activity: EditableActivity) {
  return normalizeText(activity.editedTitle) || normalizeText(activity.title) || '未命名活动';
}

function previewActivitySummary(activity: EditableActivity) {
  return normalizeText(activity.editedSummary) || normalizeText(activity.summary);
}

function isImageAttachment(att: EventLineReportAttachment) {
  return (att.mimeType || '').startsWith('image/') || /\.(jpg|jpeg|png|gif|webp)$/i.test(att.title);
}

function attachmentFamilyLabel(att: EventLineReportAttachment) {
  if (isImageAttachment(att)) return '图像证据';
  const ext = (att.title.split('.').pop() || '').toLowerCase();
  if (ext === 'pdf') return 'PDF 资料';
  if (['doc', 'docx'].includes(ext)) return 'Word 文档';
  if (['xls', 'xlsx'].includes(ext)) return '表格资料';
  if (['ppt', 'pptx'].includes(ext)) return '汇报材料';
  return '补充资料';
}

function fileSizeLabel(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function attachmentFamilySummary(attachments: EventLineReportAttachment[]) {
  const familyCounts = new Map<string, number>();
  for (const attachment of attachments) {
    const family = attachmentFamilyLabel(attachment);
    familyCounts.set(family, (familyCounts.get(family) || 0) + 1);
  }
  const entries = Array.from(familyCounts.entries()).sort((left, right) => {
    if (right[1] !== left[1]) return right[1] - left[1];
    return left[0].localeCompare(right[0], 'zh-CN');
  });
  return {
    entries,
    shortText: entries.length > 0 ? entries.slice(0, 3).map(([label]) => label).join('、') : '暂无附件',
    detailedText: entries.length > 0 ? entries.slice(0, 3).map(([label, count]) => `${label}${count}份`).join('、') : '暂无附件材料',
  };
}

function normalizeAttachmentName(title: string) {
  return normalizeText(title).toLowerCase();
}

function formatAttachmentBytes(bytes: number | null | undefined): string {
  const n = Number(bytes || 0);
  if (!n) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(1)} GB`;
}

function resolveAttachmentUrl(att: EventLineReportAttachment, backendBaseUrl: string) {
  const url = normalizeText(att.downloadUrl);
  if (!url) return '';
  if (/^https?:\/\//i.test(url)) return url;
  return `${backendBaseUrl}${url}`;
}

function resolveAttachmentOpenUrl(att: EventLineReportAttachment, backendBaseUrl: string) {
  const url = normalizeText(att.openUrl) || normalizeText(att.downloadUrl);
  if (!url) return '';
  if (/^https?:\/\//i.test(url)) return url;
  return `${backendBaseUrl}${url}`;
}

function attachmentDisplayTags(att: EventLineReportAttachment) {
  const parseLabel = attachmentParseStatusLabel(att.parseStatus);
  return [
    att.sourceKind === 'task_attachment' ? '强相关' : '原始文件',
    att.documentId ? '已建立资料记录' : '待建立资料记录',
    parseLabel,
  ].filter(Boolean);
}

const ATTACHMENT_PARSE_STATUS_LABELS: Record<string, string> = {
  uploaded: '已上传，尚未解析',
  queued: '等待解析',
  parsing: '正在解析',
  ready: '解析完成',
  partial_ready: '部分内容可用',
  failed: '解析失败',
  missing_source: '缺少原文件',
  missing_document: '待建立资料记录',
  pending: '等待解析',
  unparsed: '尚未解析',
};

function attachmentParseStatusLabel(status: string | null | undefined) {
  const normalized = normalizeText(status).toLowerCase();
  return ATTACHMENT_PARSE_STATUS_LABELS[normalized] || (normalized ? '状态待确认' : '尚未解析');
}

function isAttachmentParsePending(att: EventLineReportAttachment) {
  return ['queued', 'parsing', 'pending', 'running'].includes(
    normalizeText(att.parseJobStatus || att.parseStatus).toLowerCase(),
  );
}

function canRetryAttachmentParse(att: EventLineReportAttachment) {
  return ['event_line_attachment', 'task_attachment'].includes(normalizeText(att.sourceKind))
    && ['failed', 'missing_source', 'missing_document'].includes(normalizeText(att.parseStatus).toLowerCase());
}

function canStartAttachmentParse(att: EventLineReportAttachment) {
  return ['event_line_attachment', 'task_attachment'].includes(normalizeText(att.sourceKind))
    && !isAttachmentParsePending(att)
    && ['', 'uploaded', 'unparsed'].includes(normalizeText(att.parseStatus).toLowerCase());
}

function isHistoricalSuggestionActivity(activity: EditableActivity) {
  return activity.includeInNarrative === false
    || activity.associationStatus === 'historical_suggestion'
    || ['document_ingest', 'atomic_fact'].includes(activity.sourceType);
}

function isTestAttachment(att: EventLineReportAttachment) {
  const title = normalizeText(att.title).toLowerCase();
  return /(^|[\/\s_.-])(test|smoke|dummy|sample|demo)([\/\s_.-]|$)/i.test(title) || title.includes('测试');
}

function isAttachmentUploadTrace(activity: EditableActivity) {
  const text = `${previewActivityTitle(activity)} ${previewActivitySummary(activity)}`;
  return activity.sourceType === 'attachment' || /上传附件|归档到任务附件|归档到事件线附件/u.test(text);
}

function isSystemTraceActivity(activity: EditableActivity) {
  const metadata = activity.metadata || {};
  const eventType = String((metadata as Record<string, unknown>).eventType || '').toLowerCase();
  const title = previewActivityTitle(activity);
  const summary = previewActivitySummary(activity);
  const text = `${title} ${summary}`;
  if (isBootstrapActivity(activity) || isAttachmentUploadTrace(activity)) return true;
  if (eventType === 'updated' || eventType === 'created') return true;
  if (SYSTEM_TRACE_KEYWORDS.test(text)) return true;
  return false;
}

function materialTimeDesc(left: EventLineMaterialBundle, right: EventLineMaterialBundle) {
  return (right.happenedAt || '').localeCompare(left.happenedAt || '');
}

function taskStatusLabel(status: string) {
  return TASK_STATUS_LABELS[status] || status || '';
}

function materialSourceLabel(kind: EventLineMaterialBundleKind, fallback: string) {
  if (kind === 'loose') return fallback || '待确认材料';
  if (kind === 'task') return fallback || '关联任务';
  if (kind === 'system') return fallback || '系统痕迹';
  return fallback || '活动';
}

function looseAttachmentFamilyKey(att: EventLineReportAttachment) {
  if (isImageAttachment(att)) return 'image';
  return attachmentFamilyLabel(att);
}

function latestAttachmentTime(attachments: EventLineReportAttachment[], fallback: string) {
  return attachments
    .map((att) => att.createdAt)
    .filter(Boolean)
    .sort((left, right) => right.localeCompare(left))[0] || fallback;
}

function analyzeMaterialAttachments(attachments: EventLineReportAttachment[]): EventLineMaterialAttachmentAnalysis {
  const attachmentBuckets = new Map<string, EventLineReportAttachment[]>();
  for (const attachment of attachments) {
    const key = normalizeAttachmentName(attachment.title) || attachment.id;
    const list = attachmentBuckets.get(key) || [];
    list.push(attachment);
    attachmentBuckets.set(key, list);
  }

  const attachmentGroups: EventLineMaterialAttachmentGroup[] = [];
  let duplicateAttachmentCount = 0;
  let versionConflictCount = 0;
  let testAttachmentCount = 0;
  let missingDownloadCount = 0;
  let imageCount = 0;
  let docCount = 0;
  const familyLabels = new Set<string>();

  for (const [bucketKey, bucket] of attachmentBuckets) {
    const sorted = [...bucket].sort((left, right) => (right.createdAt || '').localeCompare(left.createdAt || ''));
    const primary = sorted[0];
    const familyLabel = attachmentFamilyLabel(primary);
    const isImage = isImageAttachment(primary);
    const uniqueSizes = new Set(sorted.map((item) => Number(item.sizeBytes || 0)));
    const duplicateCount = sorted.length > 1 ? sorted.length : undefined;
    const versionCount = uniqueSizes.size > 1 ? uniqueSizes.size : undefined;
    const hasTest = sorted.some(isTestAttachment);
    const missingDownload = sorted.some((item) => !normalizeText(item.downloadUrl));

    if (duplicateCount) duplicateAttachmentCount += sorted.length - 1;
    if (versionCount) versionConflictCount += 1;
    testAttachmentCount += sorted.filter(isTestAttachment).length;
    missingDownloadCount += sorted.filter((item) => !normalizeText(item.downloadUrl)).length;
    if (isImage) imageCount += sorted.length;
    else docCount += sorted.length;
    familyLabels.add(familyLabel);

    attachmentGroups.push({
      id: `attachment-group:${bucketKey}:${primary.id}`,
      title: normalizeText(primary.title) || '未命名附件',
      familyLabel,
      isImage,
      primary,
      attachments: sorted,
      duplicateCount,
      versionCount,
      hasTest,
      missingDownload,
    });
  }

  attachmentGroups.sort((left, right) => (right.primary.createdAt || '').localeCompare(left.primary.createdAt || ''));

  return {
    attachmentGroups,
    duplicateAttachmentCount,
    versionConflictCount,
    testAttachmentCount,
    missingDownloadCount,
    imageCount,
    docCount,
    totalCount: attachments.length,
    hasAnyImage: imageCount > 0,
    hasOnlyTestAttachments: attachments.length > 0 && testAttachmentCount === attachments.length,
    familyLabels: Array.from(familyLabels),
  };
}

function materialAttachmentTags(analysis: EventLineMaterialAttachmentAnalysis) {
  return [
    analysis.totalCount > 0 ? `素材 ${analysis.totalCount}` : '',
    analysis.imageCount > 0 ? `图片 ${analysis.imageCount}` : '',
    analysis.docCount > 0 ? `文档 ${analysis.docCount}` : '',
  ].filter(Boolean);
}

function materialAttachmentWarnings(analysis: EventLineMaterialAttachmentAnalysis, extraWarnings: string[] = []) {
  return [
    ...extraWarnings,
    analysis.duplicateAttachmentCount > 0 ? `重复附件 ${analysis.duplicateAttachmentCount} 条` : '',
    analysis.versionConflictCount > 0 ? `多版本素材 ${analysis.versionConflictCount} 组` : '',
    analysis.testAttachmentCount > 0 ? `含疑似测试素材 ${analysis.testAttachmentCount} 个` : '',
    analysis.missingDownloadCount > 0 ? `缺少下载地址 ${analysis.missingDownloadCount} 个` : '',
  ].filter(Boolean);
}

function deriveEventLineMaterialModel(snapshot: EventLineReportSnapshot, draft: ReportDraft): EventLineMaterialModel {
  const groups: Record<EventLineMaterialGroupKey, EventLineMaterialBundle[]> = {
    core: [],
    review: [],
    supplement: [],
    system: [],
  };
  const backendNodes = (snapshot.timelineNodes || []).filter((node) => Boolean(node && node.id && node.title));
  if (backendNodes.length > 0) {
    let duplicateAttachmentCount = 0;
    let testAttachmentCount = 0;
    let looseAttachmentCount = 0;
    for (const node of backendNodes) {
      const attachments = Array.isArray(node.attachments) ? node.attachments : [];
      const analysis = analyzeMaterialAttachments(attachments);
      duplicateAttachmentCount += analysis.duplicateAttachmentCount;
      testAttachmentCount += analysis.testAttachmentCount;
      if (node.kind === 'needs_review') looseAttachmentCount += attachments.filter((att) => !normalizeText(att.taskId)).length;
      const group: EventLineMaterialGroupKey = node.kind === 'system_trace'
        ? 'system'
        : node.kind === 'needs_review'
          ? 'review'
          : node.kind === 'admin_archive'
            ? 'supplement'
            : 'core';
      groups[group].push({
        id: `node:${node.id}`,
        group,
        kind: node.kind === 'system_trace' ? 'system' : node.sourceTaskId || (node.sourceTaskIds || []).length > 0 ? 'task' : 'activity',
        title: node.title,
        summary: truncateText(node.summary || node.evidenceSummary || '', 180),
        sourceLabel: TIMELINE_KIND_LABELS[node.kind] || '事件节点',
        happenedAt: node.time || draft.snapshotAt,
        actorName: node.ownerName || node.actorName,
        statusLabel: node.kind === 'needs_review' ? '待确认' : '',
        tags: [
          ...(node.tags || []),
          ...materialAttachmentTags(analysis),
        ].filter(Boolean),
        warnings: materialAttachmentWarnings(analysis, node.warnings || []),
        attachments,
        attachmentGroups: analysis.attachmentGroups,
        duplicateCount: analysis.duplicateAttachmentCount || undefined,
        versionCount: analysis.versionConflictCount || undefined,
        testAttachmentCount: analysis.testAttachmentCount || undefined,
        missingDownloadCount: analysis.missingDownloadCount || undefined,
      });
    }
    for (const activity of draft.activities.filter(isHistoricalSuggestionActivity)) {
      const title = previewActivityTitle(activity);
      groups.review.push({
        id: `historical-suggestion:${activity.id}`,
        group: 'review',
        kind: 'activity',
        title,
        summary: truncateText(previewActivitySummary(activity) || title, 180),
        sourceLabel: SOURCE_TYPE_LABELS[activity.sourceType] || '历史系统推荐',
        happenedAt: activity.happenedAt,
        actorName: activity.actorName,
        statusLabel: '历史推荐，未确认',
        tags: ['历史系统推荐', '不参与主线'],
        warnings: ['这是旧版系统推测产生的关联，尚未由用户确认为正式证据。'],
        attachments: [],
        attachmentGroups: [],
      });
    }
    for (const key of Object.keys(groups) as EventLineMaterialGroupKey[]) {
      groups[key] = groups[key].sort(materialTimeDesc);
    }
    const gaps = [
      normalizeText(snapshot.eventLine.recentDecision) ? '' : '缺关键决策：建议补“为什么形成今天这个判断”。',
      normalizeText(snapshot.eventLine.nextStep) ? '' : '缺下一步：建议补负责人、动作和时间点。',
      normalizeText(snapshot.eventLine.currentBlocker) ? '' : '缺当前阻塞：建议补这条线现在卡在哪里。',
      groups.review.length > 0 ? `存在待确认材料：${groups.review.length} 个节点需要补归属、清理测试素材或等待解析。` : '',
    ].filter(Boolean);
    return {
      groups,
      gaps,
      duplicateAttachmentCount,
      testAttachmentCount,
      looseAttachmentCount,
    };
  }
  const taskMap = new Map((draft.tasks || []).map((task) => [task.id, task]));
  let duplicateAttachmentCount = 0;
  let testAttachmentCount = 0;
  let looseAttachmentCount = 0;

  const pushMaterial = (bundle: EventLineMaterialBundle) => {
    groups[bundle.group].push(bundle);
  };

  const taskAttachmentMap = new Map<string, EventLineReportAttachment[]>();
  const looseAttachmentMap = new Map<string, EventLineReportAttachment[]>();

  for (const attachment of draft.attachments) {
    const taskId = normalizeText(attachment.taskId);
    if (taskId && taskMap.has(taskId)) {
      const list = taskAttachmentMap.get(taskId) || [];
      list.push(attachment);
      taskAttachmentMap.set(taskId, list);
      continue;
    }
    const looseKey = looseAttachmentFamilyKey(attachment);
    const list = looseAttachmentMap.get(looseKey) || [];
    list.push(attachment);
    looseAttachmentMap.set(looseKey, list);
  }

  for (const task of draft.tasks || []) {
    const title = normalizeText(task.title);
    const taskLike = task as unknown as Record<string, unknown>;
    const description = normalizeText(task.desc || (typeof taskLike.description === 'string' ? taskLike.description : undefined));
    const taskAttachments = taskAttachmentMap.get(task.id) || [];
    const analysis = analyzeMaterialAttachments(taskAttachments);
    const contextText = [
      title,
      description,
      normalizeText(task.currentBlocker),
      normalizeText(task.nextAction),
      normalizeText(task.recentDecision),
      taskAttachments.map((attachment) => attachment.title).join(' '),
    ].join(' ');
    // 「按任务查看」必须列出完整任务列表 —— 不再用关键词过滤"是否有汇报价值"。
    // 没标题的任务才跳过(数据异常); 即使没附件、没关键词也保留一个骨架卡片。
    if (!title) continue;
    const hasMaterialContext = taskAttachments.length > 0 || MATERIAL_CORE_KEYWORDS.test(contextText);

    duplicateAttachmentCount += analysis.duplicateAttachmentCount;
    testAttachmentCount += analysis.testAttachmentCount;
    const hasCoreSignal = MATERIAL_CORE_KEYWORDS.test(contextText) || analysis.hasAnyImage;
    const hasReviewSignal = (
      analysis.testAttachmentCount > 0
      || analysis.versionConflictCount > 0
      || analysis.missingDownloadCount > 0
      || analysis.hasOnlyTestAttachments
    );
    const group: EventLineMaterialGroupKey = hasReviewSignal ? 'review' : hasCoreSignal ? 'core' : 'supplement';
    const familyText = analysis.familyLabels.length > 0 ? analysis.familyLabels.slice(0, 3).join('、') : '';
    const warnings = materialAttachmentWarnings(analysis, [
      taskAttachments.length > 0 && !description ? '任务缺少说明，建议补充这组材料要证明什么' : '',
    ]);

    pushMaterial({
      id: `task:${task.id}`,
      group,
      kind: 'task',
      title,
      summary: truncateText(description || (taskAttachments.length > 0 ? `这组材料来自任务附件，包含 ${taskAttachments.length} 个素材${familyText ? `（${familyText}）` : ''}。` : ''), 160),
      sourceLabel: materialSourceLabel('task', group === 'core' ? '任务材料包' : '关联任务'),
      happenedAt: latestAttachmentTime(taskAttachments, task.updatedAt || task.createdAt || draft.snapshotAt),
      actorName: task.ownerName,
      statusLabel: taskStatusLabel(task.status),
      tags: [
        '任务材料包',
        taskStatusLabel(task.status),
        hasCoreSignal ? '可进汇报' : '过程材料',
        ...materialAttachmentTags(analysis),
      ].filter(Boolean),
      warnings,
      attachments: taskAttachments,
      attachmentGroups: analysis.attachmentGroups,
      duplicateCount: analysis.duplicateAttachmentCount || undefined,
      versionCount: analysis.versionConflictCount || undefined,
      testAttachmentCount: analysis.testAttachmentCount || undefined,
      missingDownloadCount: analysis.missingDownloadCount || undefined,
    });
  }

  for (const [looseKey, attachments] of looseAttachmentMap) {
    const analysis = analyzeMaterialAttachments(attachments);
    const latest = [...attachments].sort((left, right) => (right.createdAt || '').localeCompare(left.createdAt || ''))[0];
    const familyLabel = looseKey === 'image' ? '图片素材' : looseKey;
    duplicateAttachmentCount += analysis.duplicateAttachmentCount;
    testAttachmentCount += analysis.testAttachmentCount;
    looseAttachmentCount += attachments.length;

    pushMaterial({
      id: `loose:${looseKey}`,
      group: 'review',
      kind: 'loose',
      title: looseKey === 'image' ? '图片材料主题待确认' : `${familyLabel}主题待确认`,
      summary: `这些附件暂时缺少清晰业务上下文，先作为待确认材料保留。建议后续绑定到具体任务或补充说明。`,
      sourceLabel: materialSourceLabel('loose', '待确认材料'),
      happenedAt: latest?.createdAt || draft.snapshotAt,
      actorName: latest?.actorName,
      statusLabel: '待确认',
      tags: ['待确认', ...materialAttachmentTags(analysis)],
      warnings: materialAttachmentWarnings(analysis, ['缺少任务/活动归属']),
      attachments,
      attachmentGroups: analysis.attachmentGroups,
      duplicateCount: analysis.duplicateAttachmentCount || undefined,
      versionCount: analysis.versionConflictCount || undefined,
      testAttachmentCount: analysis.testAttachmentCount || undefined,
      missingDownloadCount: analysis.missingDownloadCount || undefined,
    });
  }

  for (const activity of draft.activities) {
    const title = previewActivityTitle(activity);
    const summary = previewActivitySummary(activity);
    const sourceLabel = SOURCE_TYPE_LABELS[activity.sourceType] || activity.sourceType;
    const task = activity.sourceType === 'task_activity' ? taskMap.get(activity.sourceId) : undefined;
    const taskText = task ? `${task.title} ${task.desc || ''}` : '';
    const text = `${title} ${summary} ${taskText}`;

    if (isHistoricalSuggestionActivity(activity)) {
      pushMaterial({
        id: `historical-suggestion:${activity.id}`,
        group: 'review',
        kind: 'activity',
        title,
        summary: truncateText(summary || title, 160),
        sourceLabel: sourceLabel || '历史系统推荐',
        happenedAt: activity.happenedAt,
        actorName: activity.actorName,
        statusLabel: '历史推荐，未确认',
        tags: ['历史系统推荐', '不参与主线'],
        warnings: ['这是旧版系统推测产生的关联，尚未由用户确认为正式证据。'],
        attachments: [],
        attachmentGroups: [],
      });
      continue;
    }

    if (isSystemTraceActivity(activity)) {
      pushMaterial({
        id: `activity:${activity.id}`,
        group: 'system',
        kind: 'system',
        title,
        summary: summary || title,
        sourceLabel: materialSourceLabel('system', sourceLabel),
        happenedAt: activity.happenedAt,
        actorName: activity.actorName,
        tags: ['系统记录'],
        warnings: [],
        attachments: [],
        attachmentGroups: [],
      });
      continue;
    }

    if (activity.sourceType === 'task_activity' && taskMap.has(activity.sourceId)) continue;
    if (!summary && !MATERIAL_ACTIVITY_KEYWORDS.test(text)) continue;
    const group: EventLineMaterialGroupKey = (
      isKeyActivity(activity)
      || activity.sourceType === 'manual_note'
      || activity.sourceType === 'meeting'
      || activity.sourceType === 'support_request'
      || activity.sourceType === 'review'
      || MATERIAL_CORE_KEYWORDS.test(text)
    ) ? 'core' : 'supplement';

    pushMaterial({
      id: `activity:${activity.id}`,
      group,
      kind: 'activity',
      title,
      summary: truncateText(summary || title, 160),
      sourceLabel: materialSourceLabel('activity', sourceLabel),
      happenedAt: activity.happenedAt,
      actorName: activity.actorName,
      tags: [activity.sourceType === 'manual_note' ? '补充说明' : sourceLabel, group === 'core' ? '支撑判断' : '过程记录'].filter(Boolean),
      warnings: [],
      attachments: [],
      attachmentGroups: [],
    });
  }

  for (const key of Object.keys(groups) as EventLineMaterialGroupKey[]) {
    groups[key] = groups[key].sort(materialTimeDesc);
  }

  const meaningfulActivities = [...groups.core, ...groups.supplement].filter((item) => item.kind === 'activity');
  const gaps = [
    meaningfulActivities.some((item) => MATERIAL_ACTIVITY_KEYWORDS.test(`${item.title} ${item.summary}`))
      ? ''
      : '缺关键沟通记录：建议补一次会议纪要、访谈记录或阶段沟通说明。',
    draft.attachments.length > 0 ? '' : '缺原始材料或交付底稿：建议上传可支撑判断的附件。',
    normalizeText(snapshot.eventLine.recentDecision) ? '' : '缺关键决策：建议补“为什么形成今天这个判断”。',
    normalizeText(snapshot.eventLine.nextStep) ? '' : '缺下一步：建议补负责人、动作和时间点。',
    normalizeText(snapshot.eventLine.currentBlocker) ? '' : '缺当前阻塞：建议补这条线现在卡在哪里。',
    duplicateAttachmentCount || testAttachmentCount
      ? `存在待清理素材：${duplicateAttachmentCount ? `重复附件 ${duplicateAttachmentCount} 条` : ''}${duplicateAttachmentCount && testAttachmentCount ? '，' : ''}${testAttachmentCount ? `测试文件 ${testAttachmentCount} 条` : ''}。`
      : '',
    looseAttachmentCount ? `存在待确认素材：${looseAttachmentCount} 个附件缺少任务或活动上下文，建议后续绑定到具体任务。` : '',
  ].filter(Boolean);

  return {
    groups,
    gaps,
    duplicateAttachmentCount,
    testAttachmentCount,
    looseAttachmentCount,
  };
}

function attachmentParsedPreview(att: EventLineReportAttachment) {
  return normalizeText(att.parsedPreview);
}

function hasMeetingSignal(text: string) {
  return /(会议纪要|沟通会|沟通会议|会议|纪要|复盘)/u.test(text);
}

function hasAdminMaterialSignal(text: string) {
  return /(报销|票据|发票|凭证|收据|行政)/u.test(text);
}

function parsedEvidenceSummary(attachments: EventLineReportAttachment[]) {
  const parsed = attachments
    .filter((att) => !isTestAttachment(att))
    .map((att) => attachmentParsedPreview(att))
    .filter(Boolean);
  return truncateText(dedupeStrings(parsed).join(' '), 260);
}

function attachmentBasisTags(attachments: EventLineReportAttachment[]) {
  const tags = new Set<string>();
  for (const attachment of attachments) {
    const title = normalizeText(attachment.title);
    const preview = attachmentParsedPreview(attachment);
    if (hasMeetingSignal(`${title} ${preview}`)) tags.add('来自会议纪要');
    if (isImageAttachment(attachment)) tags.add(hasAdminMaterialSignal(`${title} ${preview}`) ? '来自票据 OCR' : '来自图片证据');
    if (attachment.documentId) tags.add(attachment.parseStatus === 'ready' ? '数据中心已解析' : '待解析');
  }
  return Array.from(tags);
}

function timelineNodeTime(attachments: EventLineReportAttachment[], fallback?: string | null) {
  const latest = latestAttachmentTime(attachments, fallback || '');
  return latest || fallback || '';
}

function timelineNodeSummary({
  title,
  description,
  attachments,
  kind,
}: {
  title: string;
  description: string;
  attachments: EventLineReportAttachment[];
  kind: EventLineTimelineNodeKind;
}) {
  const evidence = parsedEvidenceSummary(attachments);
  if (evidence) {
    if (kind === 'meeting_material') return truncateText(evidence, 220);
    if (kind === 'admin_material') return truncateText(`${title}已形成材料归档。${evidence}`, 220);
    return truncateText(evidence, 220);
  }
  if (description) return truncateText(description, 220);
  if (attachments.length > 0) {
    const family = attachmentFamilySummary(attachments).detailedText;
    return `这一步归集了 ${attachments.length} 个附件，主要包括${family}，可在节点内预览或下载。`;
  }
  return '这一步已进入事件线，后续可继续补充任务说明、会议纪要或附件依据。';
}

function deriveEventLineTimelineModel(snapshot: EventLineReportSnapshot, draft: ReportDraft): EventLineTimelineModel {
  const taskMap = new Map((draft.tasks || []).map((task) => [task.id, task]));
  const attachmentByTask = new Map<string, EventLineReportAttachment[]>();
  const looseAttachments: EventLineReportAttachment[] = [];
  for (const attachment of draft.attachments || []) {
    const taskId = normalizeText(attachment.taskId);
    if (taskId && taskMap.has(taskId)) {
      const list = attachmentByTask.get(taskId) || [];
      list.push(attachment);
      attachmentByTask.set(taskId, list);
    } else {
      looseAttachments.push(attachment);
    }
  }

  const activityIdsByTask = new Map<string, string[]>();
  const systemNodes: EventLineTimelineNode[] = [];
  for (const activity of draft.activities || []) {
    if (isHistoricalSuggestionActivity(activity)) continue;
    const metadata = activity.metadata || {};
    const metadataTaskId = normalizeText((metadata as Record<string, unknown>).taskId as string | undefined);
    const taskId = activity.sourceType === 'task_activity' ? normalizeText(activity.sourceId) : metadataTaskId;
    if (taskId) {
      const ids = activityIdsByTask.get(taskId) || [];
      ids.push(activity.id);
      activityIdsByTask.set(taskId, ids);
    }
    if (isSystemTraceActivity(activity)) {
      systemNodes.push({
        id: `system:${activity.id}`,
        kind: 'system',
        title: previewActivityTitle(activity),
        time: activity.happenedAt,
        summary: previewActivitySummary(activity) || previewActivityTitle(activity),
        sourceActivityIds: [activity.id],
        attachments: [],
        evidenceSummary: '',
        warnings: [],
        tags: ['系统痕迹'],
        actorName: activity.actorName,
      });
    }
  }

  const mainNodes: EventLineTimelineNode[] = [];
  const reviewNodes: EventLineTimelineNode[] = [];

  if (normalizeText(snapshot.eventLine.summary) || normalizeText(snapshot.eventLine.intent)) {
    mainNodes.push({
      id: `event-line:${snapshot.eventLine.id}:overview`,
      kind: 'project_milestone',
      title: '事件线建立',
      time: snapshot.eventLine.createdAt || draft.snapshotAt,
      summary: truncateText(normalizeText(snapshot.eventLine.intent) || normalizeText(snapshot.eventLine.summary), 220),
      sourceActivityIds: [],
      attachments: [],
      evidenceSummary: '',
      warnings: [],
      tags: ['事件线建立'],
      actorName: snapshot.eventLine.ownerName,
      ownerName: snapshot.eventLine.ownerName,
    });
  }

  for (const task of draft.tasks || []) {
    const title = normalizeText(task.title);
    if (!title) continue;
    const taskLike = task as unknown as Record<string, unknown>;
    const description = normalizeText(task.desc || (typeof taskLike.description === 'string' ? taskLike.description : undefined));
    const attachments = attachmentByTask.get(task.id) || [];
    const contextText = `${title} ${description} ${attachments.map((att) => `${att.title} ${attachmentParsedPreview(att)}`).join(' ')}`;
    const nonTestAttachments = attachments.filter((att) => !isTestAttachment(att));
    const hasOnlyTest = attachments.length > 0 && nonTestAttachments.length === 0;
    const kind: EventLineTimelineNodeKind = hasAdminMaterialSignal(contextText)
      ? 'admin_material'
      : hasMeetingSignal(contextText)
        ? 'meeting_material'
        : 'task_bundle';
    const node: EventLineTimelineNode = {
      id: `task:${task.id}`,
      kind,
      title,
      time: timelineNodeTime(attachments, task.updatedAt || task.createdAt || draft.snapshotAt),
      summary: timelineNodeSummary({ title, description, attachments: nonTestAttachments, kind }),
      sourceTaskId: task.id,
      sourceActivityIds: activityIdsByTask.get(task.id) || [],
      attachments,
      evidenceSummary: parsedEvidenceSummary(nonTestAttachments),
      warnings: [
        hasOnlyTest ? '该任务下只有疑似测试素材，未纳入主线判断。' : '',
        attachments.some((att) => att.documentId && att.parseStatus !== 'ready') ? '部分附件仍待数据中心解析完成。' : '',
      ].filter(Boolean),
      tags: [
        kind === 'meeting_material' ? '会议/复盘节点' : kind === 'admin_material' ? '行政材料' : '任务节点',
        ...attachmentBasisTags(nonTestAttachments),
        attachments.length > 0 ? `附件 ${attachments.length}` : '',
        taskStatusLabel(task.status),
      ].filter(Boolean),
      actorName: task.creatorName,
      ownerName: task.ownerName,
    };
    if (hasOnlyTest) reviewNodes.push(node);
    else mainNodes.push(node);
  }

  const looseGroups = new Map<string, EventLineReportAttachment[]>();
  for (const attachment of looseAttachments) {
    const key = isImageAttachment(attachment) ? 'image' : normalizeAttachmentName(attachment.title).replace(/\(\d+\)(?=\.[^.]+$)/, '');
    const list = looseGroups.get(key) || [];
    list.push(attachment);
    looseGroups.set(key, list);
  }
  for (const [key, attachments] of looseGroups) {
    const nonTestAttachments = attachments.filter((att) => !isTestAttachment(att));
    const latest = [...attachments].sort((left, right) => (right.createdAt || '').localeCompare(left.createdAt || ''))[0];
    const contextText = attachments.map((att) => `${att.title} ${attachmentParsedPreview(att)}`).join(' ');
    const kind: EventLineTimelineNodeKind = hasAdminMaterialSignal(contextText)
      ? 'admin_material'
      : hasMeetingSignal(contextText)
        ? 'meeting_material'
        : 'attachment_bundle';
    const node: EventLineTimelineNode = {
      id: `loose:${key}`,
      kind,
      title: kind === 'meeting_material'
        ? '会议材料主题待确认'
        : kind === 'admin_material'
          ? '行政材料主题待确认'
          : latest && isImageAttachment(latest) ? '图片材料主题待确认' : `${latest?.title || key}主题待确认`,
      time: latest?.createdAt || draft.snapshotAt,
      summary: timelineNodeSummary({
        title: latest?.title || '待确认素材',
        description: '',
        attachments: nonTestAttachments,
        kind,
      }),
      sourceActivityIds: [],
      attachments,
      evidenceSummary: parsedEvidenceSummary(nonTestAttachments),
      warnings: [
        '缺少任务/活动归属。',
        attachments.some((att) => !att.documentId) ? '部分附件尚未完成资料库解析。' : '',
        attachments.some((att) => att.documentId && att.parseStatus !== 'ready') ? '部分附件仍待数据中心解析完成。' : '',
      ].filter(Boolean),
      tags: ['待确认', ...attachmentBasisTags(nonTestAttachments), attachments.length > 0 ? `附件 ${attachments.length}` : ''].filter(Boolean),
      actorName: latest?.actorName,
    };
    if (kind === 'meeting_material' || kind === 'admin_material') mainNodes.push(node);
    else reviewNodes.push(node);
  }

  const nonSystemActivities = (draft.activities || [])
    .filter((activity) => !isHistoricalSuggestionActivity(activity))
    .filter((activity) => !isSystemTraceActivity(activity))
    .filter((activity) => !(activity.sourceType === 'task_activity' && taskMap.has(activity.sourceId)))
    .filter((activity) => previewActivitySummary(activity) || MATERIAL_ACTIVITY_KEYWORDS.test(`${previewActivityTitle(activity)} ${previewActivitySummary(activity)}`));
  for (const activity of nonSystemActivities) {
    mainNodes.push({
      id: `activity:${activity.id}`,
      kind: activity.sourceType === 'meeting' ? 'meeting_material' : 'project_milestone',
      title: previewActivityTitle(activity),
      time: activity.happenedAt,
      summary: truncateText(previewActivitySummary(activity) || previewActivityTitle(activity), 220),
      sourceActivityIds: [activity.id],
      attachments: [],
      evidenceSummary: '',
      warnings: [],
      tags: [SOURCE_TYPE_LABELS[activity.sourceType] || activity.sourceType, '关键记录'],
      actorName: activity.actorName,
    });
  }

  const byTime = (left: EventLineTimelineNode, right: EventLineTimelineNode) => (left.time || '').localeCompare(right.time || '');
  return {
    mainNodes: mainNodes.sort(byTime),
    reviewNodes: reviewNodes.sort(byTime),
    systemNodes: systemNodes.sort(byTime),
  };
}

function normalizeBackendTimelineNode(node: BackendEventLineTimelineNode): EventLineTimelineNode {
  const sourceTaskIds = Array.isArray(node.sourceTaskIds)
    ? node.sourceTaskIds.filter(Boolean)
    : (node.sourceTaskId ? [node.sourceTaskId] : []);
  return {
    ...node,
    time: node.time || '',
    summary: node.summary || '',
    sourceTaskIds,
    sourceTaskId: node.sourceTaskId || sourceTaskIds[0] || '',
    sourceActivityIds: Array.isArray(node.sourceActivityIds) ? node.sourceActivityIds : [],
    attachments: Array.isArray(node.attachments) ? node.attachments : [],
    evidenceSummary: node.evidenceSummary || '',
    warnings: Array.isArray(node.warnings) ? node.warnings : [],
    tags: Array.isArray(node.tags) ? node.tags : [],
  };
}

function buildEventLineTimelineModel(snapshot: EventLineReportSnapshot, draft: ReportDraft): EventLineTimelineModel {
  const backendNodes = (snapshot.timelineNodes || [])
    .filter((node): node is BackendEventLineTimelineNode => Boolean(node && node.id && node.title))
    .map(normalizeBackendTimelineNode);
  if (backendNodes.length === 0) {
    return deriveEventLineTimelineModel(snapshot, draft);
  }
  const byTime = (left: EventLineTimelineNode, right: EventLineTimelineNode) => (left.time || '').localeCompare(right.time || '');
  // P0 · 主线还原只留有叙事价值的节点 kind:
  //   project_start / material_intake / project_review / project_milestone / key_turning_point
  // 砍掉 continuing_task / admin_archive: 这些是任务流水, 属于"按任务查看"管辖,
  // 留在主线只会让用户在 N 张卡片里找不到真正的转折点。
  const MAIN_KIND_BLACKLIST = new Set(['needs_review', 'system_trace', 'continuing_task', 'admin_archive']);
  return {
    mainNodes: backendNodes
      .filter((node) => !MAIN_KIND_BLACKLIST.has(node.kind))
      .sort(byTime),
    reviewNodes: backendNodes
      .filter((node) => node.kind === 'needs_review')
      .sort(byTime),
    systemNodes: backendNodes
      .filter((node) => node.kind === 'system_trace')
      .sort(byTime),
  };
}

 /* ------------------------------------------------------------------ */
/*  DocContentViewer — loads and displays document text content         */
/* ------------------------------------------------------------------ */

/** Map file extension to a display label + color for the file-type badge */
function fileTypeBadge(filename: string): { label: string; color: string; bg: string } {
  const ext = (filename.split('.').pop() || '').toLowerCase();
  switch (ext) {
    case 'doc': case 'docx': return { label: 'Word', color: '#2B579A', bg: '#E8EEF7' };
    case 'xls': case 'xlsx': return { label: 'Excel', color: '#217346', bg: '#E2F0E8' };
    case 'ppt': case 'pptx': return { label: 'PPT', color: '#D24726', bg: '#FCEAE5' };
    case 'pdf': return { label: 'PDF', color: '#B30B00', bg: '#FDE8E7' };
    case 'txt': case 'md': return { label: 'TXT', color: '#6B7280', bg: '#F3F4F6' };
    case 'jpg': case 'jpeg': case 'png': case 'gif': case 'webp': return { label: ext.toUpperCase(), color: '#7C3AED', bg: '#EDE9FE' };
    default: return { label: ext.toUpperCase() || '文件', color: '#6B7280', bg: '#F3F4F6' };
  }
}

function DocContentViewer({ att, backendBaseUrl }: { att: EventLineReportAttachment; backendBaseUrl: string }) {
  const [summary, setSummary] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const badge = fileTypeBadge(att.title);
  const downloadUrl = resolveAttachmentUrl(att, backendBaseUrl);
  const openUrl = resolveAttachmentOpenUrl(att, backendBaseUrl);
  const tags = attachmentDisplayTags(att);

  useEffect(() => {
    if (normalizeText(att.parsedPreview)) {
      setSummary(att.parsedPreview || null);
      setLoading(false);
      return;
    }
    // Try text-content first, fall back to ocr-summary
    void fetch(`${backendBaseUrl}/api/public/task-attachments/${att.id}/text-content`)
      .then((r) => r.json())
      .then((data: { text?: string; unsupported?: boolean }) => {
        const text = (data.text || '').trim();
        if (text && !text.includes('提取失败') && !text.includes('No module') && !data.unsupported) {
          setSummary(text);
        } else {
          // Fall back to ocr-summary
          return fetch(`${backendBaseUrl}/api/public/task-attachments/${att.id}/ocr-summary`)
            .then((r2) => r2.json())
            .then((ocr: { summary?: string; unsupported?: boolean }) => {
              if (ocr.summary && !ocr.unsupported) {
                setSummary(ocr.summary);
              } else {
                setSummary(null);
              }
            });
        }
      })
      .catch(() => setSummary(null))
      .finally(() => setLoading(false));
  }, [att.id, att.parsedPreview, backendBaseUrl]);

  return (
    <div className="rounded-lg border border-gray-200 bg-white overflow-hidden">
      {/* File header · 极简 */}
      <div className="flex items-center gap-3 px-3.5 py-2.5 border-b border-gray-100">
        <div
          className="flex-shrink-0 flex items-center justify-center rounded w-8 h-8 text-[9px] font-bold"
          style={{ backgroundColor: badge.bg, color: badge.color }}
        >
          {badge.label}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[12.5px] font-medium text-gray-900 truncate">{att.title}</p>
          <div className="mt-0.5 flex items-baseline gap-2 text-[10px] text-gray-400">
            {tags.map((tag) => (
              <span
                key={tag}
                className={tag === '已解析' ? 'text-emerald-600' : tag === '待确认' ? 'text-amber-600' : 'text-gray-500'}
              >
                {tag}
              </span>
            ))}
            <span className="tabular-nums">{fileSizeLabel(att.sizeBytes)}</span>
          </div>
        </div>
        {openUrl && (
          <a
            href={openUrl}
            target="_blank"
            rel="noreferrer"
            title="在浏览器中打开"
            className="flex-shrink-0 inline-flex h-7 w-7 items-center justify-center rounded-md border border-gray-200 bg-white text-gray-500 transition-all hover:border-gray-300 hover:text-gray-900 hover:bg-gray-50"
          >
            <ExternalLink size={11} strokeWidth={2} />
          </a>
        )}
        {downloadUrl && (
          <a
            href={downloadUrl}
            download={att.title}
            title="下载文件"
            className="flex-shrink-0 inline-flex h-7 w-7 items-center justify-center rounded-md border border-gray-200 bg-white text-gray-500 transition-all hover:border-gray-300 hover:text-gray-900 hover:bg-gray-50"
          >
            <Download size={11} strokeWidth={2} />
          </a>
        )}
      </div>
      {/* AI summary */}
      <div className="px-3.5 py-3 bg-gray-50/40">
        {loading ? (
          <div className="flex items-center gap-2 text-[10.5px] text-gray-400">
            <RefreshCw size={10} strokeWidth={1.8} className="animate-spin" />
            <span>正在提取文档摘要…</span>
          </div>
        ) : summary ? (
          <pre className="max-h-[600px] overflow-y-auto whitespace-pre-wrap text-[11.5px] leading-5 text-gray-600 font-sans">{summary}</pre>
        ) : (
          <p className="text-[10.5px] text-gray-300">暂无文档摘要</p>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  ImageWithOcr — image preview with OCR summary below                */
/* ------------------------------------------------------------------ */

function ImageWithOcr({ att, backendBaseUrl }: { att: EventLineReportAttachment; backendBaseUrl: string }) {
  const [ocrText, setOcrText] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const imageUrl = resolveAttachmentUrl(att, backendBaseUrl);
  const openUrl = resolveAttachmentOpenUrl(att, backendBaseUrl);
  const tags = attachmentDisplayTags(att);

  useEffect(() => {
    if (normalizeText(att.parsedPreview)) {
      setOcrText(att.parsedPreview || null);
      setLoading(false);
      return;
    }
    void fetch(`${backendBaseUrl}/api/public/task-attachments/${att.id}/ocr-summary`)
      .then((r) => r.json())
      .then((data: { summary?: string; unsupported?: boolean }) => {
        if (data.summary && !data.unsupported) {
          setOcrText(data.summary);
        } else {
          setOcrText(null);
        }
      })
      .catch(() => setOcrText(null))
      .finally(() => setLoading(false));
  }, [att.id, att.parsedPreview, backendBaseUrl]);

  return (
    <div className="rounded-xl border border-gray-200 overflow-hidden bg-gray-50">
      {imageUrl ? (
        <img
          src={imageUrl}
          alt={att.title}
          className="w-full object-contain max-h-[300px]"
        />
      ) : (
        <div className="flex h-40 items-center justify-center bg-gray-100 text-[10px] text-gray-400">
          暂无图片预览地址
        </div>
      )}
      <div className="px-2 py-1.5">
        <p className="text-[10px] text-gray-500 truncate">{att.title}</p>
        <div className="mt-1 flex flex-wrap items-center gap-1">
          {tags.map((tag) => (
            <span key={tag} className={`rounded-full px-1.5 py-0.5 text-[9px] font-bold ${tag === '已解析' ? 'bg-emerald-50 text-emerald-700' : tag === '待确认' ? 'bg-amber-50 text-amber-700' : 'bg-blue-50 text-[#4B66D8]'}`}>
              {tag}
            </span>
          ))}
          {openUrl ? (
            <a href={openUrl} target="_blank" rel="noreferrer" className="ml-auto inline-flex items-center gap-1 rounded-full bg-white px-2 py-0.5 text-[9px] font-bold text-[#4B66D8]">
              <ExternalLink size={10} /> 打开原文
            </a>
          ) : null}
        </div>
        {loading ? (
          <div className="mt-1 flex items-center gap-1">
            <div className="h-2 w-2 animate-spin rounded-full border border-gray-300 border-t-[#5B7BFE]" />
            <span className="text-[9px] text-gray-400">识别中…</span>
          </div>
        ) : ocrText ? (
          <p className="mt-1 text-[10px] leading-4 text-gray-400">{ocrText}</p>
        ) : null}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

type Props = {
  eventLineId: string;
  backendBaseUrl: string;
  onClose: () => void;
  onOpenSavedReport?: (artifact: ReportArtifactSummary) => void;
  onOpenTask?: (task: Task) => void;
  onDownloadReport?: (localPath: string, fileName: string) => Promise<void>;
};

function taskCandidateAlreadyIncluded(
  candidate: EventLineTaskCandidate,
  eventLineId: string,
  cloudEventLineId?: string | null,
) {
  return Boolean(
    candidate.alreadyReferenced
    || candidate.eventLineId === eventLineId
    || (cloudEventLineId && candidate.eventLineId === cloudEventLineId)
  );
}

export default function EventLineReportPanel({ eventLineId, backendBaseUrl, onClose, onOpenSavedReport, onOpenTask, onDownloadReport }: Props) {
  const [snapshot, setSnapshot] = useState<EventLineReportSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [materialActionError, setMaterialActionError] = useState<string | null>(null);
  const [uploadingMaterial, setUploadingMaterial] = useState(false);
  const [retryingAttachmentId, setRetryingAttachmentId] = useState<string | null>(null);
  const [retryingFailedMaterials, setRetryingFailedMaterials] = useState(false);
  const [materialUploadOpen, setMaterialUploadOpen] = useState(false);
  const [materialUploadTaskId, setMaterialUploadTaskId] = useState('');
  const [materialUploadName, setMaterialUploadName] = useState('');
  const [materialUploadPurpose, setMaterialUploadPurpose] = useState('');
  const [materialUploadFile, setMaterialUploadFile] = useState<File | null>(null);
  const [expandedEvidenceTaskIds, setExpandedEvidenceTaskIds] = useState<Set<string>>(new Set());
  const eventLineVersionRef = useRef(1);

  /* Local editable draft — built from immutable cloud snapshot */
  const [draft, setDraft] = useState<ReportDraft | null>(null);

  /* Per-activity toggle: which activities have docs expanded / images expanded */
  const [docsExpandedActivities, setDocsExpandedActivities] = useState<Set<string>>(new Set());
  const [imagesExpandedActivities, setImagesExpandedActivities] = useState<Set<string>>(new Set());
  const [showSystemTraces, setShowSystemTraces] = useState(false);
  const [viewMode, setViewMode] = useState<'context' | 'milestones' | 'evidence' | 'timeline' | 'blueprint' | 'report'>('context');

  /* P1 主线还原 LLM 叙事 */
  const [timelineNarrative, setTimelineNarrative] = useState<EventLineTimelineNarrative | null>(null);
  const [narrativeRegenerating, setNarrativeRegenerating] = useState(false);
  const [narrativeError, setNarrativeError] = useState<string | null>(null);

  /* 人工确认的主线事实；AI 只把草稿放回编辑框。 */
  const [goalText, setGoalText] = useState('');
  const [backgroundText, setBackgroundText] = useState('');
  const [goalAction, setGoalAction] = useState<'polish' | 'save' | null>(null);
  const [goalMessage, setGoalMessage] = useState<string | null>(null);
  const [goalError, setGoalError] = useState<string | null>(null);
  const [goalFailedAction, setGoalFailedAction] = useState<'polish' | 'save' | null>(null);
  const [backgroundAction, setBackgroundAction] = useState<'draft' | 'save' | null>(null);
  const [backgroundMessage, setBackgroundMessage] = useState<string | null>(null);
  const [backgroundError, setBackgroundError] = useState<string | null>(null);
  const [backgroundFailedAction, setBackgroundFailedAction] = useState<'draft' | 'save' | null>(null);
  const [backgroundDraftCitations, setBackgroundDraftCitations] = useState<EventLineDraft['citations']>([]);
  const [backgroundDraftWarning, setBackgroundDraftWarning] = useState<string | null>(null);
  const [expandedNarrativeNodes, setExpandedNarrativeNodes] = useState<Set<string>>(new Set());
  const [taskCandidates, setTaskCandidates] = useState<EventLineTaskCandidate[]>([]);
  const [selectedTaskCandidateIds, setSelectedTaskCandidateIds] = useState<Set<string>>(new Set());
  const [taskSearch, setTaskSearch] = useState('');
  const [taskSearchScope, setTaskSearchScope] = useState<'client' | 'organization'>('client');
  const [taskCandidateState, setTaskCandidateState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [taskCandidateError, setTaskCandidateError] = useState<string | null>(null);
  const [linkingTaskId, setLinkingTaskId] = useState<string | null>(null);
  const [taskLinkError, setTaskLinkError] = useState<string | null>(null);
  const [taskLinkMessage, setTaskLinkMessage] = useState<string | null>(null);
  const [milestoneTaskId, setMilestoneTaskId] = useState<string | null>(null);
  const [milestoneError, setMilestoneError] = useState<string | null>(null);
  const [reportArtifacts, setReportArtifacts] = useState<ReportArtifactSummary[]>([]);
  const [legacyReportRuns, setLegacyReportRuns] = useState<ReportRunSummary[]>([]);
  const [reportListState, setReportListState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [reportListError, setReportListError] = useState<string | null>(null);
  const [reportActionId, setReportActionId] = useState<string | null>(null);
  const [showLegacyReports, setShowLegacyReports] = useState(false);
  const [reportDraft, setReportDraft] = useState<ReportRunSummary | null>(null);
  const [reportDraftState, setReportDraftState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [reportDraftError, setReportDraftError] = useState<string | null>(null);
  const [snapshotRefreshError, setSnapshotRefreshError] = useState<string | null>(null);
  const reportLoadIdRef = useRef(0);
  const reportDraftLoadIdRef = useRef(0);

  /* 加载已有叙事 */
  useEffect(() => {
    if (!eventLineId) {
      setTimelineNarrative(null);
      return;
    }
    let cancelled = false;
    void getEventLineTimelineNarrative(eventLineId)
      .then((data) => { if (!cancelled) setTimelineNarrative(data); })
      .catch(() => { if (!cancelled) setTimelineNarrative(null); });
    return () => { cancelled = true; };
  }, [eventLineId]);

  const handleRegenerateNarrative = async () => {
    if (!eventLineId || narrativeRegenerating || snapshot?.canEdit === false) return;
    setNarrativeRegenerating(true);
    setNarrativeError(null);
    try {
      const next = await regenerateEventLineTimelineNarrative(eventLineId, 'manual');
      setTimelineNarrative(next);
    } catch (err) {
      setNarrativeError(err instanceof Error ? err.message : '生成失败');
    } finally {
      setNarrativeRegenerating(false);
    }
  };

  const renderNarrativeNode = (node: EventLineNarrativeNode, index: number) => {
    const rankText = String(index + 1).padStart(2, '0');
    const confColor =
      node.confidence === 'high'
        ? 'text-emerald-700 bg-emerald-50 ring-emerald-200'
        : node.confidence === 'low'
          ? 'text-rose-700 bg-rose-50 ring-rose-200'
          : 'text-amber-700 bg-amber-50 ring-amber-200';
    const timeLabel = node.time ? (node.time.slice(0, 10) || node.time) : '时间待补';
    const expanded = expandedNarrativeNodes.has(node.id);
    const linkedTasks = [
      ...(snapshot?.tasks || []),
      ...(snapshot?.referencedTasks || []),
    ].filter((task) => node.linkedTaskIds.includes(task.id));
    const linkedAttachments = (snapshot?.attachments || []).filter((attachment) => node.linkedAttachmentIds.includes(attachment.id));
    const linkedActivities = (snapshot?.activities || []).filter((activity) => node.linkedActivityIds?.includes(activity.id));
    const evidenceCount = linkedTasks.length + linkedAttachments.length + linkedActivities.length;
    return (
      <article key={node.id} className="group relative pl-8">
        <div className="absolute left-0 top-2 bottom-2 w-[2px] rounded-full bg-gray-900" />
        <div className="flex items-baseline gap-4 mb-2">
          <span className="text-[28px] leading-none font-extralight tracking-tighter text-gray-200">
            {rankText}
          </span>
          <div className="flex-1 min-w-0">
            <div className="flex items-baseline gap-3 flex-wrap">
              <h4 className="text-[16px] font-semibold leading-snug text-gray-900">{node.title}</h4>
              <span className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium tracking-wide uppercase ring-1 ${confColor}`}>
                <span className={`h-1 w-1 rounded-full ${node.confidence === 'high' ? 'bg-emerald-500' : node.confidence === 'low' ? 'bg-rose-500' : 'bg-amber-500'}`} />
                {node.confidence}
              </span>
              <span className="text-[11px] text-gray-400 tabular-nums">{timeLabel}</span>
            </div>
            <p className="mt-2 text-[14px] leading-relaxed text-gray-700">{node.narrative}</p>
            {node.evidenceSummary && (
              <p className="mt-2 border-l border-gray-200 pl-3 text-[11px] leading-5 text-gray-500">
                证据：{node.evidenceSummary}
              </p>
            )}
            {node.evidenceGaps && node.evidenceGaps.length > 0 && (
              <p className="mt-1.5 text-[10.5px] leading-5 text-amber-700">
                证据缺口：{node.evidenceGaps.join('；')}
              </p>
            )}
            {evidenceCount > 0 && (
              <div className="mt-3">
                <button
                  type="button"
                  onClick={() => setExpandedNarrativeNodes((current) => {
                    const next = new Set(current);
                    if (next.has(node.id)) next.delete(node.id);
                    else next.add(node.id);
                    return next;
                  })}
                  className="text-[10.5px] font-medium text-gray-500 hover:text-gray-900"
                >
                  {expanded ? '收起证据' : `查看证据（${evidenceCount}）`}
                </button>
                {expanded && (
                  <div className="mt-2 space-y-1.5 rounded-md border border-gray-100 bg-gray-50/70 p-2.5">
                    {linkedTasks.map((task) => (
                      <button
                        key={`node-task:${task.id}`}
                        type="button"
                        onClick={() => onOpenTask?.(task)}
                        className="flex w-full items-center justify-between gap-3 rounded px-2 py-1.5 text-left text-[11px] text-gray-700 hover:bg-white"
                      >
                        <span className="truncate">任务 · {task.title}</span>
                        {onOpenTask && <ArrowRight size={11} className="shrink-0 text-gray-400" />}
                      </button>
                    ))}
                    {linkedActivities.map((activity) => (
                      <div key={`node-activity:${activity.id}`} className="rounded px-2 py-1.5 text-[11px] text-gray-600">
                        会议/节点 · {activity.title}
                      </div>
                    ))}
                    {linkedAttachments.map((attachment) => {
                      const openUrl = resolveAttachmentOpenUrl(attachment, backendBaseUrl) || resolveAttachmentUrl(attachment, backendBaseUrl);
                      return openUrl ? (
                        <a key={`node-attachment:${attachment.id}`} href={openUrl} target="_blank" rel="noreferrer" className="flex items-center justify-between gap-3 rounded px-2 py-1.5 text-[11px] text-gray-700 hover:bg-white">
                          <span className="truncate">材料 · {attachment.title}</span>
                          <ExternalLink size={11} className="shrink-0 text-gray-400" />
                        </a>
                      ) : (
                        <div key={`node-attachment:${attachment.id}`} className="rounded px-2 py-1.5 text-[11px] text-gray-500">材料 · {attachment.title}</div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </article>
    );
  };

  /* Fetch immutable snapshot from cloud */
  useEffect(() => {
    setSnapshot(null);
    setDraft(null);
    setError(null);
    setLoading(true);
    setMaterialActionError(null);
    setUploadingMaterial(false);
    setRetryingAttachmentId(null);
    setRetryingFailedMaterials(false);
    setDocsExpandedActivities(new Set());
    setImagesExpandedActivities(new Set());
    setShowSystemTraces(false);
    setViewMode('context');
    setGoalText('');
    setBackgroundText('');
    setGoalAction(null);
    setGoalMessage(null);
    setGoalError(null);
    setGoalFailedAction(null);
    setBackgroundAction(null);
    setBackgroundMessage(null);
    setBackgroundError(null);
    setBackgroundFailedAction(null);
    setBackgroundDraftCitations([]);
    setBackgroundDraftWarning(null);
    setExpandedNarrativeNodes(new Set());
    setTaskCandidates([]);
    setSelectedTaskCandidateIds(new Set());
    setTaskSearch('');
    setTaskSearchScope('client');
    setTaskCandidateState('idle');
    setTaskCandidateError(null);
    setLinkingTaskId(null);
    setTaskLinkError(null);
    setTaskLinkMessage(null);
    setMilestoneTaskId(null);
    setMilestoneError(null);
    setReportArtifacts([]);
    setLegacyReportRuns([]);
    setReportListState('idle');
    setReportListError(null);
    setReportActionId(null);
    setShowLegacyReports(false);
    setReportDraft(null);
    setReportDraftState('idle');
    setReportDraftError(null);
    setSnapshotRefreshError(null);
  }, [eventLineId]);

  // S4.3 fix: 切事件线时, 旧 loadSnapshot 请求可能在新 eventLineId 已生效后才返回,
  // 把旧数据 setState 到新页面 → 用户看到串台数据. 用 currentLoadIdRef 追踪本次 load 编号,
  // 异步返回时若编号已变 (说明切了), 丢弃结果.
  const currentLoadIdRef = useRef(0);

  const loadSnapshot = useCallback(async (options?: { silent?: boolean; minimumVersion?: number }) => {
    const loadId = ++currentLoadIdRef.current;
    const targetEventLineId = eventLineId;
    if (!options?.silent) {
      setLoading(true);
      setError(null);
    }
    try {
      const data = await getEventLineReportSnapshot(targetEventLineId);
      // S4.3 fix: 旧请求返回时新 eventLineId 已变 → 丢弃
      if (loadId !== currentLoadIdRef.current) return false;
      const responseVersion = Math.max(1, Number(data.eventLine.version || 1));
      const minimumVersion = Math.max(
        1,
        Number(options?.minimumVersion || 1),
        Number(eventLineVersionRef.current || 1),
      );
      if (responseVersion < minimumVersion) {
        if (options?.silent) {
          setSnapshotRefreshError(null);
        }
        return false;
      }
      setSnapshot(data);
      setSnapshotRefreshError(null);
      eventLineVersionRef.current = responseVersion;
      if (!options?.silent) {
        setGoalText(data.eventLine.intent ?? '');
        setBackgroundText(data.eventLine.summary ?? '');
      }
      setDraft((prev) => {
        const prevEditMap = new Map<string, { editedTitle?: string; editedSummary?: string }>();
        if (options?.silent && prev) {
          for (const a of prev.activities) {
            if (a.editedTitle || a.editedSummary) {
              prevEditMap.set(a.id, { editedTitle: a.editedTitle, editedSummary: a.editedSummary });
            }
          }
        }
        return {
          eventLineName: options?.silent && prev ? prev.eventLineName : data.eventLine.name,
          summary: options?.silent && prev ? prev.summary : data.eventLine.summary ?? '',
          activities: (data.activities || []).map((a: EventLineActivity) => ({
            ...a,
            ...(prevEditMap.get(a.id) || {}),
          })),
          attachments: [...(data.attachments || [])],
          tasks: [...(data.tasks || [])],
          participantNames: [...(data.participantNames || [])],
          snapshotAt: data.snapshotAt || new Date().toISOString(),
          timelineNodes: [...(data.timelineNodes || [])].map(normalizeBackendTimelineNode),
        };
      });
      return true;
    } catch (err) {
      if (loadId !== currentLoadIdRef.current) return false;
      const message = err instanceof Error ? err.message : '加载事件线快照失败';
      if (!options?.silent) {
        setError(message);
      } else {
        setSnapshotRefreshError(message);
      }
      return false;
    } finally {
      if (loadId === currentLoadIdRef.current && !options?.silent) {
        setLoading(false);
      }
    }
  }, [eventLineId]);

  const applyUpdatedEventLine = useCallback((updated: EventLineReportSnapshot['eventLine']) => {
    const incomingVersion = Math.max(1, Number(updated.version || 1));
    if (incomingVersion < eventLineVersionRef.current) return;
    eventLineVersionRef.current = incomingVersion;
    setSnapshot((current) => current
      ? { ...current, eventLine: { ...current.eventLine, ...updated } }
      : current);
  }, []);

  const applyMilestoneMutationResult = useCallback((result: {
    eventLine: EventLineReportSnapshot['eventLine'];
    task: Task;
    activity?: EventLineActivity | null;
  }) => {
    applyUpdatedEventLine(result.eventLine);
    setSnapshot((current) => {
      if (!current) return current;
      const nextTasks = current.tasks.some((item) => item.id === result.task.id)
        ? current.tasks.map((item) => (item.id === result.task.id ? { ...item, ...result.task } : item))
        : [...current.tasks, result.task];
      const nextActivities = result.activity
        ? (
            current.activities.some((item) => item.id === result.activity?.id)
              ? current.activities.map((item) => (
                  item.id === result.activity?.id ? { ...item, ...result.activity } : item
                ))
              : [...current.activities, result.activity]
          )
        : current.activities;
      return {
        ...current,
        eventLine: { ...current.eventLine, ...result.eventLine },
        tasks: nextTasks,
        activities: nextActivities,
      };
    });
    setDraft((current) => {
      if (!current) return current;
      const nextTasks = current.tasks.some((item) => item.id === result.task.id)
        ? current.tasks.map((item) => (item.id === result.task.id ? { ...item, ...result.task } : item))
        : [...current.tasks, result.task];
      const nextActivities = result.activity
        ? (
            current.activities.some((item) => item.id === result.activity?.id)
              ? current.activities.map((item) => (
                  item.id === result.activity?.id ? { ...item, ...result.activity } : item
                ))
              : [...current.activities, result.activity]
          )
        : current.activities;
      return { ...current, tasks: nextTasks, activities: nextActivities };
    });
  }, [applyUpdatedEventLine]);

  const handlePolishGoal = useCallback(async () => {
    if (!goalText.trim() || goalAction || snapshot?.canEdit === false) return;
    setGoalAction('polish');
    setGoalMessage(null);
    setGoalError(null);
    setGoalFailedAction(null);
    try {
      const result = await polishEventLineGoal(eventLineId, goalText.trim());
      setGoalText(result.draft);
      setGoalMessage('AI 已生成可编辑草稿；确认内容无误后再保存目标。');
    } catch (err) {
      setGoalError(err instanceof Error ? err.message : 'AI 润色目标失败');
      setGoalFailedAction('polish');
    } finally {
      setGoalAction(null);
    }
  }, [eventLineId, goalAction, goalText, snapshot?.canEdit]);

  const handleSaveGoal = useCallback(async () => {
    if (!goalText.trim() || goalAction || snapshot?.canEdit === false) return;
    setGoalAction('save');
    setGoalMessage(null);
    setGoalError(null);
    setGoalFailedAction(null);
    try {
      const updated = await updateEventLine(eventLineId, {
        expectedVersion: eventLineVersionRef.current,
        intent: goalText.trim(),
        confirmIntent: true,
      });
      applyUpdatedEventLine(updated);
      setGoalMessage('目标已保存。');
    } catch (err) {
      const message = err instanceof Error ? err.message : '目标保存失败';
      if (message.includes('已在其他设备更新') || message.includes('已更新，请刷新')) {
        await loadSnapshot({ silent: true });
        setGoalError('事件线已更新，已刷新最新内容，请确认后重试。');
      } else {
        setGoalError(message);
      }
      setGoalFailedAction('save');
    } finally {
      setGoalAction(null);
    }
  }, [applyUpdatedEventLine, eventLineId, goalAction, goalText, loadSnapshot, snapshot?.canEdit]);

  const handleDraftBackground = useCallback(async () => {
    if (backgroundAction || snapshot?.canEdit === false) return;
    setBackgroundAction('draft');
    setBackgroundMessage(null);
    setBackgroundError(null);
    setBackgroundFailedAction(null);
    try {
      const result = await draftEventLineBackground(eventLineId, backgroundText.trim());
      setBackgroundText(result.draft);
      setBackgroundDraftCitations(result.citations);
      setBackgroundDraftWarning(result.warning || null);
      setBackgroundMessage('AI 已结合目标和项目基础信息整理为可编辑草稿；确认后再保存。');
    } catch (err) {
      setBackgroundError(err instanceof Error ? err.message : 'AI 生成背景失败');
      setBackgroundFailedAction('draft');
    } finally {
      setBackgroundAction(null);
    }
  }, [backgroundAction, backgroundText, eventLineId, snapshot?.canEdit]);

  const handleSaveBackground = useCallback(async () => {
    if (!backgroundText.trim() || backgroundAction || snapshot?.canEdit === false) return;
    setBackgroundAction('save');
    setBackgroundMessage(null);
    setBackgroundError(null);
    setBackgroundFailedAction(null);
    try {
      const updated = await updateEventLine(eventLineId, {
        expectedVersion: eventLineVersionRef.current,
        summary: backgroundText.trim(),
        confirmSummary: true,
      });
      applyUpdatedEventLine(updated);
      setDraft((current) => current ? { ...current, summary: backgroundText.trim() } : current);
      setBackgroundDraftCitations([]);
      setBackgroundDraftWarning(null);
      setBackgroundMessage('背景已保存。');
    } catch (err) {
      const message = err instanceof Error ? err.message : '背景保存失败';
      if (message.includes('已在其他设备更新') || message.includes('已更新，请刷新')) {
        await loadSnapshot({ silent: true });
        setBackgroundError('事件线已更新，已刷新最新内容，请确认后重试。');
      } else {
        setBackgroundError(message);
      }
      setBackgroundFailedAction('save');
    } finally {
      setBackgroundAction(null);
    }
  }, [applyUpdatedEventLine, backgroundAction, backgroundText, eventLineId, loadSnapshot, snapshot?.canEdit]);

  const loadTaskCandidates = useCallback(async () => {
    if (!snapshot?.canEdit || taskCandidateState === 'loading') return;
    setTaskCandidateState('loading');
    setTaskCandidateError(null);
    setTaskLinkError(null);
    setTaskLinkMessage(null);
    try {
      const items = await getEventLineTaskCandidates(eventLineId, {
        q: taskSearch,
        scope: taskSearchScope,
        limit: 60,
      });
      setTaskCandidates(items);
      const cloudEventLineId = snapshot.eventLine.cloudId || snapshot.eventLine.id;
      const selectableIds = new Set(
        items
          .filter((item) => !taskCandidateAlreadyIncluded(item, eventLineId, cloudEventLineId))
          .map((item) => item.id),
      );
      setSelectedTaskCandidateIds((current) => new Set(
        [...current].filter((id) => selectableIds.has(id)),
      ));
      setTaskCandidateState('ready');
    } catch (err) {
      setTaskCandidates([]);
      setSelectedTaskCandidateIds(new Set());
      setTaskCandidateState('error');
      setTaskCandidateError(err instanceof Error ? err.message : '任务查找失败');
    }
  }, [eventLineId, snapshot, taskCandidateState, taskSearch, taskSearchScope]);

  const selectableTaskCandidates = useMemo(() => {
    const cloudEventLineId = snapshot?.eventLine.cloudId || snapshot?.eventLine.id;
    return taskCandidates.filter(
      (candidate) => !taskCandidateAlreadyIncluded(candidate, eventLineId, cloudEventLineId),
    );
  }, [eventLineId, snapshot?.eventLine.cloudId, snapshot?.eventLine.id, taskCandidates]);

  const allTaskCandidatesSelected = selectableTaskCandidates.length > 0
    && selectableTaskCandidates.every((candidate) => selectedTaskCandidateIds.has(candidate.id));

  const toggleTaskCandidateSelection = useCallback((taskId: string) => {
    setSelectedTaskCandidateIds((current) => {
      const next = new Set(current);
      if (next.has(taskId)) next.delete(taskId);
      else next.add(taskId);
      return next;
    });
  }, []);

  const toggleAllTaskCandidates = useCallback(() => {
    setSelectedTaskCandidateIds(
      allTaskCandidatesSelected
        ? new Set()
        : new Set(selectableTaskCandidates.map((candidate) => candidate.id)),
    );
  }, [allTaskCandidatesSelected, selectableTaskCandidates]);

  const handleLinkCandidate = useCallback(async (candidate: EventLineTaskCandidate) => {
    if (!snapshot?.canEdit || linkingTaskId) return;
    const currentCloudId = snapshot.eventLine.cloudId || snapshot.eventLine.id;
    if (
      candidate.relationMode === 'formal'
      && candidate.eventLineId
      && candidate.eventLineId !== currentCloudId
      && candidate.eventLineId !== eventLineId
    ) {
      const approved = window.confirm(`任务“${candidate.title}”已关联“${candidate.eventLineName || '其他事件线'}”，是否改挂到当前事件线？`);
      if (!approved) return;
    }
    setLinkingTaskId(candidate.id);
    setTaskLinkError(null);
    setTaskLinkMessage(null);
    try {
      const result = await linkTaskToEventLine(
        eventLineId,
        candidate.id,
        candidate.taskVersion,
        eventLineVersionRef.current,
        Boolean(
          candidate.relationMode === 'formal'
          && candidate.eventLineId
          && candidate.eventLineId !== currentCloudId
          && candidate.eventLineId !== eventLineId
        ),
      );
      applyUpdatedEventLine(result.eventLine);
      await loadSnapshot();
      await loadTaskCandidates();
      setTaskLinkMessage(
        result.relationMode === 'formal'
          ? result.taskProjectAssigned
            ? '已将本人负责的未归属任务补入当前项目，并纳入本事件线。'
            : '已将本人负责的同项目任务纳入本事件线。'
          : '已作为引用加入事件线，原任务的负责人、项目和事件线归属均未修改。',
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : '引用任务失败';
      if (message.includes('已在其他设备更新') || message.includes('已更新，请刷新')) {
        await loadSnapshot({ silent: true });
        setTaskLinkError('事件线已更新，已刷新最新内容，请确认后重试关联。');
      } else {
        setTaskLinkError(message);
      }
    } finally {
      setLinkingTaskId(null);
    }
  }, [applyUpdatedEventLine, eventLineId, linkingTaskId, loadSnapshot, loadTaskCandidates, snapshot]);

  const handleBatchLinkCandidates = useCallback(async () => {
    if (!snapshot?.canEdit || linkingTaskId || selectedTaskCandidateIds.size === 0) return;
    const currentCloudId = snapshot.eventLine.cloudId || snapshot.eventLine.id;
    const selected = selectableTaskCandidates.filter(
      (candidate) => selectedTaskCandidateIds.has(candidate.id),
    );
    if (selected.length === 0) return;
    const reassigning = selected.filter((candidate) => (
      candidate.relationMode === 'formal'
      && candidate.eventLineId
      && candidate.eventLineId !== currentCloudId
      && candidate.eventLineId !== eventLineId
    ));
    if (reassigning.length > 0) {
      const names = reassigning.slice(0, 3).map((candidate) => `“${candidate.title}”`).join('、');
      const suffix = reassigning.length > 3 ? `等 ${reassigning.length} 条任务` : '';
      const approved = window.confirm(
        `${names}${suffix}已在其他事件线中；批量引用会将这些本人负责的同项目任务改挂到当前事件线，是否继续？`,
      );
      if (!approved) return;
    }

    setLinkingTaskId('__batch__');
    setTaskLinkError(null);
    setTaskLinkMessage(null);
    let formalCount = 0;
    let referenceCount = 0;
    const failures: Array<{ candidate: EventLineTaskCandidate; message: string }> = [];
    try {
      for (const candidate of selected) {
        try {
          const allowReassign = Boolean(
            candidate.relationMode === 'formal'
            && candidate.eventLineId
            && candidate.eventLineId !== currentCloudId
            && candidate.eventLineId !== eventLineId
          );
          const result = await linkTaskToEventLine(
            eventLineId,
            candidate.id,
            candidate.taskVersion,
            eventLineVersionRef.current,
            allowReassign,
          );
          applyUpdatedEventLine(result.eventLine);
          if (result.relationMode === 'formal') formalCount += 1;
          else referenceCount += 1;
        } catch (err) {
          failures.push({
            candidate,
            message: err instanceof Error ? err.message : '引用失败',
          });
        }
      }
      await loadSnapshot();
      await loadTaskCandidates();
      if (failures.length > 0) {
        const succeeded = formalCount + referenceCount;
        const detail = failures.slice(0, 3)
          .map(({ candidate, message }) => `${candidate.title}：${message}`)
          .join('；');
        setTaskLinkError(
          `${succeeded > 0 ? `已成功引用 ${succeeded} 条；` : ''}${failures.length} 条失败。${detail}${failures.length > 3 ? '；其余失败项请刷新后重试' : ''}`,
        );
      } else {
        const parts = [
          formalCount > 0 ? `${formalCount} 条已按细化规则纳入项目与事件线` : '',
          referenceCount > 0 ? `${referenceCount} 条仅作引用、未修改原任务` : '',
        ].filter(Boolean);
        setTaskLinkMessage(`批量引用完成：${parts.join('；')}。`);
      }
    } finally {
      setLinkingTaskId(null);
    }
  }, [
    applyUpdatedEventLine,
    eventLineId,
    linkingTaskId,
    loadSnapshot,
    loadTaskCandidates,
    selectableTaskCandidates,
    selectedTaskCandidateIds,
    snapshot,
  ]);

  const humanMilestoneTaskIds = useMemo(() => new Set(
    (snapshot?.activities || [])
      .filter((activity) => activity.sourceType === 'task_activity' && activity.isKey && activity.keySource === 'human')
      .map((activity) => activity.sourceId),
  ), [snapshot?.activities]);

  const handleToggleMilestone = useCallback(async (task: Task) => {
    if (!snapshot?.eventLine.viewerCapabilities.canSetMilestone || milestoneTaskId) return;
    const nextValue = !humanMilestoneTaskIds.has(task.id);
    setMilestoneTaskId(task.id);
    setMilestoneError(null);
    try {
      const result = await setEventLineTaskMilestone(
        eventLineId,
        task.id,
        nextValue,
        eventLineVersionRef.current,
      );
      applyMilestoneMutationResult(result);
      const confirmedVersion = Math.max(1, Number(result.eventLine.version || eventLineVersionRef.current));
      void loadSnapshot({ silent: true, minimumVersion: confirmedVersion });
    } catch (err) {
      const message = err instanceof Error ? err.message : '里程碑更新失败';
      if (message.includes('已在其他设备更新') || message.includes('已更新，请刷新')) {
        await loadSnapshot({ silent: true });
        setMilestoneError('事件线已更新，已刷新最新内容，请确认后重试设置里程碑。');
      } else {
        setMilestoneError(message);
      }
    } finally {
      setMilestoneTaskId(null);
    }
  }, [applyMilestoneMutationResult, eventLineId, humanMilestoneTaskIds, loadSnapshot, milestoneTaskId, snapshot?.eventLine.viewerCapabilities.canSetMilestone]);

  const goalConfirmed = Boolean(snapshot?.eventLine.intent?.trim());
  const backgroundConfirmed = Boolean(snapshot?.eventLine.summary?.trim());
  const formalInputsReady = goalConfirmed && humanMilestoneTaskIds.size > 0;
  const formalNarrativeReady = timelineNarrative?.outputKind === 'formal_mainline'
    && !timelineNarrative.isStale
    && timelineNarrative.availabilityStatus !== 'blocked';
  const currentEventLineIdentity = snapshot?.eventLine.cloudId || snapshot?.eventLine.id || eventLineId;

  useEffect(() => {
    void loadSnapshot();
  }, [loadSnapshot]);

  useEffect(() => {
    if (!snapshot?.attachments.some(isAttachmentParsePending)) return undefined;
    const timer = window.setInterval(() => {
      void loadSnapshot({ silent: true });
    }, 2000);
    return () => window.clearInterval(timer);
  }, [loadSnapshot, snapshot?.attachments]);

  const openMaterialUpload = useCallback((task?: Task) => {
    setMaterialUploadTaskId(task?.id || '');
    setMaterialUploadName('');
    setMaterialUploadPurpose(
      task ? `补充“${task.title}”的过程证据` : '',
    );
    setMaterialUploadFile(null);
    setMaterialActionError(null);
    setMaterialUploadOpen(true);
  }, []);

  const handleUploadMaterial = useCallback(async () => {
    if (!snapshot?.canEdit || uploadingMaterial || !materialUploadFile) return;
    const title = materialUploadName.trim() || materialUploadFile.name;
    const purpose = materialUploadPurpose.trim();
    if (!purpose) {
      setMaterialActionError('请说明这份材料的用途。');
      return;
    }
    setUploadingMaterial(true);
    setMaterialActionError(null);
    try {
      const uploadedTaskId = materialUploadTaskId;
      await uploadEventLineAttachment(eventLineId, materialUploadFile, {
        title,
        purpose,
        relatedTaskId: uploadedTaskId || null,
      });
      await loadSnapshot({ silent: true });
      if (uploadedTaskId) {
        setExpandedEvidenceTaskIds((previous) => new Set(previous).add(uploadedTaskId));
      }
      setMaterialUploadOpen(false);
      setMaterialUploadFile(null);
      setMaterialUploadName('');
      setMaterialUploadPurpose('');
      setMaterialUploadTaskId('');
      setViewMode('evidence');
    } catch (err) {
      setMaterialActionError(err instanceof Error ? err.message : '素材上传失败');
    } finally {
      setUploadingMaterial(false);
    }
  }, [
    eventLineId,
    loadSnapshot,
    materialUploadFile,
    materialUploadName,
    materialUploadPurpose,
    materialUploadTaskId,
    snapshot?.canEdit,
    uploadingMaterial,
  ]);

  const handleRetryAttachment = useCallback(async (attachmentId: string) => {
    if (!snapshot?.canEdit || retryingAttachmentId) return;
    setRetryingAttachmentId(attachmentId);
    setMaterialActionError(null);
    try {
      await retryEventLineAttachmentParse(eventLineId, attachmentId);
      await loadSnapshot({ silent: true });
    } catch (err) {
      setMaterialActionError(err instanceof Error ? err.message : '重新解析失败');
    } finally {
      setRetryingAttachmentId(null);
    }
  }, [eventLineId, loadSnapshot, retryingAttachmentId, snapshot?.canEdit]);

  const handleRetryFailedMaterials = useCallback(async () => {
    if (!snapshot?.canEdit || retryingFailedMaterials) return;
    setRetryingFailedMaterials(true);
    setMaterialActionError(null);
    try {
      await retryFailedEventLineAttachments(eventLineId);
      await loadSnapshot({ silent: true });
    } catch (err) {
      setMaterialActionError(err instanceof Error ? err.message : '重试失败材料失败');
    } finally {
      setRetryingFailedMaterials(false);
    }
  }, [eventLineId, loadSnapshot, retryingFailedMaterials, snapshot?.canEdit]);


  const loadReportEntries = useCallback(async () => {
    const loadId = ++reportLoadIdRef.current;
    setReportListState('loading');
    setReportListError(null);
    try {
      const [artifactsResult, legacyRunsResult] = await Promise.allSettled([
        listEventLineReportArtifacts(eventLineId),
        listLegacyEventLineReportRuns(eventLineId),
      ]);
      if (loadId !== reportLoadIdRef.current) return;
      setReportArtifacts(artifactsResult.status === 'fulfilled' ? artifactsResult.value : []);
      setLegacyReportRuns(legacyRunsResult.status === 'fulfilled' ? legacyRunsResult.value : []);
      const failures = [artifactsResult, legacyRunsResult]
        .filter((result): result is PromiseRejectedResult => result.status === 'rejected')
        .map((result) => result.reason instanceof Error ? result.reason.message : '项目报告加载失败');
      if (failures.length > 0) {
        setReportListError(Array.from(new Set(failures)).join('；'));
        setReportListState('error');
      } else {
        setReportListState('ready');
      }
    } catch (err) {
      if (loadId !== reportLoadIdRef.current) return;
      setReportListError(err instanceof Error ? err.message : '项目报告加载失败');
      setReportListState('error');
    }
  }, [eventLineId]);

  useEffect(() => {
    if (viewMode !== 'report' || reportListState !== 'idle') return;
    void loadReportEntries();
  }, [loadReportEntries, reportListState, viewMode]);

  const loadReportDraft = useCallback(async () => {
    const loadId = ++reportDraftLoadIdRef.current;
    setReportDraftState('loading');
    setReportDraftError(null);
    try {
      const result = await getEventLineReportDraft(eventLineId);
      if (loadId !== reportDraftLoadIdRef.current) return;
      setReportDraft(result);
      setReportDraftState('ready');
    } catch (err) {
      if (loadId !== reportDraftLoadIdRef.current) return;
      setReportDraftError(err instanceof Error ? err.message : '报告骨架恢复失败');
      setReportDraftState('error');
    }
  }, [eventLineId]);

  useEffect(() => {
    if (viewMode !== 'blueprint' || reportDraftState !== 'idle') return;
    void loadReportDraft();
    // reportDraftState deliberately is not a dependency: changing to loading
    // must not cancel the in-flight request that will settle this state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventLineId, loadReportDraft, viewMode]);

  const handleDownloadSavedReport = useCallback(async (
    artifact: ReportArtifactSummary,
    format: ReportFileFormat,
  ) => {
    if (artifact.availability_status === 'blocked' || reportActionId) return;
    setReportActionId(`download:${artifact.id}:${format}`);
    setReportListError(null);
    try {
      const rendered = await renderReportArtifact(artifact.id, format);
      if (onDownloadReport) await onDownloadReport(rendered.file_path, rendered.file_name);
      else await window.yiyuWorkbench?.saveFileAs(rendered.file_path, rendered.file_name);
    } catch (err) {
      setReportListError(err instanceof Error ? err.message : '下载报告失败');
    } finally {
      setReportActionId(null);
    }
  }, [onDownloadReport, reportActionId]);

  const handlePromoteLegacyReport = useCallback(async (run: ReportRunSummary) => {
    if (reportActionId) return;
    setReportActionId(`promote:${run.id}`);
    setReportListError(null);
    try {
      await saveReport(run.id, { change_summary: '将历史生成结果保存为正式报告' });
      await loadReportEntries();
    } catch (err) {
      setReportListError(err instanceof Error ? err.message : '历史报告已失效，请依据当前正式主线重新生成');
    } finally {
      setReportActionId(null);
    }
  }, [loadReportEntries, reportActionId]);

  const materialModel = useMemo(() => {
    if (!draft || !snapshot) return null;
    return deriveEventLineMaterialModel(snapshot, draft);
  }, [draft, snapshot]);
  const milestoneTasks = useMemo(
    () => [...(snapshot?.tasks || [])].sort(compareTasksByBusinessDate),
    [snapshot?.tasks],
  );
  const referencedTasks = useMemo(
    () => [...(snapshot?.referencedTasks || [])].sort(compareTasksByBusinessDate),
    [snapshot?.referencedTasks],
  );
  // 这里只展示可重建的证据读模型；正式证据资格仍由云端 source set 裁决。
  const evidenceTaskRows = useMemo<EvidenceTaskRow[]>(() => {
    const tasks = snapshot?.tasks || [];
    const attachments = snapshot?.attachments || [];
    return tasks
      .map((task) => {
        const taskAttachments = attachments.filter((attachment) => normalizeText(attachment.taskId) === task.id);
        return {
          task,
          attachments: taskAttachments,
          isMilestone: humanMilestoneTaskIds.has(task.id),
          happenedAt: taskBusinessDate(task),
        };
      })
      .filter((row) => row.isMilestone || row.attachments.length > 0)
      .sort((left, right) => compareTasksByBusinessDate(left.task, right.task));
  }, [humanMilestoneTaskIds, snapshot?.attachments, snapshot?.tasks]);
  const generalEvidenceAttachments = useMemo(
    () => (snapshot?.attachments || [])
      .filter((attachment) => (
        attachment.sourceKind === 'event_line_attachment'
        && !normalizeText(attachment.taskId)
      ))
      .sort((left, right) => normalizeText(left.createdAt).localeCompare(normalizeText(right.createdAt))),
    [snapshot?.attachments],
  );
  const meetingEvidenceRows = useMemo(() => {
    if (!materialModel) return [];
    return [
      ...materialModel.groups.core,
      ...materialModel.groups.review,
      ...materialModel.groups.supplement,
    ]
      .filter((item) => item.kind === 'activity' && ['会议', '会议纪要'].includes(item.sourceLabel))
      .sort(materialTimeDesc)
      .reverse();
  }, [materialModel]);
  const directEventLineAttachments = useMemo(
    () => (snapshot?.attachments || []).filter(
      (attachment) => attachment.sourceKind === 'event_line_attachment',
    ),
    [snapshot?.attachments],
  );
  const failedMaterialAttachments = directEventLineAttachments.filter(canRetryAttachmentParse);
  const processingMaterialCount = directEventLineAttachments.filter(isAttachmentParsePending).length;
  const materialUploadTask = (snapshot?.tasks || []).find((task) => task.id === materialUploadTaskId) || null;

  const timelineModel = useMemo(() => {
    if (!draft || !snapshot) return null;
    return buildEventLineTimelineModel(snapshot, draft);
  }, [draft, snapshot]);

  /* ---------------------------------------------------------------- */
  /*  Render                                                           */
  /* ---------------------------------------------------------------- */

  if (loading) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-md">
        <div className="rounded-3xl bg-white px-10 py-8 text-center shadow-xl">
          <p className="text-[13px] text-gray-500">正在从云端拉取完整事件线...</p>
        </div>
      </div>
    );
  }

  if (error || !draft || !snapshot) {
    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-md">
        <div className="rounded-3xl bg-white px-10 py-8 text-center shadow-xl">
          <p className="text-[13px] text-red-600">{error || '无法加载事件线'}</p>
          <button type="button" className="mt-4 rounded-2xl bg-gray-100 px-4 py-2 text-[12px]" onClick={onClose}>
            关闭
          </button>
        </div>
      </div>
    );
  }

  const renderTimelineAttachments = (attachments: EventLineReportAttachment[], nodeId: string) => {
    if (attachments.length === 0) return null;
    const imageAttachments = attachments.filter(isImageAttachment);
    const docAttachments = attachments.filter((att) => !isImageAttachment(att));
    const downloadableAtts = attachments.filter((att) => resolveAttachmentUrl(att, backendBaseUrl));
    const docKey = `timeline-docs:${nodeId}`;
    const imageKey = `timeline-images:${nodeId}`;
    const isDocsExpanded = docsExpandedActivities.has(docKey);
    const isImagesExpanded = imagesExpandedActivities.has(imageKey);
    const primaryOpenAtt = attachments.find((att) => resolveAttachmentOpenUrl(att, backendBaseUrl));
    const primaryOpenUrl = primaryOpenAtt ? resolveAttachmentOpenUrl(primaryOpenAtt, backendBaseUrl) : '';

    return (
      <div className="mt-3 rounded-2xl border border-gray-100 bg-[#FAFBFF] p-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <span className="rounded-full bg-white px-2.5 py-1 text-[10px] font-bold text-gray-500 shadow-sm">
              附件 {attachments.length}
            </span>
            {imageAttachments.length > 0 && (
              <span className="rounded-full bg-violet-50 px-2.5 py-1 text-[10px] font-bold text-violet-700">
                图片 {imageAttachments.length}
              </span>
            )}
            {docAttachments.length > 0 && (
              <span className="rounded-full bg-blue-50 px-2.5 py-1 text-[10px] font-bold text-blue-700">
                文档 {docAttachments.length}
              </span>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              title={isDocsExpanded ? '折叠文档' : '展开文档'}
              disabled={docAttachments.length === 0}
              className={`rounded p-1 transition ${docAttachments.length === 0 ? 'cursor-default text-gray-200' : isDocsExpanded ? 'bg-blue-100 text-[#5B7BFE]' : 'text-gray-400 hover:bg-gray-100 hover:text-gray-600'}`}
              onClick={() => {
                if (docAttachments.length === 0) return;
                setDocsExpandedActivities((prev) => {
                  const next = new Set(prev);
                  if (next.has(docKey)) next.delete(docKey);
                  else next.add(docKey);
                  return next;
                });
              }}
            >
              <FileText size={12} />
            </button>
            <button
              type="button"
              title={isImagesExpanded ? '折叠图片' : '展开图片'}
              disabled={imageAttachments.length === 0}
              className={`rounded p-1 transition ${imageAttachments.length === 0 ? 'cursor-default text-gray-200' : isImagesExpanded ? 'bg-blue-100 text-[#5B7BFE]' : 'text-gray-400 hover:bg-gray-100 hover:text-gray-600'}`}
              onClick={() => {
                if (imageAttachments.length === 0) return;
                setImagesExpandedActivities((prev) => {
                  const next = new Set(prev);
                  if (next.has(imageKey)) next.delete(imageKey);
                  else next.add(imageKey);
                  return next;
                });
              }}
            >
              <Image size={12} />
            </button>
            <div className="relative group/dl">
              <button
                type="button"
                title={downloadableAtts.length ? `下载节点附件（${downloadableAtts.length}个）` : '暂无可下载附件'}
                disabled={downloadableAtts.length === 0}
                className={`rounded p-1 transition ${downloadableAtts.length ? 'text-gray-400 hover:bg-gray-100 hover:text-[#5B7BFE]' : 'cursor-default text-gray-200'}`}
                onClick={() => {
                  for (const att of downloadableAtts) {
                    const link = document.createElement('a');
                    link.href = resolveAttachmentUrl(att, backendBaseUrl);
                    link.download = att.title;
                    link.click();
                  }
                }}
              >
                <Download size={12} />
              </button>
              {downloadableAtts.length > 0 && (
                <div className="invisible group-hover/dl:visible absolute right-0 top-full z-30 mt-1 w-72 max-h-80 overflow-y-auto rounded-lg border border-gray-200 bg-white shadow-lg">
                  <div className="border-b border-gray-100 px-3 py-2 text-[10px] font-bold uppercase tracking-[0.16em] text-gray-400">
                    附件列表 · {downloadableAtts.length} 个
                  </div>
                  {downloadableAtts.map((att) => {
                    const dlUrl = resolveAttachmentUrl(att, backendBaseUrl);
                    const openUrl = resolveAttachmentOpenUrl(att, backendBaseUrl);
                    return (
                      <div key={att.id} className="flex items-center justify-between gap-2 px-3 py-2 hover:bg-gray-50">
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-[12px] font-medium text-gray-800" title={att.title}>{att.title}</p>
                          <p className="text-[10px] text-gray-400">{formatAttachmentBytes(att.sizeBytes)}</p>
                        </div>
                        <div className="flex shrink-0 items-center gap-1">
                          {openUrl && (
                            <a
                              href={openUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              title="在浏览器中打开"
                              className="rounded p-1 text-gray-400 transition hover:bg-blue-50 hover:text-[#5B7BFE]"
                            >
                              <ExternalLink size={11} />
                            </a>
                          )}
                          <a
                            href={dlUrl}
                            download={att.title}
                            title="下载"
                            className="rounded p-1 text-gray-400 transition hover:bg-gray-100 hover:text-[#5B7BFE]"
                          >
                            <Download size={11} />
                          </a>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </div>

        {!isDocsExpanded && !isImagesExpanded && (
          <div className="mt-3 space-y-2">
            {imageAttachments.length > 0 && (
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {imageAttachments.slice(0, 4).map((att) => {
                  const url = resolveAttachmentUrl(att, backendBaseUrl);
                  return url ? (
                    <a
                      key={att.id}
                      href={url}
                      download={att.title}
                      className="min-w-0 rounded-xl border border-gray-100 bg-white p-1.5 transition hover:border-[#C9D6FF]"
                      title={att.title}
                    >
                      <img src={url} alt={att.title} className="h-20 w-full rounded-lg object-cover" loading="lazy" />
                      <p className="mt-1 truncate text-[10px] text-gray-500">{att.title}</p>
                    </a>
                  ) : (
                    <div key={att.id} className="min-w-0 rounded-xl border border-gray-100 bg-white p-1.5" title={att.title}>
                      <div className="flex h-20 items-center justify-center rounded-lg bg-gray-100 text-[10px] text-gray-300">
                        无预览
                      </div>
                      <p className="mt-1 truncate text-[10px] text-gray-400">{att.title}</p>
                    </div>
                  );
                })}
                {imageAttachments.length > 4 && (
                  <div className="flex h-[104px] items-center justify-center rounded-xl border border-gray-100 bg-white text-[10px] text-gray-400">
                    另有 {imageAttachments.length - 4} 张图片
                  </div>
                )}
              </div>
            )}

            {docAttachments.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {docAttachments.slice(0, 8).map((att) => {
                  const badge = fileTypeBadge(att.title);
                  const url = resolveAttachmentUrl(att, backendBaseUrl);
                  const content = (
                    <>
                      <span className="rounded px-1 py-0.5 text-[8px] font-bold" style={{ backgroundColor: badge.bg, color: badge.color }}>
                        {badge.label}
                      </span>
                      <span className="truncate">{att.title}</span>
                      {att.parseStatus && (
                        <span className={`rounded px-1 text-[9px] font-bold ${att.parseStatus === 'ready' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>
                          {att.parseStatus === 'ready' ? '已解析' : '待解析'}
                        </span>
                      )}
                    </>
                  );
                  return url ? (
                    <a
                      key={att.id}
                      href={url}
                      download={att.title}
                      className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-2 py-1 text-[10px] text-gray-600 transition hover:border-[#C9D6FF] hover:text-[#5B7BFE]"
                      title={att.title}
                    >
                      {content}
                    </a>
                  ) : (
                    <span
                      key={att.id}
                      className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-gray-100 bg-white px-2 py-1 text-[10px] text-gray-300"
                      title={att.title}
                    >
                      {content}
                    </span>
                  );
                })}
                {docAttachments.length > 8 && (
                  <span className="rounded-lg bg-white px-2 py-1 text-[10px] text-gray-400">
                    另有 {docAttachments.length - 8} 份文档
                  </span>
                )}
              </div>
            )}
          </div>
        )}

        {isDocsExpanded && docAttachments.length > 0 && (
          <div className="mt-3 space-y-2">
            {docAttachments.map((att) => (
              <DocContentViewer key={att.id} att={att} backendBaseUrl={backendBaseUrl} />
            ))}
          </div>
        )}

        {isImagesExpanded && imageAttachments.length > 0 && (
          <div className="mt-3 grid grid-cols-2 gap-2">
            {imageAttachments.map((att) => (
              <ImageWithOcr key={att.id} att={att} backendBaseUrl={backendBaseUrl} />
            ))}
          </div>
        )}

        {primaryOpenUrl && (
          <div className="mt-3 flex justify-end">
            <a
              href={primaryOpenUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 rounded-xl bg-blue-50 px-3 py-2 text-[11px] font-bold text-[#4B66D8] transition hover:bg-blue-100"
            >
              <ExternalLink size={12} />
              打开原文
            </a>
          </div>
        )}
      </div>
    );
  };

  const renderTimelineNode = (node: EventLineTimelineNode, index: number, tone: 'main' | 'review' | 'system' = 'main') => {
    const timeLabel = node.time ? formatTs(node.time) : '时间待补';
    const accentLine =
      tone === 'review' ? 'bg-amber-500'
        : tone === 'system' ? 'bg-gray-300'
          : 'bg-gray-900';
    return (
      <article key={node.id} className="group relative pl-7 py-3">
        <div className={`absolute left-0 top-3 bottom-3 w-[2px] rounded-full ${accentLine}`} />
        <div className="flex items-baseline gap-4 mb-1.5">
          <span className="text-[24px] leading-none font-extralight tracking-tighter text-gray-200">
            {String(index + 1).padStart(2, '0')}
          </span>
          <div className="flex-1 min-w-0">
            <div className="flex items-baseline gap-3 flex-wrap mb-0.5">
              <h3 className="text-[14.5px] font-semibold leading-snug text-gray-900">{node.title}</h3>
              <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-gray-400">
                {TIMELINE_KIND_LABELS[node.kind]}
              </span>
              <span className="text-[10px] text-gray-400 tabular-nums">{timeLabel}</span>
              {node.ownerName && <span className="text-[10px] text-gray-400">{node.ownerName}</span>}
              {!node.ownerName && node.actorName && <span className="text-[10px] text-gray-400">{node.actorName}</span>}
            </div>
            <p className="whitespace-pre-wrap text-[12.5px] leading-6 text-gray-600">{node.summary}</p>
            {node.evidenceSummary && (
              <div className="mt-2 border-l-[2px] border-gray-200 pl-3 py-1">
                <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-gray-400 mb-0.5">解析依据</p>
                <p className="text-[11.5px] leading-5 text-gray-600">{node.evidenceSummary}</p>
              </div>
            )}
            {node.tags.length > 0 && (
              <div className="mt-1.5 flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5 text-[10.5px] text-gray-500">
                {node.tags.map((tag, i) => (
                  <span key={tag}>{i > 0 && <span className="text-gray-300 mr-2">·</span>}{tag}</span>
                ))}
              </div>
            )}
            {node.warnings.length > 0 && (
              <p className="mt-1.5 text-[11px] leading-5 text-amber-700">⚠ {node.warnings.join(' · ')}</p>
            )}
            {renderTimelineAttachments(node.attachments, node.id)}
          </div>
        </div>
      </article>
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4 backdrop-blur-sm animate-in fade-in">
      <div
        className="relative flex h-[88vh] w-full max-w-[920px] flex-col rounded-xl border border-gray-200 bg-white shadow-[0_8px_32px_rgba(0,0,0,0.08)]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* ── Header · 极简 typography ── */}
        <div className="flex items-start gap-4 border-b border-gray-100 px-7 pt-6 pb-5">
          <button
            type="button"
            className="mt-1 inline-flex h-8 w-8 items-center justify-center rounded-md border border-gray-200 bg-white text-gray-400 transition-all hover:border-gray-300 hover:text-gray-900 hover:bg-gray-50"
            onClick={onClose}
            aria-label="关闭"
          >
            <X size={14} strokeWidth={2} />
          </button>
          <div className="flex-1 min-w-0">
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">事件线工作区</p>
            <h2 className="mt-1.5 text-[24px] font-light tracking-tight text-gray-900 leading-tight">{draft.eventLineName}</h2>
            <p className="mt-2 text-[12px] leading-5 text-gray-500">
              先确认目标、背景与里程碑，再让 AI 依据证据还原主线。
            </p>
            {!snapshot.canEdit && (
              <p className="mt-1 text-[11px] text-gray-400">
                {snapshot.readOnlyReason || '当前账号可查看这条事件线，但不能修改或补充材料。'}
              </p>
            )}
            {materialActionError && (
              <p className="mt-1 text-[11px] text-rose-600">{materialActionError}</p>
            )}
          </div>
        </div>

        {/* ── Meta · status + project + participants ── */}
        <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1 border-b border-gray-100 px-7 py-3 text-[11px]">
          {(() => {
            const statusMeta: Record<string, { dot: string; label: string; text: string }> = {
              active: { dot: 'bg-emerald-500', label: '进行中', text: 'text-emerald-700' },
              blocked: { dot: 'bg-rose-500', label: '受阻', text: 'text-rose-700' },
              paused: { dot: 'bg-amber-500', label: '历史暂停状态', text: 'text-amber-700' },
              done: { dot: 'bg-gray-400', label: '已完成', text: 'text-gray-600' },
              archived: { dot: 'bg-gray-300', label: '已归档', text: 'text-gray-500' },
            };
            const s = statusMeta[snapshot.eventLine.status] || statusMeta.active;
            return (
              <span className={`inline-flex items-center gap-1.5 font-medium ${s.text}`}>
                <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />
                {s.label}
              </span>
            );
          })()}
          {snapshot.eventLine.primaryClientName && (
            <span className="text-gray-400">
              <span className="text-[10px] uppercase tracking-[0.14em] mr-1">项目</span>
              <span className="text-gray-700 font-medium">{snapshot.eventLine.primaryClientName}</span>
            </span>
          )}
          {draft.participantNames.length > 0 && (
            <span className="text-gray-400 inline-flex items-baseline gap-1">
              <span className="text-[10px] uppercase tracking-[0.14em]">参与</span>
              <span className="text-gray-700 font-medium">{draft.participantNames.join(' · ')}</span>
            </span>
          )}
          {snapshot.sourceState !== 'cloud_ready' && (
            <span className={`rounded-md px-1.5 py-0.5 text-[10px] font-medium ${snapshot.sourceState === 'sync_degraded' ? 'bg-amber-50 text-amber-700' : snapshot.sourceState === 'local_history' ? 'bg-gray-100 text-gray-600' : 'bg-sky-50 text-sky-700'}`}>
              {snapshot.sourceState === 'sync_degraded' ? '同步降级' : snapshot.sourceState === 'local_history' ? '本机历史镜像' : '本机可编辑'}
            </span>
          )}
          {(() => {
            const status = snapshot.eventLine.syncStatus;
            if (!status || status === 'synced') return null;
            const cfg: Record<string, { label: string; dot: string; text: string; bg: string; ring: string }> = {
              local: { label: '仅本地', dot: 'bg-gray-400', text: 'text-gray-600', bg: 'bg-gray-50', ring: 'ring-gray-200' },
              syncing: { label: '同步中', dot: 'bg-sky-500', text: 'text-sky-700', bg: 'bg-sky-50', ring: 'ring-sky-200' },
              pending: { label: '待同步', dot: 'bg-amber-500', text: 'text-amber-700', bg: 'bg-amber-50', ring: 'ring-amber-200' },
              error: { label: '同步失败', dot: 'bg-rose-500', text: 'text-rose-700', bg: 'bg-rose-50', ring: 'ring-rose-200' },
              remote_missing: { label: '云端已删除', dot: 'bg-gray-500', text: 'text-gray-700', bg: 'bg-gray-100', ring: 'ring-gray-300' },
            };
            const item = cfg[status];
            if (!item) return null;
            return (
              <span
                className={`inline-flex items-center gap-1 rounded-md ${item.bg} px-1.5 py-0.5 text-[10px] font-medium tracking-wide uppercase ${item.text} ring-1 ${item.ring}/60`}
                title={snapshot.eventLine.lastSyncError || undefined}
              >
                <span className={`h-1 w-1 rounded-full ${item.dot}`} />
                {item.label}
              </span>
            );
          })()}
        </div>
        <div className="border-b border-gray-100 px-7 py-3">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
            <span className="text-[10px] font-bold uppercase tracking-[0.16em] text-gray-400">报告准备度</span>
            <span className={`font-semibold ${snapshot.eventLine.readinessLevel === 'substantial' ? 'text-emerald-700' : snapshot.eventLine.readinessLevel === 'general' ? 'text-amber-700' : 'text-rose-700'}`}>
              {snapshot.eventLine.readinessLevel === 'substantial' ? '较完整' : snapshot.eventLine.readinessLevel === 'general' ? '一般' : '不完整'}
            </span>
            <span className="text-emerald-700">
              已有：{EVENT_LINE_READINESS_DIMENSIONS.filter((item) => !(snapshot.eventLine.readinessMissingItems || []).includes(item)).join('、') || '暂无'}
            </span>
            {(snapshot.eventLine.readinessMissingItems || []).length > 0 && (
              <span className="text-amber-700">
                主要缺：{(snapshot.eventLine.readinessMissingItems || []).join('、')}
              </span>
            )}
          </div>
          {snapshotRefreshError && (
            <div className="mt-2 flex items-center justify-between gap-3 text-[10.5px] text-rose-600">
              <span>事件线最新状态刷新失败：{snapshotRefreshError}</span>
              <button
                type="button"
                onClick={() => void loadSnapshot({ silent: true })}
                className="shrink-0 font-medium underline"
              >
                重试刷新
              </button>
            </div>
          )}
        </div>

        <div className="flex-1 overflow-y-auto px-7 py-5">
          <div className="mb-6 border-b border-gray-100">
            <div className="grid grid-cols-6 gap-2">
              {([
                { id: 'context' as const, label: '目标与背景' },
                { id: 'milestones' as const, label: '里程碑确定' },
                { id: 'evidence' as const, label: '证据补充' },
                { id: 'timeline' as const, label: '主线还原' },
                { id: 'blueprint' as const, label: '报告骨架' },
                { id: 'report' as const, label: '项目报告' },
              ]).map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setViewMode(tab.id)}
                  className={`relative shrink-0 pb-3 text-[13px] tracking-wide transition-colors whitespace-nowrap ${
                    viewMode === tab.id ? 'font-semibold text-gray-900' : 'font-medium text-gray-400 hover:text-gray-700'
                  }`}
                >
                  {tab.label}
                  {viewMode === tab.id && <span className="absolute bottom-[-1px] left-0 h-[1.5px] w-full bg-gray-900" />}
                </button>
              ))}
            </div>
          </div>

          {viewMode === 'context' && (
          <section className="mb-6 pb-6">
            <div className="grid gap-5 lg:grid-cols-2">
              <div>
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <h3 className="text-[12px] font-semibold text-gray-900">目标</h3>
                    <span className={`text-[10px] ${goalConfirmed ? 'text-emerald-600' : 'text-amber-600'}`}>
                      {goalConfirmed ? '已填写' : '待补充'}
                    </span>
                  </div>
                </div>
                <p className="mt-1 text-[10.5px] leading-5 text-gray-400">
                  可以复制粘贴现成的目标文案；如尚未成文，也可以用口头表达的方式快速记录，AI会协助润色。
                </p>
                <textarea
                  value={goalText}
                  readOnly={!snapshot.canEdit}
                  onChange={(event) => setGoalText(event.target.value)}
                  rows={5}
                  placeholder="这条事件线最终要实现什么？"
                  className="mt-2 w-full resize-y rounded-md border border-gray-200 bg-white px-3 py-2 text-[12px] leading-5 text-gray-700 outline-none transition focus:border-gray-400 disabled:bg-gray-50"
                />
                <div className="mt-2 flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => void handlePolishGoal()}
                      disabled={!snapshot.canEdit || !goalText.trim() || Boolean(goalAction)}
                      title={!snapshot.canEdit ? snapshot.readOnlyReason || '当前只读' : undefined}
                      className="inline-flex h-8 items-center gap-1.5 rounded-md border border-gray-200 bg-white px-3 text-[11px] font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                    >
                      <Sparkles size={12} />
                      {goalAction === 'polish' ? '润色中' : 'AI润色目标'}
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleSaveGoal()}
                      disabled={!snapshot.canEdit || !goalText.trim() || Boolean(goalAction)}
                      title={!snapshot.canEdit ? snapshot.readOnlyReason || '当前只读' : undefined}
                      className="inline-flex h-8 items-center gap-1.5 rounded-md bg-gray-900 px-3 text-[11px] font-medium text-white hover:bg-gray-700 disabled:opacity-50"
                    >
                      <Check size={12} />
                      {goalAction === 'save' ? '保存中' : '保存目标'}
                    </button>
                </div>
                {goalError && (
                  <div className="mt-2 flex items-center justify-between gap-3 text-[10.5px] text-rose-600">
                    <span>{goalError}</span>
                    <button
                      type="button"
                      onClick={() => void (goalFailedAction === 'polish' ? handlePolishGoal() : handleSaveGoal())}
                      className="shrink-0 font-medium underline"
                    >
                      {goalFailedAction === 'polish' ? '重试润色' : '重试保存'}
                    </button>
                  </div>
                )}
                {!goalError && goalMessage && <p className="mt-2 text-[10.5px] text-gray-500">{goalMessage}</p>}
              </div>

              <div>
                <div className="flex items-center gap-2">
                  <h3 className="text-[12px] font-semibold text-gray-900">背景</h3>
                  <span className={`text-[10px] ${backgroundConfirmed ? 'text-emerald-600' : 'text-gray-400'}`}>
                    {backgroundConfirmed ? '已填写' : '可后补'}
                  </span>
                </div>
                <p className="mt-1 text-[10.5px] leading-5 text-gray-400">
                  简单交代为什么要做、要回应什么问题；AI会结合目标和项目基础信息整理润色。
                </p>
                <textarea
                  value={backgroundText}
                  readOnly={!snapshot.canEdit}
                  onChange={(event) => setBackgroundText(event.target.value)}
                  rows={5}
                  placeholder="补充项目缘起、现实问题与关键约束…"
                  className="mt-2 w-full resize-y rounded-md border border-gray-200 bg-white px-3 py-2 text-[12px] leading-5 text-gray-700 outline-none transition focus:border-gray-400 disabled:bg-gray-50"
                />
                <div className="mt-2 flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => void handleDraftBackground()}
                      disabled={!snapshot.canEdit || Boolean(backgroundAction)}
                      title={!snapshot.canEdit ? snapshot.readOnlyReason || '当前只读' : undefined}
                      className="inline-flex h-8 items-center gap-1.5 rounded-md border border-gray-200 bg-white px-3 text-[11px] font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                    >
                      <Sparkles size={12} />
                      {backgroundAction === 'draft' ? '润色中' : 'AI润色背景'}
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleSaveBackground()}
                      disabled={!snapshot.canEdit || !backgroundText.trim() || Boolean(backgroundAction)}
                      title={!snapshot.canEdit ? snapshot.readOnlyReason || '当前只读' : undefined}
                      className="inline-flex h-8 items-center gap-1.5 rounded-md bg-gray-900 px-3 text-[11px] font-medium text-white hover:bg-gray-700 disabled:opacity-50"
                    >
                      <Check size={12} />
                      {backgroundAction === 'save' ? '保存中' : '确认并保存'}
                    </button>
                </div>
                {backgroundError && (
                  <div className="mt-2 flex items-center justify-between gap-3 text-[10.5px] text-rose-600">
                    <span>{backgroundError}</span>
                    <button
                      type="button"
                      onClick={() => void (backgroundFailedAction === 'draft' ? handleDraftBackground() : handleSaveBackground())}
                      className="shrink-0 font-medium underline"
                    >
                      {backgroundFailedAction === 'draft' ? '重试润色' : '重试保存'}
                    </button>
                  </div>
                )}
                {!backgroundError && backgroundMessage && <p className="mt-2 text-[10.5px] text-gray-500">{backgroundMessage}</p>}
                {(backgroundDraftCitations.length > 0 || backgroundDraftWarning) && (
                  <div className="mt-3 rounded-md border border-blue-100 bg-blue-50/60 px-3 py-2.5">
                    <p className="text-[10.5px] font-semibold text-blue-800">本次 AI 草稿引用</p>
                    <div className="mt-1.5 space-y-1">
                      {backgroundDraftCitations.map((citation) => (
                        <p key={`${citation.type}:${citation.id}`} className="text-[10.5px] leading-4 text-blue-700">
                          {citation.type === 'task' ? '任务' : citation.type === 'attachment' ? '材料' : '资料'} · {citation.title}
                        </p>
                      ))}
                    </div>
                    {backgroundDraftWarning && <p className="mt-1.5 text-[10.5px] text-amber-700">{backgroundDraftWarning}</p>}
                  </div>
                )}
              </div>
            </div>
          </section>
          )}

          {viewMode === 'milestones' && (
            <section className="mb-6 border-b border-gray-100 pb-6">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="text-[12px] font-semibold text-gray-900">里程碑</h3>
                    <span className={`text-[10px] ${humanMilestoneTaskIds.size > 0 ? 'text-emerald-600' : 'text-amber-600'}`}>
                      {humanMilestoneTaskIds.size > 0 ? `已确认 ${humanMilestoneTaskIds.size} 项` : '至少确认一项'}
                    </span>
                  </div>
                  <p className="mt-1 text-[10.5px] leading-5 text-gray-400">只有已正式纳入当前项目与事件线的任务可设为里程碑；轻量引用不会改变原任务归属。</p>
                </div>
                <span className={`rounded-md px-2 py-1 text-[10px] font-medium ${formalInputsReady ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>
                  {formalInputsReady ? '可生成正式主线' : '当前只能生成素材概览'}
                </span>
              </div>

              {snapshot.taskMirrorStatus === 'failed' && (
                <div className="mt-3 flex items-center justify-between gap-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[10.5px] text-amber-800">
                  <span>{snapshot.taskMirrorError || '组织云任务已读取，但本机镜像暂未补齐。'}</span>
                  <button type="button" onClick={() => void loadSnapshot()} className="shrink-0 font-medium underline">重新同步</button>
                </div>
              )}

              <div className="mt-3 space-y-2">
                {milestoneTasks.map((task) => {
                  const isMilestone = humanMilestoneTaskIds.has(task.id);
                  const taskDate = taskBusinessDate(task);
                  return (
                    <div key={`linked-task:${task.id}`} className="flex items-center justify-between gap-3 rounded-md border border-gray-100 bg-gray-50/60 px-3 py-2">
                      <div className="min-w-0">
                        <button type="button" onClick={() => onOpenTask?.(task)} className="block max-w-full truncate text-left text-[11.5px] font-medium text-gray-700 hover:text-gray-950">
                          {task.title}
                        </button>
                        <p className="mt-0.5 text-[10px] text-gray-400">
                          {taskDate ? `日期：${formatDateLabel(taskDate)}` : '日期未填写'}
                          {' · '}
                          {TASK_STATUS_LABELS[task.status] || task.status}
                        </p>
                      </div>
                      {snapshot.eventLine.viewerCapabilities.canSetMilestone && (
                        <button
                          type="button"
                          onClick={() => void handleToggleMilestone(task)}
                          disabled={Boolean(milestoneTaskId)}
                          className={`shrink-0 rounded-md px-2.5 py-1 text-[10.5px] font-medium transition disabled:opacity-50 ${isMilestone ? 'bg-gray-900 text-white' : 'border border-gray-200 bg-white text-gray-600 hover:border-gray-300'}`}
                        >
                          {milestoneTaskId === task.id ? '处理中' : isMilestone ? '已设为里程碑' : '设为里程碑'}
                        </button>
                      )}
                    </div>
                  );
                })}
                {milestoneTasks.length === 0 && (
                  <p className="rounded-md border border-dashed border-gray-200 px-3 py-4 text-center text-[11px] text-gray-400">当前还没有正式纳入事件线的任务。</p>
                )}
              </div>
              {referencedTasks.length > 0 && (
                <div className="mt-4">
                  <div className="flex items-center gap-2">
                    <h4 className="text-[11px] font-semibold text-gray-700">引用任务</h4>
                    <span className="text-[10px] text-gray-400">仅补充事件线脉络，不改变原任务</span>
                  </div>
                  <div className="mt-2 space-y-1.5">
                    {referencedTasks.map((task) => (
                      <div key={`referenced-task:${task.id}`} className="flex items-center justify-between gap-3 rounded-md border border-blue-100 bg-blue-50/30 px-3 py-2">
                        <div className="min-w-0">
                          <button type="button" onClick={() => onOpenTask?.(task)} className="block max-w-full truncate text-left text-[11.5px] font-medium text-gray-700 hover:text-gray-950">
                            {task.title}
                          </button>
                          <p className="mt-0.5 truncate text-[10px] text-gray-400">
                            {task.clientName || '未归属项目'}{task.eventLineName ? ` · 原事件线：${task.eventLineName}` : ''}
                          </p>
                        </div>
                        <span className="shrink-0 rounded-md bg-white px-2 py-1 text-[10px] font-medium text-blue-600 ring-1 ring-blue-100">引用</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {milestoneError && (
                <p className="mt-2 text-[10.5px] text-rose-600">{milestoneError}</p>
              )}

              {snapshot.canEdit && (
                <details className="mt-3 rounded-md border border-gray-100 bg-white px-3 py-2.5">
                  <summary className="cursor-pointer text-[11px] font-medium text-gray-600">查找并引用任务</summary>
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <div className="flex min-w-[220px] flex-1 items-center gap-2 rounded-md border border-gray-200 px-2.5">
                      <Search size={12} className="text-gray-400" />
                      <input
                        value={taskSearch}
                        onChange={(event) => setTaskSearch(event.target.value)}
                        onKeyDown={(event) => { if (event.key === 'Enter') void loadTaskCandidates(); }}
                        placeholder="搜索任务标题或说明"
                        className="h-8 min-w-0 flex-1 border-0 bg-transparent text-[11px] outline-none"
                      />
                    </div>
                    <div className="inline-flex rounded-md border border-gray-200 p-0.5">
                      {(['client', 'organization'] as const).map((scope) => (
                        <button
                          key={scope}
                          type="button"
                          onClick={() => setTaskSearchScope(scope)}
                          className={`rounded px-2 py-1 text-[10px] ${taskSearchScope === scope ? 'bg-gray-900 text-white' : 'text-gray-500'}`}
                        >
                          {scope === 'client' ? '同项目' : '当前组织'}
                        </button>
                      ))}
                    </div>
                    <button
                      type="button"
                      onClick={() => void loadTaskCandidates()}
                      disabled={taskCandidateState === 'loading'}
                      className="inline-flex h-8 items-center gap-1 rounded-md bg-gray-900 px-3 text-[10.5px] font-medium text-white disabled:opacity-50"
                    >
                      {taskCandidateState === 'loading' ? <RefreshCw size={11} className="animate-spin" /> : <Search size={11} />}
                      查找
                    </button>
                  </div>
                  {taskCandidateState === 'error' && (
                    <div className="mt-3 flex items-center justify-between gap-3 text-[10.5px] text-rose-600">
                      <span>{taskCandidateError || '任务查找失败'}</span>
                      <button type="button" onClick={() => void loadTaskCandidates()} className="shrink-0 font-medium underline">重试</button>
                    </div>
                  )}
                  {taskCandidateState === 'ready' && taskCandidates.length === 0 && (
                    <p className="mt-3 rounded-md border border-dashed border-gray-200 px-3 py-4 text-center text-[11px] text-gray-400">
                      没有找到符合条件的任务。
                    </p>
                  )}
                  {taskCandidates.length > 0 && (
                    <div className="mt-3">
                      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 pb-2">
                        <label className="inline-flex cursor-pointer items-center gap-2 text-[10.5px] text-gray-600">
                          <input
                            type="checkbox"
                            checked={allTaskCandidatesSelected}
                            aria-checked={
                              selectedTaskCandidateIds.size > 0 && !allTaskCandidatesSelected
                                ? 'mixed'
                                : allTaskCandidatesSelected
                            }
                            onChange={toggleAllTaskCandidates}
                            disabled={selectableTaskCandidates.length === 0 || Boolean(linkingTaskId)}
                            className="h-3.5 w-3.5 rounded border-gray-300 text-[#4D6BFE] focus:ring-[#4D6BFE] disabled:opacity-40"
                          />
                          全选可引用任务
                          {selectedTaskCandidateIds.size > 0 && (
                            <span className="text-gray-400">已选 {selectedTaskCandidateIds.size} 条</span>
                          )}
                        </label>
                        <button
                          type="button"
                          onClick={() => void handleBatchLinkCandidates()}
                          disabled={selectedTaskCandidateIds.size === 0 || Boolean(linkingTaskId)}
                          className="inline-flex h-7 items-center gap-1.5 rounded-md bg-[#4D6BFE] px-3 text-[10.5px] font-medium text-white transition hover:bg-[#4059d7] disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          {linkingTaskId === '__batch__' ? <RefreshCw size={11} className="animate-spin" /> : <Link2 size={11} />}
                          {linkingTaskId === '__batch__' ? '批量引用中' : `批量引用${selectedTaskCandidateIds.size > 0 ? `（${selectedTaskCandidateIds.size}）` : ''}`}
                        </button>
                      </div>
                      <div className="max-h-52 space-y-1 overflow-y-auto">
                        {taskCandidates.map((candidate) => {
                          const alreadyLinked = taskCandidateAlreadyIncluded(
                            candidate,
                            eventLineId,
                            currentEventLineIdentity,
                          );
                          return (
                            <div key={`candidate:${candidate.id}`} className="flex items-center justify-between gap-3 rounded-md px-2 py-2 hover:bg-gray-50">
                              <input
                                type="checkbox"
                                checked={selectedTaskCandidateIds.has(candidate.id)}
                                onChange={() => toggleTaskCandidateSelection(candidate.id)}
                                disabled={alreadyLinked || Boolean(linkingTaskId)}
                                aria-label={`选择任务：${candidate.title}`}
                                className="h-3.5 w-3.5 shrink-0 rounded border-gray-300 text-[#4D6BFE] focus:ring-[#4D6BFE] disabled:opacity-30"
                              />
                              <div className="min-w-0 flex-1">
                              <p className="truncate text-[11px] font-medium text-gray-700">{candidate.title}</p>
                              <p className="mt-0.5 truncate text-[9.5px] text-gray-400">
                                {candidate.clientName || '未关联项目'}{candidate.eventLineName ? ` · 已关联 ${candidate.eventLineName}` : ''}
                              </p>
                              {!alreadyLinked && (
                                <p className="mt-0.5 truncate text-[9.5px] text-gray-400">
                                  {candidate.relationMode === 'formal'
                                    ? candidate.clientId
                                      ? '本人负责且同项目，将纳入本事件线'
                                      : '本人负责且未归属项目，将补入本项目和事件线'
                                    : '仅引用，不修改原任务信息'}
                                </p>
                              )}
                              </div>
                              <button
                                type="button"
                                onClick={() => void handleLinkCandidate(candidate)}
                                disabled={alreadyLinked || Boolean(linkingTaskId)}
                                className="inline-flex shrink-0 items-center gap-1 text-[10.5px] font-medium text-[#4D6BFE] disabled:text-gray-300"
                              >
                                {alreadyLinked ? <Check size={11} /> : <Link2 size={11} />}
                                {alreadyLinked ? '已引用' : linkingTaskId === candidate.id ? '引用中' : '引用'}
                              </button>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                  {taskLinkError && <p className="mt-2 text-[10.5px] text-rose-600">{taskLinkError}</p>}
                  {!taskLinkError && taskLinkMessage && <p className="mt-2 text-[10.5px] text-emerald-600">{taskLinkMessage}</p>}
                </details>
              )}
            </section>
          )}

          {viewMode === 'timeline' && timelineModel ? (
            <div className="space-y-5 pb-6">
              {/* P1 · AI 主线还原 banner */}
              {timelineNarrative ? (
                <section className="rounded-2xl border border-gray-900 bg-gray-900 px-5 py-5 text-white">
                  <div className="flex items-baseline justify-between gap-4 mb-3">
                    <div className="flex items-center gap-2">
                      <Sparkles size={14} className="text-amber-300" />
                      <h3 className="text-[15px] font-semibold tracking-tight">
                        {timelineNarrative.outputKind === 'formal_mainline' ? '正式主线' : '素材概览'} · {timelineNarrative.headline || '事件线还原'}
                      </h3>
                    </div>
                    <div className="flex items-center gap-3 text-[10px] text-gray-400">
                      <span>rev {timelineNarrative.rev}</span>
                      <span>·</span>
                      <span>{timelineNarrative.updatedAt.slice(0, 16).replace('T', ' ')}</span>
                      {timelineNarrative.availabilityStatus === 'blocked'
                        ? <span className="text-rose-300">暂不可用</span>
                        : timelineNarrative.isStale && <span className="text-amber-300">输入已变化</span>}
                      <button
                        type="button"
                        onClick={handleRegenerateNarrative}
                        disabled={narrativeRegenerating || !snapshot.canEdit}
                        className="ml-2 inline-flex items-center gap-1 rounded-md border border-gray-700 bg-gray-800 px-2.5 py-1 text-[10px] font-medium text-gray-200 transition hover:bg-gray-700 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {narrativeRegenerating ? (
                          <>
                            <RefreshCw size={10} className="animate-spin" />
                            重新生成中
                          </>
                        ) : (
                          <>
                            <RefreshCw size={10} />
                            重新生成
                          </>
                        )}
                      </button>
                    </div>
                  </div>
                  {timelineNarrative.opening && (
                    <p className="text-[13px] leading-relaxed text-gray-200">{timelineNarrative.opening}</p>
                  )}
                  {timelineNarrative.availabilityStatus === 'blocked' && (
                    <p className="mt-2 text-[11px] leading-5 text-rose-300">
                      {timelineNarrative.availabilityReason || '主线中的证据已撤销、删除或当前账号无权读取。'}
                    </p>
                  )}
                  {timelineNarrative.outputKind !== 'formal_mainline' && timelineNarrative.missingRequirements && timelineNarrative.missingRequirements.length > 0 && (
                    <p className="mt-2 text-[11px] leading-5 text-amber-300">
                      要生成正式主线，还需：{timelineNarrative.missingRequirements.join('；')}
                    </p>
                  )}
                  {narrativeError && (
                    <p className="mt-2 text-[11px] text-rose-300">{narrativeError}</p>
                  )}
                </section>
              ) : (
                <section className="rounded-2xl border border-dashed border-gray-300 bg-gray-50/60 px-5 py-6 text-center">
                  <Sparkles size={16} className="mx-auto mb-2 text-gray-400" />
                  <p className="text-[13px] font-semibold text-gray-700">主线还原尚未生成</p>
                  <p className="mt-1 text-[11.5px] text-gray-500">
                    {formalInputsReady
                      ? 'AI 将严格按人工确认的里程碑顺序组织证据和进展。'
                      : '目标或人工里程碑尚未齐全，只能先生成素材概览。'}
                  </p>
                  <button
                    type="button"
                    onClick={handleRegenerateNarrative}
                    disabled={narrativeRegenerating || !snapshot.canEdit}
                    className="mt-3 inline-flex items-center gap-1.5 rounded-md bg-gray-900 px-4 py-2 text-[12px] font-medium text-white transition hover:bg-gray-700 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {narrativeRegenerating ? (
                      <>
                        <RefreshCw size={12} className="animate-spin" />
                        AI 生成中 · 约 1-2 分钟
                      </>
                    ) : (
                      <>
                        <Sparkles size={12} />
                        {formalInputsReady ? '生成正式主线' : '生成素材概览'}
                      </>
                    )}
                  </button>
                  {narrativeError && (
                    <p className="mt-3 text-[11px] text-rose-600">{narrativeError}</p>
                  )}
                </section>
              )}

              {timelineNarrative && timelineNarrative.nodes.length > 0 ? (
                <div className="space-y-5">
                  {timelineNarrative.nodes.map((node, index) => renderNarrativeNode(node, index))}
                  {timelineNarrative.closing && (
                    <section className="rounded-2xl border-l-[2px] border-gray-900 bg-gray-50 pl-4 py-3">
                      <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400 mb-2">今天在哪里</p>
                      <p className="text-[13px] leading-relaxed text-gray-700">{timelineNarrative.closing}</p>
                    </section>
                  )}
                  {timelineModel.mainNodes.length > 0 && (
                    <details className="mt-6 rounded-xl border border-gray-100 bg-gray-50/40 px-4 py-3">
                      <summary className="cursor-pointer text-[11px] font-medium text-gray-500 hover:text-gray-700">
                        查看原始时间线节点 ({timelineModel.mainNodes.length} 个) — 由规则切分, 仅供参考
                      </summary>
                      <div className="mt-3 space-y-3">
                        {timelineModel.mainNodes.map((node, index) => renderTimelineNode(node, index))}
                      </div>
                    </details>
                  )}
                </div>
              ) : timelineModel.mainNodes.length > 0 ? (
                <div className="space-y-3">
                  {timelineModel.mainNodes.map((node, index) => renderTimelineNode(node, index))}
                </div>
              ) : (
                <div className="rounded-2xl border border-gray-100 bg-white px-4 py-8 text-center text-[12px] text-gray-400">
                  当前还没有足够信息形成主线节点。
                </div>
              )}

              {timelineModel.reviewNodes.length > 0 && (
                <section className="pt-5 border-t border-gray-100">
                  <div className="mb-3 flex items-baseline gap-3">
                    <h3 className="text-[10px] font-bold uppercase tracking-[0.18em] text-amber-600">待确认节点</h3>
                    <span className="text-[11px] text-gray-400 tabular-nums">{timelineModel.reviewNodes.length} 项</span>
                  </div>
                  <p className="mb-4 text-[11.5px] leading-relaxed text-gray-500">
                    缺少归属、含测试文件或解析状态不完整 · 暂不进入主线叙事
                  </p>
                  <div className="space-y-2">
                    {timelineModel.reviewNodes.map((node, index) => renderTimelineNode(node, index, 'review'))}
                  </div>
                </section>
              )}

              {timelineModel.systemNodes.length > 0 && (
                <section className="pt-5 border-t border-gray-100">
                  <button
                    type="button"
                    onClick={() => setShowSystemTraces((prev) => !prev)}
                    className="flex w-full items-baseline justify-between gap-3 text-left group/sys"
                  >
                    <div className="flex items-baseline gap-3">
                      <h3 className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400 group-hover/sys:text-gray-700">系统痕迹</h3>
                      <span className="text-[11px] text-gray-400 tabular-nums">{timelineModel.systemNodes.length} 项</span>
                    </div>
                    <span className="text-[11px] font-medium text-gray-400 group-hover/sys:text-gray-900 transition-colors">
                      {showSystemTraces ? '收起 ↑' : '展开 ↓'}
                    </span>
                  </button>
                  <p className="mt-1 text-[11.5px] leading-relaxed text-gray-400">
                    创建 · 上传 · 更新等审计流水
                  </p>
                  {showSystemTraces && (
                    <div className="mt-4 space-y-2">
                      {timelineModel.systemNodes.map((node, index) => renderTimelineNode(node, index, 'system'))}
                    </div>
                  )}
                </section>
              )}
            </div>
          ) : null}

          {viewMode === 'blueprint' ? (
            <div className="space-y-5 pb-6">
              {!formalNarrativeReady ? (
                <section className="rounded-xl border border-amber-200 bg-amber-50 px-5 py-4">
                  <p className="text-[13px] font-semibold text-amber-900">报告骨架暂不能生成</p>
                  <p className="mt-1 text-[11.5px] leading-5 text-amber-800">
                    {!formalInputsReady
                      ? '请先填写并保存目标，再在“里程碑确定”中人工指定至少一个里程碑。'
                      : timelineNarrative?.availabilityStatus === 'blocked'
                        ? timelineNarrative.availabilityReason || '正式主线中的证据已撤销、删除或当前账号无权读取。'
                        : timelineNarrative?.isStale
                          ? '目标、里程碑或证据已经变化，请先重新生成正式主线。'
                          : '请先在“主线还原”中生成正式主线。'}
                  </p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button type="button" onClick={() => setViewMode('context')} className="rounded-md border border-amber-200 bg-white px-3 py-1.5 text-[11px] font-medium text-amber-900">检查目标</button>
                    <button type="button" onClick={() => setViewMode('milestones')} className="rounded-md border border-amber-200 bg-white px-3 py-1.5 text-[11px] font-medium text-amber-900">检查里程碑</button>
                    <button type="button" onClick={() => setViewMode('timeline')} className="rounded-md bg-amber-900 px-3 py-1.5 text-[11px] font-medium text-white">查看主线</button>
                  </div>
                </section>
              ) : reportDraftState === 'loading' ? (
                <div className="flex items-center justify-center gap-2 py-14 text-[12px] text-gray-400">
                  <RefreshCw size={13} className="animate-spin" /> 正在恢复报告骨架与生成进度…
                </div>
              ) : reportDraftState === 'error' ? (
                <div className="rounded-xl border border-rose-200 bg-rose-50 px-5 py-4">
                  <p className="text-[12px] leading-5 text-rose-700">{reportDraftError || '报告骨架恢复失败'}</p>
                  <button type="button" onClick={() => void loadReportDraft()} className="mt-2 text-[11px] font-medium text-rose-700">重试</button>
                </div>
              ) : (
                <AIReportGeneratorModal
                  key={`${eventLineId}:${reportDraft?.id || 'new'}`}
                  embedded
                  initialRun={reportDraft}
                  eventLineId={eventLineId}
                  eventLineName={draft.eventLineName}
                  clientName={snapshot.eventLine.primaryClientName || undefined}
                  onDownload={onDownloadReport}
                  onOpenSmartEditor={(artifact) => onOpenSavedReport?.(artifact)}
                  onSaved={(artifact) => {
                    setReportArtifacts((current) => [artifact, ...current.filter((item) => item.id !== artifact.id)]);
                    setReportListState('ready');
                    setReportDraft(null);
                  }}
                />
              )}
            </div>
          ) : null}

          {viewMode === 'report' ? (
            <div className="space-y-5 pb-6">
              {reportListError && (
                <div className="flex items-start justify-between gap-3 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3">
                  <p className="text-[11.5px] leading-5 text-rose-700">{reportListError}</p>
                  <button type="button" onClick={() => void loadReportEntries()} className="shrink-0 text-[11px] font-medium text-rose-700">重试</button>
                </div>
              )}

              {reportListState === 'loading' && (
                <div className="flex items-center justify-center gap-2 py-12 text-[12px] text-gray-400">
                  <RefreshCw size={13} className="animate-spin" /> 正在加载项目报告…
                </div>
              )}

              {reportListState === 'ready' && reportArtifacts.length === 0 && (
                <div className="rounded-xl border border-dashed border-gray-200 py-10 text-center">
                  <FileText size={22} className="mx-auto text-gray-300" />
                  <p className="mt-3 text-[13px] font-medium text-gray-600">还没有已保存的项目报告</p>
                  <p className="mt-1 text-[11px] text-gray-400">生成中的正文不会出现在这里，人工保存后才成为共享报告。</p>
                  <button type="button" onClick={() => setViewMode('blueprint')} className="mt-3 rounded-md bg-gray-900 px-3 py-1.5 text-[11px] font-medium text-white">前往报告骨架</button>
                </div>
              )}

              {reportArtifacts.map((artifact) => {
                const blocked = artifact.availability_status === 'blocked';
                const stale = artifact.availability_status === 'stale';
                return (
                  <article key={artifact.id} className={`rounded-xl border p-5 ${blocked ? 'border-rose-200 bg-rose-50/60' : stale ? 'border-amber-200 bg-amber-50/50' : 'border-gray-200 bg-white'}`}>
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="truncate text-[14px] font-semibold text-gray-900">{artifact.title}</h3>
                          <span className={`rounded-full px-2 py-0.5 text-[9.5px] font-semibold ${blocked ? 'bg-rose-100 text-rose-700' : stale ? 'bg-amber-100 text-amber-700' : 'bg-emerald-50 text-emerald-700'}`}>
                            {blocked ? '不可用' : stale ? '已过期' : '可用'}
                          </span>
                        </div>
                        <p className="mt-1 text-[10.5px] text-gray-400">版本 {artifact.latest_version} · {formatTs(artifact.updated_at)}</p>
                        {(artifact.availability_reason || artifact.stale_reasons.length > 0) && (
                          <p className={`mt-2 text-[11px] leading-5 ${blocked ? 'text-rose-700' : 'text-amber-700'}`}>
                            {artifact.availability_reason || artifact.stale_reasons.join('；')}
                          </p>
                        )}
                      </div>
                      {!blocked && (
                        <div className="flex shrink-0 flex-wrap justify-end gap-2">
                          {onOpenSavedReport && (
                            <button type="button" onClick={() => onOpenSavedReport(artifact)} className="rounded-md bg-gray-900 px-3 py-1.5 text-[11px] font-medium text-white">智能编辑</button>
                          )}
                          {(['docx', 'pdf', 'md'] as ReportFileFormat[]).map((format) => (
                            <button
                              key={format}
                              type="button"
                              disabled={Boolean(reportActionId)}
                              onClick={() => void handleDownloadSavedReport(artifact, format)}
                              className="rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-[10.5px] font-medium text-gray-600 disabled:opacity-40"
                            >
                              {reportActionId === `download:${artifact.id}:${format}` ? '处理中' : format.toUpperCase()}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                    {!blocked && artifact.latest.content_markdown && (
                      <details className="mt-4 border-t border-gray-100 pt-3">
                        <summary className="cursor-pointer text-[11px] font-medium text-gray-500">查看最近保存的正文</summary>
                        <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap font-sans text-[11.5px] leading-6 text-gray-600">{artifact.latest.content_markdown}</pre>
                      </details>
                    )}
                  </article>
                );
              })}

              {legacyReportRuns.length > 0 && (
                <section className="border-t border-gray-100 pt-4">
                  <button type="button" onClick={() => setShowLegacyReports((value) => !value)} className="flex w-full items-center justify-between text-left">
                    <span className="text-[12px] font-semibold text-gray-700">历史生成结果 · {legacyReportRuns.length}</span>
                    <span className="text-[10.5px] text-gray-400">{showLegacyReports ? '收起' : '展开'}</span>
                  </button>
                  {showLegacyReports && (
                    <div className="mt-3 space-y-2">
                      {legacyReportRuns.map((run) => (
                        <div key={run.id} className="flex items-center justify-between gap-4 rounded-lg border border-gray-100 bg-gray-50 px-4 py-3">
                          <div className="min-w-0">
                            <p className="truncate text-[11.5px] font-medium text-gray-700">{run.blueprint?.title || '历史项目报告'}</p>
                            <p className="mt-1 text-[10px] text-gray-400">{formatTs(run.updated_at)} · 尚未成为正式共享报告</p>
                          </div>
                          <button
                            type="button"
                            disabled={Boolean(reportActionId)}
                            onClick={() => void handlePromoteLegacyReport(run)}
                            className="shrink-0 rounded-md border border-gray-200 bg-white px-3 py-1.5 text-[10.5px] font-medium text-gray-700 disabled:opacity-40"
                          >
                            {reportActionId === `promote:${run.id}` ? '保存中' : '保存为新版报告'}
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </section>
              )}
            </div>
          ) : null}

          {viewMode === 'evidence' && materialModel ? (
            <div className="space-y-6">
              <section className="rounded-lg border border-gray-200 bg-gray-50/50 px-4 py-3.5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="text-[12px] font-semibold text-gray-800">任务与会议证据</p>
                    <p className="mt-1 text-[11px] leading-5 text-gray-500">
                      按时间查看已有材料的任务、会议纪要和人工里程碑。设为里程碑不会取得任务编辑权；从这里补充的文件属于事件线证据，不会冒充任务原附件。
                    </p>
                  </div>
                  <span className="rounded-md bg-white px-2 py-1 text-[10px] font-medium text-gray-500 ring-1 ring-gray-200">
                    {evidenceTaskRows.length} 项任务 · {meetingEvidenceRows.length} 条会议记录
                  </span>
                </div>
                {evidenceTaskRows.length > 0 ? (
                  <div className="mt-3 divide-y divide-gray-100 rounded-md border border-gray-100 bg-white px-3">
                    {evidenceTaskRows.map((row) => {
                      const isExpanded = expandedEvidenceTaskIds.has(row.task.id);
                      return (
                        <div key={`task-evidence:${row.task.id}`} className="py-3">
                          <div className="flex items-start justify-between gap-4">
                            <button
                              type="button"
                              onClick={() => setExpandedEvidenceTaskIds((previous) => {
                                const next = new Set(previous);
                                if (next.has(row.task.id)) next.delete(row.task.id);
                                else next.add(row.task.id);
                                return next;
                              })}
                              className="flex min-w-0 flex-1 items-start gap-2 text-left"
                              aria-expanded={isExpanded}
                            >
                              <ChevronDown
                                size={14}
                                className={`mt-0.5 shrink-0 text-gray-400 transition-transform ${isExpanded ? '' : '-rotate-90'}`}
                              />
                              <div className="min-w-0">
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="block max-w-full truncate text-[11.5px] font-medium text-gray-800">
                                  {row.task.title}
                                </span>
                                {row.isMilestone && (
                                  <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[9.5px] font-medium text-amber-700">人工里程碑</span>
                                )}
                              </div>
                              <p className="mt-1 text-[10.5px] text-gray-400">
                                {row.happenedAt ? formatTs(row.happenedAt) : '时间未填写'} · {row.attachments.length} 份材料
                              </p>
                              </div>
                            </button>
                            <div className="flex shrink-0 items-center gap-2">
                              {onOpenTask && (
                                <button
                                  type="button"
                                  onClick={() => onOpenTask(row.task)}
                                  className="rounded-md px-2 py-1 text-[10.5px] font-medium text-gray-500 hover:bg-gray-50 hover:text-gray-700"
                                >
                                  查看任务
                                </button>
                              )}
                              {row.isMilestone && snapshot.canEdit && (
                              <button
                                type="button"
                                onClick={() => openMaterialUpload(row.task)}
                                className="shrink-0 rounded-md border border-gray-200 bg-white px-2.5 py-1 text-[10.5px] font-medium text-gray-700 hover:border-gray-300"
                              >
                                {row.attachments.length > 0 ? '继续补充' : '上传证据材料'}
                              </button>
                              )}
                            </div>
                          </div>
                          {isExpanded && row.attachments.length > 0 && (
                            <div className="mt-2 space-y-1.5">
                              {row.attachments.map((attachment) => (
                                <div key={attachment.id} className="flex items-start justify-between gap-3 rounded-md bg-gray-50 px-2.5 py-2">
                                  <div className="min-w-0">
                                    <p className="truncate text-[10.5px] font-medium text-gray-700">{attachment.title}</p>
                                    <p className="mt-0.5 text-[10px] text-gray-400">
                                      {attachment.sourceKind === 'task_attachment' ? '任务原附件' : '事件线补充证据'}
                                      {attachment.purpose ? ` · ${attachment.purpose}` : ''}
                                      {` · ${attachmentParseStatusLabel(attachment.parseStatus)}`}
                                    </p>
                                  </div>
                                  {snapshot.canEdit && (canStartAttachmentParse(attachment) || canRetryAttachmentParse(attachment)) && (
                                    <button
                                      type="button"
                                      onClick={() => void handleRetryAttachment(attachment.id)}
                                      disabled={Boolean(retryingAttachmentId)}
                                      className="shrink-0 text-[10.5px] font-medium text-[#4D6BFE] disabled:opacity-50"
                                    >
                                      {retryingAttachmentId === attachment.id
                                        ? '处理中'
                                        : canRetryAttachmentParse(attachment) ? '重新解析' : '确认解析'}
                                    </button>
                                  )}
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="mt-3 rounded-md border border-dashed border-gray-200 bg-white px-3 py-4 text-center text-[11px] text-gray-400">
                    当前还没有带材料的关联任务或人工里程碑。
                  </div>
                )}
              </section>

              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 pb-4">
                <div>
                  <p className="text-[12px] font-semibold text-gray-800">全程或特定材料补充</p>
                  <p className="mt-1 text-[11px] text-gray-400">
                    {generalEvidenceAttachments.length > 0
                      ? `${generalEvidenceAttachments.length} 份材料${processingMaterialCount ? ` · ${processingMaterialCount} 份解析中` : ''}`
                      : '用于财务报销、整体成果等不便归到单个任务的材料'}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {failedMaterialAttachments.length > 0 && snapshot.canEdit && (
                    <button
                      type="button"
                      onClick={() => void handleRetryFailedMaterials()}
                      disabled={retryingFailedMaterials}
                      className="inline-flex h-8 items-center gap-1.5 rounded-md border border-gray-200 bg-white px-3 text-[11px] font-medium text-gray-700 transition hover:border-gray-300 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      <RefreshCw size={12} className={retryingFailedMaterials ? 'animate-spin' : ''} />
                      重试失败材料
                    </button>
                  )}
                  {snapshot.canEdit && (
                    <button
                      type="button"
                      onClick={() => openMaterialUpload()}
                      disabled={uploadingMaterial}
                      className="inline-flex h-8 items-center gap-1.5 rounded-md bg-gray-900 px-3 text-[11px] font-medium text-white transition hover:bg-gray-700 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {uploadingMaterial ? <RefreshCw size={12} className="animate-spin" /> : <Paperclip size={12} />}
                      新建补充材料
                    </button>
                  )}
                </div>
              </div>

              {generalEvidenceAttachments.length > 0 && (
                <div className="divide-y divide-gray-100 rounded-md border border-gray-100 bg-gray-50/40 px-3">
                  {generalEvidenceAttachments.map((attachment) => (
                    <div key={`parse-state:${attachment.id}`} className="flex items-start justify-between gap-4 py-2.5">
                      <div className="min-w-0">
                        <p className="truncate text-[11.5px] font-medium text-gray-700">{attachment.title}</p>
                        {attachment.purpose && (
                          <p className="mt-0.5 text-[10.5px] text-gray-500">用途：{attachment.purpose}</p>
                        )}
                        <p className={`mt-0.5 text-[10.5px] ${canRetryAttachmentParse(attachment) ? 'text-rose-600' : isAttachmentParsePending(attachment) ? 'text-amber-600' : 'text-gray-400'}`}>
                          {attachmentParseStatusLabel(attachment.parseStatus)}
                          {attachment.parseError ? ` · ${attachment.parseError}` : ''}
                        </p>
                        {attachment.parsedPreview && (
                          <p className="mt-1 line-clamp-2 text-[10.5px] leading-5 text-gray-500">{attachment.parsedPreview}</p>
                        )}
                      </div>
                      {snapshot.canEdit && (canStartAttachmentParse(attachment) || canRetryAttachmentParse(attachment)) && (
                        <button
                          type="button"
                          onClick={() => void handleRetryAttachment(attachment.id)}
                          disabled={Boolean(retryingAttachmentId)}
                          className="shrink-0 text-[10.5px] font-medium text-[#4D6BFE] hover:text-[#304FE0] disabled:opacity-50"
                        >
                          {retryingAttachmentId === attachment.id
                            ? '处理中'
                            : canRetryAttachmentParse(attachment) ? '重新解析' : '确认解析'}
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}

              <div className="flex items-baseline justify-between gap-4">
                <p className="text-[12px] leading-relaxed text-gray-500 max-w-2xl">会议与过程记录。会议纪要已有正文时可直接作为证据；原始录音等需要读取内容时再手动解析。</p>
                <p className="shrink-0 text-[10px] uppercase tracking-[0.14em] text-gray-400 tabular-nums">
                  共 <span className="text-gray-900 font-medium">{meetingEvidenceRows.length}</span> 条记录
                </p>
              </div>

              <div className="space-y-2">
                  {meetingEvidenceRows.length > 0 ? meetingEvidenceRows.map((material) => {
                    const imageGroups = material.attachmentGroups.filter((group) => group.isImage);
                    const docGroups = material.attachmentGroups.filter((group) => !group.isImage);
                    const downloadableAtts = material.attachments.filter((att) => resolveAttachmentUrl(att, backendBaseUrl));
                    const isDocsExpanded = docsExpandedActivities.has(material.id);
                    const isImagesExpanded = imagesExpandedActivities.has(material.id);
                    const hasAtts = material.attachments.length > 0;
                    const totalGroupCount = material.attachmentGroups.length;

                    return (
                      <article
                        key={material.id}
                        className="group relative py-4 border-b border-gray-100 last:border-0"
                      >
                        <div className="flex items-start justify-between gap-4">
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[10px]">
                              <span className="font-bold uppercase tracking-[0.16em] text-gray-400">
                                {material.sourceLabel}
                              </span>
                              {material.statusLabel && (
                                <span className="text-gray-500 font-medium">{material.statusLabel}</span>
                              )}
                              {material.happenedAt && <span className="text-gray-400 tabular-nums">{formatTs(material.happenedAt)}</span>}
                              {material.actorName && <span className="text-gray-400">{material.actorName}</span>}
                            </div>
                            <h4 className="mt-1.5 text-[14.5px] font-semibold leading-snug text-gray-900">{material.title}</h4>
                            {material.summary && (
                              <p className="mt-1 text-[12.5px] leading-6 text-gray-500 whitespace-pre-wrap">{material.summary}</p>
                            )}
                            <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[10.5px]">
                              {material.tags.map((tag) => (
                                <span key={tag} className="text-gray-500">
                                  {tag}
                                </span>
                              ))}
                              {material.duplicateCount && (
                                <span className="text-amber-700 font-medium">
                                  · 重复 {material.duplicateCount}
                                </span>
                              )}
                              {material.versionCount && (
                                <span className="text-rose-700 font-medium">
                                  · {material.versionCount} 版本
                                </span>
                              )}
                              {material.testAttachmentCount && (
                                <span className="text-orange-700 font-medium">
                                  · 测试 {material.testAttachmentCount}
                                </span>
                              )}
                            </div>
                            {material.warnings.length > 0 && (
                              <p className="mt-2 text-[11px] leading-5 text-amber-700">
                                ⚠ {material.warnings.join(' · ')}
                              </p>
                            )}
                          </div>

                          <div className="flex shrink-0 items-center gap-1.5">
                            <button
                              type="button"
                              title={isDocsExpanded ? '折叠文档' : '展开文档'}
                              disabled={docGroups.length === 0}
                              className={`inline-flex h-7 w-7 items-center justify-center rounded-md border border-gray-200 bg-white text-gray-500 transition-all ${docGroups.length === 0 ? 'cursor-not-allowed opacity-40' : isDocsExpanded ? 'bg-gray-100 text-gray-900 border-gray-300' : 'hover:border-gray-300 hover:text-gray-900 hover:bg-gray-50'}`}
                              onClick={() => {
                                if (docGroups.length === 0) return;
                                setDocsExpandedActivities((prev) => {
                                  const next = new Set(prev);
                                  if (next.has(material.id)) next.delete(material.id);
                                  else next.add(material.id);
                                  return next;
                                });
                              }}
                            >
                              <FileText size={12} />
                            </button>
                            <button
                              type="button"
                              title={isImagesExpanded ? '折叠图片' : '展开图片'}
                              disabled={imageGroups.length === 0}
                              className={`inline-flex h-7 w-7 items-center justify-center rounded-md border border-gray-200 bg-white text-gray-500 transition-all ${imageGroups.length === 0 ? 'cursor-not-allowed opacity-40' : isImagesExpanded ? 'bg-gray-100 text-gray-900 border-gray-300' : 'hover:border-gray-300 hover:text-gray-900 hover:bg-gray-50'}`}
                              onClick={() => {
                                if (imageGroups.length === 0) return;
                                setImagesExpandedActivities((prev) => {
                                  const next = new Set(prev);
                                  if (next.has(material.id)) next.delete(material.id);
                                  else next.add(material.id);
                                  return next;
                                });
                              }}
                            >
                              <Image size={12} />
                            </button>
                            <div className="relative group/dl">
                              <button
                                type="button"
                                title={downloadableAtts.length ? `下载素材附件（${downloadableAtts.length}个）` : '暂无可下载附件'}
                                disabled={downloadableAtts.length === 0}
                                className={`inline-flex h-7 w-7 items-center justify-center rounded-md border border-gray-200 bg-white text-gray-500 transition-all ${downloadableAtts.length === 0 ? 'cursor-not-allowed opacity-40' : 'hover:border-gray-300 hover:text-gray-900 hover:bg-gray-50'}`}
                                onClick={() => {
                                  for (const att of downloadableAtts) {
                                    const link = document.createElement('a');
                                    link.href = resolveAttachmentUrl(att, backendBaseUrl);
                                    link.download = att.title;
                                    link.click();
                                  }
                                }}
                              >
                                <Download size={12} />
                              </button>
                              {downloadableAtts.length > 0 && (
                                <div className="invisible group-hover/dl:visible absolute right-0 top-full z-30 mt-1 w-72 max-h-80 overflow-y-auto rounded-lg border border-gray-200 bg-white shadow-lg">
                                  <div className="border-b border-gray-100 px-3 py-2 text-[10px] font-bold uppercase tracking-[0.16em] text-gray-400">
                                    附件列表 · {downloadableAtts.length} 个
                                  </div>
                                  {downloadableAtts.map((att) => {
                                    const dlUrl = resolveAttachmentUrl(att, backendBaseUrl);
                                    const openUrl = resolveAttachmentOpenUrl(att, backendBaseUrl);
                                    return (
                                      <div key={att.id} className="flex items-center justify-between gap-2 px-3 py-2 hover:bg-gray-50">
                                        <div className="min-w-0 flex-1">
                                          <p className="truncate text-[12px] font-medium text-gray-800" title={att.title}>{att.title}</p>
                                          <p className="text-[10px] text-gray-400">{formatAttachmentBytes(att.sizeBytes)}</p>
                                        </div>
                                        <div className="flex shrink-0 items-center gap-1">
                                          {openUrl && (
                                            <a
                                              href={openUrl}
                                              target="_blank"
                                              rel="noopener noreferrer"
                                              title="在浏览器中打开"
                                              className="rounded p-1 text-gray-400 transition hover:bg-blue-50 hover:text-[#5B7BFE]"
                                            >
                                              <ExternalLink size={11} />
                                            </a>
                                          )}
                                          <a
                                            href={dlUrl}
                                            download={att.title}
                                            title="下载"
                                            className="rounded p-1 text-gray-400 transition hover:bg-gray-100 hover:text-[#5B7BFE]"
                                          >
                                            <Download size={11} />
                                          </a>
                                        </div>
                                      </div>
                                    );
                                  })}
                                </div>
                              )}
                            </div>
                          </div>
                        </div>

                        {hasAtts && !isDocsExpanded && !isImagesExpanded && (
                          <div className="mt-3 space-y-2">
                            {imageGroups.length > 0 && (
                              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                                {imageGroups.slice(0, 6).map((group) => {
                                  const url = resolveAttachmentUrl(group.primary, backendBaseUrl);
                                  const title = `${group.title} · ${fileSizeLabel(group.primary.sizeBytes)}`;
                                  const inner = (
                                    <>
                                      {url ? (
                                        <img
                                          src={url}
                                          alt={group.title}
                                          className="h-20 w-full rounded-lg object-cover"
                                          loading="lazy"
                                        />
                                      ) : (
                                        <div className="flex h-20 items-center justify-center rounded-lg bg-gray-100 text-[10px] text-gray-300">
                                          无预览
                                        </div>
                                      )}
                                      <div className="mt-1 flex items-center gap-1">
                                        <span className="truncate text-[10px] text-gray-500">{group.title}</span>
                                        {group.attachments.length > 1 && (
                                          <span className="shrink-0 rounded bg-amber-50 px-1 text-[9px] font-bold text-amber-700">
                                            x{group.attachments.length}
                                          </span>
                                        )}
                                      </div>
                                    </>
                                  );
                                  return url ? (
                                    <a
                                      key={group.id}
                                      href={url}
                                      download={group.primary.title}
                                      title={title}
                                      className="min-w-0 rounded-xl border border-gray-100 bg-gray-50 p-1.5 transition hover:border-[#C9D6FF]"
                                    >
                                      {inner}
                                    </a>
                                  ) : (
                                    <div key={group.id} title={title} className="min-w-0 rounded-xl border border-gray-100 bg-gray-50 p-1.5">
                                      {inner}
                                    </div>
                                  );
                                })}
                                {imageGroups.length > 6 && (
                                  <div className="flex h-[104px] items-center justify-center rounded-xl border border-gray-100 bg-gray-50 text-[10px] text-gray-400">
                                    另有 {imageGroups.length - 6} 组图片
                                  </div>
                                )}
                              </div>
                            )}

                            {docGroups.length > 0 && (
                              <div className="flex flex-wrap gap-1.5">
                                {docGroups.slice(0, 8).map((group) => {
                                  const att = group.primary;
                                  const badge = fileTypeBadge(att.title);
                                  const url = resolveAttachmentUrl(att, backendBaseUrl);
                                  const title = `${att.title} · ${fileSizeLabel(att.sizeBytes)}`;
                                  const content = (
                                    <>
                                      <span className="rounded px-1 py-0.5 text-[8px] font-bold" style={{ backgroundColor: badge.bg, color: badge.color }}>{badge.label}</span>
                                      <span className="truncate">{att.title}</span>
                                      {group.attachments.length > 1 && (
                                        <span className="rounded bg-amber-50 px-1 text-[9px] font-bold text-amber-700">
                                          x{group.attachments.length}
                                        </span>
                                      )}
                                      {group.versionCount && (
                                        <span className="rounded bg-rose-50 px-1 text-[9px] font-bold text-rose-700">
                                          {group.versionCount}版
                                        </span>
                                      )}
                                    </>
                                  );
                                  return url ? (
                                    <a
                                      key={group.id}
                                      href={url}
                                      download={att.title}
                                      className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-gray-200 bg-gray-50 px-2 py-1 text-[10px] text-gray-600 transition hover:border-[#C9D6FF] hover:text-[#5B7BFE]"
                                      title={title}
                                    >
                                      {content}
                                    </a>
                                  ) : (
                                    <span
                                      key={group.id}
                                      className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-gray-100 bg-gray-50 px-2 py-1 text-[10px] text-gray-300"
                                      title={title}
                                    >
                                      {content}
                                    </span>
                                  );
                                })}
                                {totalGroupCount > imageGroups.length + Math.min(docGroups.length, 8) && (
                                  <span className="rounded-lg bg-gray-50 px-2 py-1 text-[10px] text-gray-400">
                                    另有 {totalGroupCount - imageGroups.length - Math.min(docGroups.length, 8)} 组素材
                                  </span>
                                )}
                              </div>
                            )}
                          </div>
                        )}

                        {isDocsExpanded && docGroups.length > 0 && (
                          <div className="mt-3 space-y-2">
                            {docGroups.map((group) => (
                              <div key={group.id} className="space-y-1">
                                {(group.duplicateCount || group.versionCount) && (
                                  <p className="text-[10px] text-amber-700">
                                    {[
                                      group.duplicateCount ? `同名素材出现 ${group.duplicateCount} 次` : '',
                                      group.versionCount ? `${group.versionCount} 个版本，默认展示最新版本` : '',
                                    ].filter(Boolean).join(' · ')}
                                  </p>
                                )}
                                <DocContentViewer att={group.primary} backendBaseUrl={backendBaseUrl} />
                              </div>
                            ))}
                          </div>
                        )}

                        {isImagesExpanded && imageGroups.length > 0 && (
                          <div className="mt-3 grid grid-cols-2 gap-2">
                            {imageGroups.map((group) => (
                              <div key={group.id} className="space-y-1">
                                {(group.duplicateCount || group.versionCount) && (
                                  <p className="text-[10px] text-amber-700">
                                    {[
                                      group.duplicateCount ? `同名素材出现 ${group.duplicateCount} 次` : '',
                                      group.versionCount ? `${group.versionCount} 个版本，默认展示最新版本` : '',
                                    ].filter(Boolean).join(' · ')}
                                  </p>
                                )}
                                <ImageWithOcr att={group.primary} backendBaseUrl={backendBaseUrl} />
                              </div>
                            ))}
                          </div>
                        )}
                      </article>
                    );
                  }) : (
                    <div className="py-16 text-center text-[12px] text-gray-400">
                      当前还没有会议或过程记录。
                    </div>
                  )}
                </div>
            </div>
          ) : null}
        </div>
      </div>
      {materialUploadOpen && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/35 p-4" onClick={() => !uploadingMaterial && setMaterialUploadOpen(false)}>
          <div className="w-full max-w-[460px] rounded-lg border border-gray-200 bg-white p-5 shadow-2xl" onClick={(event) => event.stopPropagation()}>
            <div className="flex items-start justify-between gap-4">
              <div>
                <h3 className="text-[15px] font-semibold text-gray-900">补充证据材料</h3>
                <p className="mt-1 text-[11px] leading-5 text-gray-500">
                  {materialUploadTask
                    ? `关联到“${materialUploadTask.title}”，但不会写成该任务的原附件。`
                    : '作为整条事件线的全程或特定材料保存。'}
                </p>
              </div>
              <button
                type="button"
                aria-label="关闭补充材料"
                disabled={uploadingMaterial}
                onClick={() => setMaterialUploadOpen(false)}
                className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:bg-gray-100 hover:text-gray-700 disabled:opacity-40"
              >
                <X size={15} />
              </button>
            </div>
            <div className="mt-4 space-y-3">
              <label className="block">
                <span className="text-[11px] font-medium text-gray-700">材料名称</span>
                <input
                  value={materialUploadName}
                  onChange={(event) => setMaterialUploadName(event.target.value)}
                  placeholder="未填写时使用文件名"
                  className="mt-1 h-9 w-full rounded-md border border-gray-200 px-3 text-[12px] outline-none focus:border-[#91A7FF]"
                />
              </label>
              <label className="block">
                <span className="text-[11px] font-medium text-gray-700">用途</span>
                <textarea
                  value={materialUploadPurpose}
                  onChange={(event) => setMaterialUploadPurpose(event.target.value)}
                  placeholder="例如：用于证明本阶段已完成现场沟通"
                  rows={3}
                  className="mt-1 w-full resize-none rounded-md border border-gray-200 px-3 py-2 text-[12px] leading-5 outline-none focus:border-[#91A7FF]"
                />
              </label>
              <label className="block">
                <span className="text-[11px] font-medium text-gray-700">选择文件</span>
                <input
                  type="file"
                  onChange={(event) => setMaterialUploadFile(event.target.files?.[0] || null)}
                  className="mt-1 block w-full text-[11px] text-gray-500 file:mr-3 file:rounded-md file:border-0 file:bg-gray-100 file:px-3 file:py-2 file:text-[11px] file:font-medium file:text-gray-700"
                />
              </label>
              <p className="text-[10.5px] leading-5 text-gray-400">
                上传后只保存原件和名称。若后续主线或报告需要读取正文，再点击“解析”；图片可作为报告插图保留，无需为了展示而解析。
              </p>
              {materialActionError && <p className="text-[10.5px] text-rose-600">{materialActionError}</p>}
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                disabled={uploadingMaterial}
                onClick={() => setMaterialUploadOpen(false)}
                className="h-8 rounded-md border border-gray-200 px-3 text-[11px] font-medium text-gray-600 disabled:opacity-40"
              >
                取消
              </button>
              <button
                type="button"
                disabled={uploadingMaterial || !materialUploadFile || !materialUploadPurpose.trim()}
                onClick={() => void handleUploadMaterial()}
                className="inline-flex h-8 items-center gap-1.5 rounded-md bg-gray-900 px-3 text-[11px] font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
              >
                {uploadingMaterial && <RefreshCw size={12} className="animate-spin" />}
                {uploadingMaterial ? '正在上传' : '确认上传'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
