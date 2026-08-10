import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Building2, Check, ChevronDown, ChevronLeft, Copy, FileText, Pencil, Plus, Save, Trash2, UploadCloud, Users, X } from 'lucide-react';

import type {
  DepartmentOption,
  EmployeeRecord,
  OrgDepartmentSettings,
  OrgEmployeeBindingSettings,
  OrgIntroDocumentSettings,
  OrgModelSettings,
  OrgQuarterKey,
  OrgRoleTemplateSettings,
  OrgTaskEditScope,
  OrgVisibilityScope,
} from '../../../shared/types';
import { buildDepartmentInviteCode, buildManagementTitleInviteCode } from '../../../shared/departmentInvite';
// 顾源源 5/24: 机器人同事 — 直接复用弹窗组件, 不挂底部抽屉
import { BotMemberFormDialog, BotRotateTokenDialog } from './BotMembersPanel';
import { listBotMembers, type BotMemberRecord } from '../../lib/api';
import {
  isActiveOrganizationEmployee,
  isAssignableOrganizationEmployee,
  isLegacyOrganizationPersonName,
} from '../../lib/organizationEmployeeFilters';

type LinkedSection = 'tasks' | 'handbook';

type Props = {
  value: OrgModelSettings;
  departmentCatalog: DepartmentOption[];
  employees: EmployeeRecord[];
  canEdit: boolean;
  isSaving?: boolean;
  activeWeekLabel: string;
  initialAdvancedTab?: string | null;
  /** 顾源源 5/24 P1: 当前登录用户 — 创建机器人时作为 created_by + creator 审批人 */
  currentUserId?: string;
  currentUserName?: string;
  onChange: (next: OrgModelSettings) => void;
  onSave: (next?: OrgModelSettings) => Promise<boolean | void> | boolean | void;
  getInputDrafts?: () => OrganizationSetupInputDraftState;
  setInputDrafts?: (next: OrganizationSetupInputDraftState) => void;
  onUploadIntroDocument?: (title: string) => Promise<OrgIntroDocumentSettings | null>;
  onOpenSection: (section: LinkedSection) => void;
};

export type OrganizationSetupInputDraftState = {
  organizationName?: string;
  organizationLeaderName?: string;
  departmentLeaderNames?: Record<string, string>;
  editingNodeId?: string | null;
  editingField?: 'name' | 'leadName' | null;
  editingText?: string;
};

type ActiveView = 'tree' | 'codes';

type EditableField = 'name' | 'leadName';

type TreeDepartmentNode = {
  id: string;
  name: string;
  type: 'department';
  leadName: string;
  leaderUserId?: string | null;
  leaderName?: string | null;
  parentDepartmentId?: string | null;
  color: string;
  children: TreePositionNode[];
};

type TreeOrgNode = {
  id: string;
  name: string;
  type: 'org';
  children: TreeDepartmentNode[];
};

type TreePositionNode = {
  id: string;
  name: string;
  type: 'position';
  departmentId: string | null;
};

type ManagementDisplayPerson = {
  id: string;
  name: string;
  detail: string;
};

type ManagementDisplayGroup = {
  key: string;
  label: string;
  color: string;
  people: ManagementDisplayPerson[];
  emptyText: string;
};

const DEPARTMENT_COLORS = ['#5B7BFE', '#0EA5E9', '#14B8A6', '#F59E0B', '#EF4444', '#8B5CF6'];

function nextUiId(prefix: string) {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

function clampPercent(value: number) {
  return Math.max(0, Math.min(100, Math.round(value)));
}

function buildEmptyQuarterPlan(): OrgDepartmentSettings['quarterPlan'] {
  return {
    year: '',
    quarter: 'Q1' satisfies OrgQuarterKey,
    objective: '',
    deliverables: [],
    successMetrics: [],
    majorRisks: [],
    updatedAt: '',
  };
}

function isManagementTitleRole(role: OrgRoleTemplateSettings) {
  if (role.active === false) return false;
  if (role.departmentId) return false;
  return role.visibilityScope === 'organization'
    || role.taskEditScope === 'organization'
    || role.level === 'organization_lead'
    || role.isManager;
}

function visibilityScopeLabel(scope?: OrgVisibilityScope | null) {
  if (scope === 'organization') return '组织级可见';
  if (scope === 'department') return '部门级可见';
  return '个人可见';
}

function systemRoleLabel(employee: Pick<EmployeeRecord, 'primaryRole'>) {
  if (employee.primaryRole === 'admin') return '管理员';
  if (employee.primaryRole === 'ai_agent') return '机器人同事';
  return '成员';
}

function memberIdentityLabel(employee: EmployeeRecord, departmentName?: string | null, isLead = false) {
  const name = (departmentName || employee.departmentName || '').trim();
  const managementTitle = (employee.managementTitleName || '').trim();
  const legacyManagementTitle = !name && employee.visibilityScope === 'organization' ? (employee.jobTitle || '').trim() : '';
  if (employee.primaryRole === 'admin') {
    return managementTitle || legacyManagementTitle ? `管理员 · ${managementTitle || legacyManagementTitle}` : '管理员';
  }
  if (!departmentName && (managementTitle || legacyManagementTitle)) {
    return managementTitle || legacyManagementTitle;
  }
  if (isLead) return name ? `${name}负责人` : '部门负责人';
  if (name) return `${name}成员`;
  return '身份待同步';
}

function tint(hexColor: string, suffix = '12') {
  return `${hexColor}${suffix}`;
}

function visibleLeaderNameInput(value: string | null | undefined) {
  const name = (value || '').trim();
  if (!name || isLegacyOrganizationPersonName(name)) return '';
  return name;
}

function isComposingKeyEvent(
  event: React.KeyboardEvent<HTMLInputElement>,
  composingRef: React.MutableRefObject<boolean>,
) {
  const nativeEvent = event.nativeEvent as KeyboardEvent & { isComposing?: boolean; keyCode?: number };
  return composingRef.current || nativeEvent.isComposing || nativeEvent.keyCode === 229;
}

function resolveInputDraftText(draftValue: string | undefined, fallbackValue: string) {
  return typeof draftValue === 'string' ? draftValue : fallbackValue;
}

function employeeDisplayDetail(employee: EmployeeRecord) {
  return memberIdentityLabel(employee)
    || employee.departmentName?.trim()
    || employee.jobTitle?.trim()
    || employee.email?.trim()
    || employee.phone?.trim()
    || '组织成员';
}

function normalizeOrgLabel(value: string | null | undefined) {
  return (value || '').trim().replace(/\s+/g, '').toLowerCase();
}

function employeeBelongsToDepartment(
  employee: EmployeeRecord,
  binding: OrgEmployeeBindingSettings | undefined,
  department: Pick<OrgDepartmentSettings, 'id' | 'name'>,
) {
  if (employee.departmentId && employee.departmentId === department.id) return true;
  if (binding?.departmentId && binding.departmentId === department.id) return true;
  const departmentName = normalizeOrgLabel(department.name);
  if (!departmentName) return false;
  return normalizeOrgLabel(employee.departmentName) === departmentName;
}

function deriveTree(
  value: OrgModelSettings,
  fallbackOrganizationName: string,
): TreeOrgNode {
  const activeDepartments = value.departments.filter((item) => item.active !== false);
  const activeRoles = value.roles
    .filter((item) => item.active !== false)
    .sort((left, right) => left.sortOrder - right.sortOrder);

  return {
    id: value.organization.organizationId || 'organization-root',
    name: value.organization.name.trim() || fallbackOrganizationName,
    type: 'org',
    children: activeDepartments.map((department) => {
      const rawLeaderName = department.leaderName?.trim() || '';
      const visibleLeaderName = rawLeaderName && !isLegacyOrganizationPersonName(rawLeaderName) ? rawLeaderName : '';
      return {
        id: department.id,
        name: department.name || '未命名部门',
        type: 'department',
        leadName: visibleLeaderName || '待设置',
        leaderUserId: department.leaderUserId || null,
        leaderName: visibleLeaderName || null,
        parentDepartmentId: department.parentDepartmentId || null,
        color: department.color || DEPARTMENT_COLORS[0],
        children: activeRoles
          .filter((role) => role.departmentId === department.id)
          .map((role) => ({
            id: role.id,
            name: role.name || '未命名岗位',
            type: 'position',
            departmentId: department.id,
          })),
      };
    }),
  };
}

function computeStats(
  value: OrgModelSettings,
  employees: EmployeeRecord[],
) {
  const activeDepartments = value.departments.filter((item) => item.active !== false);
  const activeRoles = value.roles.filter((item) => item.active !== false);
  const managementTitleCount = activeRoles.filter(isManagementTitleRole).length;
  const activePlans = value.departmentPlans.filter((item) => item.status !== 'closed');
  const activeEmployees = employees.filter(isAssignableOrganizationEmployee);
  const activeEmployeeIds = new Set(activeEmployees.map((item) => item.id));
  const boundMemberIds = new Set(value.bindings.filter((item) => item.departmentId && activeEmployeeIds.has(item.userId)).map((item) => item.userId));
  const manualDepartmentLeaderCount = activeDepartments.filter((department) => {
    const visibleLeaderName = visibleLeaderNameInput(department.leaderName);
    return Boolean(visibleLeaderName && !department.leaderUserId);
  }).length;
  const memberCount = Math.max(boundMemberIds.size + manualDepartmentLeaderCount, activeEmployees.length);

  const completenessByDepartment = activeDepartments.map((department) => {
    const planCount = activePlans.filter((plan) => plan.departmentId === department.id).length;
    const memberBindings = value.bindings.filter((binding) => binding.departmentId === department.id && activeEmployeeIds.has(binding.userId));
    const visibleLeaderName = department.leaderName?.trim() && !isLegacyOrganizationPersonName(department.leaderName)
      ? department.leaderName.trim()
      : '';
    const hasVisibleLeader = Boolean(visibleLeaderName) || Boolean(department.leaderUserId && activeEmployeeIds.has(department.leaderUserId));
    const memberCount = countDepartmentMembers(department, memberBindings);
    const missing = [
      !hasVisibleLeader,
      !department.mission.trim(),
      memberCount === 0,
      planCount === 0,
    ].filter(Boolean).length;
    return clampPercent(((4 - missing) / 4) * 100);
  });

  const completeness = activeDepartments.length > 0
    ? clampPercent(completenessByDepartment.reduce((sum, item) => sum + item, 0) / activeDepartments.length)
    : 0;

  return [
    { label: '部门', value: `${activeDepartments.length}` },
    { label: '管理层头衔', value: `${managementTitleCount}` },
    { label: '成员', value: `${memberCount}` },
    { label: '计划数', value: `${activePlans.length}` },
    { label: '完整度', value: `${completeness}%` },
  ];
}

function departmentColor(index: number, existing?: string | null) {
  if (existing && existing.trim()) return existing;
  return DEPARTMENT_COLORS[index % DEPARTMENT_COLORS.length];
}

function buildEmptyOrgModel(value: OrgModelSettings): OrgModelSettings {
  const timestamp = new Date().toISOString();
  return {
    ...value,
    organization: {
      ...value.organization,
      name: '',
      annualGoal: '',
      annualStrategyYear: String(new Date().getFullYear()),
      annualStrategy: '',
      quarterPlans: [],
      quarterlyFocus: [],
      leaderUserId: null,
      leaderName: '',
      introDocument: null,
      managementUserIds: [],
      updatedAt: timestamp,
    },
    departments: [],
    roles: [],
    bindings: [],
    reportingLines: [],
    taskControlRules: [],
    roleProcessTemplates: [],
    focusItems: [],
    departmentPlans: [],
    updatedAt: timestamp,
  };
}

function applyOrganizationNameDraft(value: OrgModelSettings, rawName: string): OrgModelSettings {
  const nextName = rawName.trim();
  if (nextName === value.organization.name) {
    return value;
  }
  const timestamp = new Date().toISOString();
  return {
    ...value,
    organization: {
      ...value.organization,
      name: nextName,
      updatedAt: timestamp,
    },
    updatedAt: timestamp,
  };
}

function applyOrganizationLeaderNameDraft(value: OrgModelSettings, rawName: string): OrgModelSettings {
  const nextName = visibleLeaderNameInput(rawName);
  const currentName = visibleLeaderNameInput(value.organization.leaderName);
  if (
    nextName === currentName
    && !value.organization.leaderUserId
    && currentName === (value.organization.leaderName || '').trim()
  ) {
    return value;
  }
  const timestamp = new Date().toISOString();
  return {
    ...value,
    organization: {
      ...value.organization,
      leaderUserId: null,
      leaderName: nextName,
      managementUserIds: [],
      updatedAt: timestamp,
    },
    updatedAt: timestamp,
  };
}

function applyOrganizationLeaderUserIdDraft(
  value: OrgModelSettings,
  userId: string | null,
  displayName: string,
): OrgModelSettings {
  const nextName = displayName.trim();
  if (
    (value.organization.leaderUserId ?? null) === userId
    && (value.organization.leaderName ?? '') === nextName
  ) {
    return value;
  }
  const timestamp = new Date().toISOString();
  return {
    ...value,
    organization: {
      ...value.organization,
      leaderUserId: userId,
      leaderName: nextName,
      managementUserIds: userId ? [userId] : [],
      updatedAt: timestamp,
    },
    updatedAt: timestamp,
  };
}

function applyDepartmentLeaderUserIdDraft(
  value: OrgModelSettings,
  departmentId: string,
  userId: string | null,
  displayName: string,
): OrgModelSettings {
  const dept = value.departments.find((d) => d.id === departmentId);
  if (!dept) return value;
  const nextName = displayName.trim();
  if (
    (dept.leaderUserId ?? null) === userId
    && (dept.leaderName ?? '') === nextName
  ) {
    return value;
  }
  const timestamp = new Date().toISOString();
  return {
    ...value,
    departments: value.departments.map((d) =>
      d.id === departmentId
        ? { ...d, leaderUserId: userId, leaderName: nextName, updatedAt: timestamp }
        : d,
    ),
    updatedAt: timestamp,
  };
}

function applyDepartmentLeaderNameDrafts(
  value: OrgModelSettings,
  drafts: Record<string, string>,
): OrgModelSettings {
  const draftEntries = Object.entries(drafts);
  const timestamp = new Date().toISOString();
  let changed = false;
  const draftByDepartmentId = new Map(draftEntries.map(([departmentId, rawName]) => [departmentId, rawName.trim()]));
  const departments = value.departments.map((department) => {
    const hasDraft = draftByDepartmentId.has(department.id);
    const nextName = visibleLeaderNameInput(hasDraft ? draftByDepartmentId.get(department.id) : department.leaderName);
    const currentName = visibleLeaderNameInput(department.leaderName);
    if (
      nextName === currentName
      && !department.leaderUserId
      && currentName === (department.leaderName || '').trim()
    ) {
      return department;
    }
    changed = true;
    return {
      ...department,
      leaderUserId: null,
      leaderName: nextName,
      updatedAt: timestamp,
    };
  });
  if (!changed) {
    return value;
  }
  return {
    ...value,
    departments,
    updatedAt: timestamp,
  };
}

function applyDepartmentNameDraft(value: OrgModelSettings, departmentId: string, rawName: string): OrgModelSettings {
  const nextName = rawName.trim();
  if (!nextName) return value;
  const department = value.departments.find((item) => item.id === departmentId);
  if (!department || department.name === nextName) return value;
  const timestamp = new Date().toISOString();
  return {
    ...value,
    departments: value.departments.map((item) => (
      item.id === departmentId ? { ...item, name: nextName, updatedAt: timestamp } : item
    )),
    updatedAt: timestamp,
  };
}

function applyRoleNameDraft(value: OrgModelSettings, roleId: string, rawName: string): OrgModelSettings {
  const nextName = rawName.trim();
  if (!nextName) return value;
  const role = value.roles.find((item) => item.id === roleId);
  if (!role || role.name === nextName) return value;
  const timestamp = new Date().toISOString();
  return {
    ...value,
    roles: value.roles.map((item) => (
      item.id === roleId ? { ...item, name: nextName, updatedAt: timestamp } : item
    )),
    updatedAt: timestamp,
  };
}

function introDocumentChanged(nextDocument: OrgIntroDocumentSettings | null | undefined, currentDocument: OrgIntroDocumentSettings | null | undefined) {
  if (!nextDocument) return false;
  return nextDocument.contentHash !== currentDocument?.contentHash
    || nextDocument.fileName !== currentDocument?.fileName
    || nextDocument.uploadedAt !== currentDocument?.uploadedAt;
}

function applyOrganizationIntroDocumentDraft(value: OrgModelSettings, document: OrgIntroDocumentSettings): OrgModelSettings {
  if (!introDocumentChanged(document, value.organization.introDocument)) {
    return value;
  }
  const timestamp = new Date().toISOString();
  return {
    ...value,
    organization: {
      ...value.organization,
      introDocument: document,
      updatedAt: timestamp,
    },
    updatedAt: timestamp,
  };
}

function applyDepartmentIntroDocumentDraft(
  value: OrgModelSettings,
  departmentId: string,
  document: OrgIntroDocumentSettings,
): OrgModelSettings {
  const department = value.departments.find((item) => item.id === departmentId);
  if (!department || !introDocumentChanged(document, department.introDocument)) {
    return value;
  }
  const timestamp = new Date().toISOString();
  return {
    ...value,
    departments: value.departments.map((item) => (
      item.id === departmentId
        ? { ...item, introDocument: document, updatedAt: timestamp }
        : item
    )),
    updatedAt: timestamp,
  };
}

function countDepartmentMembers(
  department: OrgDepartmentSettings,
  bindings: OrgEmployeeBindingSettings[],
) {
  const userIds = new Set(bindings.map((binding) => binding.userId).filter(Boolean));
  if (department.leaderUserId) {
    userIds.add(department.leaderUserId);
  }
  const visibleLeaderName = visibleLeaderNameInput(department.leaderName);
  const manualLeaderCount = !department.leaderUserId && visibleLeaderName ? 1 : 0;
  return userIds.size + manualLeaderCount;
}

export function OrganizationSetupCenter({
  value,
  departmentCatalog,
  employees,
  canEdit,
  isSaving = false,
  currentUserId,
  currentUserName,
  onChange,
  onSave,
  getInputDrafts,
  setInputDrafts,
  onUploadIntroDocument,
}: Props) {
  void departmentCatalog;

  const initialInputDrafts = getInputDrafts?.() || {};
  const [activeView, setActiveView] = useState<ActiveView>('tree');
  // 顾源源 5/24: 添加机器人同事弹窗 (按部门触发, 记录该按钮属于哪个部门)
  const [botDialogDept, setBotDialogDept] = useState<{ id: string; name: string } | null>(null);
  // 顾源源 5/24 M1: 机器人同事数据源 — 全组织 active bot 一次拉, LeaderPicker 按部门过滤展示
  const [botMembers, setBotMembers] = useState<BotMemberRecord[]>([]);
  // M5: 编辑模式弹窗 (mode=edit) — 跟创建模式复用 BotMemberFormDialog
  const [botEditDialog, setBotEditDialog] = useState<BotMemberRecord | null>(null);
  // M5/M6.1: 重置密钥弹窗 — 独立组件 BotRotateTokenDialog.
  //   两个入口:
  //     (a) 编辑弹窗内"密钥管理"区点"重置密钥"     → { bot, autoCopy: false }
  //     (b) 岗位卡 hover "复制密钥" confirm 后    → { bot, autoCopy: true }
  //   autoStart 在 dialog 侧永远 true (用户已经在外面确认过, 不再二次确认).
  const [botRotateDialog, setBotRotateDialog] = useState<{ bot: BotMemberRecord; autoCopy: boolean } | null>(null);
  // M6.1: 岗位卡 hover "复制密钥" 按钮的轻确认 modal —
  //   db 只存 token hash, 物理上没法读旧 plain, 所以必须明确告知用户:
  //   "复制" 实际是 "重置 + 显示新的 + 自动复制", 旧 token 立刻作废.
  //   inline 在本文件里 (不另开 Modal 文件), 用 useState 控制显隐.
  const [botCopyConfirm, setBotCopyConfirm] = useState<BotMemberRecord | null>(null);

  const reloadBotMembers = useCallback(async () => {
    try {
      const resp = await listBotMembers({ status: 'active' });
      setBotMembers(resp.items);
    } catch (err) {
      // 拉失败不阻塞页面; 控制台留痕便于排查
      // eslint-disable-next-line no-console
      console.error('[OrganizationSetupCenter] listBotMembers failed:', err);
    }
  }, []);

  useEffect(() => {
    void reloadBotMembers();
  }, [reloadBotMembers]);

  // 按 department_id 索引, LeaderPicker 按部门过滤
  const botMembersByDepartmentId = useMemo(() => {
    const mapping = new Map<string, BotMemberRecord[]>();
    botMembers.forEach((bot) => {
      if (!bot.department_id) return;
      const list = mapping.get(bot.department_id) || [];
      list.push(bot);
      mapping.set(bot.department_id, list);
    });
    return mapping;
  }, [botMembers]);
  // 全表反查 (持岗人按 holderBotId 拿 bot record)
  const botMemberById = useMemo(() => {
    const mapping = new Map<string, BotMemberRecord>();
    botMembers.forEach((bot) => mapping.set(bot.id, bot));
    return mapping;
  }, [botMembers]);
  const [editingNodeId, setEditingNodeId] = useState<string | null>(initialInputDrafts.editingNodeId || null);
  const [editingField, setEditingField] = useState<EditableField | null>((initialInputDrafts.editingField as EditableField | null) || null);
  const [editingText, setEditingText] = useState(initialInputDrafts.editingText || '');
  const [toast, setToast] = useState<string | null>(null);
  const [organizationNameInput, setOrganizationNameInput] = useState(
    resolveInputDraftText(initialInputDrafts.organizationName, value.organization.name),
  );
  const [organizationLeaderNameInput, setOrganizationLeaderNameInput] = useState(
    visibleLeaderNameInput(resolveInputDraftText(initialInputDrafts.organizationLeaderName, value.organization.leaderName || '')),
  );
  const [departmentLeaderNameInputs, setDepartmentLeaderNameInputs] = useState<Record<string, string>>(
    initialInputDrafts.departmentLeaderNames || {},
  );
  const [pendingOrganizationIntroDocument, setPendingOrganizationIntroDocument] = useState<OrgIntroDocumentSettings | null>(null);
  const [pendingDepartmentIntroDocuments, setPendingDepartmentIntroDocuments] = useState<Record<string, OrgIntroDocumentSettings>>({});
  const [bulkInviteCopied, setBulkInviteCopied] = useState(false);
  const [resetConfirmOpen, setResetConfirmOpen] = useState(false);
  const [resetConfirmText, setResetConfirmText] = useState('');
  // 编辑模式开关：默认关闭，组织搭建视图为只读；点"编辑组织"开启后才显示
  // 各种局部编辑控件（添加部门/岗位、删除 X、保存对勾、文本输入框）。
  const [isEditingMode, setIsEditingMode] = useState(false);
  const canModify = canEdit && isEditingMode;
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const bulkInviteTimerRef = useRef<number | null>(null);
  const textCompositionRef = useRef(false);

  const tree = useMemo(
    () => deriveTree(value, '当前组织'),
    [value],
  );
  const stats = useMemo(() => computeStats(value, employees), [employees, value]);
  const activeDepartments = useMemo(() => value.departments.filter((item) => item.active !== false), [value.departments]);
  const activeRoles = useMemo(() => value.roles.filter((item) => item.active !== false), [value.roles]);
  const departmentNameById = useMemo(() => {
    const mapping = new Map<string, string>();
    activeDepartments.forEach((department) => mapping.set(department.id, department.name || '未命名部门'));
    return mapping;
  }, [activeDepartments]);
  const approvedEmployees = useMemo(
    () => employees.filter(isAssignableOrganizationEmployee),
    [employees],
  );
  const approvedEmployeeIds = useMemo(() => new Set(approvedEmployees.map((item) => item.id)), [approvedEmployees]);
  const bindingsByDepartmentId = useMemo(() => {
    const mapping = new Map<string, OrgEmployeeBindingSettings[]>();
    value.bindings.forEach((binding) => {
      if (!binding.departmentId) return;
      if (!approvedEmployeeIds.has(binding.userId)) return;
      const list = mapping.get(binding.departmentId) || [];
      list.push(binding);
      mapping.set(binding.departmentId, list);
    });
    return mapping;
  }, [approvedEmployeeIds, value.bindings]);

  const organizationName = organizationNameInput.trim() || value.organization.name.trim() || tree.name || '当前组织';
  const managementTitleRoles = useMemo(
    () => activeRoles.filter(isManagementTitleRole).sort((left, right) => left.sortOrder - right.sortOrder),
    [activeRoles],
  );
  const bindingByUserId = useMemo(
    () => new Map(value.bindings.map((binding) => [binding.userId, binding])),
    [value.bindings],
  );
  const managementInviteCards = useMemo(() => {
    return managementTitleRoles.map((role, index) => {
      const label = role.name.trim() || '未命名管理层头衔';
      const inviteCode = buildManagementTitleInviteCode(value.organization.organizationId, role.id, {
        organizationName,
        titleName: label,
        order: index,
      });
      const roleIds = new Set([role.id]);
      const bindingUserIds = new Set(
        value.bindings
          .filter((binding) => binding.primaryRoleId && roleIds.has(binding.primaryRoleId))
          .map((binding) => binding.userId),
      );
      const joinedCount = approvedEmployees.filter((employee) => {
        if (bindingUserIds.has(employee.id)) return true;
        if (bindingByUserId.has(employee.id)) return false;
        return employee.managementTitleId === role.id
          || (employee.managementTitleName || '').trim() === label;
      }).length;
      return {
        key: role.id,
        label,
        inviteCode,
        joinedCount,
        color: DEPARTMENT_COLORS[index % DEPARTMENT_COLORS.length],
        helper: '组织管理层头衔，可查看组织级汇总信息并维护组织搭建',
      };
    });
  }, [approvedEmployees, bindingByUserId, managementTitleRoles, organizationName, value.bindings, value.organization.organizationId]);
  const managementGroups = useMemo<ManagementDisplayGroup[]>(() => {
    const employeeById = new Map(approvedEmployees.map((employee) => [employee.id, employee]));
    const peopleFromIds = (ids: Iterable<string>) => {
      const seen = new Set<string>();
      const people: ManagementDisplayPerson[] = [];
      Array.from(ids).forEach((id) => {
        if (seen.has(id)) return;
        const employee = employeeById.get(id);
        if (!employee) return;
        seen.add(id);
        people.push({
          id: employee.id,
          name: employee.fullName || employee.email || '未命名成员',
          detail: employeeDisplayDetail(employee),
        });
      });
      return people;
    };
    const rolePeople = (role: OrgRoleTemplateSettings) => {
      const roleIds = new Set([role.id]);
      const ids = new Set<string>();
      value.bindings.forEach((binding) => {
        if (binding.primaryRoleId && roleIds.has(binding.primaryRoleId)) {
          ids.add(binding.userId);
        }
      });
      approvedEmployees.forEach((employee) => {
        const localBinding = bindingByUserId.get(employee.id);
        if (localBinding && localBinding.primaryRoleId !== role.id) {
          return;
        }
        if (
          employee.managementTitleId === role.id
          || (employee.managementTitleName || '').trim() === role.name.trim()
        ) {
          ids.add(employee.id);
        }
      });
      return peopleFromIds(ids);
    };
    const adminGroup: ManagementDisplayGroup = {
      key: 'admin',
      label: '管理员',
      color: '#6366F1',
      people: approvedEmployees
        .filter((employee) => employee.primaryRole === 'admin')
        .map((employee) => ({
          id: employee.id,
          name: employee.fullName || employee.email || '未命名管理员',
          detail: employeeDisplayDetail(employee),
        })),
      emptyText: '暂无管理员',
    };
    return [
      adminGroup,
      ...managementTitleRoles.map((role, index) => ({
        key: role.id,
        label: role.name.trim() || '未命名管理层头衔',
        color: DEPARTMENT_COLORS[index % DEPARTMENT_COLORS.length],
        people: rolePeople(role),
        emptyText: '待填写或待加入',
      })),
    ];
  }, [approvedEmployees, bindingByUserId, managementTitleRoles, value.bindings]);
  const managementGroupByKey = useMemo(() => {
    return new Map(managementGroups.map((group) => [group.key, group]));
  }, [managementGroups]);
  const approvedEmployeeById = useMemo(
    () => new Map(approvedEmployees.map((employee) => [employee.id, employee])),
    [approvedEmployees],
  );
  const bulkInviteText = useMemo(() => {
    const managementLines = managementInviteCards.map((item) => `${item.label}：${item.inviteCode}`);
    const linesOfText = tree.children.map((department, index) => {
      const inviteCode = buildDepartmentInviteCode(department.id, {
        organizationId: value.organization.organizationId,
        organizationName,
        departmentName: department.name,
        order: index,
      });
      return `${department.name}：${inviteCode}`;
    });
    return [
      `${organizationName} 邀请码`,
      '管理层成员填写对应头衔邀请码；部门成员填写部门邀请码后进入该部门成员池。',
      ...managementLines,
      ...linesOfText,
    ].join('\n');
  }, [managementInviteCards, organizationName, tree.children, value.organization.organizationId]);

  const showToast = useCallback((message: string) => {
    setToast(message);
    if (toastTimerRef.current) {
      clearTimeout(toastTimerRef.current);
    }
    toastTimerRef.current = setTimeout(() => setToast(null), 2400);
  }, []);

  const updateInputDrafts = useCallback((updater: (current: OrganizationSetupInputDraftState) => OrganizationSetupInputDraftState) => {
    const current = getInputDrafts?.() || {};
    setInputDrafts?.(updater(current));
  }, [getInputDrafts, setInputDrafts]);

  const clearInputDrafts = useCallback(() => {
    setInputDrafts?.({});
  }, [setInputDrafts]);

  const setOrganizationNameDraft = useCallback((nextName: string) => {
    setOrganizationNameInput(nextName);
    updateInputDrafts((current) => ({ ...current, organizationName: nextName }));
  }, [updateInputDrafts]);

  const setOrganizationLeaderNameDraft = useCallback((nextName: string) => {
    setOrganizationLeaderNameInput(nextName);
    updateInputDrafts((current) => ({ ...current, organizationLeaderName: nextName }));
  }, [updateInputDrafts]);

  const clearOrganizationNameDraft = useCallback(() => {
    updateInputDrafts((current) => {
      const next = { ...current };
      delete next.organizationName;
      return next;
    });
  }, [updateInputDrafts]);

  const clearOrganizationLeaderNameDraft = useCallback(() => {
    updateInputDrafts((current) => {
      const next = { ...current };
      delete next.organizationLeaderName;
      return next;
    });
  }, [updateInputDrafts]);

  const setDepartmentLeaderNameDraft = useCallback((departmentId: string, nextName: string) => {
    setDepartmentLeaderNameInputs((previous) => ({
      ...previous,
      [departmentId]: nextName,
    }));
    updateInputDrafts((current) => ({
      ...current,
      departmentLeaderNames: {
        ...(current.departmentLeaderNames || {}),
        [departmentId]: nextName,
      },
    }));
  }, [updateInputDrafts]);

  const clearDepartmentLeaderNameDraft = useCallback((departmentId: string) => {
    updateInputDrafts((current) => {
      const departmentLeaderNames = { ...(current.departmentLeaderNames || {}) };
      delete departmentLeaderNames[departmentId];
      const next = { ...current };
      if (Object.keys(departmentLeaderNames).length > 0) {
        next.departmentLeaderNames = departmentLeaderNames;
      } else {
        delete next.departmentLeaderNames;
      }
      return next;
    });
  }, [updateInputDrafts]);

  const setEditingTextDraft = useCallback((nextText: string) => {
    setEditingText(nextText);
    updateInputDrafts((current) => ({ ...current, editingText: nextText }));
  }, [updateInputDrafts]);

  const clearEditingDraft = useCallback(() => {
    updateInputDrafts((current) => {
      const next = { ...current };
      delete next.editingNodeId;
      delete next.editingField;
      delete next.editingText;
      return next;
    });
  }, [updateInputDrafts]);

  const handleTextCompositionStart = useCallback(() => {
    textCompositionRef.current = true;
  }, []);

  const handleTextCompositionEnd = useCallback(() => {
    window.setTimeout(() => {
      textCompositionRef.current = false;
    }, 0);
  }, []);

  useEffect(() => {
    const draftName = getInputDrafts?.().organizationName;
    setOrganizationNameInput(resolveInputDraftText(draftName, value.organization.name));
  }, [getInputDrafts, value.organization.name]);

  useEffect(() => {
    const draftName = getInputDrafts?.().organizationLeaderName;
    setOrganizationLeaderNameInput(
      visibleLeaderNameInput(resolveInputDraftText(draftName, value.organization.leaderName || '')),
    );
  }, [getInputDrafts, value.organization.leaderName]);

  useEffect(() => {
    const activeDepartmentIds = new Set(value.departments.map((department) => department.id));
    setDepartmentLeaderNameInputs((previous) => {
      let changed = false;
      const next: Record<string, string> = {};
      const persistedDrafts = getInputDrafts?.().departmentLeaderNames || {};
      Object.entries({ ...persistedDrafts, ...previous }).forEach(([departmentId, draft]) => {
        if (activeDepartmentIds.has(departmentId)) {
          if (previous[departmentId] !== draft) {
            changed = true;
          }
          next[departmentId] = draft;
        } else {
          changed = true;
        }
      });
      if (Object.keys(previous).length !== Object.keys(next).length) {
        changed = true;
      }
      return changed ? next : previous;
    });
  }, [getInputDrafts, value.departments]);

  useEffect(() => () => {
    if (toastTimerRef.current) {
      clearTimeout(toastTimerRef.current);
    }
    if (bulkInviteTimerRef.current) {
      clearTimeout(bulkInviteTimerRef.current);
    }
  }, []);

  const handleCopyAllInvites = useCallback(async () => {
    if (tree.children.length === 0 && managementInviteCards.length === 0) {
      showToast('还没有邀请码可复制');
      return;
    }
    try {
      await navigator.clipboard.writeText(bulkInviteText);
    } catch {
      const textArea = document.createElement('textarea');
      textArea.value = bulkInviteText;
      textArea.style.position = 'absolute';
      textArea.style.left = '-99999px';
      textArea.style.top = '-99999px';
      document.body.appendChild(textArea);
      textArea.focus();
      textArea.select();
      document.execCommand('copy');
      document.body.removeChild(textArea);
    }
    setBulkInviteCopied(true);
    if (bulkInviteTimerRef.current) {
      clearTimeout(bulkInviteTimerRef.current);
    }
    bulkInviteTimerRef.current = window.setTimeout(() => setBulkInviteCopied(false), 1800);
    showToast('已复制全部邀请码');
  }, [bulkInviteText, managementInviteCards.length, showToast, tree.children.length]);

  const updateDepartment = useCallback((departmentId: string, patch: Partial<OrgDepartmentSettings>) => {
    onChange({
      ...value,
      departments: value.departments.map((item) => (item.id === departmentId ? { ...item, ...patch } : item)),
    });
  }, [onChange, value]);

  const commitOrganizationNameInput = useCallback(() => {
    const nextValue = applyOrganizationNameDraft(value, organizationNameInput);
    if (nextValue !== value) {
      onChange(nextValue);
    }
    setOrganizationNameInput(nextValue.organization.name);
    clearOrganizationNameDraft();
    return nextValue;
  }, [clearOrganizationNameDraft, onChange, organizationNameInput, value]);

  const commitOrganizationLeaderNameInput = useCallback(() => {
    const nextValue = applyOrganizationLeaderNameDraft(value, organizationLeaderNameInput);
    if (nextValue !== value) {
      onChange(nextValue);
    }
    setOrganizationLeaderNameInput(visibleLeaderNameInput(nextValue.organization.leaderName));
    clearOrganizationLeaderNameDraft();
    return nextValue;
  }, [clearOrganizationLeaderNameDraft, onChange, organizationLeaderNameInput, value]);

  // 通过下拉选择组织负责人：同时绑定 leaderUserId（精确关联，不依赖文本 fuzzy match）
  const handleSelectOrganizationLeader = useCallback((employee: EmployeeRecord | null) => {
    if (!canEdit) return;
    const nextValue = applyOrganizationLeaderUserIdDraft(
      value,
      employee?.id ?? null,
      employee?.fullName ?? '',
    );
    if (nextValue !== value) {
      onChange(nextValue);
    }
    setOrganizationLeaderNameInput(employee?.fullName ?? '');
    clearOrganizationLeaderNameDraft();
  }, [canEdit, clearOrganizationLeaderNameDraft, onChange, value]);

  // 通过下拉选择岗位持岗人：更新 bindings (userId.primaryRoleId = roleId)
  // 一个员工同时只能有一个 primaryRoleId；若选的员工已经占别的岗位，会被搬过来；
  // 若该岗位本来有人，那个人的 primaryRoleId 会被置空。
  // 顾源源 5/24 M3: 选员工时同时清掉 role.holderBotId (员工/机器人持岗人互斥)
  const handleSelectRoleHolder = useCallback((roleId: string, employee: EmployeeRecord | null) => {
    if (!canEdit) return;
    const timestamp = new Date().toISOString();
    const targetUserId = employee?.id ?? null;

    // 1. 找到当前占该岗位的员工 binding
    const currentHolder = value.bindings.find((b) => b.primaryRoleId === roleId);

    // 2. 找到目标员工的现有 binding（如果有）
    const targetExistingBinding = targetUserId
      ? value.bindings.find((b) => b.userId === targetUserId)
      : null;

    // 3. 从该岗位查 departmentId（用作新 binding 的 default departmentId）
    const role = value.roles.find((r) => r.id === roleId);
    const inferredDepartmentId = role?.departmentId ?? null;
    const hadBotHolder = !!(role && role.holderBotId);
    // No-op: 当前员工持岗人就是目标 且 没有 bot 持岗人 → 跳过
    if (!hadBotHolder && (currentHolder?.userId ?? null) === targetUserId) return;

    let nextBindings = value.bindings.map((b) => {
      // 清空 current holder 的 primaryRoleId（如果要换人/清空）
      if (currentHolder && b.userId === currentHolder.userId && currentHolder.userId !== targetUserId) {
        return { ...b, primaryRoleId: null, updatedAt: timestamp };
      }
      // 把 target employee 的 binding.primaryRoleId 设到此 role
      if (targetUserId && b.userId === targetUserId) {
        return {
          ...b,
          primaryRoleId: roleId,
          departmentId: b.departmentId ?? inferredDepartmentId,
          updatedAt: timestamp,
        };
      }
      return b;
    });

    // 4. 如果 target employee 没有现存 binding，新建一条
    if (targetUserId && !targetExistingBinding) {
      nextBindings = [
        ...nextBindings,
        {
          userId: targetUserId,
          departmentId: inferredDepartmentId,
          primaryRoleId: roleId,
          managerUserId: null,
          isManager: false,
          projectRoleLabels: [],
          currentFocus: '',
          taskEditScope: 'self',
          canApproveTasks: false,
          canReassignTasks: false,
          canChangeDeadline: false,
          updatedAt: timestamp,
        },
      ];
    }

    // M3: 同时清空 role.holderBotId (员工/机器人持岗人互斥)
    const nextRoles = hadBotHolder
      ? value.roles.map((r) =>
          r.id === roleId ? { ...r, holderBotId: null, updatedAt: timestamp } : r,
        )
      : value.roles;

    onChange({
      ...value,
      bindings: nextBindings,
      roles: nextRoles,
      updatedAt: timestamp,
    });
  }, [canEdit, onChange, value]);

  // 顾源源 5/24 M3: 选机器人同事作为持岗人
  // 写 role.holderBotId, 同时清掉该岗位现有的员工 binding.primaryRoleId
  const handleSelectRoleHolderBot = useCallback((roleId: string, bot: BotMemberRecord | null) => {
    if (!canEdit) return;
    const timestamp = new Date().toISOString();
    const targetBotId = bot?.id ?? null;

    const role = value.roles.find((r) => r.id === roleId);
    if (!role) return;
    if ((role.holderBotId ?? null) === targetBotId) return;

    // 清掉现有的员工 binding (如果有), 让该岗位由机器人独占
    const currentHolder = value.bindings.find((b) => b.primaryRoleId === roleId);
    const nextBindings = currentHolder
      ? value.bindings.map((b) =>
          b.userId === currentHolder.userId
            ? { ...b, primaryRoleId: null, updatedAt: timestamp }
            : b,
        )
      : value.bindings;

    const nextRoles = value.roles.map((r) =>
      r.id === roleId ? { ...r, holderBotId: targetBotId, updatedAt: timestamp } : r,
    );

    onChange({
      ...value,
      roles: nextRoles,
      bindings: nextBindings,
      updatedAt: timestamp,
    });
  }, [canEdit, onChange, value]);

  // 通过下拉选择部门负责人：同时绑定 leaderUserId
  const handleSelectDepartmentLeader = useCallback((departmentId: string, employee: EmployeeRecord | null) => {
    if (!canEdit) return;
    const currentDepartment = value.departments.find((department) => department.id === departmentId);
    let nextValue = applyDepartmentLeaderUserIdDraft(
      value,
      departmentId,
      employee?.id ?? null,
      employee?.fullName ?? '',
    );
    const timestamp = new Date().toISOString();
    const targetUserId = employee?.id ?? null;
    if (targetUserId) {
      const targetBinding = nextValue.bindings.find((binding) => binding.userId === targetUserId);
      nextValue = {
        ...nextValue,
        bindings: nextValue.bindings.map((binding) => {
          if (binding.departmentId === departmentId && binding.userId !== targetUserId && binding.isManager) {
            return {
              ...binding,
              isManager: false,
              visibilityScope: binding.visibilityScope === 'department' ? 'self' : binding.visibilityScope,
              taskEditScope: binding.taskEditScope === 'department' ? 'self' : binding.taskEditScope,
              updatedAt: timestamp,
            };
          }
          if (binding.userId === targetUserId) {
            return {
              ...binding,
              departmentId,
              isManager: true,
              visibilityScope: binding.visibilityScope === 'organization' ? 'organization' : 'department',
              taskEditScope: binding.taskEditScope === 'organization' ? 'organization' : 'department',
              updatedAt: timestamp,
            };
          }
          return binding;
        }),
        updatedAt: timestamp,
      };
      if (!targetBinding) {
        nextValue = {
          ...nextValue,
          bindings: [
            ...nextValue.bindings,
            {
              userId: targetUserId,
              departmentId,
              primaryRoleId: null,
              managerUserId: null,
              isManager: true,
              visibilityScope: 'department',
              projectRoleLabels: [],
              currentFocus: '',
              taskEditScope: 'department',
              canApproveTasks: false,
              canReassignTasks: false,
              canChangeDeadline: false,
              updatedAt: timestamp,
            },
          ],
          updatedAt: timestamp,
        };
      }
    } else if (currentDepartment?.leaderUserId) {
      nextValue = {
        ...nextValue,
        bindings: nextValue.bindings.map((binding) => (
          binding.userId === currentDepartment.leaderUserId && binding.departmentId === departmentId
              ? {
                ...binding,
                isManager: false,
                visibilityScope: binding.visibilityScope === 'department' ? 'self' : binding.visibilityScope,
                taskEditScope: binding.taskEditScope === 'department' ? 'self' : binding.taskEditScope,
                updatedAt: timestamp,
              }
              : binding
        )),
        updatedAt: timestamp,
      };
    }
    if (nextValue !== value) {
      onChange(nextValue);
    }
    setDepartmentLeaderNameInputs((previous) => {
      if (!(departmentId in previous)) return previous;
      const next = { ...previous };
      delete next[departmentId];
      return next;
    });
    clearDepartmentLeaderNameDraft(departmentId);
  }, [canEdit, clearDepartmentLeaderNameDraft, onChange, value]);

  const handleDepartmentLeaderNameChange = useCallback((departmentId: string, nextName: string) => {
    setDepartmentLeaderNameDraft(departmentId, nextName);
  }, [setDepartmentLeaderNameDraft]);

  const commitDepartmentLeaderNameInput = useCallback((departmentId: string) => {
    if (!canEdit) return;
    const rawDraft = departmentLeaderNameInputs[departmentId];
    if (rawDraft === undefined) return;
    const department = value.departments.find((item) => item.id === departmentId);
    if (!department) return;
    const nextName = rawDraft.trim();
      const currentName = visibleLeaderNameInput(department.leaderName);
      if (nextName !== currentName || department.leaderUserId) {
      updateDepartment(departmentId, {
        leaderUserId: null,
        leaderName: nextName,
        updatedAt: new Date().toISOString(),
      });
    }
    setDepartmentLeaderNameInputs((previous) => {
      if (!(departmentId in previous)) return previous;
      const next = { ...previous };
      delete next[departmentId];
      return next;
    });
    clearDepartmentLeaderNameDraft(departmentId);
  }, [canEdit, clearDepartmentLeaderNameDraft, departmentLeaderNameInputs, updateDepartment, value.departments]);

  const handleUploadOrganizationIntroDocument = useCallback(async () => {
    if (!canEdit || !onUploadIntroDocument) return;
    const document = await onUploadIntroDocument(`${organizationName || '当前组织'}组织介绍`);
    if (!document) return;
    setPendingOrganizationIntroDocument(document);
    showToast('组织介绍已添加，点击右上角对勾保存');
  }, [canEdit, onUploadIntroDocument, organizationName, showToast]);

  const handleUploadDepartmentIntroDocument = useCallback(async (department: TreeDepartmentNode) => {
    if (!canEdit || !onUploadIntroDocument) return;
    const document = await onUploadIntroDocument(`${department.name || '部门'}介绍`);
    if (!document) return;
    setPendingDepartmentIntroDocuments((previous) => ({
      ...previous,
      [department.id]: document,
    }));
    showToast('部门介绍已添加，点击卡片右上角对勾保存');
  }, [canEdit, onUploadIntroDocument, showToast]);

  const finishEditing = useCallback(() => {
    setEditingNodeId(null);
    setEditingField(null);
    setEditingText('');
    clearEditingDraft();
  }, [clearEditingDraft]);

  const applyActiveEditingDraft = useCallback((baseValue: OrgModelSettings, targetNodeId?: string) => {
    const nextValue = editingText.trim();
    if (!editingNodeId || !editingField || !nextValue) {
      return baseValue;
    }
    if (targetNodeId && editingNodeId !== targetNodeId) {
      return baseValue;
    }
    if (editingNodeId === tree.id && editingField === 'name') {
      return applyOrganizationNameDraft(baseValue, nextValue);
    }
    const targetDepartment = activeDepartments.find((item) => item.id === editingNodeId);
    if (targetDepartment) {
      if (editingField === 'name') {
        return applyDepartmentNameDraft(baseValue, editingNodeId, nextValue);
      }
      return applyDepartmentLeaderNameDrafts(baseValue, { [editingNodeId]: nextValue });
    }
    const targetRole = activeRoles.find((item) => item.id === editingNodeId);
    if (targetRole && editingField === 'name') {
      return applyRoleNameDraft(baseValue, editingNodeId, nextValue);
    }
    return baseValue;
  }, [activeDepartments, activeRoles, editingField, editingNodeId, editingText, tree.id]);

  const handleSaveEdit = useCallback(() => {
    const nextValue = applyActiveEditingDraft(value);
    if (nextValue !== value) {
      onChange(nextValue);
    }
    finishEditing();
  }, [applyActiveEditingDraft, finishEditing, onChange, value]);

  const handleKeyDown = useCallback((event: React.KeyboardEvent<HTMLInputElement>) => {
    if (isComposingKeyEvent(event, textCompositionRef)) return;
    if (event.key === 'Enter') {
      handleSaveEdit();
    }
    if (event.key === 'Escape') {
      finishEditing();
    }
  }, [finishEditing, handleSaveEdit]);

  const startEditing = useCallback((nodeId: string, field: EditableField, currentText: string) => {
    if (!canEdit) return;
    const nextText = currentText || '';
    setEditingNodeId(nodeId);
    setEditingField(field);
    setEditingText(nextText);
    updateInputDrafts((current) => ({
      ...current,
      editingNodeId: nodeId,
      editingField: field,
      editingText: nextText,
    }));
  }, [canEdit, updateInputDrafts]);

  const handleAddDepartment = useCallback((preset?: { name?: string; parentDepartmentId?: string | null }) => {
    if (!canEdit) return;
    const nextIndex = value.departments.length;
    const nextDepartment: OrgDepartmentSettings = {
      id: nextUiId('department'),
      name: preset?.name || `新部门 ${nextIndex + 1}`,
      color: departmentColor(nextIndex),
      leaderUserId: null,
      leaderName: '',
      introDocument: null,
      parentDepartmentId: preset?.parentDepartmentId ?? null,
      mission: '',
      businessContext: '',
      teamContext: '',
      quarterPlan: buildEmptyQuarterPlan(),
      quarterlyFocus: [],
      collaborationDepartmentIds: [],
      active: true,
      updatedAt: '',
    };

    onChange({
      ...value,
      departments: [...value.departments, nextDepartment],
    });

    window.setTimeout(() => {
      startEditing(nextDepartment.id, 'name', nextDepartment.name);
    }, 10);
  }, [canEdit, onChange, startEditing, value]);

  const handleAddManagementTitle = useCallback(() => {
    if (!canEdit) return;
    const timestamp = new Date().toISOString();
    const nextRole: OrgRoleTemplateSettings = {
      id: nextUiId('management_title'),
      departmentId: null,
      name: '新管理层头衔',
      level: 'organization_lead',
      visibilityScope: 'organization',
      managerRoleId: null,
      isManager: true,
      goal: '',
      responsibilities: [],
      shouldAvoid: [],
      collaborationRoleIds: [],
      taskEditScope: 'organization',
      canApproveTasks: true,
      canReassignTasks: true,
      canChangeDeadline: true,
      sortOrder: value.roles.length,
      active: true,
      updatedAt: timestamp,
    };

    onChange({
      ...value,
      roles: [...value.roles, nextRole],
      updatedAt: timestamp,
    });

    window.setTimeout(() => {
      startEditing(nextRole.id, 'name', nextRole.name);
    }, 10);
  }, [canEdit, onChange, startEditing, value]);
  const handleAddManagementDepartment = handleAddManagementTitle;

  const handleAddManagementTitleMember = useCallback((role: OrgRoleTemplateSettings, employee: EmployeeRecord | null) => {
    if (!canEdit || !employee) return;
    const timestamp = new Date().toISOString();
    const existingBinding = bindingByUserId.get(employee.id);
    const nextBinding: OrgEmployeeBindingSettings = existingBinding
      ? {
        ...existingBinding,
        departmentId: existingBinding.departmentId ?? employee.departmentId ?? null,
        primaryRoleId: role.id,
        visibilityScope: 'organization',
        taskEditScope: existingBinding.taskEditScope === 'self' ? 'organization' : existingBinding.taskEditScope,
        updatedAt: timestamp,
      }
      : {
        userId: employee.id,
        departmentId: employee.departmentId ?? null,
        primaryRoleId: role.id,
        managerUserId: null,
        isManager: Boolean(employee.isDepartmentLead),
        visibilityScope: 'organization',
        projectRoleLabels: [],
        currentFocus: employee.currentFocus || '',
        taskEditScope: 'organization',
        canApproveTasks: false,
        canReassignTasks: false,
        canChangeDeadline: false,
        updatedAt: timestamp,
      };
    onChange({
      ...value,
      bindings: existingBinding
        ? value.bindings.map((binding) => (binding.userId === employee.id ? nextBinding : binding))
        : [...value.bindings, nextBinding],
      updatedAt: timestamp,
    });
    showToast(`已将 ${employee.fullName || employee.email || '该成员'} 加入 ${role.name || '管理层头衔'}，记得保存修改`);
  }, [bindingByUserId, canEdit, onChange, showToast, value]);

  const handleRemoveManagementTitleMember = useCallback((role: OrgRoleTemplateSettings, employeeId: string) => {
    if (!canEdit) return;
    const timestamp = new Date().toISOString();
    const employee = approvedEmployeeById.get(employeeId);
    const existingBinding = bindingByUserId.get(employeeId);
    const nextScope: OrgVisibilityScope = (existingBinding?.isManager || employee?.isDepartmentLead) ? 'department' : 'self';
    const nextTaskEditScope: OrgTaskEditScope = nextScope === 'department' ? 'department' : 'self';
    const nextBinding: OrgEmployeeBindingSettings = existingBinding
      ? {
        ...existingBinding,
        primaryRoleId: existingBinding.primaryRoleId === role.id ? null : existingBinding.primaryRoleId,
        visibilityScope: existingBinding.primaryRoleId === role.id ? nextScope : existingBinding.visibilityScope,
        taskEditScope: existingBinding.primaryRoleId === role.id && existingBinding.taskEditScope === 'organization'
          ? nextTaskEditScope
          : existingBinding.taskEditScope,
        updatedAt: timestamp,
      }
      : {
        userId: employeeId,
        departmentId: employee?.departmentId ?? null,
        primaryRoleId: null,
        managerUserId: null,
        isManager: Boolean(employee?.isDepartmentLead),
        visibilityScope: nextScope,
        projectRoleLabels: [],
        currentFocus: employee?.currentFocus || '',
        taskEditScope: nextTaskEditScope,
        canApproveTasks: false,
        canReassignTasks: false,
        canChangeDeadline: false,
        updatedAt: timestamp,
      };
    onChange({
      ...value,
      bindings: existingBinding
        ? value.bindings.map((binding) => (binding.userId === employeeId ? nextBinding : binding))
        : [...value.bindings, nextBinding],
      updatedAt: timestamp,
    });
    showToast(`已从 ${role.name || '管理层头衔'} 移除该成员，记得保存修改`);
  }, [approvedEmployeeById, bindingByUserId, canEdit, onChange, showToast, value]);

  const handleUpdateDepartmentParent = useCallback((departmentId: string, parentDepartmentId: string | null) => {
    if (!canEdit) return;
    if (departmentId === parentDepartmentId) return;
    onChange({
      ...value,
      departments: value.departments.map((department) => (
        department.id === departmentId
          ? { ...department, parentDepartmentId, updatedAt: new Date().toISOString() }
          : department
      )),
    });
  }, [canEdit, onChange, value]);

  const handleAddRole = useCallback((departmentId: string | null) => {
    if (!canEdit) return;
    const nextRole: OrgRoleTemplateSettings = {
      id: nextUiId('role'),
      departmentId,
      name: '新岗位',
      level: 'employee',
      managerRoleId: null,
      isManager: false,
      goal: '',
      responsibilities: [],
      shouldAvoid: [],
      collaborationRoleIds: [],
      taskEditScope: 'self',
      canApproveTasks: false,
      canReassignTasks: false,
      canChangeDeadline: false,
      sortOrder: value.roles.length,
      active: true,
      updatedAt: '',
    };

    onChange({
      ...value,
      roles: [...value.roles, nextRole],
    });

    window.setTimeout(() => {
      startEditing(nextRole.id, 'name', nextRole.name);
    }, 10);
  }, [canEdit, onChange, startEditing, value]);

  const handleDeleteDepartment = useCallback((departmentId: string) => {
    if (!canEdit) return;
    const timestamp = new Date().toISOString();

    onChange({
      ...value,
      departments: value.departments.map((department) => (
        department.id === departmentId
          ? { ...department, active: false, updatedAt: timestamp }
          : department
      )),
      roles: value.roles.map((role) => (
        role.departmentId === departmentId
          ? { ...role, active: false, updatedAt: timestamp }
          : role
      )),
      bindings: value.bindings.map((binding) => (
        binding.departmentId === departmentId
          ? {
            ...binding,
            departmentId: null,
            isManager: false,
            visibilityScope: binding.visibilityScope === 'department' ? 'self' : binding.visibilityScope,
            taskEditScope: binding.taskEditScope === 'department' ? 'self' : binding.taskEditScope,
            updatedAt: timestamp,
          }
          : binding
      )),
      departmentPlans: value.departmentPlans.map((plan) => (
        plan.departmentId === departmentId
          ? { ...plan, departmentId: null }
          : plan
      )),
    });
  }, [canEdit, onChange, value]);

  const handleDeleteRole = useCallback((roleId: string) => {
    if (!canEdit) return;

    onChange({
      ...value,
      roles: value.roles.map((role) => (
        role.id === roleId
          ? { ...role, active: false, updatedAt: role.updatedAt || new Date().toISOString() }
          : role
      )),
      bindings: value.bindings.map((binding) => (
        binding.primaryRoleId === roleId
          ? { ...binding, primaryRoleId: null, updatedAt: binding.updatedAt || new Date().toISOString() }
          : binding
      )),
    });
  }, [canEdit, onChange, value]);

  const handleSaveOrganizationCard = useCallback(async () => {
    if (!canEdit || isSaving) return;
    let nextValue = applyOrganizationNameDraft(value, organizationNameInput);
    nextValue = applyOrganizationLeaderNameDraft(nextValue, organizationLeaderNameInput);
    if (pendingOrganizationIntroDocument) {
      nextValue = applyOrganizationIntroDocumentDraft(nextValue, pendingOrganizationIntroDocument);
    }
    nextValue = applyActiveEditingDraft(nextValue, tree.id);
    if (nextValue !== value) {
      onChange(nextValue);
    }
    setPendingOrganizationIntroDocument(null);
    setOrganizationNameInput(nextValue.organization.name);
    setOrganizationLeaderNameInput(visibleLeaderNameInput(nextValue.organization.leaderName));
    clearOrganizationNameDraft();
    clearOrganizationLeaderNameDraft();
    if (editingNodeId === tree.id) {
      finishEditing();
    }
    const saved = await onSave(nextValue);
    if (saved !== false) {
      showToast('当前组织卡片已保存');
    }
  }, [
    applyActiveEditingDraft,
    canEdit,
    clearOrganizationLeaderNameDraft,
    clearOrganizationNameDraft,
    editingNodeId,
    finishEditing,
    isSaving,
    onChange,
    onSave,
    organizationLeaderNameInput,
    organizationNameInput,
    pendingOrganizationIntroDocument,
    showToast,
    tree.id,
    value,
  ]);

  const handleSaveDepartmentCard = useCallback((departmentId: string) => {
    if (!canEdit || isSaving) return;
    let nextValue = value;
    if (Object.prototype.hasOwnProperty.call(departmentLeaderNameInputs, departmentId)) {
      nextValue = applyDepartmentLeaderNameDrafts(nextValue, {
        [departmentId]: departmentLeaderNameInputs[departmentId],
      });
    }
    if (pendingDepartmentIntroDocuments[departmentId]) {
      nextValue = applyDepartmentIntroDocumentDraft(nextValue, departmentId, pendingDepartmentIntroDocuments[departmentId]);
    }
    nextValue = applyActiveEditingDraft(nextValue, departmentId);
    if (nextValue !== value) {
      onChange(nextValue);
    }
    setDepartmentLeaderNameInputs((previous) => {
      if (!(departmentId in previous)) return previous;
      const next = { ...previous };
      delete next[departmentId];
      return next;
    });
    setPendingDepartmentIntroDocuments((previous) => {
      if (!(departmentId in previous)) return previous;
      const next = { ...previous };
      delete next[departmentId];
      return next;
    });
    clearDepartmentLeaderNameDraft(departmentId);
    if (editingNodeId === departmentId) {
      finishEditing();
    }
    void onSave(nextValue);
    showToast('部门卡片已保存');
  }, [
    applyActiveEditingDraft,
    canEdit,
    clearDepartmentLeaderNameDraft,
    departmentLeaderNameInputs,
    editingNodeId,
    finishEditing,
    isSaving,
    onChange,
    onSave,
    pendingDepartmentIntroDocuments,
    showToast,
    value,
  ]);

  const handleSaveRoleCard = useCallback((roleId: string) => {
    if (!canEdit || isSaving) return;
    const nextValue = applyActiveEditingDraft(value, roleId);
    if (nextValue !== value) {
      onChange(nextValue);
    }
    if (editingNodeId === roleId) {
      finishEditing();
    }
    void onSave(nextValue);
    showToast('头衔已保存');
  }, [
    applyActiveEditingDraft,
    canEdit,
    editingNodeId,
    finishEditing,
    isSaving,
    onChange,
    onSave,
    showToast,
    value,
  ]);

  const handleSave = useCallback(async () => {
    const hasDepartmentLeaderDrafts = Object.keys(departmentLeaderNameInputs).length > 0;
    let nextValue = applyOrganizationNameDraft(value, organizationNameInput);
    nextValue = applyOrganizationLeaderNameDraft(nextValue, organizationLeaderNameInput);
    nextValue = applyDepartmentLeaderNameDrafts(nextValue, departmentLeaderNameInputs);
    if (pendingOrganizationIntroDocument) {
      nextValue = applyOrganizationIntroDocumentDraft(nextValue, pendingOrganizationIntroDocument);
    }
    Object.entries(pendingDepartmentIntroDocuments).forEach(([departmentId, document]) => {
      nextValue = applyDepartmentIntroDocumentDraft(nextValue, departmentId, document);
    });
    nextValue = applyActiveEditingDraft(nextValue);
    if (nextValue !== value) {
      onChange(nextValue);
    }
    setOrganizationNameInput(nextValue.organization.name);
    setOrganizationLeaderNameInput(visibleLeaderNameInput(nextValue.organization.leaderName));
    if (hasDepartmentLeaderDrafts) {
      setDepartmentLeaderNameInputs({});
    }
    setPendingOrganizationIntroDocument(null);
    setPendingDepartmentIntroDocuments({});
    clearInputDrafts();
    finishEditing();
    const saved = await onSave(nextValue);
    if (saved !== false) {
      showToast('组织结构已保存');
      // 保存成功后自动退出编辑模式：右上角的 X / + / 对勾按钮一齐隐藏，
      // 用户视觉上明确知道"这一轮编辑已完成"，下次还要改再点"编辑组织"。
      setIsEditingMode(false);
    }
  }, [applyActiveEditingDraft, clearInputDrafts, departmentLeaderNameInputs, finishEditing, onChange, onSave, organizationLeaderNameInput, organizationNameInput, pendingDepartmentIntroDocuments, pendingOrganizationIntroDocument, showToast, value]);

  const handleOpenResetConfirm = useCallback(() => {
    if (!canEdit || isSaving) return;
    setResetConfirmText('');
    setResetConfirmOpen(true);
  }, [canEdit, isSaving]);

  const handleResetOrganizationSetup = useCallback(() => {
    if (!canEdit || isSaving) return;
    if (resetConfirmText.trim() !== '重新搭建') {
      showToast('请输入“重新搭建”确认');
      return;
    }
    const nextValue = buildEmptyOrgModel(value);
    onChange(nextValue);
    setOrganizationNameInput('');
    setOrganizationLeaderNameInput('');
    setDepartmentLeaderNameInputs({});
    setPendingOrganizationIntroDocument(null);
    setPendingDepartmentIntroDocuments({});
    clearInputDrafts();
    setActiveView('tree');
    setEditingNodeId(null);
    setEditingField(null);
    setResetConfirmOpen(false);
    setResetConfirmText('');
    void onSave(nextValue);
    showToast('组织搭建已清空');
  }, [canEdit, clearInputDrafts, isSaving, onChange, onSave, resetConfirmText, showToast, value]);

  const organizationCardDirty = organizationNameInput.trim() !== value.organization.name
    || visibleLeaderNameInput(organizationLeaderNameInput) !== visibleLeaderNameInput(value.organization.leaderName)
    || introDocumentChanged(pendingOrganizationIntroDocument, value.organization.introDocument)
    || (editingNodeId === tree.id && editingText.trim().length > 0 && editingText.trim() !== tree.name);
  const semanticAuthority = value.authorityStates?.unfrozenSemanticFields;
  const roleProcessAutomation = value.authorityStates?.roleProcessAutomation;

  return (
    <div className="space-y-6">
      {toast ? (
        <div className="fixed top-6 left-1/2 z-50 -translate-x-1/2 rounded-full bg-gray-900/90 px-5 py-2.5 text-[13px] font-medium text-white shadow-lg">
          {toast}
        </div>
      ) : null}

      {semanticAuthority?.state === 'blocked' ? (
        <div className="rounded-2xl border border-amber-200 bg-amber-50 px-5 py-4 text-[13px] leading-6 text-amber-900">
          <div className="font-bold">部分组织语义字段尚未接通</div>
          <div className="mt-1">
            {semanticAuthority.message || '当前仅组织身份结构与组织计划是严格权威数据。'}
          </div>
          <div className="mt-1 text-[12px] text-amber-700">
            未接通字段显示的空白不是“组织没有数据”，保存这些字段会被阻止。
          </div>
        </div>
      ) : null}

      {roleProcessAutomation?.state === 'blocked' ? (
        <div className="rounded-2xl border border-blue-200 bg-blue-50 px-5 py-4 text-[13px] leading-6 text-blue-900">
          <div className="font-bold">角色流程模板可保存，自动执行尚未接通</div>
          <div className="mt-1">
            {roleProcessAutomation.message
              || '当前可配置和共享模板；触发后不会创建假的执行记录。'}
          </div>
        </div>
      ) : null}

      {resetConfirmOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/30 px-4 backdrop-blur-sm">
          <div className="w-full max-w-[440px] rounded-[24px] border border-gray-100 bg-white p-6 shadow-[0_28px_90px_rgba(15,23,42,0.18)]">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="inline-flex h-10 w-10 items-center justify-center rounded-full bg-rose-50 text-rose-500">
                  <Trash2 size={18} />
                </div>
                <h3 className="mt-4 text-[18px] font-bold text-gray-900">删除当前组织搭建</h3>
                <p className="mt-2 text-[13px] leading-6 text-gray-500">
                  会清空部门、管理层头衔、负责人、规则、计划和邀请码，保留组织身份与当前账号。
                </p>
              </div>
              <button
                type="button"
                onClick={() => setResetConfirmOpen(false)}
                className="inline-flex h-8 w-8 items-center justify-center rounded-full text-gray-400 transition hover:bg-gray-100 hover:text-gray-700"
              >
                <X size={16} />
              </button>
            </div>

            <div className="mt-5">
              <label className="text-[12px] font-bold text-gray-500">
                输入“重新搭建”确认
              </label>
              <input
                autoFocus
                value={resetConfirmText}
                onCompositionStart={handleTextCompositionStart}
                onCompositionEnd={handleTextCompositionEnd}
                onChange={(event) => setResetConfirmText(event.target.value)}
                onKeyDown={(event) => {
                  if (isComposingKeyEvent(event, textCompositionRef)) return;
                  if (event.key === 'Enter') {
                    handleResetOrganizationSetup();
                  }
                }}
                className="mt-2 w-full rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 text-[14px] font-medium text-gray-900 outline-none transition focus:border-rose-300 focus:bg-white"
              />
            </div>

            <div className="mt-6 flex justify-end gap-3">
              <button
                type="button"
                onClick={() => setResetConfirmOpen(false)}
                className="rounded-full border border-gray-200 bg-white px-5 py-2.5 text-[13px] font-bold text-gray-600 transition hover:border-gray-300 hover:bg-gray-50"
              >
                取消
              </button>
              <button
                type="button"
                onClick={handleResetOrganizationSetup}
                disabled={isSaving || resetConfirmText.trim() !== '重新搭建'}
                className="inline-flex items-center gap-2 rounded-full bg-rose-500 px-5 py-2.5 text-[13px] font-bold text-white transition hover:bg-rose-600 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Trash2 size={14} />
                确认删除
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <div className="grid grid-cols-2 gap-x-8 gap-y-6 xl:grid-cols-5">
        {stats.map((stat) => {
          const numericValue = typeof stat.value === 'number' ? stat.value : Number(stat.value);
          const hasValue = !Number.isNaN(numericValue) && numericValue > 0;
          return (
            <div key={stat.label} className="flex flex-col">
              <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">{stat.label}</p>
              <span className="mt-3 text-[36px] leading-none font-light tracking-tight text-gray-900">{stat.value}</span>
              <div className={`mt-3 h-[2px] w-8 rounded-full ${hasValue ? 'bg-emerald-500' : 'bg-transparent'}`} />
            </div>
          );
        })}
      </div>

      <div className="overflow-hidden rounded-[32px] border border-[#DCE4FF] bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-gray-100 px-8 py-6">
          <div className="flex items-center gap-3">
            {activeView === 'codes' ? (
              <button
                type="button"
                onClick={() => setActiveView('tree')}
                className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-gray-200 text-gray-500 transition hover:border-[#5B7BFE]/40 hover:text-[#5B7BFE]"
              >
                <ChevronLeft size={18} />
              </button>
            ) : null}
            <div className="inline-flex items-center gap-2 rounded-full border border-gray-100 bg-white px-4 py-2 text-[13px] font-bold text-[#5B7BFE] shadow-sm">
              <Building2 size={14} />
              组织搭建中心
            </div>
          </div>
          <div className="flex items-center gap-3">
            {canEdit ? (
              isEditingMode ? (
                <>
                  <button
                    type="button"
                    onClick={handleSave}
                    disabled={isSaving}
                    className="inline-flex items-center gap-2 rounded-full bg-[#5B7BFE] px-5 py-3 text-[13px] font-bold text-white shadow-[0_12px_30px_rgba(91,123,254,0.25)] transition hover:bg-[#4A63CF] disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <Save size={14} />
                    {isSaving ? '保存中' : '保存修改'}
                  </button>
                  <button
                    type="button"
                    onClick={() => setIsEditingMode(false)}
                    disabled={isSaving}
                    className="inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white px-4 py-3 text-[13px] font-bold text-gray-600 transition hover:border-gray-300 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <X size={14} />
                    退出编辑
                  </button>
                  <button
                    type="button"
                    onClick={handleOpenResetConfirm}
                    disabled={isSaving}
                    className="inline-flex items-center gap-1.5 rounded-full border border-rose-100 bg-white px-3 py-2 text-[12px] font-medium text-rose-500 transition hover:border-rose-200 hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-60"
                    title="谨慎操作：会删除整个组织搭建"
                  >
                    <Trash2 size={12} />
                    删除搭建
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  onClick={() => setIsEditingMode(true)}
                  className="inline-flex items-center gap-2 rounded-full bg-[#5B7BFE] px-5 py-3 text-[13px] font-bold text-white shadow-[0_12px_30px_rgba(91,123,254,0.25)] transition hover:bg-[#4A63CF]"
                >
                  <Pencil size={14} />
                  编辑组织
                </button>
              )
            ) : null}
            {/* 顾源源 5/24: 添加机器人同事入口已挪到每个部门"添加岗位"旁边, header 不重复放. */}
            {activeView === 'tree' ? (
              <button
                type="button"
                onClick={() => setActiveView('codes')}
                className="rounded-full border border-[#DCE4FF] bg-white px-5 py-3 text-[13px] font-bold text-[#4A63CF] transition hover:border-[#5B7BFE]/40 hover:text-[#5B7BFE]"
              >
                查看邀请码
              </button>
            ) : (
              <button
                type="button"
                onClick={() => void handleCopyAllInvites()}
                className="inline-flex items-center gap-2 rounded-full border border-[#DCE4FF] bg-white px-5 py-3 text-[13px] font-bold text-[#4A63CF] transition hover:border-[#5B7BFE]/40 hover:text-[#5B7BFE]"
              >
                {bulkInviteCopied ? <Check size={14} /> : <Copy size={14} />}
                {bulkInviteCopied ? '已复制全部邀请码' : '一键复制邀请码'}
              </button>
            )}
          </div>
        </div>

        <div className="relative overflow-x-auto bg-[#F9FAFB]">
          {activeView === 'tree' ? (
            <>
              <div className="relative z-10 min-w-[980px] space-y-6 p-8">
                <div className="relative max-w-[330px] rounded-[28px] border border-[#DCE4FF] bg-white p-6 shadow-sm">
                  {canModify ? (
                    <CardSaveButton
                      active={organizationCardDirty}
                      disabled={isSaving}
                      label="保存当前组织卡片"
                      onClick={handleSaveOrganizationCard}
                    />
                  ) : null}
                  <div className="flex flex-col gap-4 pr-10">
                    <div className="min-w-0">
                      <div className="flex items-center gap-3">
                        <Building2 className="text-[#5B7BFE]" size={20} />
                        {canModify ? (
                          <input
                            value={organizationNameInput}
                            onCompositionStart={handleTextCompositionStart}
                            onCompositionEnd={handleTextCompositionEnd}
                            onChange={(event) => setOrganizationNameDraft(event.target.value)}
                            onBlur={commitOrganizationNameInput}
                            onKeyDown={(event) => {
                              if (isComposingKeyEvent(event, textCompositionRef)) return;
                              if (event.key === 'Enter') {
                                event.currentTarget.blur();
                              }
                              if (event.key === 'Escape') {
                                setOrganizationNameInput(value.organization.name);
                                clearOrganizationNameDraft();
                                event.currentTarget.blur();
                              }
                            }}
                            placeholder="请输入组织名称"
                            className="w-full min-w-[220px] rounded-xl border border-[#DCE4FF] bg-white/90 px-3 py-2 text-[15px] font-bold text-gray-900 outline-none transition placeholder:text-gray-300 focus:border-[#5B7BFE] focus:bg-white"
                          />
                        ) : (
                          <span className="text-[18px] font-bold text-gray-900">
                            {value.organization.name.trim() || '未命名组织'}
                          </span>
                        )}
                      </div>
                      <p className="mt-2 pl-8 text-[12px] leading-5 text-gray-500">
                        组织架构按信息可见范围分为三层：管理层看组织汇总，部门负责人看本部门汇总，成员看个人信息。
                      </p>
                    </div>
                    <IntroDocumentAction
                      canEdit={canModify}
                      disabled={isSaving || !onUploadIntroDocument}
                      document={value.organization.introDocument}
                      label="组织介绍"
                      onUpload={() => void handleUploadOrganizationIntroDocument()}
                      pendingDocument={pendingOrganizationIntroDocument}
                    />
                  </div>
                </div>

                <section className="relative">
                  <div className="mb-5 flex items-center justify-between gap-3">
                    <div>
                      <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-[#5B7BFE]">Three-layer tree</p>
                      <h3 className="mt-1 text-[17px] font-bold text-gray-900">三层信息权限结构</h3>
                      <p className="mt-1 text-[12px] text-gray-500">管理层、部门负责人、部门成员池用连线对应；同一成员可同时拥有多个身份，信息权限取最高。</p>
                    </div>
                  </div>

                  <div className="relative grid min-w-[1120px] grid-cols-[300px_300px_minmax(420px,1fr)] gap-12">
                    <div id="org-tree-management-layer" className="relative z-10 flex flex-col items-stretch">
                      {tree.children.length > 0 ? (
                        <span className="pointer-events-none absolute right-[-48px] top-[194px] z-0 h-px w-12 bg-[#CBD5E1]" />
                      ) : null}
                      <div className="mb-4 text-center">
                        <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-[#5B7BFE]">第一层</p>
                        <h4 className="mt-1 text-[15px] font-bold text-gray-900">组织管理层</h4>
                        <span className="mt-2 inline-flex rounded-full bg-indigo-50 px-2.5 py-1 text-[10px] font-bold text-[#5B7BFE]">
                          组织级
                        </span>
                      </div>
                      <div className="space-y-3">
                        {managementGroups.map((group, index) => {
                          const titleRole = managementTitleRoles.find((role) => role.id === group.key) || null;
                          const isEditingTitleName = Boolean(titleRole && editingNodeId === titleRole.id && editingField === 'name');
                          const titleDirty = Boolean(
                            isEditingTitleName
                            && editingText.trim().length > 0
                            && editingText.trim() !== titleRole?.name,
                          );
                          const inviteCard = titleRole ? managementInviteCards.find((item) => item.key === titleRole.id) : null;
                          return (
                            <div
                              key={group.key}
                              className="relative rounded-2xl border border-indigo-50 bg-white p-3 shadow-sm"
                              style={{ boxShadow: `0 10px 24px ${tint(group.color, '12')}` }}
                            >
                              {canModify && titleRole ? (
                                <CardSaveButton
                                  active={titleDirty}
                                  className="right-2 top-2 h-6 w-6"
                                  disabled={isSaving}
                                  iconSize={11}
                                  label={`保存${titleRole.name}头衔`}
                                  onClick={() => handleSaveRoleCard(titleRole.id)}
                                />
                              ) : null}
                              {canModify && titleRole ? (
                                <button
                                  type="button"
                                  onClick={() => handleDeleteRole(titleRole.id)}
                                  className="absolute -right-1.5 -top-1.5 rounded-full border border-rose-200 bg-white p-0.5 text-rose-400 shadow-sm transition hover:border-rose-300 hover:bg-rose-50 hover:text-rose-500"
                                  title="删除该管理层头衔"
                                >
                                  <X size={11} />
                                </button>
                              ) : null}
                              <div className="flex items-start justify-between gap-3 pr-7">
                                <div className="min-w-0">
                                  <div className="flex min-w-0 items-center gap-1.5">
                                    <span
                                      className="h-2.5 w-2.5 shrink-0 rounded-full"
                                      style={{ backgroundColor: group.color }}
                                    />
                                    {isEditingTitleName && titleRole ? (
                                      <input
                                        autoFocus
                                        value={editingText}
                                        onCompositionStart={handleTextCompositionStart}
                                        onCompositionEnd={handleTextCompositionEnd}
                                        onChange={(event) => setEditingTextDraft(event.target.value)}
                                        onBlur={handleSaveEdit}
                                        onKeyDown={handleKeyDown}
                                        className="w-full border-b border-[#5B7BFE] bg-transparent text-[13px] font-bold text-gray-800 outline-none"
                                      />
                                    ) : (
                                      <>
                                        <span className="truncate text-[13px] font-bold text-gray-800">{group.label}</span>
                                        {canModify && titleRole ? (
                                          <button
                                            type="button"
                                            onClick={() => startEditing(titleRole.id, 'name', titleRole.name)}
                                            className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-gray-300 transition hover:bg-indigo-50 hover:text-[#5B7BFE]"
                                            title="修改头衔名称"
                                          >
                                            <Pencil size={12} />
                                          </button>
                                        ) : null}
                                      </>
                                    )}
                                  </div>
                                  <p className="mt-1 text-[11px] text-gray-400">
                                    {group.key === 'admin' ? '系统权限 · 无邀请码' : '信息权限 · 组织级'}
                                  </p>
                                </div>
                                <span className="rounded-full bg-gray-50 px-2 py-0.5 text-[10px] font-bold text-gray-500">
                                  {index + 1}
                                </span>
                              </div>
                              {inviteCard ? (
                                <div className="mt-2 flex items-center gap-2 rounded-xl border border-indigo-50 bg-indigo-50/70 px-2.5 py-2">
                                  <span className="text-[10px] font-bold text-indigo-400">邀请码</span>
                                  <span className="min-w-0 truncate text-[11px] font-bold tracking-[0.08em] text-[#4A63CF]">{inviteCard.inviteCode}</span>
                                </div>
                              ) : null}
                              <div className="mt-3 space-y-1.5">
                                {group.people.length > 0 ? group.people.map((person) => (
                                  <div key={`${group.key}-${person.id}`} className="flex items-center justify-between gap-2 rounded-xl border border-gray-100 bg-gray-50/80 px-2.5 py-2">
                                    <div className="min-w-0">
                                      <p className="truncate text-[11px] font-bold text-gray-800">{person.name}</p>
                                      <p className="truncate text-[10px] text-gray-400">{person.detail}</p>
                                    </div>
                                    {canModify && titleRole ? (
                                      <button
                                        type="button"
                                        onClick={() => handleRemoveManagementTitleMember(titleRole, person.id)}
                                        className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-gray-300 transition hover:bg-rose-50 hover:text-rose-500"
                                        title="从该管理层头衔移除"
                                      >
                                        <X size={12} />
                                      </button>
                                    ) : null}
                                  </div>
                                )) : (
                                  <p className="rounded-xl border border-dashed border-gray-200 bg-white/70 px-3 py-2 text-[11px] text-gray-400">{group.emptyText}</p>
                                )}
                              </div>
                              {canModify && titleRole ? (
                                <div className="mt-3">
                                  <LeaderPicker
                                    value={{ userId: null, displayName: '' }}
                                    employees={approvedEmployees}
                                    onSelect={(employee) => handleAddManagementTitleMember(titleRole, employee)}
                                    placeholder="添加已有成员"
                                    compact
                                  />
                                </div>
                              ) : null}
                            </div>
                          );
                        })}
                      </div>
                      {canModify ? (
                        <button
                          type="button"
                          onClick={handleAddManagementTitle}
                          className="mt-4 inline-flex w-full items-center justify-center gap-1.5 rounded-2xl border border-dashed border-indigo-200 bg-white/80 px-4 py-3 text-[13px] font-bold text-indigo-500 transition hover:border-indigo-300 hover:bg-indigo-50"
                        >
                          <Plus size={14} />
                          添加管理层头衔
                        </button>
                      ) : null}
                    </div>

                    <div id="org-tree-department-layer" className="relative z-10 flex flex-col items-stretch">
                      <div className="mb-4 text-center">
                        <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-[#0EA5E9]">第二层</p>
                        <h4 className="mt-1 text-[15px] font-bold text-gray-900">部门负责人</h4>
                        <span className="mt-2 inline-flex rounded-full bg-sky-50 px-2.5 py-1 text-[10px] font-bold text-[#0EA5E9]">
                          部门级
                        </span>
                      </div>
                      {tree.children.length === 0 ? (
                        <div className="rounded-2xl border border-dashed border-gray-200 bg-gray-50 px-5 py-8 text-center text-[13px] text-gray-400">
                          暂无部门
                        </div>
                      ) : (
                        <div className="space-y-3">
                          {tree.children.map((department, index) => {
                            const isEditingDepartmentName = editingNodeId === department.id && editingField === 'name';
                            const departmentSettings = value.departments.find((item) => item.id === department.id);
                            const departmentBindings = bindingsByDepartmentId.get(department.id) || [];
                            const memberIdSet = new Set(departmentBindings.map((binding) => binding.userId));
                            if (department.leaderUserId) {
                              memberIdSet.add(department.leaderUserId);
                            }
                            const departmentMembers = approvedEmployees.filter((employee) => (
                              employeeBelongsToDepartment(employee, bindingByUserId.get(employee.id), department)
                              || memberIdSet.has(employee.id)
                            ));
                            const pendingDepartmentIntroDocument = pendingDepartmentIntroDocuments[department.id] || null;
                            const departmentLeaderNameValue = departmentLeaderNameInputs[department.id] ?? department.leaderName ?? '';
                            const hasDepartmentLeaderDraft = Object.prototype.hasOwnProperty.call(departmentLeaderNameInputs, department.id);
                            const departmentCardDirty = (
                              isEditingDepartmentName
                              && editingText.trim().length > 0
                              && editingText.trim() !== department.name
                            ) || (
                              hasDepartmentLeaderDraft
                              && visibleLeaderNameInput(departmentLeaderNameValue) !== visibleLeaderNameInput(department.leaderName)
                            ) || introDocumentChanged(pendingDepartmentIntroDocument, departmentSettings?.introDocument);
                            return (
                              <div
                                id={`org-tree-department-${department.id}`}
                                key={department.id}
                                className="relative min-h-[162px] rounded-2xl border bg-gray-50/70 p-3 pr-9 shadow-sm"
                                style={{ borderColor: tint(department.color, '38') }}
                              >
                                <span
                                  className="pointer-events-none absolute right-[-48px] top-1/2 z-0 h-px w-12 -translate-y-1/2"
                                  style={{ backgroundColor: tint(department.color, '55') }}
                                />
                                {canModify ? (
                                  <CardSaveButton
                                    active={departmentCardDirty}
                                    className="right-2 top-2 h-6 w-6"
                                    disabled={isSaving}
                                    iconSize={11}
                                    label={`保存${department.name}部门`}
                                    onClick={() => handleSaveDepartmentCard(department.id)}
                                  />
                                ) : null}
                                {canModify ? (
                                  <button
                                    type="button"
                                    onClick={() => handleDeleteDepartment(department.id)}
                                    className="absolute -right-1.5 -top-1.5 rounded-full border border-rose-200 bg-white p-0.5 text-rose-400 shadow-sm transition hover:border-rose-300 hover:bg-rose-50 hover:text-rose-500"
                                    title="删除该部门"
                                  >
                                    <X size={11} />
                                  </button>
                                ) : null}
                                <div className="flex items-start justify-between gap-2">
                                  <div className="min-w-0">
                                    <div className="flex min-w-0 items-center gap-1.5">
                                      <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: department.color }} />
                                      {isEditingDepartmentName ? (
                                        <input
                                          autoFocus
                                          value={editingText}
                                          onCompositionStart={handleTextCompositionStart}
                                          onCompositionEnd={handleTextCompositionEnd}
                                          onChange={(event) => setEditingTextDraft(event.target.value)}
                                          onBlur={handleSaveEdit}
                                          onKeyDown={handleKeyDown}
                                          className="w-full border-b border-[#5B7BFE] bg-transparent text-[13px] font-bold text-gray-800 outline-none"
                                        />
                                      ) : (
                                        <>
                                          <span className="truncate text-[13px] font-bold text-gray-900">{department.name}</span>
                                          {canModify ? (
                                            <button
                                              type="button"
                                              onClick={() => startEditing(department.id, 'name', department.name)}
                                              className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-gray-300 transition hover:bg-white hover:text-[#5B7BFE]"
                                              title="修改部门名称"
                                            >
                                              <Pencil size={12} />
                                            </button>
                                          ) : null}
                                        </>
                                      )}
                                    </div>
                                    <p className="mt-1 text-[10px] text-gray-400">第 {index + 1} 个部门</p>
                                  </div>
                                  <IntroDocumentAction
                                    canEdit={canModify}
                                    compact
                                    disabled={isSaving || !onUploadIntroDocument}
                                    document={departmentSettings?.introDocument}
                                    label="部门介绍"
                                    onUpload={() => void handleUploadDepartmentIntroDocument(department)}
                                    pendingDocument={pendingDepartmentIntroDocument}
                                  />
                                </div>
                                <div className="mt-3 rounded-xl border border-white bg-white px-3 py-2">
                                  <p className="mb-2 text-[10px] font-bold text-[#0EA5E9]">负责人</p>
                                  {canModify ? (
                                    <LeaderPicker
                                      value={{
                                        userId: department.leaderUserId ?? null,
                                        displayName: visibleLeaderNameInput(department.leaderName),
                                      }}
                                      employees={departmentMembers.length > 0 ? departmentMembers : approvedEmployees}
                                      onSelect={(employee) => handleSelectDepartmentLeader(department.id, employee)}
                                      placeholder={departmentMembers.length > 0 ? '从成员池选负责人' : '成员池为空'}
                                      compact
                                    />
                                  ) : (
                                    <span className="inline-flex rounded-full border border-blue-100 bg-white px-3 py-1.5 text-[11px] font-bold text-gray-600">
                                      {department.leadName || '待设置负责人'}
                                    </span>
                                  )}
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      )}
                      {canModify ? (
                        <button
                          type="button"
                          onClick={() => handleAddDepartment()}
                          className="mt-4 inline-flex w-full items-center justify-center gap-1.5 rounded-2xl border border-dashed border-gray-300 bg-white/80 px-4 py-3 text-[13px] font-bold text-gray-500 transition hover:border-[#5B7BFE]/60 hover:bg-[#5B7BFE]/5"
                        >
                          <Plus size={14} />
                          添加部门
                        </button>
                      ) : null}
                    </div>

                    <div className="relative z-10 flex flex-col items-stretch">
                      <div className="mb-4 text-center">
                        <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-gray-400">第三层</p>
                        <h4 className="mt-1 text-[15px] font-bold text-gray-900">部门成员池</h4>
                        <span className="mt-2 inline-flex rounded-full bg-gray-100 px-2.5 py-1 text-[10px] font-bold text-gray-500">
                          个人级
                        </span>
                      </div>
                      {tree.children.length === 0 ? (
                        <div className="rounded-2xl border border-dashed border-gray-200 bg-gray-50 px-5 py-8 text-center text-[13px] text-gray-400">
                          新增部门后自动出现对应成员池
                        </div>
                      ) : (
                        <div className="space-y-3">
                          {tree.children.map((department, index) => {
                            const departmentBindings = bindingsByDepartmentId.get(department.id) || [];
                            const memberIdSet = new Set(departmentBindings.map((binding) => binding.userId));
                            if (department.leaderUserId) {
                              memberIdSet.add(department.leaderUserId);
                            }
                            const departmentMembers = approvedEmployees.filter((employee) => (
                              employeeBelongsToDepartment(employee, bindingByUserId.get(employee.id), department)
                              || memberIdSet.has(employee.id)
                            ));
                            const inviteCode = buildDepartmentInviteCode(department.id, {
                              organizationId: value.organization.organizationId,
                              organizationName,
                              departmentName: department.name,
                              order: index,
                            });
                            return (
                              <div
                                id={`org-tree-pool-${department.id}`}
                                key={`${department.id}-pool`}
                                className="min-h-[162px] rounded-2xl border bg-gray-50/70 p-3 shadow-sm"
                                style={{ borderColor: tint(department.color, '32') }}
                              >
                                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                                  <div className="flex min-w-0 items-center gap-2">
                                    <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: department.color }} />
                                    <span className="truncate text-[12px] font-bold text-gray-800">{department.name}</span>
                                  </div>
                                  <span
                                    className="rounded-full px-2.5 py-1 text-[10px] font-bold"
                                    style={{ backgroundColor: tint(department.color), color: department.color }}
                                  >
                                    部门邀请码 {inviteCode}
                                  </span>
                                </div>
                                {departmentMembers.length > 0 ? (
                                  <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                                    {departmentMembers.map((employee) => {
                                      const isLead = employee.id === department.leaderUserId;
                                      const isAdmin = employee.primaryRole === 'admin';
                                      const hasManagementTitle = Boolean(
                                        employee.managementTitleId
                                        || managementTitleRoles.some((role) => bindingByUserId.get(employee.id)?.primaryRoleId === role.id),
                                      );
                                      return (
                                        <div key={employee.id} className="rounded-xl border border-white bg-white px-3 py-2 shadow-sm">
                                          <div className="flex items-center justify-between gap-2">
                                            <span className="truncate text-[12px] font-bold text-gray-800">
                                              {employee.fullName || employee.email || '未命名成员'}
                                            </span>
                                            {isLead ? (
                                              <span className="shrink-0 rounded-full bg-blue-50 px-2 py-0.5 text-[9px] font-bold text-[#0EA5E9]">
                                                负责人
                                              </span>
                                            ) : null}
                                          </div>
                                          <div className="mt-1 flex flex-wrap gap-1">
                                            <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[9px] font-bold text-gray-500">
                                              {visibilityScopeLabel(
                                                isAdmin || hasManagementTitle
                                                  ? 'organization'
                                                  : isLead
                                                    ? 'department'
                                                    : employee.visibilityScope || 'self',
                                              )}
                                            </span>
                                            <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[9px] font-bold text-gray-500">
                                              {systemRoleLabel(employee)}
                                            </span>
                                            <span className="rounded-full bg-blue-50 px-2 py-0.5 text-[9px] font-bold text-[#4A63CF]">
                                              {memberIdentityLabel(employee, department.name, isLead)}
                                            </span>
                                          </div>
                                        </div>
                                      );
                                    })}
                                  </div>
                                ) : (
                                  <p className="rounded-xl border border-dashed border-gray-200 bg-white px-3 py-3 text-[11px] text-gray-400">
                                    暂无成员。成员填写该部门邀请码后会进入这里。
                                  </p>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  </div>
                </section>
              </div>

              {false ? (
            <div className="relative min-w-max p-12">
              <div className="relative z-10 flex items-center gap-12">
                <div
                  id={`node-${tree.id}`}
                  className="relative z-10 flex min-w-[260px] flex-col gap-3 rounded-2xl border-2 border-[#5B7BFE]/30 bg-gradient-to-br from-[#EEF3FF] to-white px-5 py-4 pr-12 shadow-sm"
                >
                  {canModify ? (
                    <CardSaveButton
                      active={organizationCardDirty}
                      disabled={isSaving}
                      label="保存当前组织卡片"
                      onClick={handleSaveOrganizationCard}
                    />
                  ) : null}
                  <div className="flex items-center gap-3">
                    <Building2 className="text-[#5B7BFE]" size={20} />
                    {canModify ? (
                      <input
                        value={organizationNameInput}
                        onCompositionStart={handleTextCompositionStart}
                        onCompositionEnd={handleTextCompositionEnd}
                        onChange={(event) => setOrganizationNameDraft(event.target.value)}
                        onBlur={commitOrganizationNameInput}
                        onKeyDown={(event) => {
                          if (isComposingKeyEvent(event, textCompositionRef)) return;
                          if (event.key === 'Enter') {
                            event.currentTarget.blur();
                          }
                          if (event.key === 'Escape') {
                            setOrganizationNameInput(value.organization.name);
                            clearOrganizationNameDraft();
                            event.currentTarget.blur();
                          }
                        }}
                        placeholder="请输入组织名称"
                        className="w-full min-w-[180px] rounded-xl border border-[#DCE4FF] bg-white/90 px-3 py-2 text-[15px] font-bold text-gray-900 outline-none transition placeholder:text-gray-300 focus:border-[#5B7BFE] focus:bg-white"
                      />
                    ) : (
                      <span className="text-[16px] font-bold text-gray-900">
                        {value.organization.name.trim() || '未命名组织'}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2 pl-8">
                    <span className="text-[11px] font-medium text-gray-400">负责人</span>
                    {canModify ? (
                      <LeaderPicker
                        value={{
                          userId: value.organization.leaderUserId ?? null,
                          displayName: visibleLeaderNameInput(value.organization.leaderName),
                        }}
                        employees={employees}
                        onSelect={handleSelectOrganizationLeader}
                        placeholder="从同事中选负责人"
                      />
                    ) : (
                      <span className="rounded-full border border-[#DCE4FF] bg-white px-3 py-1.5 text-[11px] font-medium text-gray-700">
                        {visibleLeaderNameInput(value.organization.leaderName) || '待填写负责人'}
                      </span>
                    )}
                  </div>
                  <IntroDocumentAction
                    canEdit={canModify}
                    disabled={isSaving || !onUploadIntroDocument}
                    document={value.organization.introDocument}
                    label="组织介绍"
                    onUpload={() => void handleUploadOrganizationIntroDocument()}
                    pendingDocument={pendingOrganizationIntroDocument}
                  />
		                </div>

                <div className="relative z-10 flex min-w-[240px] flex-col gap-3 rounded-2xl border border-[#DCE4FF] bg-white px-4 py-4 shadow-sm">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <Users size={15} className="text-[#5B7BFE]" />
                      <span className="text-[13px] font-bold text-gray-800">管理层</span>
                    </div>
                    <span className="rounded-full bg-[#EEF3FF] px-2.5 py-1 text-[10px] font-bold text-[#5B7BFE]">
                      {managementGroups.reduce((sum, group) => sum + group.people.length, 0)} 人
                    </span>
                  </div>
                  <div className="flex flex-col gap-2.5">
                    {managementGroups.map((group) => (
                      <div key={group.key} className="rounded-xl border border-gray-100 bg-gray-50/80 px-3 py-2.5">
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-[11px] font-bold text-gray-600">{group.label}</span>
                          <span
                            className="h-2 w-2 rounded-full"
                            style={{ backgroundColor: group.color }}
                          />
                        </div>
                        {group.people.length > 0 ? (
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {group.people.map((person) => (
                              <span
                                key={`${group.key}-${person.id}`}
                                className="inline-flex max-w-[200px] flex-col rounded-lg border border-white bg-white px-2.5 py-1.5 shadow-sm"
                              >
                                <span className="truncate text-[11px] font-bold text-gray-800">{person.name}</span>
                                <span className="truncate text-[10px] text-gray-400">{person.detail}</span>
                              </span>
                            ))}
                          </div>
                        ) : (
                          <p className="mt-1.5 text-[11px] text-gray-400">{group.emptyText}</p>
                        )}
                      </div>
                    ))}
                  </div>
                </div>

		                <div className="relative flex flex-col gap-6">
		                  {tree.children.map((department, index) => {
	                    const isEditingDepartmentName = editingNodeId === department.id && editingField === 'name';
	                    const departmentSettings = value.departments.find((item) => item.id === department.id);
	                    const departmentBindings = bindingsByDepartmentId.get(department.id) || [];
	                    const pendingDepartmentIntroDocument = pendingDepartmentIntroDocuments[department.id] || null;
	                    const departmentLeaderNameValue = departmentLeaderNameInputs[department.id] ?? department.leaderName ?? '';
	                    const hasDepartmentLeaderDraft = Object.prototype.hasOwnProperty.call(departmentLeaderNameInputs, department.id);
	                    const departmentForCount = departmentSettings && hasDepartmentLeaderDraft
	                      ? { ...departmentSettings, leaderUserId: null, leaderName: departmentLeaderNameValue }
	                      : departmentSettings;
	                    const memberCount = departmentForCount
	                      ? countDepartmentMembers(departmentForCount, departmentBindings)
	                      : departmentBindings.length;
	                    const departmentCardDirty = (
	                      isEditingDepartmentName
	                      && editingText.trim().length > 0
	                      && editingText.trim() !== department.name
	                    ) || (
	                      hasDepartmentLeaderDraft
	                      && visibleLeaderNameInput(departmentLeaderNameValue) !== visibleLeaderNameInput(department.leaderName)
	                    ) || introDocumentChanged(pendingDepartmentIntroDocument, departmentSettings?.introDocument);
	                    const inviteCode = buildDepartmentInviteCode(department.id, {
	                      organizationId: value.organization.organizationId,
		                      organizationName,
		                      departmentName: department.name,
		                      order: index,
	                    });
                    const parentDepartmentName = department.parentDepartmentId ? departmentNameById.get(department.parentDepartmentId) : null;
	                    return (
	                      <div key={department.id} className="flex items-center gap-10">
                        <div
                          id={`node-${department.id}`}
                          className="group relative z-10 flex min-w-[170px] flex-col gap-1.5 rounded-2xl border border-gray-200 bg-white px-4 py-3 pr-10 shadow-sm transition hover:border-[#5B7BFE]/40"
                          style={{ boxShadow: `0 12px 28px ${tint(department.color, '16')}` }}
                        >
                          {canModify ? (
                            <CardSaveButton
                              active={departmentCardDirty}
                              disabled={isSaving}
                              label={`保存${department.name}卡片`}
                              onClick={() => handleSaveDepartmentCard(department.id)}
                            />
                          ) : null}
                          {canModify ? (
                            <button
                              type="button"
                              onClick={() => handleDeleteDepartment(department.id)}
                              className="absolute -right-2 -top-2 rounded-full border border-rose-200 bg-white p-0.5 text-rose-400 shadow-sm transition hover:border-rose-300 hover:bg-rose-50 hover:text-rose-500"
                              title="删除该部门"
                            >
                              <X size={12} />
                            </button>
                          ) : null}

                          <div className="flex items-center gap-2">
                            <Users className="text-gray-400" size={14} />
                            {isEditingDepartmentName ? (
                              <input
                                autoFocus
                                value={editingText}
                                onCompositionStart={handleTextCompositionStart}
                                onCompositionEnd={handleTextCompositionEnd}
                                onChange={(event) => setEditingTextDraft(event.target.value)}
                                onBlur={handleSaveEdit}
                                onKeyDown={handleKeyDown}
                                className="w-full border-b border-[#5B7BFE] bg-transparent text-[13px] font-bold text-gray-800 outline-none"
                              />
                            ) : (
                              <button
                                type="button"
                                onClick={() => startEditing(department.id, 'name', department.name)}
                                className="text-left text-[13px] font-bold text-gray-800 transition hover:text-[#5B7BFE]"
                              >
                                {department.name}
                              </button>
                            )}
                          </div>

	                          <div className="flex items-center gap-1.5 pl-6">
	                            <span className="text-[11px] text-gray-400">负责人</span>
                            {canModify ? (
                              <LeaderPicker
                                value={{
                                  userId: department.leaderUserId ?? null,
                                  displayName: visibleLeaderNameInput(department.leaderName),
                                }}
                                employees={employees}
                                onSelect={(employee) => handleSelectDepartmentLeader(department.id, employee)}
                                placeholder="选负责人"
                                compact
                              />
                            ) : (
	                              <span className="text-[11px] text-gray-500">{department.leadName || '待设置'}</span>
	                            )}
	                          </div>

                          <div className="flex items-center gap-1.5 pl-6">
                            <span className="text-[11px] text-gray-400">上级</span>
                            {canModify ? (
                              <select
                                value={department.parentDepartmentId || ''}
                                onChange={(event) => handleUpdateDepartmentParent(department.id, event.target.value || null)}
                                className="max-w-[118px] rounded-lg border border-gray-100 bg-gray-50 px-2 py-1 text-[11px] font-medium text-gray-600 outline-none focus:border-[#5B7BFE]"
                              >
                                <option value="">组织直属</option>
                                {activeDepartments
                                  .filter((item) => item.id !== department.id)
                                  .map((item) => (
                                    <option key={item.id} value={item.id}>{item.name || '未命名部门'}</option>
                                  ))}
                              </select>
                            ) : (
                              <span className="text-[11px] text-gray-500">{parentDepartmentName || '组织直属'}</span>
                            )}
                          </div>

                          <div className="mt-2 flex items-center gap-2 pl-6">
                            <span
                              className="rounded-full px-2.5 py-1 text-[10px] font-bold"
                              style={{ backgroundColor: tint(department.color), color: department.color }}
                            >
                              邀请码 {inviteCode}
                            </span>
                            <span className="rounded-full bg-gray-100 px-2.5 py-1 text-[10px] font-bold text-gray-500">
                              {memberCount} 人
                            </span>
                          </div>
                          <IntroDocumentAction
                            canEdit={canModify}
                            compact
                            disabled={isSaving || !onUploadIntroDocument}
                            document={departmentSettings?.introDocument}
                            label="部门介绍"
                            onUpload={() => void handleUploadDepartmentIntroDocument(department)}
                            pendingDocument={pendingDepartmentIntroDocument}
                          />
                        </div>

                        <div className="relative flex flex-col gap-3">
                          {department.children.map((role) => {
                            const isEditingRoleName = editingNodeId === role.id && editingField === 'name';
                            const roleCardDirty = isEditingRoleName
                              && editingText.trim().length > 0
                              && editingText.trim() !== role.name;
                            // 顾源源 5/24 M4: 持岗人解析 — 机器人优先, 否则反查 bindings
                            const roleSettings = value.roles.find((r) => r.id === role.id);
                            const holderBotId = roleSettings?.holderBotId ?? null;
                            const holderBot = holderBotId ? botMemberById.get(holderBotId) ?? null : null;
                            const holderBinding = holderBot
                              ? null
                              : value.bindings.find((b) => b.primaryRoleId === role.id);
                            const holderEmployee = holderBinding
                              ? employees.find((e) => e.id === holderBinding.userId) ?? null
                              : null;
                            const deptBots = botMembersByDepartmentId.get(department.id) || [];
                            return (
                              <div
                                id={`node-${role.id}`}
                                key={role.id}
                                className="group relative z-10 min-w-[180px] rounded-xl border border-gray-100 bg-gray-50/90 px-3 py-2 pr-8 transition hover:border-gray-200"
                              >
                                {canModify ? (
                                  <CardSaveButton
                                    active={roleCardDirty}
                                    className="right-1.5 top-1.5 h-5 w-5"
                                    disabled={isSaving}
                                    iconSize={10}
                                    label={`保存${role.name}卡片`}
                                    onClick={() => handleSaveRoleCard(role.id)}
                                  />
                                ) : null}
                                {canModify ? (
                                  <button
                                    type="button"
                                    onClick={() => handleDeleteRole(role.id)}
                                    className="absolute -right-1.5 -top-1.5 rounded-full border border-rose-200 bg-white p-0.5 text-rose-400 shadow-sm transition hover:border-rose-300 hover:bg-rose-50 hover:text-rose-500"
                                    title="删除该岗位"
                                  >
                                    <X size={10} />
                                  </button>
                                ) : null}

                                {/* 岗位名（可编辑） */}
                                {isEditingRoleName ? (
                                  <input
                                    autoFocus
                                    value={editingText}
                                    onCompositionStart={handleTextCompositionStart}
                                    onCompositionEnd={handleTextCompositionEnd}
                                    onChange={(event) => setEditingTextDraft(event.target.value)}
                                    onBlur={handleSaveEdit}
                                    onKeyDown={handleKeyDown}
                                    className="w-full border-b border-gray-300 bg-transparent text-center text-[12px] font-medium text-gray-700 outline-none focus:border-[#5B7BFE]"
                                  />
                                ) : (
                                  <button
                                    type="button"
                                    onClick={() => canModify && startEditing(role.id, 'name', role.name)}
                                    disabled={!canModify}
                                    className="w-full text-center text-[12px] font-medium text-gray-600 transition hover:text-[#5B7BFE] disabled:cursor-default disabled:hover:text-gray-600"
                                  >
                                    {role.name}
                                  </button>
                                )}

                                {/* 持岗人（下拉选员工 + 机器人同事） */}
                                <div className="mt-1.5 flex flex-col items-center gap-1">
                                  {canModify ? (
                                    <LeaderPicker
                                      value={{
                                        userId: holderBot
                                          ? holderBot.id
                                          : holderEmployee?.id ?? null,
                                        displayName: holderBot
                                          ? holderBot.display_name
                                          : holderEmployee?.fullName ?? '',
                                        isBot: !!holderBot,
                                      }}
                                      employees={employees}
                                      botMembers={deptBots}
                                      onSelect={(employee) => handleSelectRoleHolder(role.id, employee)}
                                      onSelectBot={(bot) => handleSelectRoleHolderBot(role.id, bot)}
                                      placeholder="选员工"
                                      compact
                                      onAddBotMember={() => setBotDialogDept({ id: department.id, name: department.name })}
                                    />
                                  ) : holderBot ? (
                                    <span className="inline-flex items-center gap-1 text-[11px] font-medium text-[#4A63CF]">
                                      <span className="inline-flex shrink-0 items-center rounded-full bg-[#5B7BFE] px-1.5 py-[1px] text-[9px] font-bold tracking-wide text-white">
                                        AI
                                      </span>
                                      {holderBot.display_name}
                                    </span>
                                  ) : (
                                    <span className="text-[11px] text-gray-500">
                                      {holderEmployee?.fullName || '待指派'}
                                    </span>
                                  )}
                                  {/* M6.1: 机器人持岗人 hover 3 按钮 — 顺序: 复制密钥 / 编辑 / 解除指派.
                                      复制密钥 = 蓝色主行动 (#5B7BFE), 物理上是"重置 + 自动复制"(db 只存 hash, 读不到旧 plain).
                                      编辑    = 中性灰. 重置密钥按钮已迁入编辑弹窗里的"密钥管理" section.
                                      解除指派 = 灰边 hover 红. */}
                                  {canModify && holderBot ? (
                                    <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                                      <button
                                        type="button"
                                        onClick={() => setBotCopyConfirm(holderBot)}
                                        className="rounded-full bg-[#5B7BFE] px-2 py-0.5 text-[10px] font-semibold text-white shadow-[0_2px_6px_rgba(91,123,254,0.25)] transition hover:bg-[#4A63CF]"
                                        title={`复制 ${holderBot.display_name} 的启动密钥 (会重置并自动复制新密钥)`}
                                      >
                                        复制密钥
                                      </button>
                                      <button
                                        type="button"
                                        onClick={() => setBotEditDialog(holderBot)}
                                        className="rounded-full px-1.5 py-0.5 text-[10px] text-gray-500 transition hover:bg-gray-100 hover:text-gray-700"
                                        title={`编辑 ${holderBot.display_name}`}
                                      >
                                        编辑
                                      </button>
                                      <button
                                        type="button"
                                        onClick={() => handleSelectRoleHolderBot(role.id, null)}
                                        className="rounded-full border border-gray-200 px-1.5 py-0.5 text-[10px] text-gray-500 transition hover:border-rose-200 hover:bg-rose-50 hover:text-rose-500"
                                        title="解除该岗位的机器人持岗人"
                                      >
                                        解除指派
                                      </button>
                                    </div>
                                  ) : null}
                                </div>
                              </div>
                            );
                          })}

                          {canModify ? (
                            <button
                              id={`add-btn-${department.id}`}
                              type="button"
                              onClick={() => handleAddRole(department.id)}
                              className="z-10 inline-flex min-w-[120px] items-center justify-center gap-1 rounded-xl border border-dashed border-gray-200 bg-white/70 px-3 py-2 text-[12px] text-gray-400 transition hover:border-[#5B7BFE]/40 hover:bg-[#5B7BFE]/5"
                            >
                              <Plus size={12} />
                              添加岗位
                            </button>
                          ) : null}
                        </div>
                      </div>
                    );
                  })}

	                  {canModify ? (
                    <div className="flex flex-col gap-2">
                      <button
                        id={`add-btn-${tree.id}`}
                        type="button"
                        onClick={() => handleAddDepartment()}
                        className="z-10 inline-flex min-w-[150px] items-center justify-center gap-1.5 rounded-xl border border-dashed border-gray-300 bg-white/70 px-4 py-3 text-[13px] font-medium text-gray-400 transition hover:border-[#5B7BFE]/60 hover:bg-[#5B7BFE]/5"
                      >
                        <Plus size={14} />
                        添加部门
                      </button>
                      <button
                        type="button"
                        onClick={handleAddManagementDepartment}
                        className="z-10 inline-flex min-w-[150px] items-center justify-center gap-1.5 rounded-xl border border-dashed border-indigo-200 bg-indigo-50/60 px-4 py-3 text-[13px] font-medium text-indigo-500 transition hover:border-indigo-300 hover:bg-indigo-50"
                      >
                        <Plus size={14} />
                        添加管理层
                      </button>
                    </div>
	                  ) : null}
                </div>
              </div>
            </div>
              ) : null}
            </>
          ) : (
            <div className="p-8">
              <div className="mx-auto grid max-w-5xl grid-cols-1 gap-5 md:grid-cols-2">
                {managementInviteCards.map((item) => (
                  <InviteCard
                    key={item.key}
                    departmentName={item.label}
                    leadName="管理层身份"
                    inviteCode={item.inviteCode}
                    positions={item.helper}
                    joinedCount={item.joinedCount}
                    color={item.color}
                  />
                ))}
                {tree.children.map((department, index) => {
                  const inviteCode = buildDepartmentInviteCode(department.id, {
                    organizationId: value.organization.organizationId,
                    organizationName,
                    departmentName: department.name,
                    order: index,
                  });
                  const departmentSettings = value.departments.find((item) => item.id === department.id);
                  const departmentBindings = bindingsByDepartmentId.get(department.id) || [];
                  const joinedCount = departmentSettings
                    ? countDepartmentMembers(departmentSettings, departmentBindings)
                    : departmentBindings.length;
                  return (
                    <InviteCard
                      key={department.id}
                      departmentName={department.name}
                      leadName={department.leadName}
                      inviteCode={inviteCode}
                      positions="部门成员池 · 默认个人可见范围；负责人由管理层或管理员在组织架构中指定"
                      joinedCount={joinedCount}
                      color={department.color}
                    />
                  );
                })}
              </div>
            </div>
          )}
        </div>

      </div>

      {/* 顾源源 5/24: 添加机器人同事弹窗 (从每个部门"选员工"下拉里点 → 入口触发) */}
      {botDialogDept ? (
        <BotMemberFormDialog
          mode="create"
          defaultDepartmentId={botDialogDept.id}
          defaultDepartmentName={botDialogDept.name}
          departments={value.departments
            .filter((d) => d.active !== false)
            .map((d) => ({ id: d.id, name: d.name, color: d.color }))}
          currentUserId={currentUserId}
          currentUserName={currentUserName}
          onClose={() => setBotDialogDept(null)}
          onCreated={async () => {
            const justAddedDeptName = botDialogDept.name;
            setBotDialogDept(null);
            await reloadBotMembers();
            showToast(`已为「${justAddedDeptName}」添加机器人同事`);
          }}
        />
      ) : null}
      {/* M5: 编辑机器人同事弹窗 (mode=edit) */}
      {botEditDialog ? (
        <BotMemberFormDialog
          mode="edit"
          existingBot={botEditDialog}
          departments={value.departments
            .filter((d) => d.active !== false)
            .map((d) => ({ id: d.id, name: d.name, color: d.color }))}
          currentUserId={currentUserId}
          currentUserName={currentUserName}
          onClose={() => setBotEditDialog(null)}
          onCreated={async () => {
            const name = botEditDialog.display_name;
            setBotEditDialog(null);
            await reloadBotMembers();
            showToast(`已更新「${name}」`);
          }}
        />
      ) : null}
      {/* M5/M6.1: 重置启动密钥弹窗 (独立组件, 必须复制后才能关).
          M6.1 之后这个弹窗有两个入口:
            (a) 编辑弹窗里的"重置密钥"按钮 → autoStart=true, autoCopy=false (用户主动重置, 由编辑里触发, 手动点复制)
            (b) 岗位卡"复制密钥" confirm modal 确认 → autoStart=true, autoCopy=true (流程零步, 直接送剪贴板)
          状态形态用 union 区分两种入口, 复用同一个 dialog 组件. */}
      {botRotateDialog ? (
        <BotRotateTokenDialog
          bot={botRotateDialog.bot}
          autoStart
          autoCopy={botRotateDialog.autoCopy}
          onClose={() => setBotRotateDialog(null)}
          onRotated={async () => {
            const name = botRotateDialog.bot.display_name;
            const wasAutoCopy = botRotateDialog.autoCopy;
            setBotRotateDialog(null);
            await reloadBotMembers();
            showToast(
              wasAutoCopy
                ? `已重置「${name}」启动密钥并自动复制到剪贴板`
                : `「${name}」的启动密钥已重置`,
            );
          }}
        />
      ) : null}
      {/* M6.1: 岗位卡"复制密钥" inline 轻确认 modal —
          db 只存 hash, 所以这里必须告诉用户"复制 = 重置 + 复制新", 旧密钥立即作废.
          用户点 [重置并复制] 后, 把这个 confirm 关掉, 同时打开 BotRotateTokenDialog (autoStart+autoCopy). */}
      {botCopyConfirm ? (
        <div className="fixed inset-0 z-[125] flex items-center justify-center bg-gray-900/30 p-4 backdrop-blur-sm">
          <div className="w-full max-w-[480px] rounded-3xl bg-white shadow-2xl ring-1 ring-black/5">
            <div className="border-b border-gray-100 px-8 py-6">
              <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">
                COPY TOKEN · 复制启动密钥
              </p>
              <h3 className="mt-2 text-[18px] font-bold text-gray-900">
                复制「{botCopyConfirm.display_name}」的启动密钥
              </h3>
              <p className="mt-2 text-[12px] leading-relaxed text-gray-500">
                启动密钥设计为<span className="font-medium text-gray-700">不可重复读取</span>,
                数据库里只存哈希。点击 <span className="font-medium text-[#4A63CF]">重置并复制</span>{' '}
                会立即生成一把新密钥并自动送进剪贴板,
                <span className="font-medium text-rose-600">旧密钥立刻作废</span>,
                当前正在用旧密钥的 Codex / Claude 需要重新粘贴。
              </p>
            </div>
            <div className="flex items-center justify-end gap-3 border-t border-gray-100 bg-gray-50/60 px-8 py-4">
              <button
                type="button"
                onClick={() => setBotCopyConfirm(null)}
                className="inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white px-5 py-2.5 text-[13px] font-bold text-gray-600 transition hover:border-gray-300 hover:bg-gray-50"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => {
                  const bot = botCopyConfirm;
                  setBotCopyConfirm(null);
                  setBotRotateDialog({ bot, autoCopy: true });
                }}
                className="inline-flex items-center gap-2 rounded-full bg-[#5B7BFE] px-6 py-2.5 text-[13px] font-bold text-white shadow-[0_12px_30px_rgba(91,123,254,0.25)] transition hover:bg-[#4A63CF]"
              >
                重置并复制
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

type InviteCardProps = {
  departmentName: string;
  leadName: string;
  inviteCode: string;
  positions: string;
  joinedCount: number;
  color: string;
};

type CardSaveButtonProps = {
  active: boolean;
  disabled?: boolean;
  iconSize?: number;
  label: string;
  onClick: () => void;
  className?: string;
};

type IntroDocumentActionProps = {
  canEdit: boolean;
  compact?: boolean;
  disabled?: boolean;
  document?: OrgIntroDocumentSettings | null;
  label: string;
  onUpload: () => void;
  pendingDocument?: OrgIntroDocumentSettings | null;
};

function IntroDocumentAction({
  canEdit,
  compact = false,
  disabled = false,
  document,
  label,
  onUpload,
  pendingDocument,
}: IntroDocumentActionProps) {
  const activeDocument = pendingDocument || document || null;
  const hasPendingDocument = Boolean(pendingDocument);
  const fileName = activeDocument?.fileName?.trim() || '';
  const buttonText = fileName ? '替换' : '上传';
  return (
    <div className={`flex min-w-0 items-center gap-2 ${compact ? 'pl-6 pt-1' : 'pl-8'}`}>
      <span className="inline-flex shrink-0 items-center gap-1 text-[10px] font-bold text-gray-400">
        <FileText size={compact ? 11 : 12} />
        {label}
      </span>
      {fileName ? (
        <span
          title={fileName}
          className={`min-w-0 truncate rounded-full px-2 py-1 text-[10px] font-bold ${
            hasPendingDocument ? 'bg-blue-50 text-[#4A63CF]' : 'bg-gray-100 text-gray-500'
          }`}
        >
          {hasPendingDocument ? '待保存 · ' : ''}{fileName}
        </span>
      ) : (
        <span className="text-[10px] font-medium text-gray-300">未上传</span>
      )}
      {canEdit ? (
        <button
          type="button"
          disabled={disabled}
          onClick={onUpload}
          className="inline-flex shrink-0 items-center gap-1 rounded-full border border-[#DCE4FF] bg-white px-2 py-1 text-[10px] font-bold text-[#5B7BFE] transition hover:border-[#5B7BFE]/60 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <UploadCloud size={11} />
          {buttonText}
        </button>
      ) : null}
    </div>
  );
}

function CardSaveButton({
  active,
  disabled = false,
  iconSize = 13,
  label,
  onClick,
  className = 'right-3 top-3 h-7 w-7',
}: CardSaveButtonProps) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onMouseDown={(event) => event.preventDefault()}
      onClick={onClick}
      className={`absolute inline-flex items-center justify-center rounded-full border shadow-sm transition disabled:cursor-not-allowed disabled:opacity-50 ${
        active
          ? 'border-[#5B7BFE] bg-[#5B7BFE] text-white shadow-[0_8px_18px_rgba(91,123,254,0.24)]'
          : 'border-[#DCE4FF] bg-white text-[#5B7BFE] opacity-75 hover:border-[#5B7BFE]/60 hover:opacity-100'
      } ${className}`}
    >
      <Check size={iconSize} strokeWidth={2.6} />
    </button>
  );
}

function InviteCard({
  departmentName,
  leadName,
  inviteCode,
  positions,
  joinedCount,
  color,
}: InviteCardProps) {
  return (
    <div className="flex h-full flex-col rounded-2xl border border-gray-100 bg-white p-6 shadow-sm transition hover:shadow-md">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h3 className="text-[17px] font-bold text-gray-900">{departmentName}</h3>
          <p className="mt-1 text-[12px] text-gray-500">{leadName || '待设置负责人'}</p>
        </div>
        <div className="rounded-full bg-gray-100 px-3 py-1 text-[11px] font-bold text-gray-500">
          已加入 {joinedCount} 人
        </div>
      </div>

      <div className="mt-auto rounded-xl border px-4 py-4" style={{ backgroundColor: tint(color, '08'), borderColor: tint(color, '18') }}>
        <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.18em]" style={{ color }}>
          邀请码
        </div>
        <div className="text-[22px] font-bold tracking-[0.16em]" style={{ color }}>
          {inviteCode}
        </div>
      </div>

      <div className="mt-4 border-t border-gray-100 pt-4 text-[12px] text-gray-500">
        {positions}
      </div>
    </div>
  );
}


type LeaderPickerProps = {
  value: { userId: string | null; displayName: string; isBot?: boolean };
  employees: EmployeeRecord[];
  onSelect: (employee: EmployeeRecord | null) => void;
  placeholder?: string;
  compact?: boolean;
  disabled?: boolean;
  /** 顾源源 5/24: 当下拉允许添加机器人同事时, 提供该回调 → 底部出现 "添加机器人同事" 行. */
  onAddBotMember?: () => void;
  /** 顾源源 5/24 M2: 机器人同事候选列表 (调用方按部门过滤后传入). 非空时下拉员工区下方加 "机器人同事" 区. */
  botMembers?: BotMemberRecord[];
  /** M2: 选机器人同事的回调; 跟 onSelect(employee) 互斥 */
  onSelectBot?: (bot: BotMemberRecord) => void;
};

function LeaderPicker({
  value,
  employees,
  onSelect,
  placeholder = '选择负责人',
  compact = false,
  disabled = false,
  onAddBotMember,
  botMembers,
  onSelectBot,
}: LeaderPickerProps) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const buttonRef = useRef<HTMLButtonElement>(null);
  // dropdown 位置 — 通过 Portal 渲染到 body 后，需用 fixed 定位
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);

  const candidates = useMemo(
    () =>
      employees.filter(isActiveOrganizationEmployee),
    [employees],
  );

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return candidates;
    return candidates.filter(
      (e) =>
        e.fullName.toLowerCase().includes(needle)
        || (e.email || '').toLowerCase().includes(needle),
    );
  }, [candidates, search]);

  // 计算 dropdown 的 fixed 位置（按钮下方 4px，右边贴齐 viewport）
  const computePosition = useCallback(() => {
    if (!buttonRef.current) return null;
    const rect = buttonRef.current.getBoundingClientRect();
    const dropdownWidth = 260;
    const gap = 6;
    // 默认贴按钮左侧；若右溢出，右对齐
    let left = rect.left;
    if (left + dropdownWidth > window.innerWidth - 12) {
      left = Math.max(12, window.innerWidth - dropdownWidth - 12);
    }
    return { top: rect.bottom + gap, left };
  }, []);

  useLayoutEffect(() => {
    if (!open) {
      setPosition(null);
      return;
    }
    setPosition(computePosition());
  }, [open, computePosition]);

  // 监听 scroll / resize 重新算位置（dropdown 跟随按钮）
  useEffect(() => {
    if (!open) return;
    const recalc = () => setPosition(computePosition());
    window.addEventListener('scroll', recalc, true);
    window.addEventListener('resize', recalc);
    return () => {
      window.removeEventListener('scroll', recalc, true);
      window.removeEventListener('resize', recalc);
    };
  }, [open, computePosition]);

  // ESC 关闭
  useEffect(() => {
    if (!open) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
        setSearch('');
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open]);

  const close = useCallback(() => {
    setOpen(false);
    setSearch('');
  }, []);

  const handleSelect = useCallback(
    (employee: EmployeeRecord | null) => {
      onSelect(employee);
      close();
    },
    [close, onSelect],
  );

  // M2: 机器人同事点击 → 走 onSelectBot 通道
  const handleSelectBot = useCallback(
    (bot: BotMemberRecord) => {
      if (onSelectBot) onSelectBot(bot);
      close();
    },
    [close, onSelectBot],
  );

  // 机器人同事按搜索词过滤
  const filteredBots = useMemo(() => {
    if (!botMembers || botMembers.length === 0) return [];
    const needle = search.trim().toLowerCase();
    if (!needle) return botMembers;
    return botMembers.filter(
      (b) =>
        b.display_name.toLowerCase().includes(needle)
        || (b.handle || '').toLowerCase().includes(needle),
    );
  }, [botMembers, search]);

  const displayLabel = value.displayName?.trim() || '';
  const displayIsBot = !!value.isBot;

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        disabled={disabled}
        onClick={() => setOpen((prev) => !prev)}
        className={
          compact
            ? 'inline-flex min-w-[100px] items-center gap-1 rounded-full border border-gray-200 bg-white px-2.5 py-1 text-[11px] font-medium text-gray-600 outline-none transition hover:border-[#5B7BFE]/50 disabled:cursor-not-allowed disabled:opacity-50'
            : 'inline-flex min-w-[160px] items-center gap-1 rounded-full border border-[#DCE4FF] bg-white px-3 py-1.5 text-[11px] font-medium text-gray-700 outline-none transition hover:border-[#5B7BFE] disabled:cursor-not-allowed disabled:opacity-50'
        }
      >
        {displayIsBot && displayLabel ? (
          <span className="inline-flex shrink-0 items-center rounded-full bg-[#5B7BFE] px-1.5 py-[1px] text-[9px] font-bold tracking-wide text-white">
            AI
          </span>
        ) : null}
        <span
          className={
            displayLabel
              ? displayIsBot
                ? 'text-[#4A63CF]'
                : ''
              : 'text-gray-300'
          }
        >
          {displayLabel || placeholder}
        </span>
        <ChevronDown size={10} className="ml-auto opacity-60" />
      </button>
      {open && position
        ? createPortal(
            <>
              {/* 毛玻璃 backdrop — 覆盖整个屏幕，点击即关闭 */}
              <div
                className="fixed inset-0 z-[100] bg-gray-900/15 backdrop-blur-[3px] transition-opacity"
                onClick={close}
                aria-hidden
              />
              {/* dropdown 面板 — 在 backdrop 之上 */}
              <div
                role="dialog"
                style={{ top: position.top, left: position.left }}
                className="fixed z-[101] w-[260px] rounded-2xl border border-gray-100 bg-white p-2 shadow-2xl ring-1 ring-black/5"
              >
                <input
                  type="text"
                  autoFocus
                  placeholder="搜索姓名或邮箱..."
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  className="mb-1.5 w-full rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1.5 text-[12px] outline-none transition focus:border-[#5B7BFE] focus:bg-white"
                />
                <div className="max-h-[260px] overflow-y-auto">
                  <button
                    type="button"
                    onClick={() => handleSelect(null)}
                    className="w-full rounded-lg px-2 py-1.5 text-left text-[11px] font-medium text-gray-400 transition hover:bg-gray-50"
                  >
                    清空（不指定负责人）
                  </button>
                  {filtered.length === 0 ? (
                    <p className="px-2 py-2 text-[10px] text-gray-400">
                      无匹配同事 — 请先让 ta 在系统注册
                    </p>
                  ) : (
                    filtered.map((e) => (
                      <button
                        key={e.id}
                        type="button"
                        onClick={() => handleSelect(e)}
                        className={`w-full rounded-lg px-2 py-1.5 text-left text-[11px] transition hover:bg-[#EEF3FF] ${
                          !displayIsBot && e.id === value.userId ? 'bg-[#EEF3FF] font-bold text-[#4A63CF]' : 'text-gray-700'
                        }`}
                      >
                        <div className="flex items-center gap-1.5">
                          <span>{e.fullName}</span>
                          {e.primaryRole === 'admin' ? (
                            <span className="rounded-full bg-[#FFF7E0] px-1.5 py-0.5 text-[9px] font-bold text-[#A87A1F]">
                              admin
                            </span>
                          ) : null}
                        </div>
                        <div className="text-[10px] text-gray-400">{memberIdentityLabel(e)} · {e.email}</div>
                      </button>
                    ))
                  )}
                  {/* 顾源源 5/24 M2: 机器人同事区 — 员工列表下方独立分组, uppercase 小标题, 每项前 [AI] 角标. */}
                  {botMembers && botMembers.length > 0 && onSelectBot ? (
                    <>
                      <div className="my-1 border-t border-gray-100" />
                      <p className="px-2 pb-1 pt-0.5 text-[9px] font-bold uppercase tracking-[0.18em] text-gray-400">
                        机器人同事 · BOT MEMBERS
                      </p>
                      {filteredBots.length === 0 ? (
                        <p className="px-2 py-1.5 text-[10px] text-gray-400">
                          无匹配的机器人同事
                        </p>
                      ) : (
                        filteredBots.map((bot) => (
                          <button
                            key={bot.id}
                            type="button"
                            onClick={() => handleSelectBot(bot)}
                            className={`w-full rounded-lg px-2 py-1.5 text-left text-[11px] transition hover:bg-[#EEF3FF] ${
                              displayIsBot && bot.id === value.userId
                                ? 'bg-[#EEF3FF] font-bold text-[#4A63CF]'
                                : 'text-gray-700'
                            }`}
                          >
                            <div className="flex items-center gap-1.5">
                              <span className="inline-flex shrink-0 items-center rounded-full bg-[#5B7BFE] px-1.5 py-[1px] text-[9px] font-bold tracking-wide text-white">
                                AI
                              </span>
                              <span>{bot.display_name}</span>
                            </div>
                            {bot.description ? (
                              <div className="ml-7 text-[10px] text-gray-400 truncate">{bot.description}</div>
                            ) : null}
                          </button>
                        ))
                      )}
                    </>
                  ) : null}
                  {/* 顾源源 5/24: 下拉底部加 "添加机器人同事" 行 — 跟人类同事并列在同一序列里.
                       样式跟"清空"完全一致(灰色+左对齐+小字), 不用 emoji, 不堆视觉. */}
                  {onAddBotMember ? (
                    <>
                      <div className="my-1 border-t border-gray-100" />
                      <button
                        type="button"
                        onClick={() => {
                          close();
                          onAddBotMember();
                        }}
                        className="w-full rounded-lg px-2 py-1.5 text-left text-[11px] font-medium text-[#4A63CF] transition hover:bg-[#EEF3FF]"
                      >
                        添加机器人同事
                        <span className="ml-1 text-[10px] font-normal text-gray-400">
                          创建一个 AI 同事并指派到此岗位
                        </span>
                      </button>
                    </>
                  ) : null}
                </div>
              </div>
            </>,
            document.body,
          )
        : null}
    </>
  );
}
