import test from 'node:test';
import assert from 'node:assert/strict';

import {
  resolveTaskCreationDefaults,
  resolveTaskPlanDepartmentIdFromScopeValue,
  resolveTaskPlanScopeSelectValue,
  resolveTaskProjectAutoSelection,
  taskPlanMatchesDepartmentScope,
  TASK_PLAN_ORGANIZATION_SCOPE_VALUE,
} from './taskCreationDefaults.js';

test('current organization membership department and internal project become task defaults', () => {
  assert.deepEqual(resolveTaskCreationDefaults({
    hasOrganization: true,
    organizationName: '星丛',
    organizationClientId: 'project-xingcong',
    membershipDepartmentId: 'department-product',
    sessionDepartmentId: 'department-stale',
  }), {
    clientId: 'project-xingcong',
    clientConfidence: 'high',
    clientReason: '默认归入当前组织“星丛”，可切换到本组织内其他项目。',
    departmentId: 'department-product',
  });
});

test('CEO or adviser without a department remains organization-level', () => {
  const defaults = resolveTaskCreationDefaults({
    hasOrganization: true,
    organizationName: '星丛',
    organizationClientId: 'project-xingcong',
    membershipDepartmentId: null,
    sessionDepartmentId: 'department-from-an-older-session',
  });

  assert.equal(defaults.clientId, 'project-xingcong');
  assert.equal(defaults.departmentId, '');
});

test('organization-level task scope round-trips through the select without inventing a department', () => {
  assert.equal(resolveTaskPlanScopeSelectValue(null), TASK_PLAN_ORGANIZATION_SCOPE_VALUE);
  assert.equal(
    resolveTaskPlanDepartmentIdFromScopeValue(TASK_PLAN_ORGANIZATION_SCOPE_VALUE),
    '',
  );
  assert.equal(resolveTaskPlanScopeSelectValue('department-product'), 'department-product');
  assert.equal(resolveTaskPlanDepartmentIdFromScopeValue('department-product'), 'department-product');
});

test('organization plans with a null department match the organization-level task scope', () => {
  assert.equal(taskPlanMatchesDepartmentScope(null, ''), true);
  assert.equal(taskPlanMatchesDepartmentScope(undefined, ''), true);
  assert.equal(taskPlanMatchesDepartmentScope('department-product', ''), false);
  assert.equal(taskPlanMatchesDepartmentScope('department-product', 'department-product'), true);
});

test('session department is used only before the current organization membership snapshot is ready', () => {
  const defaults = resolveTaskCreationDefaults({
    hasOrganization: false,
    organizationName: null,
    organizationClientId: '',
    membershipDepartmentId: null,
    sessionDepartmentId: 'department-session',
  });

  assert.equal(defaults.departmentId, 'department-session');
  assert.equal(defaults.clientId, '');
  assert.equal(defaults.clientConfidence, 'none');
  assert.equal(defaults.clientReason, '当前组织尚未设置默认内部项目，请先选择项目。');
});

test('typing a task without project keywords keeps the organization default project', () => {
  assert.deepEqual(resolveTaskProjectAutoSelection({
    currentClientId: 'project-xingcong',
    currentConfidence: 'high',
    currentClientReason: '默认归入当前组织“星丛”，可切换到本组织内其他项目。',
    clientTouched: false,
    organizationClientId: 'project-xingcong',
    organizationClientReason: '默认归入当前组织“星丛”，可切换到本组织内其他项目。',
    clearMatchClientId: null,
    clearMatchClientName: null,
    hasAmbiguousMatches: false,
  }), {
    clientId: 'project-xingcong',
    clientConfidence: 'high',
    clientReason: '默认归入当前组织“星丛”，可切换到本组织内其他项目。',
    source: 'organization_default',
  });
});

test('an explicit keyword match can replace the organization default project', () => {
  assert.deepEqual(resolveTaskProjectAutoSelection({
    currentClientId: 'project-xingcong',
    currentConfidence: 'high',
    currentClientReason: '默认归入当前组织“星丛”，可切换到本组织内其他项目。',
    clientTouched: false,
    organizationClientId: 'project-xingcong',
    organizationClientReason: '默认归入当前组织“星丛”，可切换到本组织内其他项目。',
    clearMatchClientId: 'project-library',
    clearMatchClientName: '知识收集',
    hasAmbiguousMatches: false,
  }), {
    clientId: 'project-library',
    clientConfidence: 'high',
    clientReason: '根据标题和说明推荐「知识收集」；保存前仍可手动修改。',
    source: 'keyword_match',
  });
});

test('removing a prior keyword match falls back to the organization default project', () => {
  const selection = resolveTaskProjectAutoSelection({
    currentClientId: 'project-library',
    currentConfidence: 'high',
    currentClientReason: '根据标题和说明推荐「知识收集」；保存前仍可手动修改。',
    clientTouched: false,
    organizationClientId: 'project-xingcong',
    organizationClientReason: '默认归入当前组织“星丛”，可切换到本组织内其他项目。',
    clearMatchClientId: null,
    clearMatchClientName: null,
    hasAmbiguousMatches: false,
  });

  assert.equal(selection.clientId, 'project-xingcong');
  assert.equal(selection.source, 'organization_default');
});

test('a manual project selection is never replaced by keyword automation', () => {
  const selection = resolveTaskProjectAutoSelection({
    currentClientId: 'project-manual',
    currentConfidence: 'manual',
    currentClientReason: '已手动选择项目。',
    clientTouched: true,
    organizationClientId: 'project-xingcong',
    organizationClientReason: '默认归入当前组织“星丛”，可切换到本组织内其他项目。',
    clearMatchClientId: 'project-library',
    clearMatchClientName: '知识收集',
    hasAmbiguousMatches: false,
  });

  assert.equal(selection.clientId, 'project-manual');
  assert.equal(selection.source, 'manual');
});
