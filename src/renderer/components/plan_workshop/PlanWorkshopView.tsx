import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, ArrowRight, Plus, RefreshCw, X, Trash2, Sparkles, Check, RotateCcw, Pencil } from 'lucide-react';

import { getPlanItemTaskCounts, getTasksForPlanItem, parseDepartmentPlan } from '../../lib/api';
import { useBackdropClickClose } from '../../lib/useBackdropClickClose';
import {
  defaultPlanningPeriodKey,
  formatPlanningPeriodLabel,
  isoWeekKeyForDate,
  planningPeriodSortKey,
  weekStartInputValue,
  type PlanningCycleType,
} from '../../../shared/planningPeriods';
import type {
  OrgDepartmentPlanItemSettings,
  OrgDepartmentPlanSettings,
  OrgDepartmentPlanItemStatus,
  OrgModelSettings,
  SessionUser,
  Task,
} from '../../../shared/types';

interface Props {
  value: OrgModelSettings;
  currentUser: SessionUser | null;
  clients?: Array<{ id: string; name: string }>;
  onSavePlan?: (plan: OrgDepartmentPlanSettings) => Promise<void> | void;
  /** 点击挂接任务卡片 / 卡片右上的快捷打开图标时调用 — 由 App.tsx 把 task 注入 openTaskEditor */
  onOpenTask?: (task: Task) => void;
  /** 详情面板里"生成任务"按钮：根据当前计划项预填新建任务表单（标题/说明/挂接关系） */
  onGenerateTaskFromPlanItem?: (
    planItem: OrgDepartmentPlanItemSettings,
    scopeName: string,
    plan: OrgDepartmentPlanSettings,
  ) => void;
  isLoading?: boolean;
  loadError?: string;
}

interface DepartmentRow {
  scopeId: string;          // department.id, or ORG_LEVEL_ID for organization-wide
  scopeName: string;        // department name, or organization name
  scopeKind: 'org' | 'department';
  leaderName: string;
  latestPlan: OrgDepartmentPlanSettings | null;
  plans: OrgDepartmentPlanSettings[];
  allPlansCount: number;
  itemCount: number;
  doneCount: number;
  unfinished: number;
  completeness: number;
}

const ORG_LEVEL_ID = '__org__';

type CycleType = PlanningCycleType;

interface DraftItem {
  id: string;
  title: string;
  statement: string;
  expectedOutput: string;
  status: OrgDepartmentPlanItemStatus;
}

const CYCLE_LABELS: Record<CycleType, string> = {
  month: '月度',
  quarter: '季度',
  year: '年度',
  week: '周',
  custom: '自定义',
};

function defaultPeriodValue(cycle: CycleType): string {
  return defaultPlanningPeriodKey(cycle);
}

function newDraftItem(): DraftItem {
  return {
    id: `tmp-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    title: '',
    statement: '',
    expectedOutput: '',
    status: 'active',
  };
}

/**
 * 把 weekLabel 转成可排序的 ISO 日期字符串，让月度/季度/年度/周混排时按真实时间序。
 *
 * 不修这个 bug：plans.sort((a,b) => b.weekLabel.localeCompare(a.weekLabel)) 用字符串比较会把
 * "2026-Q3" 排到 "2026-09" 之后（Q > 0~9 字符序），导致月度和季度同时存在时取到错误"最新"。
 */
function weekLabelToSortKey(label: string | null | undefined): string {
  return planningPeriodSortKey(label);
}

export function PlanWorkshopView({
  value,
  currentUser,
  clients = [],
  onSavePlan,
  onOpenTask,
  onGenerateTaskFromPlanItem,
  isLoading = false,
  loadError = '',
}: Props) {
  // 组织计划只消费 App 已按同一请求代次装载的部门/周期/行动快照。组件自身再
  // 发一套请求会造成界面看见新计划、任务编辑器却仍读到旧空数组的双快照。
  const effectiveValue = value;
  const isAdmin = currentUser?.primaryRole === 'admin';
  const userDeptId = currentUser?.departmentId ?? null;
  const organizationName = effectiveValue.organization?.name?.trim() || '组织';
  const organizationLeaderName = effectiveValue.organization?.leaderName?.trim() || '组织负责人未指派';

  const visibleDepartments = useMemo(() => {
    const allActive = effectiveValue.departments.filter((d) => d.active !== false);
    if (isAdmin) return allActive;
    return allActive.filter((d) => d.id === userDeptId);
  }, [effectiveValue.departments, isAdmin, userDeptId]);

  const livePlanAuthor = Boolean(
    isAdmin
    || (currentUser?.isDepartmentLead ?? false)
    || currentUser?.visibilityScope === 'department'
  );
  const confirmedPlanAuthorRef = useRef<{ userId: string; allowed: boolean }>({ userId: '', allowed: false });
  const currentUserId = currentUser?.id || '';
  if (confirmedPlanAuthorRef.current.userId !== currentUserId) {
    confirmedPlanAuthorRef.current = { userId: currentUserId, allowed: livePlanAuthor };
  } else if (livePlanAuthor) {
    confirmedPlanAuthorRef.current.allowed = true;
  }
  const canCreatePlan = Boolean(onSavePlan) && confirmedPlanAuthorRef.current.allowed;
  // Only admin can author organization-level plans; department leads see them but can't create one.
  const canCreateOrgPlan = Boolean(onSavePlan) && isAdmin;

  const [selectedPeriodByScope, setSelectedPeriodByScope] = useState<Record<string, string>>({});

  const rows: DepartmentRow[] = useMemo(() => {
    const computeRow = (
      scopeId: string,
      scopeName: string,
      scopeKind: 'org' | 'department',
      leaderName: string,
      planFilter: (p: OrgDepartmentPlanSettings) => boolean,
    ): DepartmentRow => {
      const plans = effectiveValue.departmentPlans.filter(planFilter);
      const sortedPlans = [...plans].sort((a, b) => {
          // 按 weekLabel 真实时间序排（解决月度/季度混排字典序错排）
          const cmp = weekLabelToSortKey(b.weekLabel).localeCompare(weekLabelToSortKey(a.weekLabel));
          if (cmp !== 0) return cmp;
          // 同 period 时 fallback 用 updatedAt
          return (b.updatedAt || '').localeCompare(a.updatedAt || '');
        });
      const selectedPeriod = selectedPeriodByScope[scopeId];
      const latestPlan = sortedPlans.find((plan) => plan.weekLabel === selectedPeriod) || sortedPlans[0] || null;
      const items = latestPlan?.items ?? [];
      const doneCount = items.filter((i) => i.status === 'done').length;
      const completeness = items.length > 0 ? Math.round((doneCount / items.length) * 100) : 0;
      const unfinished = items.length - doneCount;
      return {
        scopeId,
        scopeName,
        scopeKind,
        leaderName,
        latestPlan,
        plans: sortedPlans,
        allPlansCount: plans.length,
        itemCount: items.length,
        doneCount,
        unfinished,
        completeness,
      };
    };

    const result: DepartmentRow[] = [];
    // Organization-level row always first (visible to all roles so everyone can see org strategy)
    result.push(
      computeRow(
        ORG_LEVEL_ID,
        organizationName,
        'org',
        organizationLeaderName,
        (p) => !p.departmentId,
      ),
    );
    for (const dept of visibleDepartments) {
      result.push(
        computeRow(
          dept.id,
          dept.name,
          'department',
          dept.leaderName?.trim() || '未指派负责人',
          (p) => p.departmentId === dept.id,
        ),
      );
    }
    return result;
  }, [visibleDepartments, effectiveValue.departmentPlans, organizationName, organizationLeaderName, selectedPeriodByScope]);

  // Stats exclude the org-level row so they read as "department coverage" not "scope coverage".
  const deptRows = rows.filter((r) => r.scopeKind === 'department');
  const totalDepts = deptRows.length;
  const deptsWithPlan = deptRows.filter((r) => r.latestPlan !== null).length;
  const deptsWithoutPlan = totalDepts - deptsWithPlan;
  const totalUnfinished = deptRows.reduce((sum, r) => sum + r.unfinished, 0);
  const avgCompleteness = deptRows.length > 0
    ? Math.round(deptRows.reduce((sum, r) => sum + r.completeness, 0) / deptRows.length)
    : 0;

  const [expandedScopeId, setExpandedScopeId] = useState<string | null>(null);
  // 首次加载时按"看自己应该看的"自动展开：
  //   - admin（CEO 等）→ 展开组织级（若有计划）
  //   - 部门用户       → 展开自己部门（若有计划）；自己部门无计划就全部折叠，
  //                       让用户感知"我们部门还没填"，而不是错位看别人的
  // 用户主动收起后保持收起 — 不能让 effect 依赖 expandedScopeId，
  // 否则用户点收起后 expandedScopeId=null 又触发 effect 复位，UI"收不起来"。
  const hasAutoExpandedRef = useRef(false);
  useEffect(() => {
    if (hasAutoExpandedRef.current) return;
    if (rows.length === 0) return;
    let targetScopeId: string | null = null;
    if (isAdmin) {
      const orgRow = rows.find((r) => r.scopeId === ORG_LEVEL_ID);
      if (orgRow && orgRow.latestPlan) targetScopeId = ORG_LEVEL_ID;
    } else if (userDeptId) {
      const myRow = rows.find((r) => r.scopeId === userDeptId);
      if (myRow && myRow.latestPlan) targetScopeId = userDeptId;
    }
    if (targetScopeId) {
      setExpandedScopeId(targetScopeId);
    }
    hasAutoExpandedRef.current = true;
  }, [rows, isAdmin, userDeptId]);

  const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
  const [itemTasks, setItemTasks] = useState<Record<string, Task[]>>({});
  const [loadingItemId, setLoadingItemId] = useState<string | null>(null);
  const [itemTaskError, setItemTaskError] = useState<string | null>(null);
  const [taskCountByItemId, setTaskCountByItemId] = useState<Record<string, number>>({});
  const [isRelationRefreshing, setIsRelationRefreshing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const refreshCounts = () => getPlanItemTaskCounts()
      .then((counts) => {
        if (!cancelled) setTaskCountByItemId(counts);
      })
      .catch(() => {
        // 静默失败:徽章是辅助信息,拉不到不影响主流程
      });
    void refreshCounts();
    const onRelationsChanged = () => {
      setIsRelationRefreshing(true);
      void refreshCounts().finally(() => {
        if (!cancelled) setIsRelationRefreshing(false);
      });
      if (selectedItemId) void fetchTasksForItem(selectedItemId);
    };
    window.addEventListener('yiyu:plan-relations-changed', onRelationsChanged);
    return () => {
      cancelled = true;
      window.removeEventListener('yiyu:plan-relations-changed', onRelationsChanged);
    };
  }, [selectedItemId]);

  const fetchTasksForItem = async (itemId: string) => {
    setItemTaskError(null);
    setLoadingItemId(itemId);
    try {
      const tasks = await getTasksForPlanItem(itemId);
      setItemTasks((prev) => ({ ...prev, [itemId]: tasks }));
      setTaskCountByItemId((prev) => ({ ...prev, [itemId]: tasks.length }));
    } catch (error) {
      setItemTaskError(error instanceof Error ? error.message : '加载挂接任务失败');
    } finally {
      setLoadingItemId(null);
    }
  };

  const handleSelectItem = async (itemId: string) => {
    setSelectedItemId(itemId);
    setItemTaskError(null);
    if (itemTasks[itemId]) return;
    await fetchTasksForItem(itemId);
  };

  const handleRefreshTasks = async () => {
    if (!selectedItemId) return;
    await fetchTasksForItem(selectedItemId);
  };

  const selectedItem: OrgDepartmentPlanItemSettings | null = useMemo(() => {
    if (!selectedItemId) return null;
    for (const plan of effectiveValue.departmentPlans) {
      const found = (plan.items || []).find((it) => it.id === selectedItemId);
      if (found) return found;
    }
    return null;
  }, [selectedItemId, effectiveValue.departmentPlans]);

  const selectedItemScopeName = useMemo(() => {
    if (!selectedItemId) return null;
    for (const plan of effectiveValue.departmentPlans) {
      if ((plan.items || []).some((it) => it.id === selectedItemId)) {
        if (!plan.departmentId) return organizationName;
        return effectiveValue.departments.find((d) => d.id === plan.departmentId)?.name || '未知部门';
      }
    }
    return null;
  }, [selectedItemId, effectiveValue.departmentPlans, effectiveValue.departments, organizationName]);

  // Find the plan that contains the currently-selected item (used for edit/delete).
  const selectedItemParentPlan: OrgDepartmentPlanSettings | null = useMemo(() => {
    if (!selectedItemId) return null;
    return effectiveValue.departmentPlans.find((p) => (p.items || []).some((it) => it.id === selectedItemId)) || null;
  }, [selectedItemId, effectiveValue.departmentPlans]);

  // ─── Edit / Delete state for the selected item ─────────────────────
  const [isEditingItem, setIsEditingItem] = useState(false);
  const [editDraft, setEditDraft] = useState<{ title: string; statement: string; expectedOutput: string; status: OrgDepartmentPlanItemStatus }>({
    title: '',
    statement: '',
    expectedOutput: '',
    status: 'active',
  });
  const [isMutatingItem, setIsMutatingItem] = useState(false);
  const [mutateError, setMutateError] = useState<string | null>(null);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  // When user switches selected item, exit edit mode automatically.
  useEffect(() => {
    setIsEditingItem(false);
    setShowDeleteConfirm(false);
    setMutateError(null);
  }, [selectedItemId]);

  const handleEnterEdit = () => {
    if (!selectedItem) return;
    setEditDraft({
      title: selectedItem.title || '',
      statement: selectedItem.statement || '',
      expectedOutput: selectedItem.expectedOutput || '',
      status: selectedItem.status || 'active',
    });
    setMutateError(null);
    setIsEditingItem(true);
  };

  const handleCancelEdit = () => {
    if (isMutatingItem) return;
    setIsEditingItem(false);
    setMutateError(null);
  };

  const handleSaveEdit = async () => {
    if (!onSavePlan || !selectedItem || !selectedItemParentPlan) return;
    const trimmedTitle = editDraft.title.trim();
    if (!trimmedTitle) {
      setMutateError('计划项标题不能为空');
      return;
    }
    const now = new Date().toISOString();
    const updatedPlan: OrgDepartmentPlanSettings = {
      ...selectedItemParentPlan,
      items: (selectedItemParentPlan.items || []).map((it) =>
        it.id === selectedItem.id
          ? {
              ...it,
              title: trimmedTitle,
              statement: editDraft.statement.trim(),
              expectedOutput: editDraft.expectedOutput.trim(),
              status: editDraft.status,
              updatedAt: now,
            }
          : it,
      ),
      updatedAt: now,
    };
    setIsMutatingItem(true);
    setMutateError(null);
    try {
      await onSavePlan(updatedPlan);
      setIsEditingItem(false);
    } catch (error) {
      setMutateError(error instanceof Error ? error.message : '保存失败');
    } finally {
      setIsMutatingItem(false);
    }
  };

  const handleToggleItemDone = async () => {
    if (!onSavePlan || !selectedItem || !selectedItemParentPlan) return;
    const nextStatus: OrgDepartmentPlanItemStatus = selectedItem.status === 'done' ? 'active' : 'done';
    const now = new Date().toISOString();
    const updatedPlan: OrgDepartmentPlanSettings = {
      ...selectedItemParentPlan,
      items: (selectedItemParentPlan.items || []).map((it) =>
        it.id === selectedItem.id
          ? { ...it, status: nextStatus, updatedAt: now }
          : it,
      ),
      updatedAt: now,
    };
    setIsMutatingItem(true);
    setMutateError(null);
    try {
      await onSavePlan(updatedPlan);
    } catch (error) {
      setMutateError(error instanceof Error ? error.message : '更新失败');
    } finally {
      setIsMutatingItem(false);
    }
  };

  /**
   * 进入"删除确认"前先 ensure 任务列表已加载，让确认对话框能显示准确的
   * "挂接 N 条任务会失去关联"提示，避免用户看到模糊的"删除后不可恢复"
   * 就以为是干净删除（其实可能还挂着任务）。
   */
  const handleStartDelete = async () => {
    if (!selectedItemId) return;
    if (!itemTasks[selectedItemId]) {
      await fetchTasksForItem(selectedItemId);
    }
    setShowDeleteConfirm(true);
  };

  const handleDeleteItem = async () => {
    if (!onSavePlan || !selectedItem || !selectedItemParentPlan) return;
    const now = new Date().toISOString();
    const updatedPlan: OrgDepartmentPlanSettings = {
      ...selectedItemParentPlan,
      items: (selectedItemParentPlan.items || []).filter((it) => it.id !== selectedItem.id),
      updatedAt: now,
    };
    setIsMutatingItem(true);
    setMutateError(null);
    try {
      await onSavePlan(updatedPlan);
      setShowDeleteConfirm(false);
      setSelectedItemId(null);
    } catch (error) {
      setMutateError(error instanceof Error ? error.message : '删除失败');
    } finally {
      setIsMutatingItem(false);
    }
  };

  const canMutateSelectedItem = Boolean(onSavePlan) && (() => {
    if (!selectedItemParentPlan) return false;
    // Org-level plan: only admin can edit
    if (!selectedItemParentPlan.departmentId) return isAdmin;
    // Department plan: admin can edit any; others only their own department
    return isAdmin || selectedItemParentPlan.departmentId === userDeptId;
  })();

  // ─── Add-Plan Modal state ──────────────────────────────────────────
  // 改成"新增计划"语义：进入时如果该部门+周期已有 plan，预填现有项，提交时复用
  // plan.id 让后端走 upsert（不丢已挂任务）。
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [createDepartmentId, setCreateDepartmentId] = useState<string>('');
  const [createClientId, setCreateClientId] = useState<string>('');
  const [createCycleType, setCreateCycleType] = useState<CycleType>('month');
  const [createPeriodValue, setCreatePeriodValue] = useState<string>(defaultPeriodValue('month'));
  const [createSummary, setCreateSummary] = useState('');
  const [pasteText, setPasteText] = useState('');
  const [draftItems, setDraftItems] = useState<DraftItem[]>([]);
  const [editingExistingPlanId, setEditingExistingPlanId] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [isParsing, setIsParsing] = useState(false);
  const [parseSummary, setParseSummary] = useState<string>('');
  const [parseConfidence, setParseConfidence] = useState<'low' | 'medium' | 'high' | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);

  /**
   * 找出 modal 当前部门 + 周期值对应的现有 plan（如果有的话）。
   * - 组织级 plan：departmentId 为 null
   * - 部门级 plan：departmentId === 选中的部门 id
   * - weekLabel 必须严格匹配（即"2026-05" 不会命中 "2026-05-修订"）
   */
  const findExistingPlanForScope = (
    deptIdOrOrg: string,
    periodKey: string,
  ): OrgDepartmentPlanSettings | null => {
    const trimmedPeriod = periodKey.trim();
    if (!trimmedPeriod) return null;
    const resolvedDept = deptIdOrOrg === ORG_LEVEL_ID ? null : deptIdOrOrg;
    return (
      effectiveValue.departmentPlans.find(
        (p) => (p.departmentId || null) === resolvedDept && (p.weekLabel || '').trim() === trimmedPeriod,
      ) || null
    );
  };

  /** 把 existingPlan.items 转成可编辑的 DraftItem 列表，**保留原 item.id** 以便提交时复用。 */
  const draftFromExisting = (plan: OrgDepartmentPlanSettings): DraftItem[] =>
    (plan.items || []).map((it) => ({
      id: it.id, // 关键：复用原 id，确保后端 upsert 时挂接的任务不丢
      title: it.title || '',
      statement: it.statement || '',
      expectedOutput: it.expectedOutput || '',
      status: (it.status || 'active') as OrgDepartmentPlanItemStatus,
    }));

  const openCreateModal = () => {
    // Admin defaults to org-level (季度/年度报告主体一般是组织); others default to own department.
    const defaultDept = isAdmin
      ? ORG_LEVEL_ID
      : (userDeptId || '');
    const defaultCycle: CycleType = 'month';
    const defaultPeriod = defaultPeriodValue(defaultCycle);
    setCreateDepartmentId(defaultDept);
    setCreateCycleType(defaultCycle);
    setCreatePeriodValue(defaultPeriod);
    setPasteText('');
    setCreateError(null);
    setParseSummary('');
    setParseConfidence(null);

    // 检测该部门+周期是否已有 plan：有则进"追加"模式，预填现有项 + summary
    const existing = findExistingPlanForScope(defaultDept, defaultPeriod);
    if (existing) {
      setCreateClientId(existing.clientId || '');
      setEditingExistingPlanId(existing.id);
      setCreateSummary(existing.summary || '');
      setDraftItems(draftFromExisting(existing));
    } else {
      setCreateClientId('');
      setEditingExistingPlanId(null);
      setCreateSummary('');
      setDraftItems([newDraftItem()]);
    }
    setIsCreateOpen(true);
  };

  /**
   * 当用户在 modal 里切换部门/周期类型/周期值时，重新匹配是否有现有 plan：
   * - 切到已有 plan 的 scope：自动加载已有项 + summary，进入追加模式
   * - 切到未制定的 scope：清空，进入新建模式
   * 注意：只在 isCreateOpen 时跑，避免关 modal 后还触发。
   */
  useEffect(() => {
    if (!isCreateOpen) return;
    const existing = findExistingPlanForScope(createDepartmentId, createPeriodValue);
    const targetId = existing?.id || null;
    if (targetId === editingExistingPlanId) return; // 没换 plan，保留用户已编辑内容
    setEditingExistingPlanId(targetId);
    if (existing) {
      setCreateClientId(existing.clientId || '');
      setCreateSummary(existing.summary || '');
      setDraftItems(draftFromExisting(existing));
    } else {
      setCreateClientId('');
      setCreateSummary('');
      setDraftItems([newDraftItem()]);
    }
    setParseSummary('');
    setParseConfidence(null);
    setCreateError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [createDepartmentId, createPeriodValue, isCreateOpen]);

  const closeCreateModal = () => {
    if (isSaving || isParsing) return;
    setIsCreateOpen(false);
  };

  const createModalBackdropHandlers = useBackdropClickClose(closeCreateModal, !isSaving && !isParsing);

  const handleParseText = async () => {
    if (!pasteText.trim()) {
      setCreateError('请先粘贴计划文本再进行解析');
      return;
    }
    setIsParsing(true);
    setCreateError(null);
    setParseSummary('');
    setParseConfidence(null);
    try {
      const isOrgLevel = createDepartmentId === ORG_LEVEL_ID;
      const dept = effectiveValue.departments.find((d) => d.id === createDepartmentId);
      const result = await parseDepartmentPlan({
        text: pasteText,
        organizationName,
        scopeKind: isOrgLevel ? 'org' : 'department',
        scopeName: isOrgLevel ? organizationName : (dept?.name || ''),
        periodKey: createPeriodValue.trim(),
        cycleType: createCycleType,
      });
      if (!result.items || result.items.length === 0) {
        setCreateError('AI 没能从原文中提取出计划项。可手动调整原文后重试，或在下方直接逐条添加。');
        return;
      }
      const items: DraftItem[] = result.items.map((it) => ({
        ...newDraftItem(),
        title: it.title,
        statement: it.statement || '',
        expectedOutput: it.expectedOutput || '',
      }));
      setDraftItems(items);
      setParseSummary(result.summary || '');
      setParseConfidence(result.confidence || null);
      if (result.summary && !createSummary.trim()) {
        setCreateSummary(result.summary);
      }
    } catch (error) {
      setCreateError(error instanceof Error ? `AI 解析失败：${error.message}` : 'AI 解析失败');
    } finally {
      setIsParsing(false);
    }
  };

  const handleSubmitCreate = async () => {
    if (!onSavePlan) return;
    if (!createDepartmentId) { setCreateError('请选择计划主体（组织或部门）'); return; }
    if (!createPeriodValue.trim()) { setCreateError('请填写周期值'); return; }
    const validItems = draftItems.filter((it) => it.title.trim().length > 0);
    if (validItems.length === 0) { setCreateError('至少需要填写一条计划项标题'); return; }

    const now = new Date().toISOString();
    const resolvedDepartmentId = createDepartmentId === ORG_LEVEL_ID ? null : createDepartmentId;
    const existingPlan = editingExistingPlanId
      ? effectiveValue.departmentPlans.find((p) => p.id === editingExistingPlanId) || null
      : null;
    // 关键：保留 existing plan.id 让后端走 upsert（不丢已挂任务的关联）；
    // 已有 item 复用其 id（避免 task_plan_links.department_plan_item_id 失效）。
    const plan: OrgDepartmentPlanSettings = {
      id: existingPlan?.id || `plan-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      departmentId: resolvedDepartmentId,
      clientId: createClientId || null,
      weekLabel: createPeriodValue.trim(),
      ownerUserId: existingPlan?.ownerUserId ?? null,
      summary: createSummary.trim(),
      majorRisks: existingPlan?.majorRisks || [],
      dependencies: existingPlan?.dependencies || [],
      status: existingPlan?.status || 'active',
      items: validItems.map((it, index) => {
        // DraftItem.id 以 `item-` 开头说明来自现有 plan（在 draftFromExisting 时复用了）；
        // 以 `tmp-` 或其它开头则是用户在 modal 里新增的草稿，需要分配真实 id。
        const existingItem = existingPlan?.items.find((p) => p.id === it.id) || null;
        const isExisting = Boolean(existingItem);
        return {
          id: isExisting ? it.id : `item-${Date.now()}-${index}-${Math.random().toString(36).slice(2, 6)}`,
          focusItemId: existingItem?.focusItemId ?? null,
          title: it.title.trim(),
          statement: it.statement.trim(),
          ownerUserId: existingItem?.ownerUserId ?? null,
          status: it.status,
          expectedOutput: it.expectedOutput.trim(),
          sortOrder: index,
          updatedAt: now,
        };
      }),
      updatedAt: now,
    };

    setIsSaving(true);
    setCreateError(null);
    try {
      await onSavePlan(plan);
      const scopeId = resolvedDepartmentId || ORG_LEVEL_ID;
      setSelectedPeriodByScope((previous) => ({ ...previous, [scopeId]: plan.weekLabel }));
      setExpandedScopeId(scopeId);
      setIsCreateOpen(false);
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : '保存失败');
    } finally {
      setIsSaving(false);
    }
  };

  const subtitleText = isAdmin
    ? '管理员视图 · 看全部部门当前周期计划与挂接任务'
    : currentUser?.departmentName
      ? `${currentUser.departmentName} · 部门负责人视图`
      : '部门视图';

  // 组织计划首次进入时，严格部门/周期/行动是一个三位一体快照。等待期间不能
  // 先渲染 EMPTY_ORG_MODEL_SETTINGS 里的“当前组织 / 0 / 0”，否则用户会把
  // 一段加载过程误判成权限丢失或计划被清空。
  if (isLoading) {
    return (
      <div className="overflow-y-auto h-full">
        <div className="mx-auto max-w-7xl px-6 lg:px-8 pt-8 pb-20 space-y-8">
          <div>
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">PLAN WORKSHOP</p>
            <h1 className="mt-2 text-[22px] font-light tracking-tight text-gray-900">组织计划</h1>
            <p className="mt-1 text-[12px] text-gray-500 leading-relaxed">{subtitleText}</p>
          </div>
          <div className="rounded-2xl border border-blue-100 bg-blue-50/60 px-5 py-10 text-center text-[12px] text-blue-700">
            正在读取正式组织、部门和计划，请稍候…
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="overflow-y-auto h-full">
      <div className="mx-auto max-w-7xl px-6 lg:px-8 pt-8 pb-20 space-y-10">
        {/* Header ─────────────────────────────────────────── */}
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="min-w-0">
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">PLAN WORKSHOP</p>
            <h1 className="mt-2 text-[22px] font-light tracking-tight text-gray-900">组织计划</h1>
            <p className="mt-1 text-[12px] text-gray-500 leading-relaxed">{subtitleText}</p>
            {isLoading && <p className="mt-1 text-[11px] text-blue-600">正在读取正式组织、部门和计划…</p>}
            {!isLoading && loadError && <p className="mt-1 text-[11px] text-rose-600">组织计划加载失败：{loadError}</p>}
            {isRelationRefreshing && <p className="mt-1 text-[11px] text-blue-600">正在更新计划与任务关联…</p>}
          </div>
          {canCreatePlan && (
            <button
              type="button"
              onClick={openCreateModal}
              className="shrink-0 inline-flex items-center gap-1.5 rounded-xl bg-[#5B7BFE] text-white px-3.5 py-2 text-[12px] font-bold hover:bg-[#4a6ae8] transition-colors"
            >
              <Plus size={14} /> 新增计划
            </button>
          )}
        </div>

        {isLoading && effectiveValue.departments.length === 0 ? (
          <div className="rounded-2xl border border-blue-100 bg-blue-50/60 px-5 py-8 text-center text-[12px] text-blue-700">
            正在读取正式组织、部门和计划，请稍候…
          </div>
        ) : null}

        {/* PLAN OVERVIEW · 4 个 KPI hero block ────────────── */}
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">PLAN OVERVIEW</p>
          <div className="mt-5 grid grid-cols-2 md:grid-cols-4 gap-x-8 gap-y-6">
            <div className="flex flex-col">
              <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">部门覆盖</p>
              <div className="mt-3 flex items-baseline gap-1.5">
                <span className="text-[32px] leading-none font-light tracking-tight text-gray-900">{deptsWithPlan}</span>
                <span className="text-[14px] leading-none font-light text-gray-400">/ {totalDepts}</span>
              </div>
              <div className={`mt-2 h-[2px] w-8 rounded-full ${deptsWithPlan === totalDepts && totalDepts > 0 ? 'bg-emerald-500' : 'bg-transparent'}`} />
              <p className="mt-2 text-[11px] text-gray-400">已制定计划的部门</p>
            </div>
            <div className="flex flex-col">
              <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">待制定</p>
              <div className="mt-3 flex items-baseline gap-1.5">
                <span className={`text-[32px] leading-none font-light tracking-tight ${deptsWithoutPlan > 0 ? 'text-amber-600' : 'text-gray-900'}`}>{deptsWithoutPlan}</span>
                <span className="text-[14px] leading-none font-light text-gray-400">个部门</span>
              </div>
              <div className={`mt-2 h-[2px] w-8 rounded-full ${deptsWithoutPlan > 0 ? 'bg-amber-500' : 'bg-transparent'}`} />
              <p className="mt-2 text-[11px] text-gray-400">{deptsWithoutPlan > 0 ? '需要尽快制定' : '全部已覆盖'}</p>
            </div>
            <div className="flex flex-col">
              <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">未完成项</p>
              <div className="mt-3 flex items-baseline gap-1.5">
                <span className="text-[32px] leading-none font-light tracking-tight text-gray-900">{totalUnfinished}</span>
                <span className="text-[14px] leading-none font-light text-gray-400">项</span>
              </div>
              <div className="mt-2 h-[2px] w-8 rounded-full bg-transparent" />
              <p className="mt-2 text-[11px] text-gray-400">所有进行中的计划项</p>
            </div>
            <div className="flex flex-col">
              <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">平均完成率</p>
              <div className="mt-3 flex items-baseline gap-1.5">
                <span className="text-[32px] leading-none font-light tracking-tight text-gray-900">{rows.length > 0 ? avgCompleteness : '—'}</span>
                {rows.length > 0 && <span className="text-[14px] leading-none font-light text-gray-400">%</span>}
              </div>
              <div className={`mt-2 h-[2px] w-8 rounded-full ${avgCompleteness >= 80 ? 'bg-emerald-500' : avgCompleteness >= 40 ? 'bg-amber-500' : 'bg-transparent'}`} />
              <p className="mt-2 text-[11px] text-gray-400">跨部门加权平均</p>
            </div>
          </div>
        </div>

        {/* 主体双栏 ─ 部门列表 + 详情 ────────────────────── */}
        {rows.length === 0 ? (
          <div className="border-t border-gray-100 pt-10">
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">DEPARTMENTS</p>
            <p className="mt-5 text-[13px] text-gray-400">
              {isAdmin ? '当前组织尚未建立部门' : '你的部门信息未配置,请联系管理员'}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-[1.1fr_1fr] gap-x-8 gap-y-8 border-t border-gray-100 pt-10">
            {/* 左栏 · 部门列表 */}
            <div>
              <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400 mb-4">DEPARTMENTS · 部门 · 当前周期</p>
              <div className="max-h-[640px] overflow-y-auto -mx-2">
                {rows.map((row) => (
                  <DepartmentRowBlock
                    key={row.scopeId}
                    row={row}
                    expanded={expandedScopeId === row.scopeId}
                    onToggle={() => setExpandedScopeId(expandedScopeId === row.scopeId ? null : row.scopeId)}
                    selectedItemId={selectedItemId}
                    onSelectItem={handleSelectItem}
                    taskCountByItemId={taskCountByItemId}
                    onSelectPeriod={(period) => {
                      setSelectedPeriodByScope((previous) => ({ ...previous, [row.scopeId]: period }));
                      setSelectedItemId(null);
                    }}
                  />
                ))}
              </div>
            </div>

            {/* 右栏 · 选中详情 */}
            <div>
              <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400 mb-4">DETAIL · 计划项详情</p>
              <div className="max-h-[640px] overflow-y-auto">
                {!selectedItem ? (
                  <div className="py-12 text-center">
                    <p className="text-[12px] text-gray-400">选择左侧某条计划项</p>
                    <p className="mt-1.5 text-[11px] text-gray-300">这里会显示标题、状态、期望产出和挂接的任务</p>
                  </div>
                ) : (
                  <div className="space-y-7">
                    {/* 头部 ─ scope eyebrow + 标题 + 操作按钮 */}
                    <div>
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex-1 min-w-0">
                          <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">{selectedItemScopeName || '未知主体'} · 计划项</p>
                          {isEditingItem ? (
                            <input
                              type="text"
                              value={editDraft.title}
                              onChange={(e) => setEditDraft((prev) => ({ ...prev, title: e.target.value }))}
                              placeholder="计划项标题"
                              autoFocus
                              className="mt-2 w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-[18px] font-light tracking-tight text-gray-900 outline-none focus:border-[#5B7BFE]"
                            />
                          ) : (
                            <h3 className="mt-2 text-[20px] font-light tracking-tight text-gray-900 leading-tight">{selectedItem.title}</h3>
                          )}
                        </div>
                        {canMutateSelectedItem && (
                          <div className="shrink-0 flex items-center gap-0.5">
                            {isEditingItem ? (
                              <>
                                <button
                                  type="button"
                                  onClick={handleCancelEdit}
                                  disabled={isMutatingItem}
                                  className="rounded-md border border-gray-200 bg-white px-3 py-1.5 text-[11px] font-bold text-gray-600 hover:bg-gray-50 disabled:opacity-50 transition-colors"
                                >
                                  取消
                                </button>
                                <button
                                  type="button"
                                  onClick={() => void handleSaveEdit()}
                                  disabled={isMutatingItem}
                                  className="ml-1.5 rounded-md bg-[#5B7BFE] text-white px-3 py-1.5 text-[11px] font-bold hover:bg-[#4a6ae8] disabled:opacity-60 transition-colors"
                                >
                                  {isMutatingItem ? '保存中…' : '保存'}
                                </button>
                              </>
                            ) : (
                              <>
                                {selectedItem.status === 'done' ? (
                                  <button
                                    type="button"
                                    onClick={() => void handleToggleItemDone()}
                                    disabled={isMutatingItem}
                                    className="inline-flex items-center justify-center rounded-md p-1.5 text-gray-400 hover:text-amber-600 hover:bg-amber-50 disabled:opacity-40 transition-colors"
                                    title="取消已完成状态,恢复为进行中"
                                    aria-label="取消完成"
                                  >
                                    <RotateCcw size={15} />
                                  </button>
                                ) : (
                                  <button
                                    type="button"
                                    onClick={() => void handleToggleItemDone()}
                                    disabled={isMutatingItem}
                                    className="inline-flex items-center justify-center rounded-md p-1.5 text-gray-400 hover:text-emerald-600 hover:bg-emerald-50 disabled:opacity-40 transition-colors"
                                    title="标记本条计划项为已完成"
                                    aria-label="完成"
                                  >
                                    <Check size={15} />
                                  </button>
                                )}
                                {onGenerateTaskFromPlanItem && (
                                  <button
                                    type="button"
                                    onClick={() => selectedItemParentPlan && onGenerateTaskFromPlanItem(selectedItem, selectedItemScopeName || '', selectedItemParentPlan)}
                                    disabled={isMutatingItem}
                                    className="inline-flex items-center justify-center rounded-md p-1.5 text-gray-400 hover:text-violet-600 hover:bg-violet-50 disabled:opacity-40 transition-colors"
                                    title="生成任务:把这条计划项作为内容新建一条任务并自动挂接"
                                    aria-label="生成任务"
                                  >
                                    <ArrowRight size={15} />
                                  </button>
                                )}
                                <button
                                  type="button"
                                  onClick={handleEnterEdit}
                                  disabled={isMutatingItem}
                                  className="inline-flex items-center justify-center rounded-md p-1.5 text-gray-400 hover:text-[#5B7BFE] hover:bg-[#EEF2FF] disabled:opacity-40 transition-colors"
                                  title="编辑计划项"
                                  aria-label="编辑"
                                >
                                  <Pencil size={15} />
                                </button>
                                <button
                                  type="button"
                                  onClick={() => void handleStartDelete()}
                                  disabled={isMutatingItem}
                                  className="inline-flex items-center justify-center rounded-md p-1.5 text-gray-400 hover:text-rose-600 hover:bg-rose-50 disabled:opacity-40 transition-colors"
                                  title="删除计划项"
                                  aria-label="删除"
                                >
                                  <Trash2 size={15} />
                                </button>
                              </>
                            )}
                          </div>
                        )}
                      </div>

                      {isEditingItem ? (
                        <div className="mt-3 space-y-2.5">
                          <div>
                            <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-gray-500 mb-1.5">说明</p>
                            <textarea
                              value={editDraft.statement}
                              onChange={(e) => setEditDraft((prev) => ({ ...prev, statement: e.target.value }))}
                              placeholder="可选"
                              rows={2}
                              className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-[12.5px] text-gray-700 outline-none focus:border-[#5B7BFE] resize-none"
                            />
                          </div>
                          <div>
                            <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-gray-500 mb-1.5">期望产出</p>
                            <textarea
                              value={editDraft.expectedOutput}
                              onChange={(e) => setEditDraft((prev) => ({ ...prev, expectedOutput: e.target.value }))}
                              placeholder="例如:输出一份 20 页方案 + 3 个 case study"
                              rows={2}
                              className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-[12.5px] text-gray-700 outline-none focus:border-[#5B7BFE] resize-none"
                            />
                          </div>
                          <div>
                            <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-gray-500 mb-1.5">状态</p>
                            <select
                              value={editDraft.status}
                              onChange={(e) => setEditDraft((prev) => ({ ...prev, status: e.target.value as OrgDepartmentPlanItemStatus }))}
                              className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-[12.5px] text-gray-700 outline-none focus:border-[#5B7BFE]"
                            >
                              <option value="active">进行中</option>
                              <option value="paused">暂停</option>
                              <option value="done">已完成</option>
                              <option value="dropped">已废弃</option>
                            </select>
                          </div>
                        </div>
                      ) : (
                        <div className="mt-3 space-y-2.5">
                          <div className="flex items-center gap-2">
                            <PlanItemStatusBadge status={selectedItem.status} />
                          </div>
                          {selectedItem.statement && (
                            <p className="text-[12.5px] leading-6 text-gray-600">{selectedItem.statement}</p>
                          )}
                          {selectedItem.expectedOutput && (
                            <div className="border-t border-gray-100 pt-2.5">
                              <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-gray-400 mb-1">期望产出</p>
                              <p className="text-[12.5px] text-gray-700 leading-6">{selectedItem.expectedOutput}</p>
                            </div>
                          )}
                        </div>
                      )}

                      {mutateError && (
                        <p className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-[11px] text-rose-600">{mutateError}</p>
                      )}

                      {showDeleteConfirm && !isEditingItem && (
                        <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-3">
                          <p className="text-[12px] font-bold text-rose-700">确认删除这条计划项?</p>
                          <p className="mt-1 text-[11px] text-rose-600 leading-5">
                            删除后不可恢复{itemTasks[selectedItem.id] && itemTasks[selectedItem.id].length > 0
                              ? `;已挂接的 ${itemTasks[selectedItem.id].length} 条任务会失去与本计划项的关联(任务本身不会被删)`
                              : ''}。
                          </p>
                          <div className="mt-2.5 flex items-center justify-end gap-1.5">
                            <button
                              type="button"
                              onClick={() => setShowDeleteConfirm(false)}
                              disabled={isMutatingItem}
                              className="rounded-md border border-gray-200 bg-white px-3 py-1.5 text-[11px] font-bold text-gray-600 hover:bg-gray-50 disabled:opacity-50 transition-colors"
                            >
                              取消
                            </button>
                            <button
                              type="button"
                              onClick={() => void handleDeleteItem()}
                              disabled={isMutatingItem}
                              className="rounded-md bg-rose-500 text-white px-3 py-1.5 text-[11px] font-bold hover:bg-rose-600 disabled:opacity-60 transition-colors"
                            >
                              {isMutatingItem ? '删除中…' : '确认删除'}
                            </button>
                          </div>
                        </div>
                      )}
                    </div>

                    {/* 挂接任务区 ─ uppercase eyebrow + hairline 简洁列表 */}
                    <div className="border-t border-gray-100 pt-5">
                      <div className="flex items-center justify-between mb-2">
                        <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">
                          TASKS · 挂接任务{selectedItemId && itemTasks[selectedItemId] ? ` · ${itemTasks[selectedItemId].length} 条` : ''}
                        </p>
                        <button
                          type="button"
                          onClick={() => void handleRefreshTasks()}
                          disabled={loadingItemId === selectedItemId}
                          title="重新拉取挂接的任务"
                          className="inline-flex items-center justify-center rounded-md p-1 text-gray-400 hover:text-gray-700 hover:bg-gray-100 disabled:opacity-40 transition-colors"
                        >
                          <RefreshCw size={12} className={loadingItemId === selectedItemId ? 'animate-spin' : ''} />
                        </button>
                      </div>
                      {loadingItemId === selectedItemId ? (
                        <p className="text-[12px] text-gray-400 py-3">加载中…</p>
                      ) : itemTaskError ? (
                        <p className="text-[12px] text-rose-500 py-3">{itemTaskError}</p>
                      ) : !itemTasks[selectedItemId!] || itemTasks[selectedItemId!].length === 0 ? (
                        <p className="text-[11.5px] text-gray-400 py-4 leading-5">
                          这条计划项还没有任何任务挂接。<br />
                          可在「任务与日程」新建任务,在表单的"计划关联"模块里挂到此项。
                        </p>
                      ) : (
                        <div>
                          {itemTasks[selectedItemId!].map((task) => (
                            <PlanItemTaskCard
                              key={task.id}
                              task={task}
                              onOpen={onOpenTask ? () => onOpenTask(task) : undefined}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ──────────────── Create-Plan Modal ──────────────── */}
      {isCreateOpen && (
        <div
          className="fixed inset-0 z-[1000] flex items-center justify-center bg-gray-900/20 backdrop-blur-sm px-6"
          {...createModalBackdropHandlers}
        >
          <div className="w-full max-w-2xl rounded-2xl bg-white shadow-[0_28px_90px_rgba(15,23,42,0.18)] flex flex-col" style={{ maxHeight: '90vh' }}>
            <div className="px-7 pt-6 pb-5 flex items-start justify-between shrink-0">
              <div className="min-w-0">
                <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">
                  {editingExistingPlanId ? 'EDIT PLAN · 修改' : 'NEW PLAN · 新建'}
                </p>
                <h3 className="mt-1.5 text-[20px] font-light tracking-tight text-gray-900">
                  {editingExistingPlanId ? '修改计划' : '新增计划'}
                </h3>
                {editingExistingPlanId && (
                  <p className="mt-1.5 text-[11px] text-amber-700 leading-5">
                    已有 {(effectiveValue.departmentPlans.find((p) => p.id === editingExistingPlanId)?.items?.length) || 0} 项计划项。保存会追加新增项 / 更新已修改项,不会删除已挂任务关系。
                  </p>
                )}
              </div>
              <button type="button" onClick={closeCreateModal} disabled={isSaving} className="shrink-0 -mt-1 -mr-1 inline-flex items-center justify-center rounded-md p-1 text-gray-400 hover:text-gray-700 hover:bg-gray-100 disabled:opacity-50 transition-colors">
                <X size={18} />
              </button>
            </div>

            <div className="px-7 pb-6 space-y-6 overflow-y-auto">
              <div className="space-y-3">
                <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">BASIC · 主体与周期</p>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-gray-500 mb-1.5">计划主体</p>
                  <select
                    value={createDepartmentId}
                    onChange={(e) => setCreateDepartmentId(e.target.value)}
                    disabled={!isAdmin && visibleDepartments.length <= 1}
                    className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-[12.5px] font-medium text-gray-900 outline-none focus:border-[#5B7BFE] disabled:bg-gray-50 disabled:text-gray-400"
                  >
                    {canCreateOrgPlan && (
                      <option value={ORG_LEVEL_ID}>{organizationName}（组织级）</option>
                    )}
                    {visibleDepartments.map((dept) => (
                      <option key={dept.id} value={dept.id}>{dept.name}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-gray-500 mb-1.5">周期类型</p>
                  <select
                    value={createCycleType}
                    onChange={(e) => {
                      const next = e.target.value as CycleType;
                      setCreateCycleType(next);
                      setCreatePeriodValue(defaultPeriodValue(next));
                    }}
                    className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-[12.5px] font-medium text-gray-900 outline-none focus:border-[#5B7BFE]"
                  >
                    {Object.entries(CYCLE_LABELS).map(([key, label]) => (
                      <option key={key} value={key}>{label}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-gray-500 mb-1.5">
                    周期值
                    <span className="text-gray-300 font-normal ml-1 normal-case tracking-normal">
                      {createCycleType === 'month' && 'YYYY-MM'}
                      {createCycleType === 'quarter' && 'YYYY-Q1~Q4'}
                      {createCycleType === 'year' && 'YYYY'}
                      {createCycleType === 'week' && '周一至周日'}
                    </span>
                  </p>
                  {createCycleType === 'week' ? (
                    <div className="space-y-1">
                      <input
                        type="date"
                        value={weekStartInputValue(createPeriodValue)}
                        onChange={(event) => {
                          const date = new Date(`${event.target.value}T12:00:00`);
                          if (!Number.isNaN(date.getTime())) setCreatePeriodValue(isoWeekKeyForDate(date));
                        }}
                        className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-[12.5px] font-medium text-gray-900 outline-none focus:border-[#5B7BFE]"
                      />
                      <p className="text-[10.5px] text-gray-500">{formatPlanningPeriodLabel(createPeriodValue)}</p>
                    </div>
                  ) : (
                    <input
                      type={createCycleType === 'month' ? 'month' : 'text'}
                      value={createPeriodValue}
                      onChange={(e) => setCreatePeriodValue(e.target.value)}
                      placeholder={defaultPeriodValue(createCycleType) || '自定义'}
                      className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-[12.5px] font-medium text-gray-900 outline-none focus:border-[#5B7BFE]"
                    />
                  )}
                </div>
                <div>
                  <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-gray-500 mb-1.5">关联项目 · 可选</p>
                  <select
                    value={createClientId}
                    onChange={(event) => setCreateClientId(event.target.value)}
                    className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-[12.5px] font-medium text-gray-900 outline-none focus:border-[#5B7BFE]"
                  >
                    <option value="">不关联项目</option>
                    {clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
                  </select>
                </div>
                </div>
              </div>

              {/* 计划总述 */}
              <div className="border-t border-gray-100 pt-5">
                <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-gray-500 mb-1.5">计划总述 · 可选</p>
                <input
                  type="text"
                  value={createSummary}
                  onChange={(e) => setCreateSummary(e.target.value)}
                  placeholder="例如:5 月主攻新客户开发与老客户复购"
                  className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-[12.5px] text-gray-900 outline-none focus:border-[#5B7BFE]"
                />
              </div>

              {/* 计划项列表 */}
              <div className="border-t border-gray-100 pt-5">
                <div className="flex items-center justify-between mb-3">
                  <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">ITEMS · 计划项 · {draftItems.length} 条</p>
                  <button
                    type="button"
                    onClick={() => setDraftItems((prev) => [...prev, newDraftItem()])}
                    className="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2.5 py-1 text-[11px] font-bold text-[#5B7BFE] hover:bg-gray-50 transition-colors"
                  >
                    <Plus size={12} /> 新增一条
                  </button>
                </div>
                <div className="space-y-2 max-h-[280px] overflow-y-auto -mx-1 px-1">
                  {draftItems.length === 0 ? (
                    <p className="text-[11px] text-gray-400 py-4 text-center leading-5">
                      还没有计划项<br />
                      点上方"+ 新增一条"逐条填写,或下方粘贴整段文本让 AI 解析
                    </p>
                  ) : (
                    draftItems.map((item, index) => (
                      <div key={item.id} className="border-t border-gray-100 pt-3 first:border-t-0 first:pt-0">
                        <div className="flex items-start gap-2.5">
                          <span className="text-[10px] font-bold uppercase tracking-[0.1em] text-gray-400 mt-2.5 w-6 text-center tabular-nums">{String(index + 1).padStart(2, '0')}</span>
                          <div className="flex-1 space-y-2">
                            <input
                              type="text"
                              value={item.title}
                              onChange={(e) => setDraftItems((prev) => prev.map((it) => it.id === item.id ? { ...it, title: e.target.value } : it))}
                              placeholder="计划项标题"
                              className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-[13px] font-medium text-gray-900 outline-none focus:border-[#5B7BFE]"
                            />
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                              <textarea
                                value={item.statement}
                                onChange={(e) => setDraftItems((prev) => prev.map((it) => it.id === item.id ? { ...it, statement: e.target.value } : it))}
                                placeholder="说明(可选)"
                                rows={2}
                                className="rounded-xl border border-gray-200 bg-white px-3 py-2 text-[12px] text-gray-700 outline-none resize-none focus:border-[#5B7BFE]"
                              />
                              <textarea
                                value={item.expectedOutput}
                                onChange={(e) => setDraftItems((prev) => prev.map((it) => it.id === item.id ? { ...it, expectedOutput: e.target.value } : it))}
                                placeholder="期望产出(可选,如:20 页方案 + 3 个 case)"
                                rows={2}
                                className="rounded-xl border border-gray-200 bg-white px-3 py-2 text-[12px] text-gray-700 outline-none resize-none focus:border-[#5B7BFE]"
                              />
                            </div>
                          </div>
                          <button
                            type="button"
                            onClick={() => setDraftItems((prev) => prev.filter((it) => it.id !== item.id))}
                            className="text-gray-300 hover:text-rose-500 mt-2.5 transition-colors"
                            title="删除这一条"
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>

              {/* AI 智能解析 ─ Foldable 风格 */}
              <details className="border-t border-gray-100 pt-5 group" open={draftItems.length === 0 && !editingExistingPlanId}>
                <summary className="cursor-pointer select-none flex items-center gap-2 list-none -mx-2 px-2 py-2 rounded-md hover:bg-gray-50/70 transition-colors">
                  <Sparkles size={14} className="text-[#5B7BFE]" />
                  <div className="flex-1 min-w-0">
                    <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">AI · 智能解析</p>
                    <p className="text-[11px] text-gray-500 mt-0.5">粘贴整段计划原文 · AI 自动拆条 / 抽期望产出 / 剥列表标记</p>
                  </div>
                  <ChevronDown size={14} className="text-gray-400 transition-transform group-open:rotate-180 shrink-0" />
                </summary>
                <div className="pt-3 space-y-2.5">
                  <textarea
                    value={pasteText}
                    onChange={(e) => setPasteText(e.target.value)}
                    placeholder={'粘贴整段计划原文,比如月度计划、季度报告、年度战略等。\n\nAI 会自动:\n• 把每条计划的标题、说明、期望产出拆到不同字段\n• 合并多行属于同一条的内容(不会按行拆碎)\n• 忽略章节标题、空白段落、序号'}
                    rows={5}
                    disabled={isParsing}
                    className="w-full rounded-xl border border-gray-200 bg-white px-3 py-2.5 text-[12px] text-gray-700 outline-none resize-none focus:border-[#5B7BFE] disabled:bg-gray-50"
                  />
                  <div className="flex items-center justify-between gap-2 flex-wrap">
                    <button
                      type="button"
                      onClick={() => void handleParseText()}
                      disabled={!pasteText.trim() || isParsing}
                      className="inline-flex items-center gap-1.5 rounded-md bg-[#5B7BFE] text-white px-3 py-1.5 text-[11px] font-bold hover:bg-[#4a6ae8] disabled:opacity-50 transition-colors"
                    >
                      {isParsing ? (
                        <>
                          <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-white/40 border-t-white" />
                          AI 解析中…
                        </>
                      ) : (
                        <>
                          <Sparkles size={12} />
                          AI 解析为计划项
                        </>
                      )}
                    </button>
                    {parseConfidence && (
                      <div className="flex items-center gap-2 text-[10.5px]">
                        <span className={`rounded-full border px-2 py-0.5 font-bold uppercase tracking-[0.12em] ${
                          parseConfidence === 'high' ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                          : parseConfidence === 'medium' ? 'border-amber-200 bg-amber-50 text-amber-700'
                          : 'border-rose-200 bg-rose-50 text-rose-600'
                        }`}>
                          置信度 {parseConfidence === 'high' ? '高' : parseConfidence === 'medium' ? '中' : '低'}
                        </span>
                        {parseSummary && (
                          <span className="text-gray-500 truncate max-w-[280px]" title={parseSummary}>{parseSummary}</span>
                        )}
                      </div>
                    )}
                  </div>
                  {editingExistingPlanId && (
                    <p className="text-[10.5px] text-amber-700 mt-1 leading-5">
                      AI 解析会<strong>覆盖</strong>上方计划项列表。如果只想补 1-2 条新项,建议直接用"+ 新增"按钮。
                    </p>
                  )}
                </div>
              </details>

              {createError && (
                <p className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-[12px] text-rose-600">{createError}</p>
              )}
            </div>

            <div className="px-7 py-4 border-t border-gray-100 flex items-center justify-end gap-2 shrink-0">
              <button
                type="button"
                onClick={closeCreateModal}
                disabled={isSaving}
                className="rounded-md border border-gray-200 bg-white px-4 py-2 text-[12px] font-bold text-gray-600 hover:bg-gray-50 disabled:opacity-50 transition-colors"
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleSubmitCreate}
                disabled={isSaving}
                className="rounded-md bg-[#5B7BFE] text-white px-4 py-2 text-[12px] font-bold hover:bg-[#4a6ae8] disabled:opacity-60 transition-colors"
              >
                {isSaving ? '保存中…' : editingExistingPlanId ? '保存修改' : '新增计划'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function DepartmentRowBlock({
  row,
  expanded,
  onToggle,
  selectedItemId,
  onSelectItem,
  taskCountByItemId,
  onSelectPeriod,
}: {
  row: DepartmentRow;
  expanded: boolean;
  onToggle: () => void;
  selectedItemId: string | null;
  onSelectItem: (id: string) => void;
  taskCountByItemId: Record<string, number>;
  onSelectPeriod: (period: string) => void;
}) {
  const isOrg = row.scopeKind === 'org';
  const hasPlan = Boolean(row.latestPlan);
  const items = row.latestPlan?.items || [];
  return (
    <div className="border-t border-gray-100 last:border-b">
      <button
        type="button"
        onClick={onToggle}
        className={`w-full flex items-center gap-3 px-3 py-3.5 text-left transition-colors ${expanded ? 'bg-gray-50/60' : 'hover:bg-gray-50/70'}`}
      >
        <ChevronDown
          size={14}
          className={`shrink-0 text-gray-400 transition-transform ${expanded ? '' : '-rotate-90'}`}
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <p className="text-[13px] font-medium text-gray-900 truncate">{row.scopeName}</p>
            {isOrg && (
              <span className="rounded-full bg-[#5B7BFE]/10 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.1em] text-[#5B7BFE]">组织</span>
            )}
          </div>
          <p className="mt-0.5 text-[10.5px] text-gray-400 truncate">
            {row.leaderName}
            {hasPlan ? ` · ${formatPlanningPeriodLabel(row.latestPlan?.weekLabel)} · ${items.length} 项` : ' · 尚未制定'}
          </p>
        </div>
        <div className="shrink-0 flex items-center gap-2.5">
          {hasPlan ? (
            <CompletenessBar pct={row.completeness} />
          ) : (
            <span className="text-[10px] font-bold uppercase tracking-[0.12em] text-amber-600">待制定</span>
          )}
        </div>
      </button>
      {expanded && hasPlan && (
        <div className="pl-7 pr-3 pb-4 pt-1 space-y-2.5">
          <div className="flex items-center gap-2 pb-1">
            <span className="shrink-0 text-[10px] font-bold uppercase tracking-[0.12em] text-gray-400">计划周期</span>
            <select
              value={row.latestPlan?.weekLabel || ''}
              onChange={(event) => onSelectPeriod(event.target.value)}
              className="min-w-0 flex-1 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-[11px] font-medium text-gray-700 outline-none focus:border-[#5B7BFE]"
            >
              {row.plans.map((plan) => (
                <option key={plan.id} value={plan.weekLabel}>{formatPlanningPeriodLabel(plan.weekLabel)}</option>
              ))}
            </select>
          </div>
          {items.length === 0 ? (
            <p className="text-[11px] text-gray-400 py-3">当前周期尚未填写计划项</p>
          ) : (
            items.map((item) => {
              const isSelected = selectedItemId === item.id;
              const statusAccentCls =
                item.status === 'done' ? 'before:bg-emerald-500'
                : item.status === 'paused' ? 'before:bg-gray-300'
                : item.status === 'dropped' ? 'before:bg-rose-400'
                : 'before:bg-[#5B7BFE]';
              const assignedCount = taskCountByItemId[item.id] || 0;
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => onSelectItem(item.id)}
                  className={`relative w-full flex flex-col items-start border rounded-xl pl-6 pr-4 py-3.5 text-left transition-all duration-150
                    before:absolute before:left-3 before:top-3.5 before:bottom-3.5 before:w-[2.5px] before:rounded-full ${statusAccentCls}
                    ${isSelected
                      ? 'border-[#9FB2FF] bg-[#5B7BFE]/[0.04] shadow-[0_1px_2px_rgba(91,123,254,0.05)]'
                      : 'border-gray-100 bg-white hover:border-gray-200 hover:bg-gray-50/40 hover:shadow-[0_1px_3px_rgba(15,23,42,0.04)]'
                    }`}
                >
                  <div className="flex w-full items-start gap-2">
                    <p className={`text-[13.5px] font-medium leading-snug flex-1 min-w-0 ${isSelected ? 'text-[#3D5CD9]' : 'text-gray-900'}`}>
                      {item.title || '未命名计划项'}
                    </p>
                    {assignedCount > 0 && (
                      <span
                        className="shrink-0 rounded-full bg-[#5B7BFE]/10 px-2 py-0.5 text-[10px] font-bold text-[#5B7BFE]"
                        title={`已分配 ${assignedCount} 条任务`}
                      >
                        已分配 · {assignedCount}
                      </span>
                    )}
                  </div>
                  {item.statement && (
                    <p className="mt-1.5 text-[11.5px] leading-[1.65] text-gray-500 line-clamp-2 w-full">
                      {item.statement}
                    </p>
                  )}
                </button>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

function PlanItemTaskCard({ task, onOpen }: { task: Task; onOpen?: () => void }) {
  const statusDotMap: Record<string, string> = {
    doing: 'bg-[#5B7BFE]',
    done: 'bg-emerald-500',
    paused: 'bg-gray-300',
    cancelled: 'bg-rose-300',
  };
  const statusLabelMap: Record<string, string> = {
    doing: '进行中',
    done: '已完成',
    paused: '暂停',
    cancelled: '已取消',
  };
  const dotCls = statusDotMap[task.status] || 'bg-gray-300';
  const statusLabel = statusLabelMap[task.status] || task.status;
  const clickable = Boolean(onOpen);
  return (
    <div
      role={clickable ? 'button' : undefined}
      tabIndex={clickable ? 0 : undefined}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (!clickable) return;
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onOpen?.();
        }
      }}
      className={`group flex items-start gap-3 -mx-2 px-3 py-4 rounded-md transition-colors even:bg-[#FAFAFA] ${
        clickable ? 'cursor-pointer hover:bg-gray-100/70' : ''
      }`}
      title={clickable ? '打开任务详情' : undefined}
    >
      <span className={`mt-[7px] inline-block h-[6px] w-[6px] rounded-full shrink-0 ${dotCls}`} />
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-medium text-gray-900 truncate">{task.title}</p>
        <div className="mt-1 flex items-center gap-2 text-[10.5px] text-gray-400">
          <span>{statusLabel}</span>
          {task.ownerName && <><span>·</span><span>{task.ownerName}</span></>}
          {task.dueDate && <><span>·</span><span>{task.dueDate.slice(0, 10)}</span></>}
        </div>
      </div>
      {clickable && (
        <ArrowRight size={14} className="shrink-0 mt-1 text-gray-300 group-hover:text-[#5B7BFE] transition-colors" />
      )}
    </div>
  );
}

function PlanItemStatusBadge({ status, compact = false }: { status: string; compact?: boolean }) {
  const toneMap: Record<string, string> = {
    active: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    paused: 'border-gray-200 bg-gray-50 text-gray-500',
    done: 'border-blue-200 bg-blue-50 text-blue-700',
    dropped: 'border-rose-200 bg-rose-50 text-rose-500',
  };
  const labelMap: Record<string, string> = {
    active: '进行中',
    paused: '暂停',
    done: '已完成',
    dropped: '已废弃',
  };
  const cls = toneMap[status] || toneMap.active;
  return (
    <span className={`inline-flex items-center rounded-full border font-bold uppercase tracking-[0.12em] ${cls} ${compact ? 'px-1.5 py-0.5 text-[9px]' : 'px-2.5 py-1 text-[10px]'}`}>
      {labelMap[status] || status}
    </span>
  );
}

function CompletenessBar({ pct }: { pct: number }) {
  const colorCls = pct >= 80 ? 'bg-emerald-500' : pct >= 40 ? 'bg-amber-500' : 'bg-gray-300';
  return (
    <div className="flex items-center gap-2 shrink-0">
      <div className="w-16 h-[3px] rounded-full bg-gray-100 overflow-hidden">
        <div
          className={`h-full ${colorCls}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-[10.5px] font-medium text-gray-600 w-8 text-right tabular-nums">{pct}%</span>
    </div>
  );
}
