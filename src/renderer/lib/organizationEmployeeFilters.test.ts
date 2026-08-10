import assert from 'node:assert/strict';
import test from 'node:test';

import {
  isActiveOrganizationAccountStatus,
  isActiveOrganizationEmployee,
  isAssignableOrganizationEmployee,
} from './organizationEmployeeFilters';
import type { EmployeeRecord } from '../../shared/types';

function employee(
  accountStatus: EmployeeRecord['accountStatus'],
  membershipStatus: EmployeeRecord['membershipStatus'] = 'approved',
): EmployeeRecord {
  return {
    id: 'employee-test',
    email: 'employee@example.com',
    fullName: '测试成员',
    primaryRole: 'employee',
    accountStatus,
    membershipStatus,
    departmentId: null,
    departmentName: null,
    isDepartmentLead: false,
    visibilityScope: 'organization',
    managementTitleId: null,
    managementTitleName: null,
    createdAt: '2026-07-31T00:00:00.000Z',
    lastLoginAt: null,
  };
}

test('strict active and transitional approved both mean an active account', () => {
  assert.equal(isActiveOrganizationAccountStatus('active'), true);
  assert.equal(isActiveOrganizationAccountStatus('approved'), true);
  assert.equal(isActiveOrganizationAccountStatus('disabled'), false);
});

test('active cloud members remain visible and assignable', () => {
  const active = employee('active');
  assert.equal(isActiveOrganizationEmployee(active), true);
  assert.equal(isAssignableOrganizationEmployee(active), true);
});

test('disabled or non-approved memberships remain unavailable', () => {
  assert.equal(isActiveOrganizationEmployee(employee('disabled', 'disabled')), false);
  assert.equal(isActiveOrganizationEmployee(employee('active', 'rejected')), false);
});
