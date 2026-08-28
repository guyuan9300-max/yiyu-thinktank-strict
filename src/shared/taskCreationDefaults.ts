export interface TaskCreationDefaultInput {
  hasOrganization: boolean;
  organizationName?: string | null;
  organizationClientId?: string | null;
  membershipDepartmentId?: string | null;
  sessionDepartmentId?: string | null;
}

export interface TaskCreationDefaults {
  clientId: string;
  clientConfidence: 'none' | 'high';
  clientReason: string;
  departmentId: string;
}

export type TaskClientConfidence = 'none' | 'low' | 'medium' | 'high' | 'manual';

export const TASK_PLAN_ORGANIZATION_SCOPE_VALUE = '__organization_plan_scope__';

export function normalizeTaskPlanDepartmentScope(departmentId?: string | null): string {
  return (departmentId || '').trim();
}

export function taskPlanMatchesDepartmentScope(
  planDepartmentId?: string | null,
  selectedDepartmentId?: string | null,
): boolean {
  return normalizeTaskPlanDepartmentScope(planDepartmentId)
    === normalizeTaskPlanDepartmentScope(selectedDepartmentId);
}

export function resolveTaskPlanScopeSelectValue(departmentId?: string | null): string {
  return normalizeTaskPlanDepartmentScope(departmentId) || TASK_PLAN_ORGANIZATION_SCOPE_VALUE;
}

export function resolveTaskPlanDepartmentIdFromScopeValue(scopeValue?: string | null): string {
  const normalizedValue = (scopeValue || '').trim();
  return normalizedValue === TASK_PLAN_ORGANIZATION_SCOPE_VALUE ? '' : normalizedValue;
}

export interface TaskProjectAutoSelectionInput {
  currentClientId: string;
  currentConfidence: TaskClientConfidence;
  currentClientReason: string;
  clientTouched: boolean;
  organizationClientId: string;
  organizationClientReason: string;
  clearMatchClientId?: string | null;
  clearMatchClientName?: string | null;
  hasAmbiguousMatches: boolean;
}

export interface TaskProjectAutoSelection {
  clientId: string;
  clientConfidence: TaskClientConfidence;
  clientReason: string;
  source: 'manual' | 'keyword_match' | 'organization_default' | 'unresolved';
}

export function resolveTaskCreationDefaults(input: TaskCreationDefaultInput): TaskCreationDefaults {
  const organizationName = (input.organizationName || '').trim() || '当前组织';
  const clientId = (input.organizationClientId || '').trim();
  const departmentId = input.hasOrganization
    ? (input.membershipDepartmentId || '').trim()
    : (input.sessionDepartmentId || '').trim();

  return {
    clientId,
    clientConfidence: clientId ? 'high' : 'none',
    clientReason: clientId
      ? `默认归入当前组织“${organizationName}”，可切换到本组织内其他项目。`
      : '当前组织尚未设置默认内部项目，请先选择项目。',
    departmentId,
  };
}

export function resolveTaskProjectAutoSelection(
  input: TaskProjectAutoSelectionInput,
): TaskProjectAutoSelection {
  if (input.clientTouched || input.currentConfidence === 'manual') {
    return {
      clientId: input.currentClientId,
      clientConfidence: input.currentConfidence,
      clientReason: input.currentClientReason,
      source: 'manual',
    };
  }

  const matchedClientId = (input.clearMatchClientId || '').trim();
  if (matchedClientId) {
    const matchedClientName = (input.clearMatchClientName || '').trim() || '匹配项目';
    return {
      clientId: matchedClientId,
      clientConfidence: 'high',
      clientReason: `根据标题和说明推荐「${matchedClientName}」；保存前仍可手动修改。`,
      source: 'keyword_match',
    };
  }

  const organizationClientId = input.organizationClientId.trim();
  if (organizationClientId) {
    return {
      clientId: organizationClientId,
      clientConfidence: 'high',
      clientReason: input.organizationClientReason,
      source: 'organization_default',
    };
  }

  return {
    clientId: '',
    clientConfidence: 'none',
    clientReason: input.hasAmbiguousMatches
      ? '识别到多个可能项目，已按匹配度调整候选顺序，请手动选择。'
      : input.organizationClientReason,
    source: 'unresolved',
  };
}
