import type {
  AnalysisAuthorityLevel,
  EvidenceSupportMode,
  FallbackPresentationMode,
  JudgmentQueryMode,
  ProposalRecord,
  RetrievalDecisionReason,
  StateAnswerSections,
  StateSourceSummary,
  WorkspaceAnswerIntent,
} from './types.js';

export type WorkspaceSignalTone = {
  sectionLabel: string;
  authorityBadge: string;
  notice: string | null;
  formal: boolean;
};

export type CockpitHeadlineTone = {
  title: string;
  subtitle: string;
  allowFormalHeadline: boolean;
};

export type ChatRetrievalPresentation = {
  label: '状态优先' | '证据下钻' | '身份校验' | '状态不足';
  detail: string;
};

export type ChatRouteDecision = {
  intentLabel: string;
  drilldownLabel: '是' | '否';
  primarySource: '状态池' | '原始资料' | '状态池 + 原始资料' | '通用背景';
  noDrilldownReason: string | null;
};

export type ProposalEffectType = 'recorded_only' | 'prep_artifact_ready' | 'followup_task_created' | 'failed';

export type ProposalGroupBuckets = {
  pendingReview: ProposalRecord[];
  approvedExecution: ProposalRecord[];
  history: ProposalRecord[];
};

export type ChatExtendedAnalysisDecision = {
  shouldRender: boolean;
  duplicateStateOnlyContent: boolean;
  blockedByPresentationMode: boolean;
  blockedByLeakMarkers: boolean;
};

const CHAT_PROCESS_LEAK_MARKERS = [
  'analysis-first',
  '当前最值得抓住的原始观察包括',
  '先基于客户工作台里的最新状态信号',
  '[本周动作]',
  '[缺失信息]',
  '单击此处编辑母版文本样式',
  '演示文稿标题',
  '演示文稿副标题',
];

function looksLikeAnswerTitle(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return false;
  if (trimmed.length < 6 || trimmed.length > 42) return false;
  if (/[。！？!?]$/.test(trimmed)) return false;
  return !/^(问题|资料简报|回答原则|当前可确认的资料事实)[:：]/.test(trimmed);
}

export function normalizeAnswerTextForDisplay(rawText: string) {
  let text = rawText.replace(/\r\n/g, '\n').trim();
  const firstLineMatch = text.match(/^([^\n]{6,48}?)(\s{2,})(.+)$/s);
  if (firstLineMatch) {
    const candidateTitle = firstLineMatch[1].trim();
    const rest = firstLineMatch[3].trimStart();
    if (looksLikeAnswerTitle(candidateTitle)) {
      text = `${candidateTitle}\n\n${rest}`;
    }
  }
  text = text.replace(/\n([一二三四五六七八九十]+、)/g, '\n\n$1');
  text = text.replace(/\n(第[一二三四五六七八九十0-9]+部分)/g, '\n\n$1');
  text = text.replace(
    /\n(首先|其次|再次|然后|接着|此外|另外|再者|最后|总之|综上|第[一二三四五六七八九十0-9]+(?:是|，)|[一二三四五六七八九十]是)/g,
    '\n\n$1',
  );
  text = text.replace(/\n{3,}/g, '\n\n');
  return text;
}

export function renderStateSectionsTextForComparison(sections?: StateAnswerSections | null) {
  if (!sections) return '';
  const orderedSections: Array<[string, string[]]> = [
    ['一、正式判断', sections.official],
    ['二、待确认判断 / 判断草稿', [...sections.candidate, ...(sections.draftFindings || [])]],
    ['三、支撑证据摘要', sections.evidenceSupport || []],
    ['四、本周动作 / 当前推进', sections.actions],
    ['五、风险提醒 / 未决问题', sections.risks],
    ['六、缺失信息 / 下一步建议', sections.unknowns],
  ];
  return orderedSections
    .map(([title, items]) => `${title}\n${items.length ? items.map((item) => `- ${item}`).join('\n') : '- 当前暂无可展示内容。'}`)
    .join('\n\n')
    .trim();
}

export function containsChatProcessLeakMarkers(rawText: string) {
  const haystack = rawText.toLowerCase();
  return CHAT_PROCESS_LEAK_MARKERS.some((marker) => haystack.includes(marker.toLowerCase()));
}

export function shouldRenderChatExtendedAnalysis(input: {
  content?: string | null;
  stateSections?: StateAnswerSections | null;
  fallbackPresentationMode?: FallbackPresentationMode | null;
}): ChatExtendedAnalysisDecision {
  const rawContent = (input.content || '').trim();
  if (!rawContent) {
    return {
      shouldRender: false,
      duplicateStateOnlyContent: false,
      blockedByPresentationMode: false,
      blockedByLeakMarkers: false,
    };
  }
  const duplicateStateOnlyContent = Boolean(input.stateSections)
    && normalizeAnswerTextForDisplay(rawContent) === normalizeAnswerTextForDisplay(renderStateSectionsTextForComparison(input.stateSections));
  const blockedByPresentationMode = input.fallbackPresentationMode === 'state_cards_only';
  const blockedByLeakMarkers = input.fallbackPresentationMode === 'compact_user_answer' && containsChatProcessLeakMarkers(rawContent);
  return {
    shouldRender: !duplicateStateOnlyContent && !blockedByPresentationMode && !blockedByLeakMarkers,
    duplicateStateOnlyContent,
    blockedByPresentationMode,
    blockedByLeakMarkers,
  };
}

export function getWorkspaceSignalTone(input: {
  hasOfficialBaseline: boolean;
  authorityLevel?: AnalysisAuthorityLevel | null;
}): WorkspaceSignalTone {
  const authorityLevel = input.authorityLevel || 'fallback';
  const authorityBadge =
    authorityLevel === 'approved'
      ? '正式判断'
      : authorityLevel === 'candidate'
        ? '候选判断'
        : '回退判断';
  if (input.hasOfficialBaseline && authorityLevel === 'approved') {
    return {
      sectionLabel: '客户级正式基线',
      authorityBadge,
      notice: null,
      formal: true,
    };
  }
  return {
    sectionLabel: '主链候选信号',
    authorityBadge,
    notice: '当前暂无客户级已批准判断。以下内容只作为候选信号或回退结果，不代表正式结论。',
    formal: false,
  };
}

export function getCockpitHeadlineTone(input: {
  officialLayerStatus: 'ready' | 'empty';
  officialEmptyReason?: string | null;
}): CockpitHeadlineTone {
  if (input.officialLayerStatus === 'ready') {
    return {
      title: '官方层已就绪',
      subtitle: '当前标题和结论可以使用正式语气。',
      allowFormalHeadline: true,
    };
  }
  return {
    title: input.officialEmptyReason || '当前暂无已批准判断',
    subtitle: '以下仅展示候选信号与风险雷达，不代表正式结论。',
    allowFormalHeadline: false,
  };
}

export function getChatRetrievalPresentation(input?: {
  reason?: RetrievalDecisionReason | null;
  judgmentQueryMode?: JudgmentQueryMode | null;
  evidenceSupportMode?: EvidenceSupportMode | null;
} | null): ChatRetrievalPresentation {
  // P2.12 FREEZE(route-copy): 当前路由标签和解释文案会直接影响用户如何理解
  // “为什么走这条链、为什么这样回答”。验证期不要继续漂移。
  const reason = input?.reason;
  const judgmentQueryMode = input?.judgmentQueryMode;
  const evidenceSupportMode = input?.evidenceSupportMode;

  if (judgmentQueryMode === 'registry_only') {
    return {
      label: '状态优先',
      detail: '当前优先展示系统内已登记的正式判断。',
    };
  }
  if (judgmentQueryMode === 'hybrid') {
    return {
      label: '状态优先',
      detail:
        evidenceSupportMode === 'raw_doc_drilldown'
          ? '当前先读取已登记判断，再结合资料、会议、任务和 DNA 信号形成待确认判断，并补充少量原文回引。'
          : '当前先读取已登记判断，再结合资料、会议、任务和 DNA 信号形成待确认判断。',
    };
  }
  if (judgmentQueryMode === 'evidence_based_synthesis') {
    return {
      label: '证据下钻',
      detail: '当前已进入证据下钻，将结合状态池与原始资料回答。',
    };
  }

  switch (reason) {
    case 'official_registry_requested':
    case 'state_first_default':
      return {
        label: '状态优先',
        detail: reason === 'official_registry_requested'
          ? '当前优先读取系统内已登记的正式判断。'
          : '这次先读客户状态池，再按需下钻原文。',
      };
    case 'document_drilldown_requested':
    case 'search_cache_requested':
      return {
        label: '证据下钻',
        detail: '这次明确要求引用原文或已有搜索证据，优先走证据链。',
      };
    case 'intro_query_needs_evidence':
    case 'project_intro_needs_evidence':
      return {
        label: '证据下钻',
        detail: reason === 'project_intro_needs_evidence'
          ? '项目介绍类问题优先回到项目资料与原始证据，不直接套状态池。'
          : '介绍或简介类问题优先回到机构介绍、项目资料和原始证据，不直接套状态池。',
      };
    case 'meeting_summary_needs_evidence':
      return {
        label: '证据下钻',
        detail: '会议纪要类问题优先检索会议、行动项和原始资料证据。',
      };
    case 'next_actions_needs_evidence':
      return {
        label: '证据下钻',
        detail: '下一步类问题优先结合任务、会议行动项与原始资料。',
      };
    case 'evidence_question_needs_evidence':
      return {
        label: '证据下钻',
        detail: '这次问题明确要求依据或引用，回答优先回到原始证据。',
      };
    case 'status_progress_needs_hybrid_evidence':
    case 'default_hybrid_evidence':
      return {
        label: '证据下钻',
        detail: '当前采用状态池 + 原始资料的混合证据回答。',
      };
    case 'identity_query_needs_evidence':
      return {
        label: '身份校验',
        detail: '涉及人物或角色问题，必须先确认原始证据。',
      };
    case 'state_pool_insufficient':
    case 'state_pool_empty':
    default:
      return {
        label: '状态不足',
        detail: '当前状态池还不够稳，需要更多证据或补充信息。',
      };
  }
}

export function getWorkspaceAnswerIntentLabel(intent?: WorkspaceAnswerIntent | null): string {
  // P2.12 FREEZE(intent-labels): 当前 intent -> 用户可见标签先冻结。
  switch (intent) {
    case 'intro_profile':
      return '介绍客户/机构';
    case 'project_intro':
      return '介绍项目';
    case 'meeting_summary':
      return '会议纪要';
    case 'next_actions':
      return '下一步行动';
    case 'official_judgment_registry':
      return '正式判断查询';
    case 'evidence_question':
      return '证据追问';
    case 'status_progress':
      return '状态推进';
    case 'general':
    default:
      return '通用问答';
  }
}

function getDataCenterIntentLabel(intent: string): string {
  const normalized = intent.trim();
  switch (normalized) {
    case 'business_profile':
    case 'intro_profile':
      return '介绍客户/机构';
    case 'project_intro':
      return '介绍项目';
    case 'meeting_summary':
      return '会议纪要';
    case 'task_next_action':
    case 'next_actions':
      return '下一步行动';
    case 'official_judgment_registry':
      return '正式判断查询';
    case 'evidence_question':
    case 'evidence_answer':
      return '证据追问';
    case 'strategy_profile':
    case 'status_progress':
      return '状态推进';
    case 'general':
    default:
      return '通用问答';
  }
}

export function getChatRouteDecisionFromDataCenter(input: {
  routeDecision?: Record<string, unknown> | null;
}): ChatRouteDecision | null {
  const routeDecision = input.routeDecision;
  if (!routeDecision || typeof routeDecision !== 'object') {
    return null;
  }
  const retrievalMode = String(routeDecision.retrievalMode || '').trim();
  const shouldUseRawEvidence = routeDecision.shouldUseRawEvidence === true;
  const shouldUseStatePool = routeDecision.shouldUseStatePool === true;
  const drilledDown = retrievalMode
    ? retrievalMode === 'raw_only' || retrievalMode === 'hybrid'
    : shouldUseRawEvidence;
  let primarySource: ChatRouteDecision['primarySource'] = '通用背景';
  if (retrievalMode === 'state_only') {
    primarySource = '状态池';
  } else if (retrievalMode === 'raw_only') {
    primarySource = '原始资料';
  } else if (retrievalMode === 'hybrid') {
    primarySource = '状态池 + 原始资料';
  } else if (shouldUseRawEvidence && shouldUseStatePool) {
    primarySource = '状态池 + 原始资料';
  } else if (shouldUseRawEvidence) {
    primarySource = '原始资料';
  } else if (shouldUseStatePool) {
    primarySource = '状态池';
  }
  const routeReason = String(routeDecision.routeReason || '').trim();
  return {
    intentLabel: getDataCenterIntentLabel(String(routeDecision.intent || '')),
    drilldownLabel: drilledDown ? '是' : '否',
    primarySource,
    noDrilldownReason: drilledDown ? null : (routeReason || '本轮路由未下钻原文。'),
  };
}

export function getChatRouteDecision(input: {
  answerIntent?: WorkspaceAnswerIntent | null;
  answerMode?: 'grounded_answer' | 'grounded_fallback' | 'low_confidence_answer' | 'general_answer' | 'system_failure' | null;
  retrievalDeferred?: boolean;
  reason?: RetrievalDecisionReason | null;
  judgmentQueryMode?: JudgmentQueryMode | null;
}): ChatRouteDecision {
  const intentLabel = getWorkspaceAnswerIntentLabel(input.answerIntent);
  const retrievalDeferred = Boolean(input.retrievalDeferred);
  const reason = input.reason || null;
  const drilledDown = !retrievalDeferred;
  let primarySource: ChatRouteDecision['primarySource'] = '原始资料';
  if (input.answerMode === 'general_answer') {
    primarySource = '通用背景';
  } else if (reason === 'official_registry_requested' || reason === 'state_first_default' || input.judgmentQueryMode === 'registry_only') {
    primarySource = '状态池';
  } else if (input.judgmentQueryMode === 'hybrid' || input.judgmentQueryMode === 'evidence_based_synthesis') {
    primarySource = drilledDown ? '状态池 + 原始资料' : '状态池';
  } else if (retrievalDeferred) {
    primarySource = '状态池';
  }
  let noDrilldownReason: string | null = null;
  if (!drilledDown) {
    noDrilldownReason = getChatRetrievalPresentation({
      reason,
      judgmentQueryMode: input.judgmentQueryMode,
    }).detail;
  }
  return {
    intentLabel,
    drilldownLabel: drilledDown ? '是' : '否',
    primarySource,
    noDrilldownReason,
  };
}

export function formatStateSourceSummary(summary?: StateSourceSummary | null): string[] {
  if (!summary) return [];
  const items: string[] = [];
  if (summary.judgments > 0) items.push(`${summary.judgments} 条判断`);
  if (summary.meetings > 0) items.push(`${summary.meetings} 次会议`);
  if (summary.tasks > 0) items.push(`${summary.tasks} 条任务`);
  if (summary.openQuestions > 0) items.push(`${summary.openQuestions} 个未决问题`);
  if (summary.conflicts > 0) items.push(`${summary.conflicts} 个风险冲突`);
  if (summary.documents > 0) items.push(`${summary.documents} 份原文`);
  return items;
}

export function groupProposalRecords(proposals: ProposalRecord[]): ProposalGroupBuckets {
  return {
    pendingReview: proposals.filter((proposal) => proposal.status === 'pending_review' || proposal.status === 'draft'),
    approvedExecution: proposals.filter((proposal) => proposal.status === 'approved' || proposal.status === 'execution_pending'),
    history: proposals.filter((proposal) => ['executed', 'failed', 'rejected'].includes(proposal.status)).slice(0, 8),
  };
}

export function getProposalEffectType(proposal: ProposalRecord): ProposalEffectType {
  const resultType = proposal.executionTicket?.result?.resultType;
  if (resultType) return resultType;
  if (proposal.status === 'failed') return 'failed';
  if (proposal.status === 'executed') {
    return proposal.kind === 'meeting_followup' ? 'followup_task_created' : 'prep_artifact_ready';
  }
  return 'recorded_only';
}
