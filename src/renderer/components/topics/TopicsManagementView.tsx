import React, { useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronUp, FilePlus2, Loader2, MessageCircle, RefreshCw, Save, Search, Send, Trash2, X } from 'lucide-react';

import type {
  EventLine,
  IntelligenceProfile,
  IntelligenceProfileMutationPayload,
  MentionCandidate,
  SessionUser,
  Task,
  TaskList,
  TaskSettings,
  TopicCandidate,
  TopicCandidateChatMessage,
  TopicRadar,
  TopicTaskPromotionDraft,
  TopicsSettings,
} from '../../../shared/types';
import {
  askCandidateQuestion,
  deleteCandidate,
  getEventLines,
  getMentionCandidates,
  promoteCandidateTasks,
  refreshIntelligenceProfile,
  runDueIntelligenceProfiles,
  trialRunIntelligenceProfile,
  updateIntelligenceProfile,
} from '../../lib/api';

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

type AdvisorJudgmentCardModel = {
  id: string;
  candidate?: TopicCandidate;
  isExample: boolean;
  conclusion: string;
  relatedObject: string;
  intelligenceBrief: string[];
  intelligenceAssessment: string[];
  assessmentContext: string[];
  actionAdvice: string[];
  evidenceStatus: string;
  evidenceChain: string[];
  recommendationBasis: string[];
  groundingFacts: string[];
  sourceSummary: string;
};

type AdvisorBriefingViewModel = {
  title: string;
  basis: string;
  backgroundNotes: string[];
  judgments: AdvisorJudgmentCardModel[];
  isExampleOnly: boolean;
};

type AdvisorProfileDraft = {
  summary: string;
  focusText: string;
  excludeText: string;
  urlsText: string;
  profileRefreshEnabled: boolean;
  profileRefreshFrequency: IntelligenceProfile['adminProfileRefreshFrequency'];
  pushEnabled: boolean;
  pushFrequency: IntelligenceProfile['adminPushFrequency'];
};

type TaskPromotionDialogState = {
  ownerId: string;
  ownerName: string;
  collaboratorIds: string[];
  dueDate: string;
  eventLineId: string;
};

const DEFAULT_ADVISOR_QUESTIONS = [
  '这个机会适合我们现在跟进吗？',
  '如果转任务，负责人应先判断什么？',
  '它和当前客户或项目画像的关系是什么？',
];

function formatBriefingDate(value?: string | null) {
  if (!value) return '';
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

function cleanObjectTitle(value?: string | null) {
  const cleaned = String(value || '')
    .replace(/自动情报画像|自定义情报画像|情报画像|自动画像|自定义画像|画像|雷达/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  return cleaned;
}

function profileTitle(profile?: Pick<IntelligenceProfile, 'title' | 'radarTitle'> | null) {
  return cleanObjectTitle(profile?.title || profile?.radarTitle);
}

function profileScopeLabel(profile: IntelligenceProfile) {
  if (profile.profileKind === 'custom') return '自定义画像';
  if (profile.scopeType === 'client') return '客户画像';
  if (profile.scopeType === 'project_module') return '项目画像';
  return '组织画像';
}

function frequencyLabel(value?: string | null) {
  if (value === 'daily') return '每日';
  if (value === 'weekly') return '每周';
  if (value === 'workday') return '工作日';
  return '手动';
}

function profileStatusLabel(profile: IntelligenceProfile) {
  if (profile.profileReadiness === 'waiting_material') return '等待补充资料';
  if (profile.status === 'ready') return '画像可用';
  if (profile.status === 'fallback') return '规则画像可用';
  if (profile.status === 'failed') return '生成失败';
  return '待生成';
}

function profileMaterialLine(profile: IntelligenceProfile) {
  if (profile.materialSummary.length) return profile.materialSummary.join('、');
  return profile.profileReadiness === 'waiting_material'
    ? '目前只有名称或基础占位，补充资料后再生成画像。'
    : '已有可用于画像的资料。';
}

function profileWorkContextLine(profile: IntelligenceProfile) {
  const parts = [
    ...profile.workContext,
    ...profile.targetBeneficiaries.map((item) => `服务对象：${item}`),
    ...profile.regions.map((item) => `地域：${item}`),
    ...profile.opportunityTypes.map((item) => `关注：${item}`),
  ].filter(Boolean);
  return parts.length ? parts.slice(0, 5).join(' / ') : profile.effectiveSummary || profile.summary || profileMaterialLine(profile);
}

function profileNeedLine(profile: IntelligenceProfile) {
  return profile.priorityNeeds.length ? profile.priorityNeeds.slice(0, 4).join('、') : '等待系统从工作台资料和管理员补充中识别当前需求。';
}

function profileGapLine(profile: IntelligenceProfile) {
  return profile.materialGaps.length ? profile.materialGaps.slice(0, 4).join('、') : '当前没有明显资料缺口。';
}

function profilePillKeywords(profile: IntelligenceProfile) {
  return [
    ...profile.targetBeneficiaries,
    ...profile.regions,
    ...profile.opportunityTypes,
    ...profile.priorityNeeds,
    ...profile.workContext,
  ]
    .map((item) => item.replace(/^服务对象：|^地域：|^关注：/, '').trim())
    .filter(Boolean)
    .slice(0, 3);
}

function profilePillStatus(profile: IntelligenceProfile) {
  if (profile.lastFetch?.failureReason) return '最近无精选';
  if (profile.backgroundEnrichments.length > 0) return `补资料 ${profile.backgroundEnrichments.length}`;
  return profileStatusLabel(profile);
}

function profileDraftFromProfile(profile: IntelligenceProfile): AdvisorProfileDraft {
  return {
    summary: profile.adminSummaryOverride || profile.effectiveSummary || profile.summary,
    focusText: profile.adminFocus.join('\n'),
    excludeText: profile.adminExcludeTerms.join('\n'),
    urlsText: profile.adminPriorityUrls.join('\n'),
    profileRefreshEnabled: profile.adminProfileRefreshEnabled,
    profileRefreshFrequency: profile.adminProfileRefreshFrequency || 'manual',
    pushEnabled: profile.adminPushEnabled,
    pushFrequency: profile.adminPushFrequency || 'manual',
  };
}

function splitDraftLines(value: string) {
  return value
    .split(/\n|，|,|；|;/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function splitRadarLineValues(value: string) {
  return value
    .split(/；|;|，|,|\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function radarPromptLine(prompt: string, label: string) {
  const pattern = new RegExp(`${label}[：:]\\s*([^\\n]*)`);
  return prompt.match(pattern)?.[1]?.trim() || '';
}

function looksLikeRegion(value: string) {
  return /(广东|广州|深圳|佛山|珠海|东莞|中山|惠州|汕头|全国|省|市|区|县)$/.test(value);
}

function buildFallbackProfilesFromRadars(radars: TopicRadar[]): IntelligenceProfile[] {
  return radars
    .filter((radar) => /画像/.test(radar.title) || /工作语境|当前需求|服务对象\/地域|系统会围绕/.test(radar.prompt))
    .map((radar) => {
      const workContext = splitRadarLineValues(radarPromptLine(radar.prompt, '工作语境'));
      const priorityNeeds = splitRadarLineValues(radarPromptLine(radar.prompt, '当前需求'));
      const serviceAndRegions = splitRadarLineValues(radarPromptLine(radar.prompt, '服务对象/地域'));
      const regions = serviceAndRegions.filter(looksLikeRegion);
      const targetBeneficiaries = serviceAndRegions.filter((item) => !looksLikeRegion(item));
      const opportunityTypes = priorityNeeds
        .map((item) => item.replace(/^寻找/, '').replace(/^补全/, '公开资料补全').trim())
        .filter(Boolean);
      const firstLine = radar.prompt.split('\n').map((line) => line.trim()).find(Boolean) || radar.title;
      const title = cleanObjectTitle(radar.title) || radar.title;
      const isOrganization = /组织/.test(radar.title);
      return {
        id: `profile_${radar.id}`,
        title,
        radarId: radar.id,
        radarTitle: radar.title,
        profileKind: /自定义/.test(radar.title) ? 'custom' : 'auto',
        scopeType: isOrganization ? 'organization' : 'client',
        scopeId: isOrganization ? 'organization' : radar.id,
        clientId: isOrganization ? null : radar.id,
        projectModuleId: null,
        status: workContext.length || priorityNeeds.length || targetBeneficiaries.length ? 'ready' : 'pending',
        profileReadiness: workContext.length || priorityNeeds.length || targetBeneficiaries.length ? 'ready' : 'waiting_material',
        summary: firstLine,
        effectiveSummary: firstLine,
        adminSummaryOverride: null,
        adminFocus: [],
        adminExcludeTerms: splitRadarLineValues(radarPromptLine(radar.prompt, '排除方向')),
        adminPriorityUrls: radar.preferredSources.map((source) => source.url).filter(Boolean),
        adminProfileRefreshEnabled: false,
        adminProfileRefreshFrequency: 'weekly',
        adminPushEnabled: false,
        adminPushFrequency: 'weekly',
        materialSummary: workContext.length || priorityNeeds.length ? ['已从本地画像记录恢复资料线索'] : [],
        workContext,
        priorityNeeds,
        targetBeneficiaries,
        regions,
        opportunityTypes,
        materialGaps: [],
        groundingFacts: [],
        backgroundEnrichments: [],
        lastFetch: null,
        deletedAt: null,
        createdAt: radar.createdAt,
        updatedAt: radar.createdAt,
      } satisfies IntelligenceProfile;
    });
}

function scopeName(candidate?: TopicCandidate) {
  if (!candidate?.scopeType) return '组织层面';
  if (candidate.scopeType === 'organization') return '组织层面';
  if (candidate.scopeType === 'client') return '客户相关';
  if (candidate.scopeType === 'project_module') return '项目相关';
  return '待绑定对象';
}

function relatedObjectName(
  candidate: TopicCandidate,
  profileByRadarId: Map<string, IntelligenceProfile>,
  radarById: Map<string, TopicRadar>,
) {
  const profile = profileByRadarId.get(candidate.radarId);
  const profileName = profileTitle(profile);
  if (profileName) return `${scopeName(candidate)}：${profileName}`;
  const radarName = cleanObjectTitle(radarById.get(candidate.radarId)?.title);
  if (radarName) return `${scopeName(candidate)}：${radarName}`;
  return scopeName(candidate);
}

function evidenceStatusText(candidate?: TopicCandidate) {
  if (!candidate) return '示例 / 未核验';
  if (candidate.evidenceStatus === 'accepted') return '已核验';
  if (candidate.evidenceStatus === 'rejected') return '已排除';
  if (candidate.evidenceStatus === 'candidate') return '未核验';
  if (candidate.sourceUrl) return '公开来源 / 待核验';
  return '来源待核验';
}

function isExampleCandidate(candidate: TopicCandidate) {
  const text = [candidate.title, candidate.summary, candidate.source].join(' ');
  return /示例|样例|mock|demo/i.test(text);
}

function candidateSortTime(candidate: TopicCandidate) {
  const time = new Date(candidate.publishedAt || candidate.createdAt).getTime();
  return Number.isNaN(time) ? 0 : time;
}

function candidatePriority(candidate: TopicCandidate) {
  if (candidate.primaryBadge === 'follow_up') return 40;
  if (candidate.primaryBadge === 'focus') return 26;
  if (candidate.whyRecommended?.trim() && candidate.suggestedAction?.trim()) return 22;
  if (candidate.relevanceReason?.trim()) return 16;
  if (candidate.sourceUrl) return 8;
  return 0;
}

function hasEnoughJudgmentValue(candidate: TopicCandidate) {
  if (isExampleCandidate(candidate)) return false;
  if (candidate.contentKind && candidate.contentKind !== 'advisor_intelligence') return false;
  const deep = candidate.deepAnalysis && typeof candidate.deepAnalysis === 'object' && !Array.isArray(candidate.deepAnalysis)
    ? candidate.deepAnalysis as Record<string, unknown>
    : {};
  const hasDeepMemo = ['advisorMemo', 'intelligenceBrief', 'advisorAssessment', 'actionPlan', 'coreInfo', 'opportunityOrRisk']
    .some((key) => valueAsText(deep[key]) || valueAsLines(deep[key], 1).length > 0);
  return Boolean(
    candidate.primaryBadge ||
    candidate.status === 'promoted' ||
    hasDeepMemo ||
    (candidate.recommendationBasis && candidate.recommendationBasis.length > 0) ||
    candidate.whyRecommended?.trim() ||
    candidate.relevanceReason?.trim() ||
    candidate.suggestedAction?.trim(),
  );
}

function buildEvidenceChain(candidate: TopicCandidate) {
  const chain = [
    candidate.source ? `来源：${candidate.source}` : '',
    candidate.publishedAt ? `时间：${formatBriefingDate(candidate.publishedAt)}` : '',
    candidate.sourceUrl ? '可打开外部来源复核' : '暂无外部来源链接',
    evidenceStatusText(candidate),
  ].filter(Boolean);
  return chain.length ? chain : ['公开来源待补充', evidenceStatusText(candidate)];
}

function cleanAdvisorHeadline(value: string) {
  const stripped = value
    .replace(/^【[^】]*】/, '')
    .replace(/^(顾问判断|情报研判|行动建议|建议|判断|结论)[：:]/, '')
    .replace(/^[\s:：-]+/, '')
    .trim();
  const advisorySplit = stripped.split(/[。！？!?；;，,]/).find((part) => {
    const text = part.trim();
    return text && !/(建议|判断|值得|需要|可以|是否|转给|推进|跟进|适合|优先|应先|下一步|马上|现在该)/.test(text);
  });
  const headline = advisorySplit?.trim() || stripped;
  return headline.length > 42 ? `${headline.slice(0, 40)}…` : headline;
}

function cleanMemoLinePrefix(line: string) {
  return line
    .replace(/^(情报速览|情报研判|行动建议|推荐依据|底座事实|已知限制)[：:]\s*/, '')
    .replace(/^[-*]\s*/, '')
    .trim();
}

function splitAdvisorLines(value: string, fallback: string, limit = 4) {
  const lines = value
    .split(/\n|；|;/)
    .map((item) => cleanMemoLinePrefix(item))
    .filter(Boolean);
  const fallbackLine = cleanMemoLinePrefix(fallback);
  return (lines.length ? lines : (fallbackLine ? [fallbackLine] : [])).slice(0, limit);
}

function valueAsText(value: unknown) {
  if (typeof value === 'string') return value.trim();
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const record = value as Record<string, unknown>;
    return valueAsText(record.text || record.content || record.title || record.summary || record.description);
  }
  return '';
}

function valueAsLines(value: unknown, limit = 5) {
  if (Array.isArray(value)) {
    return value.map(valueAsText).filter(Boolean).slice(0, limit);
  }
  const text = valueAsText(value);
  if (!text) return [];
  return splitAdvisorLines(text, '', limit);
}

function deepAnalysisValue(candidate: TopicCandidate, key: string) {
  const analysis = candidate.deepAnalysis;
  if (!analysis || typeof analysis !== 'object' || Array.isArray(analysis)) return undefined;
  return (analysis as Record<string, unknown>)[key];
}

function deepAnalysisText(candidate: TopicCandidate, key: string) {
  return valueAsText(deepAnalysisValue(candidate, key));
}

function deepAnalysisLines(candidate: TopicCandidate, key: string, limit = 5) {
  return valueAsLines(deepAnalysisValue(candidate, key), limit);
}

function parseAdvisorMemoSections(text: string) {
  const raw = text.trim();
  const sections: Record<'brief' | 'assessment' | 'action', string> = {
    brief: '',
    assessment: '',
    action: '',
  };
  const labelToKey: Record<string, keyof typeof sections> = {
    情报速览: 'brief',
    情报研判: 'assessment',
    行动建议: 'action',
  };
  const buckets: Record<keyof typeof sections, string[]> = {
    brief: [],
    assessment: [],
    action: [],
  };
  let currentKey: keyof typeof sections | null = null;
  raw.split(/\r?\n/).forEach((line) => {
    const trimmed = line.trim();
    const bracketLabel = trimmed.match(/^【(情报速览|情报研判|行动建议)】\s*(.*)$/);
    const plainLabel = trimmed.match(/^(情报速览|情报研判|行动建议)[：:]\s*(.*)$/);
    const standaloneLabel = trimmed.match(/^(情报速览|情报研判|行动建议)$/);
    const label = bracketLabel?.[1] || plainLabel?.[1] || standaloneLabel?.[1];
    if (label) {
      currentKey = labelToKey[label];
      const rest = bracketLabel?.[2] || plainLabel?.[2] || '';
      if (rest.trim()) buckets[currentKey].push(rest.trim());
      return;
    }
    if (currentKey && trimmed) {
      buckets[currentKey].push(trimmed);
    }
  });
  sections.brief = buckets.brief.join('\n').trim();
  sections.assessment = buckets.assessment.join('\n').trim();
  sections.action = buckets.action.join('\n').trim();
  return sections;
}

function advisorMemoLooksInvalid(text: string) {
  const memo = text.trim();
  if (!memo) return false;
  if (memo.length < 450) return true;
  if (!['情报速览', '情报研判', '行动建议'].every((label) => memo.includes(label))) return true;
  return /一、这篇内容主要讲什么|文章里最值得抓住的观点|它对团队的实际价值|大模型安全|风险治理前置|coreInfo|opportunityOrRisk/.test(memo);
}

function memoSectionLines(text: string, fallback: string[] = [], limit?: number) {
  const raw = text.trim();
  if (!raw) return limit ? fallback.slice(0, limit) : fallback;
  const lines = raw
    .split(/\n+/)
    .map((line) => cleanMemoLinePrefix(line.trim()))
    .filter(Boolean);
  const result = lines.length ? lines : [cleanMemoLinePrefix(raw)].filter(Boolean);
  return typeof limit === 'number' ? result.slice(0, limit) : result;
}

function advisorMemoSections(candidate: TopicCandidate) {
  const memo = deepAnalysisText(candidate, 'advisorMemo');
  return memo && !advisorMemoLooksInvalid(memo) ? parseAdvisorMemoSections(memo) : { brief: '', assessment: '', action: '' };
}

function advisorMaterialLooksInternal(value: string) {
  const text = value.trim();
  if (!text) return true;
  return /命中客户画像|命中地域|命中机会类型|推荐依据|底座事实|这条公开信息与|可能相关|值得关注|建议关注|建议跟进/.test(text);
}

function candidateLooksLikeFundingWindow(candidate: TopicCandidate) {
  const text = `${candidate.title || ''} ${candidate.summary || ''} ${candidate.source || ''}`;
  return /资助|公益创投|申报|征集|招募|资金|基金|合作|政策窗口|青少年心理健康|平台搭建/.test(text);
}

function buildIntelligenceBrief(candidate: TopicCandidate) {
  const memo = advisorMemoSections(candidate);
  if (memo.brief) return memoSectionLines(memo.brief);
  const fromDeepAnalysis = deepAnalysisText(candidate, 'intelligenceBrief');
  if (fromDeepAnalysis) return memoSectionLines(fromDeepAnalysis);
  return splitAdvisorLines(candidate.summary?.trim() || candidate.title, candidate.title, 4);
}

function buildIntelligenceAssessment(candidate: TopicCandidate, recommendationBasis: string[], relationReason: string) {
  const memo = advisorMemoSections(candidate);
  if (memo.assessment) return memoSectionLines(memo.assessment);
  const fromDeepAnalysis = deepAnalysisText(candidate, 'advisorAssessment');
  const preferred = fromDeepAnalysis || candidate.whyRecommended?.trim() || candidate.relevanceReason?.trim() || relationReason;
  const shouldIgnorePreferred = advisorMaterialLooksInternal(preferred);
  const fallback = candidateLooksLikeFundingWindow(candidate)
    ? [
        '结论：这条线索的价值不在“又出现一个公益创投”，而在它可能提供一个把当前项目能力转成本地联合方案的入口。',
        '如果通知允许外地机构联合参与，可以把当前项目的课程、督导和学校/社区服务经验包装成合作能力；如果限定本地注册或执行主体，就应先寻找本地伙伴。',
        '真正需要判断的是主体条件、联合规则、截止时间和材料成本是否过线；这些硬条件过不了，线索就应降级为合作观察，而不是推进申报。',
      ]
    : [
        '结论：这条线索暂时只能进入初筛，不能直接判断为高价值机会。',
        '它需要先和客户/项目的真实近况对上：服务对象、地域、当前任务、可投入资源和已有合作基础至少要有一项能形成明确连接。',
        '如果只能停留在主题相似，就应作为弱线索留存；只有能落到机会、风险、合作或政策窗口，才值得进入行动层。',
      ];
  if (shouldIgnorePreferred) return fallback;
  const lines = splitAdvisorLines(
    preferred,
    '结论：这条线索具备一定行动价值，但仍需要结合来源原文与当前项目条件判断。',
    5,
  );
  if (lines.some((line) => advisorMaterialLooksInternal(line))) return fallback;
  if (lines.some((line) => line.startsWith('结论：'))) return lines;
  return lines.map((line, index) => (index === 0 ? `结论：${line}` : line));
}

function buildAssessmentContext(candidate: TopicCandidate, recommendationBasis: string[]) {
  const groundingFacts = deepAnalysisLines(candidate, 'groundingFacts', 3);
  const knownLimitations = deepAnalysisLines(candidate, 'knownLimitations', 2);
  const compact = [
    ...groundingFacts,
    ...recommendationBasis.slice(0, Math.max(0, 3 - groundingFacts.length)),
    ...knownLimitations,
  ].filter((line) => line && !advisorMaterialLooksInternal(line));
  return compact.slice(0, 2);
}

function buildActionAdvice(candidate: TopicCandidate) {
  const memo = advisorMemoSections(candidate);
  if (memo.action) return memoSectionLines(memo.action);
  const actionPlan = deepAnalysisLines(candidate, 'actionPlan', 5);
  const fallback = candidateLooksLikeFundingWindow(candidate)
    ? [
        '结论：先做 30 分钟硬条件初筛，再决定是否转为正式推进任务。',
        '第一步：核对正式通知里的申报主体、地域限制、截止时间、资助额度分档、是否允许联合申报。',
        '第二步：准备一页初筛材料：当前项目可贡献的服务对象、课程/督导能力、过往服务证据、预算框架和潜在本地伙伴。',
        '第三步：条件过线后再交给战略陪伴负责人判断申报路径；主体或地域不匹配时，转为寻找合作方的线索。',
      ]
    : [
        '结论：先核验事实和匹配条件，再决定是否转任务。',
        '第一步：确认来源、主体、时间和对象关系是否可靠。',
        '第二步：把可行动信息与背景资料分开，避免让宽泛线索占用团队精力。',
        '第三步：只有能落到具体机会、风险或合作窗口时，再交给负责人处理。',
      ];
  if (
    actionPlan.length >= 3
    && !actionPlan.some((line) => advisorMaterialLooksInternal(line))
    && !actionPlan.join(' ').includes('先核对正式申报通知')
  ) return actionPlan;
  if (actionPlan.length) return fallback;
  return splitAdvisorLines(
    candidate.suggestedAction?.trim() || '',
    '结论：先做可行性判断，再决定是否转成完整申报或合作推进任务。',
    4,
  ).map((line, index) => {
    if (advisorMaterialLooksInternal(line)) return fallback[index] || line;
    if (index === 0 && !line.startsWith('结论：')) return `结论：${line}`;
    return line;
  });
}

function buildJudgmentModel(
  candidate: TopicCandidate,
  profileByRadarId: Map<string, IntelligenceProfile>,
  radarById: Map<string, TopicRadar>,
): AdvisorJudgmentCardModel {
  const relatedObject = relatedObjectName(candidate, profileByRadarId, radarById);
  const relationReason =
    (candidate.recommendationBasis && candidate.recommendationBasis.length > 0 ? candidate.recommendationBasis.join('；') : '') ||
    candidate.whyRecommended?.trim() ||
    candidate.relevanceReason?.trim() ||
    `这条公开信息与「${relatedObjectName(candidate, profileByRadarId, radarById)}」相关，需要结合当前业务背景判断行动价值。`;
  const sourceSummary = candidate.summary?.trim() || candidate.title;
  const recommendationBasis = candidate.recommendationBasis && candidate.recommendationBasis.length > 0
    ? candidate.recommendationBasis
    : [relationReason].filter(Boolean);
  return {
	    id: candidate.id,
	    candidate,
	    isExample: false,
	    conclusion: cleanAdvisorHeadline(candidate.title || sourceSummary),
	    relatedObject,
	    intelligenceBrief: buildIntelligenceBrief(candidate),
	    intelligenceAssessment: buildIntelligenceAssessment(candidate, recommendationBasis, relationReason),
	    assessmentContext: buildAssessmentContext(candidate, recommendationBasis),
	    actionAdvice: buildActionAdvice(candidate),
	    evidenceStatus: evidenceStatusText(candidate),
	    evidenceChain: buildEvidenceChain(candidate),
	    groundingFacts: deepAnalysisLines(candidate, 'groundingFacts', 8).length
	      ? deepAnalysisLines(candidate, 'groundingFacts', 8)
	      : (candidate.groundingFactRefs?.length ? candidate.groundingFactRefs : recommendationBasis.slice(0, 3)),
	    sourceSummary,
	    recommendationBasis,
		  };
}

function renderInlineStrong(text: string, keyPrefix: string) {
  const parts = text.split(/(\*\*[^*]+\*\*)/g).filter((part) => part.length > 0);
  return parts.map((part, index) => {
    const strong = part.match(/^\*\*([^*]+)\*\*$/);
    if (strong) {
      return (
        <span key={`${keyPrefix}-strong-${index}`} className="font-black text-gray-950">
          {strong[1]}
        </span>
      );
    }
    return <React.Fragment key={`${keyPrefix}-text-${index}`}>{part}</React.Fragment>;
  });
}

function AdvisorMemoBlock({
  title,
  lines,
  className,
}: {
  title: string;
  lines: string[];
  className: string;
}) {
  return (
    <section className={`rounded-lg border px-4 py-4 ${className}`}>
      <p className="text-[12px] font-black tracking-[0.14em] text-gray-500">{title}</p>
      <div className="mt-3 space-y-2 text-[13px] leading-6 text-gray-700">
        {lines.map((line, index) => {
          const normalized = line.trim();
          const markdownLabel = normalized.match(/^\*\*([^*]{1,12}[：:])\*\*\s*(.*)$/);
          const plainLabel = normalized.match(/^(结论|机会|风险|成本|边界|先核验|先找人|先备料|止损条件|仍需核验|第一步|第二步|第三步)[：:]\s*(.*)$/);
          const label = markdownLabel?.[1] || (plainLabel ? `${plainLabel[1]}：` : '');
          const body = markdownLabel?.[2] ?? plainLabel?.[2] ?? normalized;
          const contentKey = `${title}-${index}`;
          return (
            <p key={contentKey}>
              {label ? (
                <>
                  <span className="font-black text-gray-950">{label}</span>
                  <span className="font-semibold text-gray-800">{renderInlineStrong(body, contentKey)}</span>
                </>
              ) : (
                <span>{renderInlineStrong(normalized, contentKey)}</span>
              )}
            </p>
          );
		        })}
		      </div>
		    </section>
		  );
}

function buildBackgroundNotes(profiles: IntelligenceProfile[]) {
  const enrichmentNotes = profiles
    .flatMap((profile) => profile.backgroundEnrichments.map((item) => `已补入「${profileTitle(profile) || profile.title || '相关对象'}」公开资料：${item.title}。`))
    .slice(0, 4);
  if (enrichmentNotes.length) return enrichmentNotes;
  const notes = profiles
    .filter((profile) => !profile.deletedAt)
    .slice(0, 3)
    .map((profile) => {
      const name = profileTitle(profile) || '未命名对象';
      const workLine = profileWorkContextLine(profile);
      if (profile.scopeType === 'client') return `正在形成「${name}」的客户语境：${workLine}。`;
      if (profile.scopeType === 'project_module') return `正在形成「${name}」的项目语境：${workLine}。`;
      return `正在形成「${name}」的组织语境：${workLine}。`;
    });
  return notes.length
    ? notes
    : [
        '当前还没有可交代的真实补全记录；后续新建客户或项目后，系统会在这里说明本轮补了哪些公开背景。',
      ];
}

function buildBriefingViewModel(
  candidates: TopicCandidate[],
  profiles: IntelligenceProfile[],
  profileByRadarId: Map<string, IntelligenceProfile>,
  radarById: Map<string, TopicRadar>,
): AdvisorBriefingViewModel {
  const judgments = candidates
    .filter((candidate) => candidate.status !== 'archived' && hasEnoughJudgmentValue(candidate))
    .sort((left, right) => {
      const priorityDelta = candidatePriority(right) - candidatePriority(left);
      return priorityDelta || candidateSortTime(right) - candidateSortTime(left);
    })
    .slice(0, 3)
    .map((candidate) => buildJudgmentModel(candidate, profileByRadarId, radarById));

  const isExampleOnly = judgments.length === 0;
  return {
    title: '外部情报顾问简报',
    basis: profiles.length
      ? `本轮已结合 ${profiles.filter((profile) => !profile.deletedAt).length || profiles.length} 个组织、客户或项目背景，以及近期公开线索生成判断。未核验内容会明确标注。`
      : '本轮会结合组织、客户、项目和近期公开信息生成判断；当前还缺少足够的真实背景与公开线索。',
    backgroundNotes: buildBackgroundNotes(profiles),
    judgments,
    isExampleOnly,
  };
}

export function TopicsManagementView({
  radars,
  intelligenceProfiles = [],
  candidates,
  tasks,
  activeTaskLists,
  effectiveTaskSettings,
  currentSessionUser,
  currentOperatorName,
  focusCandidateId,
  onFocusCandidateHandled,
  flash,
  onTopicsReload,
  onTasksReload,
}: TopicsManagementViewProps) {
  const [backgroundOpen, setBackgroundOpen] = useState(false);
  const [taskPendingId, setTaskPendingId] = useState<string | null>(null);
  const [taskDialogItem, setTaskDialogItem] = useState<AdvisorJudgmentCardModel | null>(null);
  const [taskDialogDraft, setTaskDialogDraft] = useState<TaskPromotionDialogState | null>(null);
  const [questionPanelItemId, setQuestionPanelItemId] = useState('');
  const [questionDraftByCandidateId, setQuestionDraftByCandidateId] = useState<Record<string, string>>({});
  const [questionPendingId, setQuestionPendingId] = useState<string | null>(null);
  const [deletePendingId, setDeletePendingId] = useState<string | null>(null);
  const [chatMessagesByCandidateId, setChatMessagesByCandidateId] = useState<Record<string, TopicCandidateChatMessage[]>>({});
  const [highlightedCandidateId, setHighlightedCandidateId] = useState('');
  const [peopleOptions, setPeopleOptions] = useState<MentionCandidate[]>([]);
  const [eventLineOptions, setEventLineOptions] = useState<EventLine[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState<string | null>(null);
  const [profileDraft, setProfileDraft] = useState<AdvisorProfileDraft | null>(null);
  const [profilePendingId, setProfilePendingId] = useState<string | null>(null);
  const [automationChecked, setAutomationChecked] = useState(false);

  const currentViewerId = currentSessionUser?.id || 'local-device-user';
  const isAdmin = currentSessionUser?.primaryRole === 'admin';
  const defaultListId = effectiveTaskSettings.defaultListId || activeTaskLists[0]?.id || 'list-0';
  const currentPerson = useMemo<MentionCandidate>(() => ({
    id: currentSessionUser?.id || 'local-device-user',
    fullName: currentSessionUser?.fullName || currentOperatorName || '当前用户',
    email: currentSessionUser?.email || '',
    primaryRole: currentSessionUser?.primaryRole === 'admin' ? 'admin' : 'employee',
    isSelf: true,
  }), [currentOperatorName, currentSessionUser]);
  const memberOptions = useMemo(() => {
    const merged = new Map<string, MentionCandidate>();
    [currentPerson, ...peopleOptions].forEach((item) => {
      if (!item.id) return;
      merged.set(item.id, item);
    });
    return Array.from(merged.values());
  }, [currentPerson, peopleOptions]);
  const activeEventLines = useMemo(
    () => eventLineOptions.filter((item) => item.status !== 'archived'),
    [eventLineOptions],
  );
  const radarById = useMemo(() => new Map(radars.map((radar) => [radar.id, radar])), [radars]);
  const effectiveProfiles = useMemo(
    () => intelligenceProfiles.length > 0 ? intelligenceProfiles : buildFallbackProfilesFromRadars(radars),
    [intelligenceProfiles, radars],
  );
  const profileByRadarId = useMemo(() => {
    const next = new Map<string, IntelligenceProfile>();
    effectiveProfiles.forEach((profile) => {
      if (profile.radarId) next.set(profile.radarId, profile);
    });
    return next;
  }, [effectiveProfiles]);

  const viewModel = useMemo(
    () => buildBriefingViewModel(candidates, effectiveProfiles, profileByRadarId, radarById),
    [candidates, effectiveProfiles, profileByRadarId, radarById],
  );
  const visibleProfiles = useMemo(
    () => effectiveProfiles.filter((profile) => !profile.deletedAt),
    [effectiveProfiles],
  );
  const selectedProfile = useMemo(
    () => effectiveProfiles.find((profile) => profile.id === selectedProfileId) || null,
    [effectiveProfiles, selectedProfileId],
  );
  const questionDialogItem = useMemo(
    () => viewModel.judgments.find((item) => item.id === questionPanelItemId) || null,
    [questionPanelItemId, viewModel.judgments],
  );
  const questionDialogCandidate = questionDialogItem?.candidate || null;
  const questionDialogMessages = questionDialogCandidate ? (chatMessagesByCandidateId[questionDialogCandidate.id] || []) : [];
  const questionDialogDraft = questionDialogCandidate ? (questionDraftByCandidateId[questionDialogCandidate.id] || '') : '';

  const relatedTasksByCandidate = useMemo(() => {
    const grouped = new Map<string, Task[]>();
    tasks.forEach((task) => {
      if (task.sourceType !== 'topic_candidate' || !task.sourceId) return;
      grouped.set(task.sourceId, [...(grouped.get(task.sourceId) || []), task]);
    });
    return grouped;
  }, [tasks]);

  useEffect(() => {
    if (!focusCandidateId) return;
    const exists = viewModel.judgments.some((item) => item.candidate?.id === focusCandidateId);
    if (exists) {
      setHighlightedCandidateId(focusCandidateId);
      window.setTimeout(() => {
        document.getElementById(`topic-candidate-${focusCandidateId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 80);
      window.setTimeout(() => {
        setHighlightedCandidateId((current) => (current === focusCandidateId ? '' : current));
      }, 3600);
    } else {
      flash('info', '这条情报当前没有进入精选列表，可能已删除、归档或未达到推荐门槛。');
    }
    onFocusCandidateHandled?.();
  }, [flash, focusCandidateId, onFocusCandidateHandled, viewModel.judgments]);

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

  useEffect(() => {
    let cancelled = false;
    void getEventLines()
      .then((items) => {
        if (!cancelled) setEventLineOptions(items);
      })
      .catch(() => {
        if (!cancelled) setEventLineOptions([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (automationChecked) return;
    setAutomationChecked(true);
    void runDueIntelligenceProfiles({ limit: 2 })
      .then((result) => {
        if (result.refreshedCount || result.fetchedCount) {
          void onTopicsReload();
        }
      })
      .catch(() => {
        // 自动检查失败不打扰普通用户；画像卡里仍会显示最近失败原因。
      });
  }, [automationChecked, onTopicsReload]);

  const openProfileDetail = (profile: IntelligenceProfile) => {
    setSelectedProfileId(profile.id);
    setProfileDraft(profileDraftFromProfile(profile));
  };

  const closeProfileDetail = () => {
    setSelectedProfileId(null);
    setProfileDraft(null);
  };

  const handleSaveProfile = async () => {
    if (!selectedProfile || !profileDraft || !isAdmin || profilePendingId) return;
    setProfilePendingId(selectedProfile.id);
    try {
      const payload: IntelligenceProfileMutationPayload = {
        title: selectedProfile.title,
        summary: profileDraft.summary,
        focus: splitDraftLines(profileDraft.focusText),
        excludeTerms: splitDraftLines(profileDraft.excludeText),
        priorityUrls: splitDraftLines(profileDraft.urlsText),
        profileRefreshEnabled: profileDraft.profileRefreshEnabled,
        profileRefreshFrequency: profileDraft.profileRefreshFrequency,
        pushEnabled: profileDraft.pushEnabled,
        pushFrequency: profileDraft.pushFrequency,
      };
      await updateIntelligenceProfile(selectedProfile.id, payload);
      await onTopicsReload();
      flash('success', '画像设置已保存');
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '画像保存失败');
    } finally {
      setProfilePendingId(null);
    }
  };

  const handleRefreshProfile = async (profile: IntelligenceProfile) => {
    if (!isAdmin || profilePendingId) return;
    setProfilePendingId(profile.id);
    try {
      await refreshIntelligenceProfile(profile.id, { force: true, allowAi: true, autoTrial: false });
      await onTopicsReload();
      flash('success', '系统理解已刷新');
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '画像刷新失败');
    } finally {
      setProfilePendingId(null);
    }
  };

  const handleTrialRunProfile = async (profile: IntelligenceProfile) => {
    if (!isAdmin || profilePendingId) return;
    setProfilePendingId(profile.id);
	    try {
	      const result = await trialRunIntelligenceProfile(profile.id);
	      await onTopicsReload();
	      const resultMessage = result as typeof result & { failureReason?: string; error?: string };
	      const message = result.createdCount > 0
	        ? `试跑完成：新增 ${result.createdCount} 条情报`
	        : `试跑完成：${resultMessage.failureReason || resultMessage.error || '没有抓到新的候选线索'}`;
      flash(result.createdCount > 0 ? 'success' : 'info', message);
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '画像试跑失败');
    } finally {
      setProfilePendingId(null);
    }
  };

  const openTaskPromotionDialog = (item: AdvisorJudgmentCardModel) => {
    const candidate = item.candidate;
    if (!candidate || item.isExample) {
      flash('info', '示例内容不能转任务');
      return;
    }
    if (candidate.convertedTaskId || (relatedTasksByCandidate.get(candidate.id) || []).length > 0) {
      flash('info', '这条情报已经关联过任务');
      return;
    }
    setTaskDialogItem(item);
    setTaskDialogDraft({
      ownerId: currentPerson.id,
      ownerName: currentPerson.fullName,
      collaboratorIds: [],
      dueDate: '',
      eventLineId: '',
    });
  };

  const handleConfirmPromoteToTask = async () => {
    const item = taskDialogItem;
    const draftState = taskDialogDraft;
    const candidate = item?.candidate;
    if (!item || !draftState || !candidate || item.isExample) return;
    if (taskPendingId) return;
    setTaskPendingId(candidate.id);
    try {
      const actorName = currentSessionUser?.fullName || currentOperatorName || currentViewerId;
      const owner = memberOptions.find((person) => person.id === draftState.ownerId);
      const collaborators = memberOptions.filter((person) => draftState.collaboratorIds.includes(person.id));
      const draft: TopicTaskPromotionDraft = {
        title: `跟进情报：${candidate.title}`,
        desc: `【情报速览】\n${item.intelligenceBrief.join('\n')}\n\n【情报研判】\n${item.intelligenceAssessment.join('\n')}\n\n【行动建议】\n${item.actionAdvice.join('\n')}\n\n【情报原文】\n情报标题：${candidate.title}\n关联对象：${item.relatedObject}\n情报 ID：${candidate.id}\n查看方式：在任务卡片中点击“查看情报原文”，会回到资讯情报站里的这条情报。`,
        priority: 'normal',
        listId: defaultListId,
        dueDate: draftState.dueDate || null,
        ddl: draftState.dueDate || '待确认',
        eventLineId: draftState.eventLineId || null,
        ownerId: draftState.ownerId || null,
        ownerName: draftState.ownerName || owner?.fullName || actorName,
        collaboratorIds: collaborators.map((person) => person.id),
        tagIds: [],
        tags: ['情报跟进'],
        note: `【依据来源】\n${item.evidenceChain.join('\n')}\n\n【底座事实】\n${item.groundingFacts.join('\n')}`,
        ownerRecipient: owner ? { userId: owner.id, fullName: owner.fullName, email: owner.email } : null,
        collaboratorRecipients: collaborators.map((person) => ({ userId: person.id, fullName: person.fullName, email: person.email })),
        actorId: currentViewerId,
        actorName,
        autoShare: true,
      };
      const result = await promoteCandidateTasks(candidate.id, [draft]);
      await onTasksReload();
      await onTopicsReload();
      const resultText = [
        `已转成任务`,
        ...(result.flowbackResults || []),
        ...(result.warnings || []),
      ].filter(Boolean).join('；');
      flash('success', resultText);
      setTaskDialogItem(null);
      setTaskDialogDraft(null);
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '转任务失败');
    } finally {
      setTaskPendingId(null);
    }
  };

  const handleAskQuestion = async (item: AdvisorJudgmentCardModel, question: string) => {
    const candidate = item.candidate;
    if (!candidate || item.isExample) {
      flash('info', '示例内容不能追问');
      return;
    }
    const normalizedQuestion = question.trim();
    if (!normalizedQuestion) return;
    if (questionPendingId) return;
    const history = chatMessagesByCandidateId[candidate.id] || [];
    const userMessage: TopicCandidateChatMessage = {
      role: 'user',
      content: normalizedQuestion,
      createdAt: new Date().toISOString(),
    };
    setChatMessagesByCandidateId((prev) => ({
      ...prev,
      [candidate.id]: [...(prev[candidate.id] || []), userMessage],
    }));
    setQuestionPendingId(candidate.id);
    try {
      const result = await askCandidateQuestion(candidate.id, {
        question: normalizedQuestion,
        history,
      });
      setChatMessagesByCandidateId((prev) => ({
        ...prev,
        [candidate.id]: [...(prev[candidate.id] || []), result.message],
      }));
      setQuestionDraftByCandidateId((prev) => ({ ...prev, [candidate.id]: '' }));
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '追问失败');
    } finally {
      setQuestionPendingId(null);
    }
  };

  const handleDeleteJudgment = async (item: AdvisorJudgmentCardModel) => {
    const candidate = item.candidate;
    if (!isAdmin || !candidate || item.isExample || deletePendingId) return;
    const confirmed = window.confirm('确认删除这条情报吗？删除后它会从情报站隐藏；如果同一来源再次被抓取，系统也会尽量避免重复推荐。');
    if (!confirmed) return;
    setDeletePendingId(candidate.id);
    try {
      await deleteCandidate(candidate.id);
      await onTopicsReload();
      if (questionPanelItemId === item.id) setQuestionPanelItemId('');
      flash('success', '情报已删除');
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '删除情报失败');
    } finally {
      setDeletePendingId(null);
    }
  };

  return (
    <div className="h-full overflow-y-auto bg-[#F8FAFC] font-sans text-gray-900">
      <main className="mx-auto max-w-[1040px] px-6 py-8 lg:px-10">
        <header className="rounded-lg border border-gray-200 bg-white px-7 py-6 shadow-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-gray-950 px-3 py-1 text-[12px] font-bold text-white">{viewModel.title}</span>
            {viewModel.isExampleOnly && <span className="rounded-full bg-gray-100 px-3 py-1 text-[12px] font-bold text-gray-600">暂无真实判断</span>}
          </div>
          <p className="mt-4 max-w-[820px] text-[15px] leading-8 text-gray-700">{viewModel.basis}</p>

          <section className="mt-5 rounded-md border border-gray-100 bg-gray-50">
            <button
              type="button"
              onClick={() => setBackgroundOpen((current) => !current)}
              className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
            >
              <div>
                <p className="text-[12px] font-bold uppercase tracking-[0.18em] text-gray-400">本轮后台补全</p>
                <p className="mt-1 text-[13px] text-gray-600">
                  {viewModel.backgroundNotes[0]}
                </p>
              </div>
              {backgroundOpen ? <ChevronUp size={16} className="shrink-0 text-gray-400" /> : <ChevronDown size={16} className="shrink-0 text-gray-400" />}
            </button>
            {backgroundOpen && (
              <ul className="border-t border-gray-100 px-4 py-3 text-[13px] leading-6 text-gray-600">
                {viewModel.backgroundNotes.map((note) => (
                  <li key={note}>- {note}</li>
                ))}
              </ul>
            )}
          </section>

	          {visibleProfiles.length > 0 && (
            <section className="mt-4">
              <div className="mb-2 flex items-center justify-between gap-3">
                <p className="text-[12px] font-bold uppercase tracking-[0.18em] text-gray-400">顾问正在理解的对象</p>
                <span className="text-[12px] font-semibold text-gray-400">{visibleProfiles.length} 个画像</span>
              </div>
              <p className="mb-3 text-[12px] font-medium leading-5 text-gray-500">
                可点击画像，调整画像更新和情报抓取频率与方向。
              </p>
              <div className="relative -mx-1">
                <div className="flex gap-2 overflow-x-auto px-1 pb-2 [scrollbar-width:thin]">
                {visibleProfiles.map((profile) => (
                  <button
                    key={profile.id}
                    type="button"
                    onClick={() => openProfileDetail(profile)}
                    className="flex shrink-0 items-center gap-2 rounded-full border border-gray-100 bg-white px-3 py-2 text-left text-[12px] shadow-sm transition hover:border-gray-300 hover:shadow-md"
                  >
                    <span className="rounded-full bg-slate-100 px-2 py-1 text-[11px] font-black text-slate-600">{profileScopeLabel(profile).replace('画像', '')}</span>
                    <span className="max-w-[160px] truncate font-black text-gray-900">{profileTitle(profile) || profile.title || '未命名'}</span>
                    {profilePillKeywords(profile).map((keyword) => (
                      <span key={`${profile.id}-${keyword}`} className="max-w-[92px] truncate text-gray-500">{keyword}</span>
                    ))}
                    <span className={`rounded-full px-2 py-1 text-[11px] font-bold ${profile.profileReadiness === 'ready' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>
                      {profilePillStatus(profile)}
                    </span>
                  </button>
                ))}
                </div>
                <div className="pointer-events-none absolute inset-y-0 right-0 w-10 bg-gradient-to-l from-white to-transparent" />
              </div>
            </section>
          )}
        </header>

        <section className="mt-7 space-y-4">
          <div>
            <p className="text-[12px] font-bold tracking-[0.18em] text-gray-400">精选判断</p>
            <h2 className="mt-2 text-[22px] font-black text-gray-950">精选顾问判断</h2>
          </div>

          {viewModel.isExampleOnly && (
            <div className="rounded-lg border border-dashed border-amber-200 bg-amber-50/70 px-5 py-4 text-[13px] leading-6 text-amber-800">
              empty · 本轮没有达到推荐门槛的真实外部情报；系统不会用示例或宽泛内容制造“已有判断”的假象。
            </div>
          )}

          {viewModel.judgments.map((item) => {
            const candidate = item.candidate;
            const taskLinked = Boolean(candidate && (candidate.convertedTaskId || (relatedTasksByCandidate.get(candidate.id) || []).length > 0));
            const highlighted = Boolean(candidate && highlightedCandidateId === candidate.id);
            return (
              <article
                key={item.id}
                id={candidate ? `topic-candidate-${candidate.id}` : undefined}
                className={`rounded-lg border bg-white px-6 py-5 shadow-sm transition ${highlighted ? 'border-indigo-300 ring-4 ring-indigo-100' : 'border-gray-200'}`}
              >
                <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      {item.isExample && <span className="rounded-full bg-amber-50 px-2.5 py-1 text-[11px] font-bold text-amber-700">示例</span>}
                      <span className="rounded-full bg-blue-50 px-2.5 py-1 text-[11px] font-bold text-blue-700">{item.relatedObject}</span>
                      <span className="rounded-full bg-gray-100 px-2.5 py-1 text-[11px] font-bold text-gray-600">{item.evidenceStatus}</span>
                      {taskLinked && <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-[11px] font-bold text-emerald-700">已转任务</span>}
                    </div>
                    <h3 className="mt-3 text-[18px] font-black leading-snug text-gray-950">{item.conclusion}</h3>
                  </div>
                  <div className="flex shrink-0 flex-wrap gap-2 sm:flex-col sm:items-stretch">
                    {item.isExample ? (
                      <button
                        type="button"
                        disabled
                        className="inline-flex items-center justify-center gap-1 rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-[12px] font-bold text-gray-400"
                      >
                        示例不可操作
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => openTaskPromotionDialog(item)}
                        disabled={taskPendingId === candidate?.id || taskLinked}
                        className="inline-flex items-center justify-center gap-1 rounded-md bg-gray-950 px-3 py-2 text-[12px] font-bold text-white hover:bg-gray-800 disabled:cursor-not-allowed disabled:bg-gray-300"
                      >
                        {taskPendingId === candidate?.id ? <Loader2 size={14} className="animate-spin" /> : <FilePlus2 size={14} />}
                        {taskLinked ? '已转任务' : '转任务'}
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => setQuestionPanelItemId(item.id)}
                      disabled={item.isExample}
                      className="inline-flex items-center justify-center gap-1 rounded-md border border-indigo-100 bg-indigo-50 px-3 py-2 text-[12px] font-bold text-indigo-700 hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-45"
                    >
                      <MessageCircle size={14} />
                      追问
                    </button>
                    {isAdmin && candidate && !item.isExample && (
                      <button
                        type="button"
                        onClick={() => void handleDeleteJudgment(item)}
                        disabled={deletePendingId === candidate.id}
                        className="inline-flex items-center justify-center gap-1 rounded-md border border-rose-100 bg-rose-50 px-3 py-2 text-[12px] font-bold text-rose-700 hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-45"
                      >
                        {deletePendingId === candidate.id ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                        删除
                      </button>
                    )}
                  </div>
                </div>

	                <div className="mt-5 grid gap-3">
	                  <AdvisorMemoBlock
	                    title="情报速览"
	                    lines={item.intelligenceBrief}
	                    className="border-slate-100 bg-slate-50"
	                  />
		                  <AdvisorMemoBlock
		                    title="情报研判"
		                    lines={item.intelligenceAssessment}
		                    className="border-blue-100 bg-blue-50/70"
		                  />
                  <AdvisorMemoBlock
                    title="行动建议"
                    lines={item.actionAdvice}
                    className="border-amber-100 bg-amber-50/80"
                  />
                </div>

                <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-gray-100 pt-4 text-[12px] font-semibold text-gray-500">
	                  <span>参考链接：</span>
                  {candidate?.sourceUrl ? (
                    <a
                      href={candidate.sourceUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="text-blue-700 underline decoration-blue-200 underline-offset-4 hover:text-blue-900"
                    >
                      {candidate.source || '公开来源'}
                    </a>
                  ) : (
                    <span>{candidate?.source || item.evidenceChain[0] || '公开来源待补充'}</span>
                  )}
                  <span>· {item.evidenceStatus}</span>
                  {candidate?.publishedAt && <span>· {formatBriefingDate(candidate.publishedAt)}</span>}
                </div>
              </article>
            );
          })}
        </section>
      </main>

      {taskDialogItem && taskDialogDraft && taskDialogItem.candidate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/35 px-4 py-6">
          <div className="w-full max-w-[640px] rounded-lg bg-white p-6 shadow-2xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-[12px] font-bold uppercase tracking-[0.18em] text-gray-400">转成任务</p>
                <h3 className="mt-2 text-[20px] font-black text-gray-950">{taskDialogItem.conclusion}</h3>
                <p className="mt-2 text-[13px] leading-6 text-gray-600">
                  系统会把顾问结论、推荐理由和情报原文回链写入任务，并自动给负责人和协作者开放这条情报的查看权限。
                </p>
              </div>
              <button
                type="button"
                onClick={() => {
                  setTaskDialogItem(null);
                  setTaskDialogDraft(null);
                }}
                className="rounded-md border border-gray-200 p-2 text-gray-500 hover:bg-gray-50"
              >
                <X size={16} />
              </button>
            </div>

            <div className="mt-5 grid gap-4">
              <label className="grid gap-2 text-[13px] font-bold text-gray-700">
                负责人
                <select
                  value={taskDialogDraft.ownerId}
                  onChange={(event) => {
                    const owner = memberOptions.find((person) => person.id === event.target.value) || currentPerson;
                    setTaskDialogDraft((prev) => prev ? {
                      ...prev,
                      ownerId: owner.id,
                      ownerName: owner.fullName,
                      collaboratorIds: prev.collaboratorIds.filter((id) => id !== owner.id),
                    } : prev);
                  }}
                  className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-[13px] outline-none focus:border-gray-400"
                >
                  {memberOptions.map((person) => (
                    <option key={person.id} value={person.id}>{person.fullName || person.email || person.id}{person.isSelf ? '（我）' : ''}</option>
                  ))}
                </select>
              </label>

              <div className="grid gap-2 text-[13px] font-bold text-gray-700">
                协作者
                <div className="max-h-[140px] overflow-y-auto rounded-md border border-gray-100 bg-gray-50 p-2">
                  {memberOptions.filter((person) => person.id !== taskDialogDraft.ownerId).length === 0 ? (
                    <p className="px-2 py-1 text-[12px] font-medium text-gray-500">暂无可选协作者。</p>
                  ) : (
                    memberOptions.filter((person) => person.id !== taskDialogDraft.ownerId).map((person) => (
                      <label key={person.id} className="flex items-center gap-2 rounded-md px-2 py-1.5 text-[13px] font-medium text-gray-700 hover:bg-white">
                        <input
                          type="checkbox"
                          checked={taskDialogDraft.collaboratorIds.includes(person.id)}
                          onChange={(event) => {
                            setTaskDialogDraft((prev) => {
                              if (!prev) return prev;
                              const next = event.target.checked
                                ? [...prev.collaboratorIds, person.id]
                                : prev.collaboratorIds.filter((id) => id !== person.id);
                              return { ...prev, collaboratorIds: next };
                            });
                          }}
                        />
                        <span>{person.fullName || person.email || person.id}</span>
                      </label>
                    ))
                  )}
                </div>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <label className="grid gap-2 text-[13px] font-bold text-gray-700">
                  截止日期
                  <input
                    type="date"
                    value={taskDialogDraft.dueDate}
                    onChange={(event) => setTaskDialogDraft((prev) => prev ? { ...prev, dueDate: event.target.value } : prev)}
                    className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-[13px] outline-none focus:border-gray-400"
                  />
                </label>
                <label className="grid gap-2 text-[13px] font-bold text-gray-700">
                  关联事件线（可选）
                  <select
                    value={taskDialogDraft.eventLineId}
                    onChange={(event) => setTaskDialogDraft((prev) => prev ? { ...prev, eventLineId: event.target.value } : prev)}
                    className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-[13px] outline-none focus:border-gray-400"
                  >
                    <option value="">不关联事件线</option>
                    {activeEventLines.map((line) => (
                      <option key={line.id} value={line.id}>{line.name}</option>
                    ))}
                  </select>
                </label>
              </div>
            </div>

	            <div className="mt-5 rounded-md bg-gray-50 px-4 py-3 text-[12px] leading-6 text-gray-600">
	              <p className="font-bold text-gray-800">将写入任务的核心内容</p>
	              <p className="mt-1">情报速览：{taskDialogItem.intelligenceBrief[0]}</p>
	              <p>行动建议：{taskDialogItem.actionAdvice[0]}</p>
	            </div>

            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  setTaskDialogItem(null);
                  setTaskDialogDraft(null);
                }}
                className="rounded-md border border-gray-200 px-4 py-2 text-[13px] font-bold text-gray-700 hover:bg-gray-50"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void handleConfirmPromoteToTask()}
                disabled={taskPendingId === taskDialogItem.candidate.id}
                className="inline-flex items-center gap-1 rounded-md bg-gray-950 px-4 py-2 text-[13px] font-bold text-white hover:bg-gray-800 disabled:cursor-not-allowed disabled:bg-gray-300"
              >
                {taskPendingId === taskDialogItem.candidate.id ? <Loader2 size={14} className="animate-spin" /> : <FilePlus2 size={14} />}
                创建任务
              </button>
            </div>
          </div>
        </div>
      )}

      {questionDialogItem && questionDialogCandidate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/35 px-4 py-6">
          <div className="flex max-h-[86vh] w-full max-w-[680px] flex-col rounded-lg bg-white p-6 shadow-2xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-[12px] font-bold uppercase tracking-[0.18em] text-indigo-500">追问情报顾问</p>
                <h3 className="mt-2 text-[20px] font-black leading-snug text-gray-950">{questionDialogItem.conclusion}</h3>
                <p className="mt-2 text-[13px] leading-6 text-gray-600">
                  追问会基于这条情报、来源摘录、画像和已有底座事实回答；如果材料不够，会直接说明需要补什么。
                </p>
              </div>
              <button
                type="button"
                onClick={() => setQuestionPanelItemId('')}
                className="rounded-md border border-gray-200 p-2 text-gray-500 hover:bg-gray-50"
              >
                <X size={16} />
              </button>
            </div>

            <div className="mt-5 flex flex-wrap gap-2">
              {DEFAULT_ADVISOR_QUESTIONS.map((question) => (
                <button
                  key={question}
                  type="button"
                  onClick={() => void handleAskQuestion(questionDialogItem, question)}
                  disabled={questionPendingId === questionDialogCandidate.id}
                  className="rounded-full border border-indigo-100 bg-indigo-50 px-3 py-1.5 text-[12px] font-bold text-indigo-700 hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-45"
                >
                  {question}
                </button>
              ))}
            </div>

            <div className="mt-4 min-h-[120px] flex-1 overflow-y-auto rounded-lg border border-gray-100 bg-gray-50 p-3">
              {questionDialogMessages.length === 0 ? (
                <p className="px-1 py-2 text-[13px] leading-6 text-gray-500">
                  可以直接点上面的建议问题，也可以输入更具体的问题。发送后这里会显示回答进度和结果。
                </p>
              ) : (
                <div className="space-y-2">
                  {questionDialogMessages.map((message, index) => (
                    <div
                      key={`${message.createdAt}-${index}`}
                      className={`rounded-md px-3 py-2 text-[13px] leading-6 ${message.role === 'user' ? 'bg-white text-gray-700' : 'bg-indigo-900 text-white'}`}
                    >
                      {message.content}
                    </div>
                  ))}
                  {questionPendingId === questionDialogCandidate.id && (
                    <div className="inline-flex items-center gap-2 rounded-md bg-white px-3 py-2 text-[13px] font-bold text-indigo-700">
                      <Loader2 size={14} className="animate-spin" />
                      正在基于当前情报回答...
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="mt-4 flex gap-2">
              <input
                value={questionDialogDraft}
                onChange={(event) => setQuestionDraftByCandidateId((prev) => ({ ...prev, [questionDialogCandidate.id]: event.target.value }))}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    void handleAskQuestion(questionDialogItem, questionDialogDraft);
                  }
                }}
                placeholder="继续追问这条情报，例如：它适合现在转任务吗？"
                className="min-w-0 flex-1 rounded-md border border-indigo-100 bg-white px-3 py-2 text-[13px] outline-none focus:border-indigo-300"
              />
              <button
                type="button"
                onClick={() => void handleAskQuestion(questionDialogItem, questionDialogDraft)}
                disabled={questionPendingId === questionDialogCandidate.id || !questionDialogDraft.trim()}
                className="inline-flex items-center gap-1 rounded-md bg-indigo-600 px-3 py-2 text-[12px] font-bold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-indigo-200"
              >
                {questionPendingId === questionDialogCandidate.id ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                发送
              </button>
            </div>
          </div>
        </div>
      )}

      {selectedProfile && profileDraft && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/35 px-4 py-6">
          <div className="max-h-[88vh] w-full max-w-[720px] overflow-y-auto rounded-lg bg-white p-6 shadow-2xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-bold text-slate-600">{profileScopeLabel(selectedProfile)}</span>
                  <span className={`rounded-full px-2.5 py-1 text-[11px] font-bold ${selectedProfile.profileReadiness === 'ready' ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'}`}>
                    {profileStatusLabel(selectedProfile)}
                  </span>
                </div>
                <h3 className="mt-3 text-[20px] font-black text-gray-950">{profileTitle(selectedProfile) || selectedProfile.title || '未命名画像'}</h3>
                <p className="mt-2 text-[13px] leading-6 text-gray-500">
                  资料状态：{selectedProfile.materialCount} 类材料 · {profileMaterialLine(selectedProfile)}
                </p>
              </div>
              <button type="button" onClick={closeProfileDetail} className="rounded-md border border-gray-200 p-2 text-gray-500 hover:bg-gray-50">
                <X size={16} />
              </button>
            </div>

            <div className="mt-5 grid gap-4">
              <label className="grid gap-2 text-[13px] font-bold text-gray-700">
                画像概况补充
                {isAdmin ? (
                  <textarea
                    value={profileDraft.summary}
                    onChange={(event) => setProfileDraft((prev) => (prev ? { ...prev, summary: event.target.value } : prev))}
                    rows={4}
                    className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-[13px] font-medium leading-6 outline-none focus:border-gray-400"
                    placeholder="管理员可以补充系统没读到的背景、阶段、真实关注点。"
                  />
                ) : (
                  <p className="rounded-md bg-gray-50 px-3 py-2 text-[13px] font-medium leading-6 text-gray-600">{selectedProfile.effectiveSummary || '暂未形成画像概况。'}</p>
                )}
              </label>

              <div className="grid gap-4 md:grid-cols-3">
                <label className="grid gap-2 text-[13px] font-bold text-gray-700">
                  关注重点
                  <textarea
                    value={profileDraft.focusText}
                    onChange={(event) => setProfileDraft((prev) => (prev ? { ...prev, focusText: event.target.value } : prev))}
                    rows={4}
                    disabled={!isAdmin}
                    className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-[13px] font-medium leading-6 outline-none disabled:text-gray-500"
                    placeholder="资助机会 / 合作方 / 政策窗口"
                  />
                </label>
                <label className="grid gap-2 text-[13px] font-bold text-gray-700">
                  排除方向
                  <textarea
                    value={profileDraft.excludeText}
                    onChange={(event) => setProfileDraft((prev) => (prev ? { ...prev, excludeText: event.target.value } : prev))}
                    rows={4}
                    disabled={!isAdmin}
                    className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-[13px] font-medium leading-6 outline-none disabled:text-gray-500"
                    placeholder="不相关地域 / 已知无效来源"
                  />
                </label>
                <label className="grid gap-2 text-[13px] font-bold text-gray-700">
                  重点网址
                  <textarea
                    value={profileDraft.urlsText}
                    onChange={(event) => setProfileDraft((prev) => (prev ? { ...prev, urlsText: event.target.value } : prev))}
                    rows={4}
                    disabled={!isAdmin}
                    className="rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-[13px] font-medium leading-6 outline-none disabled:text-gray-500"
                    placeholder="每行一个公开来源网址"
                  />
                </label>
              </div>

              <div className="grid gap-3 rounded-md border border-gray-100 bg-gray-50 p-4 md:grid-cols-2">
                <label className="grid gap-2 text-[13px] font-bold text-gray-700">
                  画像刷新频率
                  <div className="flex gap-2">
                    <select
                      value={profileDraft.profileRefreshFrequency}
                      onChange={(event) => setProfileDraft((prev) => (prev ? { ...prev, profileRefreshFrequency: event.target.value as AdvisorProfileDraft['profileRefreshFrequency'], profileRefreshEnabled: event.target.value !== 'manual' } : prev))}
                      disabled={!isAdmin}
                      className="w-full rounded-md border border-gray-200 bg-white px-3 py-2 text-[13px] outline-none disabled:text-gray-500"
                    >
                      <option value="manual">手动</option>
                      <option value="weekly">每周</option>
                      <option value="workday">工作日</option>
                      <option value="daily">每日</option>
                    </select>
                  </div>
                  <span className="text-[12px] font-medium text-gray-500">下次预计：{selectedProfile.nextProfileRefreshAt ? formatBriefingDate(selectedProfile.nextProfileRefreshAt) : '未安排'}</span>
                </label>
                <label className="grid gap-2 text-[13px] font-bold text-gray-700">
                  情报抓取频率
                  <select
                    value={profileDraft.pushFrequency}
                    onChange={(event) => setProfileDraft((prev) => (prev ? { ...prev, pushFrequency: event.target.value as AdvisorProfileDraft['pushFrequency'], pushEnabled: event.target.value !== 'manual' } : prev))}
                    disabled={!isAdmin}
                    className="w-full rounded-md border border-gray-200 bg-white px-3 py-2 text-[13px] outline-none disabled:text-gray-500"
                  >
                    <option value="manual">手动</option>
                    <option value="weekly">每周</option>
                    <option value="workday">工作日</option>
                    <option value="daily">每日</option>
                  </select>
                  <span className="text-[12px] font-medium text-gray-500">下次预计：{selectedProfile.nextIntelligenceFetchAt ? formatBriefingDate(selectedProfile.nextIntelligenceFetchAt) : '未安排'}</span>
                </label>
              </div>
            </div>

            <div className="mt-5 flex flex-wrap justify-between gap-2">
              <div className="text-[12px] leading-6 text-gray-500">
                {selectedProfile.lastAutomationResult ? `最近自动结果：${selectedProfile.lastAutomationResult}` : '自动运行只会在资料就绪且频率到期后触发。'}
              </div>
              <div className="flex flex-wrap gap-2">
                {isAdmin && (
                  <>
                    <button
                      type="button"
                      onClick={handleSaveProfile}
                      disabled={profilePendingId === selectedProfile.id}
                      className="inline-flex items-center gap-1 rounded-md bg-gray-950 px-3 py-2 text-[12px] font-bold text-white disabled:bg-gray-300"
                    >
                      {profilePendingId === selectedProfile.id ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
                      保存画像
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleRefreshProfile(selectedProfile)}
                      disabled={profilePendingId === selectedProfile.id}
                      className="inline-flex items-center gap-1 rounded-md border border-gray-200 px-3 py-2 text-[12px] font-bold text-gray-700 hover:bg-gray-50 disabled:opacity-45"
                    >
                      <RefreshCw size={14} />
                      刷新系统理解
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleTrialRunProfile(selectedProfile)}
                      disabled={profilePendingId === selectedProfile.id || selectedProfile.profileReadiness !== 'ready'}
                      className="inline-flex items-center gap-1 rounded-md border border-blue-100 bg-blue-50 px-3 py-2 text-[12px] font-bold text-blue-700 hover:bg-blue-100 disabled:opacity-45"
                    >
                      <Search size={14} />
                      试跑情报抓取
                    </button>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
