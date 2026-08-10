import type { AccountStatus, EmployeeRecord, MembershipStatus } from '../../shared/types';

// 顾源源 5/25 PM:
// V2.1 早期有一批 mock 假员工 (益语管理员/庆华/嘉宁/一朔/佳乐/大周, 邮箱 @yiyu-system.com),
// 之前用 LEGACY_SIMULATION_NAMES + IDS + EMAILS 黑名单过滤.
// 现在:
//   1. 庆华 是真 AI 同事 (bot_members 表, primaryRole='ai_agent'), 不能过滤
//   2. 其他 mock 员工已经清掉, 黑名单本身没意义了
// → 删除全部黑名单, filter 只看 accountStatus.

type OrganizationIdentity = Pick<EmployeeRecord, 'id' | 'email' | 'fullName' | 'primaryRole'>;

/**
 * 是否是"假员工" — 现在恒返 false (旧 mock 黑名单已删).
 * 保留这个函数是为了不破坏现有调用方 (App.tsx 8613 行等). 直接返 false.
 */
export function isLegacyOrganizationEmployee(_employee: OrganizationIdentity): boolean {
  return false;
}

/**
 * 名字是否是"假员工 mock 名字" — 现在恒返 false.
 * 顾源源 5/25 PM 钦定: 真员工/真 bot 名字都尊重, 不再 hard-code 黑名单.
 */
export function isLegacyOrganizationPersonName(_value: string | null | undefined): boolean {
  return false;
}

export function isActiveOrganizationAccountStatus(
  status: AccountStatus | string | null | undefined,
): boolean {
  return status === 'active' || status === 'approved';
}

export function isActiveOrganizationEmployee(
  employee: Pick<EmployeeRecord, 'accountStatus' | 'membershipStatus'>,
): boolean {
  const membershipStatus = employee.membershipStatus as MembershipStatus | string | null | undefined;
  return isActiveOrganizationAccountStatus(employee.accountStatus)
    && (!membershipStatus || membershipStatus === 'approved');
}

/**
 * 是否可作为任务 owner / 协作者 / 部门成员：严格云返回 active；
 * 旧本地快照可能仍返回 approved，过渡期间两者都表示有效账号。
 * AI 同事 (primaryRole='ai_agent') 跟 employee/admin 平权, 也能当 owner/协作者.
 */
export function isAssignableOrganizationEmployee(employee: EmployeeRecord): boolean {
  if (employee.accountStatus === 'disabled') return false;
  return isActiveOrganizationEmployee(employee);
}
