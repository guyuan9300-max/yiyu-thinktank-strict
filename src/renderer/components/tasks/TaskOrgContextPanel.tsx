import React from 'react';

import type { BackgroundReadiness, EmployeeRole, EventLine, Task, TaskContextPreview, TaskProjectContext } from '../../../shared/types';

type TaskOrgContextPanelProps = {
  task: Pick<Task, 'title' | 'desc' | 'projectModuleName' | 'projectFlowName' | 'sourceType' | 'orgContext' | 'projectContext' | 'eventLineName' | 'attachments' | 'memoryHints' | 'backgroundReadiness' | 'linkedFactsPreview'>;
  compact?: boolean;
  viewerRole?: EmployeeRole | null;
  eventLine?: Pick<EventLine, 'id' | 'name' | 'stage' | 'summary' | 'intent' | 'currentBlocker' | 'recentDecision' | 'nextStep' | 'status'> | null;
  contextPreview?: TaskContextPreview | null;
};

type InsightTone = 'neutral' | 'focus' | 'risk' | 'action' | 'opportunity';
type TaskMode = 'relationship' | 'deliverable' | 'materials' | 'decision' | 'analysis' | 'general';
type BusinessCategory = 'business_expansion' | 'delivery' | 'knowledge_base' | 'coordination' | 'analysis' | 'internal';

type InsightItem = {
  label: string;
  value: string;
  tone: InsightTone;
  order: number;
};

function pushUniqueInsight(target: InsightItem[], item: InsightItem | null) {
  if (!item?.value.trim()) return;
  if (target.some((existing) => existing.label === item.label && existing.value === item.value)) return;
  target.push(item);
}

function buildSummaryChips(
  categoryLabel?: string | null,
  projectContext?: TaskProjectContext | null,
  eventLineName?: string | null,
  eventLineStage?: string | null,
) {
  const chips: string[] = [];
  if (categoryLabel) chips.push(categoryLabel);
  if (eventLineName) chips.push(`事件线 · ${eventLineName}`);
  if (projectContext?.clientName) chips.push(`项目 · ${projectContext.clientName}`);
  if (eventLineStage) chips.push(`线索阶段 · ${eventLineStage}`);
  else if (projectContext?.stage) chips.push(`阶段 · ${projectContext.stage}`);
  if (projectContext?.projectModuleName) chips.push(`模块 · ${projectContext.projectModuleName}`);
  else if (projectContext?.projectFlowName) chips.push(`流程 · ${projectContext.projectFlowName}`);
  return chips.slice(0, 3);
}

function normalizeLineText(value?: string | null) {
  return (value || '').replace(/\s+/g, ' ').trim();
}

function isGenericAnalysisLine(value?: string | null, taskTitle?: string | null) {
  const normalized = normalizeLineText(value);
  if (!normalized) return true;
  const genericPatterns = [
    /^当前没有特别突出的阻塞/u,
    /^当前阻塞更像/u,
    /^当前阻塞仍需结合最近/u,
    /^最近进展仍待补充/u,
    /^最近进展：.+\s*\/\s*.+$/u,
    /^当前任务更具体的落点是/u,
    /^当前更具体的推进点是/u,
    /^当前重点仍待补充/u,
    /^下一步动作：先补齐项目背景/u,
    /项目背景、目标和流程线索/u,
    /挂进清晰的项目结构/u,
    /挂进明确模块或流程/u,
    /结构化归属不足/u,
    /可围绕既定目标继续推进/u,
    /已围绕.+持续推进/u,
  ];
  if (genericPatterns.some((pattern) => pattern.test(normalized))) return true;
  const normalizedTitle = normalizeLineText(taskTitle);
  if (normalizedTitle && normalized.includes(normalizedTitle) && normalized.length <= normalizedTitle.length + 24) return true;
  return false;
}

function truncateText(value: string, limit = 48) {
  if (value.length <= limit) return value;
  return `${value.slice(0, limit - 1).trim()}…`;
}

function compactInsightText(value?: string | null, limit = 38) {
  const normalized = normalizeLineText(value)
    .replace(/^(当前任务更具体的推进点是|当前更具体的推进点是|当前推进点是|当前落点是|当前主要风险|当前卡点|当前阻塞|主要阻塞|最近进展|下一步动作|下一步|背景|目标|风险|建议动作|最近已经明确)[:：]\s*/u, '')
    .replace(/^这条线(当前)?在推进[:：]?\s*/u, '')
    .replace(/^对于[^，。；;]+来说[，,]\s*/u, '')
    .replace(/^当前(不是|仍|更像|已经)?/u, '')
    .replace(/如果未来1-2周还不收束[，,]?\s*/u, '')
    .replace(/[。；;]+$/u, '')
    .trim();
  return truncateText(normalized, limit);
}

function splitTaskClauses(value?: string | null) {
  return (value || '')
    .split(/\n|[|｜]|[。；;]/)
    .map((item) => normalizeLineText(item))
    .map((item) => item.replace(/^(内部管理|客户项目|项目背景|背景|说明|备注|关联目标|当前事项|当前阻塞|最近进展|预期输出|下一步动作|下一步|结果|目标)[:：]\s*/u, ''))
    .filter(Boolean);
}

function pickClause(clauses: string[], patterns: RegExp[], fallbackToFirst = false) {
  for (const pattern of patterns) {
    const matched = clauses.find((item) => pattern.test(item));
    if (matched) return matched;
  }
  return fallbackToFirst ? (clauses[0] || '') : '';
}

function inferTaskMode(task: TaskOrgContextPanelProps['task']): TaskMode {
  const source = `${task.title} ${task.desc || ''}`;
  if (/(吃饭|见面|拜访|会谈|沟通|讨论|约见|会面|午餐|晚餐|聊|对接)/u.test(source)) return 'relationship';
  if (/(方案|文稿|报告|清单|框架|PPT|输出|提交|草稿|总结|邮件|交付)/u.test(source)) return 'deliverable';
  if (/(资料|整理|导入|补录|拍照|收集|归档|文件|台账|底稿|素材|信息库)/u.test(source)) return 'materials';
  if (/(确认|拍板|审批|复核|签字|决定|校对|认领)/u.test(source)) return 'decision';
  if (/(调研|分析|诊断|研究|洞察|判断|策略)/u.test(source)) return 'analysis';
  return 'general';
}

function inferBusinessCategory(
  task: TaskOrgContextPanelProps['task'],
  taskMode: TaskMode,
  eventLine?: TaskOrgContextPanelProps['eventLine'],
): BusinessCategory {
  const source = `${task.title} ${task.desc || ''} ${eventLine?.intent || ''} ${eventLine?.summary || ''}`;
  if (taskMode === 'relationship' || /(客户|基金会|合作|拓展|关系|会面|拜访|赋能|对接)/u.test(source)) return 'business_expansion';
  if (taskMode === 'deliverable') return 'delivery';
  if (taskMode === 'materials') return 'knowledge_base';
  if (taskMode === 'analysis') return 'analysis';
  if (taskMode === 'decision' || /(确认|审批|复核|协同|认领|拍板|校对)/u.test(source)) return 'coordination';
  return 'internal';
}

function businessCategoryLabel(category: BusinessCategory) {
  if (category === 'business_expansion') return '业务扩展';
  if (category === 'delivery') return '正式交付';
  if (category === 'knowledge_base') return '资料沉淀';
  if (category === 'coordination') return '协同确认';
  if (category === 'analysis') return '判断提炼';
  return '内部推进';
}

function buildModeFocus(
  category: BusinessCategory,
  taskMode: TaskMode,
  _taskTitle: string,
  moduleName?: string | null,
  flowName?: string | null,
) {
  const scope = flowName || moduleName || '当前事项';
  if (category === 'business_expansion') return `先把这次接触收成业务结论，落点放在${scope}`;
  if (category === 'delivery') return `先把这条线收成正式交付，落点放在${scope}`;
  if (category === 'knowledge_base') return `先把资料补成底盘，落点放在${scope}`;
  if (category === 'coordination') return `先把确认链前移，落点放在${scope}`;
  if (category === 'analysis') return `先把判断压成可执行结论，落点放在${scope}`;
  if (taskMode === 'relationship') return `先把关系推进收成明确结论，落点放在${scope}`;
  return `先把这件事收成明确结果，不再停在泛推进`;
}

function buildModeRisk(category: BusinessCategory, taskMode: TaskMode, taskTitle: string) {
  if (category === 'business_expansion') return `最大风险是只推进关系，没有沉成业务结论`;
  if (category === 'delivery') return `最大风险是口径不定，继续停在反复修改`;
  if (category === 'knowledge_base') return `最大风险是资料不成底盘，后续判断都会偏浅`;
  if (category === 'coordination') return `最大风险是确认链继续拖，后续动作都会顺带卡住`;
  if (category === 'analysis') return `最大风险是边界不清，继续分析却不落地`;
  if (taskMode === 'relationship') return `最大风险是关系在推进，业务没有收口`;
  return `${taskTitle}还缺清晰边界，投入容易继续摊薄`;
}

function buildModeOpportunity(category: BusinessCategory, taskMode: TaskMode, taskTitle: string, attachmentCount: number) {
  if (category === 'business_expansion') return `把会谈结论写实后，这条线就能转成下一步合作`;
  if (category === 'delivery') return `一旦成稿，这条线就会变成可复用交付资产`;
  if (category === 'knowledge_base') return `资料一旦补齐，后续判断会明显变准${attachmentCount > 0 ? `，已有 ${attachmentCount} 份证据可用` : ''}`;
  if (category === 'coordination') return `确认一旦完成，会直接释放后续推进空间`;
  if (category === 'analysis') return `判断一旦写实，后续沟通和决策会明显聚焦`;
  if (taskMode === 'relationship') return `把会谈沉成结论后，容易直接转成下一步合作`;
  return `只要尽快收束成结果，就能从零散动作变成推进线`;
}

function buildModeAction(category: BusinessCategory, taskMode: TaskMode, taskTitle: string) {
  if (category === 'business_expansion') return `先定结论、关键判断和会后跟进动作`;
  if (category === 'delivery') return `先定交付边界、口径和成稿责任`;
  if (category === 'knowledge_base') return `先补关键资料、证据和底层文件`;
  if (category === 'coordination') return `先定谁确认、何时确认、确认后谁推进`;
  if (category === 'analysis') return `先把判断压成一句结论和三个动作`;
  if (taskMode === 'relationship') return `先把会谈沉成结论，不要只停在交流`;
  return `先定义最小结果，再推进`;
}

function buildInsightAnchor(task: TaskOrgContextPanelProps['task']) {
  return [
    task.title,
    task.projectFlowName,
    task.projectModuleName,
    task.eventLineName,
    task.projectContext?.projectFlowName,
    task.projectContext?.projectModuleName,
  ]
    .map((item) => normalizeLineText(item))
    .find(Boolean) || '';
}

function prefixWithAnchor(anchor: string, value?: string | null) {
  const normalized = normalizeLineText(value);
  if (!normalized) return '';
  if (!anchor || normalized.includes(anchor)) return normalized;
  return `${anchor}：${normalized}`;
}

function hasSpecificTaskScope(task: TaskOrgContextPanelProps['task']) {
  return Boolean(
    normalizeLineText(task.eventLineName) ||
    task.projectContext?.projectModuleId ||
    task.projectContext?.projectFlowId,
  );
}

function hasMaterialProgress(task: TaskOrgContextPanelProps['task'], eventLine?: TaskOrgContextPanelProps['eventLine']) {
  return Boolean(
    task.attachments.length > 0 ||
    normalizeLineText(task.projectContext?.recentProgress) ||
    normalizeLineText(task.projectContext?.nextAction) ||
    normalizeLineText(task.projectContext?.currentBlocker) ||
    normalizeLineText(task.projectContext?.currentFocus) ||
    normalizeLineText(eventLine?.summary) ||
    normalizeLineText(eventLine?.currentBlocker) ||
    normalizeLineText(eventLine?.recentDecision) ||
    normalizeLineText(eventLine?.nextStep) ||
    normalizeLineText(eventLine?.intent),
  );
}

function buildContextRisk(
  task: TaskOrgContextPanelProps['task'],
  eventLine: TaskOrgContextPanelProps['eventLine'],
  anchor: string,
  hasSpecificScope: boolean,
) {
  const { projectContext, orgContext } = task;
  const eventLineBlocker = isGenericAnalysisLine(eventLine?.currentBlocker, task.title) ? '' : normalizeLineText(eventLine?.currentBlocker);
  const projectBlocker = isGenericAnalysisLine(projectContext?.currentBlocker, task.title) ? '' : normalizeLineText(projectContext?.currentBlocker);
  if (eventLineBlocker && hasSpecificScope) return prefixWithAnchor(anchor, eventLineBlocker);
  if (projectBlocker && hasSpecificScope) return prefixWithAnchor(anchor, projectBlocker);
  if (eventLine?.status === 'blocked') return `${anchor || '这条任务'}所属事件线当前处于受阻状态，建议先处理卡点再推进。`;
  if (eventLine?.status === 'paused') return `${anchor || '这条任务'}所属事件线当前处于暂停状态，推进前需要先确认是否继续。`;
  if (orgContext?.blockedAtStep) return `${anchor || '这条任务'}卡在 ${orgContext.blockedAtStep}`;
  if (hasSpecificScope && (orgContext?.approvalState === 'pending' || orgContext?.needsReview)) {
    return `${anchor || '这条任务'}仍卡在确认链，推进速度受复核影响`;
  }
  if (hasSpecificScope && orgContext?.isCrossDepartment) return `${anchor || '这条任务'}需要跨部门同步，节奏容易受他方反馈影响`;
  if (hasSpecificScope && projectContext?.infoCompleteness === 'low') return `${anchor || '这条任务'}的项目资料仍偏薄，判断深度和交付精度都受影响`;
  if (projectContext?.riskSummary && hasSpecificScope) return prefixWithAnchor(anchor, projectContext.riskSummary);
  return '';
}

function buildOpportunity(
  task: TaskOrgContextPanelProps['task'],
  eventLine: TaskOrgContextPanelProps['eventLine'],
  anchor: string,
  hasSpecificScope: boolean,
  hasProgressSignal: boolean,
) {
  const { projectContext, orgContext, attachments } = task;
  const projectProgress = isGenericAnalysisLine(projectContext?.recentProgress, task.title) ? '' : normalizeLineText(projectContext?.recentProgress);
  const eventLineSummary = isGenericAnalysisLine(eventLine?.summary, task.title) ? '' : normalizeLineText(eventLine?.summary);
  const eventLineDecision = isGenericAnalysisLine(eventLine?.recentDecision, task.title) ? '' : normalizeLineText(eventLine?.recentDecision);
  const eventLineIntent = isGenericAnalysisLine(eventLine?.intent, task.title) ? '' : normalizeLineText(eventLine?.intent);
  if (attachments.length > 0 && projectProgress) {
    return `${prefixWithAnchor(anchor, projectProgress)}，而且证据已开始沉淀，可继续放大为正式交付`;
  }
  if (projectProgress && hasSpecificScope) return prefixWithAnchor(anchor, projectProgress);
  if (eventLineSummary && hasSpecificScope) return prefixWithAnchor(anchor, eventLineSummary);
  if (attachments.length > 0) return `${anchor || '这条任务'}已沉淀 ${attachments.length} 份任务附件，可从零散动作转为可复用成果`;
  if (projectContext?.goalSummary && hasSpecificScope) return `${anchor || '这条任务'}当前对准：${projectContext.goalSummary}`;
  if (eventLineDecision && hasSpecificScope) return `${anchor || '这条任务'}最近已明确：${eventLineDecision}`;
  if (eventLineIntent && hasSpecificScope) return `${anchor || '这条任务'}正在对准：${eventLineIntent}`;
  if (hasProgressSignal && orgContext?.organizationFocusKey && hasSpecificScope) return `${anchor || '这条任务'}已经贴近机构焦点：${orgContext.organizationFocusKey}`;
  if (hasProgressSignal && orgContext?.departmentFocusKey && hasSpecificScope) return `${anchor || '这条任务'}已经贴近部门焦点：${orgContext.departmentFocusKey}`;
  return '';
}

function buildInsights(
  task: TaskOrgContextPanelProps['task'],
  viewerRole: EmployeeRole | null | undefined,
  compact: boolean,
  eventLine?: TaskOrgContextPanelProps['eventLine'],
) {
  const insights: InsightItem[] = [];
  const { projectContext, orgContext, attachments = [] } = task;
  const isAdminView = viewerRole === 'admin';
  const anchor = buildInsightAnchor(task);
  const taskTitle = truncateText(normalizeLineText(task.title) || '这条任务', 28);
  const taskMode = inferTaskMode(task);
  const category = inferBusinessCategory(task, taskMode, eventLine);
  const clauses = splitTaskClauses(task.desc);
  const hasSpecificScope = hasSpecificTaskScope(task);
  const hasProgressSignal = hasMaterialProgress(task, eventLine);
  const taskFocusText = pickClause(clauses, [/预期输出/u, /推进/u, /形成/u, /梳理/u, /整理/u, /确认/u, /交付/u, /方案/u], true);
  const taskRiskText = pickClause(clauses, [/阻塞/u, /卡/u, /等待/u, /待/u, /未/u, /缺/u, /补/u, /风险/u, /确认/u]);
  const taskOpportunityText = pickClause(clauses, [/预期输出/u, /目标/u, /结果/u, /交付/u, /沉淀/u, /复用/u, /资料/u, /方案/u]);
  const taskNextText = pickClause(clauses, [/下一步/u, /继续/u, /先/u, /补/u, /确认/u, /推进/u]);
  const eventLineFocusText = !isGenericAnalysisLine(eventLine?.summary, task.title)
    ? normalizeLineText(eventLine?.summary)
    : !isGenericAnalysisLine(eventLine?.intent, task.title)
      ? normalizeLineText(eventLine?.intent)
      : '';
  const eventLineRiskText = isGenericAnalysisLine(eventLine?.currentBlocker, task.title) ? '' : normalizeLineText(eventLine?.currentBlocker);
  const eventLineNextText = isGenericAnalysisLine(eventLine?.nextStep, task.title) ? '' : normalizeLineText(eventLine?.nextStep);
  const eventLineDecisionText = isGenericAnalysisLine(eventLine?.recentDecision, task.title) ? '' : normalizeLineText(eventLine?.recentDecision);
  const projectFocusText = isGenericAnalysisLine(projectContext?.currentFocus, task.title) ? '' : normalizeLineText(projectContext?.currentFocus);
  const projectNextText = isGenericAnalysisLine(projectContext?.nextAction, task.title) ? '' : normalizeLineText(projectContext?.nextAction);
  const focusText = taskFocusText || eventLineFocusText || projectFocusText;
  const backgroundText = hasSpecificScope ? normalizeLineText(projectContext?.backgroundSummary) : '';
  const riskText = taskRiskText || eventLineRiskText || buildContextRisk(task, eventLine, anchor, hasSpecificScope);
  const opportunityText = taskOpportunityText || eventLineDecisionText || buildOpportunity(task, eventLine, anchor, hasSpecificScope, hasProgressSignal);
  const nextActionText = hasSpecificScope
    ? (taskNextText || eventLineNextText || projectNextText)
    : '';
  const recentDecisionText = hasSpecificScope ? eventLineDecisionText : '';
  const moduleName = task.projectModuleName || projectContext?.projectModuleName;
  const flowName = task.projectFlowName || projectContext?.projectFlowName;

  pushUniqueInsight(insights, focusText
    ? {
        label: isAdminView ? '核心判断' : '当前重点',
        value: taskFocusText
          ? compactInsightText(taskFocusText, compact ? 28 : 36)
          : eventLineFocusText
            ? compactInsightText(eventLineFocusText, compact ? 28 : 36)
            : (compactInsightText(prefixWithAnchor(anchor, focusText), compact ? 28 : 36) || buildModeFocus(category, taskMode, taskTitle, moduleName, flowName)),
        tone: 'focus',
        order: isAdminView ? 1 : 2,
      }
    : backgroundText
      ? {
          label: isAdminView ? '核心判断' : '当前重点',
          value: compactInsightText(prefixWithAnchor(anchor, backgroundText), compact ? 28 : 36) || buildModeFocus(category, taskMode, taskTitle, moduleName, flowName),
          tone: 'focus',
          order: isAdminView ? 1 : 2,
        }
      : {
          label: isAdminView ? '核心判断' : '当前重点',
          value: buildModeFocus(category, taskMode, taskTitle, moduleName, flowName),
          tone: 'focus',
          order: isAdminView ? 1 : 2,
        });

  pushUniqueInsight(insights, {
    label: isAdminView ? '最大风险' : '当前卡点',
    value: riskText
      ? (taskRiskText
          ? compactInsightText(taskRiskText, compact ? 28 : 36)
          : eventLineRiskText
            ? compactInsightText(eventLineRiskText, compact ? 28 : 36)
            : compactInsightText(riskText, compact ? 28 : 36))
      : buildModeRisk(category, taskMode, taskTitle),
    tone: 'risk',
    order: 0,
  });

  pushUniqueInsight(insights, {
    label: isAdminView ? '可放大点' : '可继续放大',
    value: opportunityText
      ? (taskOpportunityText
          ? compactInsightText(taskOpportunityText, compact ? 28 : 36)
          : eventLineDecisionText
            ? compactInsightText(eventLineDecisionText, compact ? 28 : 36)
            : compactInsightText(opportunityText, compact ? 28 : 36))
      : buildModeOpportunity(category, taskMode, taskTitle, attachments.length),
    tone: 'opportunity',
    order: isAdminView ? 2 : 3,
  });

  pushUniqueInsight(insights, recentDecisionText
    ? {
        label: '最近关键决策',
        value: prefixWithAnchor(anchor, recentDecisionText),
        tone: 'neutral',
        order: isAdminView ? 2 : 3,
      }
    : null);

  pushUniqueInsight(insights, {
    label: '先做什么',
    value: nextActionText
      ? (taskNextText
          ? compactInsightText(taskNextText, compact ? 28 : 36)
          : eventLineNextText
            ? compactInsightText(eventLineNextText, compact ? 28 : 36)
            : compactInsightText(prefixWithAnchor(anchor, nextActionText), compact ? 28 : 36))
      : buildModeAction(category, taskMode, taskTitle),
    tone: 'action',
    order: isAdminView ? 3 : 1,
  });

  return insights
    .sort((left, right) => left.order - right.order)
    .slice(0, 4);
}

function toneClasses(tone: InsightTone) {
  if (tone === 'focus') return 'border-blue-100 bg-blue-50/70 text-blue-700';
  if (tone === 'risk') return 'border-amber-100 bg-amber-50/70 text-amber-700';
  if (tone === 'action') return 'border-emerald-100 bg-emerald-50/70 text-emerald-700';
  if (tone === 'opportunity') return 'border-violet-100 bg-violet-50/70 text-violet-700';
  return 'border-slate-200 bg-white text-slate-700';
}

function memorySourceLabel(value: string) {
  if (value === 'organization_notebook') return '组织笔记';
  if (value === 'event_line_memory') return '事件线记忆';
  if (value === 'task_facts') return '任务事实';
  if (value === 'client_facts') return '客户事实';
  if (value === 'event_line_facts') return '事件线事实';
  return value;
}

function readinessTone(readiness?: BackgroundReadiness | null) {
  if (readiness?.level === 'high') return 'border-emerald-100 bg-emerald-50/70 text-emerald-700';
  if (readiness?.level === 'medium') return 'border-blue-100 bg-blue-50/70 text-blue-700';
  return 'border-amber-100 bg-amber-50/70 text-amber-700';
}

function buildPreviewInsights(preview: TaskContextPreview, compact: boolean, viewerRole?: EmployeeRole | null) {
  const isManagerView = viewerRole === 'admin';
  const judgment = preview.judgment;
  const limit = compact ? 34 : 60;
  const happened = compactInsightText(judgment.whatHappened, limit);
  const matters = compactInsightText(judgment.whyItMatters, limit);
  const blocker = compactInsightText(judgment.coreBlocker || judgment.riskIfIgnored, limit);
  const opportunity = compactInsightText(judgment.opportunityIfAmplified || judgment.managerImplication, limit);
  const action = compactInsightText(judgment.minimumAction || judgment.nextWeekFocus, limit);
  return [
    {
      label: isManagerView ? '管理判断' : '本周推进',
      value: isManagerView ? (matters || happened) : (happened || matters),
      tone: 'focus' as const,
      order: isManagerView ? 1 : 2,
    },
    {
      label: isManagerView ? '真正阻碍' : '当前卡点',
      value: blocker,
      tone: 'risk' as const,
      order: 0,
    },
    {
      label: isManagerView ? '可放大点' : '可继续放大',
      value: opportunity,
      tone: 'opportunity' as const,
      order: isManagerView ? 2 : 3,
    },
    {
      label: '先做什么',
      value: action,
      tone: 'action' as const,
      order: isManagerView ? 3 : 1,
    },
  ].filter((item) => item.value);
}

export function TaskOrgContextPanel({ task, compact = false, viewerRole, eventLine, contextPreview }: TaskOrgContextPanelProps) {
  const taskMode = inferTaskMode(task);
  const category = inferBusinessCategory(task, taskMode, eventLine);
  const summaryChips = contextPreview?.summaryChips?.length
    ? contextPreview.summaryChips
    : buildSummaryChips(businessCategoryLabel(category), task.projectContext, task.eventLineName, eventLine?.stage || null);
  const insights = contextPreview
    ? buildPreviewInsights(contextPreview, compact, viewerRole)
    : buildInsights(task, viewerRole, compact, eventLine);
  const memoryHints = contextPreview
    ? [
        contextPreview.judgment.managerImplication,
        contextPreview.judgment.evidenceSummary,
        contextPreview.judgment.riskIfIgnored,
      ].filter(Boolean)
    : (task.memoryHints || []);
  const backgroundReadiness: BackgroundReadiness | null = contextPreview
    ? {
        score: contextPreview.readiness === 'high' ? 1 : contextPreview.readiness === 'medium' ? 0.6 : 0.3,
        level: contextPreview.readiness,
        missingSlots: [],
        backgroundSources: [
          contextPreview.contextBundle.organizationIntro ? 'organization_notebook' : '',
          contextPreview.contextBundle.lineName ? 'event_line_memory' : '',
          contextPreview.contextBundle.taskFacts.length ? 'task_facts' : '',
          contextPreview.contextBundle.attachmentFacts.length ? 'task_facts' : '',
        ].filter(Boolean),
      }
    : (task.backgroundReadiness || null);
  const linkedFactsPreview = contextPreview
    ? [
        ...contextPreview.contextBundle.recentFacts,
        ...contextPreview.contextBundle.clarificationFacts.map((item) => item.summary),
      ].filter(Boolean).slice(0, 4)
    : (task.linkedFactsPreview || []);
  const memorySourceChips = (backgroundReadiness?.backgroundSources || []).map(memorySourceLabel);
  if (summaryChips.length === 0 && insights.length === 0 && memoryHints.length === 0 && memorySourceChips.length === 0) return null;

  return (
    <div className={`rounded-2xl border border-slate-200 bg-slate-50/70 ${compact ? 'mt-3 px-3 py-3' : 'mt-4 px-4 py-3.5'}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[10px] font-bold tracking-[0.18em] text-slate-400">AI 洞察</span>
        {summaryChips.map((chip) => (
          <span
            key={chip}
            className={`rounded-full border border-slate-200 bg-white text-slate-600 ${compact ? 'px-2 py-1 text-[10px]' : 'px-2.5 py-1 text-[11px]'} font-semibold`}
          >
            {chip}
          </span>
        ))}
      </div>
      {(memoryHints.length > 0 || memorySourceChips.length > 0 || (backgroundReadiness?.missingSlots?.length || 0) > 0) && (
        <div className={`mt-3 rounded-xl border px-3 py-2.5 ${readinessTone(backgroundReadiness)}`}>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[10px] font-semibold opacity-75">
              准备度 {Math.round((backgroundReadiness?.score || 0) * 100)}%
            </span>
            {memorySourceChips.map((chip) => (
              <span key={chip} className="rounded-full bg-white/80 px-2 py-1 text-[10px] font-semibold">
                {chip}
              </span>
            ))}
          </div>
          {memoryHints.length > 0 && (
            <p className={`mt-2 ${compact ? 'text-[11px]' : 'text-[12px]'} leading-5`}>
              {compactInsightText(memoryHints[0], compact ? 44 : 88)}
            </p>
          )}
          {backgroundReadiness?.missingSlots?.length ? (
            <p className="mt-2 text-[11px] leading-5 opacity-80">
              待补：{backgroundReadiness.missingSlots.slice(0, 3).join('、')}
            </p>
          ) : null}
          {linkedFactsPreview.length > 0 ? (
            <p className="mt-1 text-[11px] leading-5 opacity-70">
              已关联 {linkedFactsPreview.length} 条背景事实
            </p>
          ) : null}
        </div>
      )}
      {insights.length > 0 && (
        <div className={`grid gap-2 ${compact ? 'mt-2.5' : 'mt-3'} ${insights.length > 1 ? 'sm:grid-cols-2' : 'grid-cols-1'}`}>
          {insights.map((insight) => (
            <div
              key={`${insight.label}-${insight.value}`}
              className={`rounded-xl border px-3 py-2.5 ${toneClasses(insight.tone)}`}
            >
              <p className="text-[10px] font-semibold opacity-70">{insight.label}</p>
              <p className={`mt-1 ${compact ? 'text-[11px]' : 'text-[12px]'} font-medium leading-5 break-words line-clamp-3`}>
                {insight.value}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
