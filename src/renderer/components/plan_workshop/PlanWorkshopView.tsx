import React, { useEffect, useMemo, useRef, useState } from 'react';
import { CheckCircle2, ChevronDown, ChevronLeft, ChevronRight, Pencil, Plus, Sparkles, Trash2, X } from 'lucide-react';

import { getPlanItemTaskCounts, getTasksForPlanItem, parseDepartmentPlan } from '../../lib/api';
import {
  defaultPlanningPeriodKey,
  formatPlanningPeriodLabel,
  isoWeekDateRange,
  isoWeekKeyForDate,
  planningPeriodSortKey,
  weekStartInputValue,
  type PlanningCycleType,
} from '../../../shared/planningPeriods';
import type {
  OrgDepartmentPlanItemSettings,
  OrgDepartmentPlanSettings,
  OrgModelSettings,
  SessionUser,
  Task,
} from '../../../shared/types';
import { useRuntimeUiSessionState } from '../../lib/runtimeUiSessionStore';

interface Props {
  value: OrgModelSettings;
  currentUser: SessionUser | null;
  clients?: Array<{ id: string; name: string }>;
  tasks?: Task[];
  onSavePlan?: (plan: OrgDepartmentPlanSettings) => Promise<void> | void;
  onDeletePlan?: (plan: OrgDepartmentPlanSettings) => Promise<void> | void;
  onOpenTask?: (task: Task) => void;
  onGenerateTaskFromPlanItem?: (
    planItem: OrgDepartmentPlanItemSettings,
    scopeName: string,
    plan: OrgDepartmentPlanSettings,
  ) => void;
  isLoading?: boolean;
  loadError?: string;
  uiSessionScopeKey?: string;
}

type CycleType = PlanningCycleType;
type PeriodFilterType = 'all' | CycleType;
type ScopeKind = 'org' | 'department';

interface ScopeRow {
  scopeId: string;
  scopeName: string;
  scopeKind: ScopeKind;
  leaderName: string;
  plans: OrgDepartmentPlanSettings[];
}

interface AiPlanDraft {
  id: string;
  title: string;
  summary: string;
}

const ORG_LEVEL_ID = '__org__';
const FIELD_CLASS = 'w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-[12px] font-normal text-gray-800 outline-none focus:border-[#5B7BFE]';
const CYCLE_OPTIONS: Array<{ value: CycleType; label: string }> = [
  { value: 'week', label: '周' },
  { value: 'month', label: '月度' },
  { value: 'quarter', label: '季度' },
  { value: 'year', label: '年度' },
  { value: 'custom', label: '自定义' },
];

function inferCycleType(period: string): CycleType {
  if (/^\d{4}-W\d{2}$/.test(period)) return 'week';
  if (/^\d{4}-\d{2}$/.test(period)) return 'month';
  if (/^\d{4}-Q[1-4]$/.test(period)) return 'quarter';
  if (/^\d{4}$/.test(period)) return 'year';
  return 'custom';
}

function makePlan(departmentId: string | null, cycle: CycleType = 'month'): OrgDepartmentPlanSettings {
  return {
    id: `plan-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    departmentId,
    clientId: null,
    weekLabel: defaultPlanningPeriodKey(cycle),
    ownerUserId: null,
    title: '',
    summary: '',
    majorRisks: [],
    dependencies: [],
    status: 'draft',
    items: [],
    updatedAt: new Date().toISOString(),
  };
}

function planLabel(plan: OrgDepartmentPlanSettings): string {
  return plan.title?.trim() || plan.summary?.trim() || '未命名计划';
}

function shiftWeekKey(value: string, amount: number): string {
  const range = isoWeekDateRange(value) || isoWeekDateRange(defaultPlanningPeriodKey('week'));
  if (!range) return defaultPlanningPeriodKey('week');
  const target = new Date(range.start);
  target.setDate(target.getDate() + amount * 7);
  return isoWeekKeyForDate(target);
}

export function PlanWorkshopView({
  value,
  currentUser,
  tasks = [],
  onSavePlan,
  onDeletePlan,
  onOpenTask,
  isLoading = false,
  loadError = '',
  uiSessionScopeKey = 'plan-workshop:local:anonymous',
}: Props) {
  const isAdmin = currentUser?.primaryRole === 'admin';
  const livePlanAuthor = Boolean(
    isAdmin
    || currentUser?.isDepartmentLead
    || currentUser?.visibilityScope === 'department',
  );
  const confirmedPlanAuthorRef = useRef({ userId: '', allowed: false });
  if (confirmedPlanAuthorRef.current.userId !== (currentUser?.id || '')) {
    confirmedPlanAuthorRef.current = { userId: currentUser?.id || '', allowed: livePlanAuthor };
  } else if (livePlanAuthor) {
    confirmedPlanAuthorRef.current.allowed = true;
  }
  const canCreatePlan = Boolean(onSavePlan && confirmedPlanAuthorRef.current.allowed);

  const visibleDepartments = useMemo(() => value.departments.filter((department) => (
    department.active !== false && (isAdmin || department.id === currentUser?.departmentId)
  )), [currentUser?.departmentId, isAdmin, value.departments]);

  const rows = useMemo<ScopeRow[]>(() => {
    const sorted = [...value.departmentPlans].sort((left, right) => (
      planningPeriodSortKey(right.weekLabel).localeCompare(planningPeriodSortKey(left.weekLabel))
      || (right.updatedAt || '').localeCompare(left.updatedAt || '')
    ));
    const organizationName = value.organization?.name?.trim() || '当前组织';
    return [
      {
        scopeId: ORG_LEVEL_ID,
        scopeName: organizationName,
        scopeKind: 'org',
        leaderName: value.organization?.leaderName?.trim() || '组织负责人未指派',
        plans: sorted.filter((plan) => !plan.departmentId),
      },
      ...visibleDepartments.map((department) => ({
        scopeId: department.id,
        scopeName: department.name,
        scopeKind: 'department' as const,
        leaderName: department.leaderName?.trim() || '未指派负责人',
        plans: sorted.filter((plan) => plan.departmentId === department.id),
      })),
    ];
  }, [value.departmentPlans, value.organization, visibleDepartments]);

  const [taskCounts, setTaskCounts] = useState<Record<string, number>>({});
  const [tasksByPlanId, setTasksByPlanId] = useState<Record<string, Task[]>>({});
  const [tasksLoadingPlanId, setTasksLoadingPlanId] = useState<string | null>(null);
  const [tasksError, setTasksError] = useState('');
  const [expandedScopeId, setExpandedScopeId] = useRuntimeUiSessionState<string | null>(`${uiSessionScopeKey}:expanded-scope`, null);
  const [selectedPlanId, setSelectedPlanId] = useRuntimeUiSessionState<string | null>(`${uiSessionScopeKey}:selected-plan`, null);
  const [editingPlan, setEditingPlan] = useState<OrgDepartmentPlanSettings | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [formError, setFormError] = useState('');
  const [aiOpen, setAiOpen] = useState(false);
  const [aiScopeId, setAiScopeId] = useState('');
  const [aiCycle, setAiCycle] = useState<CycleType>('month');
  const [aiPeriod, setAiPeriod] = useState(defaultPlanningPeriodKey('month'));
  const [aiText, setAiText] = useState('');
  const [aiDrafts, setAiDrafts] = useState<AiPlanDraft[]>([]);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState('');
  const [taskSearchQuery, setTaskSearchQuery] = useRuntimeUiSessionState(`${uiSessionScopeKey}:search`, '');
  const [periodFilterType, setPeriodFilterType] = useRuntimeUiSessionState<PeriodFilterType>(`${uiSessionScopeKey}:period-type`, 'all');
  const [periodFilterValue, setPeriodFilterValue] = useRuntimeUiSessionState(`${uiSessionScopeKey}:period-value`, '');
  const [showArchived, setShowArchived] = useRuntimeUiSessionState(`${uiSessionScopeKey}:show-completed`, false);
  const [lifecycleBusyPlanId, setLifecycleBusyPlanId] = useState<string | null>(null);
  const [pendingLifecyclePlan, setPendingLifecyclePlan] = useState<OrgDepartmentPlanSettings | null>(null);
  const [lifecycleError, setLifecycleError] = useState('');

  useEffect(() => {
    let live = true;
    void getPlanItemTaskCounts().then((counts) => {
      if (live) setTaskCounts(counts);
    }).catch(() => {
      if (live) setTaskCounts({});
    });
    return () => { live = false; };
  }, [value.departmentPlans]);

  useEffect(() => {
    if (!expandedScopeId && rows.length > 0) setExpandedScopeId(rows[0].scopeId);
    if (!selectedPlanId) {
      const first = rows.flatMap((row) => row.plans).find((plan) => showArchived || plan.status !== 'closed');
      if (first) setSelectedPlanId(first.id);
    }
  }, [expandedScopeId, rows, selectedPlanId, showArchived]);

  useEffect(() => {
    const selected = value.departmentPlans.find((plan) => plan.id === selectedPlanId);
    if (!showArchived && selected?.status === 'closed') setSelectedPlanId(null);
  }, [selectedPlanId, showArchived, value.departmentPlans]);

  const tasksForSearchByPlanId = useMemo(() => {
    const grouped: Record<string, Task[]> = {};
    tasks.forEach((task) => {
      const planId = task.planningCycleId || '';
      if (!planId) return;
      (grouped[planId] ||= []).push(task);
    });
    return grouped;
  }, [tasks]);
  const normalizedTaskSearch = taskSearchQuery.trim().toLocaleLowerCase('zh-CN');
  const planMatchesFilters = (plan: OrgDepartmentPlanSettings) => {
    if (!showArchived && plan.status === 'closed') return false;
    if (periodFilterType !== 'all') {
      if (inferCycleType(plan.weekLabel) !== periodFilterType) return false;
      if (periodFilterValue && plan.weekLabel !== periodFilterValue) return false;
    }
    if (normalizedTaskSearch) {
      return (tasksForSearchByPlanId[plan.id] || []).some((task) => (
        `${task.title || ''}\n${task.desc || ''}`.toLocaleLowerCase('zh-CN').includes(normalizedTaskSearch)
      ));
    }
    return true;
  };
  const selectedPlan = value.departmentPlans.find((plan) => plan.id === selectedPlanId && planMatchesFilters(plan)) || null;
  const selectedScope = rows.find((row) => (
    selectedPlan?.departmentId ? row.scopeId === selectedPlan.departmentId : row.scopeId === ORG_LEVEL_ID
  )) || null;
  const departmentRows = rows.filter((row) => row.scopeKind === 'department');
  const filteredActivePlans = rows.flatMap((row) => row.plans).filter((plan) => plan.status !== 'closed' && planMatchesFilters(plan));
  const coveredDepartments = departmentRows.filter((row) => row.plans.some((plan) => plan.status !== 'closed' && planMatchesFilters(plan))).length;
  const unlinkedPlans = filteredActivePlans.filter((plan) => (taskCounts[plan.id] || 0) === 0);
  const linkedPlans = filteredActivePlans.length - unlinkedPlans.length;
  const executionCoverage = filteredActivePlans.length > 0 ? Math.round((linkedPlans / filteredActivePlans.length) * 100) : 0;
  const archivedCount = rows.flatMap((row) => row.plans).filter((plan) => plan.status === 'closed').length;
  const subtitle = isAdmin
    ? '管理员视图 · 看全部部门计划与挂接任务'
    : currentUser?.departmentName
      ? `${currentUser.departmentName} · 部门负责人视图`
      : '部门视图';

  const scopeCanCreate = (row: ScopeRow) => row.scopeKind === 'department' ? canCreatePlan : Boolean(onSavePlan && isAdmin);

  useEffect(() => {
    const currentStillVisible = value.departmentPlans.some((plan) => plan.id === selectedPlanId && planMatchesFilters(plan));
    if (currentStillVisible) return;
    const firstVisible = rows.flatMap((row) => row.plans).find(planMatchesFilters);
    setSelectedPlanId(firstVisible?.id || null);
  }, [periodFilterType, periodFilterValue, showArchived, taskSearchQuery, tasksForSearchByPlanId, value.departmentPlans]);

  const changePlanLifecycle = async (plan: OrgDepartmentPlanSettings) => {
    const linkedCount = taskCounts[plan.id] || 0;
    setLifecycleBusyPlanId(plan.id);
    setLifecycleError('');
    try {
      if (linkedCount > 0) {
        if (!onSavePlan) return;
        await onSavePlan({ ...plan, status: 'closed', items: [] });
      } else {
        if (!onDeletePlan) return;
        await onDeletePlan(plan);
        setSelectedPlanId(null);
      }
      setPendingLifecyclePlan(null);
    } catch (error) {
      setLifecycleError(error instanceof Error ? error.message : linkedCount > 0 ? '完成计划失败' : '删除计划失败');
    } finally {
      setLifecycleBusyPlanId(null);
    }
  };

  useEffect(() => {
    if (!selectedPlanId) return;
    let live = true;
    setTasksLoadingPlanId(selectedPlanId);
    setTasksError('');
    void getTasksForPlanItem(selectedPlanId).then((tasks) => {
      if (!live) return;
      setTasksByPlanId((current) => ({ ...current, [selectedPlanId]: tasks }));
    }).catch((error) => {
      if (!live) return;
      setTasksError(error instanceof Error ? error.message : '关联任务加载失败');
    }).finally(() => {
      if (live) setTasksLoadingPlanId((current) => current === selectedPlanId ? null : current);
    });
    return () => { live = false; };
  }, [selectedPlanId]);

  const openCreate = (scopeId?: string) => {
    const fallback = rows.find((row) => scopeCanCreate(row));
    const resolved = scopeId || fallback?.scopeId;
    if (!resolved) return;
    setFormError('');
    setEditingPlan(makePlan(resolved === ORG_LEVEL_ID ? null : resolved));
  };

  const saveOnePlan = async () => {
    if (!editingPlan || !onSavePlan) return;
    if (!editingPlan.title?.trim()) {
      setFormError('请填写计划名称');
      return;
    }
    if (!editingPlan.weekLabel.trim()) {
      setFormError('请填写计划周期');
      return;
    }
    setIsSaving(true);
    setFormError('');
    try {
      await onSavePlan({
        ...editingPlan,
        clientId: null,
        title: editingPlan.title.trim(),
        summary: editingPlan.summary.trim(),
        items: [],
      });
      setSelectedPlanId(editingPlan.id);
      setExpandedScopeId(editingPlan.departmentId || ORG_LEVEL_ID);
      setEditingPlan(null);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : '保存失败');
    } finally {
      setIsSaving(false);
    }
  };

  const openAiSplit = () => {
    const fallback = rows.find((row) => scopeCanCreate(row));
    if (!fallback) return;
    setAiScopeId(fallback.scopeId);
    setAiCycle('month');
    setAiPeriod(defaultPlanningPeriodKey('month'));
    setAiText('');
    setAiDrafts([]);
    setAiError('');
    setAiOpen(true);
  };

  const runAiSplit = async () => {
    if (!aiText.trim()) {
      setAiError('请先粘贴或输入需要拆解的计划内容');
      return;
    }
    const scope = rows.find((row) => row.scopeId === aiScopeId);
    if (!scope) return;
    setAiBusy(true);
    setAiError('');
    try {
      const result = await parseDepartmentPlan({
        text: aiText.trim(),
        organizationName: value.organization?.name || '',
        scopeKind: scope.scopeKind,
        scopeName: scope.scopeName,
        periodKey: aiPeriod,
        cycleType: aiCycle,
      });
      const drafts = result.items.map((item, index) => ({
        id: `ai-plan-${Date.now()}-${index}`,
        title: item.title.trim(),
        summary: [item.statement, item.expectedOutput].filter(Boolean).join('\n'),
      })).filter((item) => item.title);
      setAiDrafts(drafts);
      if (drafts.length === 0) setAiError('没有识别出可独立保存的计划，请调整原文后重试');
    } catch (error) {
      setAiError(error instanceof Error ? error.message : 'AI 拆解失败');
    } finally {
      setAiBusy(false);
    }
  };

  const saveAiPlans = async () => {
    if (!onSavePlan) return;
    const validDrafts = aiDrafts.filter((draft) => draft.title.trim());
    if (validDrafts.length === 0) {
      setAiError('至少保留一条计划');
      return;
    }
    setAiBusy(true);
    setAiError('');
    try {
      const departmentId = aiScopeId === ORG_LEVEL_ID ? null : aiScopeId;
      for (const [index, draft] of validDrafts.entries()) {
        await onSavePlan({
          ...makePlan(departmentId, aiCycle),
          id: `plan-ai-${Date.now()}-${index}-${Math.random().toString(36).slice(2, 6)}`,
          clientId: null,
          weekLabel: aiPeriod,
          title: draft.title.trim(),
          summary: draft.summary.trim(),
          status: 'active',
          items: [],
        });
      }
      setExpandedScopeId(aiScopeId);
      setAiOpen(false);
    } catch (error) {
      setAiError(error instanceof Error ? error.message : '批量保存失败，已成功的计划不会重复生成');
    } finally {
      setAiBusy(false);
    }
  };

  if (isLoading) {
    return (
      <div className="h-full overflow-y-auto">
        <div className="mx-auto max-w-7xl space-y-8 px-6 pb-20 pt-8 lg:px-8">
          <PageHeading subtitle={subtitle} />
          <div className="rounded-2xl border border-blue-100 bg-blue-50/60 px-5 py-10 text-center text-[12px] text-blue-700">
            正在读取正式组织、部门和计划，请稍候…
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <div className="mx-auto flex h-full min-h-0 w-full max-w-7xl flex-col px-6 pt-6 lg:px-8">
        <div className="z-30 -mx-2 shrink-0 border-b border-gray-100 bg-[#F9FAFB] px-2 pb-3 pt-1">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <PageHeading subtitle={subtitle} error={loadError} />
            {canCreatePlan && (
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setShowArchived((current) => !current)}
                  role="switch"
                  aria-checked={!showArchived}
                  className="inline-flex items-center gap-2 rounded-2xl border border-gray-200 bg-white px-3 py-2 text-[12px] font-bold text-gray-600"
                >
                  <span>隐藏已完成</span>
                  <span className={`relative inline-flex h-5 w-9 rounded-full transition-colors ${!showArchived ? 'bg-[#5B7BFE]' : 'bg-gray-200'}`}>
                    <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${!showArchived ? 'translate-x-[18px]' : 'translate-x-0.5'}`} />
                  </span>
                  <span className="tabular-nums text-[10px] text-gray-400">({archivedCount})</span>
                </button>
                <button type="button" onClick={openAiSplit} className="inline-flex items-center gap-1.5 rounded-xl border border-[#5B7BFE]/30 bg-white px-3.5 py-2 text-[12px] font-bold text-[#4A66D8] hover:bg-[#5B7BFE]/5">
                  <Sparkles size={14} /> AI 拆解多计划
                </button>
                <button type="button" onClick={() => openCreate()} className="inline-flex items-center gap-1.5 rounded-xl bg-[#5B7BFE] px-3.5 py-2 text-[12px] font-bold text-white hover:bg-[#4A6AE8]">
                  <Plus size={14} /> 新增计划
                </button>
              </div>
            )}
          </div>

          <section className="mt-3 flex flex-wrap items-center gap-2 border-y border-gray-100 py-2.5">
            <label className="min-w-[260px] flex-1">
              <input
                value={taskSearchQuery}
                onChange={(event) => setTaskSearchQuery(event.target.value)}
                className="w-full rounded-2xl border border-gray-200 bg-white px-3 py-2 text-[12px] text-gray-800 outline-none focus:border-[#5B7BFE]"
                placeholder="搜索任务名称或任务说明中的关键词"
              />
            </label>
            <label className="w-[132px]">
              <select
                value={periodFilterType}
                onChange={(event) => {
                  const nextType = event.target.value as PeriodFilterType;
                  setPeriodFilterType(nextType);
                  setPeriodFilterValue(nextType === 'all' ? '' : defaultPlanningPeriodKey(nextType));
                }}
                className="w-full rounded-2xl border border-gray-200 bg-white px-3 py-2 text-[12px] font-bold text-gray-700 outline-none focus:border-[#5B7BFE]"
              >
                <option value="all">全部周期</option>
                {CYCLE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
              </select>
            </label>
            <label className="w-[210px]">
              {periodFilterType === 'all' ? (
                <span className="block w-full cursor-not-allowed rounded-2xl border border-gray-200 bg-gray-100 px-3 py-2 text-[12px] font-bold text-gray-400">
                  请先选择周期类型
                </span>
              ) : periodFilterType === 'week' ? (
                <WeekPeriodInput value={periodFilterValue || defaultPlanningPeriodKey('week')} onChange={setPeriodFilterValue} compact />
              ) : (
                <input
                  value={periodFilterValue}
                  onChange={(event) => setPeriodFilterValue(event.target.value)}
                  className="w-full rounded-2xl border border-gray-200 bg-white px-3 py-2 text-[12px] font-bold text-gray-700 outline-none focus:border-[#5B7BFE]"
                  placeholder={periodFilterType === 'month' ? '例如 2026-08' : periodFilterType === 'quarter' ? '例如 2026-Q3' : periodFilterType === 'year' ? '例如 2026' : '输入目标周期'}
                />
              )}
            </label>
            {(taskSearchQuery || periodFilterType !== 'all') && (
              <button
                type="button"
                onClick={() => { setTaskSearchQuery(''); setPeriodFilterType('all'); setPeriodFilterValue(''); }}
                className="rounded-2xl border border-gray-200 bg-white px-3 py-2 text-[12px] font-bold text-gray-500 hover:border-[#C9D6FF] hover:text-[#5B7BFE]"
              >
                清除筛选
              </button>
            )}
          </section>
        </div>

        <div className="min-h-0 flex-1 space-y-7 overflow-y-auto pb-20 pt-7">
          {isAdmin && <section>
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">PLAN OVERVIEW</p>
            <div className="mt-3 grid grid-cols-2 gap-x-8 gap-y-4 md:grid-cols-4">
              <Metric label="部门覆盖" value={`${coveredDepartments} / ${departmentRows.length}`} hint="已制定计划的部门" />
              <Metric label="待制定" value={String(departmentRows.length - coveredDepartments)} hint="尚无计划的部门" accent="amber" />
              <Metric label="未启动计划" value={String(unlinkedPlans.length)} hint="尚无任务承接的有效计划" accent="amber" />
              <Metric label="执行覆盖率" value={`${executionCoverage}%`} hint={`${linkedPlans} / ${filteredActivePlans.length} 条计划已有任务承接`} accent="blue" />
            </div>
          </section>}

          <section className="grid grid-cols-1 gap-x-8 gap-y-8 border-t border-gray-100 pt-6 lg:grid-cols-[1.1fr_1fr]">
          <div>
            <p className="mb-4 text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">DEPARTMENTS · 部门 · 计划</p>
            <div className="-mx-2 max-h-[640px] overflow-y-auto">
              {rows.map((row) => {
                const expanded = expandedScopeId === row.scopeId;
                const displayedPlans = row.plans.filter(planMatchesFilters).sort((left, right) => Number(left.status === 'closed') - Number(right.status === 'closed'));
                const rowActivePlans = row.plans.filter((plan) => plan.status !== 'closed' && planMatchesFilters(plan));
                return (
                  <div key={row.scopeId} className="border-t border-gray-100 last:border-b">
                    <div className={`flex items-center gap-3 px-3 py-3.5 ${expanded ? 'bg-gray-50/60' : ''}`}>
                      <button type="button" onClick={() => setExpandedScopeId(expanded ? null : row.scopeId)} className="flex min-w-0 flex-1 items-center gap-3 text-left">
                        <ChevronDown size={14} className={`shrink-0 text-gray-400 transition-transform ${expanded ? '' : '-rotate-90'}`} />
                        <span className="min-w-0 flex-1">
                          <span className="flex items-center gap-2">
                            <span className="truncate text-[13px] font-medium text-gray-900">{row.scopeName}</span>
                            {row.scopeKind === 'org' && <span className="rounded-full bg-[#5B7BFE]/10 px-1.5 py-0.5 text-[9px] font-bold text-[#5B7BFE]">组织</span>}
                          </span>
                          <span className="mt-0.5 block truncate text-[10.5px] text-gray-400">{row.leaderName} · {rowActivePlans.length} 条计划 · {rowActivePlans.filter((plan) => (taskCounts[plan.id] || 0) === 0).length} 条未关联任务</span>
                        </span>
                      </button>
                      {scopeCanCreate(row) && (
                        <button type="button" onClick={() => openCreate(row.scopeId)} className="rounded-lg p-2 text-gray-400 hover:bg-white hover:text-[#5B7BFE]" title={`为${row.scopeName}新建计划`}><Plus size={14} /></button>
                      )}
                    </div>
                    {expanded && (
                      <div className="space-y-2.5 pb-4 pl-7 pr-3 pt-1">
                        {displayedPlans.length === 0 ? (
                          <p className="py-3 text-[11px] text-gray-400">
                            {taskSearchQuery || periodFilterType !== 'all'
                              ? '当前筛选条件下没有匹配的计划或关联任务'
                              : row.plans.length > 0
                                ? '当前仅有已完成计划，可用右上角开关显示'
                                : '尚未制定计划'}
                          </p>
                        ) : displayedPlans.map((plan) => {
                          const selected = selectedPlanId === plan.id;
                          const archived = plan.status === 'closed';
                          return (
                            <button key={plan.id} type="button" onClick={() => setSelectedPlanId(plan.id)} className={`relative w-full rounded-xl border py-3.5 pl-6 pr-4 text-left transition-all before:absolute before:bottom-3.5 before:left-3 before:top-3.5 before:w-[2.5px] before:rounded-full ${archived ? 'before:bg-gray-300 opacity-55' : 'before:bg-[#5B7BFE]'} ${selected ? 'border-[#9FB2FF] bg-[#5B7BFE]/[0.04]' : 'border-gray-100 bg-white hover:border-gray-200 hover:bg-gray-50/40'}`}>
                              <span className="flex items-start gap-2">
                                <span className={`min-w-0 flex-1 text-[13.5px] font-medium ${selected ? 'text-[#3D5CD9]' : 'text-gray-900'}`}>{planLabel(plan)}</span>
                                <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold ${archived ? 'bg-gray-100 text-gray-500' : 'bg-[#5B7BFE]/10 text-[#5B7BFE]'}`}>{archived ? '已完成' : `关联 ${taskCounts[plan.id] || 0}`}</span>
                              </span>
                              <span className="mt-1.5 block text-[11.5px] leading-[1.65] text-gray-500">{formatPlanningPeriodLabel(plan.weekLabel)}{plan.summary ? ` · ${plan.summary}` : ''}</span>
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          <div>
            <p className="mb-4 text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">DETAIL · 计划详情</p>
            <div className="max-h-[640px] overflow-y-auto">
              {!selectedPlan ? (
                <div className="py-12 text-center text-[12px] text-gray-400">选择左侧某条计划查看详情</div>
              ) : (
                <div className="space-y-7">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">{selectedScope?.scopeName || '未知主体'} · 独立计划</p>
                      <h3 className="mt-2 text-[20px] font-light leading-tight tracking-tight text-gray-900">{planLabel(selectedPlan)}</h3>
                    </div>
                    {selectedPlan.status !== 'closed' && <div className="flex items-center gap-1">
                      {onSavePlan && <button type="button" onClick={() => { setFormError(''); setEditingPlan({ ...selectedPlan, items: [] }); }} className="rounded-md p-2 text-gray-400 hover:bg-gray-100 hover:text-[#5B7BFE]" title="编辑计划"><Pencil size={15} /></button>}
                      {(taskCounts[selectedPlan.id] || 0) > 0 ? (
                        <button type="button" disabled={lifecycleBusyPlanId === selectedPlan.id} onClick={() => { setLifecycleError(''); setPendingLifecyclePlan(selectedPlan); }} className="rounded-md p-2 text-gray-400 hover:bg-gray-100 hover:text-emerald-600 disabled:opacity-40" title="完成计划"><CheckCircle2 size={15} /></button>
                      ) : (
                        <button type="button" disabled={lifecycleBusyPlanId === selectedPlan.id} onClick={() => { setLifecycleError(''); setPendingLifecyclePlan(selectedPlan); }} className="rounded-md p-2 text-gray-400 hover:bg-rose-50 hover:text-rose-600 disabled:opacity-40" title="删除未关联任务的计划"><Trash2 size={15} /></button>
                      )}
                    </div>}
                  </div>
                  {lifecycleError && <p className="rounded-xl border border-rose-100 bg-rose-50 px-3 py-2 text-[11px] text-rose-600">{lifecycleError}</p>}
                  <div>
                    <p className="text-[10px] font-bold uppercase tracking-[0.15em] text-gray-400">计划说明</p>
                    <p className="mt-3 whitespace-pre-wrap text-[13px] leading-7 text-gray-600">{selectedPlan.summary || '暂无说明'}</p>
                  </div>
                  <div className="border-t border-gray-100 pt-6">
                    <div className="flex items-center justify-between">
                      <p className="text-[10px] font-bold uppercase tracking-[0.15em] text-gray-400">关联任务</p>
                      <span className="text-[11px] text-gray-400">{taskCounts[selectedPlan.id] || 0} 项</span>
                    </div>
                    {tasksLoadingPlanId === selectedPlan.id ? (
                      <p className="py-6 text-center text-[11px] text-blue-600">正在读取关联任务…</p>
                    ) : tasksError ? (
                      <p className="py-4 text-[11px] text-rose-600">{tasksError}</p>
                    ) : (tasksByPlanId[selectedPlan.id] || []).length === 0 ? (
                      <p className="py-6 text-center text-[11px] text-gray-400">当前没有可见的关联任务</p>
                    ) : (
                      <div className="mt-3 divide-y divide-gray-100">
                        {(tasksByPlanId[selectedPlan.id] || []).map((task) => (
                          <button
                            key={task.id}
                            type="button"
                            onClick={() => onOpenTask?.(task)}
                            className={`flex w-full items-start gap-3 rounded-lg px-2 py-3 text-left transition-colors ${onOpenTask ? 'hover:bg-gray-50' : ''}`}
                          >
                            <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${task.status === 'done' ? 'bg-emerald-500' : 'bg-[#5B7BFE]'}`} />
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-[13px] font-medium text-gray-800">{task.title}</span>
                              <span className="mt-1 block text-[10.5px] text-gray-400">
                                {task.status === 'done' ? '已完成' : '进行中'}
                                {task.ownerName ? ` · ${task.ownerName}` : ''}
                                {task.dueDate ? ` · ${task.dueDate.slice(0, 10)}` : ''}
                              </span>
                            </span>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
          </section>
        </div>
      </div>

      {editingPlan && (
        <PlanModal
          plan={editingPlan}
          rows={rows}
          canChooseOrg={isAdmin}
          saving={isSaving}
          error={formError}
          isExisting={value.departmentPlans.some((plan) => plan.id === editingPlan.id)}
          onChange={setEditingPlan}
          onClose={() => setEditingPlan(null)}
          onSave={() => void saveOnePlan()}
        />
      )}

      {pendingLifecyclePlan && (() => {
        const linkedCount = taskCounts[pendingLifecyclePlan.id] || 0;
        const isArchive = linkedCount > 0;
        return (
          <div className="fixed inset-0 z-[145] flex items-center justify-center bg-slate-950/30 p-5">
            <div className="w-full max-w-md rounded-2xl bg-white shadow-2xl">
              <div className="flex items-start justify-between border-b border-gray-100 px-6 py-5">
                <div>
                  <p className={`text-[10px] font-bold uppercase tracking-[0.18em] ${isArchive ? 'text-emerald-600' : 'text-rose-600'}`}>{isArchive ? 'COMPLETE PLAN' : 'DELETE PLAN'}</p>
                  <h2 className="mt-1 text-xl font-light">{isArchive ? '确认完成计划？' : '确认删除计划？'}</h2>
                </div>
                <button type="button" disabled={Boolean(lifecycleBusyPlanId)} onClick={() => { setPendingLifecyclePlan(null); setLifecycleError(''); }} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100 disabled:opacity-40"><X size={18} /></button>
              </div>
              <div className="space-y-3 px-6 py-5">
                <p className="text-[13px] font-medium text-gray-900">{planLabel(pendingLifecyclePlan)}</p>
                <p className="text-[12px] leading-6 text-gray-500">
                  {isArchive
                    ? `该计划已有 ${linkedCount} 条任务承接。完成后仍保留在组织计划中，但不会再出现在任务编辑器的可关联计划列表。`
                    : '该计划尚未关联任务或会议。删除后不会再出现在组织计划和任务编辑器中；审计与生命周期记录仍会保留。'}
                </p>
                {lifecycleError && <p className="rounded-xl border border-rose-100 bg-rose-50 px-3 py-2 text-[11px] text-rose-600">{lifecycleError}</p>}
              </div>
              <div className="flex justify-end gap-2 border-t border-gray-100 px-6 py-4">
                <button type="button" disabled={Boolean(lifecycleBusyPlanId)} onClick={() => { setPendingLifecyclePlan(null); setLifecycleError(''); }} className="rounded-lg border border-gray-200 px-4 py-2 text-[12px] disabled:opacity-40">取消</button>
                <button type="button" disabled={Boolean(lifecycleBusyPlanId)} onClick={() => void changePlanLifecycle(pendingLifecyclePlan)} className={`rounded-lg px-4 py-2 text-[12px] font-bold text-white disabled:opacity-50 ${isArchive ? 'bg-emerald-500 hover:bg-emerald-600' : 'bg-rose-500 hover:bg-rose-600'}`}>{lifecycleBusyPlanId ? '处理中…' : isArchive ? '确认完成' : '确认删除'}</button>
              </div>
            </div>
          </div>
        );
      })()}

      {aiOpen && (
        <div className="fixed inset-0 z-[140] flex items-center justify-center bg-slate-950/30 p-5">
          <div className="max-h-[88vh] w-full max-w-3xl overflow-y-auto rounded-2xl bg-white shadow-2xl">
            <div className="flex items-start justify-between border-b border-gray-100 px-6 py-5">
              <div><p className="text-[10px] font-bold uppercase tracking-[0.18em] text-[#5B7BFE]">AI PLAN SPLITTER</p><h2 className="mt-1 text-xl font-light">AI 拆解多计划</h2><p className="mt-1 text-[12px] text-gray-500">把一段复杂设想拆成多条平级计划；确认后逐条保存。</p></div>
              <button type="button" onClick={() => setAiOpen(false)} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100"><X size={18} /></button>
            </div>
            <div className="space-y-5 p-6">
              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                <Field label="所属范围"><select value={aiScopeId} onChange={(event) => setAiScopeId(event.target.value)} className={FIELD_CLASS}>{rows.filter(scopeCanCreate).map((row) => <option key={row.scopeId} value={row.scopeId}>{row.scopeName}</option>)}</select></Field>
                <Field label="周期类型"><select value={aiCycle} onChange={(event) => { const next = event.target.value as CycleType; setAiCycle(next); setAiPeriod(defaultPlanningPeriodKey(next)); }} className={FIELD_CLASS}>{CYCLE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></Field>
                <Field label="计划周期">{aiCycle === 'week' ? <WeekPeriodInput value={aiPeriod} onChange={setAiPeriod} navigation /> : <input value={aiPeriod} onChange={(event) => setAiPeriod(event.target.value)} className={FIELD_CLASS} />}</Field>
              </div>
              <Field label="待拆解内容"><textarea value={aiText} onChange={(event) => setAiText(event.target.value)} className={`${FIELD_CLASS} min-h-32`} placeholder="例如：本月完成安卓端架构调整、登录与任务链路复刻，并准备内测…" /></Field>
              <div className="flex justify-end"><button type="button" disabled={aiBusy} onClick={() => void runAiSplit()} className="inline-flex items-center gap-1.5 rounded-xl border border-[#5B7BFE]/30 px-4 py-2 text-[12px] font-bold text-[#4A66D8] disabled:opacity-50"><Sparkles size={14} />{aiBusy ? '拆解中…' : '开始拆解'}</button></div>
              {aiDrafts.length > 0 && <div className="space-y-3 border-t border-gray-100 pt-5">{aiDrafts.map((draft, index) => <div key={draft.id} className="rounded-xl border border-gray-200 p-4"><p className="mb-2 text-[10px] font-bold text-gray-400">独立计划 {index + 1}</p><input value={draft.title} onChange={(event) => setAiDrafts((current) => current.map((item) => item.id === draft.id ? { ...item, title: event.target.value } : item))} className={`${FIELD_CLASS} font-medium`} /><textarea value={draft.summary} onChange={(event) => setAiDrafts((current) => current.map((item) => item.id === draft.id ? { ...item, summary: event.target.value } : item))} className={`${FIELD_CLASS} mt-2 min-h-20`} /></div>)}</div>}
              {aiError && <p className="text-[12px] text-rose-600">{aiError}</p>}
            </div>
            <div className="flex justify-end gap-2 border-t border-gray-100 px-6 py-4"><button type="button" onClick={() => setAiOpen(false)} className="rounded-lg border border-gray-200 px-4 py-2 text-[12px]">取消</button><button type="button" disabled={aiBusy || aiDrafts.length === 0} onClick={() => void saveAiPlans()} className="rounded-lg bg-[#5B7BFE] px-4 py-2 text-[12px] font-bold text-white disabled:opacity-50">{aiBusy ? '保存中…' : `保存 ${aiDrafts.length} 条计划`}</button></div>
          </div>
        </div>
      )}
    </div>
  );
}

function PageHeading({ subtitle, error = '' }: { subtitle: string; error?: string }) {
  return <div><p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">PLAN WORKSHOP</p><h1 className="mt-2 text-[22px] font-light tracking-tight text-gray-900">组织计划</h1><p className="mt-1 text-[12px] leading-relaxed text-gray-500">{subtitle}</p>{error && <p className="mt-1 text-[11px] text-rose-600">组织计划加载失败：{error}</p>}</div>;
}

function Metric({ label, value, hint, accent = 'gray' }: { label: string; value: string; hint: string; accent?: 'gray' | 'amber' | 'blue' }) {
  const valueClass = accent === 'amber' ? 'text-amber-600' : accent === 'blue' ? 'text-[#5B7BFE]' : 'text-gray-900';
  return <div><p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">{label}</p><p className={`mt-2 text-[28px] font-light leading-none tracking-tight ${valueClass}`}>{value}</p><p className="mt-2 text-[11px] text-gray-400">{hint}</p></div>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block text-[11px] font-bold text-gray-600">{label}<div className="mt-2">{children}</div></label>;
}

function WeekPeriodInput({ value, onChange, compact = false, navigation = false }: { value: string; onChange: (value: string) => void; compact?: boolean; navigation?: boolean }) {
  if (compact || navigation) {
    return (
      <span className={`flex w-full items-center overflow-hidden border border-gray-200 bg-white text-gray-700 ${compact ? 'rounded-2xl' : 'rounded-xl'}`}>
        <button type="button" onClick={() => onChange(shiftWeekKey(value, -1))} className={`shrink-0 text-gray-400 hover:bg-gray-50 hover:text-[#5B7BFE] ${compact ? 'px-2 py-2' : 'px-2.5 py-3'}`} title="上一周" aria-label="上一周"><ChevronLeft size={14} /></button>
        <span className={`relative min-w-0 flex-1 border-x border-gray-100 text-center font-bold ${compact ? 'px-1 py-2 text-[11px]' : 'px-2 py-3 text-[12px]'}`}>
          <span className="block truncate">{formatPlanningPeriodLabel(value)}</span>
          <input
            type="date"
            value={weekStartInputValue(value)}
            onChange={(event) => event.target.value && onChange(isoWeekKeyForDate(new Date(`${event.target.value}T12:00:00`)))}
            className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
            title="选择目标周"
          />
        </span>
        <button type="button" onClick={() => onChange(shiftWeekKey(value, 1))} className={`shrink-0 text-gray-400 hover:bg-gray-50 hover:text-[#5B7BFE] ${compact ? 'px-2 py-2' : 'px-2.5 py-3'}`} title="下一周" aria-label="下一周"><ChevronRight size={14} /></button>
      </span>
    );
  }
  return (
    <span className="relative block">
      <span className={`${FIELD_CLASS} block min-h-[42px] cursor-pointer`}>{formatPlanningPeriodLabel(value)}</span>
      <input
        type="date"
        value={weekStartInputValue(value)}
        onChange={(event) => event.target.value && onChange(isoWeekKeyForDate(new Date(`${event.target.value}T12:00:00`)))}
        className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
        title="选择任意一天，系统按该周周一至周日保存"
      />
    </span>
  );
}

function PlanModal({ plan, rows, canChooseOrg, saving, error, isExisting, onChange, onClose, onSave }: {
  plan: OrgDepartmentPlanSettings;
  rows: ScopeRow[];
  canChooseOrg: boolean;
  saving: boolean;
  error: string;
  isExisting: boolean;
  onChange: (plan: OrgDepartmentPlanSettings) => void;
  onClose: () => void;
  onSave: () => void;
}) {
  const scopeId = plan.departmentId || ORG_LEVEL_ID;
  const cycle = inferCycleType(plan.weekLabel);
  return (
    <div className="fixed inset-0 z-[130] flex items-center justify-center bg-slate-950/30 p-5">
      <div className="w-full max-w-xl rounded-2xl bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-gray-100 px-6 py-5"><div><p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">{isExisting ? 'EDIT PLAN' : 'NEW PLAN'}</p><h2 className="mt-1 text-xl font-light">{isExisting ? '编辑计划' : '新增一条计划'}</h2></div><button type="button" onClick={onClose} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100"><X size={18} /></button></div>
        <div className="space-y-4 p-6">
          <Field label="所属范围"><select disabled={isExisting} value={scopeId} onChange={(event) => onChange({ ...plan, departmentId: event.target.value === ORG_LEVEL_ID ? null : event.target.value })} className={FIELD_CLASS}><option value={ORG_LEVEL_ID} disabled={!canChooseOrg}>当前组织</option>{rows.filter((row) => row.scopeKind === 'department').map((row) => <option key={row.scopeId} value={row.scopeId}>{row.scopeName}</option>)}</select></Field>
          <Field label="计划名称"><input autoFocus value={plan.title || ''} onChange={(event) => onChange({ ...plan, title: event.target.value })} className={FIELD_CLASS} placeholder="例如：完成安卓版架构调整" /></Field>
          <div className="grid grid-cols-2 gap-3"><Field label="周期类型"><select value={cycle} onChange={(event) => onChange({ ...plan, weekLabel: defaultPlanningPeriodKey(event.target.value as CycleType) })} className={FIELD_CLASS}>{CYCLE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></Field><Field label="计划周期">{cycle === 'week' ? <WeekPeriodInput value={plan.weekLabel} onChange={(value) => onChange({ ...plan, weekLabel: value })} navigation /> : <input value={plan.weekLabel} onChange={(event) => onChange({ ...plan, weekLabel: event.target.value })} className={FIELD_CLASS} />}</Field></div>
          <Field label="计划说明"><textarea value={plan.summary} onChange={(event) => onChange({ ...plan, summary: event.target.value })} className={`${FIELD_CLASS} min-h-28`} placeholder="说明目标、范围或预期结果" /></Field>
          {error && <p className="text-[12px] text-rose-600">{error}</p>}
        </div>
        <div className="flex justify-end gap-2 border-t border-gray-100 px-6 py-4"><button type="button" onClick={onClose} className="rounded-lg border border-gray-200 px-4 py-2 text-[12px]">取消</button><button type="button" disabled={saving} onClick={onSave} className="rounded-lg bg-[#5B7BFE] px-4 py-2 text-[12px] font-bold text-white disabled:opacity-50">{saving ? '保存中…' : '保存计划'}</button></div>
      </div>
    </div>
  );
}
