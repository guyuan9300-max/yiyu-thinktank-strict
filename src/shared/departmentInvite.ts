function toInviteSeed(value: string) {
  let hash = 0;
  for (const char of value) {
    hash = (hash * 131 + char.charCodeAt(0)) % 1_000_000;
  }
  return String(hash).padStart(6, '0');
}

function toBase36Seed(value: string, modulo = 36 ** 4) {
  let hash = 0;
  for (const char of value) {
    hash = (hash * 131 + char.charCodeAt(0)) % modulo;
  }
  return hash.toString(36).toUpperCase();
}

function normalizeInviteSegment(
  rawValue: string | null | undefined,
  limit: number,
) {
  const value = rawValue?.trim();
  if (!value) return '';
  const asciiOnly = value.replace(/[^A-Za-z0-9]+/g, '').toUpperCase();
  if (asciiOnly) {
    return asciiOnly.slice(0, limit);
  }
  return toBase36Seed(value, 36 ** limit).padStart(limit, '0').slice(0, limit);
}

export type DepartmentInviteCodeOptions = {
  organizationId?: string | null;
  organizationName?: string | null;
  departmentName?: string | null;
  order?: number | null;
};

export type ManagementInviteRole = 'organization_lead' | 'advisor';

const MANAGEMENT_INVITE_ROLE_META: Record<ManagementInviteRole, { label: string; prefix: string; order: number }> = {
  organization_lead: { label: '组织负责人', prefix: 'OF', order: 1 },
  advisor: { label: '顾问', prefix: 'GW', order: 2 },
};

export type ManagementTitleInviteCodeOptions = {
  organizationName?: string | null;
  titleName?: string | null;
  order?: number | null;
};

export function buildDepartmentInviteCode(
  departmentId: string,
  options: DepartmentInviteCodeOptions = {},
) {
  const { organizationId, organizationName, departmentName, order } = options;
  if (!organizationId && !organizationName && !departmentName && typeof order !== 'number') {
    return toInviteSeed(departmentId);
  }

  const orgPrefix = normalizeInviteSegment(organizationName, 4)
    || toBase36Seed(organizationId || '', 36 ** 4).padStart(4, '0').slice(0, 4)
    || 'ORGX';
  const deptPrefix = normalizeInviteSegment(departmentName, 2)
    || toBase36Seed(departmentId, 36 ** 2).padStart(2, '0').slice(0, 2)
    || 'BM';
  const orderValue = typeof order === 'number' && Number.isFinite(order)
    ? Math.max(1, order + 1)
    : (parseInt(toInviteSeed(departmentId).slice(-2), 10) % 99) + 1;
  const orderSegment = String(orderValue).padStart(2, '0');
  const checksum = toBase36Seed(`${organizationId || ''}:${departmentId}`, 36 ** 4).padStart(4, '0').slice(0, 4);
  return `${orgPrefix}-${deptPrefix}${orderSegment}-${checksum}`;
}

export function buildManagementInviteCode(
  organizationId: string,
  role: ManagementInviteRole,
  organizationName?: string | null,
) {
  const meta = MANAGEMENT_INVITE_ROLE_META[role];
  const orgPrefix = normalizeInviteSegment(organizationName, 4)
    || toBase36Seed(organizationId || '', 36 ** 4).padStart(4, '0').slice(0, 4)
    || 'ORGX';
  const orderSegment = String(meta.order).padStart(2, '0');
  const checksum = toBase36Seed(`${organizationId || ''}:management:${role}`, 36 ** 4).padStart(4, '0').slice(0, 4);
  return `${orgPrefix}-${meta.prefix}${orderSegment}-${checksum}`;
}

export function managementInviteRoleLabel(role: ManagementInviteRole) {
  return MANAGEMENT_INVITE_ROLE_META[role].label;
}

export function buildManagementTitleInviteCode(
  organizationId: string,
  managementTitleId: string,
  options: ManagementTitleInviteCodeOptions = {},
) {
  const { organizationName, titleName, order } = options;
  const orgPrefix = normalizeInviteSegment(organizationName, 4)
    || toBase36Seed(organizationId || '', 36 ** 4).padStart(4, '0').slice(0, 4)
    || 'ORGX';
  const titlePrefix = normalizeInviteSegment(titleName, 2)
    || toBase36Seed(managementTitleId || '', 36 ** 2).padStart(2, '0').slice(0, 2)
    || 'GL';
  const orderValue = typeof order === 'number' && Number.isFinite(order)
    ? Math.max(1, order + 1)
    : (parseInt(toInviteSeed(managementTitleId).slice(-2), 10) % 99) + 1;
  const orderSegment = String(orderValue).padStart(2, '0');
  const checksum = toBase36Seed(`${organizationId || ''}:management:${managementTitleId}`, 36 ** 4).padStart(4, '0').slice(0, 4);
  return `${orgPrefix}-${titlePrefix}${orderSegment}-${checksum}`;
}

export function buildDepartmentInviteShareText(departmentName: string, inviteCode: string) {
  return `${departmentName} 邀请码 ${inviteCode}`;
}

export function parseDepartmentInviteCode(rawValue: string) {
  const value = rawValue.trim();
  if (!value) return '';

  try {
    const directUrl = new URL(value);
    const invite = directUrl.searchParams.get('invite');
    if (invite) return parseDepartmentInviteCode(decodeURIComponent(invite));
    const departmentId = directUrl.searchParams.get('departmentId');
    if (departmentId) return departmentId.trim();
  } catch {}

  const inviteMatch = value.match(/invite=([^&]+)/i);
  if (inviteMatch) {
    try {
      return parseDepartmentInviteCode(decodeURIComponent(inviteMatch[1]));
    } catch {
      return parseDepartmentInviteCode(inviteMatch[1]);
    }
  }

  const departmentMatch = value.match(/departmentId=([^&]+)/i);
  if (departmentMatch) {
    return decodeURIComponent(departmentMatch[1]).trim();
  }

  if (value.startsWith('dept:')) {
    return value.slice(5).trim();
  }

  const formattedInviteCodeMatch = value.match(/\b([A-Z0-9]{2,8}-[A-Z0-9]{2,8}(?:-[A-Z0-9]{2,8})?)\b/i);
  if (formattedInviteCodeMatch) {
    return formattedInviteCodeMatch[1].toUpperCase();
  }

  const inviteCodeMatch = value.match(/\b(\d{6})\b/);
  if (inviteCodeMatch) {
    return inviteCodeMatch[1];
  }

  return value;
}
