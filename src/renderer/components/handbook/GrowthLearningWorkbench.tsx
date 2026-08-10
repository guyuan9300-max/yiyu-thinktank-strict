import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  ArrowRight,
  BookOpen,
  Bot,
  Briefcase,
  CalendarDays,
  CheckCircle,
  CheckSquare,
  ChevronDown,
  FileText,
  ListTodo,
  MessageSquare,
  ShieldAlert,
  Target,
  Trophy,
  UserCheck,
  Users,
  X,
  Zap,
  type LucideIcon,
} from 'lucide-react';

import type {
  GrowthAfterActionCapture,
  GrowthActionPlanItem,
  GrowthContextLink,
  GrowthFocusAction,
  GrowthGenericLesson,
  GrowthLearningSummary,
  GrowthMaterialRef,
  GrowthPendingCapture,
  GrowthProjectContextPack,
  GrowthProjectGuidance,
  GrowthReasoningTrace,
  GrowthRobotAssist,
  GrowthTaskIntent,
  GrowthUniversalSkillItem,
  GrowthWorkbenchSnapshot,
  Task,
} from '../../../shared/types';
import { isPersonalOnlyTask } from '../../../shared/taskVisibility';

type FlashLevel = 'success' | 'error';

type LearningWorkbenchCard = {
  id: string;
  theme: string;
  reason: string;
  summary?: string | null;
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

type AbilityWorkbenchCard = {
  id: string;
  name: string;
  currentScore: number;
  previousScore: number;
  stage: string;
  numericInc: number;
  evidence: string;
};

type DailyDropCard = {
  id: string;
  task: string;
  time: string;
  xp: number;
  type: string;
  isSpecial: boolean;
};

type GrowthLearningWorkbenchProps = {
  learningCards: LearningWorkbenchCard[];
  abilityCards: AbilityWorkbenchCard[];
  dailyDrops: DailyDropCard[];
  workbenchSnapshot?: GrowthWorkbenchSnapshot | null;
  currentFocusActions?: GrowthFocusAction[];
  pendingCaptures?: GrowthPendingCapture[];
  tasks?: Task[];
  flash: (level: FlashLevel, message: string) => void;
  onScheduleRecommendation: (recommendationId?: string | null) => Promise<void>;
  onDismissRecommendation: (recommendationId?: string | null) => Promise<void>;
  schedulingRecommendationId: string | null;
  dismissingRecommendationId: string | null;
  onOpenComposer: () => void;
  onSeedComposer?: (seed: { title: string; summary: string; sourceType?: string }) => void;
  onNavigate?: (tab: string) => void;
  onOpenContext?: (context: GrowthContextLink) => void;
};

type WorkbenchTask = {
  id: string;
  title: string;
  project: string;
  deadline: string;
  urgency: string;
  urgencyColor: string;
  phase: ProcessStep['name'];
  risks: string[];
  nextAdvice: string;
  robotReady: boolean;
  robotReasons: string[];
  recommendationId?: string | null;
  linkedTaskId?: string | null;
  linkedContexts: GrowthContextLink[];
  xpReward: number;
  contextSummary?: string;
  projectModuleName?: string | null;
  projectFlowName?: string | null;
  sourceEvidence: string[];
  currentBlocker?: string | null;
  missingSignals: string[];
  hasBackground: boolean;
  hasDeadline: boolean;
  isCrossDepartment: boolean;
  needsReview: boolean;
  evidenceCount: number;
  pendingCollaborations: number;
  taskIntent: GrowthTaskIntent;
  universalSkills: GrowthUniversalSkillItem[];
  projectContextPack: GrowthProjectContextPack;
  actionPlan: GrowthActionPlanItem[];
  materialRefs: GrowthMaterialRef[];
};

type WorkbenchAction = {
  id: string;
  title: string;
  output: string;
  scenario: string;
  actionLabel: string;
  supportTitle: string;
  detail?: string;
  context?: GrowthContextLink | null;
  seedTitle?: string;
  seedSummary?: string;
  kind: 'schedule' | 'support' | 'process' | 'compose' | 'task';
  recommendationId?: string | null;
};

type SupportMaterial = {
  id: string;
  title: string;
  type: '流程说明' | '经验案例' | '模板工具';
  scenario: string;
  summary?: string;
  linkedContext?: GrowthContextLink | null;
};

type ProcessStep = {
  id: string;
  name: '需求接收' | '信息核对' | '内部对齐' | '方案产出' | '沟通推进' | '交付闭环' | '复盘沉淀';
  output: string;
  bottlenecks: string[];
};

type ModalType = 'robot' | 'support' | 'process' | null;

const PROCESS_STEPS: ProcessStep[] = [
  { id: 'p1', name: '需求接收', output: '明确需求来源、目标对象和优先级', bottlenecks: ['需求来源模糊', '优先级未经确认'] },
  { id: 'p2', name: '信息核对', output: '确认关键事实、材料和依赖项都已到位', bottlenecks: ['输入材料不完整', '事实口径未统一'] },
  { id: 'p3', name: '内部对齐', output: '明确会议目标、参会人及预期结论', bottlenecks: ['未提前拉齐信息', '会议目标发散'] },
  { id: 'p4', name: '方案产出', output: '形成结构清晰、可执行的初版方案', bottlenecks: ['结构与受众不匹配', '缺少支撑数据'] },
  { id: 'p5', name: '沟通推进', output: '把边界、责任人和时间线谈清楚', bottlenecks: ['临场判断不足', '关键利益方未提前对齐'] },
  { id: 'p6', name: '交付闭环', output: '形成明确交付物、待办与复核节点', bottlenecks: ['只做了动作，没有闭环', '责任人和时间点不明确'] },
  { id: 'p7', name: '复盘沉淀', output: '把本次有效做法转成可复用经验', bottlenecks: ['只记录结果，没有方法', '经验无法迁移复用'] },
];

const PHASE_BY_INDEX: ProcessStep['name'][] = ['需求接收', '信息核对', '内部对齐', '方案产出', '沟通推进', '交付闭环', '复盘沉淀'];

const EMPTY_TASK: WorkbenchTask = {
  id: 'growth-empty-task',
  title: '等待成长上下文接入',
  project: '等待任务、会议或推荐接入',
  deadline: '尚未关联时间点',
  urgency: '等待上下文',
  urgencyColor: 'text-slate-500 bg-slate-100',
  phase: '信息核对',
  risks: ['系统需要真实任务、事件线或成长推荐才能推导具体动作，请先创建一条业务对象。'],
  nextAdvice: '先在任务与日历创建一条任务，或在客户工作台发布会议 / 行动项，任务学习页就会自动补全上下文。',
  robotReady: false,
  robotReasons: ['需要先有真实业务对象和阶段信息，机器人才能判断是否适合接手标准动作。'],
  recommendationId: null,
  linkedTaskId: null,
  linkedContexts: [],
  xpReward: 0,
  contextSummary: '',
  projectModuleName: null,
  projectFlowName: null,
  sourceEvidence: [],
  currentBlocker: null,
  missingSignals: ['缺真实任务', '缺项目上下文'],
  hasBackground: false,
  hasDeadline: false,
  isCrossDepartment: false,
  needsReview: false,
  evidenceCount: 0,
  pendingCollaborations: 0,
  taskIntent: {
    taskKind: 'general_execution',
    goal: '先形成一条真实任务，再进入任务学习页',
    deliverable: '一条带背景和时间点的任务',
    riskTypes: ['fact_gap'],
    requiredAbilities: ['exec', 'collab'],
    confidence: 0.2,
    whyRelevant: '系统需要真实任务对象才能判断更细的技能与项目背景，请先创建任务。',
  },
  universalSkills: [],
  projectContextPack: {
    title: '',
    taskNotes: [],
    attachments: [],
    memoryHints: [],
    linkedFacts: [],
    clientSummary: '',
    recentMeetings: [],
    eventLineSummary: '',
    strategicFocus: [],
    keyWarnings: [],
    contextGaps: ['缺真实任务', '缺项目背景'],
  },
  actionPlan: [],
  materialRefs: [],
};

function cx(...classNames: Array<string | false | null | undefined>) {
  return classNames.filter(Boolean).join(' ');
}

function buildFallbackTaskIntent(taskKind: string, goal: string, deliverable: string, whyRelevant: string, requiredAbilities: GrowthTaskIntent['requiredAbilities']): GrowthTaskIntent {
  return {
    taskKind,
    goal,
    deliverable,
    riskTypes: ['fact_gap', 'boundary_risk'],
    requiredAbilities,
    confidence: 0.52,
    whyRelevant,
  };
}

function buildFallbackProjectContextPack(title: string, summary: string, extras?: Partial<GrowthProjectContextPack>): GrowthProjectContextPack {
  return {
    title,
    taskNotes: summary ? [summary] : [],
    attachments: [],
    memoryHints: [],
    linkedFacts: [],
    clientSummary: '',
    recentMeetings: [],
    eventLineSummary: '',
    strategicFocus: [],
    keyWarnings: [],
    contextGaps: [],
    ...extras,
  };
}

function buildTaskFromLearningCard(card: LearningWorkbenchCard, index: number, ability?: AbilityWorkbenchCard): WorkbenchTask {
  const phase = (PROCESS_STEPS.find((step) => card.projectStage?.includes(step.name) || card.triggerNode?.includes(step.name))?.name || PHASE_BY_INDEX[Math.min(index + 2, PHASE_BY_INDEX.length - 1)]);
  const urgentColor = card.isUrgent ? 'text-red-700 bg-red-100' : index === 1 ? 'text-green-700 bg-green-100' : 'text-orange-700 bg-orange-100';
  const urgency = card.isUrgent ? '建议优先处理' : index === 1 ? '可直接推进' : '需先补关键动作';
  const heuristicText = `${card.theme}${card.learnContent.title}${card.practiceTask}`;
  const robotReady = /(模板|清单|纪要|生成|对齐|跟踪|排查)/.test(heuristicText) && card.learnContent.type !== '纠偏卡';
  const robotReasons = robotReady
    ? ['任务输出格式明确', `已匹配${card.learnContent.type}资产`, '当前阶段可先由机器人生成首稿']
    : ['关键判断仍需人工定调', '上下文还需要结合现场信息', '属于高博弈或高创造性动作'];

  return {
    id: card.id,
    title: card.theme,
    project: card.clientName || card.eventLineName || card.learnContent.title,
    deadline: card.isUrgent ? '本周内' : index === 0 ? '本周排期' : '可安排到下周',
    urgency,
    urgencyColor: urgentColor,
    phase,
    risks: [card.reason, card.whyNow || ability?.evidence || '当前场景缺少稳定复用动作，容易在关键节点返工'],
    nextAdvice: card.whyNow || card.practiceTask,
    robotReady,
    robotReasons,
    recommendationId: card.recommendationId,
    linkedTaskId: card.linkedTaskId ?? null,
    linkedContexts: card.linkedTaskId && !(card.linkedContexts || []).some((context) => context.objectType === 'task')
      ? [
          {
            objectType: 'task',
            objectId: card.linkedTaskId,
            label: card.theme,
            subtitle: card.projectStage || card.eventLineName || card.clientName || '成长练习',
            tab: 'tasks',
            statusLabel: '成长练习',
          },
          ...(card.linkedContexts || []),
        ]
      : (card.linkedContexts || []),
    xpReward: card.xpReward,
    contextSummary: card.reason || card.whyNow || card.practiceTask,
    projectModuleName: null,
    projectFlowName: null,
    sourceEvidence: [card.learnContent.title].filter(Boolean),
    currentBlocker: card.reason,
    missingSignals: [card.reason].filter(Boolean),
    hasBackground: true,
    hasDeadline: false,
    isCrossDepartment: Boolean(card.eventLineName || card.clientName),
    needsReview: false,
    evidenceCount: 1,
    pendingCollaborations: 0,
    taskIntent: buildFallbackTaskIntent(
      'growth_practice',
      card.practiceTask,
      card.learnContent.title,
      card.whyNow || card.reason || '系统根据当前成长缺口推了这条练习。',
      [card.learnContent.type === '模板' ? 'write' : 'collab', ability?.name === '分析判断' ? 'analyze' : 'exec'].filter(Boolean) as GrowthTaskIntent['requiredAbilities'],
    ),
    universalSkills: [
      {
        id: `${card.id}-skill`,
        cardType: '动作卡',
        title: card.learnContent.title,
        summary: card.summary || card.reason || card.practiceTask,
        whyRelevant: card.whyNow || card.reason || '这是当前成长缺口最接近的一条练习。',
        checklist: [card.practiceTask].filter(Boolean),
        talkTrack: [],
        templateHint: card.learnContent.title,
        sourceKind: 'rule',
        expectedOutput: card.practiceTask,
      },
    ],
    projectContextPack: buildFallbackProjectContextPack(card.clientName || card.eventLineName || card.theme, card.reason || card.practiceTask),
    actionPlan: [],
    materialRefs: [],
  };
}

function findPhaseByHint(value?: string | null): ProcessStep['name'] | null {
  const normalized = normalizeText(value);
  if (!normalized) return null;
  const matched = PROCESS_STEPS.find((step) => normalized.includes(step.name));
  return matched?.name || null;
}

function contextIdentity(context: GrowthContextLink) {
  return `${context.objectType}:${context.objectId}`;
}

function ensureTaskContext(label: string, subtitle: string, taskId?: string | null, contexts?: GrowthContextLink[]) {
  if (!taskId) return contexts || [];
  const taskContext = {
    objectType: 'task',
    objectId: taskId,
    label,
    subtitle,
    tab: 'tasks',
    statusLabel: '成长练习',
  } satisfies GrowthContextLink;
  if ((contexts || []).some((context) => context.objectType === 'task' && context.objectId === taskId)) {
    return contexts || [];
  }
  return [taskContext, ...(contexts || [])];
}

function buildTaskFromFocusAction(focus: GrowthFocusAction, index: number): WorkbenchTask {
  const phase = findPhaseByHint(focus.triggerNode || focus.projectStage || focus.summary || focus.title) || PHASE_BY_INDEX[Math.min(index + 2, PHASE_BY_INDEX.length - 1)];
  const heuristicText = `${focus.title}${focus.summary}${focus.whyNow}`;
  const robotReady = /(模板|清单|纪要|生成|对齐|跟踪|排查|草案)/.test(heuristicText);
  return {
    id: `focus-${focus.id}`,
    title: focus.title,
    project: focus.clientName || focus.eventLineName || focus.triggerNode || '成长焦点',
    deadline: focus.linkedTaskId ? '跟随当前任务' : '本周补动作',
    urgency: /风险|卡住|返工|阻塞|现在/.test(focus.whyNow) ? '建议优先处理' : '需先补关键动作',
    urgencyColor: /风险|卡住|返工|阻塞|现在/.test(focus.whyNow) ? 'text-red-700 bg-red-100' : 'text-orange-700 bg-orange-100',
    phase,
    risks: [focus.whyNow || focus.summary || '当前动作还没有稳定落到真实任务中。'],
    nextAdvice: focus.summary || focus.whyNow || `先围绕${focus.title}补一条可执行动作。`,
    robotReady,
    robotReasons: robotReady
      ? ['当前动作有清晰输出', '已匹配到可复用练习或模板', '适合先让机器人生成草案再人工判断']
      : ['仍需要人工结合现场判断', '当前动作更偏策略或协作博弈，不适合直接自动执行'],
    recommendationId: null,
    linkedTaskId: focus.linkedTaskId ?? null,
    linkedContexts: ensureTaskContext(focus.title, focus.projectStage || focus.eventLineName || focus.clientName || '当前焦点', focus.linkedTaskId, focus.linkedContexts),
    xpReward: 20,
    contextSummary: focus.summary,
    projectModuleName: null,
    projectFlowName: focus.triggerNode || null,
    sourceEvidence: [focus.whyNow || focus.summary].filter(Boolean),
    currentBlocker: focus.whyNow,
    missingSignals: [focus.whyNow].filter(Boolean),
    hasBackground: true,
    hasDeadline: false,
    isCrossDepartment: Boolean(focus.eventLineId || focus.clientId),
    needsReview: false,
    evidenceCount: 1,
    pendingCollaborations: 0,
    taskIntent: buildFallbackTaskIntent(
      'focus_action',
      focus.summary || focus.title,
      focus.triggerNode || '当前焦点动作',
      focus.whyNow || '这条动作被识别为当前最值得补的一步。',
      ['exec', 'collab'],
    ),
    universalSkills: [],
    projectContextPack: buildFallbackProjectContextPack(focus.clientName || focus.eventLineName || focus.title, focus.summary || focus.whyNow),
    actionPlan: [],
    materialRefs: [],
  };
}

function buildTaskFromPendingCapture(capture: GrowthPendingCapture, index: number): WorkbenchTask {
  const phase = findPhaseByHint(capture.projectStage || capture.nextActionText || capture.summary) || (capture.sourceType === 'task_attachment_candidate' ? '信息核对' : PHASE_BY_INDEX[Math.min(index + 3, PHASE_BY_INDEX.length - 1)]);
  return {
    id: `capture-${capture.id}`,
    title: capture.title,
    project: capture.clientName || capture.eventLineName || '待放大成长',
    deadline: '等待闭环',
    urgency: capture.missingReasons.some((reason) => /复盘|沉淀|闭环/.test(reason)) ? '需先补关键动作' : '可继续推进',
    urgencyColor: capture.missingReasons.some((reason) => /复盘|沉淀|闭环/.test(reason)) ? 'text-orange-700 bg-orange-100' : 'text-green-700 bg-green-100',
    phase,
    risks: capture.missingReasons.length ? capture.missingReasons.slice(0, 2) : [capture.summary || '系统已经识别到成长信号，但还缺最终闭环。'],
    nextAdvice: capture.nextActionText || capture.summary || '先补资料、复盘或沉淀，再把这条成长放大。 ',
    robotReady: false,
    robotReasons: ['当前更适合先由人补资料、复盘或沉淀说明', '这类信号需要解释层，不适合只靠自动执行完成'],
    recommendationId: null,
    linkedTaskId: capture.linkedContexts.find((context) => context.objectType === 'task')?.objectId ?? null,
    linkedContexts: capture.linkedContexts,
    xpReward: 16,
    contextSummary: capture.summary,
    projectModuleName: null,
    projectFlowName: capture.projectStage || null,
    sourceEvidence: capture.missingReasons,
    currentBlocker: capture.missingReasons[0] || null,
    missingSignals: capture.missingReasons,
    hasBackground: true,
    hasDeadline: false,
    isCrossDepartment: Boolean(capture.eventLineId || capture.clientId),
    needsReview: capture.missingReasons.some((reason) => /复盘|解释|说明/.test(reason)),
    evidenceCount: 1,
    pendingCollaborations: 0,
    taskIntent: buildFallbackTaskIntent(
      'pending_capture',
      capture.nextActionText || capture.summary || '把这条成长候选放大成正式沉淀',
      '一条完成闭环的成长记录',
      capture.stateReason || '系统已经看到信号，但还缺最后的解释或沉淀。',
      ['write', 'analyze'],
    ),
    universalSkills: [],
    projectContextPack: buildFallbackProjectContextPack(capture.clientName || capture.eventLineName || capture.title, capture.summary, {
      contextGaps: capture.missingReasons,
    }),
    actionPlan: [],
    materialRefs: [],
  };
}

function normalizeText(value?: string | null) {
  return (value ?? '').trim();
}

function parseTaskDate(value?: string | null) {
  if (!value) return null;
  const candidate = value.length <= 10 ? `${value}T00:00:00` : value;
  const date = new Date(candidate);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatTaskDeadline(task: Task) {
  const raw = task.dueDate || task.ddl;
  if (!raw) return '待补日期';
  const date = parseTaskDate(raw);
  if (!date) return raw;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  date.setHours(0, 0, 0, 0);
  const diffDays = Math.round((date.getTime() - today.getTime()) / 86400000);
  if (diffDays < 0) return `已超期 ${Math.abs(diffDays)} 天`;
  if (diffDays === 0) return '今天';
  if (diffDays === 1) return '明天';
  if (diffDays <= 7) return `${diffDays} 天后`;
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

function inferTaskPhase(task: Task): ProcessStep['name'] {
  const blockedStep = normalizeText(task.orgContext?.blockedAtStep);
  const haystack = `${task.title} ${task.desc} ${task.note ?? ''} ${blockedStep}`;
  if (/需求|接收|收件|待接收/.test(haystack) || task.status === 'inbox') return '需求接收';
  if (/信息|资料|材料|核对|澄清/.test(haystack)) return '信息核对';
  if (/对齐|会议|纪要|评审/.test(haystack)) return '内部对齐';
  if (/方案|白皮书|提案|文档|大纲|写作|输出/.test(haystack)) return '方案产出';
  if (/沟通|协调|协作|推进|谈判|资源/.test(haystack)) return '沟通推进';
  if (/交付|验收|上线|发布|闭环/.test(haystack)) return '交付闭环';
  if (task.status === 'done') return '复盘沉淀';
  if (task.status === 'doing') return task.orgContext?.isCrossDepartment ? '沟通推进' : '交付闭环';
  return task.orgContext?.isCrossDepartment || task.collaborators.length > 0 ? '内部对齐' : '信息核对';
}

function buildUrgencyMeta(task: Task) {
  const dueDate = parseTaskDate(task.dueDate || task.ddl);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diffDays = dueDate ? Math.round((new Date(dueDate.setHours(0, 0, 0, 0)).getTime() - today.getTime()) / 86400000) : null;
  if (diffDays !== null && diffDays < 0) {
    return { urgency: '建议优先处理', urgencyColor: 'text-red-700 bg-red-100' };
  }
  if (task.priority === 'high' || (diffDays !== null && diffDays <= 2)) {
    return { urgency: '建议优先处理', urgencyColor: 'text-red-700 bg-red-100' };
  }
  if (task.viewerInboxStatus === 'pending' || task.orgContext?.needsReview || task.orgContext?.blockedAtStep) {
    return { urgency: '需先补关键动作', urgencyColor: 'text-orange-700 bg-orange-100' };
  }
  return { urgency: '可直接推进', urgencyColor: 'text-green-700 bg-green-100' };
}

function buildTaskRisks(task: Task, phase: ProcessStep['name']) {
  const risks: string[] = [];
  if (!normalizeText(task.desc) && !normalizeText(task.note)) {
    risks.push('任务背景信息偏少，开始前建议先补齐目标、上下文和预期输出。');
  }
  if (!task.dueDate && !task.ddl) {
    risks.push('截止时间尚未明确，推进节奏容易在中途松掉。');
  }
  if (task.orgContext?.isCrossDepartment || task.collaborators.length > 0) {
    risks.push('涉及多人或跨部门协作，如果不先对齐边界和责任人，后续容易返工。');
  }
  if (task.viewerInboxStatus === 'pending' || (task.collaborationSummary?.pending ?? 0) > 0) {
    risks.push('仍有协作者未完成接收确认，关键动作可能停在等待。');
  }
  if (task.orgContext?.needsReview) {
    risks.push('当前任务仍需要复核或审批，建议先补齐说明与证据。');
  }
  if (task.status === 'inbox') {
    risks.push('任务还停留在待接收，若不尽快确认信息，容易拖成被动响应。');
  }
  if (risks.length > 0) return risks.slice(0, 2);
  const defaults: Record<ProcessStep['name'], string> = {
    需求接收: '需求来源和目标对象还未完全确认，过早执行容易方向跑偏。',
    信息核对: '关键信息口径若未先统一，后续材料和决策会反复返工。',
    内部对齐: '参会人、边界和预期结论不清楚时，会议很容易变成信息交换。',
    方案产出: '结构与受众若不匹配，方案会花很多时间在重写上。',
    沟通推进: '关键利益方未提前识别时，推进节点最容易卡在协作博弈上。',
    交付闭环: '只推进动作不收责任人和时间点，容易在最后一步失去闭环。',
    复盘沉淀: '如果只记录结果不提炼方法，这次经验很难转成下次可复用资产。',
  };
  return [defaults[phase]];
}

function buildRobotAssessment(task: Task, phase: ProcessStep['name']) {
  const contextSignals = [normalizeText(task.desc), normalizeText(task.note), task.tags.length ? 'tags' : '', task.dueDate || task.ddl || '']
    .filter(Boolean)
    .length;
  const haystack = `${task.title}${task.desc}${task.note ?? ''}`;
  const standardizable = /(会议|纪要|清单|模板|方案|提纲|白皮书|复盘|风险|对齐|材料|SOP|文档)/.test(haystack);
  const humanHeavy = task.orgContext?.isCrossDepartment || /(协调|沟通|谈判|客户|资源|博弈|冲突)/.test(haystack);
  const robotReady = contextSignals >= 2 && standardizable && !humanHeavy && task.status !== 'inbox';
  if (robotReady) {
    return {
      robotReady: true,
      robotReasons: ['任务上下文已补齐到可生成首稿', `当前处在${phase}阶段，标准输出较明确`, '可先由机器人生成准备清单或文档草稿'],
    };
  }
  const reasons = [];
  if (contextSignals < 2) reasons.push('任务描述、备注或截止信息仍不够完整');
  if (humanHeavy) reasons.push('当前阶段强依赖跨部门或现场判断，暂不适合全自动执行');
  if (!standardizable) reasons.push('任务输出结构还不够标准化，机器人难以稳定接手');
  return {
    robotReady: false,
    robotReasons: reasons.slice(0, 3).length > 0 ? reasons.slice(0, 3) : ['当前任务仍需要人先定调，再适合让机器人协助执行'],
  };
}

function buildNextAdvice(task: Task, phase: ProcessStep['name']) {
  const taskName = `「${task.title}」`;
  switch (phase) {
    case '需求接收':
      return `先为${taskName}确认目标对象、优先级和成功标准，再进入执行。`;
    case '信息核对':
      return `先补齐${taskName}所需的材料、数据和关键口径，再进入下一步。`;
    case '内部对齐':
      return `建议先把${taskName}的参会人、边界和预期结论写清楚，再开始拉会或对齐。`;
    case '方案产出':
      return `已具备开始条件，建议先为${taskName}拉出结构化大纲，再补细节。`;
    case '沟通推进':
      return `不要直接硬推，先把${taskName}的责任人、协作边界和时间线谈清楚。`;
    case '交付闭环':
      return `把${taskName}的交付物、待办和复核节点一起收拢，避免最后一步失焦。`;
    case '复盘沉淀':
      return `完成${taskName}后，尽快把有效做法沉淀成一条可复用经验。`;
    default:
      return `先补齐${taskName}的关键动作，再继续推进。`;
  }
}

function buildWorkbenchTaskFromTask(task: Task): WorkbenchTask {
  const phase = inferTaskPhase(task);
  const urgencyMeta = buildUrgencyMeta(task);
  const robotAssessment = buildRobotAssessment(task, phase);
  const linkedContexts: GrowthContextLink[] = [
    {
      objectType: 'task',
      objectId: task.id,
      label: task.title,
      subtitle: task.projectContext?.stage || task.eventLineName || task.clientName || task.listName,
      tab: 'tasks',
      statusLabel: task.status,
    },
  ];
  if (task.eventLineId && task.eventLineName) {
    linkedContexts.push({
      objectType: 'event_line',
      objectId: task.eventLineId,
      label: task.eventLineName,
      subtitle: task.businessCategory || task.projectContext?.stage || '事件线',
      tab: 'tasks',
      statusLabel: '事件线',
    });
  }
  if (task.clientId && task.clientName) {
    linkedContexts.push({
      objectType: 'client',
      objectId: task.clientId,
      label: task.clientName,
      subtitle: task.projectContext?.stage || task.businessCategory || '项目工作台',
      tab: 'client_workspace',
      statusLabel: '客户项目',
    });
  }
  const projectModuleId = task.projectContext?.projectModuleId || task.projectModuleId;
  const projectModuleName = task.projectContext?.projectModuleName || task.projectModuleName;
  if (projectModuleId && projectModuleName) {
    linkedContexts.push({
      objectType: 'project_module',
      objectId: projectModuleId,
      label: projectModuleName,
      subtitle: task.clientName || task.eventLineName || '项目模块',
      tab: 'tasks',
      statusLabel: '项目模块',
    });
  }
  const projectFlowId = task.projectContext?.projectFlowId || task.projectFlowId;
  const projectFlowName = task.projectContext?.projectFlowName || task.projectFlowName;
  if (projectFlowId && projectFlowName) {
    linkedContexts.push({
      objectType: 'project_flow',
      objectId: projectFlowId,
      label: projectFlowName,
      subtitle: task.projectContext?.stage || task.businessCategory || '流程节点',
      tab: 'tasks',
      statusLabel: '项目流程',
    });
  }
  const contextSummary = task.projectContext?.backgroundSummary || task.desc || task.note || '';
  const memoryHints = task.memoryHints || [];
  const linkedFactsPreview = task.linkedFactsPreview || [];
  const taskIntent = buildFallbackTaskIntent(
    /(协议|合同|条款|合作说明|说明迭代)/.test(`${task.title}${task.desc}`) ? 'agreement_alignment'
      : /(沟通|对接|访谈|老师|客户)/.test(`${task.title}${task.desc}`) ? 'external_communication'
      : /(会议|议程|纪要|评审)/.test(`${task.title}${task.desc}`) ? 'meeting_preparation'
      : /(方案|白皮书|提案|大纲|说明书)/.test(`${task.title}${task.desc}`) ? 'proposal_output'
      : 'general_execution',
    task.projectContext?.goalSummary || task.nextAction || buildNextAdvice(task, phase),
    task.projectContext?.projectFlowSummary || task.projectContext?.projectModuleSummary || task.nextAction || '一条明确的后续动作',
    task.currentBlocker || task.projectContext?.riskSummary || '系统根据当前任务字段推导了最小作战建议。',
    /(方案|白皮书|提案|大纲|说明书)/.test(`${task.title}${task.desc}`) ? ['write', 'analyze'] : ['collab', 'exec'],
  );
  const projectContextPack = buildFallbackProjectContextPack(task.clientName || task.eventLineName || task.title, contextSummary, {
    taskNotes: [task.desc, task.note || '', task.projectContext?.goalSummary || '', task.recentDecision || ''].map((item) => normalizeText(item)).filter(Boolean).slice(0, 4),
    attachments: task.attachments.map((item) => item.title).filter(Boolean).slice(0, 4),
    memoryHints: memoryHints.slice(0, 4),
    linkedFacts: linkedFactsPreview.map((item) => item.factValue).filter(Boolean).slice(0, 4),
    clientSummary: task.projectContext?.backgroundSummary || '',
    eventLineSummary: [task.eventLineName || '', task.projectContext?.currentFocus || '', task.projectContext?.currentBlocker || ''].map((item) => normalizeText(item)).filter(Boolean).join('；'),
    keyWarnings: task.projectContext?.riskSummary ? [task.projectContext.riskSummary] : [],
    contextGaps: [
      !normalizeText(task.desc) && !normalizeText(task.note) ? '缺任务背景说明' : '',
      !task.attachments.length && !linkedFactsPreview.length ? '缺附件或事实依据' : '',
      !task.clientId && !task.eventLineId ? '缺项目归属' : '',
    ].filter(Boolean),
  });
  return {
    id: task.id,
    title: task.title,
    project: task.projectContext?.projectFlowName || task.projectContext?.projectModuleName || task.eventLineName || task.projectContext?.clientName || task.clientName || task.listName || task.ownerName || '任务执行',
    deadline: formatTaskDeadline(task),
    urgency: urgencyMeta.urgency,
    urgencyColor: urgencyMeta.urgencyColor,
    phase,
    risks: buildTaskRisks(task, phase),
    nextAdvice: task.nextAction || task.projectContext?.nextAction || buildNextAdvice(task, phase),
    robotReady: robotAssessment.robotReady,
    robotReasons: robotAssessment.robotReasons,
    recommendationId: null,
    linkedTaskId: task.id,
    linkedContexts,
    xpReward: task.priority === 'high' ? 28 : task.priority === 'normal' ? 22 : 16,
    contextSummary,
    projectModuleName,
    projectFlowName,
    sourceEvidence: task.projectContext?.sourceEvidence || [],
    currentBlocker: task.currentBlocker || task.projectContext?.currentBlocker || task.orgContext?.blockedAtStep || null,
    missingSignals: [
      !normalizeText(task.desc) && !normalizeText(task.note) ? '缺任务背景说明' : '',
      !task.dueDate && !task.ddl ? '缺明确时间点' : '',
      (task.orgContext?.isCrossDepartment || task.collaborators.length > 0) ? '缺协作边界确认' : '',
      task.orgContext?.needsReview ? '缺复核说明' : '',
    ].filter(Boolean),
    hasBackground: Boolean(normalizeText(task.desc) || normalizeText(task.note) || normalizeText(task.projectContext?.backgroundSummary)),
    hasDeadline: Boolean(task.dueDate || task.ddl),
    isCrossDepartment: Boolean(task.orgContext?.isCrossDepartment || task.collaborators.length > 0),
    needsReview: Boolean(task.orgContext?.needsReview),
    evidenceCount: task.evidenceCount,
    pendingCollaborations: task.collaborationSummary?.pending ?? 0,
    taskIntent,
    universalSkills: [],
    projectContextPack,
    actionPlan: [],
    materialRefs: [],
  };
}

function buildSkillLabel(ability: AbilityWorkbenchCard) {
  if (ability.currentScore >= 75) return { label: '可放大', tone: 'bg-green-50 text-green-700 border-green-100' };
  if (ability.currentScore - ability.previousScore >= 12) return { label: '适合练一次', tone: 'bg-orange-50 text-orange-600 border-orange-100' };
  return { label: '需补动作', tone: 'bg-red-50 text-red-600 border-red-100' };
}

function sourceKindLabel(sourceKind: string) {
  const labels: Record<string, string> = {
    rule: '通用规则',
    project_context: '项目背景',
    ai_supplement: 'AI 补位',
    task_material: '任务材料',
    client_workspace: '客户工作台',
    event_line: '事件线',
    strategic_focus: '战略焦点',
  };
  return labels[sourceKind] || sourceKind;
}

function sourceKindTone(sourceKind: string) {
  if (sourceKind === 'rule') return 'bg-blue-50 text-blue-700 border-blue-100';
  if (sourceKind === 'project_context' || sourceKind === 'client_workspace' || sourceKind === 'event_line' || sourceKind === 'strategic_focus') {
    return 'bg-emerald-50 text-emerald-700 border-emerald-100';
  }
  if (sourceKind === 'ai_supplement') return 'bg-amber-50 text-amber-700 border-amber-100';
  return 'bg-slate-100 text-slate-600 border-slate-200';
}

function materialIcon(type: SupportMaterial['type']): LucideIcon {
  if (type === '流程说明') return BookOpen;
  if (type === '经验案例') return AlertTriangle;
  return FileText;
}

function processStepForPhase(phase: ProcessStep['name']) {
  return PROCESS_STEPS.find((step) => step.name === phase) || PROCESS_STEPS[2];
}

function normalizePhaseName(value?: string | null): ProcessStep['name'] {
  return PROCESS_STEPS.find((step) => step.name === value)?.name || '信息核对';
}

function contextsOverlap(left: GrowthContextLink[] = [], right: GrowthContextLink[] = []) {
  if (!left.length || !right.length) return false;
  const rightKeys = new Set(right.map(contextIdentity));
  return left.some((context) => rightKeys.has(contextIdentity(context)));
}

function workbenchTaskMatchesTask(
  task: WorkbenchTask,
  input: {
    linkedTaskId?: string | null;
    linkedContexts?: GrowthContextLink[];
    clientName?: string | null;
    eventLineName?: string | null;
    projectStage?: string | null;
  },
) {
  if (input.linkedTaskId && task.linkedTaskId && input.linkedTaskId === task.linkedTaskId) return true;
  if (contextsOverlap(task.linkedContexts || [], input.linkedContexts || [])) return true;
  if (normalizeText(input.eventLineName) && normalizeText(task.project).includes(normalizeText(input.eventLineName))) return true;
  if (normalizeText(input.clientName) && normalizeText(task.project).includes(normalizeText(input.clientName))) return true;
  const hintedPhase = findPhaseByHint(input.projectStage);
  if (hintedPhase && hintedPhase === task.phase) return true;
  return false;
}

function buildProcessSteps(
  task: WorkbenchTask,
  focusActions: GrowthFocusAction[],
  captures: GrowthPendingCapture[],
): ProcessStep[] {
  return PROCESS_STEPS.map((step) => {
    if (step.name === task.phase) {
      return {
        ...step,
        output: task.nextAdvice || focusActions[0]?.summary || task.contextSummary || step.output,
        bottlenecks:
          task.risks.length > 0
            ? task.risks.slice(0, 2)
            : captures.flatMap((capture) => capture.missingReasons).filter(Boolean).slice(0, 2).length
              ? captures.flatMap((capture) => capture.missingReasons).filter(Boolean).slice(0, 2)
              : step.bottlenecks,
      };
    }
    if (step.name === '复盘沉淀' && captures.length) {
      return {
        ...step,
        output: captures[0]?.nextActionText || step.output,
        bottlenecks: captures.flatMap((capture) => capture.missingReasons).filter(Boolean).slice(0, 2).length
          ? captures.flatMap((capture) => capture.missingReasons).filter(Boolean).slice(0, 2)
          : step.bottlenecks,
      };
    }
    return step;
  });
}

function buildProcessChecklist(
  task: WorkbenchTask,
  step: ProcessStep,
  focusActions: GrowthFocusAction[],
  captures: GrowthPendingCapture[],
) {
  const items = [
    `明确该节点的预期产出：${step.output}`,
    !task.hasBackground ? '补齐任务背景、目标和预期输出' : '',
    !task.hasDeadline ? '补齐明确的截止时间或推进节奏' : '',
    task.isCrossDepartment ? '把协作边界、责任人和时间点讲清楚' : '',
    task.pendingCollaborations > 0 ? `完成 ${task.pendingCollaborations} 个待确认协作动作` : '',
    task.needsReview ? '补复核说明、审批依据或验证证据' : '',
    task.evidenceCount <= 0 && ['信息核对', '方案产出', '交付闭环'].includes(step.name) ? '补关键材料、附件或事实依据' : '',
    focusActions[0] ? `把「${focusActions[0].title}」压进当前任务动作清单` : '',
    captures[0] ? `完成后处理「${captures[0].title}」的复盘或经验沉淀` : '',
  ].filter(Boolean) as string[];
  return Array.from(new Set(items)).slice(0, 5);
}

function buildSupportMaterials(
  task: WorkbenchTask,
  learningCards: LearningWorkbenchCard[],
  focusActions: GrowthFocusAction[],
  captures: GrowthPendingCapture[],
): SupportMaterial[] {
  const materials: SupportMaterial[] = [];
  if (task.projectFlowName || task.projectModuleName) {
    materials.push({
      id: `${task.id}-flow`,
      title: task.projectFlowName || task.projectModuleName || '当前项目流程说明',
      type: '流程说明',
      scenario: task.contextSummary || `适用于当前${task.phase}阶段`,
      summary: task.sourceEvidence?.[0] || task.nextAdvice,
      linkedContext: task.linkedContexts.find((context) => ['project_flow', 'project_module', 'task'].includes(context.objectType)) || null,
    });
  }
  if (learningCards[0]) {
    materials.push({
      id: `learning-${learningCards[0].id}`,
      title: learningCards[0].learnContent.title,
      type: learningCards[0].learnContent.type === '模板' ? '模板工具' : learningCards[0].learnContent.type === '方法卡' ? '流程说明' : '经验案例',
      scenario: learningCards[0].whyNow || learningCards[0].reason,
      summary: learningCards[0].practiceTask,
      linkedContext: learningCards[0].linkedContexts?.[0] || null,
    });
  }
  if (captures[0]) {
    materials.push({
      id: `capture-${captures[0].id}`,
      title: captures[0].title,
      type: '经验案例',
      scenario: captures[0].summary || captures[0].projectStage || '系统已识别到待放大的成长信号',
      summary: captures[0].missingReasons.join('；') || captures[0].nextActionText,
      linkedContext: captures[0].linkedContexts[0] || null,
    });
  }
  if (focusActions[0] && materials.length < 3) {
    materials.push({
      id: `focus-${focusActions[0].id}`,
      title: focusActions[0].title,
      type: '模板工具',
      scenario: focusActions[0].whyNow || focusActions[0].summary,
      summary: focusActions[0].summary,
      linkedContext: focusActions[0].linkedContexts[0] || null,
    });
  }
  if (!materials.length && task.sourceEvidence?.length) {
    materials.push({
      id: `${task.id}-evidence`,
      title: task.sourceEvidence[0] || '当前任务背景材料',
      type: '流程说明',
      scenario: task.contextSummary || '来自当前任务的项目背景',
      summary: task.nextAdvice,
      linkedContext: task.linkedContexts[0] || null,
    });
  }
  return materials.slice(0, 3);
}

function buildSupportCopy(task: WorkbenchTask, step: ProcessStep, captures: GrowthPendingCapture[]) {
  const title = task.isCrossDepartment
    ? '为什么这件事要先讲清边界与责任？'
    : !task.hasBackground
      ? '为什么开始前一定要先补齐上下文？'
      : step.name === '复盘沉淀'
        ? '为什么动作刚做完就要立刻沉淀？'
        : `为什么在「${step.name}」阶段要先补关键动作？`;
  const intro = task.isCrossDepartment
    ? '这类跨部门或多人任务最容易翻车的点，不是大家不努力，而是边界、责任人和时间点没有先被讲清楚。'
    : !task.hasBackground
      ? '系统已经识别到当前任务缺少背景、目标或预期输出。没有这些上下文，后续动作看起来很忙，但很容易做偏。'
      : captures.length
        ? '系统已经识别到这条任务里出现了可转化为成长的信号。如果不趁热补复盘或经验沉淀，这次有效动作很快就会丢掉。'
        : '任务学习页不是给你堆资料，而是先指出当前节点最应该补的关键动作。先把动作做对，再去扩写内容。';
  const bullets = [
    task.hasBackground ? '当前任务已经有基础背景，可以直接对齐关键动作。' : '先写清任务目标、对象和预期交付物。',
    task.hasDeadline ? '当前已经有时间点，下一步重点是把责任和边界讲清楚。' : '没有截止时间时，动作很容易在中途失焦。',
    task.isCrossDepartment ? '跨部门任务要优先处理协作边界，避免会后推诿返工。' : '单点任务更要先补事实依据和当前阶段判断。',
  ].filter(Boolean);
  return { title, intro, bullets: bullets.slice(0, 3) };
}

function buildRobotPlan(task: WorkbenchTask, step: ProcessStep, focusActions: GrowthFocusAction[], captures: GrowthPendingCapture[]) {
  const items = [
    `根据${task.project}的上下文，先拟一版「${step.name}」阶段动作清单`,
    task.currentBlocker ? `围绕当前卡点「${task.currentBlocker}」生成一版应对草案` : '',
    focusActions[0] ? `把推荐动作「${focusActions[0].title}」整理成可直接执行的脚本或清单` : '',
    captures[0] ? `预先生成「${captures[0].title}」对应的复盘或经验沉淀骨架` : '',
  ].filter(Boolean);
  return Array.from(new Set(items)).slice(0, 3);
}

function buildLearningSummaryFallback(task: WorkbenchTask, sourceMode: GrowthWorkbenchSnapshot['sourceMode']): GrowthLearningSummary {
  if (sourceMode === 'empty') {
    return {
      headline: '学习导航等待真实任务接入',
      whyItMatters: '系统需要真实任务、项目上下文或成长信号才能给出负责任的学习判断。',
      immediateMove: '前往任务与日历、客户工作台或战略陪伴创建一条真实对象，学习导航将自动激活。',
      generator: 'rules',
      confidence: 'low',
    };
  }
  if (sourceMode === 'growth_seed') {
    return {
      headline: '先把成长信号压成真实任务，再谈更深的项目判断。',
      whyItMatters: '当前更多来自成长推荐或待放大信号，还不是一条上下文完整的真实任务。',
      immediateMove: task.nextAdvice || '先把这条信号落成真实任务，并补齐背景、附件和责任人。',
      generator: 'rules',
      confidence: 'low',
    };
  }
  if (!task.hasBackground) {
    return {
      headline: '这次最该学的不是直接推进，而是先把任务背景、目标和边界补清楚。',
      whyItMatters: '如果目标、上下文和预期输出没说清，后续动作再多也会变成低质量忙碌。',
      immediateMove: task.nextAdvice,
      generator: 'rules',
      confidence: 'low',
    };
  }
  if (task.isCrossDepartment) {
    return {
      headline: '这次真正要学的是：多人协作里先收边界、责任人与时间线。',
      whyItMatters: '跨部门动作最怕默认别人会懂，真正的学习价值在于把协作边界收成可执行对象。',
      immediateMove: task.nextAdvice,
      generator: 'rules',
      confidence: task.evidenceCount > 0 ? 'medium' : 'low',
    };
  }
  return {
    headline: '这次真正要学的是：先判断当前阶段最关键的一步，再推进动作。',
    whyItMatters: '任务学习页的价值不是多给动作，而是先说清这次任务真正值得学的判断。',
    immediateMove: task.nextAdvice,
    generator: 'rules',
    confidence: task.evidenceCount > 0 || task.sourceEvidence.length ? 'medium' : 'low',
  };
}

function buildGenericLessonsFallback(task: WorkbenchTask, learningCards: LearningWorkbenchCard[]): GrowthGenericLesson[] {
  if (learningCards.length > 0) {
    return learningCards.slice(0, 3).map((card) => ({
      id: `learning-${card.id}`,
      title: card.learnContent.title,
      judgment: card.reason || card.whyNow || card.practiceTask,
      applicableScene: card.projectStage || card.triggerNode || task.phase,
      whyItWorks: card.whyNow || card.reason || '这条方法来自近期真实成长推荐，可以直接作为当前任务的练习模板。',
      reuseHint: card.practiceTask || '把这条方法沉淀到成长手册或任务模板里。',
      linkedContext: card.linkedContexts?.[0] || task.linkedContexts?.[0] || null,
    }));
  }
  const defaults: GrowthGenericLesson[] = [];
  if (task.isCrossDepartment) {
    defaults.push({
      id: `${task.id}-lesson-collab`,
      title: '边界不清先补对齐话术',
      judgment: '跨组动作先把目标、交付边界和依赖讲清楚，再进入推进。',
      applicableScene: '多人协作、跨部门推进、需要共同确认责任时',
      whyItWorks: '协作问题大多不是执行力差，而是边界没被提前说清。',
      reuseHint: '把目标、责任人、时间点和依赖写进会前对齐模板。',
      linkedContext: task.linkedContexts?.[0] || null,
    });
  }
  defaults.push({
    id: `${task.id}-lesson-phase`,
    title: '先把当前阶段最关键的一步做对',
    judgment: task.nextAdvice,
    applicableScene: `当前处在「${task.phase}」阶段`,
    whyItWorks: '任务学习页不该把所有动作一次性抛给执行者，而要先把当前阶段最关键的一步说清楚。',
    reuseHint: '以后遇到同类阶段，先按这个判断来收目标、材料或边界。',
    linkedContext: task.linkedContexts?.[0] || null,
  });
  if (!task.hasBackground) {
    defaults.push({
      id: `${task.id}-lesson-background`,
      title: '开始前先补任务背景',
      judgment: '没有背景、目标和预期输出时，任何动作都容易做偏。',
      applicableScene: '任务说明偏少、附件不足、事件线不明确时',
      whyItWorks: '补背景是为了让后续判断更稳，不是为了把页面填满。',
      reuseHint: '下次建任务时先写清对象、目标和预期交付物。',
      linkedContext: task.linkedContexts?.[0] || null,
    });
  }
  return defaults.slice(0, 3);
}

function buildProjectGuidanceFallback(task: WorkbenchTask, sourceMode: GrowthWorkbenchSnapshot['sourceMode']): GrowthProjectGuidance[] {
  const items: GrowthProjectGuidance[] = [];
  if (sourceMode !== 'task') {
    items.push({
      id: `${task.id}-context-mode`,
      title: '当前还不是完整项目判断',
      judgment: '现在更多来自成长推荐或待放大信号，不是来自一条上下文完整的真实任务。',
      whySpecial: '没有真实任务、附件和事件线连续上下文时，系统只能给规则基础版建议。',
      guidanceType: 'context_gap',
      linkedContexts: task.linkedContexts || [],
      evidenceRefs: task.missingSignals || ['缺真实任务上下文'],
    });
  }
  if (task.projectFlowName || task.projectModuleName || task.project) {
    items.push({
      id: `${task.id}-project-specific`,
      title: '这个项目真正特殊的地方',
      judgment: task.currentBlocker || `当前动作挂在「${task.projectFlowName || task.projectModuleName || task.project}」下，判断标准不是把内容写满，而是让这个节点继续向前。`,
      whySpecial: '一旦任务已经有明确项目挂接，它就不是通用待办，而是某条业务线上的推进节点。',
      guidanceType: 'project_specific',
      linkedContexts: task.linkedContexts || [],
      evidenceRefs: [...task.sourceEvidence, ...(task.currentBlocker ? [task.currentBlocker] : [])].slice(0, 3),
    });
  }
  items.push({
    id: `${task.id}-stage-risk`,
    title: '当前阶段最容易返工的点',
    judgment: task.risks[0] || '当前阶段如果不先补关键动作，后面很容易返工。',
    whySpecial: '这条风险来自当前任务对象本身，而不是通用模板里的套话。',
    guidanceType: 'stage_risk',
    linkedContexts: task.linkedContexts || [],
    evidenceRefs: [...task.risks, ...task.missingSignals].slice(0, 3),
  });
  return items.slice(0, 3);
}

function buildReasoningTraceFallback(
  task: WorkbenchTask,
  sourceMode: GrowthWorkbenchSnapshot['sourceMode'],
  focusActions: GrowthFocusAction[],
  captures: GrowthPendingCapture[],
): GrowthReasoningTrace {
  const usedInputs = [
    ...(task.linkedContexts || []).slice(0, 4).map((context) => ({
      id: `${context.objectType}-${context.objectId}`,
      sourceType: ['task', 'event_line', 'client', 'project_module', 'project_flow'].includes(context.objectType) ? (context.objectType as GrowthReasoningTrace['usedInputs'][number]['sourceType']) : 'rule',
      label: context.label,
      detail: context.subtitle || context.statusLabel || '',
    })),
    ...focusActions.slice(0, 1).map((item) => ({
      id: `focus-${item.id}`,
      sourceType: 'focus_action' as const,
      label: item.title,
      detail: item.summary || item.whyNow,
    })),
    ...captures.slice(0, 1).map((item) => ({
      id: `capture-${item.id}`,
      sourceType: 'pending_capture' as const,
      label: item.title,
      detail: item.summary || item.nextActionText,
    })),
  ];
  const missingContext = Array.from(
    new Set(
      [
        ...task.missingSignals,
        sourceMode !== 'task' ? '当前没有真实任务上下文' : '',
        !task.linkedContexts.some((context) => context.objectType === 'event_line') ? '缺事件线连续上下文' : '',
        (!task.evidenceCount && task.sourceEvidence.length === 0) ? '缺附件或明确证据' : '',
        !task.hasBackground ? '缺任务背景说明' : '',
      ].filter(Boolean),
    ),
  );
  return {
    mode: 'rules_only',
    usedInputs: usedInputs.length
      ? usedInputs
      : [
          {
            id: 'rule-only',
            sourceType: 'rule',
            label: '规则推导基线',
            detail: '当前没有足够的真实对象输入，系统只能输出基础规则判断。',
          },
        ],
    evidenceRefs: Array.from(new Set([...task.sourceEvidence, ...(task.currentBlocker ? [task.currentBlocker] : []), ...task.risks])).slice(0, 6),
    missingContext,
    aiContribution: [],
    modelLabel: null,
    confidence: sourceMode === 'task' && task.hasBackground && (task.evidenceCount > 0 || task.sourceEvidence.length > 0) && missingContext.length <= 1 ? 'high' : missingContext.length >= 3 ? 'low' : 'medium',
  };
}

function buildRobotAssistFallback(task: WorkbenchTask): GrowthRobotAssist {
  const haystack = `${task.title}${task.project}${task.contextSummary}${task.currentBlocker ?? ''}`;
  const canDelegate = [
    /(会议|对齐|沟通|纪要)/.test(haystack) ? '会议议程初稿' : '',
    /(会议|对齐|沟通|纪要)/.test(haystack) ? '行动项清单' : '',
    /(方案|提案|白皮书|文档|大纲|写)/.test(haystack) ? '结构化大纲或首版文档骨架' : '',
    /(复盘|总结|方法|沉淀)/.test(haystack) ? '复盘骨架或方法卡初稿' : '',
    task.evidenceCount > 0 || task.sourceEvidence.length > 0 ? '材料整理与证据摘录' : '待确认问题清单',
  ].filter(Boolean);
  const mustStayHuman = [
    task.isCrossDepartment || task.pendingCollaborations > 0 ? '跨部门边界和责任分配' : '',
    /(客户|沟通|谈判|协调)/.test(haystack) ? '关键对象口径和现场判断' : '',
    task.needsReview ? '复核 / 审批结论' : '',
    '最终优先级和是否推进的拍板',
  ].filter(Boolean);
  return {
    ready: task.robotReady,
    canDelegate: Array.from(new Set(canDelegate)).slice(0, 3),
    mustStayHuman: Array.from(new Set(mustStayHuman)).slice(0, 3),
    why: Array.from(new Set(task.robotReasons)).slice(0, 3),
  };
}

function buildAfterActionCaptureFallback(task: WorkbenchTask, captures: GrowthPendingCapture[]): GrowthAfterActionCapture {
  if (captures[0]) {
    return {
      title: captures[0].title,
      summary: captures[0].summary || captures[0].nextActionText,
      experienceType: '待放大成长信号',
      recommendedWriteback: captures[0].eventLineName ? `优先写回事件线「${captures[0].eventLineName}」` : captures[0].clientName ? `优先写回客户「${captures[0].clientName}」` : '写回成长手册或项目经验库',
    };
  }
  return {
    title: `${task.title}：${task.phase} 阶段复盘`,
    summary: `记录这次在「${task.phase}」阶段的关键判断、有效动作、适用边界和下次可复用的方法。`,
    experienceType: task.isCrossDepartment || task.phase === '复盘沉淀' ? '方法卡' : '复盘卡',
    recommendedWriteback: task.project ? `优先写回「${task.project}」相关背景或成长手册` : '写回成长手册',
  };
}

function RocketIcon(props: React.ComponentProps<'svg'>) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z" />
      <path d="m12 15-3-3a22 22 0 0 1 3.82-13 1.5 1.5 0 0 0-1.83 2.5 19.3 19.3 0 0 0-3 3.5C6.19 6.85 4.38 9.07 3 11a19 19 0 0 0 6.13 6.13c1.93-1.38 4.15-3.19 6-5.01a19.3 19.3 0 0 0 3.5-3 1.5 1.5 0 0 0 2.5-1.83A22 22 0 0 1 15 12z" />
      <path d="m12 15 3 3" />
      <path d="m9 12 3 3" />
    </svg>
  );
}

export function GrowthLearningWorkbench({
  learningCards,
  abilityCards,
  dailyDrops,
  workbenchSnapshot,
  currentFocusActions = [],
  pendingCaptures = [],
  tasks: sourceTasks = [],
  flash,
  onScheduleRecommendation,
  onDismissRecommendation,
  schedulingRecommendationId,
  dismissingRecommendationId,
  onOpenComposer,
  onSeedComposer,
  onNavigate,
  onOpenContext,
}: GrowthLearningWorkbenchProps) {
  const realTasks = useMemo(() => {
    const statusRank: Record<Task['status'], number> = { doing: 0, todo: 1, inbox: 2, done: 3, rejected: 4 };
    const priorityRank: Record<Task['priority'], number> = { high: 0, normal: 1, low: 2 };
    return sourceTasks
      .filter((task) => !isPersonalOnlyTask(task))
      .filter((task) => task.status !== 'done' && task.status !== 'rejected')
      .sort((left, right) => {
        const statusDiff = statusRank[left.status] - statusRank[right.status];
        if (statusDiff !== 0) return statusDiff;
        const priorityDiff = priorityRank[left.priority] - priorityRank[right.priority];
        if (priorityDiff !== 0) return priorityDiff;
        const leftDue = parseTaskDate(left.dueDate || left.ddl)?.getTime() ?? Number.MAX_SAFE_INTEGER;
        const rightDue = parseTaskDate(right.dueDate || right.ddl)?.getTime() ?? Number.MAX_SAFE_INTEGER;
        if (leftDue !== rightDue) return leftDue - rightDue;
        return new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime();
      })
      .slice(0, 3)
      .map(buildWorkbenchTaskFromTask);
  }, [sourceTasks]);
  const normalizedWorkbenchSnapshot = useMemo(() => {
    if (!workbenchSnapshot) return null;
    return {
      tasks: workbenchSnapshot.tasks.map((task) => ({
        ...task,
        phase: normalizePhaseName(task.phase),
        taskIntent: task.taskIntent || EMPTY_TASK.taskIntent,
        universalSkills: task.universalSkills || [],
        projectContextPack: task.projectContextPack || EMPTY_TASK.projectContextPack,
        actionPlan: task.actionPlan || [],
        materialRefs: task.materialRefs || [],
      })) as WorkbenchTask[],
      processSteps: workbenchSnapshot.processSteps.map((step) => ({
        ...step,
        name: normalizePhaseName(step.name),
      })) as ProcessStep[],
      actionGroups: {
        before: workbenchSnapshot.actionsBefore.map((action) => ({
          ...action,
          context: action.linkedContext || null,
        })) as WorkbenchAction[],
        during: workbenchSnapshot.actionsDuring.map((action) => ({
          ...action,
          context: action.linkedContext || null,
        })) as WorkbenchAction[],
        after: workbenchSnapshot.actionsAfter.map((action) => ({
          ...action,
          context: action.linkedContext || null,
        })) as WorkbenchAction[],
      },
      supportMaterials: workbenchSnapshot.supportMaterials.map((material) => ({
        ...material,
        linkedContext: material.linkedContext || null,
      })) as SupportMaterial[],
      checklistItems: workbenchSnapshot.checklistItems,
      learningSummary: workbenchSnapshot.learningSummary,
      genericLessons: workbenchSnapshot.genericLessons || [],
      projectGuidance: workbenchSnapshot.projectGuidance || [],
      reasoningTrace: workbenchSnapshot.reasoningTrace,
      robotAssist: workbenchSnapshot.robotAssist,
      afterActionCapture: workbenchSnapshot.afterActionCapture,
      supportCopy: workbenchSnapshot.supportCopy,
      robotPlan: workbenchSnapshot.robotPlan,
      activeTaskId: workbenchSnapshot.activeTaskId || null,
      activeProcessId: workbenchSnapshot.activeProcessId || null,
      sourceMode: workbenchSnapshot.sourceMode,
    };
  }, [workbenchSnapshot]);
  const hasRealTaskContext = realTasks.length > 0;
  const tasks = useMemo(() => {
    if (normalizedWorkbenchSnapshot?.tasks.length) return normalizedWorkbenchSnapshot.tasks;
    if (hasRealTaskContext) return realTasks;
    const derived = [
      ...currentFocusActions.slice(0, 2).map((action, index) => buildTaskFromFocusAction(action, index)),
      ...learningCards.slice(0, 2).map((card, index) => buildTaskFromLearningCard(card, index, abilityCards[index])),
      ...pendingCaptures.slice(0, 2).map((capture, index) => buildTaskFromPendingCapture(capture, index)),
    ];
    const seen = new Set<string>();
    return derived.filter((item) => {
      const key = item.linkedTaskId || item.id;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    }).slice(0, 3);
  }, [abilityCards, currentFocusActions, hasRealTaskContext, learningCards, normalizedWorkbenchSnapshot, pendingCaptures, realTasks]);
  const hasRecommendationContext = !hasRealTaskContext && tasks.length > 0;
  const currentSourceMode: GrowthWorkbenchSnapshot['sourceMode'] = normalizedWorkbenchSnapshot?.sourceMode || (hasRealTaskContext ? 'task' : tasks.length ? 'growth_seed' : 'empty');
  const [activeTaskId, setActiveTaskId] = useState(tasks[0]?.id || EMPTY_TASK.id);
  const [activeProcessId, setActiveProcessId] = useState(processStepForPhase(tasks[0]?.phase || EMPTY_TASK.phase).id);
  const [modalType, setModalType] = useState<ModalType>(null);
  const lastTaskIdRef = useRef<string | null>(null);

  const activeTask = useMemo(() => tasks.find((task) => task.id === activeTaskId) || tasks[0] || EMPTY_TASK, [activeTaskId, tasks]);
  const relatedLearningCards = useMemo(
    () =>
      learningCards.filter((card) =>
        workbenchTaskMatchesTask(activeTask, {
          linkedTaskId: card.linkedTaskId,
          linkedContexts: card.linkedContexts,
          clientName: card.clientName,
          eventLineName: card.eventLineName,
          projectStage: card.projectStage || card.triggerNode,
        }),
      ).slice(0, 3),
    [activeTask, learningCards],
  );
  const relatedFocusActions = useMemo(
    () =>
      currentFocusActions.filter((action) =>
        workbenchTaskMatchesTask(activeTask, {
          linkedTaskId: action.linkedTaskId,
          linkedContexts: action.linkedContexts,
          clientName: action.clientName,
          eventLineName: action.eventLineName,
          projectStage: action.projectStage || action.triggerNode,
        }),
      ).slice(0, 3),
    [activeTask, currentFocusActions],
  );
  const relatedCaptures = useMemo(
    () =>
      pendingCaptures.filter((capture) =>
        workbenchTaskMatchesTask(activeTask, {
          linkedContexts: capture.linkedContexts,
          clientName: capture.clientName,
          eventLineName: capture.eventLineName,
          projectStage: capture.projectStage,
        }),
      ).slice(0, 3),
    [activeTask, pendingCaptures],
  );
  const learningSummary = useMemo(
    () => normalizedWorkbenchSnapshot?.learningSummary || buildLearningSummaryFallback(activeTask, currentSourceMode),
    [activeTask, currentSourceMode, normalizedWorkbenchSnapshot],
  );
  const genericLessons = useMemo(
    () => (normalizedWorkbenchSnapshot?.genericLessons?.length ? normalizedWorkbenchSnapshot.genericLessons : buildGenericLessonsFallback(activeTask, relatedLearningCards.length ? relatedLearningCards : learningCards)),
    [activeTask, learningCards, normalizedWorkbenchSnapshot, relatedLearningCards],
  );
  const projectGuidance = useMemo(
    () => (normalizedWorkbenchSnapshot?.projectGuidance?.length ? normalizedWorkbenchSnapshot.projectGuidance : buildProjectGuidanceFallback(activeTask, currentSourceMode)),
    [activeTask, currentSourceMode, normalizedWorkbenchSnapshot],
  );
  const reasoningTrace = useMemo(
    () => normalizedWorkbenchSnapshot?.reasoningTrace || buildReasoningTraceFallback(activeTask, currentSourceMode, relatedFocusActions, relatedCaptures),
    [activeTask, currentSourceMode, normalizedWorkbenchSnapshot, relatedCaptures, relatedFocusActions],
  );
  const robotAssist = useMemo(
    () => normalizedWorkbenchSnapshot?.robotAssist || buildRobotAssistFallback(activeTask),
    [activeTask, normalizedWorkbenchSnapshot],
  );
  const afterActionCapture = useMemo(
    () => normalizedWorkbenchSnapshot?.afterActionCapture || buildAfterActionCaptureFallback(activeTask, relatedCaptures),
    [activeTask, normalizedWorkbenchSnapshot, relatedCaptures],
  );
  const processSteps = useMemo(
    () => normalizedWorkbenchSnapshot?.processSteps.length ? normalizedWorkbenchSnapshot.processSteps : buildProcessSteps(activeTask, relatedFocusActions, relatedCaptures),
    [activeTask, normalizedWorkbenchSnapshot, relatedCaptures, relatedFocusActions],
  );
  const activeProcess = useMemo(
    () => processSteps.find((step) => step.id === activeProcessId) || processSteps.find((step) => step.name === activeTask.phase) || processSteps[2],
    [activeProcessId, activeTask.phase, processSteps],
  );

  useEffect(() => {
    setActiveTaskId((current) => {
      if (normalizedWorkbenchSnapshot?.activeTaskId && tasks.some((task) => task.id === normalizedWorkbenchSnapshot.activeTaskId)) {
        return normalizedWorkbenchSnapshot.activeTaskId;
      }
      return tasks.some((task) => task.id === current) ? current ?? tasks[0]?.id ?? EMPTY_TASK.id : tasks[0]?.id || EMPTY_TASK.id;
    });
  }, [normalizedWorkbenchSnapshot?.activeTaskId, tasks]);

  useEffect(() => {
    if (lastTaskIdRef.current !== activeTask.id) {
      lastTaskIdRef.current = activeTask.id;
      setActiveProcessId(
        normalizedWorkbenchSnapshot?.activeProcessId
          || (processSteps.find((step) => step.name === activeTask.phase) || processStepForPhase(activeTask.phase)).id,
      );
    }
  }, [activeTask.id, activeTask.phase, normalizedWorkbenchSnapshot?.activeProcessId, processSteps]);

  const actionGroups = useMemo<{ before: WorkbenchAction[]; during: WorkbenchAction[]; after: WorkbenchAction[] }>(
    () =>
      normalizedWorkbenchSnapshot?.actionGroups || {
        before: [
          {
            id: `${activeTask.id}-before-1`,
            title: relatedFocusActions[0]?.title || `开始前先定：${activeTask.title} 的目标与边界`,
            output: relatedFocusActions[0]?.summary || `${activeProcess.output}，并明确第一责任人`,
            scenario: `${activeTask.phase} 开始前`,
            actionLabel: activeTask.recommendationId ? (activeTask.robotReady ? '一键生成草案' : '排入练习') : '打开当前任务',
            supportTitle: '查看为什么要做这一步',
            detail: relatedFocusActions[0]?.whyNow || activeTask.contextSummary,
            kind: activeTask.recommendationId ? 'schedule' : 'task',
            recommendationId: activeTask.recommendationId,
          },
          {
            id: `${activeTask.id}-before-2`,
            title: activeTask.currentBlocker ? `优先处理卡点：${activeTask.currentBlocker}` : '识别风险：先排查最可能翻车的 2 个点',
            output: relatedCaptures[0]?.nextActionText || '关键争议点 + 一条可执行预案',
            scenario: '正式拉人或开工前',
            actionLabel: activeTask.currentBlocker ? '回到当前任务' : '先做风险排查',
            supportTitle: '查看常见翻车案例',
            detail: relatedCaptures[0]?.missingReasons[0] || activeTask.risks[0],
            kind: activeTask.currentBlocker ? 'task' : 'support',
          },
        ],
        during: [
          {
            id: `${activeTask.id}-during-1`,
            title: `执行中关键动作：稳住${activeTask.phase}`,
            output: activeTask.isCrossDepartment ? '各方认同的交付物、边界与时间线' : (relatedFocusActions[1]?.summary || activeProcess.output),
            scenario: '讨论开始发散或推进变慢时',
            actionLabel: activeTask.isCrossDepartment ? '生成沟通话术' : '查看节点清单',
            supportTitle: activeTask.isCrossDepartment ? '查看沟通原理' : '查看节点标准',
            kind: 'support',
          },
        ],
        after: [
          {
            id: `${activeTask.id}-after-1`,
            title: relatedCaptures[0]?.title ? `完成后补强：${relatedCaptures[0].title}` : '完成后沉淀：把这次动作转成可复用经验',
            output: relatedCaptures[0]?.nextActionText || `一条可复用经验 + ${activeTask.xpReward} XP 的练习回流`,
            scenario: '动作完成后 2 小时内',
            actionLabel: relatedCaptures[0] ? '沉淀为经验' : '去记录经验',
            supportTitle: relatedCaptures[0]?.missingReasons[0] ? '查看为什么还没放大' : '查看标准沉淀方式',
            seedTitle: relatedCaptures[0]?.title,
            seedSummary: relatedCaptures[0]?.summary || relatedCaptures[0]?.nextActionText,
            kind: 'compose',
          },
        ],
      },
    [activeProcess.output, activeTask, normalizedWorkbenchSnapshot, relatedCaptures, relatedFocusActions],
  );

  const fallbackMaterials = useMemo<SupportMaterial[]>(
    () =>
      normalizedWorkbenchSnapshot?.supportMaterials
      || buildSupportMaterials(activeTask, relatedLearningCards.length ? relatedLearningCards : learningCards, relatedFocusActions, relatedCaptures),
    [activeTask, learningCards, normalizedWorkbenchSnapshot, relatedCaptures, relatedFocusActions, relatedLearningCards],
  );
  const universalMaterials = useMemo(
    () =>
      genericLessons.map((lesson) => ({
        id: `generic-${lesson.id}`,
        title: lesson.title,
        summary: lesson.judgment,
        scenario: lesson.applicableScene,
        linkedContext: lesson.linkedContext || null,
      })),
    [genericLessons],
  );
  const projectMaterials = useMemo(
    () =>
      fallbackMaterials
        .filter((material) => Boolean(material.linkedContext) || Boolean(material.summary))
        .slice(0, 3),
    [fallbackMaterials],
  );
  const processChecklist = useMemo(
    () => normalizedWorkbenchSnapshot?.checklistItems || buildProcessChecklist(activeTask, activeProcess, relatedFocusActions, relatedCaptures),
    [activeProcess, activeTask, normalizedWorkbenchSnapshot, relatedCaptures, relatedFocusActions],
  );
  const supportCopy = useMemo(
    () => normalizedWorkbenchSnapshot?.supportCopy || buildSupportCopy(activeTask, activeProcess, relatedCaptures),
    [activeProcess, activeTask, normalizedWorkbenchSnapshot, relatedCaptures],
  );
  const robotPlan = useMemo(
    () => normalizedWorkbenchSnapshot?.robotPlan || buildRobotPlan(activeTask, activeProcess, relatedFocusActions, relatedCaptures),
    [activeProcess, activeTask, normalizedWorkbenchSnapshot, relatedCaptures, relatedFocusActions],
  );
  const structuredActionPlan = useMemo<GrowthActionPlanItem[]>(
    () =>
      activeTask.actionPlan.length
        ? activeTask.actionPlan
        : [
            ...actionGroups.before.map((action) => ({
              id: action.id,
              phaseGroup: 'before' as const,
              title: action.title,
              purpose: action.detail || action.scenario,
              expectedOutput: action.output,
              ifMissing: action.detail || activeTask.risks[0] || '',
              actionLabel: action.actionLabel,
              sourceKind: 'rule' as const,
              linkedContext: action.context || undefined,
            })),
            ...actionGroups.during.map((action) => ({
              id: action.id,
              phaseGroup: 'during' as const,
              title: action.title,
              purpose: action.detail || action.scenario,
              expectedOutput: action.output,
              ifMissing: action.detail || activeTask.risks[0] || '',
              actionLabel: action.actionLabel,
              sourceKind: 'rule' as const,
              linkedContext: action.context || undefined,
            })),
            ...actionGroups.after.map((action) => ({
              id: action.id,
              phaseGroup: 'after' as const,
              title: action.title,
              purpose: action.detail || action.scenario,
              expectedOutput: action.output,
              ifMissing: action.detail || activeTask.risks[0] || '',
              actionLabel: action.actionLabel,
              sourceKind: 'rule' as const,
              linkedContext: action.context || undefined,
            })),
          ],
    [actionGroups.after, actionGroups.before, actionGroups.during, activeTask],
  );
  const structuredActionGroups = useMemo(
    () => ({
      before: structuredActionPlan.filter((item) => item.phaseGroup === 'before'),
      during: structuredActionPlan.filter((item) => item.phaseGroup === 'during'),
      after: structuredActionPlan.filter((item) => item.phaseGroup === 'after'),
    }),
    [structuredActionPlan],
  );
  const highlightedAbilities = useMemo(() => {
    const ranked = [...abilityCards].sort((left, right) => {
      const leftGap = left.currentScore - left.previousScore;
      const rightGap = right.currentScore - right.previousScore;
      return rightGap - leftGap;
    });
    return ranked.slice(0, 2);
  }, [abilityCards]);

  const experienceEchoes = useMemo(() => dailyDrops.slice(0, 2), [dailyDrops]);

  const preferredTaskContext = useMemo(() => {
    const contexts = activeTask.linkedContexts || [];
    return (
      contexts.find((context) => context.objectType === 'task')
      || contexts.find((context) => context.objectType === 'event_line')
      || contexts.find((context) => context.objectType === 'client')
      || contexts[0]
      || null
    );
  }, [activeTask.linkedContexts]);

  const openActiveTaskContext = () => {
    if (preferredTaskContext && onOpenContext) {
      onOpenContext(preferredTaskContext);
      return;
    }
    onNavigate?.('tasks');
    flash('success', '已打开任务页，可继续围绕这条真实任务推进动作');
  };

  const handleAction = async (action: WorkbenchAction) => {
    if (action.kind === 'schedule') {
      await onScheduleRecommendation(action.recommendationId);
      return;
    }
    if (action.kind === 'compose') {
      if (action.seedTitle && action.seedSummary && onSeedComposer) {
        onSeedComposer({ title: action.seedTitle, summary: action.seedSummary, sourceType: 'task' });
        return;
      }
      onOpenComposer();
      return;
    }
    if (action.kind === 'task') {
      if (action.context && onOpenContext) {
        onOpenContext(action.context);
        return;
      }
      openActiveTaskContext();
      return;
    }
    if (action.kind === 'process') {
      setModalType('process');
      return;
    }
    setModalType('support');
  };

  const handleActionPlanItem = (item: GrowthActionPlanItem) => {
    if (item.phaseGroup === 'after') {
      if (onSeedComposer) {
        onSeedComposer({
          title: activeTask.title,
          summary: item.expectedOutput || activeTask.nextAdvice,
          sourceType: 'task',
        });
        return;
      }
      onOpenComposer();
      return;
    }
    if (item.linkedContext && onOpenContext) {
      onOpenContext(item.linkedContext);
      return;
    }
    if (item.phaseGroup === 'during') {
      setModalType('support');
      return;
    }
    openActiveTaskContext();
  };

  const handleSecondaryAction = async (label: string) => {
    if (label === 'calendar') {
      setModalType('process');
      return;
    }
    if (label === 'assign') {
      setModalType(activeTask.robotReady ? 'robot' : 'support');
      return;
    }
    if (label === 'tasks') {
      openActiveTaskContext();
      return;
    }
    if (label === 'dismiss') {
      await onDismissRecommendation(activeTask.recommendationId);
      return;
    }
    flash('success', '当前动作已经进入任务学习页的执行清单');
  };

  return (
    <div className="animate-in space-y-6 fade-in duration-300">
      <section>
        <div className="mb-4 flex gap-3 overflow-x-auto pb-2">
          {tasks.map((task) => (
            <button
              key={task.id}
              type="button"
              onClick={() => setActiveTaskId(task.id)}
              className={cx(
                'w-64 flex-shrink-0 rounded-2xl border p-3 text-left transition-all',
                activeTask.id === task.id ? 'border-blue-600 bg-white shadow-md ring-1 ring-blue-600' : 'border-slate-200 bg-white opacity-75 hover:border-slate-300 hover:opacity-100',
              )}
            >
              <div className="mb-2 flex items-start justify-between gap-2">
                <h3 className="truncate text-sm font-semibold text-slate-900">{task.title}</h3>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs text-slate-500">{task.deadline}</span>
                <span className={cx('rounded px-1.5 py-0.5 text-[10px] font-medium', task.urgencyColor)}>{task.urgency}</span>
              </div>
            </button>
          ))}
        </div>
        {!tasks.length ? (
          <div className="mb-4 rounded-[24px] border border-dashed border-slate-200 bg-white px-5 py-8 text-center shadow-sm">
            <p className="text-[13px] font-bold text-slate-500">学习导航等待内容接入</p>
            <p className="mt-1 text-[12px] text-slate-400">在任务与日历创建任务、在客户工作台发起会议动作，或在战略陪伴添加成长推荐后，学习导航将自动补全阶段、风险和动作。</p>
          </div>
        ) : null}
        <div className="flex gap-4">
          <div className="relative flex-1 overflow-hidden rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
            <div className="pointer-events-none absolute right-0 top-0 text-slate-900 opacity-5">
              <Target className="translate-x-10 -translate-y-10 h-[200px] w-[200px]" />
            </div>
            <div className="relative z-10 flex h-full flex-col justify-center">
              <div className="mb-2 flex items-center gap-2">
                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">当前聚焦执行</span>
                <span className="text-sm text-slate-500">
                  {activeTask.project || '客户项目'} · 处在 <strong className="font-medium text-blue-600">{activeTask.phase}</strong> 阶段
                </span>
              </div>
              <h2 className="mb-6 text-2xl font-bold text-slate-900">{activeTask.title}</h2>
              <div className="mb-4 rounded-2xl border border-red-100 bg-red-50/80 p-4">
                <div className="mb-2 flex items-center gap-2">
                  <ShieldAlert className="h-4 w-4 text-red-600" />
                  <h4 className="text-sm font-bold text-red-900">这件事现在最容易做错的地方</h4>
                </div>
                <ul className="mb-3 list-disc space-y-1 pl-6 text-sm text-red-800">
                  {(activeTask.risks.length ? activeTask.risks : [learningSummary.whyItMatters]).slice(0, 3).map((risk, index) => (
                    <li key={`${activeTask.id}-risk-${index}`}>{risk}</li>
                  ))}
                </ul>
                <div className="flex items-center gap-2 rounded-lg bg-white/60 px-3 py-2 text-xs font-medium text-red-800">
                  <ArrowRight className="h-3.5 w-3.5" />
                  <span>{activeTask.nextAdvice || learningSummary.immediateMove}</span>
                </div>
              </div>
              {activeTask.linkedContexts.length ? (
                <div className="flex flex-wrap gap-2">
                  {activeTask.linkedContexts.slice(0, 3).map((context) => (
                    <button
                      key={`${activeTask.id}-${context.objectType}-${context.objectId}`}
                      type="button"
                      onClick={() => onOpenContext?.(context)}
                      className="rounded-full border border-[#D9E3FF] bg-[#F6F8FF] px-3 py-1.5 text-[12px] font-medium text-[#335CFE] transition hover:bg-[#EEF3FF]"
                    >
                      {context.label}
                      {context.subtitle ? <span className="ml-1 text-slate-400">· {context.subtitle}</span> : null}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          </div>

          <div className={cx('w-80 rounded-[28px] border p-5 transition-all', activeTask.robotReady ? 'border-emerald-200 bg-emerald-50 shadow-[0_0_15px_rgba(16,185,129,0.1)]' : 'border-slate-200 bg-slate-50')}>
            <div className="mb-3 flex items-center gap-2">
              <div className={cx('rounded-lg p-2', activeTask.robotReady ? 'bg-emerald-100 text-emerald-600' : 'bg-slate-200 text-slate-400')}>
                <Bot className="h-6 w-6" />
              </div>
              <div>
                <h3 className={cx('text-sm font-bold', activeTask.robotReady ? 'text-emerald-900' : 'text-slate-600')}>招聘机器人同事协助</h3>
                <p className="mt-1 text-[10px] uppercase tracking-wider text-slate-500">
                  {activeTask.robotReady ? 'AI AGENT READY' : 'CONDITION UNMET'}
                </p>
              </div>
            </div>

            {activeTask.robotReady ? (
              <>
                <p className="mb-4 flex-1 text-xs font-medium text-emerald-800">已满足此阶段自动执行条件，可指派机器人完成大部分标准化筹备工作。</p>
                <div className="mb-4 space-y-1.5">
                  {robotAssist.canDelegate.slice(0, 3).map((item, index) => (
                    <div key={`${activeTask.id}-delegate-${index}`} className="flex items-start gap-1.5 text-[11px] text-emerald-700/80">
                      <CheckCircle className="mt-0.5 h-3 w-3 shrink-0" />
                      <span>{item}</span>
                    </div>
                  ))}
                </div>
                <button
                  type="button"
                  onClick={() => setModalType('robot')}
                  className="flex w-full items-center justify-center gap-2 rounded-2xl bg-emerald-600 py-2.5 text-sm font-bold text-white shadow-sm transition hover:bg-emerald-700"
                >
                  <UserCheck className="h-4 w-4" />
                  <span>查看并录用机器人</span>
                </button>
              </>
            ) : (
              <>
                <p className="mb-4 flex-1 text-xs text-slate-500">当前任务暂不具备自动执行条件，需人类主导判断与执行。</p>
                <div className="mb-4 space-y-1.5 rounded-xl bg-white/60 p-3">
                  <p className="mb-1 text-[10px] font-bold text-slate-400">未满足原因：</p>
                  {robotAssist.mustStayHuman.slice(0, 3).map((item, index) => (
                    <div key={`${activeTask.id}-human-${index}`} className="flex items-start gap-1.5 text-[11px] text-slate-500">
                      <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-slate-300" />
                      <span>{item}</span>
                    </div>
                  ))}
                </div>
                <button
                  type="button"
                  disabled
                  className="w-full cursor-not-allowed rounded-2xl bg-slate-200 py-2.5 text-sm font-bold text-slate-400"
                >
                  仅支持辅助执行
                </button>
              </>
            )}
          </div>
        </div>
      </section>

      <section className="overflow-hidden rounded-[28px] border border-slate-200 bg-white shadow-sm">
        <div className="grid gap-0 xl:grid-cols-3 xl:divide-x xl:divide-slate-100">
          {([
            { key: 'before', title: '开始前必须确认', dot: 'bg-orange-500', cards: actionGroups.before, buttonTone: 'bg-blue-600 text-white hover:bg-blue-700', icon: Zap },
            { key: 'during', title: '执行中可调用', dot: 'bg-blue-500', cards: actionGroups.during, buttonTone: 'border border-indigo-200 bg-indigo-50 text-indigo-700 hover:bg-indigo-100', icon: MessageSquare },
            { key: 'after', title: '完成后沉淀', dot: 'bg-green-500', cards: actionGroups.after, buttonTone: 'bg-slate-100 text-slate-700 hover:bg-slate-200', icon: FileText },
          ] as const).map((section, sectionIndex) => {
            const ActionIcon = section.icon;
            return (
              <div key={section.key} className={cx('p-6', sectionIndex !== 1 && 'bg-slate-50/30')}>
                <h3 className="mb-4 flex items-center gap-2 text-sm font-bold text-slate-900">
                  <span className={cx('h-2 w-2 rounded-sm', section.dot)} />
                  {section.title}
                </h3>
                <div className="space-y-4">
                  {section.cards.length ? section.cards.map((action) => (
                    <div key={action.id} className="group relative overflow-hidden rounded-2xl border border-slate-200 bg-white p-4 transition hover:border-blue-400">
                      <div className="absolute left-0 top-0 h-full w-1 bg-slate-200 transition-colors group-hover:bg-blue-400" />
                      <h4 className="mb-1 pr-4 text-sm font-bold text-slate-800">{action.title}</h4>
                      <p className="mb-2 text-[11px] text-slate-500">适用场景：{action.scenario}</p>
                      <div className="mb-4 flex items-start gap-2 rounded-lg border border-slate-100 bg-slate-50 px-2.5 py-1.5 text-xs text-slate-700">
                        <Target className="mt-0.5 h-3.5 w-3.5 shrink-0 text-blue-500" />
                        <span>预期产出：<span className="font-medium">{action.output}</span></span>
                      </div>
                      <div className="flex items-center justify-between gap-3">
                        <button
                          type="button"
                          onClick={() => void handleAction(action)}
                          className={cx('inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition', section.buttonTone)}
                        >
                          <ActionIcon className="h-3 w-3" />
                          <span>{action.actionLabel}</span>
                        </button>
                        <button
                          type="button"
                          onClick={() => setModalType('support')}
                          className="text-[11px] text-slate-400 underline underline-offset-2 hover:text-blue-600"
                        >
                          {action.supportTitle}
                        </button>
                      </div>
                    </div>
                  )) : (
                    <div className="rounded-2xl border border-dashed border-slate-200 bg-white px-4 py-6">
                      <p className="text-[13px] font-bold text-slate-500">此阶段尚无动作卡</p>
                      <p className="mt-1 text-[12px] text-slate-400">补充任务描述或关联更多上下文后，系统会自动生成该阶段的动作建议。</p>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
        <div className="mb-8 flex items-center justify-between">
          <h3 className="text-base font-bold text-slate-900">岗位标准流转节点</h3>
        </div>

        <div className="relative mb-6 flex flex-wrap items-start justify-between gap-y-6">
          <div className="absolute left-0 top-2.5 hidden h-0.5 w-full -translate-y-1/2 bg-slate-100 md:block" />
          {processSteps.map((step) => {
            const isActive = activeProcess.id === step.id;
            return (
              <button key={step.id} type="button" onClick={() => setActiveProcessId(step.id)} className="relative z-10 flex min-w-[80px] flex-col items-center gap-2 text-center">
                {isActive ? <span className="absolute -top-7 rounded bg-blue-100 px-2 py-0.5 text-[10px] font-bold text-blue-700 shadow-sm">当前所处</span> : null}
                <div className={cx('h-5 w-5 rounded-full border-[3px] transition-all', isActive ? 'border-blue-600 bg-white shadow-[0_0_0_4px_rgba(37,99,235,0.1)]' : 'border-slate-300 bg-white hover:border-blue-400')} />
                <span className={cx('text-xs font-bold', isActive ? 'text-blue-700' : 'text-slate-500')}>{step.name}</span>
              </button>
            );
          })}
        </div>

        <div className="mt-8 flex flex-col gap-4 rounded-2xl bg-slate-50/60 p-4 md:flex-row md:items-center md:justify-between">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-blue-100 text-blue-600">
              <CheckSquare className="h-5 w-5" />
            </div>
            <div>
              <p className="text-sm font-bold text-slate-800">在【{activeProcess.name}】阶段应该做什么？</p>
              <p className="mt-1 text-xs leading-6 text-slate-500">此阶段必须产出：{activeProcess.output}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setModalType('process')}
            className="rounded-xl border border-slate-300 bg-white px-5 py-2 text-sm font-bold text-slate-700 shadow-sm transition hover:border-blue-500 hover:text-blue-600"
          >
            查看该节点标准动作清单
          </button>
        </div>
      </section>

      <div className="grid gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
        <section className="flex h-full flex-col rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
          <h3 className="mb-6 text-base font-bold text-slate-900">需调用技能补强</h3>
          <div className="flex-1 space-y-6">
            {highlightedAbilities.map((ability, index) => {
              const badge = buildSkillLabel(ability);
              const knowledgeScore = Math.min(100, Math.max(ability.currentScore, ability.previousScore + 18));
              const practiceScore = Math.max(12, Math.min(ability.currentScore, 100));
              return (
                <div key={ability.id} className={cx(index > 0 && 'border-t border-slate-100 pt-5')}>
                  <div className="mb-2 flex items-end justify-between gap-4">
                    <span className="text-sm font-bold text-slate-800">{ability.name}</span>
                    <span className={cx('rounded border px-1.5 py-0.5 text-[10px] font-medium', badge.tone)}>{badge.label}</span>
                  </div>
                  <div className="mb-3 flex items-center gap-2">
                    <span className="w-8 text-[10px] text-slate-400">认知 {knowledgeScore}</span>
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                      <div className="h-full bg-slate-300" style={{ width: `${knowledgeScore}%` }} />
                    </div>
                  </div>
                  <div className="mb-3 flex items-center gap-2">
                    <span className="w-8 text-[10px] text-slate-400">实战 {practiceScore}</span>
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                      <div className="h-full bg-blue-600" style={{ width: `${practiceScore}%` }} />
                    </div>
                  </div>
                  <p className="mb-3 text-[11px] leading-5 text-slate-500">{ability.evidence}</p>
                <button
                  type="button"
                  onClick={() => void (activeTask.recommendationId ? onScheduleRecommendation(activeTask.recommendationId) : handleSecondaryAction('tasks'))}
                  disabled={Boolean(activeTask.recommendationId && schedulingRecommendationId === activeTask.recommendationId)}
                  className="inline-flex w-full items-center justify-center gap-1.5 rounded-xl border border-blue-100/50 bg-blue-50 py-2 text-xs font-bold text-blue-600 transition hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-60"
                >
                    <Zap className="h-3 w-3" />
                    <span>{activeTask.recommendationId && schedulingRecommendationId === activeTask.recommendationId ? '处理中...' : `在当前任务中练一次`}</span>
                  </button>
                </div>
              );
            })}
          </div>
        </section>

        <div className="space-y-6">
          <section className="relative overflow-hidden rounded-[28px] bg-slate-900 p-6 text-white shadow-xl">
            <div className="absolute right-0 top-0 h-64 w-64 translate-x-1/2 -translate-y-1/2 rounded-full bg-blue-600/20 blur-3xl" />
            <div className="absolute bottom-0 left-0 h-40 w-40 -translate-x-1/2 translate-y-1/2 rounded-full bg-indigo-500/20 blur-2xl" />
            <div className="relative z-10 flex flex-col gap-6">
              <div>
                <h3 className="mb-2 flex items-center gap-2 text-xl font-bold">
                  <RocketIcon className="h-5 w-5" />
                  现在就推进这件事
                </h3>
                <p className="max-w-xl text-sm text-slate-400">别停在"我看完了"。选一个马上能执行的动作，把刚才的标准直接压回当前任务。</p>
              </div>

              <div className="flex flex-wrap gap-3">
                <button
                  type="button"
                  disabled={Boolean(activeTask.recommendationId && schedulingRecommendationId === activeTask.recommendationId)}
                  onClick={() => void (activeTask.recommendationId ? onScheduleRecommendation(activeTask.recommendationId) : handleSecondaryAction('tasks'))}
                  className="inline-flex items-center gap-2 rounded-2xl bg-blue-600 px-4 py-2.5 text-sm font-bold text-white shadow-lg transition hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <ListTodo className="h-4 w-4" />
                  <span>{activeTask.recommendationId && schedulingRecommendationId === activeTask.recommendationId ? '处理中...' : '针对此任务生成准备清单'}</span>
                </button>
                <button
                  type="button"
                  onClick={() => void handleSecondaryAction('calendar')}
                  className="inline-flex items-center gap-2 rounded-2xl border border-white/20 bg-white/10 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-white/20"
                >
                  <CalendarDays className="h-4 w-4" />
                  <span>查看节点清单</span>
                </button>
                <button
                  type="button"
                  onClick={() => void handleSecondaryAction('assign')}
                  className="inline-flex items-center gap-2 rounded-2xl border border-white/20 bg-white/10 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-white/20"
                >
                  <Users className="h-4 w-4" />
                  <span>{activeTask.robotReady ? '查看机器人可接手包' : '查看机器人协作边界'}</span>
                </button>
              </div>

              <div className="flex flex-wrap gap-3 text-xs text-white/80">
                <button
                  type="button"
                  onClick={() => void handleSecondaryAction('tasks')}
                  className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/5 px-3 py-1.5 font-medium transition hover:bg-white/10"
                >
                  <ArrowRight className="h-3.5 w-3.5" />
                  去任务页继续执行
                </button>
                {activeTask.recommendationId ? (
                  <button
                    type="button"
                    onClick={() => void handleSecondaryAction('dismiss')}
                    disabled={dismissingRecommendationId === activeTask.recommendationId}
                    className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/5 px-3 py-1.5 font-medium transition hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <X className="h-3.5 w-3.5" />
                    {dismissingRecommendationId === activeTask.recommendationId ? '忽略中...' : '先忽略这条推荐'}
                  </button>
                ) : null}
              </div>
            </div>
          </section>

          <section className="rounded-[28px] border border-slate-200 bg-white p-6 shadow-sm">
            <h3 className="mb-4 flex items-center justify-between text-sm font-bold text-slate-900">
              <span>如果你还不确定，可以看这 3 条辅助信息</span>
            </h3>
            <div className="space-y-2">
              {fallbackMaterials.length ? fallbackMaterials.map((material) => {
                const Icon = materialIcon(material.type);
                return (
                  <button
                    key={material.id}
                    type="button"
                    onClick={() => {
                      if (material.linkedContext && onOpenContext) {
                        onOpenContext(material.linkedContext);
                        return;
                      }
                      setModalType('support');
                    }}
                    className="group flex w-full items-center justify-between rounded-2xl border border-transparent p-3 text-left transition hover:border-slate-200 hover:bg-slate-50"
                  >
                    <div className="flex items-center gap-3">
                      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded bg-slate-100 text-slate-500">
                        <Icon className="h-4 w-4" />
                      </div>
                      <div>
                        <h4 className="text-sm font-medium text-slate-800 transition group-hover:text-blue-600">{material.title}</h4>
                        <p className="mt-0.5 text-[11px] text-slate-500">
                          {material.type} · 适用：{material.scenario}
                        </p>
                        {material.summary ? <p className="mt-1 text-[11px] leading-5 text-slate-400">{material.summary}</p> : null}
                      </div>
                    </div>
                    <span className="rounded bg-blue-50 px-3 py-1.5 text-xs font-medium text-blue-600 opacity-0 transition group-hover:opacity-100">
                      {material.linkedContext ? '打开对象' : '立即查看'}
                    </span>
                  </button>
                );
              }) : (
                <div className="rounded-[20px] border border-dashed border-slate-200 bg-slate-50 px-4 py-6">
                  <p className="text-[13px] font-bold text-slate-500">暂未匹配到参考材料</p>
                  <p className="mt-1 text-[12px] text-slate-400">丰富任务背景或添加附件后，系统会自动匹配流程说明、经验案例和模板工具。</p>
                </div>
              )}
            </div>
          </section>
        </div>
      </div>

      <section className="mt-10 rounded-t-[28px] border-t-4 border-slate-800 bg-white p-6 shadow-[0_-4px_10px_rgba(0,0,0,0.02)]">
        <div className="grid gap-8 lg:grid-cols-2">
          <div className="border-slate-100 pr-0 lg:border-r lg:pr-8">
            <h3 className="mb-4 flex items-center gap-2 text-sm font-bold text-slate-900">
              <Trophy className="h-4 w-4 text-slate-700" />
              实战应用与经验沉淀
            </h3>
            <div className="space-y-5">
              {experienceEchoes.length ? (
                experienceEchoes.map((echo, index) => (
                  <div key={echo.id} className={cx('relative pl-4', index === 0 ? 'border-l-2 border-green-500' : 'border-l-2 border-slate-200')}>
                    <div className={cx('absolute -left-[5px] top-1 h-2.5 w-2.5 rounded-full', index === 0 ? 'bg-green-500' : 'bg-slate-300')} />
                    <p className="text-sm text-slate-700">
                      在 <span className="font-bold text-slate-900">{echo.task}</span> 中记录了动作结果
                    </p>
                    <p className={cx('mt-1.5 flex items-center gap-1.5 text-xs', index === 0 ? 'text-slate-500' : 'text-slate-400')}>
                      {index === 0 ? <CheckCircle className="h-3 w-3 text-green-500" /> : null}
                      <span>
                        {index === 0 ? `成功转化为 1 条实战经验 · +${echo.xp} XP` : `${echo.time} · 动作已记录`}
                      </span>
                    </p>
                  </div>
                ))
              ) : (
                <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 px-4 py-6">
                  <p className="text-[13px] font-bold text-slate-500">实战回流等待第一条记录</p>
                  <p className="mt-1 text-[12px] text-slate-400">把上方任意一个动作排进日程并完成后，回流记录会自动出现在这里。</p>
                </div>
              )}
            </div>
          </div>

          <div className="pl-0 lg:pl-2">
            <h3 className="mb-4 text-sm font-bold text-slate-900">能力增幅表现</h3>
            <div className="mb-4 flex flex-col gap-4 sm:flex-row">
              {highlightedAbilities.slice(0, 1).map((ability) => (
                <div key={ability.id} className="flex-1 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3">
                  <div className="mb-1 text-xs text-slate-500">
                    因实战应用导致 <strong className="text-slate-700">{ability.name}</strong> 增长
                  </div>
                  <div className="text-xl font-bold text-slate-900">
                    +{Math.max(ability.numericInc, 6)} <span className="text-xs font-normal text-slate-500">实战经验值</span>
                  </div>
                </div>
              ))}
            </div>
            <div className="flex items-start gap-2 rounded-xl border border-blue-100 bg-blue-50/60 p-2.5 text-xs text-slate-600">
              <Zap className="mt-0.5 h-3.5 w-3.5 shrink-0 text-blue-500" />
              <span>
                下一步建议：你的"{activeTask.phase}"动作已经进入可执行阶段，当前最值得补做的是
                <strong>"{actionGroups.after[0]?.title || '把结果转成一条可复用经验'}"</strong>。
              </span>
            </div>
          </div>
        </div>
      </section>

      {modalType === 'robot' ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-sm">
          <div className="flex w-full max-w-xl flex-col overflow-hidden rounded-[28px] bg-white shadow-2xl">
            <div className="relative bg-emerald-600 p-6 text-white">
              <button type="button" onClick={() => setModalType(null)} className="absolute left-4 top-4 text-emerald-200 hover:text-white">
                <X className="h-5 w-5" />
              </button>
              <div className="flex items-center gap-4">
                <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-white/20">
                  <Bot className="h-8 w-8" />
                </div>
                <div>
                  <h2 className="text-xl font-bold">机器人可接手包</h2>
                  <p className="mt-1 text-sm text-emerald-100">先说清机器人能接什么、人必须拍板什么，以及当前为什么只能协助到这一步。</p>
                </div>
              </div>
            </div>

            <div className="max-h-[60vh] overflow-y-auto p-6">
              <div className="mb-6 rounded-xl border border-emerald-100 bg-emerald-50 p-3 text-sm font-medium text-emerald-800">
                我已读取当前任务"{activeTask.title}"的学习上下文。下面先区分机器人可接手的交付物、人必须拍板的部分，以及当前这样分工的原因。
              </div>
              <div className="space-y-5">
                <div>
                  <div className="mb-3 text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">机器人先做什么</div>
                  <ul className="relative space-y-3 before:absolute before:inset-y-2 before:left-[11px] before:w-0.5 before:bg-slate-100">
                    {robotAssist.canDelegate.map((item) => (
                      <li key={item} className="relative flex min-h-[24px] flex-col justify-center pl-8">
                        <div className="absolute left-0 z-10 flex h-6 w-6 items-center justify-center rounded-full border border-emerald-200 bg-emerald-100 text-emerald-600">
                          <CheckCircle className="h-3 w-3" />
                        </div>
                        <span className="text-sm font-bold text-slate-700">{item}</span>
                      </li>
                    ))}
                  </ul>
                </div>
                <div>
                  <div className="mb-3 text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">人必须拍板什么</div>
                  <div className="space-y-2">
                    {robotAssist.mustStayHuman.map((item) => (
                      <div key={item} className="rounded-xl border border-amber-100 bg-amber-50 px-3 py-2 text-[13px] leading-6 text-amber-900">
                        {item}
                      </div>
                    ))}
                  </div>
                </div>
                {robotAssist.why.length ? (
                  <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                    <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">为什么现在是这种分工</div>
                    <div className="space-y-2 text-[13px] leading-6 text-slate-600">
                      {robotAssist.why.map((item) => (
                        <div key={item}>{item}</div>
                      ))}
                    </div>
                  </div>
                ) : null}
                {robotPlan.length ? (
                  <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                    <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">可直接生成的首稿</div>
                    <div className="space-y-2 text-[13px] leading-6 text-slate-600">
                      {robotPlan.map((item) => (
                        <div key={item}>{item}</div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            </div>

            <div className="flex gap-3 border-t border-slate-100 bg-slate-50 p-4">
              <button
                type="button"
                onClick={async () => {
                  setModalType(null);
                  if (activeTask.recommendationId) {
                    await onScheduleRecommendation(activeTask.recommendationId);
                  } else {
                    await handleSecondaryAction('tasks');
                  }
                }}
                disabled={Boolean(activeTask.recommendationId && schedulingRecommendationId === activeTask.recommendationId)}
                className="flex-1 rounded-2xl bg-emerald-600 py-2.5 font-bold text-white shadow-sm transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {activeTask.recommendationId && schedulingRecommendationId === activeTask.recommendationId ? '处理中...' : '生成首稿并回到任务'}
              </button>
              <button type="button" onClick={() => setModalType(null)} className="flex-1 rounded-2xl border border-slate-300 bg-white py-2.5 font-bold text-slate-700 transition hover:bg-slate-50">
                先只看分工，不生成
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {modalType === 'process' ? (
        <div className="fixed inset-0 z-40 flex justify-end bg-slate-900/30 backdrop-blur-sm">
          <div className="flex h-full w-full max-w-[480px] flex-col bg-white shadow-2xl animate-in slide-in-from-right duration-300">
            <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50 px-6 py-4">
              <h2 className="font-bold text-slate-900">节点：{activeProcess.name} 标准清单</h2>
              <button type="button" onClick={() => setModalType(null)} className="p-1 text-slate-400 hover:text-slate-600">
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-6">
              <div className="mb-6 rounded-xl border border-blue-100 bg-blue-50 p-4">
                <h4 className="mb-1 text-xs font-bold uppercase tracking-wider text-blue-900">该节点必须输出</h4>
                <p className="text-sm font-medium text-blue-800">{activeProcess.output}</p>
              </div>

              <h4 className="mb-3 text-sm font-bold text-slate-900">必须完成的检查项</h4>
              <div className="mb-8 space-y-2">
                {processChecklist.map((item) => (
                  <label key={item} className="flex cursor-pointer items-center gap-3 rounded-lg border border-slate-200 p-3 hover:bg-slate-50">
                    <input type="checkbox" className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500" />
                    <span className="text-sm text-slate-700">{item}</span>
                  </label>
                ))}
              </div>

              <h4 className="mb-3 text-sm font-bold text-slate-900">此节点常见雷区</h4>
              <ul className="mb-8 space-y-2 pl-5 text-sm text-red-600">
                {activeProcess.bottlenecks.map((bottleneck) => (
                  <li key={bottleneck} className="list-disc">
                    {bottleneck}
                  </li>
                ))}
              </ul>

              <h4 className="mb-3 text-sm font-bold text-slate-900">可用模板工具</h4>
              <button
                type="button"
                onClick={() => {
                  const context = fallbackMaterials[0]?.linkedContext;
                  if (context && onOpenContext) {
                    onOpenContext(context);
                    setModalType(null);
                    return;
                  }
                  setModalType('support');
                }}
                className="group flex w-full items-center justify-between rounded-lg border border-slate-200 p-3 hover:bg-slate-50"
              >
                <span className="flex items-center gap-2 text-sm text-slate-700">
                  <FileText className="h-4 w-4 text-blue-500" />
                  {fallbackMaterials[0]?.title || '标准动作提报表'}
                </span>
                <span className="text-xs font-bold text-blue-600 opacity-0 transition group-hover:opacity-100">{fallbackMaterials[0]?.linkedContext ? '打开来源' : '一键调用'}</span>
              </button>
            </div>
            <div className="border-t border-slate-100 bg-white p-4">
              <button
                type="button"
                onClick={async () => {
                  setModalType(null);
                  if (activeTask.recommendationId) {
                    await onScheduleRecommendation(activeTask.recommendationId);
                  } else {
                    await handleSecondaryAction('tasks');
                  }
                }}
                disabled={Boolean(activeTask.recommendationId && schedulingRecommendationId === activeTask.recommendationId)}
                className="w-full rounded-2xl bg-slate-900 py-3 font-bold text-white disabled:cursor-not-allowed disabled:opacity-60"
              >
                {activeTask.recommendationId && schedulingRecommendationId === activeTask.recommendationId ? '处理中...' : '将以上清单加入当前任务'}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {modalType === 'support' ? (
        <div className="fixed inset-0 z-40 flex justify-end bg-slate-900/30 backdrop-blur-sm">
          <div className="flex h-full w-full max-w-[400px] flex-col bg-white shadow-2xl animate-in slide-in-from-right duration-300">
            <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50 px-6 py-4">
              <span className="text-xs font-bold uppercase tracking-wider text-slate-500">判断依据与沉淀建议</span>
              <button type="button" onClick={() => setModalType(null)} className="p-1 text-slate-400 hover:text-slate-600">
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-6">
              <h2 className="mb-4 text-xl font-bold text-slate-900">{learningSummary.headline}</h2>
              <div className="space-y-4 text-sm leading-7 text-slate-600">
                <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                  <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">当前判断模式</div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <span className={cx('rounded-full px-2.5 py-1 text-[11px] font-semibold', reasoningTrace.mode === 'ai_synthesized' ? 'bg-violet-100 text-violet-700' : 'bg-slate-200 text-slate-700')}>
                      {reasoningTrace.mode === 'ai_synthesized' ? 'AI 综合判断' : '规则推导基础版'}
                    </span>
                    <span className={cx('rounded-full px-2.5 py-1 text-[11px] font-semibold', reasoningTrace.confidence === 'high' ? 'bg-emerald-100 text-emerald-700' : reasoningTrace.confidence === 'medium' ? 'bg-amber-100 text-amber-700' : 'bg-rose-100 text-rose-700')}>
                      置信度 {reasoningTrace.confidence === 'high' ? '高' : reasoningTrace.confidence === 'medium' ? '中' : '低'}
                    </span>
                    {reasoningTrace.modelLabel ? (
                      <span className="rounded-full bg-white px-2.5 py-1 text-[11px] font-semibold text-slate-600">
                        模型 {reasoningTrace.modelLabel}
                      </span>
                    ) : null}
                  </div>
                  <p className="mt-3 text-[13px] leading-6 text-slate-600">
                    {reasoningTrace.mode === 'ai_synthesized'
                      ? '这次判断包含 AI 的提炼与比较，下面会列出模型实际用到的输入和结论缺口。'
                      : '这次判断主要来自规则基线：任务阶段、背景缺口、协作复杂度、附件/证据情况和显式阻塞。'}
                  </p>
                </div>

                <div>
                  <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">真正用到的输入</div>
                  <div className="space-y-2">
                    {reasoningTrace.usedInputs.slice(0, 6).map((item) => (
                      <div key={item.id} className="rounded-xl border border-slate-200 px-3 py-2">
                        <div className="text-[12px] font-semibold text-slate-800">{item.label}</div>
                        {item.detail ? <div className="mt-1 text-[12px] leading-5 text-slate-500">{item.detail}</div> : null}
                      </div>
                    ))}
                  </div>
                </div>

                <div>
                  <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">当前仍缺什么</div>
                  <div className="space-y-2">
                    {reasoningTrace.missingContext.length ? reasoningTrace.missingContext.map((item) => (
                      <div key={item} className="rounded-xl border border-amber-100 bg-amber-50 px-3 py-2 text-[12px] leading-5 text-amber-800">
                        {item}
                      </div>
                    )) : (
                      <div className="rounded-xl border border-emerald-100 bg-emerald-50 px-3 py-2 text-[12px] leading-5 text-emerald-700">
                        当前关键上下文已到位，可作为较稳的规则判断基础。
                      </div>
                    )}
                  </div>
                </div>

                {reasoningTrace.aiContribution.length ? (
                  <div>
                    <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">AI 这次具体做了什么</div>
                    <div className="space-y-2">
                      {reasoningTrace.aiContribution.map((item) => (
                        <div key={item} className="rounded-xl border border-violet-100 bg-violet-50 px-3 py-2 text-[12px] leading-5 text-violet-900">
                          {item}
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}

                {supportCopy.bullets.length ? (
                  <div>
                    <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">标准动作要求</div>
                    <ul className="space-y-2 pl-5">
                      {supportCopy.bullets.map((bullet) => (
                        <li key={bullet} className="list-disc">
                          {bullet}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                  <div className="text-[11px] font-bold uppercase tracking-[0.18em] text-slate-400">完成后应该沉淀成什么</div>
                  <div className="mt-2 text-sm font-semibold text-slate-900">{afterActionCapture.title}</div>
                  <p className="mt-2 text-[13px] leading-6 text-slate-600">{afterActionCapture.summary}</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <span className="rounded-full bg-white px-2.5 py-1 text-[11px] text-slate-600 ring-1 ring-slate-200">{afterActionCapture.experienceType}</span>
                    <span className="rounded-full bg-white px-2.5 py-1 text-[11px] text-slate-600 ring-1 ring-slate-200">{afterActionCapture.recommendedWriteback}</span>
                  </div>
                </div>
              </div>
            </div>
            <div className="border-t border-slate-100 bg-white p-6 text-center">
              <p className="mb-3 text-xs text-slate-500">看清依据和缺口后，再回到任务里推进</p>
              <button type="button" onClick={() => setModalType(null)} className="w-full rounded-2xl bg-blue-600 py-3 font-bold text-white shadow-sm">
                返回执行当前动作
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default GrowthLearningWorkbench;
