import React, { useEffect, useMemo, useState } from 'react';
import { AlertCircle, Bookmark, CheckSquare, FileText, Pause, Pencil, Play, Plus, RefreshCw, Search, Send, Share2, Sparkles, Target, Trash2, X } from 'lucide-react';

import type {
  MentionCandidate,
  SessionUser,
  Task,
  TaskList,
  TaskSettings,
  IntelligenceProfile,
  IntelligenceProfileMutationPayload,
  TopicCandidate,
  TopicCandidateChatMessage,
  TopicCandidateInsight,
  TopicCandidateInsightStatusResult,
  TopicContextRef,
  TopicIntelligenceDirection,
  TopicsSettings,
  TopicRadar,
  TopicRadarPayload,
  TopicRadarPreferredSource,
  TopicRadarPushFrequency,
  TopicShareRecipient,
} from '../../../shared/types';
import {
  acceptExternalEvidenceCard,
  assistRadarDraft,
  askCandidateQuestion,
  captureIntelligenceRadarTest,
  createIntelligenceProfile,
  createRadar,
  deleteIntelligenceProfile,
  deleteRadar,
  favoriteIntelligenceItem,
  getCandidateInsightStatus,
  getCandidateInsights,
  getMentionCandidates,
  prepareCandidateInsights,
  promoteCandidateTasks,
  rejectExternalEvidenceCard,
  trialRunIntelligenceProfile,
  refreshIntelligenceProfile,
  saveTaskNote,
  shareIntelligenceItem,
  suggestRadarSourceLabel,
  unfavoriteIntelligenceItem,
  updateIntelligenceProfile,
  updateRadar,
} from '../../lib/api';
import { TopicIntelInboxCard } from './TopicIntelInboxCard';

type TopicCandidateLocalPreference = {
  saved?: boolean;
  note?: string;
  tags?: string[];
};

type TopicCandidateLegacyPreference = TopicCandidateLocalPreference & {
  archived?: boolean;
  favorite?: boolean;
  favoriteNote?: string;
};

type TopicLocalState = {
  byCandidateId: Record<string, TopicCandidateLocalPreference>;
};

type TopicQuickTaskDraft = {
  title: string;
  desc: string;
  listId: string;
  priority: 'low' | 'normal' | 'high';
  dueDate: string;
  ddl: string;
  ownerId: string;
  ownerName: string;
  collaboratorIds: string[];
  note: string;
};

type TopicRadarDraft = {
  id: string;
  profileKind?: IntelligenceProfile['profileKind'];
  radarId?: string | null;
  title: string;
  prompt: string;
  focus: string[];
  excludeTerms: string[];
  timeRange: string;
  preferredSources: TopicRadarPreferredSource[];
  contextRefs: TopicContextRef[];
  shareRecipients: TopicShareRecipient[];
  priorityUrls: string[];
  fetchEnabled: boolean;
  pushFrequency: TopicRadarPushFrequency;
  pushTime: string;
  pushWeekday: number | null;
};

type TopicStatusView = 'favorite' | 'shared' | 'sent' | 'task_linked';
type TopicBadgeFilter = 'all' | 'follow_up' | 'focus' | 'none';
type TopicContextDraftType = 'custom_focus' | 'organization_type' | 'service_object' | 'issue_area' | 'region';
type TopicRadarModalMode = 'create' | 'manage';
type TopicTaskModalNotice = {
  type: 'success' | 'error' | 'info';
  text: string;
};

type AdvisorPrototypeItem = {
  id: string;
  candidate?: TopicCandidate;
  demo?: boolean;
  title: string;
  judgment: string;
  why: string;
  evidence: string;
  gap: string;
  action: string;
  tags: string[];
  profileTitle?: string;
  sourceMeta?: string;
  section?: 'brief' | 'opportunity' | 'risk' | 'evidence';
};

type TopicsManagementViewProps = {
  radars: TopicRadar[];
  intelligenceProfiles: IntelligenceProfile[];
  candidates: TopicCandidate[];
  tasks: Task[];
  activeTaskLists: TaskList[];
  effectiveTaskSettings: TaskSettings;
  topicsSettingsState: TopicsSettings;
  currentSessionUser: SessionUser | null;
  currentOperatorName: string;
  focusCandidateId?: string | null;
  onFocusCandidateHandled?: () => void;
  flash: (type: 'success' | 'error' | 'info', text: string) => void;
  onTopicsReload: () => Promise<unknown>;
  onTasksReload: () => Promise<unknown>;
};

const TOPIC_LOCAL_STATE_STORAGE_KEY = 'yiyu.workbench.topics.local-state.v2';
const TOPIC_SHARED_READ_AT_STORAGE_KEY = 'yiyu.workbench.topics.shared-read-at.v1';
const TOPIC_PAGE_SIZE = 6;
const TOPIC_DIRECTIONS: Array<{ id: TopicIntelligenceDirection; label: string }> = [
  { id: 'policy_environment', label: '政策环境' },
  { id: 'resource_collaboration', label: '资源与合作' },
  { id: 'public_opinion', label: '舆情与公众理解' },
  { id: 'industry_trend_case', label: '行业趋势与案例' },
];
const TOPIC_BADGE_FILTERS: Array<{ id: TopicBadgeFilter; label: string }> = [
  { id: 'all', label: '全部标记' },
  { id: 'follow_up', label: '待跟进' },
  { id: 'focus', label: '重点' },
  { id: 'none', label: '无标记' },
];
const TOPIC_CONTEXT_TYPE_OPTIONS: Array<{ id: TopicContextDraftType; label: string; placeholder: string }> = [
  { id: 'custom_focus', label: '自定义关注', placeholder: '例如：教师赋能、AI 陪伴、筹资转化' },
  { id: 'organization_type', label: '组织类型', placeholder: '例如：基金会、学校、社区组织' },
  { id: 'service_object', label: '服务对象', placeholder: '例如：儿童、青少年、一线社工' },
  { id: 'issue_area', label: '议题领域', placeholder: '例如：心理健康、社区治理、公益数字化' },
  { id: 'region', label: '地区范围', placeholder: '例如：广东、深圳、长三角' },
];
const TOPIC_CONTEXT_PRESETS: Record<TopicContextDraftType, string[]> = {
  custom_focus: [],
  organization_type: ['公益组织', '基金会', '学校', '社区组织', '企业 CSR'],
  service_object: ['儿童', '青少年', '老人', '残障群体', '一线社工'],
  issue_area: ['心理健康', '教育公平', '社区治理', '公益数字化', '筹资与传播'],
  region: ['全国', '广东', '深圳', '长三角', '粤港澳大湾区'],
};
const TOPIC_STATUS_VIEW_COPY: Record<TopicStatusView, { description: string; emptyTitle: string; emptyDescription: string }> = {
  favorite: {
    description: '这里集中展示当前用户收藏过的情报，不受当前方向、画像、标记或搜索条件影响。',
    emptyTitle: '当前还没有收藏情报',
    emptyDescription: '在情报卡片上点击“收藏”后，会出现在这里，方便后续复盘或集中处理。',
  },
  shared: {
    description: '这里集中展示其他成员共享给当前用户的情报，不受当前方向、画像、标记或搜索条件影响。',
    emptyTitle: '当前还没有共享给你的情报',
    emptyDescription: '同事通过情报卡片共享给你后，会出现在这里。',
  },
  sent: {
    description: '这里集中展示当前用户已经共享出去的情报，方便回看自己推荐过什么。',
    emptyTitle: '当前还没有共享出去的情报',
    emptyDescription: '你通过情报卡片共享给同事后，会出现在这里。',
  },
  task_linked: {
    description: '这里集中展示已经创建过任务的情报，不受当前方向、画像、标记或搜索条件影响。',
    emptyTitle: '当前还没有转成任务的情报',
    emptyDescription: '在情报卡片上点击“转任务”并保存后，会出现在这里。',
  },
};
const TOPIC_WEEKDAY_LABELS = ['', '一', '二', '三', '四', '五', '六', '日'];
const EMPTY_TOPIC_LOCAL_STATE: TopicLocalState = {
  byCandidateId: {},
};
const EMPTY_TOPIC_CANDIDATE_PREFERENCE: TopicCandidateLocalPreference = {
  saved: false,
  note: '',
  tags: [],
};

function normalizeCustomTags(value: unknown) {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  return value
    .map((item) => (typeof item === 'string' ? item.trim().replace(/\s+/g, ' ') : ''))
    .filter((item) => {
      const key = item.toLowerCase();
      if (!item || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

function normalizePreference(preference?: TopicCandidateLegacyPreference | null): TopicCandidateLocalPreference {
  const note =
    typeof preference?.note === 'string'
      ? preference.note.trimStart()
      : typeof preference?.favoriteNote === 'string'
        ? preference.favoriteNote.trimStart()
        : '';
  const tags = normalizeCustomTags(preference?.tags);
  return {
    saved: Boolean(preference?.saved || preference?.favorite || preference?.archived || note.trim() || tags.length),
    note,
    tags,
  };
}

function profileDisplayTitle(profile?: Pick<IntelligenceProfile, 'title' | 'radarTitle'> | null, fallback = '情报画像') {
  const raw = String(profile?.title || profile?.radarTitle || fallback).trim() || fallback;
  return raw.replace(/雷达/g, '画像');
}

function profileSourceTitle(
  profile: Pick<IntelligenceProfile, 'title' | 'radarTitle'> | null | undefined,
  radar: Pick<TopicRadar, 'title'> | null | undefined,
  fallback = '未命名画像',
) {
  const raw = profile ? profileDisplayTitle(profile, fallback) : String(radar?.title || fallback);
  return raw.replace(/雷达/g, '画像');
}

function normalizeTopicLocalState(input: unknown): TopicLocalState {
  if (!input || typeof input !== 'object' || typeof (input as TopicLocalState).byCandidateId !== 'object') {
    return EMPTY_TOPIC_LOCAL_STATE;
  }

  const nextByCandidateId: Record<string, TopicCandidateLocalPreference> = {};
  Object.entries((input as TopicLocalState).byCandidateId).forEach(([candidateId, preference]) => {
    nextByCandidateId[candidateId] = normalizePreference(preference as TopicCandidateLegacyPreference);
  });

  return { byCandidateId: nextByCandidateId };
}

function topicLocalStateStorageKey(viewerId: string) {
  return `${TOPIC_LOCAL_STATE_STORAGE_KEY}:${normalizeTopicIdentity(viewerId) || 'local-device-user'}`;
}

function readTopicLocalState(viewerId: string): TopicLocalState {
  if (typeof window === 'undefined') return EMPTY_TOPIC_LOCAL_STATE;
  try {
    const raw = window.localStorage.getItem(topicLocalStateStorageKey(viewerId));
    if (!raw) return EMPTY_TOPIC_LOCAL_STATE;
    return normalizeTopicLocalState(JSON.parse(raw));
  } catch {
    return EMPTY_TOPIC_LOCAL_STATE;
  }
}

function writeTopicLocalState(state: TopicLocalState, viewerId: string) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(topicLocalStateStorageKey(viewerId), JSON.stringify(state));
  } catch {
    // In some Electron/browser contexts storage may be unavailable or read-only.
  }
}

function readSharedReadAtState(): Record<string, string> {
  if (typeof window === 'undefined') return {};
  try {
    const raw = window.localStorage.getItem(TOPIC_SHARED_READ_AT_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function writeSharedReadAtState(state: Record<string, string>) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(TOPIC_SHARED_READ_AT_STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Local unread markers are best-effort only.
  }
}

function currentIsoWeekday() {
  const day = new Date().getDay();
  return day === 0 ? 7 : day;
}

function weekdayLabel(value?: number | null) {
  return TOPIC_WEEKDAY_LABELS[value || currentIsoWeekday()] || TOPIC_WEEKDAY_LABELS[currentIsoWeekday()];
}

function pushFrequencyLabel(frequency: TopicRadarPushFrequency, weekday?: number | null) {
  if (frequency === 'daily') return '每天';
  if (frequency === 'workday') return '工作日';
  if (frequency === 'weekly') return `每周（${weekdayLabel(weekday)}）`;
  return '仅手动';
}

function formatTopicDateTime(value?: string | null) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function topicRadarStatusLabel(radar: { status?: string | null; fetchEnabled?: boolean | null }) {
  if (radar.status === 'archived') return '已归档';
  if (radar.status === 'paused' || !radar.fetchEnabled) return '已暂停';
  if (radar.status === 'trial') return '试运行';
  return '运行中';
}

function topicFetchStatusLabel(status?: string | null) {
  if (status === 'done') return '抓取完成';
  if (status === 'running') return '抓取中';
  if (status === 'fetch_success') return '抓取完成，待解析';
  if (status === 'fetch_failed') return '抓取失败';
  if (status === 'parsing') return '解析中';
  if (status === 'parse_success') return '解析完成';
  if (status === 'parse_failed') return '解析失败';
  if (status === 'queued') return '等待抓取';
  return '暂无记录';
}

function nextPushTextForRadar(radar: { fetchEnabled?: boolean | null; pushFrequency?: TopicRadarPushFrequency | null; pushTime?: string | null; pushWeekday?: number | null; nextPushAt?: string | null }) {
  if (!radar.fetchEnabled) return '自动抓取已暂停';
  if (radar.nextPushAt) return `预计 ${formatTopicDateTime(radar.nextPushAt)}`;
  const time = radar.pushTime || '09:00';
  const frequency = radar.pushFrequency || 'manual';
  if (frequency === 'daily') return `每天 ${time} 推送`;
  if (frequency === 'workday') return `工作日 ${time} 推送`;
  if (frequency === 'weekly') return `每周（${weekdayLabel(radar.pushWeekday)}）${time} 推送`;
  return '仅手动试跑';
}

function radarFetchSummaryText(fetch?: TopicRadar['lastFetch'] | null) {
  if (!fetch) return '还没有试跑记录';
  const pieces = [
    `候选 ${fetch.candidateCount ?? fetch.fetchedCount ?? 0}`,
    `入库 ${fetch.insertedCount ?? fetch.createdCount ?? 0}`,
    `重复 ${fetch.duplicateCount ?? fetch.skippedCount ?? 0}`,
  ];
  const parseFailedCount = fetch.parseFailedCount || 0;
  const sourceFailedCount = fetch.sourceFailedCount || 0;
  if (parseFailedCount > 0) pieces.push(`解析失败 ${parseFailedCount}`);
  if (sourceFailedCount > 0) pieces.push(`来源失败 ${sourceFailedCount}`);
  return pieces.join(' / ');
}

function profileScopeLabel(profile: IntelligenceProfile) {
  if (profile.scopeType === 'organization') return '组织画像';
  if (profile.scopeType === 'client') return '客户画像';
  if (profile.scopeType === 'project_module') return '项目画像';
  return '情报画像';
}

function profileStatusLabel(profile: IntelligenceProfile) {
  if (profile.status === 'ready') return profile.generator === 'ai' ? 'AI 已生成' : '已生成';
  if (profile.status === 'fallback') return '规则画像';
  if (profile.status === 'failed') return '生成失败';
  return '待生成';
}

function profileDirectionLabel(direction: TopicIntelligenceDirection) {
  return TOPIC_DIRECTIONS.find((item) => item.id === direction)?.label || '综合情报';
}

function clampTopicPage(page: number, totalCount: number) {
  const totalPages = Math.max(1, Math.ceil(totalCount / TOPIC_PAGE_SIZE));
  return Math.min(Math.max(1, page), totalPages);
}

function shareRecordTime(value?: string | null) {
  const time = new Date(value || '').getTime();
  return Number.isNaN(time) ? 0 : time;
}

function latestReceivedShareTime(candidate: TopicCandidate) {
  return Math.max(0, ...(candidate.viewerShareRecords || []).map((record) => shareRecordTime(record.createdAt)));
}

function latestSentShareTime(candidate: TopicCandidate) {
  return Math.max(0, ...(candidate.viewerSentShareRecords || []).map((record) => shareRecordTime(record.createdAt)));
}

function buildTopicAttachmentNote(
  candidate: TopicCandidate,
  radarTitle: string,
  insight: TopicCandidateInsight | null | undefined,
  operatorNote: string,
) {
  const lines = [
    '【情报来源】',
    `标题：${candidate.title}`,
    `来源：${candidate.source}`,
    `来源画像：${radarTitle}`,
  ];

  if (candidate.publishedAt) {
    lines.push(`发布时间：${candidate.publishedAt}`);
  }
  lines.push(`情报 ID：${candidate.id}`);
  if (candidate.sourceUrl) {
    lines.push(`外部信息源：${candidate.sourceUrl}`);
  }

  const relationReasons = [
    candidate.whyRecommended?.trim() || '',
    candidate.relevanceReason?.trim() || '',
    ...(insight?.recommendationReasons?.filter((item) => item.trim()) || []),
  ].filter(Boolean);
  const keyPoints = insight?.keyPoints?.filter((item) => item.trim()) || [];
  const practicalUses = insight?.practicalUses?.filter((item) => item.trim()) || [];
  const editorialNote = insight?.editorialNote?.trim() || '';
  const discussionPrompts = insight?.discussionPrompts?.filter((item) => item.trim()) || [];
  const sentShares = candidate.viewerSentShareRecords || [];
  const receivedShares = candidate.viewerShareRecords || [];

  lines.push('');
  lines.push('【AI 摘要】');
  lines.push(insight?.overview?.trim() || candidate.summary || '当前只有原始摘要，尚未形成完整综述。');

  lines.push('');
  lines.push('【为什么可能相关】');
  if (relationReasons.length) {
    relationReasons.forEach((item, index) => lines.push(`${index + 1}. ${item}`));
  } else {
    lines.push(`1. 这篇内容当前来自「${radarTitle}」画像，建议先按这个主题核对。`);
  }

  lines.push('');
  lines.push('【建议动作】');
  lines.push(candidate.suggestedAction?.trim() || '建议先核对原文，再决定是否需要继续推进。');

  lines.push('');
  lines.push('【核心观点】');
  if (keyPoints.length) {
    keyPoints.forEach((item, index) => lines.push(`${index + 1}. ${item}`));
  } else {
    lines.push('1. 当前还没有稳定的核心观点提炼，建议直接点开原文。');
  }

  lines.push('');
  lines.push('【机会或风险判断】');
  lines.push(editorialNote || '当前还没有稳定的机会或风险判断，建议先结合原文和核心观点继续讨论。');

  lines.push('');
  lines.push('【可继续展开】');
  if (practicalUses.length) {
    practicalUses.forEach((item, index) => lines.push(`${index + 1}. ${item}`));
  } else {
    lines.push('1. 后续可围绕它是否值得写成文章、是否需要团队跟进继续讨论。');
  }

  lines.push('');
  lines.push('【值得继续追问的问题】');
  if (discussionPrompts.length) {
    discussionPrompts.forEach((item, index) => lines.push(`${index + 1}. ${item}`));
  } else {
    lines.push('1. 这篇内容背后最值得继续追问的变化，到底是什么？');
  }

  if (receivedShares.length || sentShares.length) {
    lines.push('');
    lines.push('【共享记录】');
    receivedShares.forEach((share) => {
      lines.push(`收到共享：${share.sharedByName || share.sharedBy || '未知成员'}${share.reason ? `；推荐理由：${share.reason}` : ''}`);
    });
    sentShares.forEach((share) => {
      const names = (share.sharedToRecipients || []).map((item) => item.fullName || item.userId).filter(Boolean).join('、');
      lines.push(`我已共享给：${names || share.sharedTo.join('、') || '已配置接收人'}${share.reason ? `；推荐理由：${share.reason}` : ''}`);
    });
  }

  if (operatorNote.trim()) {
    lines.push('');
    lines.push('给同事的补充：');
    lines.push(operatorNote.trim());
  }

  return lines.join('\n');
}

function buildTopicTaskDescription(candidate: TopicCandidate, radarTitle: string, draftDesc: string) {
  const lines = [
    draftDesc.trim() || '请结合这条情报判断是否需要继续跟进。',
    '',
    '【情报原文】',
    `情报标题：${candidate.title}`,
    `来源画像：${radarTitle}`,
    `来源：${candidate.source}`,
    `情报 ID：${candidate.id}`,
    '查看方式：在任务卡片中点击“查看情报原文”，会回到资讯情报站里的这条情报。',
  ];
  if (candidate.sourceUrl) {
    lines.push('');
    lines.push('【外部来源】');
    lines.push(`信息源链接：${candidate.sourceUrl}`);
  } else {
    lines.push('');
    lines.push('【外部来源】');
    lines.push('当前情报没有可打开的外部信息源链接。');
  }
  return lines.join('\n');
}

function buildTopicTaskAutoShareReason(candidate: TopicCandidate) {
  return `因情报「${candidate.title}」已转成任务，系统自动共享给负责人/协作者，便于查看情报原文。`;
}

function topicShareRecipientFromAssignee(assignee: MentionCandidate): TopicShareRecipient {
  return {
    userId: assignee.id,
    fullName: assignee.fullName,
    email: assignee.email || null,
  };
}

function topicShareRecipientFromOwner(ownerId: string | null, ownerName: string): TopicShareRecipient | null {
  const normalizedOwnerId = ownerId?.trim();
  const normalizedOwnerName = ownerName.trim();
  if (!normalizedOwnerId && !normalizedOwnerName) return null;
  return {
    userId: normalizedOwnerId || normalizedOwnerName,
    fullName: normalizedOwnerName || normalizedOwnerId || '未命名成员',
    email: null,
  };
}

function mergeTopicShareRecipients(recipients: Array<TopicShareRecipient | null | undefined>) {
  const seen = new Set<string>();
  const merged: TopicShareRecipient[] = [];
  recipients.forEach((recipient) => {
    if (!recipient) return;
    const userId = recipient.userId.trim();
    const key = normalizeTopicIdentity(userId || recipient.email || recipient.fullName);
    if (!key || seen.has(key)) return;
    seen.add(key);
    merged.push({
      userId: userId || recipient.fullName,
      fullName: recipient.fullName || userId || recipient.email || '未命名成员',
      email: recipient.email || null,
    });
  });
  return merged;
}

function filterOutCurrentViewerRecipients(recipients: TopicShareRecipient[], currentViewerId: string, currentSessionUser: SessionUser | null, currentOperatorName: string) {
  const viewerAliases = new Set(
    [currentViewerId, currentSessionUser?.id, currentSessionUser?.email, currentSessionUser?.fullName, currentOperatorName]
      .map((item) => normalizeTopicIdentity(item))
      .filter(Boolean),
  );
  return recipients.filter((recipient) => {
    const aliases = [recipient.userId, recipient.email || '', recipient.fullName]
      .map((item) => normalizeTopicIdentity(item))
      .filter(Boolean);
    return !aliases.some((alias) => viewerAliases.has(alias));
  });
}

function elapsedSeconds(startedAt: number | null, now: number) {
  if (!startedAt) return 0;
  return Math.max(0, Math.floor((now - startedAt) / 1000));
}

function captureProgressText(seconds: number) {
  if (seconds < 4) return '正在读取画像配置，并准备搜索标签。';
  if (seconds < 14) return '正在根据画像概况、关注重点和标签检索公开来源。';
  if (seconds < 28) return '正在解析、翻译、去重，并判断是否能入库为新情报。';
  return '仍在等待外部来源返回；已有情报不会被清空，你也可以稍后查看结果。';
}

function insightProgressText(seconds: number) {
  if (seconds < 6) return '正在读取原文、附件解析结果和关联画像。';
  if (seconds < 22) return '正在调用已配置的 AI 生成深度分析。';
  if (seconds < 42) return '正在整理结果并写入缓存，完成后会自动显示。';
  return '仍在等待 AI 或外部原文返回；你可以先继续浏览，完成后会缓存。';
}

function latestFetchStatusNote(fetch?: TopicRadar['lastFetch'] | null) {
  if (fetch?.failureReason?.trim()) return fetch.failureReason.trim();
  const statusLog = fetch?.statusLog || [];
  for (let index = statusLog.length - 1; index >= 0; index -= 1) {
    const note = statusLog[index]?.note?.trim();
    if (note) return note;
  }
  return '';
}

function captureResultMessage(result: { fetchedCount: number; createdCount: number; skippedCount: number; parseFailedCount?: number; sourceFailedCount?: number }) {
  const fetched = result.fetchedCount || 0;
  const created = result.createdCount || 0;
  const skipped = result.skippedCount || 0;
  const parseFailed = result.parseFailedCount || 0;
  const sourceFailed = result.sourceFailedCount || 0;
  if (created > 0) {
    return `试跑完成：抓到 ${fetched} 条线索，新增 ${created} 篇情报。`;
  }
  if (fetched > 0) {
    const details = [`重复 ${skipped} 条`];
    if (parseFailed > 0) details.push(`解析失败 ${parseFailed} 条`);
    if (sourceFailed > 0) details.push(`来源失败 ${sourceFailed} 个`);
    return `试跑完成：抓到 ${fetched} 条线索，但没有新增情报（${details.join('，')}）。`;
  }
  if (sourceFailed > 0) {
    return `试跑完成：没有生成新情报，且有 ${sourceFailed} 个来源访问失败。可以调整画像概况，或补充重点关注网址后再试。`;
  }
  return '试跑完成：没有抓到新的候选线索。AI 会根据画像信息生成搜索标签并筛选线索，但不会凭空生成外部资讯；可以补充重点关注网址或放宽时间范围后再试。';
}

function numericAdvisorScore(value: unknown, fallback = 0) {
  const numeric = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? Math.max(0, Math.min(100, numeric)) : fallback;
}

function candidateRecommendationScore(candidate: TopicCandidate) {
  const confidence = numericAdvisorScore(candidate.confidenceScore);
  const match = numericAdvisorScore(candidate.matchStrength);
  const credibility = numericAdvisorScore(candidate.credibilityScore);
  const badgeBonus = candidate.primaryBadge === 'follow_up' ? 9 : candidate.primaryBadge === 'focus' ? 5 : 0;
  const shareBonus = candidate.viewerSharedToMe ? 4 : 0;
  const fallback = candidate.primaryBadge ? 66 : 50;
  return Math.max(confidence, match * 0.58 + credibility * 0.32 + badgeBonus + shareBonus, fallback);
}

function candidateAdvisorCorpus(candidate: TopicCandidate) {
  return [
    candidate.title,
    candidate.summary,
    candidate.source,
    candidate.relevanceReason,
    candidate.whyRecommended,
    candidate.suggestedAction,
    candidate.badgeReason,
    typeof candidate.matchedIntent?.['query'] === 'string' ? candidate.matchedIntent['query'] : '',
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
}

function candidateLooksLikeOpportunity(candidate: TopicCandidate) {
  if (candidate.primaryDirection === 'policy_environment' || candidate.primaryDirection === 'resource_collaboration') return true;
  const corpus = candidateAdvisorCorpus(candidate);
  return /资助|基金|申报|合作|伙伴|政策窗口|招募|征集|项目机会|资源|补贴|采购/.test(corpus);
}

function candidateLooksLikeRisk(candidate: TopicCandidate) {
  if (candidate.primaryDirection === 'public_opinion') return true;
  const corpus = candidateAdvisorCorpus(candidate);
  return /风险|舆情|争议|投诉|监管|处罚|负面|预警|变化|收紧|质疑|危机/.test(corpus);
}

function advisorSourceTag(candidate: TopicCandidate) {
  const sourceQuality = candidate.sourceQuality || {};
  const level = typeof sourceQuality['credibilityLevel'] === 'string'
    ? sourceQuality['credibilityLevel']
    : typeof sourceQuality['level'] === 'string'
      ? sourceQuality['level']
      : '';
  if (/official|gov|authority|high|官方|权威/.test(level)) return '官方来源';
  if (/media|institution|ngo|机构|媒体/.test(level)) return '机构来源';
  if (candidate.sourceUrl && /\.(gov|edu)(\.|\/|$)/i.test(candidate.sourceUrl)) return '官方来源';
  if (candidate.sourceUrl) return '公开来源';
  return '来源待核验';
}

function advisorRelevanceTag(candidate: TopicCandidate) {
  const match = numericAdvisorScore(candidate.matchStrength);
  if (candidate.primaryBadge === 'follow_up' || match >= 70) return '强相关';
  if (candidate.primaryBadge === 'focus' || match >= 45) return '可能相关';
  return '待判断';
}

function advisorPriorityTag(candidate: TopicCandidate) {
  const text = [candidate.primaryBadge, candidate.suggestedAction, candidate.whyRecommended, candidate.relevanceReason]
    .filter(Boolean)
    .join(' ');
  if (candidate.primaryBadge === 'follow_up' || /今天|尽快|立即|转任务|跟进|申报|截止|窗口/.test(text)) return '建议今天处理';
  if (candidate.primaryBadge === 'focus' || /复盘|观察|讨论|评估|研究/.test(text)) return '本周重点观察';
  return '先核验价值';
}

function candidateSortTime(candidate: TopicCandidate) {
  return new Date(candidate.publishedAt || candidate.createdAt).getTime();
}

function candidateMatchesBadgeFilter(candidate: TopicCandidate, filter: TopicBadgeFilter) {
  if (filter === 'all') return true;
  if (filter === 'none') return !candidate.primaryBadge;
  return candidate.primaryBadge === filter;
}

function normalizeTagDraft(value: string) {
  return value.trim().replace(/\s+/g, ' ');
}

function normalizeTopicIdentity(value?: string | null) {
  return (value || '').trim().toLowerCase();
}

function topicViewerAliases(user: SessionUser | null) {
  const aliases = new Set<string>();
  [user?.id, user?.email, user?.fullName].forEach((value) => {
    const normalized = normalizeTopicIdentity(value);
    if (normalized) aliases.add(normalized);
  });
  if (user?.id === 'local-device-user' || user?.email === 'local@device.yiyu') {
    aliases.add('local_user');
    aliases.add('local-device-user');
  }
  return aliases;
}

function isLocalTopicViewer(user: SessionUser | null) {
  return !user || user.id === 'local-device-user' || user.organizationId === 'local-device' || user.email === 'local@device.yiyu';
}

function topicRadarBelongsToViewer(radar: TopicRadar, viewerAliases: Set<string>, localViewer: boolean) {
  if (radar.autoGenerated) return true;
  const createdBy = normalizeTopicIdentity(radar.createdBy || '');
  if (!createdBy) return localViewer;
  return viewerAliases.has(createdBy);
}

export function TopicsManagementView({
  radars,
  intelligenceProfiles,
  candidates,
  tasks,
  activeTaskLists,
  effectiveTaskSettings,
  topicsSettingsState,
  currentSessionUser,
  currentOperatorName,
  focusCandidateId,
  onFocusCandidateHandled,
  flash,
  onTopicsReload,
  onTasksReload,
}: TopicsManagementViewProps) {
  const currentViewerId = currentSessionUser?.id || 'local-device-user';
  const [selectedRadarId, setSelectedRadarId] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCandidateId, setSelectedCandidateId] = useState('');
  const [activeDirection, setActiveDirection] = useState<TopicIntelligenceDirection>('policy_environment');
  const [badgeFilter, setBadgeFilter] = useState<TopicBadgeFilter>('all');
  const [mainPage, setMainPage] = useState(1);
  const [statusModalView, setStatusModalView] = useState<TopicStatusView | null>(null);
  const [statusModalPage, setStatusModalPage] = useState(1);
  const [expandedCandidateId, setExpandedCandidateId] = useState('');
  const [tagDraft, setTagDraft] = useState('');
  const [localState, setLocalState] = useState<TopicLocalState>(() => readTopicLocalState(currentViewerId));
  const [sharedReadAtByViewer, setSharedReadAtByViewer] = useState<Record<string, string>>(() => readSharedReadAtState());
  const [editingPrefIndex, setEditingPrefIndex] = useState<number | null>(null);
  const [radarModalMode, setRadarModalMode] = useState<TopicRadarModalMode>('create');
  const [tempPref, setTempPref] = useState<TopicRadarDraft | null>(null);
  const [preferredSourceDraft, setPreferredSourceDraft] = useState('');
  const [contextRefDraft, setContextRefDraft] = useState('');
  const [contextRefType, setContextRefType] = useState<TopicContextDraftType>('custom_focus');
  const [radarShareOptions, setRadarShareOptions] = useState<MentionCandidate[]>([]);
  const [isLoadingRadarShareOptions, setIsLoadingRadarShareOptions] = useState(false);
  const [isAssistingRadar, setIsAssistingRadar] = useState(false);
  const [isGeneratingSourceLabel, setIsGeneratingSourceLabel] = useState(false);
  const [isSavingRadarConfig, setIsSavingRadarConfig] = useState(false);
  const [radarTogglePendingId, setRadarTogglePendingId] = useState<string | null>(null);
  const [radarDeletePendingId, setRadarDeletePendingId] = useState<string | null>(null);
  const [radarModalNotice, setRadarModalNotice] = useState<string | null>(null);
  const [captureTestingRadarId, setCaptureTestingRadarId] = useState<string | null>(null);
  const [captureTestingStartedAt, setCaptureTestingStartedAt] = useState<number | null>(null);
  const [profileTrialPendingId, setProfileTrialPendingId] = useState<string | null>(null);
  const [profileRefreshPendingId, setProfileRefreshPendingId] = useState<string | null>(null);
  const [globalMessage, setGlobalMessage] = useState<string | null>(null);
  const [shareCandidateId, setShareCandidateId] = useState<string | null>(null);
  const [shareReason, setShareReason] = useState('');
  const [shareModalNotice, setShareModalNotice] = useState<string | null>(null);
  const [isSharing, setIsSharing] = useState(false);
  const [favoriteOverrides, setFavoriteOverrides] = useState<Record<string, boolean>>({});
  const [favoritePendingIds, setFavoritePendingIds] = useState<Set<string>>(() => new Set());
  const [evidenceReviewPendingId, setEvidenceReviewPendingId] = useState<string | null>(null);
  const [insightCache, setInsightCache] = useState<Record<string, TopicCandidateInsight>>({});
  const [insightStatusByCandidateId, setInsightStatusByCandidateId] = useState<Record<string, TopicCandidateInsightStatusResult>>({});
  const [insightLoadingId, setInsightLoadingId] = useState<string | null>(null);
  const [insightLoadingStartedAt, setInsightLoadingStartedAt] = useState<number | null>(null);
  const [topicUiTick, setTopicUiTick] = useState(() => Date.now());
  const [chatByCandidateId, setChatByCandidateId] = useState<Record<string, TopicCandidateChatMessage[]>>({});
  const [chatDraftByCandidateId, setChatDraftByCandidateId] = useState<Record<string, string>>({});
  const [chatLoadingCandidateId, setChatLoadingCandidateId] = useState<string | null>(null);
  const [taskModalCandidateId, setTaskModalCandidateId] = useState<string | null>(null);
  const [taskDraft, setTaskDraft] = useState<TopicQuickTaskDraft | null>(null);
  const [taskAssignees, setTaskAssignees] = useState<MentionCandidate[]>([]);
  const [taskModalNotice, setTaskModalNotice] = useState<TopicTaskModalNotice | null>(null);
  const [createdTopicTaskId, setCreatedTopicTaskId] = useState<string | null>(null);
  const [isPreparingTaskModal, setIsPreparingTaskModal] = useState(false);
  const [isSubmittingTask, setIsSubmittingTask] = useState(false);

  const defaultListId = effectiveTaskSettings.defaultListId || activeTaskLists[0]?.id || 'list-0';
  const radarMap = useMemo(() => new Map(radars.map((item) => [item.id, item])), [radars]);
  const profileByRadarId = useMemo(() => {
    const next = new Map<string, IntelligenceProfile>();
    intelligenceProfiles.forEach((profile) => {
      if (profile.radarId) next.set(profile.radarId, profile);
    });
    return next;
  }, [intelligenceProfiles]);
  const viewerAliases = useMemo(() => topicViewerAliases(currentSessionUser), [
    currentSessionUser?.email,
    currentSessionUser?.fullName,
    currentSessionUser?.id,
  ]);
  const localViewer = isLocalTopicViewer(currentSessionUser);
  const viewerRadars = useMemo(
    () => radars.filter((radar) => topicRadarBelongsToViewer(radar, viewerAliases, localViewer)),
    [localViewer, radars, viewerAliases],
  );
  const visibleProfiles = useMemo(
    () => intelligenceProfiles.filter((profile) => !profile.deletedAt),
    [intelligenceProfiles],
  );
  const viewerRadarIds = useMemo(() => new Set([
    ...viewerRadars.map((radar) => radar.id),
    ...visibleProfiles.map((profile) => profile.radarId || '').filter(Boolean),
  ]), [visibleProfiles, viewerRadars]);
  const mainCandidates = useMemo(
    () => candidates.filter((candidate) => viewerRadarIds.has(candidate.radarId) || Boolean(candidate.viewerSharedToMe) || candidate.id === focusCandidateId),
    [candidates, focusCandidateId, viewerRadarIds],
  );
  const fetchHealthSummary = useMemo(() => {
    const fetches = viewerRadars.map((radar) => radar.lastFetch).filter(Boolean) as NonNullable<TopicRadar['lastFetch']>[];
    const latestFetch = [...fetches].sort((left, right) => (
      new Date(right.finishedAt || right.startedAt || right.createdAt).getTime() -
      new Date(left.finishedAt || left.startedAt || left.createdAt).getTime()
    ))[0] || null;
    const failedCount = fetches.filter((fetch) => fetch.failureType || fetch.status === 'fetch_failed' || fetch.status === 'parse_failed').length;
    const runningCount = fetches.filter((fetch) => fetch.status === 'running' || fetch.status === 'parsing').length;
    const createdCount = fetches.reduce((sum, fetch) => sum + (fetch.createdCount || 0), 0);
    const sourceCount = fetches.reduce((sum, fetch) => sum + (fetch.sourceDiagnostics?.length || 0), 0);
    return {
      latestFetch,
      latestNote: latestFetchStatusNote(latestFetch),
      failedCount,
      runningCount,
      createdCount,
      sourceCount,
    };
  }, [viewerRadars]);
  const relatedTasksByCandidate = useMemo(() => {
    const grouped = new Map<string, Task[]>();
    tasks.forEach((task) => {
      if (task.sourceType !== 'topic_candidate' || !task.sourceId) return;
      const rows = grouped.get(task.sourceId) || [];
      rows.push(task);
      grouped.set(task.sourceId, rows);
    });
    return grouped;
  }, [tasks]);
  const radarCards = useMemo(() => {
    const visible = visibleProfiles.map((profile) => ({
      id: profile.id,
      radarId: profile.radarId || null,
      title: profileDisplayTitle(profile),
      prompt: profile.adminSummaryOverride || profile.effectiveSummary || profile.summary || '',
      focus: profile.adminFocus || [],
      excludeTerms: profile.adminExcludeTerms || [],
      timeRange: topicsSettingsState.defaultTimeRange,
      preferredSources: [] as TopicRadarPreferredSource[],
      contextRefs: [],
      shareRecipients: [],
      priorityUrls: profile.adminPriorityUrls || [],
      status: profile.adminPushEnabled ? 'running' : 'trial',
      fetchEnabled: Boolean(profile.adminPushEnabled),
      pushFrequency: profile.adminPushFrequency || 'manual',
      pushTime: profile.adminPushTime || '09:00',
      pushWeekday: profile.adminPushWeekday || null,
      profileId: profile.id,
      profileKind: profile.profileKind,
      systemManaged: profile.profileKind !== 'custom',
      candidateCount: candidates.filter((candidate) => candidate.radarId === profile.radarId).length,
      nextPushAt: null,
      lastAutoFetchAt: null,
      lastPushedAt: null,
      lastFetch: profile.lastFetch || null,
      createdAt: profile.createdAt,
      updatedAt: profile.updatedAt || profile.createdAt,
    }));
    visible.push({
      id: 'placeholder-new',
      radarId: null,
      title: '',
      prompt: '',
      focus: [],
      excludeTerms: [],
      timeRange: topicsSettingsState.defaultTimeRange,
      preferredSources: [],
      contextRefs: [],
      shareRecipients: [],
      priorityUrls: [],
      status: 'trial',
      fetchEnabled: false,
      pushFrequency: 'manual' as TopicRadarPushFrequency,
      pushTime: '09:00',
      pushWeekday: currentIsoWeekday(),
      profileId: null,
      profileKind: 'custom' as const,
      systemManaged: false,
      candidateCount: 0,
      nextPushAt: null,
      lastAutoFetchAt: null,
      lastPushedAt: null,
      lastFetch: null,
      createdAt: '',
      updatedAt: '',
    });
    return visible;
  }, [candidates, topicsSettingsState.defaultTimeRange, visibleProfiles]);

  const preferenceOf = (candidateId: string) => localState.byCandidateId[candidateId] || EMPTY_TOPIC_CANDIDATE_PREFERENCE;
  const setFavoriteOverride = (candidateId: string, value: boolean | null) => {
    setFavoriteOverrides((prev) => {
      const next = { ...prev };
      if (value === null) {
        delete next[candidateId];
      } else {
        next[candidateId] = value;
      }
      return next;
    });
  };
  const setFavoritePending = (candidateId: string, pending: boolean) => {
    setFavoritePendingIds((prev) => {
      const next = new Set(prev);
      if (pending) {
        next.add(candidateId);
      } else {
        next.delete(candidateId);
      }
      return next;
    });
  };
  const isSavedCandidate = (candidate: TopicCandidate, preference = preferenceOf(candidate.id)) => {
    const override = favoriteOverrides[candidate.id];
    if (typeof override === 'boolean') return override;
    return Boolean(candidate.viewerFavorite || preference.saved);
  };
  const isCandidateTaskLinked = (candidate: TopicCandidate) =>
    Boolean(candidate.convertedTaskId || (relatedTasksByCandidate.get(candidate.id) || []).length > 0);
  const updateLocalPreference = (candidateId: string, patch: Partial<TopicCandidateLocalPreference>) => {
    setLocalState((prev) => {
      const current = prev.byCandidateId[candidateId] || EMPTY_TOPIC_CANDIDATE_PREFERENCE;
      const nextPreference = normalizePreference({
        ...current,
        ...patch,
      });
      const shouldRemove =
        !nextPreference.saved &&
        !nextPreference.note?.trim() &&
        !(nextPreference.tags?.length);
      const nextByCandidateId = { ...prev.byCandidateId };
      if (shouldRemove) {
        delete nextByCandidateId[candidateId];
      } else {
        nextByCandidateId[candidateId] = nextPreference;
      }
      const next: TopicLocalState = {
        byCandidateId: nextByCandidateId,
      };
      writeTopicLocalState(next, currentViewerId);
      return next;
    });
  };

  const viewCounts = useMemo(() => {
    const counts = {
      favorite: 0,
      shared: 0,
      sent: 0,
      task_linked: 0,
    };
    candidates.forEach((candidate) => {
      const preference = preferenceOf(candidate.id);
      const saved = isSavedCandidate(candidate, preference);
      if (saved) counts.favorite += 1;
      if (candidate.viewerSharedToMe) counts.shared += 1;
      if (candidate.viewerSharedByMe) counts.sent += 1;
      if (viewerRadarIds.has(candidate.radarId) && isCandidateTaskLinked(candidate)) counts.task_linked += 1;
    });
    return counts;
  }, [candidates, favoriteOverrides, localState, relatedTasksByCandidate, viewerRadarIds]);
  const sharedInboxReadAt = sharedReadAtByViewer[currentViewerId] || '';
  const sharedInboxUnreadCount = useMemo(() => {
    const readAtTime = shareRecordTime(sharedInboxReadAt);
    return candidates.filter((candidate) => (
      Boolean(candidate.viewerSharedToMe) && latestReceivedShareTime(candidate) > readAtTime
    )).length;
  }, [candidates, sharedInboxReadAt]);

  const filteredCandidates = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    return mainCandidates
      .filter((candidate) => {
        const preference = preferenceOf(candidate.id);
        const customTags = [...(preference.tags || []), ...(candidate.favoriteTags || [])];

        if ((candidate.primaryDirection || 'industry_trend_case') !== activeDirection) return false;
        if (selectedRadarId !== 'all' && candidate.radarId !== selectedRadarId) return false;
        if (!candidateMatchesBadgeFilter(candidate, badgeFilter)) return false;
        if (!query) return true;

        const radarTitle = profileSourceTitle(profileByRadarId.get(candidate.radarId), radarMap.get(candidate.radarId), '');
        const insight = insightCache[candidate.id];
        const corpus = [
          candidate.title,
          candidate.summary,
          candidate.source,
          radarTitle,
          candidate.whyRecommended || '',
          candidate.relevanceReason || '',
          candidate.suggestedAction || '',
          typeof candidate.matchedIntent?.query === 'string' ? candidate.matchedIntent.query : '',
          typeof candidate.sourceQuality?.credibilityReason === 'string' ? candidate.sourceQuality.credibilityReason : '',
          candidate.favoriteNote || '',
          preference.note || '',
          ...customTags,
          insight?.overview || '',
          ...(insight?.keyPoints || []),
          ...(insight?.recommendationReasons || []),
          ...(insight?.practicalUses || []),
          ...(insight?.discussionPrompts || []),
          insight?.editorialNote || '',
        ]
          .join(' ')
          .toLowerCase();
        return corpus.includes(query);
      })
      .sort((left, right) => {
        const scoreDelta = candidateRecommendationScore(right) - candidateRecommendationScore(left);
        if (Math.abs(scoreDelta) > 0.1) return scoreDelta;
        return candidateSortTime(right) - candidateSortTime(left);
      });
  }, [activeDirection, badgeFilter, insightCache, localState, mainCandidates, profileByRadarId, radarMap, searchQuery, selectedRadarId]);
  const currentMainPage = clampTopicPage(mainPage, filteredCandidates.length);
  const pagedFilteredCandidates = useMemo(() => {
    const start = (currentMainPage - 1) * TOPIC_PAGE_SIZE;
    return filteredCandidates.slice(start, start + TOPIC_PAGE_SIZE);
  }, [currentMainPage, filteredCandidates]);

  const selectedCandidate = useMemo(
    () => filteredCandidates.find((candidate) => candidate.id === selectedCandidateId) || filteredCandidates[0] || null,
    [filteredCandidates, selectedCandidateId],
  );
  const expandedCandidate = useMemo(
    () => candidates.find((candidate) => candidate.id === expandedCandidateId) || null,
    [candidates, expandedCandidateId],
  );
  const shareCandidate = useMemo(
    () => candidates.find((candidate) => candidate.id === shareCandidateId) || null,
    [candidates, shareCandidateId],
  );
  useEffect(() => {
    setLocalState(readTopicLocalState(currentViewerId));
    setFavoriteOverrides({});
    setFavoritePendingIds(new Set());
    setSelectedCandidateId('');
    setExpandedCandidateId('');
    setStatusModalView(null);
    setMainPage(1);
    setStatusModalPage(1);
  }, [currentViewerId]);

  useEffect(() => {
    setMainPage(1);
    setExpandedCandidateId('');
  }, [activeDirection, badgeFilter, searchQuery, selectedRadarId]);

  useEffect(() => {
    setMainPage((page) => clampTopicPage(page, filteredCandidates.length));
  }, [filteredCandidates.length]);

  useEffect(() => {
    if (selectedRadarId !== 'all' && !viewerRadarIds.has(selectedRadarId)) {
      setSelectedRadarId('all');
      return;
    }
    if (!filteredCandidates.length) {
      if (selectedCandidateId) setSelectedCandidateId('');
      if (!statusModalView && expandedCandidateId) setExpandedCandidateId('');
      return;
    }
    if (!filteredCandidates.some((candidate) => candidate.id === selectedCandidateId)) {
      setSelectedCandidateId(filteredCandidates[0].id);
    }
    if (!statusModalView && expandedCandidateId && !filteredCandidates.some((candidate) => candidate.id === expandedCandidateId)) {
      setExpandedCandidateId('');
    }
  }, [expandedCandidateId, filteredCandidates, selectedCandidateId, selectedRadarId, statusModalView, viewerRadarIds]);

  useEffect(() => {
    setTagDraft('');
  }, [selectedCandidateId]);

  useEffect(() => {
    if (!expandedCandidate) return;
    if (insightCache[expandedCandidate.id]) return;
    let active = true;
    let timer: number | null = null;
    setInsightLoadingId(expandedCandidate.id);
    setInsightLoadingStartedAt(Date.now());
    const candidateId = expandedCandidate.id;
    const syncStatus = (status: TopicCandidateInsightStatusResult) => {
      setInsightStatusByCandidateId((prev) => ({ ...prev, [candidateId]: status }));
      if (status.insight) {
        setInsightCache((prev) => ({ ...prev, [candidateId]: status.insight as TopicCandidateInsight }));
      }
    };
    const poll = async (first = false) => {
      try {
        const status = first
          ? await prepareCandidateInsights(candidateId)
          : await getCandidateInsightStatus(candidateId);
        if (!active) return;
        syncStatus(status);
        if (status.status === 'ready' && status.insight) {
          void onTopicsReload();
          setInsightLoadingId((current) => (current === candidateId ? null : current));
          setInsightLoadingStartedAt(null);
          return;
        }
        if (status.status === 'failed') {
          flash('error', status.error || '深度分析生成失败，可稍后重试');
          setInsightLoadingId((current) => (current === candidateId ? null : current));
          setInsightLoadingStartedAt(null);
          return;
        }
        timer = window.setTimeout(() => void poll(false), 1800);
      } catch (error) {
        if (!active) return;
        flash('error', error instanceof Error ? error.message : '情报详情加载失败');
        setInsightLoadingId((current) => (current === candidateId ? null : current));
        setInsightLoadingStartedAt(null);
      }
    };
    void poll(true);
    return () => {
      active = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [expandedCandidate, flash, insightCache, onTopicsReload]);

  useEffect(() => {
    if (!captureTestingRadarId && !insightLoadingId) return;
    setTopicUiTick(Date.now());
    const timer = window.setInterval(() => setTopicUiTick(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [captureTestingRadarId, insightLoadingId]);

  const showMessage = (message: string, duration = 3200) => {
    setGlobalMessage(message);
    window.setTimeout(() => {
      setGlobalMessage((current) => (current === message ? null : current));
    }, duration);
  };

  const markSharedInboxRead = () => {
    const readAt = new Date().toISOString();
    setSharedReadAtByViewer((prev) => {
      const next = { ...prev, [currentViewerId]: readAt };
      writeSharedReadAtState(next);
      return next;
    });
  };

  const openStatusModal = (view: TopicStatusView) => {
    if (view === 'shared') {
      markSharedInboxRead();
    }
    setStatusModalView(view);
    setStatusModalPage(1);
    setExpandedCandidateId('');
  };

  useEffect(() => {
    if (!focusCandidateId) return;
    const candidate = candidates.find((item) => item.id === focusCandidateId);
    if (!candidate) {
      flash('info', '这条情报暂时没有在当前账号的资讯情报站里找到。');
      onFocusCandidateHandled?.();
      return;
    }
    setSelectedCandidateId(candidate.id);
    setExpandedCandidateId(candidate.id);
    setStatusModalPage(1);
    if (viewerRadarIds.has(candidate.radarId) || candidate.viewerSharedToMe) {
      setActiveDirection(candidate.primaryDirection || 'industry_trend_case');
      setSelectedRadarId('all');
      setMainPage(1);
    } else {
      flash('info', '这条情报不属于当前账号的画像，也还没有共享给当前账号。');
    }
    onFocusCandidateHandled?.();
  }, [candidates, flash, focusCandidateId, onFocusCandidateHandled, viewerRadarIds]);

  const ensureInsightLoaded = async (candidate: TopicCandidate) => {
    if (insightCache[candidate.id]) return insightCache[candidate.id];
    const prepared = await prepareCandidateInsights(candidate.id);
    setInsightStatusByCandidateId((prev) => ({ ...prev, [candidate.id]: prepared }));
    if (prepared.insight) {
      setInsightCache((prev) => ({ ...prev, [candidate.id]: prepared.insight as TopicCandidateInsight }));
      return prepared.insight;
    }
    const legacyInsight = await getCandidateInsights(candidate.id);
    setInsightCache((prev) => ({ ...prev, [candidate.id]: legacyInsight }));
    return legacyInsight;
  };

  const ensureTaskAssignees = (items: MentionCandidate[]) => {
    const next = [...items];
    if (currentSessionUser && !next.some((item) => item.id === currentSessionUser.id)) {
      next.unshift({
        id: currentSessionUser.id,
        fullName: currentSessionUser.fullName,
        email: currentSessionUser.email,
        primaryRole: currentSessionUser.primaryRole,
        isSelf: true,
      });
    }
    return next;
  };

  const ensureRadarShareOptions = async () => {
    if (radarShareOptions.length || isLoadingRadarShareOptions) return;
    setIsLoadingRadarShareOptions(true);
    try {
      const items = await getMentionCandidates('');
      setRadarShareOptions(ensureTaskAssignees(items));
    } catch {
      if (currentSessionUser) {
        setRadarShareOptions([
          {
            id: currentSessionUser.id,
            fullName: currentSessionUser.fullName,
            email: currentSessionUser.email,
            primaryRole: currentSessionUser.primaryRole,
            isSelf: true,
          },
        ]);
      }
    } finally {
      setIsLoadingRadarShareOptions(false);
    }
  };

  const organizationContextRef = (): TopicContextRef => ({
    type: 'organization_dna',
    id: currentSessionUser?.organizationId || 'local_org',
    label: '本组织 DNA',
    role: 'canonical_context',
  });

  const contextTypeLabel = (type: string) =>
    TOPIC_CONTEXT_TYPE_OPTIONS.find((option) => option.id === type)?.label || (type === 'organization_dna' ? '组织画像' : '关注对象');

  const buildContextRef = (type: TopicContextDraftType, label: string): TopicContextRef => {
    const normalizedLabel = label.trim().replace(/\s+/g, ' ');
    return {
      type,
      id: `${type}:${normalizedLabel}`,
      label: normalizedLabel,
      role: type === 'custom_focus' ? 'custom_focus' : 'target_filter',
    };
  };

  const buildRadarDraftFromRadar = (radar?: TopicRadar | null): TopicRadarDraft => ({
    id: radar?.id || 'placeholder-new',
    radarId: radar?.id || null,
    profileKind: 'custom',
    title: radar?.title || '',
    prompt: radar?.prompt || '',
    focus: [],
    excludeTerms: [],
    timeRange: radar?.timeRange || topicsSettingsState.defaultTimeRange,
    preferredSources: radar?.preferredSources || [],
    contextRefs: radar?.contextRefs?.length ? radar.contextRefs : [organizationContextRef()],
    shareRecipients: radar?.shareRecipients || [],
    priorityUrls: radar?.priorityUrls || (radar?.preferredSources || []).map((source) => source.url),
    fetchEnabled: radar ? Boolean(radar.fetchEnabled) : true,
    pushFrequency: radar?.pushFrequency || 'daily',
    pushTime: radar?.pushTime || '09:00',
    pushWeekday: radar?.pushWeekday || currentIsoWeekday(),
  });

  const buildProfileDraftFromProfile = (profile?: IntelligenceProfile | null): TopicRadarDraft => ({
    id: profile?.id || 'placeholder-new',
    radarId: profile?.radarId || null,
    profileKind: profile?.profileKind || 'custom',
    title: profile ? profileDisplayTitle(profile, '') : '',
    prompt: profile?.adminSummaryOverride || profile?.effectiveSummary || profile?.summary || '',
    focus: profile?.adminFocus || [],
    excludeTerms: profile?.adminExcludeTerms || [],
    timeRange: topicsSettingsState.defaultTimeRange,
    preferredSources: [],
    contextRefs: [],
    shareRecipients: [],
    priorityUrls: profile?.adminPriorityUrls || [],
    fetchEnabled: Boolean(profile?.adminPushEnabled),
    pushFrequency: profile?.adminPushFrequency || 'manual',
    pushTime: profile?.adminPushTime || '09:00',
    pushWeekday: profile?.adminPushWeekday || currentIsoWeekday(),
  });

  const openRadarConfig = (profile?: IntelligenceProfile | null, index = 0, mode: TopicRadarModalMode = 'manage') => {
    setRadarModalMode(mode);
    setEditingPrefIndex(index);
    setPreferredSourceDraft('');
    setContextRefDraft('');
    setContextRefType('custom_focus');
    setRadarModalNotice(null);
    setTempPref(buildProfileDraftFromProfile(profile));
    void ensureRadarShareOptions();
  };

  const openNewRadarConfig = () => {
    openRadarConfig(null, radarCards.findIndex((item) => item.id === 'placeholder-new'), 'create');
  };

  const openRadarManager = () => {
    const firstProfile = visibleProfiles[0] || null;
    openRadarConfig(firstProfile, 0, 'manage');
  };

  const closeRadarConfig = () => {
    if (isSavingRadarConfig) return;
    setEditingPrefIndex(null);
    setRadarModalMode('create');
    setTempPref(null);
    setPreferredSourceDraft('');
    setContextRefDraft('');
    setContextRefType('custom_focus');
    setRadarModalNotice(null);
  };

  const hasContextRef = (refs: TopicContextRef[], type: string, id: string) =>
    refs.some((item) => item.type === type && item.id === id);

  const toggleOrganizationDnaRef = () => {
    const orgRef = organizationContextRef();
    setTempPref((prev) => {
      if (!prev) return prev;
      if (hasContextRef(prev.contextRefs, orgRef.type, orgRef.id)) {
        return { ...prev, contextRefs: prev.contextRefs.filter((item) => !(item.type === orgRef.type && item.id === orgRef.id)) };
      }
      return { ...prev, contextRefs: [orgRef, ...prev.contextRefs] };
    });
  };

  const addCustomContextRef = () => {
    const label = contextRefDraft.trim().replace(/\s+/g, ' ');
    if (!label || !tempPref) return;
    const nextRef = buildContextRef(contextRefType, label);
    if (hasContextRef(tempPref.contextRefs, nextRef.type, nextRef.id)) {
      setContextRefDraft('');
      return;
    }
    setTempPref({
      ...tempPref,
      contextRefs: [...tempPref.contextRefs, nextRef],
    });
    setContextRefDraft('');
  };

  const togglePresetContextRef = (type: TopicContextDraftType, label: string) => {
    const nextRef = buildContextRef(type, label);
    setTempPref((prev) => {
      if (!prev) return prev;
      if (hasContextRef(prev.contextRefs, nextRef.type, nextRef.id)) {
        return { ...prev, contextRefs: prev.contextRefs.filter((item) => !(item.type === nextRef.type && item.id === nextRef.id)) };
      }
      return { ...prev, contextRefs: [...prev.contextRefs, nextRef] };
    });
  };

  const removeContextRef = (ref: TopicContextRef) => {
    setTempPref((prev) => (
      prev
        ? { ...prev, contextRefs: prev.contextRefs.filter((item) => !(item.type === ref.type && item.id === ref.id)) }
        : prev
    ));
  };

  const toggleShareRecipient = (candidate: MentionCandidate) => {
    setTempPref((prev) => {
      if (!prev) return prev;
      if (prev.shareRecipients.some((item) => item.userId === candidate.id)) {
        return { ...prev, shareRecipients: prev.shareRecipients.filter((item) => item.userId !== candidate.id) };
      }
      return {
        ...prev,
        shareRecipients: [
          ...prev.shareRecipients,
          { userId: candidate.id, fullName: candidate.fullName, email: candidate.email || null },
        ],
      };
    });
  };

  const directionCounts = useMemo(() => {
    const counts = new Map<TopicIntelligenceDirection, number>();
    TOPIC_DIRECTIONS.forEach((direction) => counts.set(direction.id, 0));
    mainCandidates.forEach((candidate) => {
      const direction = candidate.primaryDirection || 'industry_trend_case';
      counts.set(direction, (counts.get(direction) || 0) + 1);
    });
    return counts;
  }, [mainCandidates]);
  const badgeFilterCounts = useMemo(() => {
    const counts: Record<TopicBadgeFilter, number> = {
      all: 0,
      follow_up: 0,
      focus: 0,
      none: 0,
    };
    mainCandidates.forEach((candidate) => {
      if ((candidate.primaryDirection || 'industry_trend_case') !== activeDirection) return;
      if (selectedRadarId !== 'all' && candidate.radarId !== selectedRadarId) return;
      counts.all += 1;
      if (candidate.primaryBadge === 'follow_up') {
        counts.follow_up += 1;
      } else if (candidate.primaryBadge === 'focus') {
        counts.focus += 1;
      } else {
        counts.none += 1;
      }
    });
    return counts;
  }, [activeDirection, mainCandidates, selectedRadarId]);

  const statusViewOptions: Array<{ id: TopicStatusView; label: string; count: number; icon: React.ReactNode }> = [
    { id: 'shared', label: '共享给我', count: viewCounts.shared, icon: <Share2 size={14} /> },
    { id: 'sent', label: '我的共享', count: viewCounts.sent, icon: <Send size={14} /> },
    { id: 'favorite', label: '我的收藏', count: viewCounts.favorite, icon: <Bookmark size={14} /> },
    { id: 'task_linked', label: '已转任务', count: viewCounts.task_linked, icon: <CheckSquare size={14} /> },
  ];
  const activeStatusLabel = statusViewOptions.find((option) => option.id === statusModalView)?.label || '';
  const activeStatusCopy = statusModalView ? TOPIC_STATUS_VIEW_COPY[statusModalView] : null;
  const statusModalCandidates = useMemo(() => {
    if (!statusModalView) return [];
    return candidates
      .filter((candidate) => {
        const preference = preferenceOf(candidate.id);
        if (statusModalView === 'favorite') return isSavedCandidate(candidate, preference);
        if (statusModalView === 'shared') return Boolean(candidate.viewerSharedToMe);
        if (statusModalView === 'sent') return Boolean(candidate.viewerSharedByMe);
        return viewerRadarIds.has(candidate.radarId) && isCandidateTaskLinked(candidate);
      })
      .sort((left, right) => {
        if (statusModalView === 'shared') return latestReceivedShareTime(right) - latestReceivedShareTime(left);
        if (statusModalView === 'sent') return latestSentShareTime(right) - latestSentShareTime(left);
        return candidateSortTime(right) - candidateSortTime(left);
      });
  }, [candidates, favoriteOverrides, localState, relatedTasksByCandidate, statusModalView, viewerRadarIds]);
  const currentStatusModalPage = clampTopicPage(statusModalPage, statusModalCandidates.length);
  const pagedStatusModalCandidates = useMemo(() => {
    const start = (currentStatusModalPage - 1) * TOPIC_PAGE_SIZE;
    return statusModalCandidates.slice(start, start + TOPIC_PAGE_SIZE);
  }, [currentStatusModalPage, statusModalCandidates]);
  useEffect(() => {
    setStatusModalPage((page) => clampTopicPage(page, statusModalCandidates.length));
  }, [statusModalCandidates.length]);
  const processingCandidates = useMemo(
    () => mainCandidates.filter((candidate) => (
      candidate.insightStatus === 'pending' &&
      (!candidate.relevanceReason?.trim() || !candidate.suggestedAction?.trim())
    )),
    [mainCandidates],
  );
  const processingRadarTitles = useMemo(() => {
    const titles = new Set<string>();
    processingCandidates.forEach((candidate) => {
      titles.add(profileSourceTitle(profileByRadarId.get(candidate.radarId), radarMap.get(candidate.radarId)));
    });
    return Array.from(titles);
  }, [processingCandidates, profileByRadarId, radarMap]);
  const captureTestingRadarTitle = captureTestingRadarId ? profileSourceTitle(profileByRadarId.get(captureTestingRadarId), radarMap.get(captureTestingRadarId), '当前画像') : '';
  const activeTempProfile = tempPref && !tempPref.id.startsWith('placeholder-') ? intelligenceProfiles.find((profile) => profile.id === tempPref.id) || null : null;
  const tempPrefIsSystemManaged = Boolean(activeTempProfile?.profileKind === 'auto');

  const mainBadgeForCandidate = (candidate: TopicCandidate) => {
    if (candidate.primaryBadge === 'follow_up') return '待跟进';
    if (candidate.primaryBadge === 'focus') return '重点';
    return null;
  };

  const sourceStatusTextForCandidate = (candidate: TopicCandidate) => {
    if (candidate.sourceStatus === 'unreachable') return '来源访问失败';
    if (candidate.sourceStatus === 'needs_review') return '来源需复核';
    if (candidate.hasAttachments && candidate.attachmentParseStatus === 'pending') return '附件解析中';
    if (candidate.hasAttachments && candidate.attachmentParseStatus === 'failed') return '附件解析失败';
    if (candidate.hasAttachments && candidate.attachmentParseStatus === 'parsed') return '附件已解析';
    return '';
  };

  const relevanceReasonForCandidate = (candidate: TopicCandidate, radarTitle: string, insight?: TopicCandidateInsight | null) => {
    const explicit = candidate.relevanceReason?.trim();
    if (explicit) return explicit;
    const reason = insight?.recommendationReasons?.find((item) => item.trim());
    if (reason) return reason;
    return `这篇内容当前来自「${radarTitle}」画像，建议先结合该主题判断是否值得继续跟进。`;
  };

  const suggestedActionForCandidate = (candidate: TopicCandidate) => {
    const explicit = candidate.suggestedAction?.trim();
    if (explicit) return explicit;
    if (candidate.primaryBadge === 'follow_up') return '建议转成任务或共享给相关同事，确认是否需要推进。';
    if (candidate.primaryBadge === 'focus') return '建议先收藏，后续复盘或选题讨论时重点查看。';
    return '可先打开原文确认价值，必要时收藏、共享或转成任务。';
  };

  const scopeLabelForCandidate = (candidate: TopicCandidate) => {
    if (candidate.scopeType === 'organization') return '组织情报';
    if (candidate.scopeType === 'client') return '客户情报';
    if (candidate.scopeType === 'project_module') return '项目情报';
    return '';
  };

  const dataCenterStatusTextForCandidate = (candidate: TopicCandidate) => {
    if (!candidate.scopeType || !candidate.scopeId) return '';
    return candidate.dataCenterIngestEventId ? '已入底座' : '待入底座';
  };

  const evidenceStatusForCandidate = (candidate: TopicCandidate) => {
    if (candidate.evidenceStatus === 'candidate') {
      return { text: '未核验候选证据', tone: 'warning' as const };
    }
    if (candidate.evidenceStatus === 'accepted') {
      return { text: '已核验', tone: 'success' as const };
    }
    if (candidate.evidenceStatus === 'rejected') {
      return { text: '不采用', tone: 'danger' as const };
    }
    return { text: '', tone: 'neutral' as const };
  };

  const handleReviewEvidence = async (candidate: TopicCandidate, action: 'accept' | 'reject') => {
    if (!candidate.externalEvidenceCardId) {
      flash('info', '这条情报还没有生成证据卡，先等待入底座完成后再核验');
      return;
    }
    setEvidenceReviewPendingId(candidate.id);
    try {
      if (action === 'accept') {
        await acceptExternalEvidenceCard(candidate.externalEvidenceCardId);
        flash('success', '已标记为已核验证据');
      } else {
        await rejectExternalEvidenceCard(candidate.externalEvidenceCardId);
        flash('success', '已标记为不采用，并从活跃检索中移除');
      }
      await onTopicsReload();
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '证据核验失败');
    } finally {
      setEvidenceReviewPendingId(null);
    }
  };

  const handleAssistRadarDraft = async () => {
    if (!tempPref?.prompt.trim()) {
      flash('error', '请先填写追踪内容说明');
      return;
    }
    setIsAssistingRadar(true);
    try {
      const assisted = await assistRadarDraft(tempPref.prompt, tempPref.timeRange);
      setTempPref((prev) => (
        prev
          ? {
              ...prev,
              title: assisted.title,
              prompt: assisted.prompt,
            }
          : prev
      ));
      showMessage('已补强检索说明，并同步提炼标题');
    } catch (error) {
      flash('error', error instanceof Error ? error.message : 'AI 补强失败');
    } finally {
      setIsAssistingRadar(false);
    }
  };

  const handleAddPreferredSource = async () => {
    if (!tempPref) return;
    if (!preferredSourceDraft.trim()) {
      flash('error', '请先填写优先检索的网址');
      return;
    }
    setIsGeneratingSourceLabel(true);
    try {
      const suggested = await suggestRadarSourceLabel(preferredSourceDraft);
      setTempPref((prev) => {
        if (!prev) return prev;
        if (prev.preferredSources.some((item) => item.url === suggested.url)) {
          return prev;
        }
        return {
          ...prev,
          preferredSources: [...prev.preferredSources, { url: suggested.url, label: suggested.label }],
          priorityUrls: prev.priorityUrls.includes(suggested.url) ? prev.priorityUrls : [...prev.priorityUrls, suggested.url],
        };
      });
      setPreferredSourceDraft('');
      flash('success', `已加入优先网址「${suggested.label}」`);
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '网址添加失败');
    } finally {
      setIsGeneratingSourceLabel(false);
    }
  };

  const handleRemovePreferredSource = (url: string) => {
    setTempPref((prev) => (prev ? { ...prev, preferredSources: prev.preferredSources.filter((item) => item.url !== url), priorityUrls: prev.priorityUrls.filter((item) => item !== url) } : prev));
  };

  const buildRadarPayloadFromDraft = (draft: TopicRadarDraft, overrides: Partial<TopicRadarDraft> = {}): TopicRadarPayload => {
    const source = { ...draft, ...overrides };
    const title = source.title.trim() || '自定义追踪项';
    const isExistingRadar = !source.id.startsWith('placeholder-');
    return {
      title,
      prompt: source.prompt.trim(),
      timeRange: source.timeRange,
      preferredSources: source.preferredSources,
      orgId: currentSessionUser?.organizationId || 'local_org',
      label: title,
      status: source.fetchEnabled ? (isExistingRadar ? 'running' : 'trial') : 'paused',
      contextRefs: source.contextRefs,
      shareRecipients: source.shareRecipients,
      priorityUrls: source.priorityUrls.length ? source.priorityUrls : source.preferredSources.map((item) => item.url),
      fetchEnabled: source.fetchEnabled,
      pushFrequency: source.pushFrequency,
      pushTime: source.pushTime || null,
      pushWeekday: source.pushFrequency === 'weekly' ? (source.pushWeekday || currentIsoWeekday()) : null,
      createdBy: currentViewerId,
    };
  };

  const buildProfilePayloadFromDraft = (draft: TopicRadarDraft, overrides: Partial<TopicRadarDraft> = {}): IntelligenceProfileMutationPayload => {
    const source = { ...draft, ...overrides };
    return {
      title: source.title.trim(),
      summary: source.prompt.trim(),
      focus: source.focus.map((item) => item.trim()).filter(Boolean),
      excludeTerms: source.excludeTerms.map((item) => item.trim()).filter(Boolean),
      priorityUrls: source.priorityUrls.map((item) => item.trim()).filter(Boolean),
      pushEnabled: source.fetchEnabled,
      pushFrequency: source.pushFrequency,
      pushTime: source.pushTime || null,
      pushWeekday: source.pushFrequency === 'weekly' ? (source.pushWeekday || currentIsoWeekday()) : null,
      scopeType: 'organization',
      scopeId: 'local_org',
    };
  };

  const handleSavePrefEdit = async () => {
    if (!tempPref) return;
    if (!tempPref.prompt.trim()) {
      setRadarModalNotice('请先填写画像概况或关注说明，这样系统才知道要持续寻找什么。');
      return;
    }
    if (!tempPref.title.trim()) {
      setRadarModalNotice('请先填写画像名称。');
      return;
    }
    setIsSavingRadarConfig(true);
    setRadarModalNotice(null);
    try {
      const payload = buildProfilePayloadFromDraft(tempPref);
      const isExistingProfile = !tempPref.id.startsWith('placeholder-');
      let savedProfile: IntelligenceProfile;
      if (isExistingProfile) {
        savedProfile = await updateIntelligenceProfile(tempPref.id, payload);
      } else {
        savedProfile = await createIntelligenceProfile(payload);
      }
      await onTopicsReload();
      closeRadarConfig();
      showMessage(`${isExistingProfile ? '画像已更新' : '已新增自定义画像'}：${savedProfile.title || '情报画像'}`);
    } catch (error) {
      setRadarModalNotice(error instanceof Error ? error.message : '保存失败');
    } finally {
      setIsSavingRadarConfig(false);
    }
  };

  const handleToggleRadarFetch = async (radar: TopicRadar) => {
    if (radarTogglePendingId) return;
    if (radar.systemManaged || radar.autoGenerated) {
      setRadarModalNotice('自动画像默认保持试运行和手动试跑，等抓取质量稳定后再开放定时推送。');
      return;
    }
    const nextEnabled = !radar.fetchEnabled;
    setRadarTogglePendingId(radar.id);
    setRadarModalNotice(null);
    try {
      const draft = buildRadarDraftFromRadar(radar);
      await updateRadar(radar.id, buildRadarPayloadFromDraft(draft, { fetchEnabled: nextEnabled }));
      if (tempPref?.id === radar.id) {
        setTempPref((prev) => (prev?.id === radar.id ? { ...prev, fetchEnabled: nextEnabled } : prev));
      }
      await onTopicsReload();
      showMessage(nextEnabled ? `已启用「${radar.title.replace(/雷达/g, '画像')}」自动抓取` : `已暂停「${radar.title.replace(/雷达/g, '画像')}」自动抓取`);
    } catch (error) {
      setRadarModalNotice(error instanceof Error ? error.message : '画像启停失败');
    } finally {
      setRadarTogglePendingId(null);
    }
  };

  const handleDeleteRadar = async (radar: TopicRadar) => {
    if (radarDeletePendingId) return;
    if (radar.systemManaged || radar.autoGenerated) {
      setRadarModalNotice('自动画像来自统一数据底座，不能删除；如需校准，可编辑画像概况和关注重点。');
      return;
    }
    const candidateCount = candidates.filter((candidate) => candidate.radarId === radar.id).length;
    const confirmed = window.confirm(
      `确定删除「${(radar.title || '未命名画像').replace(/雷达/g, '画像')}」吗？\n\n如果只是想调整画像概况、推送频率或重点网址，建议点“编辑”修改，通常不需要删除。\n\n继续删除会同时移除该画像下的 ${candidateCount} 条情报，以及相关收藏、共享和抓取记录；已经转成的任务不会自动删除。`,
    );
    if (!confirmed) return;
    const nextRadar = viewerRadars.find((item) => item.id !== radar.id) || null;
    setRadarDeletePendingId(radar.id);
    setRadarModalNotice(null);
    try {
      await deleteRadar(radar.id);
      if (selectedRadarId === radar.id) {
        setSelectedRadarId('all');
      }
      if (tempPref?.id === radar.id) {
        if (nextRadar) {
          setTempPref(buildRadarDraftFromRadar(nextRadar));
          setRadarModalMode('manage');
        } else {
          closeRadarConfig();
        }
      }
      await onTopicsReload();
      showMessage(`已删除画像「${(radar.title || '未命名画像').replace(/雷达/g, '画像')}」`);
    } catch (error) {
      setRadarModalNotice(error instanceof Error ? error.message : '删除画像失败');
    } finally {
      setRadarDeletePendingId(null);
    }
  };

  const handleDeleteProfile = async (profile: IntelligenceProfile) => {
    if (radarDeletePendingId) return;
    if (profile.profileKind !== 'custom') {
      setRadarModalNotice('组织、客户和项目自动画像不能删除；如需校准，可直接编辑概况、关注重点和排除方向。');
      return;
    }
    const candidateCount = candidates.filter((candidate) => candidate.radarId === profile.radarId).length;
    const confirmed = window.confirm(
      `确定删除自定义画像「${profileDisplayTitle(profile, '未命名画像')}」吗？\n\n删除后这张画像不会继续试跑或推送，但已经产生的 ${candidateCount} 条情报、任务和证据不会被清空。`,
    );
    if (!confirmed) return;
    setRadarDeletePendingId(profile.id);
    setRadarModalNotice(null);
    try {
      await deleteIntelligenceProfile(profile.id);
      if (selectedRadarId === profile.radarId) {
        setSelectedRadarId('all');
      }
      closeRadarConfig();
      await onTopicsReload();
      showMessage(`已删除自定义画像「${profileDisplayTitle(profile, '未命名画像')}」`);
    } catch (error) {
      setRadarModalNotice(error instanceof Error ? error.message : '删除画像失败');
    } finally {
      setRadarDeletePendingId(null);
    }
  };

  const handleCaptureTestRadar = async (radarId: string) => {
    setCaptureTestingRadarId(radarId);
    setCaptureTestingStartedAt(Date.now());
    try {
      const result = await captureIntelligenceRadarTest(radarId);
      await onTopicsReload();
      showMessage(captureResultMessage(result), 7200);
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '画像试跑失败');
    } finally {
      setCaptureTestingRadarId(null);
      setCaptureTestingStartedAt(null);
    }
  };

  const handleTrialRunProfile = async (profile: IntelligenceProfile) => {
    if (profileTrialPendingId) return;
    setProfileTrialPendingId(profile.id);
    if (profile.radarId) {
      setCaptureTestingRadarId(profile.radarId);
      setCaptureTestingStartedAt(Date.now());
    }
    try {
      const result = await trialRunIntelligenceProfile(profile.id);
      await onTopicsReload();
      showMessage(captureResultMessage(result), 7200);
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '自动画像试跑失败');
    } finally {
      setProfileTrialPendingId(null);
      setCaptureTestingRadarId(null);
      setCaptureTestingStartedAt(null);
    }
  };

  const handleRefreshProfile = async (profile: IntelligenceProfile) => {
    if (profileRefreshPendingId) return;
    setProfileRefreshPendingId(profile.id);
    try {
      await refreshIntelligenceProfile(profile.id, { force: true, allowAi: true, autoTrial: false });
      await onTopicsReload();
      showMessage('顾问画像已刷新');
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '画像刷新失败');
    } finally {
      setProfileRefreshPendingId(null);
    }
  };

  const handleToggleSaved = async (candidate: TopicCandidate) => {
    const current = preferenceOf(candidate.id);
    const currentlySaved = isSavedCandidate(candidate, current);
    const targetSaved = !currentlySaved;
    if (favoritePendingIds.has(candidate.id)) return;
    setFavoriteOverride(candidate.id, targetSaved);
    setFavoritePending(candidate.id, true);
    try {
      if (targetSaved) {
        await favoriteIntelligenceItem(candidate.id, {
          userId: currentViewerId,
          note: current.note || '',
          tags: current.tags || [],
        });
        updateLocalPreference(candidate.id, { saved: true });
        flash('success', '已收藏');
      } else {
        await unfavoriteIntelligenceItem(candidate.id, currentViewerId);
        updateLocalPreference(candidate.id, { saved: false, note: '', tags: [] });
        flash('success', '已取消收藏');
      }
      try {
        await onTopicsReload();
        setFavoriteOverride(candidate.id, null);
      } catch {
        // Keep the optimistic state visible if the refresh fails; the next reload will reconcile it.
      }
    } catch (error) {
      if (targetSaved) {
        updateLocalPreference(candidate.id, { saved: true });
        setFavoriteOverride(candidate.id, true);
        flash('error', error instanceof Error ? `${error.message}，已先保留在本机` : '收藏状态保存失败，已先保留在本机');
      } else {
        setFavoriteOverride(candidate.id, currentlySaved);
        flash('error', error instanceof Error ? error.message : '取消收藏失败');
      }
    } finally {
      setFavoritePending(candidate.id, false);
    }
  };

  const handleAddCustomTag = (candidate: TopicCandidate) => {
    const nextTag = normalizeTagDraft(tagDraft);
    if (!nextTag) return;
    const currentTags = preferenceOf(candidate.id).tags || [];
    if (currentTags.some((tag) => tag.toLowerCase() === nextTag.toLowerCase())) {
      flash('info', '这个标签已经存在');
      return;
    }
    updateLocalPreference(candidate.id, {
      saved: true,
      tags: [...currentTags, nextTag],
    });
    setTagDraft('');
    flash('success', `已添加标签「${nextTag}」`);
  };

  const handleRemoveCustomTag = (candidate: TopicCandidate, tag: string) => {
    const currentTags = preferenceOf(candidate.id).tags || [];
    updateLocalPreference(candidate.id, {
      saved: currentTags.length > 1 || Boolean(preferenceOf(candidate.id).note?.trim()),
      tags: currentTags.filter((item) => item !== tag),
    });
    flash('success', `已移除标签「${tag}」`);
  };

  const setCandidateChatDraft = (candidateId: string, value: string) => {
    setChatDraftByCandidateId((prev) => {
      if (!value) {
        if (!(candidateId in prev)) return prev;
        const next = { ...prev };
        delete next[candidateId];
        return next;
      }
      return {
        ...prev,
        [candidateId]: value,
      };
    });
  };

  const handleAskCandidateQuestion = async (candidate: TopicCandidate, forcedQuestion?: string) => {
    const question = (forcedQuestion ?? (chatDraftByCandidateId[candidate.id] || '')).trim();
    if (!question) return;
    if (chatLoadingCandidateId === candidate.id) return;

    const userMessage: TopicCandidateChatMessage = {
      role: 'user',
      content: question,
      createdAt: new Date().toISOString(),
    };
    const history = (chatByCandidateId[candidate.id] || []).slice(-8);

    setChatByCandidateId((prev) => ({
      ...prev,
      [candidate.id]: [...(prev[candidate.id] || []), userMessage],
    }));
    if (!forcedQuestion) {
      setCandidateChatDraft(candidate.id, '');
    }
    setChatLoadingCandidateId(candidate.id);

    try {
      const response = await askCandidateQuestion(candidate.id, {
        question,
        history,
      });
      setChatByCandidateId((prev) => ({
        ...prev,
        [candidate.id]: [...(prev[candidate.id] || []), response.message],
      }));
    } catch (error) {
      const fallbackMessage: TopicCandidateChatMessage = {
        role: 'assistant',
        content: error instanceof Error ? `我暂时没能接住这个追问：${error.message}` : '我暂时没能接住这个追问，请稍后再试。',
        createdAt: new Date().toISOString(),
      };
      setChatByCandidateId((prev) => ({
        ...prev,
        [candidate.id]: [...(prev[candidate.id] || []), fallbackMessage],
      }));
      flash('error', error instanceof Error ? error.message : '追问失败');
    } finally {
      setChatLoadingCandidateId((current) => (current === candidate.id ? null : current));
    }
  };

  const closeTopicTaskModal = () => {
    setTaskModalCandidateId(null);
    setTaskDraft(null);
    setTaskAssignees([]);
    setTaskModalNotice(null);
    setCreatedTopicTaskId(null);
    setIsPreparingTaskModal(false);
  };

  const topicTaskOwnerIsSelf = (ownerId: string | null, ownerName: string) => {
    const aliases = new Set(
      [
        currentSessionUser?.id,
        currentSessionUser?.email,
        currentSessionUser?.fullName,
        currentOperatorName,
      ]
        .map((item) => normalizeTopicIdentity(item))
        .filter(Boolean),
    );
    return Boolean((ownerId && aliases.has(normalizeTopicIdentity(ownerId))) || aliases.has(normalizeTopicIdentity(ownerName)));
  };

  const openTaskModal = async (candidate: TopicCandidate) => {
    const existingTasks = relatedTasksByCandidate.get(candidate.id) || [];
    if (candidate.convertedTaskId || existingTasks.length > 0) {
      setSelectedCandidateId(candidate.id);
      setExpandedCandidateId(candidate.id);
      showMessage(`这篇情报已关联 ${Math.max(existingTasks.length, candidate.convertedTaskId ? 1 : 0)} 条任务，已在卡片下方展开。`);
      return;
    }

    const defaultOwnerId = currentSessionUser?.id || '';
    setSelectedCandidateId(candidate.id);
    setTaskModalCandidateId(candidate.id);
    setTaskDraft({
      title: `跟进情报：${candidate.title.trim()}`,
      desc: candidate.suggestedAction?.trim() || `请查看任务备注中的情报来源和分析背景，并结合团队安排决定下一步处理方式。`,
      listId: defaultListId,
      priority: 'normal',
      dueDate: '',
      ddl: '待确认',
      ownerId: defaultOwnerId,
      ownerName: currentSessionUser?.fullName || currentOperatorName,
      collaboratorIds: [],
      note: '',
    });
    setTaskModalNotice(null);
    setCreatedTopicTaskId(null);
    setIsPreparingTaskModal(true);
    try {
      const mentionItems = await getMentionCandidates('').catch(() => []);
      const assignees = ensureTaskAssignees(mentionItems);
      setTaskAssignees(assignees);
      const defaultOwner = assignees.find((item) => item.id === defaultOwnerId) || assignees[0];
      if (defaultOwner) {
        setTaskDraft((prev) =>
          prev
            ? {
                ...prev,
                ownerId: defaultOwner.id,
                ownerName: defaultOwner.fullName,
                collaboratorIds: prev.collaboratorIds.filter((id) => id !== defaultOwner.id),
              }
            : prev,
        );
      }
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '任务弹窗准备失败');
    } finally {
      setIsPreparingTaskModal(false);
    }
  };

  const handleSubmitTask = async () => {
    const modalCandidate = candidates.find((candidate) => candidate.id === taskModalCandidateId);
    if (!modalCandidate || !taskDraft) return;
    if (!taskDraft.title.trim()) {
      flash('error', '请填写任务标题');
      return;
    }
    if (!taskDraft.listId) {
      flash('error', '请先选择任务清单');
      return;
    }

    setIsSubmittingTask(true);
    setTaskModalNotice({ type: 'info', text: '正在创建任务，完成后会在这里显示结果。' });
    try {
      const owner = taskAssignees.find((item) => item.id === taskDraft.ownerId);
      const ownerId = owner?.id || taskDraft.ownerId || null;
      const ownerName = owner?.fullName || taskDraft.ownerName || currentOperatorName;
      const radarTitle = profileSourceTitle(profileByRadarId.get(modalCandidate.radarId), radarMap.get(modalCandidate.radarId));
      const insight = insightCache[modalCandidate.id] || null;
      const note = buildTopicAttachmentNote(modalCandidate, radarTitle, insight, taskDraft.note);
      const desc = buildTopicTaskDescription(modalCandidate, radarTitle, taskDraft.desc);
      const ownerRecipient = topicShareRecipientFromOwner(ownerId, ownerName);
      const collaboratorRecipients = taskDraft.collaboratorIds
        .filter((id) => id && id !== ownerId)
        .map((id) => {
          const assignee = taskAssignees.find((item) => item.id === id);
          return assignee ? topicShareRecipientFromAssignee(assignee) : { userId: id, fullName: id, email: null };
        });
      const promoted = await promoteCandidateTasks(modalCandidate.id, [
        {
          title: taskDraft.title.trim(),
          desc,
          priority: taskDraft.priority,
          listId: taskDraft.listId,
          dueDate: taskDraft.dueDate || null,
          ddl: taskDraft.ddl.trim() || taskDraft.dueDate || '待确认',
          ownerId,
          ownerName,
          collaboratorIds: taskDraft.collaboratorIds.filter((id) => id && id !== ownerId),
          tagIds: [],
          tags: ['情报跟进'],
          note,
          ownerRecipient,
          collaboratorRecipients,
          actorId: currentViewerId,
          actorName: currentSessionUser?.fullName || currentOperatorName || currentViewerId,
          autoShare: true,
        },
      ]);
      const createdTask = promoted.tasks[0];
      await onTasksReload();
      await onTopicsReload();
      let successText = topicTaskOwnerIsSelf(ownerId, ownerName)
        ? '已创建任务，负责人是你，已进入任务列表；无需再到协作收件箱确认。'
        : '任务已发出，等待负责人确认；这也算转任务成功，请不要重复点击。';
      if ((promoted.autoShareCreatedCount || 0) > 0) {
        successText += ` 已自动开放 ${promoted.autoShareCreatedCount} 位负责人/协作者的情报原文权限。`;
      }
      if (promoted.flowbackResults?.length) {
        successText += ` ${promoted.flowbackResults.slice(0, 2).join('；')}。`;
      }
      if (promoted.warnings?.length) {
        successText += ` 提醒：${promoted.warnings.join('；')}`;
      }
      setCreatedTopicTaskId(createdTask?.id || 'created');
      setTaskModalNotice({ type: 'success', text: successText });
      showMessage(successText, 7200);
      flash('success', successText);
    } catch (error) {
      const message = error instanceof Error ? error.message : '转任务失败';
      setTaskModalNotice({ type: 'error', text: message });
      flash('error', message);
    } finally {
      setIsSubmittingTask(false);
    }
  };

  const openShareModal = (candidate: TopicCandidate) => {
    setShareCandidateId(candidate.id);
    setShareReason('');
    const radar = radarMap.get(candidate.radarId);
    setShareModalNotice(
      radar?.shareRecipients?.length
        ? null
        : '这个画像还没有配置情报共享者。请先在“管理画像”里补充共享者，再回来共享这篇情报。',
    );
  };

  const handleSubmitShare = async () => {
    if (!shareCandidate) return;
    const radar = radarMap.get(shareCandidate.radarId);
    const recipients = radar?.shareRecipients || [];
    if (!recipients.length) {
      setShareModalNotice('这个画像还没有配置情报共享者，暂时不能共享。');
      return;
    }
    setIsSharing(true);
    try {
      await shareIntelligenceItem(shareCandidate.id, {
        sharedBy: currentViewerId,
        sharedByName: currentSessionUser?.fullName || currentOperatorName || currentViewerId,
        sharedTo: recipients.map((item) => item.userId),
        sharedToRecipients: recipients,
        reason: shareReason.trim(),
      });
      setShareCandidateId(null);
      setShareReason('');
      setShareModalNotice(null);
      await onTopicsReload();
      flash('success', '已共享给画像配置的接收人');
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '共享失败');
    } finally {
      setIsSharing(false);
    }
  };

  const taskModalCandidate = taskModalCandidateId
    ? candidates.find((candidate) => candidate.id === taskModalCandidateId) || null
    : null;
  const shareRadar = shareCandidate ? radarMap.get(shareCandidate.radarId) || null : null;
  const taskOwnerOptions = taskAssignees.length
    ? taskAssignees
    : currentSessionUser
      ? [{
          id: currentSessionUser.id,
          fullName: currentSessionUser.fullName,
          email: currentSessionUser.email,
          primaryRole: currentSessionUser.primaryRole,
          isSelf: true,
        }]
      : [];
  const toggleTaskCollaborator = (id: string) => {
    setTaskDraft((prev) => {
      if (!prev || !id || id === prev.ownerId) return prev;
      const exists = prev.collaboratorIds.includes(id);
      return {
        ...prev,
        collaboratorIds: exists
          ? prev.collaboratorIds.filter((item) => item !== id)
          : [...prev.collaboratorIds, id],
      };
    });
  };

  const renderExpandedCandidate = (candidate: TopicCandidate, radarTitle: string) => {
    const insight = insightCache[candidate.id] || null;
    const isLoadingInsight = insightLoadingId === candidate.id;
    const insightStatus = insightStatusByCandidateId[candidate.id];
    const chatMessages = chatByCandidateId[candidate.id] || [];
    const chatDraft = chatDraftByCandidateId[candidate.id] || '';
    const relatedTasks = relatedTasksByCandidate.get(candidate.id) || [];
    const deepAnalysis = candidate.deepAnalysis || {};
    const deepText = (key: string, fallback = '') => {
      const value = deepAnalysis[key];
      return typeof value === 'string' && value.trim() ? value.trim() : fallback;
    };
    const suggestedQuestions = Array.isArray(deepAnalysis.suggestedQuestions)
      ? deepAnalysis.suggestedQuestions.map((item) => String(item)).filter(Boolean)
      : insight?.discussionPrompts || [];
    const publicOpinionSample = (
      deepAnalysis.publicOpinionSample && typeof deepAnalysis.publicOpinionSample === 'object'
        ? deepAnalysis.publicOpinionSample as Record<string, unknown>
        : null
    );

    return (
      <div className="space-y-4">
        {isLoadingInsight ? (
          <div className="rounded-lg border border-gray-100 bg-gray-50 px-4 py-6 text-center text-[13px] text-gray-500">
            <RefreshCw size={18} className="mx-auto mb-2 animate-spin text-[#5B7BFE]" />
            <p className="font-semibold text-gray-700">
              {insightStatus?.status === 'queued' ? '深度分析已排队，正在等待生成…' : '正在生成深度分析，完成后会自动缓存…'}
            </p>
            <p className="mt-1 text-[12px] text-gray-500">
              {insightProgressText(elapsedSeconds(insightLoadingStartedAt, topicUiTick))}
            </p>
          </div>
        ) : insight ? (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1.1fr_0.9fr]">
            <section className="rounded-lg border border-gray-100 bg-gray-50/70 p-4">
              <p className="text-[12px] font-bold text-gray-900">核心信息拆解</p>
              <p className="mt-2 text-[13px] leading-6 text-gray-700">{deepText('coreInfo', insight.overview || candidate.summary)}</p>
              {insight.keyPoints.length > 0 && (
                <div className="mt-4">
                  <p className="text-[12px] font-bold text-gray-900">核心观点</p>
                  <ul className="mt-2 space-y-2 text-[13px] leading-6 text-gray-700">
                    {insight.keyPoints.map((item, index) => (
                      <li key={`${candidate.id}-key-${index}`}>{index + 1}. {item}</li>
                    ))}
                  </ul>
                </div>
              )}
            </section>
            <section className="rounded-lg border border-gray-100 bg-white p-4">
              <p className="text-[12px] font-bold text-gray-900">与关联画像的关系</p>
              <p className="mt-2 text-[13px] leading-6 text-gray-700">
                {deepText('contextRelation', candidate.whyRecommended || candidate.relevanceReason || insight.recommendationReasons.join('；') || '当前还没有稳定的画像关系判断。')}
              </p>
              <div className="mt-4">
                <p className="text-[12px] font-bold text-gray-900">机会或风险</p>
                <p className="mt-2 text-[13px] leading-6 text-gray-700">{deepText('opportunityOrRisk', insight.editorialNote || '当前还没有稳定判断。')}</p>
              </div>
              {insight.practicalUses.length > 0 && (
                <div className="mt-4">
                  <p className="text-[12px] font-bold text-gray-900">可继续展开</p>
                  <ul className="mt-2 space-y-2 text-[13px] leading-6 text-gray-700">
                    {insight.practicalUses.map((item, index) => (
                      <li key={`${candidate.id}-use-${index}`}>{index + 1}. {item}</li>
                    ))}
                  </ul>
                </div>
              )}
              <div className="mt-4 rounded-md border border-gray-100 bg-gray-50 px-3 py-2 text-[12px] leading-5 text-gray-600">
                <p className="font-bold text-gray-800">来源与可信度提示</p>
                <p className="mt-1">{deepText('sourceCredibility', typeof candidate.sourceQuality?.credibilityReason === 'string' ? candidate.sourceQuality.credibilityReason : (candidate.sourceUrl ? '建议打开原文核对关键事实、时间和主体。' : '当前没有原文链接，需要人工复核来源。'))}</p>
              </div>
            </section>
          </div>
        ) : candidate.insightStatus === 'failed' ? (
          <div className="rounded-lg border border-rose-100 bg-rose-50 px-4 py-3 text-[13px] leading-6 text-rose-700">
            这篇情报深度分析生成失败，可以先打开原文，稍后再次点击深度分析重试。
          </div>
        ) : (
          <div className="rounded-lg border border-gray-100 bg-gray-50 px-4 py-3 text-[13px] leading-6 text-gray-500">
            点击“深度分析”后，系统会读取原文、附件解析结果和关联画像生成完整判断；未生成前不影响卡片浏览。
          </div>
        )}

        {publicOpinionSample && (
          <div className="rounded-lg border border-amber-100 bg-amber-50/70 p-4 text-[12px] leading-5 text-amber-900">
            <p className="font-bold text-amber-800">公开来源样本</p>
            <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-4">
              <span>范围：{String(publicOpinionSample.sourceScope || '公开来源样本')}</span>
              <span>时间：{String(publicOpinionSample.timeRange || '未标注')}</span>
              <span>样本：{String(publicOpinionSample.sampleCount ?? 0)} 条</span>
              <span>可信度：{String(publicOpinionSample.trendConfidence || 'low')}</span>
            </div>
            {publicOpinionSample.note ? <p className="mt-2">{String(publicOpinionSample.note)}</p> : null}
          </div>
        )}

        {suggestedQuestions.length ? (
          <div className="rounded-lg border border-indigo-100 bg-indigo-50/50 p-4">
            <p className="text-[12px] font-bold text-indigo-700">可继续追问</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {suggestedQuestions.slice(0, 4).map((prompt) => (
                <button
                  type="button"
                  key={`${candidate.id}-prompt-${prompt}`}
                  onClick={() => void handleAskCandidateQuestion(candidate, prompt)}
                  className="rounded-md border border-indigo-100 bg-white px-3 py-2 text-[12px] font-semibold text-indigo-700 hover:bg-indigo-50"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        {relatedTasks.length > 0 && (
          <div className="rounded-lg border border-violet-100 bg-violet-50/60 p-4">
            <p className="text-[12px] font-bold text-violet-700">已关联任务</p>
            <div className="mt-2 space-y-2 text-[13px] text-violet-900">
              {relatedTasks.slice(0, 3).map((task) => (
                <div key={task.id} className="rounded-md border border-violet-100 bg-white/70 px-3 py-2">
                  <p className="font-semibold">{task.title}</p>
                  <p className="mt-1 text-[12px] text-violet-700">
                    {task.ownerName ? `负责人：${task.ownerName}` : '负责人待确认'}
                    {task.ddl ? ` · 时间：${task.ddl}` : ''}
                    {task.collaborators?.length ? ` · 协作者：${task.collaborators.map((item) => item.fullName).join('、')}` : ''}
                  </p>
                  {task.note ? <p className="mt-1 line-clamp-2 text-[12px] leading-5 text-violet-700">{task.note}</p> : null}
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="rounded-lg border border-gray-100 bg-white p-4">
          <div className="flex items-center gap-2 text-[12px] font-bold text-gray-900">
            <FileText size={14} className="text-[#5B7BFE]" />
            围绕这篇情报追问
          </div>
          {chatMessages.length > 0 && (
            <div className="mt-3 max-h-[220px] space-y-3 overflow-y-auto rounded-lg bg-gray-50 p-3">
              {chatMessages.map((message, index) => (
                <div key={`${candidate.id}-chat-${index}`} className={`text-[13px] leading-6 ${message.role === 'user' ? 'text-gray-900' : 'text-gray-600'}`}>
                  <span className="font-bold">{message.role === 'user' ? '我' : '情报助手'}：</span>
                  {message.content}
                </div>
              ))}
            </div>
          )}
          <div className="mt-3 flex gap-2">
            <textarea
              value={chatDraft}
              onChange={(event) => setCandidateChatDraft(candidate.id, event.target.value)}
              placeholder={`继续问「${radarTitle}」画像下这篇情报`}
              className="min-h-[76px] flex-1 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-[13px] leading-6 outline-none focus:border-[#5B7BFE] focus:bg-white"
            />
            <button
              type="button"
              onClick={() => void handleAskCandidateQuestion(candidate)}
              disabled={!chatDraft.trim() || chatLoadingCandidateId === candidate.id}
              className="self-end rounded-md bg-[#5B7BFE] px-4 py-2 text-[12px] font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              {chatLoadingCandidateId === candidate.id ? '发送中…' : '发送'}
            </button>
          </div>
        </div>
      </div>
    );
  };

  const buildAdvisorPrototypeItem = (candidate: TopicCandidate, section: AdvisorPrototypeItem['section'] = 'brief'): AdvisorPrototypeItem => {
    const profileTitle = profileSourceTitle(profileByRadarId.get(candidate.radarId), radarMap.get(candidate.radarId));
    const insight = insightCache[candidate.id];
    const evidenceStatus = evidenceStatusForCandidate(candidate);
    const why = relevanceReasonForCandidate(candidate, profileTitle, insight);
    const action = suggestedActionForCandidate(candidate);
    const evidencePieces = [
      candidate.source ? `来源：${candidate.source}` : '',
      candidate.publishedAt ? `时间：${formatTopicDateTime(candidate.publishedAt)}` : '',
      evidenceStatus.text || '证据状态待生成',
    ].filter(Boolean);
    const tags = [
      advisorRelevanceTag(candidate),
      advisorSourceTag(candidate),
      advisorPriorityTag(candidate),
      scopeLabelForCandidate(candidate),
      evidenceStatus.text,
    ].filter(Boolean);
    return {
      id: candidate.id,
      candidate,
      title: candidate.title,
      judgment: candidate.whyRecommended?.trim() || candidate.relevanceReason?.trim() || candidate.summary || '这条公开信息可能值得结合当前组织、客户或项目继续判断。',
      why,
      evidence: evidencePieces.join(' / ') || '已有公开来源线索，仍需人工核验关键事实。',
      gap: candidate.evidenceStatus === 'accepted'
        ? '已通过核验，后续重点是判断是否需要进入行动。'
        : candidate.evidenceStatus === 'rejected'
          ? '已标记不采用，通常不再进入活跃判断。'
          : '还需要确认来源时效、主体关系，以及是否适合当前组织或项目行动。',
      action,
      tags,
      profileTitle,
      sourceMeta: candidate.sourceUrl ? '可打开来源复核' : '暂无外部链接',
      section,
    };
  };

  const advisorCandidates = [...mainCandidates].sort((left, right) => {
    const scoreDiff = candidateRecommendationScore(right) - candidateRecommendationScore(left);
    return scoreDiff || candidateSortTime(right) - candidateSortTime(left);
  });

  const advisorBriefRealItems = advisorCandidates
    .filter((candidate) => (
      candidate.primaryBadge ||
      candidate.convertedTaskId ||
      candidate.viewerSharedToMe ||
      candidate.whyRecommended ||
      candidate.relevanceReason ||
      candidate.suggestedAction
    ))
    .slice(0, 5)
    .map((candidate) => buildAdvisorPrototypeItem(candidate, 'brief'));

  const demoBriefItems: AdvisorPrototypeItem[] = [
    {
      id: 'demo-brief-funding',
      demo: true,
      title: '示例：某公益基金会开放儿童心理健康项目征集',
      judgment: '示例顾问判断：这类征集可能与组织现有项目方向形成直接资助机会。',
      why: '示例：系统会从组织 DNA、项目模块和过往任务中识别“儿童心理健康”“公益服务”“区域项目”等关系，再解释为什么推给你。',
      evidence: '示例证据：基金会公告页、申报指南、截止日期。',
      gap: '示例待补：需要核对申报资格、地域限制、预算口径和竞争情况。',
      action: '示例建议动作：今天先转任务给项目负责人判断是否值得申请。',
      tags: ['示例', '强相关', '官方来源', '建议今天处理', '未核验'],
      profileTitle: '示例组织画像',
    },
    {
      id: 'demo-brief-client-risk',
      demo: true,
      title: '示例：某客户所在城市发布社会组织监管新要求',
      judgment: '示例顾问判断：该政策可能影响客户项目的合规材料和活动审批节奏。',
      why: '示例：系统会把公开政策与客户地域、项目类型、近期日程联系起来，而不是只做关键词搜索。',
      evidence: '示例证据：民政部门通知、政策解读、适用时间。',
      gap: '示例待补：需要确认客户是否属于适用范围，以及当前项目是否已有相关材料。',
      action: '示例建议动作：标记为风险线索，进入下周项目复盘。',
      tags: ['示例', '可能相关', '官方来源', '本周重点观察', '未核验'],
      profileTitle: '示例客户画像',
    },
  ];

  const advisorBriefItems = advisorBriefRealItems.length ? advisorBriefRealItems : demoBriefItems;
  const opportunityItems = advisorCandidates
    .filter(candidateLooksLikeOpportunity)
    .slice(0, 4)
    .map((candidate) => buildAdvisorPrototypeItem(candidate, 'opportunity'));
  const riskItems = advisorCandidates
    .filter(candidateLooksLikeRisk)
    .slice(0, 4)
    .map((candidate) => buildAdvisorPrototypeItem(candidate, 'risk'));
  const evidenceItems = advisorCandidates
    .filter((candidate) => candidate.evidenceStatus || candidate.externalEvidenceCardId)
    .slice(0, 6)
    .map((candidate) => buildAdvisorPrototypeItem(candidate, 'evidence'));
  const pendingEvidenceCount = mainCandidates.filter((candidate) => candidate.evidenceStatus === 'candidate').length;
  const acceptedEvidenceCount = mainCandidates.filter((candidate) => candidate.evidenceStatus === 'accepted').length;
  const rejectedEvidenceCount = mainCandidates.filter((candidate) => candidate.evidenceStatus === 'rejected').length;
  const scopedCandidateCount = mainCandidates.filter((candidate) => candidate.scopeType && candidate.scopeId).length;
  const todayActionCount = advisorBriefRealItems.length;

  const enrichmentCards = visibleProfiles.slice(0, 4).map((profile) => {
    const profileRadarId = profile.radarId || '';
    const profileCandidates = mainCandidates.filter((candidate) => candidate.radarId === profileRadarId);
    const candidateCount = profileCandidates.length;
    const evidenceCount = profileCandidates.filter((candidate) => candidate.evidenceStatus === 'accepted').length;
    return {
      id: profile.id,
      title: profileDisplayTitle(profile, '系统理解对象'),
      type: profile.profileKind === 'custom' ? '自定义观察对象' : profileScopeLabel(profile),
      summary: profile.effectiveSummary || profile.summary || '系统已生成基础理解，等待更多工作台、项目和公开资料进入底座。',
      status: candidateCount
        ? `已关联 ${candidateCount} 条公开线索，${evidenceCount} 条已通过核验`
        : '待接入真实公开资料补全',
      demo: false,
    };
  });

  const demoEnrichmentCards = [
    {
      id: 'demo-enrichment-client',
      title: '示例客户：公开资料补全',
      type: '示例 / 待接入',
      summary: '未来这里会展示系统从官网、年报、媒体报道中补齐的客户公开背景、合作网络和待核验事实。',
      status: '示例：当前只是产品形态占位',
      demo: true,
    },
    {
      id: 'demo-enrichment-project',
      title: '示例项目：公开语料补全',
      type: '示例 / 待接入',
      summary: '未来新建项目后，系统会自动搜索公开资料，补充项目相关政策、资助方、合作案例和舆情样本。',
      status: '示例：待接入真实补全链路',
      demo: true,
    },
  ];

  const visibleEnrichmentCards = enrichmentCards.length ? enrichmentCards : demoEnrichmentCards;

  const renderAdvisorActions = (item: AdvisorPrototypeItem) => {
    const candidate = item.candidate;
    if (!candidate) {
      return (
        <div className="flex flex-wrap gap-2 pt-1">
          <button type="button" disabled className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-[12px] font-semibold text-gray-400">
            示例不可操作
          </button>
        </div>
      );
    }
    const isExpanded = expandedCandidateId === candidate.id;
    return (
      <div className="flex flex-wrap gap-2 pt-1">
        <button
          type="button"
          onClick={() => void openTaskModal(candidate)}
          className="rounded-md bg-gray-950 px-3 py-2 text-[12px] font-semibold text-white hover:bg-gray-800"
        >
          转任务
        </button>
        <button
          type="button"
          onClick={() => {
            setSelectedCandidateId(candidate.id);
            setExpandedCandidateId((current) => (current === candidate.id ? '' : candidate.id));
          }}
          className="rounded-md border border-gray-200 bg-white px-3 py-2 text-[12px] font-semibold text-gray-700 hover:bg-gray-50"
        >
          {isExpanded ? '收起分析' : '深度分析'}
        </button>
        <button
          type="button"
          onClick={() => {
            if (!candidate.sourceUrl) return;
            window.open(candidate.sourceUrl, '_blank', 'noopener,noreferrer');
          }}
          disabled={!candidate.sourceUrl}
          className="rounded-md border border-gray-200 bg-white px-3 py-2 text-[12px] font-semibold text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-45"
        >
          打开来源
        </button>
        {candidate.evidenceStatus === 'candidate' && (
          <>
            <button
              type="button"
              onClick={() => void handleReviewEvidence(candidate, 'accept')}
              disabled={evidenceReviewPendingId === candidate.id}
              className="rounded-md border border-emerald-100 bg-emerald-50 px-3 py-2 text-[12px] font-semibold text-emerald-700 hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              通过
            </button>
            <button
              type="button"
              onClick={() => void handleReviewEvidence(candidate, 'reject')}
              disabled={evidenceReviewPendingId === candidate.id}
              className="rounded-md border border-rose-100 bg-rose-50 px-3 py-2 text-[12px] font-semibold text-rose-700 hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              不采用
            </button>
          </>
        )}
      </div>
    );
  };

  const renderAdvisorItemCard = (item: AdvisorPrototypeItem, compact = false) => {
    const candidate = item.candidate;
    const isExpanded = candidate && expandedCandidateId === candidate.id;
    const profileTitle = item.profileTitle || (candidate ? profileSourceTitle(profileByRadarId.get(candidate.radarId), radarMap.get(candidate.radarId)) : '示例顾问画像');
    return (
      <article key={item.id} className={`rounded-lg border border-gray-200 bg-white shadow-sm ${compact ? 'p-4' : 'p-5'}`}>
        <div className="flex flex-wrap items-center gap-2">
          {item.tags.slice(0, compact ? 4 : 6).map((tag) => (
            <span
              key={`${item.id}-${tag}`}
              className={`rounded-full px-2.5 py-1 text-[11px] font-bold ${
                tag === '示例'
                  ? 'bg-amber-50 text-amber-700'
                  : tag.includes('未核验') || tag.includes('待')
                    ? 'bg-orange-50 text-orange-700'
                    : tag.includes('官方') || tag.includes('强相关')
                      ? 'bg-emerald-50 text-emerald-700'
                      : 'bg-gray-100 text-gray-600'
              }`}
            >
              {tag}
            </span>
          ))}
        </div>
        <div className="mt-4">
          <p className="text-[12px] font-bold uppercase tracking-[0.18em] text-gray-400">顾问判断</p>
          <h3 className={`${compact ? 'text-[15px]' : 'text-[18px]'} mt-2 font-bold leading-snug text-gray-950`}>
            {item.judgment}
          </h3>
          <p className="mt-2 text-[13px] leading-6 text-gray-500">{item.title}</p>
        </div>
        <div className={`mt-4 grid gap-3 ${compact ? 'grid-cols-1' : 'grid-cols-1 md:grid-cols-2'}`}>
          <div className="rounded-md bg-gray-50 p-3">
            <p className="text-[11px] font-bold text-gray-400">为什么推给你</p>
            <p className="mt-1 text-[13px] leading-6 text-gray-700">{item.why}</p>
          </div>
          <div className="rounded-md bg-gray-50 p-3">
            <p className="text-[11px] font-bold text-gray-400">证据</p>
            <p className="mt-1 text-[13px] leading-6 text-gray-700">{item.evidence}</p>
          </div>
          <div className="rounded-md bg-gray-50 p-3">
            <p className="text-[11px] font-bold text-gray-400">还缺什么</p>
            <p className="mt-1 text-[13px] leading-6 text-gray-700">{item.gap}</p>
          </div>
          <div className="rounded-md bg-gray-50 p-3">
            <p className="text-[11px] font-bold text-gray-400">建议动作</p>
            <p className="mt-1 text-[13px] leading-6 text-gray-700">{item.action}</p>
          </div>
        </div>
        <div className="mt-4 flex flex-col gap-3 border-t border-gray-100 pt-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-[12px] text-gray-500">
            来源对象：<span className="font-semibold text-gray-700">{profileTitle}</span>
            {item.sourceMeta ? <span> · {item.sourceMeta}</span> : null}
          </p>
          {renderAdvisorActions(item)}
        </div>
        {candidate && isExpanded ? (
          <div className="mt-4 border-t border-gray-100 pt-4">
            {renderExpandedCandidate(candidate, profileTitle)}
          </div>
        ) : null}
      </article>
    );
  };

  const renderCandidateCard = (candidate: TopicCandidate) => {
    const radarTitle = profileSourceTitle(profileByRadarId.get(candidate.radarId), radarMap.get(candidate.radarId));
    const relatedTaskCount = Math.max((relatedTasksByCandidate.get(candidate.id) || []).length, candidate.convertedTaskId ? 1 : 0);
    const insight = insightCache[candidate.id];
    const isExpanded = expandedCandidateId === candidate.id;
    const evidenceStatus = evidenceStatusForCandidate(candidate);
    return (
      <TopicIntelInboxCard
        key={candidate.id}
        candidate={candidate}
        radarTitle={radarTitle}
        relatedTaskCount={relatedTaskCount}
        mainBadge={mainBadgeForCandidate(candidate)}
        sourceStatusText={sourceStatusTextForCandidate(candidate)}
        scopeLabel={scopeLabelForCandidate(candidate)}
        dataCenterStatusText={dataCenterStatusTextForCandidate(candidate)}
        evidenceStatusText={evidenceStatus.text}
        evidenceStatusTone={evidenceStatus.tone}
        relevanceReason={relevanceReasonForCandidate(candidate, radarTitle, insight)}
        suggestedAction={suggestedActionForCandidate(candidate)}
        isDeepAnalysisOpen={isExpanded}
        canReviewEvidence={candidate.evidenceStatus === 'candidate'}
        isEvidenceReviewPending={evidenceReviewPendingId === candidate.id}
        onOpenTask={() => void openTaskModal(candidate)}
        onToggleDeepAnalysis={() => {
          setSelectedCandidateId(candidate.id);
          setExpandedCandidateId((current) => (current === candidate.id ? '' : candidate.id));
        }}
        onOpenSource={() => {
          if (!candidate.sourceUrl) return;
          window.open(candidate.sourceUrl, '_blank', 'noopener,noreferrer');
        }}
        onAcceptEvidence={() => void handleReviewEvidence(candidate, 'accept')}
        onRejectEvidence={() => void handleReviewEvidence(candidate, 'reject')}
      >
        {renderExpandedCandidate(candidate, radarTitle)}
      </TopicIntelInboxCard>
    );
  };

  const renderPaginationControls = (
    totalCount: number,
    currentPage: number,
    onPageChange: (page: number) => void,
  ) => {
    const totalPages = Math.max(1, Math.ceil(totalCount / TOPIC_PAGE_SIZE));
    if (totalPages <= 1) return null;
    const page = clampTopicPage(currentPage, totalCount);
    const start = (page - 1) * TOPIC_PAGE_SIZE + 1;
    const end = Math.min(totalCount, page * TOPIC_PAGE_SIZE);
    return (
      <div className="flex flex-col gap-2 rounded-lg border border-gray-100 bg-white px-4 py-3 text-[12px] text-gray-500 sm:flex-row sm:items-center sm:justify-between">
        <span>
          第 {start}-{end} 条，共 {totalCount} 条
        </span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              onPageChange(Math.max(1, page - 1));
              setExpandedCandidateId('');
            }}
            disabled={page <= 1}
            className="rounded-md border border-gray-200 bg-white px-3 py-1.5 font-semibold text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
          >
            上一页
          </button>
          <span className="min-w-[76px] text-center font-semibold text-gray-700">
            {page} / {totalPages}
          </span>
          <button
            type="button"
            onClick={() => {
              onPageChange(Math.min(totalPages, page + 1));
              setExpandedCandidateId('');
            }}
            disabled={page >= totalPages}
            className="rounded-md border border-gray-200 bg-white px-3 py-1.5 font-semibold text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
          >
            下一页
          </button>
        </div>
      </div>
    );
  };

  return (
    <div className="h-full overflow-y-auto bg-[#F9FAFB] relative font-sans text-gray-800">
      <div className="mx-auto flex max-w-[1240px] flex-col gap-7 px-5 py-7 lg:px-8">
        {globalMessage && (
          <div className="fixed left-1/2 top-5 z-50 flex -translate-x-1/2 items-center justify-center animate-in fade-in">
            <div className="flex items-center gap-2 rounded-full bg-emerald-50 px-4 py-2 text-[12px] font-bold text-emerald-600 shadow-sm">
              <CheckSquare size={14} /> {globalMessage}
            </div>
          </div>
        )}

        <section className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="grid gap-0 lg:grid-cols-[1.45fr_0.55fr]">
            <div className="p-7 lg:p-9">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-full bg-gray-950 px-3 py-1 text-[12px] font-bold text-white">外部情报顾问系统</span>
                <span className="rounded-full bg-indigo-50 px-3 py-1 text-[12px] font-bold text-indigo-700">静态产品原型</span>
              </div>
              <h1 className="mt-5 max-w-[780px] text-[30px] font-black leading-tight text-gray-950">
                今日顾问简报
              </h1>
              <p className="mt-4 max-w-[820px] text-[14px] leading-7 text-gray-600">
                系统应像熟悉组织、客户和项目的资深顾问：主动从公开渠道找机会、识别风险、补全资料，并把最值得人判断的内容摆到前面。当前先做新界面原型，真实数据优先，不足处明确标注示例。
              </p>
              {(processingCandidates.length > 0 || captureTestingRadarId) && (
                <div className="mt-5 flex items-start gap-3 rounded-md border border-indigo-100 bg-indigo-50 px-4 py-3 text-[12px] leading-5 text-indigo-800">
                  <RefreshCw size={14} className="mt-0.5 shrink-0 animate-spin text-[#5B7BFE]" />
                  <div>
                    {captureTestingRadarId ? `正在试跑：${captureProgressText(elapsedSeconds(captureTestingStartedAt, topicUiTick))}` : ''}
                    {processingCandidates.length > 0 ? ` AI 正在处理 ${processingCandidates.length} 条新线索。` : ''}
                    已有情报不会因为处理中被清空。
                  </div>
                </div>
              )}
            </div>
            <aside className="border-t border-gray-100 bg-gray-50 p-7 lg:border-l lg:border-t-0">
              <p className="text-[12px] font-bold uppercase tracking-[0.2em] text-gray-400">今日状态</p>
              <div className="mt-5 grid grid-cols-3 gap-3 lg:grid-cols-1">
                <div className="rounded-md bg-white p-4 shadow-sm">
                  <p className="text-[24px] font-black text-gray-950">{todayActionCount || advisorBriefItems.length}</p>
                  <p className="mt-1 text-[12px] text-gray-500">待处理判断</p>
                </div>
                <div className="rounded-md bg-white p-4 shadow-sm">
                  <p className="text-[24px] font-black text-gray-950">{pendingEvidenceCount}</p>
                  <p className="mt-1 text-[12px] text-gray-500">未核验证据</p>
                </div>
                <div className="rounded-md bg-white p-4 shadow-sm">
                  <p className="text-[24px] font-black text-gray-950">{scopedCandidateCount}</p>
                  <p className="mt-1 text-[12px] text-gray-500">已关联底座线索</p>
                </div>
              </div>
              <div className={`mt-4 rounded-md border px-4 py-3 text-[12px] leading-5 ${
                fetchHealthSummary.failedCount
                  ? 'border-amber-100 bg-amber-50 text-amber-800'
                  : 'border-emerald-100 bg-emerald-50 text-emerald-800'
              }`}>
                <span className="font-bold">最近抓取：</span>
                {fetchHealthSummary.runningCount
                  ? '正在抓取，旧情报会保留。'
                  : fetchHealthSummary.latestNote || '还没有试跑记录。'}
              </div>
            </aside>
          </div>
        </section>

        <section className="grid grid-cols-1 gap-5 xl:grid-cols-[1.45fr_0.55fr]">
          <div className="space-y-4">
            <div className="flex items-end justify-between gap-4">
              <div>
                <p className="text-[12px] font-bold uppercase tracking-[0.2em] text-gray-400">Briefing</p>
                <h2 className="mt-2 text-[22px] font-black text-gray-950">今天最值得处理的外部判断</h2>
              </div>
              <p className="hidden max-w-[320px] text-right text-[12px] leading-5 text-gray-500 sm:block">
                每条都按“顾问判断、原因、证据、缺口、动作”呈现，避免只给资讯列表。
              </p>
            </div>
            {advisorBriefItems.map((item) => renderAdvisorItemCard(item))}
          </div>

          <div className="space-y-5">
            <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-[12px] font-bold uppercase tracking-[0.18em] text-gray-400">Public Data</p>
                  <h2 className="mt-2 text-[18px] font-black text-gray-950">公开资料补全</h2>
                </div>
                <FileText size={18} className="text-gray-400" />
              </div>
              <div className="mt-4 space-y-3">
                {visibleEnrichmentCards.slice(0, 3).map((card) => (
                  <div key={card.id} className="rounded-md border border-gray-100 bg-gray-50 p-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={`rounded-full px-2.5 py-1 text-[11px] font-bold ${card.demo ? 'bg-amber-50 text-amber-700' : 'bg-blue-50 text-blue-700'}`}>
                        {card.type}
                      </span>
                    </div>
                    <h3 className="mt-3 text-[14px] font-bold text-gray-950">{card.title}</h3>
                    <p className="mt-2 text-[12px] leading-5 text-gray-600">{card.summary}</p>
                    <p className="mt-2 text-[12px] font-semibold text-gray-500">{card.status}</p>
                  </div>
                ))}
              </div>
            </section>

            <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-[12px] font-bold uppercase tracking-[0.18em] text-gray-400">Evidence</p>
                  <h2 className="mt-2 text-[18px] font-black text-gray-950">证据池</h2>
                </div>
                <CheckSquare size={18} className="text-gray-400" />
              </div>
              <div className="mt-4 grid grid-cols-3 gap-2">
                <div className="rounded-md bg-orange-50 p-3">
                  <p className="text-[20px] font-black text-orange-700">{pendingEvidenceCount}</p>
                  <p className="text-[11px] text-orange-700">未核验</p>
                </div>
                <div className="rounded-md bg-emerald-50 p-3">
                  <p className="text-[20px] font-black text-emerald-700">{acceptedEvidenceCount}</p>
                  <p className="text-[11px] text-emerald-700">已通过</p>
                </div>
                <div className="rounded-md bg-rose-50 p-3">
                  <p className="text-[20px] font-black text-rose-700">{rejectedEvidenceCount}</p>
                  <p className="text-[11px] text-rose-700">不采用</p>
                </div>
              </div>
              <div className="mt-4 space-y-3">
                {evidenceItems.length ? evidenceItems.slice(0, 3).map((item) => renderAdvisorItemCard(item, true)) : (
                  <div className="rounded-md border border-dashed border-gray-200 bg-gray-50 px-4 py-5 text-[12px] leading-5 text-gray-500">
                    还没有真实证据卡。等抓取线索进入底座后，这里会显示未核验、已通过和不采用的外部证据。
                  </div>
                )}
              </div>
            </section>
          </div>
        </section>

        <section className="space-y-4">
          <div className="flex items-end justify-between gap-4">
            <div>
              <p className="text-[12px] font-bold uppercase tracking-[0.2em] text-gray-400">Opportunity Pool</p>
              <h2 className="mt-2 text-[22px] font-black text-gray-950">机会池</h2>
            </div>
            <p className="hidden max-w-[420px] text-right text-[12px] leading-5 text-gray-500 sm:block">
              资助、合作、政策窗口、潜在资源等行动型机会，会在这里沉淀。
            </p>
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {(opportunityItems.length ? opportunityItems : [
              {
                id: 'demo-opportunity',
                demo: true,
                title: '示例：行业平台发布公益数字化合作伙伴招募',
                judgment: '示例顾问判断：这可能是项目资源和外部合作入口，不只是新闻。',
                why: '示例：系统会结合组织服务对象、项目模块和近期任务，判断是否值得建立联系。',
                evidence: '示例证据：平台公告、招募范围、合作案例。',
                gap: '示例待补：需要核对对方过往合作质量和投入成本。',
                action: '示例建议动作：列入合作池，安排同事初步联系。',
                tags: ['示例', '可能相关', '机构来源', '本周重点观察', '未核验'],
                profileTitle: '示例组织画像',
              },
            ] as AdvisorPrototypeItem[]).map((item) => renderAdvisorItemCard(item, true))}
          </div>
        </section>

        <section className="space-y-4">
          <div className="flex items-end justify-between gap-4">
            <div>
              <p className="text-[12px] font-bold uppercase tracking-[0.2em] text-gray-400">Risk And Public Voice</p>
              <h2 className="mt-2 text-[22px] font-black text-gray-950">风险与舆情</h2>
            </div>
            <p className="hidden max-w-[420px] text-right text-[12px] leading-5 text-gray-500 sm:block">
              当前舆情观察基于公开来源样本，不代表全网完整舆情；重点是提前提醒可能影响工作安排的变化。
            </p>
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {(riskItems.length ? riskItems : [
              {
                id: 'demo-risk',
                demo: true,
                title: '示例：某领域公益项目近期出现公众讨论升温',
                judgment: '示例顾问判断：如果组织或客户有同类项目，需要提前检查表达方式和合规材料。',
                why: '示例：系统会把舆情样本与客户项目类型、任务日程和公开议题联系起来。',
                evidence: '示例证据：公开讨论样本、媒体报道、政策背景。',
                gap: '示例待补：需要确认讨论是否代表广泛趋势，以及是否涉及当前客户。',
                action: '示例建议动作：先作为风险观察，不急于转任务。',
                tags: ['示例', '待判断', '公开来源', '本周重点观察', '未核验'],
                profileTitle: '示例客户画像',
              },
            ] as AdvisorPrototypeItem[]).map((item) => renderAdvisorItemCard(item, true))}
          </div>
        </section>
      </div>

      {editingPrefIndex !== null && tempPref && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-md z-50 flex items-center justify-center animate-in fade-in">
	          <div className={`bg-white rounded-[28px] shadow-[0_20px_60px_rgba(0,0,0,0.15)] ${radarModalMode === 'manage' ? 'w-[1120px]' : 'w-[860px]'} max-w-[94vw] overflow-hidden transform animate-in zoom-in-95 border border-gray-100`} onClick={(event) => event.stopPropagation()}>
            <div className="px-8 py-6 border-b border-gray-100 flex items-center gap-4 bg-white">
	              <button type="button" onClick={closeRadarConfig} className="rounded-2xl border border-gray-200 bg-white p-2 text-gray-400 transition hover:text-gray-700" aria-label="关闭情报画像弹窗">
                <X size={18} />
              </button>
              <h3 className="text-[18px] font-bold text-gray-900 flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-xl bg-blue-50 text-[#5B7BFE] flex items-center justify-center">
                  <Target size={16} strokeWidth={2.5} />
                </div>
                {radarModalMode === 'manage' ? '管理画像' : '新建自定义画像'}
              </h3>
            </div>

            <div className="max-h-[calc(88vh-150px)] overflow-y-auto p-8 space-y-6">
              {radarModalNotice && (
                <div className="flex items-start gap-2 rounded-lg border border-amber-100 bg-amber-50 px-4 py-3 text-[12px] leading-5 text-amber-800">
                  <AlertCircle size={15} className="mt-0.5 shrink-0" />
                  <span>{radarModalNotice}</span>
                </div>
              )}

              {radarModalMode === 'manage' && radarCards.some((item) => item.id !== 'placeholder-new') && (
                <div className="rounded-lg border border-gray-100 bg-gray-50/70 p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <p className="text-[12px] font-bold text-gray-900">已有画像</p>
                      <p className="mt-1 text-[12px] text-gray-500">在这里校准画像、设置推送，或手动试跑一次。</p>
                    </div>
                  </div>
                  <div className="mt-4 grid max-h-[400px] grid-cols-1 gap-3 overflow-y-auto pr-1">
                    {radarCards.filter((item) => item.id !== 'placeholder-new').map((pref, index) => {
                      const profile = visibleProfiles.find((item) => item.id === pref.id) || null;
                      const active = tempPref.id === pref.id;
                      const isTesting = profileTrialPendingId === pref.id || captureTestingRadarId === pref.radarId;
                      const isDeleting = radarDeletePendingId === pref.id;
                      const lastFetch = pref.lastFetch;
                      const lastFetchNote = latestFetchStatusNote(lastFetch);
                      const isAutoProfile = profile?.profileKind !== 'custom';
                      return (
                        <div
                          key={pref.id}
                          className={`rounded-lg border bg-white p-4 text-[12px] transition-colors ${
                            active ? 'border-[#5B7BFE] shadow-[0_8px_24px_rgba(91,123,254,0.12)]' : 'border-gray-200'
                          }`}
                        >
                          <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                            <div className="min-w-0 flex-1">
                              <div className="flex flex-wrap items-center gap-2">
                                <span className="truncate text-[14px] font-bold text-gray-900">{pref.title || '未命名画像'}</span>
                                <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${
                                  pref.fetchEnabled ? 'bg-emerald-50 text-emerald-700' : 'bg-gray-100 text-gray-500'
                                }`}>
                                  {pref.fetchEnabled ? '已开启推送' : '手动试跑'}
                                </span>
                                {isAutoProfile && (
                                  <span className="rounded-full bg-purple-50 px-2.5 py-1 text-[11px] font-semibold text-purple-700">
                                    自动画像
                                  </span>
                                )}
                                <span className="rounded-full bg-indigo-50 px-2.5 py-1 text-[11px] font-semibold text-indigo-700">
                                  {pushFrequencyLabel(pref.pushFrequency, pref.pushWeekday)}
                                </span>
                              </div>
                              <p className="mt-2 max-h-[42px] overflow-hidden text-[12px] leading-[21px] text-gray-500">
                                {pref.prompt || '还没有填写画像概况。'}
                              </p>
                              {profile && (
                                <div className="mt-2 rounded-md border border-purple-100 bg-purple-50/60 px-3 py-2 text-[11px] leading-5 text-purple-800">
                                  <p className="font-semibold">{profile.profileKind === 'custom' ? '自定义画像' : profileScopeLabel(profile)} · {profileStatusLabel(profile)}</p>
                                  <p className="line-clamp-2">{profile.effectiveSummary || profile.summary || '系统根据统一数据底座维护这张画像。'}</p>
                                </div>
                              )}
                            </div>
                            <div className="flex shrink-0 flex-wrap gap-2">
                              {profile && (
                                <>
                                  <button
                                    type="button"
                                    onClick={() => openRadarConfig(profile, index, 'manage')}
                                    className="inline-flex items-center gap-1.5 rounded-md border border-gray-200 bg-white px-3 py-2 text-[12px] font-semibold text-gray-600 hover:bg-gray-50"
                                  >
                                    <Pencil size={13} />
                                    编辑
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => void handleTrialRunProfile(profile)}
                                    disabled={Boolean(profileTrialPendingId)}
                                    className="inline-flex items-center gap-1.5 rounded-md border border-indigo-100 bg-indigo-50 px-3 py-2 text-[12px] font-semibold text-indigo-700 hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-50"
                                  >
                                    {isTesting ? <RefreshCw size={13} className="animate-spin" /> : <Search size={13} />}
                                    {isTesting ? '试跑中…' : '试跑一次'}
                                  </button>
                                  {isAutoProfile ? (
                                    <button
                                      type="button"
                                      onClick={() => void handleRefreshProfile(profile)}
                                      disabled={Boolean(profileRefreshPendingId)}
                                      className="inline-flex items-center gap-1.5 rounded-md border border-gray-200 bg-white px-3 py-2 text-[12px] font-semibold text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
                                    >
                                      {profileRefreshPendingId === profile.id ? <RefreshCw size={13} className="animate-spin" /> : <Sparkles size={13} />}
                                      刷新理解
                                    </button>
                                  ) : (
                                    <button
                                      type="button"
                                      onClick={() => void handleDeleteProfile(profile)}
                                      disabled={Boolean(radarDeletePendingId)}
                                      className="inline-flex items-center gap-1.5 rounded-md border border-rose-100 bg-white px-3 py-2 text-[12px] font-semibold text-rose-600 hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-50"
                                    >
                                      {isDeleting ? <RefreshCw size={13} className="animate-spin" /> : <Trash2 size={13} />}
                                      {isDeleting ? '删除中…' : '删除'}
                                    </button>
                                  )}
                                </>
                              )}
                            </div>
                          </div>

                          <div className="mt-4 grid grid-cols-2 gap-2 lg:grid-cols-5">
                            <div className="rounded-md border border-gray-100 bg-gray-50 px-3 py-2">
                              <p className="text-[11px] font-semibold text-gray-400">情报</p>
                              <p className="mt-1 font-bold text-gray-800">{pref.candidateCount} 条</p>
                            </div>
                            <div className="rounded-md border border-gray-100 bg-gray-50 px-3 py-2">
                              <p className="text-[11px] font-semibold text-gray-400">关注重点</p>
                              <p className="mt-1 font-bold text-gray-800">{pref.focus.length} 个</p>
                            </div>
                            <div className="rounded-md border border-gray-100 bg-gray-50 px-3 py-2">
                              <p className="text-[11px] font-semibold text-gray-400">排除方向</p>
                              <p className="mt-1 font-bold text-gray-800">{pref.excludeTerms.length} 个</p>
                            </div>
                            <div className="rounded-md border border-gray-100 bg-gray-50 px-3 py-2">
                              <p className="text-[11px] font-semibold text-gray-400">重点网址</p>
                              <p className="mt-1 font-bold text-gray-800">{pref.priorityUrls.length || pref.preferredSources.length} 个</p>
                            </div>
                            <div className="rounded-md border border-gray-100 bg-gray-50 px-3 py-2">
                              <p className="text-[11px] font-semibold text-gray-400">下次推送</p>
                              <p className="mt-1 font-bold text-gray-800">{nextPushTextForRadar(pref)}</p>
                            </div>
                          </div>

                          <div className={`mt-3 rounded-md border px-3 py-2 ${
                            lastFetch?.status === 'fetch_failed'
                              ? 'border-rose-100 bg-rose-50 text-rose-700'
                              : 'border-gray-100 bg-gray-50 text-gray-600'
                          }`}>
                            <div className="flex flex-col gap-1 md:flex-row md:items-center md:justify-between">
                              <p className="font-semibold">
                                最近抓取：{topicFetchStatusLabel(lastFetch?.status)} · {radarFetchSummaryText(lastFetch)}
                              </p>
                              <p className="text-[11px] text-gray-400">
                                {formatTopicDateTime(lastFetch?.finishedAt || lastFetch?.startedAt || lastFetch?.createdAt) || '尚未运行'}
                              </p>
                            </div>
                            {lastFetch?.error && (
                              <p className="mt-1 break-all text-[11px] leading-5">{lastFetch.error}</p>
                            )}
                            {lastFetchNote && !lastFetch?.error && (
                              <p className="mt-1 text-[11px] leading-5 text-gray-500">{lastFetchNote}</p>
                            )}
                            {lastFetch && (lastFetch.fetchedCount || 0) === 0 && !lastFetch.error && (
                              <p className="mt-1 text-[11px] leading-5 text-gray-400">
                                没有新增通常是因为画像关注点命中不足、时间范围较窄、来源不可访问，或与已有情报重复；AI 负责生成检索方向和轻分析，不会凭空生成外部资讯。
                              </p>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {radarModalMode === 'manage' && (
                <div className="flex items-center justify-between rounded-lg border border-indigo-100 bg-indigo-50 px-4 py-3">
                  <div>
                    <p className="text-[12px] font-bold text-indigo-900">当前编辑</p>
                    <p className="mt-1 text-[12px] text-indigo-700">{tempPref.title || '未命名画像'}</p>
                  </div>
                  <span className="rounded-full bg-white px-3 py-1 text-[11px] font-semibold text-indigo-700">
                    {tempPref.id.startsWith('placeholder-') ? '新建自定义画像' : (tempPref.profileKind === 'custom' ? '自定义画像' : '自动画像')}
                  </span>
                </div>
              )}

              {tempPrefIsSystemManaged && (
                <div className="rounded-lg border border-purple-100 bg-purple-50/70 p-5 text-[13px] leading-6 text-purple-900">
                  <p className="text-[12px] font-bold uppercase tracking-widest text-purple-500">自动画像</p>
                  <h4 className="mt-2 text-[16px] font-bold text-gray-950">{activeTempProfile?.title || tempPref.title || '自动情报画像'}</h4>
                  <p className="mt-2 text-purple-800">
                    {activeTempProfile?.effectiveSummary || activeTempProfile?.summary || '这张画像由系统根据组织、客户或项目底座生成；管理员可以在下方校准概况、关注重点和重点网址。'}
                  </p>
                  {activeTempProfile?.searchIntents?.length ? (
                    <div className="mt-4 grid grid-cols-1 gap-2 md:grid-cols-2">
                      {activeTempProfile.searchIntents.slice(0, 4).map((intent) => (
                        <div key={`${intent.direction}-${intent.query}`} className="rounded-md border border-purple-100 bg-white px-3 py-2">
                          <p className="text-[11px] font-bold text-purple-500">{profileDirectionLabel(intent.direction)}</p>
                          <p className="mt-1 font-semibold text-gray-800">{intent.query}</p>
                          {intent.why && <p className="mt-1 text-[12px] text-gray-500">{intent.why}</p>}
                        </div>
                      ))}
                    </div>
                  ) : null}
                  <div className="mt-4 flex flex-wrap gap-2">
                    {activeTempProfile && (
                      <>
                        <button
                          type="button"
                          onClick={() => void handleRefreshProfile(activeTempProfile)}
                          disabled={Boolean(profileRefreshPendingId)}
                          className="inline-flex items-center gap-2 rounded-md border border-gray-200 bg-white px-4 py-2.5 text-[13px] font-semibold text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {profileRefreshPendingId === activeTempProfile.id ? <RefreshCw size={14} className="animate-spin" /> : <Sparkles size={14} />}
                          刷新画像
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleTrialRunProfile(activeTempProfile)}
                          disabled={Boolean(profileTrialPendingId)}
                          className="inline-flex items-center gap-2 rounded-md border border-indigo-100 bg-indigo-50 px-4 py-2.5 text-[13px] font-semibold text-indigo-700 hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {profileTrialPendingId === activeTempProfile.id ? <RefreshCw size={14} className="animate-spin" /> : <Search size={14} />}
                          手动试跑一次
                        </button>
                      </>
                    )}
                  </div>
                </div>
              )}

              <div>
                <label className="block text-[12px] font-bold text-gray-500 uppercase tracking-widest mb-2.5 flex justify-between items-end">
                  画像概况 / 校准说明
                  <button
                    type="button"
                    onClick={() => void handleAssistRadarDraft()}
                    disabled={isAssistingRadar || !tempPref.prompt.trim()}
                    className="text-[11px] font-semibold text-indigo-500 flex items-center gap-1 bg-indigo-50 px-2.5 py-1 rounded-full border border-indigo-100 hover:bg-indigo-100 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {isAssistingRadar ? <RefreshCw size={10} className="animate-spin" /> : <Sparkles size={10} />}
                    {isAssistingRadar ? 'AI 补强中…' : '扩写画像 + 提炼标题'}
                  </button>
                </label>
                <textarea
                  value={tempPref.prompt}
                  onChange={(event) => setTempPref({ ...tempPref, prompt: event.target.value })}
                  placeholder="例如：持续寻找适合本组织或项目的资助机会、合作方、政策窗口、公开风险和可借鉴案例。"
                  className="w-full bg-gray-50 border border-gray-200 rounded-2xl p-4 text-[14px] font-medium outline-none focus:bg-white focus:border-[#5B7BFE] min-h-[120px] resize-none"
                />
              </div>

              <div>
                <label className="block text-[12px] font-bold text-gray-500 uppercase tracking-widest mb-2.5">画像名称</label>
                <input
                  type="text"
                  value={tempPref.title}
                  onChange={(event) => setTempPref({ ...tempPref, title: event.target.value })}
                  placeholder="例如：组织公开机会画像"
                  className="w-full bg-gray-50 border border-gray-200 rounded-2xl px-4 py-3 text-[13px] font-bold outline-none focus:bg-white focus:border-[#5B7BFE]"
                />
              </div>

              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <section className="rounded-lg border border-gray-100 bg-gray-50/70 p-4">
                  <p className="text-[12px] font-bold uppercase tracking-widest text-gray-500">关注重点</p>
                  <p className="mt-2 text-[12px] leading-5 text-gray-500">一行一个重点。系统会优先把这些内容转化为检索意图，用于管理员校准试跑。</p>
                  <textarea
                    value={tempPref.focus.join('\n')}
                    onChange={(event) => setTempPref({
                      ...tempPref,
                      focus: event.target.value.split('\n').map((item) => item.trim()).filter(Boolean),
                    })}
                    placeholder="例如：\n广东地区公益资助机会\n儿童心理健康合作方\n公益数字化标杆案例"
                    className="mt-3 min-h-[150px] w-full resize-none rounded-2xl border border-gray-200 bg-white p-4 text-[13px] outline-none focus:border-[#5B7BFE]"
                  />
                </section>

                <section className="rounded-lg border border-gray-100 bg-gray-50/70 p-4">
                  <p className="text-[12px] font-bold uppercase tracking-widest text-gray-500">排除方向</p>
                  <p className="mt-2 text-[12px] leading-5 text-gray-500">一行一个排除项。用于减少明显不相关、过宽泛或已经确认不采用的方向。</p>
                  <textarea
                    value={tempPref.excludeTerms.join('\n')}
                    onChange={(event) => setTempPref({
                      ...tempPref,
                      excludeTerms: event.target.value.split('\n').map((item) => item.trim()).filter(Boolean),
                    })}
                    placeholder="例如：\n纯商业广告\n无发布时间来源\n与公益行业无关的泛科技资讯"
                    className="mt-3 min-h-[150px] w-full resize-none rounded-2xl border border-gray-200 bg-white p-4 text-[13px] outline-none focus:border-[#5B7BFE]"
                  />
                </section>
              </div>

              <div className={`grid grid-cols-1 gap-4 rounded-lg border border-gray-100 bg-gray-50/70 p-4 ${tempPref.pushFrequency === 'weekly' ? 'md:grid-cols-5' : 'md:grid-cols-4'}`}>
                <label className="flex items-center gap-3 text-[13px] font-semibold text-gray-700">
                  <input
                    type="checkbox"
                    checked={tempPref.fetchEnabled}
                    onChange={(event) => setTempPref({ ...tempPref, fetchEnabled: event.target.checked })}
                    className="h-4 w-4 rounded border-gray-300 text-[#5B7BFE]"
                  />
                  开启定时推送
                </label>
                <div>
                  <label className="mb-2 block text-[12px] font-bold uppercase tracking-widest text-gray-500">推送频率</label>
                  <select
                    value={tempPref.pushFrequency}
                    onChange={(event) => {
                      const pushFrequency = event.target.value as TopicRadarPushFrequency;
                      setTempPref({
                        ...tempPref,
                        pushFrequency,
                        pushWeekday: pushFrequency === 'weekly' ? (tempPref.pushWeekday || currentIsoWeekday()) : tempPref.pushWeekday,
                      });
                    }}
                    className="w-full rounded-md border border-gray-200 bg-white px-3 py-2 text-[13px] outline-none focus:border-[#5B7BFE]"
                  >
                    <option value="manual">仅手动</option>
                    <option value="daily">每天</option>
                    <option value="workday">工作日</option>
                    <option value="weekly">每周（{weekdayLabel(tempPref.pushWeekday)}）</option>
                  </select>
                </div>
                {tempPref.pushFrequency === 'weekly' && (
                  <div>
                    <label className="mb-2 block text-[12px] font-bold uppercase tracking-widest text-gray-500">推送周几</label>
                    <select
                      value={tempPref.pushWeekday || currentIsoWeekday()}
                      onChange={(event) => setTempPref({ ...tempPref, pushWeekday: Number(event.target.value) })}
                      className="w-full rounded-md border border-gray-200 bg-white px-3 py-2 text-[13px] outline-none focus:border-[#5B7BFE]"
                    >
                      {TOPIC_WEEKDAY_LABELS.slice(1).map((label, index) => (
                        <option key={label} value={index + 1}>周{label}</option>
                      ))}
                    </select>
                  </div>
                )}
                <div>
                  <label className="mb-2 block text-[12px] font-bold uppercase tracking-widest text-gray-500">推送时间</label>
                  <input
                    type="time"
                    value={tempPref.pushTime}
                    onChange={(event) => setTempPref({ ...tempPref, pushTime: event.target.value })}
                    className="w-full rounded-md border border-gray-200 bg-white px-3 py-2 text-[13px] outline-none focus:border-[#5B7BFE]"
                  />
                </div>
                <div>
                  <label className="mb-2 block text-[12px] font-bold uppercase tracking-widest text-gray-500">时间范围</label>
                  <select
                    value={tempPref.timeRange}
                    onChange={(event) => setTempPref({ ...tempPref, timeRange: event.target.value })}
                    className="w-full rounded-md border border-gray-200 bg-white px-3 py-2 text-[13px] outline-none focus:border-[#5B7BFE]"
                  >
                    <option value="3_days">近 3 天</option>
                    <option value="7_days">近 7 天</option>
                    <option value="30_days">近 30 天</option>
                  </select>
                </div>
              </div>

	              <div className="rounded-[24px] border border-gray-100 bg-gray-50/60 p-5">
	                <label className="block text-[12px] font-bold text-gray-500 uppercase tracking-widest mb-2.5">优先检索网址</label>
	                <div className="mt-3 flex gap-2">
                  <input
                    type="text"
                    value={preferredSourceDraft}
                    onChange={(event) => setPreferredSourceDraft(event.target.value)}
                    placeholder="例如：https://www.chinadevelopmentbrief.org.cn 或机构公告页网址"
                    className="flex-1 bg-white border border-gray-200 rounded-2xl px-4 py-3 text-[13px] font-medium outline-none focus:border-[#5B7BFE]"
                  />
	                  <button
	                    type="button"
	                    onClick={() => void handleAddPreferredSource()}
	                    disabled={isGeneratingSourceLabel || !preferredSourceDraft.trim()}
	                    className="px-4 py-3 rounded-2xl text-[12px] font-semibold bg-indigo-50 border border-indigo-100 text-indigo-700 hover:bg-indigo-100 transition-all disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-2 shrink-0"
	                  >
	                    {isGeneratingSourceLabel ? <RefreshCw size={14} className="animate-spin" /> : <Plus size={14} />}
	                    {isGeneratingSourceLabel ? '添加中…' : '添加网址'}
	                  </button>
                </div>
                {tempPref.priorityUrls.length > 0 ? (
                  <div className="mt-4 space-y-2">
                    {tempPref.priorityUrls.map((url) => {
                      const item = tempPref.preferredSources.find((source) => source.url === url) || { url, label: '重点网址' };
                      return (
                      <div key={url} className="rounded-2xl border border-indigo-100 bg-white px-4 py-3 flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="inline-flex items-center rounded-full border border-indigo-100 bg-indigo-50 px-2.5 py-1 text-[11px] font-bold text-indigo-700">
                            {item.label}
                          </div>
                          <p className="text-[12px] text-gray-500 mt-2 break-all">{item.url}</p>
                        </div>
                        <button
                          type="button"
                          onClick={() => handleRemovePreferredSource(item.url)}
                          className="text-[12px] font-semibold text-gray-400 hover:text-rose-500 transition-colors shrink-0"
                        >
                          删除
                        </button>
                      </div>
                      );
                    })}
                  </div>
                ) : (
                  <p className="text-[12px] text-gray-400 mt-4">
                    未填写时，AI 会根据画像概况和关注重点自动生成检索词；这里仅用于补充你明确想优先关注的网站。
                  </p>
                )}
              </div>
            </div>

            <div className="px-8 py-5 border-t border-gray-100 bg-gray-50/50 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <button
                type="button"
                onClick={() => (
                  activeTempProfile
                    ? void handleTrialRunProfile(activeTempProfile)
                    : void handleCaptureTestRadar(tempPref.id)
                )}
                disabled={tempPref.id.startsWith('placeholder-') || Boolean(profileTrialPendingId)}
                className="inline-flex items-center justify-center gap-2 rounded-md border border-indigo-100 bg-indigo-50 px-4 py-2.5 text-[13px] font-semibold text-indigo-700 transition-colors hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {activeTempProfile && profileTrialPendingId === activeTempProfile.id ? <RefreshCw size={14} className="animate-spin" /> : <Search size={14} />}
                {activeTempProfile && profileTrialPendingId === activeTempProfile.id ? '试跑中…' : '手动试跑一次'}
              </button>
              <div className="flex justify-end gap-3">
                <button type="button" onClick={closeRadarConfig} className="text-[13px] font-bold text-gray-500 hover:text-gray-800 px-5 py-2 transition-colors">
                  取消
                </button>
                <button
                  type="button"
                  onClick={() => void handleSavePrefEdit()}
                  disabled={isSavingRadarConfig}
                  className="px-6 py-2.5 rounded-xl text-[13px] font-semibold bg-[#5B7BFE] text-white shadow-[0_4px_12px_rgba(91,123,254,0.3)] hover:bg-[#4a6be6] transition-all inline-flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {isSavingRadarConfig ? <RefreshCw size={14} className="animate-spin" /> : null}
                  {isSavingRadarConfig ? '保存中…' : radarModalMode === 'manage' ? '保存画像' : '保存自定义画像'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {taskModalCandidate && taskDraft && (
        <div className="fixed inset-0 bg-black/30 backdrop-blur-md z-50 flex items-center justify-center animate-in fade-in">
          <div className="bg-white rounded-[28px] shadow-[0_20px_60px_rgba(0,0,0,0.15)] w-[760px] max-h-[88vh] overflow-hidden transform animate-in zoom-in-95 border border-gray-100" onClick={(event) => event.stopPropagation()}>
            <div className="px-8 py-6 border-b border-gray-100 flex items-center gap-4 bg-white">
              <button
                type="button"
                onClick={() => { if (!isSubmittingTask) closeTopicTaskModal(); }}
                className="rounded-2xl border border-gray-200 bg-white p-2 text-gray-400 transition hover:text-gray-700 disabled:cursor-not-allowed disabled:opacity-50"
                aria-label="关闭同步到任务弹窗"
                disabled={isSubmittingTask}
              >
                <X size={18} />
              </button>
              <div>
                <h3 className="text-[18px] font-bold text-gray-900 flex items-center gap-2.5">
                  <div className="w-8 h-8 rounded-xl bg-blue-50 text-[#5B7BFE] flex items-center justify-center">
                    <CheckSquare size={16} strokeWidth={2.5} />
                  </div>
                  同步到任务
                </h3>
                <p className="text-[12px] text-gray-500 mt-1">这里只创建一条任务，并把当前情报原文入口、摘要和观点写进任务说明与备注。</p>
              </div>
            </div>

            <div className="p-8 space-y-5 overflow-y-auto max-h-[calc(88vh-150px)]">
              {taskModalNotice && (
                <div className={`rounded-2xl border px-4 py-3 text-[13px] leading-6 ${
                  taskModalNotice.type === 'success'
                    ? 'border-emerald-100 bg-emerald-50 text-emerald-700'
                    : taskModalNotice.type === 'error'
                      ? 'border-rose-100 bg-rose-50 text-rose-700'
                      : 'border-blue-100 bg-blue-50 text-blue-700'
                }`}>
                  {taskModalNotice.text}
                </div>
              )}

              <div className="rounded-[24px] border border-blue-100 bg-blue-50/50 px-5 py-4">
                <p className="text-[11px] font-bold uppercase tracking-[0.24em] text-[#5B7BFE]">当前情报</p>
                <h4 className="text-[16px] font-bold text-gray-900 mt-2">{taskModalCandidate.title}</h4>
                <p className="text-[12px] text-gray-600 mt-2 leading-6">{taskModalCandidate.summary}</p>
              </div>

              {isPreparingTaskModal ? (
                <div className="rounded-[24px] border border-gray-100 bg-gray-50 px-5 py-10 text-center text-gray-500 flex flex-col items-center gap-3">
                  <RefreshCw size={20} className="animate-spin text-[#5B7BFE]" />
                  <p className="text-[13px] font-medium">正在加载可选负责人和协作者…</p>
                </div>
              ) : (
                <>
                  <div className="space-y-3">
                    <input
                      value={taskDraft.title}
                      onChange={(event) => setTaskDraft((prev) => (prev ? { ...prev, title: event.target.value } : prev))}
                      placeholder="任务标题"
                      className="w-full bg-gray-50 border border-gray-200 rounded-2xl px-4 py-3 text-[14px] font-medium outline-none focus:border-[#5B7BFE] focus:bg-white"
                    />
                    <textarea
                      value={taskDraft.desc}
                      onChange={(event) => setTaskDraft((prev) => (prev ? { ...prev, desc: event.target.value } : prev))}
                      placeholder="任务说明"
                      className="w-full min-h-[96px] bg-gray-50 border border-gray-200 rounded-2xl px-4 py-3 text-[13px] leading-6 font-medium outline-none focus:border-[#5B7BFE] focus:bg-white"
                    />
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <select
                      value={taskDraft.ownerId}
                      onChange={(event) => {
                        const owner = taskOwnerOptions.find((item) => item.id === event.target.value);
                        setTaskDraft((prev) =>
                          prev
                            ? {
                                ...prev,
                                ownerId: event.target.value,
                                ownerName: owner?.fullName || prev.ownerName,
                                collaboratorIds: prev.collaboratorIds.filter((id) => id !== event.target.value),
                              }
                            : prev,
                        );
                      }}
                      className="w-full bg-gray-50 border border-gray-200 rounded-2xl px-4 py-3 text-[13px] font-medium outline-none focus:border-[#5B7BFE] focus:bg-white"
                    >
                      <option value="">请选择负责人</option>
                      {taskOwnerOptions.map((candidate) => (
                        <option key={candidate.id} value={candidate.id}>
                          {candidate.fullName}{candidate.isSelf ? '（自己）' : ''}
                        </option>
                      ))}
                    </select>

                    <select
                      value={taskDraft.listId}
                      onChange={(event) => setTaskDraft((prev) => (prev ? { ...prev, listId: event.target.value } : prev))}
                      className="w-full bg-gray-50 border border-gray-200 rounded-2xl px-4 py-3 text-[13px] font-medium outline-none focus:border-[#5B7BFE] focus:bg-white"
                    >
                      {activeTaskLists.map((list) => (
                        <option key={list.id} value={list.id}>
                          {list.name}
                        </option>
                      ))}
                    </select>

                    <input
                      type="date"
                      value={taskDraft.dueDate}
                      onChange={(event) =>
                        setTaskDraft((prev) =>
                          prev
                            ? {
                                ...prev,
                                dueDate: event.target.value,
                                ddl: event.target.value || prev.ddl,
                              }
                            : prev,
                        )
                      }
                      className="w-full bg-gray-50 border border-gray-200 rounded-2xl px-4 py-3 text-[13px] font-medium outline-none focus:border-[#5B7BFE] focus:bg-white"
                    />

                    <select
                      value={taskDraft.priority}
                      onChange={(event) => setTaskDraft((prev) => (prev ? { ...prev, priority: event.target.value as TopicQuickTaskDraft['priority'] } : prev))}
                      className="w-full bg-gray-50 border border-gray-200 rounded-2xl px-4 py-3 text-[13px] font-medium outline-none focus:border-[#5B7BFE] focus:bg-white"
                    >
                      <option value="low">低优先级</option>
                      <option value="normal">普通优先级</option>
                      <option value="high">高优先级</option>
                    </select>
                  </div>

                  <div className="rounded-2xl border border-gray-100 bg-gray-50 px-4 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <p className="text-[12px] font-bold text-gray-700">协作者</p>
                      <p className="text-[11px] text-gray-400">可多选，负责人不重复列为协作者</p>
                    </div>
                    <div className="mt-3 flex max-h-[116px] flex-wrap gap-2 overflow-y-auto">
                      {taskOwnerOptions
                        .filter((candidate) => candidate.id !== taskDraft.ownerId)
                        .map((candidate) => {
                          const checked = taskDraft.collaboratorIds.includes(candidate.id);
                          return (
                            <button
                              key={`task-collab-${candidate.id}`}
                              type="button"
                              onClick={() => toggleTaskCollaborator(candidate.id)}
                              className={`rounded-md border px-3 py-2 text-[12px] font-semibold transition-colors ${
                                checked
                                  ? 'border-[#5B7BFE] bg-[#eef2ff] text-[#4a67f5]'
                                  : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
                              }`}
                            >
                              {candidate.fullName}{candidate.isSelf ? '（自己）' : ''}
                            </button>
                          );
                        })}
                      {taskOwnerOptions.filter((candidate) => candidate.id !== taskDraft.ownerId).length === 0 && (
                        <span className="text-[12px] text-gray-400">暂无可选协作者。</span>
                      )}
                    </div>
                  </div>

                  <input
                    value={taskDraft.ddl}
                    onChange={(event) => setTaskDraft((prev) => (prev ? { ...prev, ddl: event.target.value } : prev))}
                    placeholder="时间描述，例如 本周内 / 3 月 18 日前 / 待确认"
                    className="w-full bg-gray-50 border border-gray-200 rounded-2xl px-4 py-3 text-[13px] font-medium outline-none focus:border-[#5B7BFE] focus:bg-white"
                  />

                  <textarea
                    value={taskDraft.note}
                    onChange={(event) => setTaskDraft((prev) => (prev ? { ...prev, note: event.target.value } : prev))}
                    placeholder="给同事的补充说明。系统会自动把情报原文入口、摘要和核心观点附在任务说明与备注里。"
                    className="w-full min-h-[120px] bg-gray-50 border border-gray-200 rounded-2xl px-4 py-3 text-[13px] leading-6 font-medium outline-none focus:border-[#5B7BFE] focus:bg-white"
                  />
                </>
              )}
            </div>

            <div className="px-8 py-5 border-t border-gray-100 bg-gray-50/50 flex justify-end gap-3">
              <button
                type="button"
                onClick={() => {
                  if (isSubmittingTask) return;
                  closeTopicTaskModal();
                }}
                className="text-[13px] font-bold text-gray-500 hover:text-gray-800 px-5 py-2 transition-colors"
              >
                {createdTopicTaskId ? '关闭' : '取消'}
              </button>
              <button
                type="button"
                onClick={() => void handleSubmitTask()}
                disabled={isPreparingTaskModal || isSubmittingTask || Boolean(createdTopicTaskId)}
                className="px-6 py-2.5 rounded-xl text-[13px] font-semibold bg-[#5B7BFE] text-white shadow-[0_4px_12px_rgba(91,123,254,0.3)] hover:bg-[#4a6be6] disabled:opacity-60 disabled:cursor-not-allowed transition-all inline-flex items-center gap-2"
              >
                {isSubmittingTask ? <RefreshCw size={14} className="animate-spin" /> : <CheckSquare size={14} />}
                {createdTopicTaskId ? '已同步' : isSubmittingTask ? '同步中…' : '确认同步到任务'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
