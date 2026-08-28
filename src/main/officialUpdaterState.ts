import type { OfficialPushUpdatePayload } from '../shared/types.js';

export type PersistedUpdateOperationStatus =
  | 'downloading'
  | 'ready-to-install'
  | 'installer-opened'
  | 'failed';

export interface PersistedUpdateOperation {
  operationId: string;
  status: PersistedUpdateOperationStatus;
  update: OfficialPushUpdatePayload;
  targetPath: string;
  temporaryPath: string;
  transferred: number;
  total: number;
  percent: number;
  etag: string | null;
  lastModified: string | null;
  lastError: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface PersistedUpdaterState {
  schemaVersion: 1;
  lastSuccessfulCheckAt: string | null;
  operation: PersistedUpdateOperation | null;
}

export interface UpdateArtifactProbe {
  exists: boolean;
  sizeBytes: number;
  sha512: string | null;
}

const OPERATION_STATUSES = new Set<PersistedUpdateOperationStatus>([
  'downloading',
  'ready-to-install',
  'installer-opened',
  'failed',
]);

function clampPercent(value: unknown): number {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 0;
  return Math.max(0, Math.min(100, numeric));
}

function parseVersion(value: string | null | undefined): [number, number, number] | null {
  const match = String(value || '').trim().match(/^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$/);
  return match ? [Number(match[1]), Number(match[2]), Number(match[3])] : null;
}

function compareVersions(left: string | null | undefined, right: string | null | undefined): number | null {
  const a = parseVersion(left);
  const b = parseVersion(right);
  if (!a || !b) return null;
  for (let index = 0; index < a.length; index += 1) {
    if (a[index] > b[index]) return 1;
    if (a[index] < b[index]) return -1;
  }
  return 0;
}

function operationIdFor(update: OfficialPushUpdatePayload): string {
  const releaseIdentity = String(update.releaseId || '').trim();
  if (releaseIdentity) return `release:${releaseIdentity.replace(/[^a-zA-Z0-9._-]/g, '-').slice(0, 120)}`;
  const digestPrefix = String(update.sha512 || '').replace(/[^a-zA-Z0-9]/g, '').slice(0, 20) || 'no-digest';
  return `version:${update.version}:${digestPrefix}`;
}

export function createPersistedUpdateOperation(
  update: OfficialPushUpdatePayload,
  targetPath: string,
  temporaryPath: string,
  now = new Date(),
): PersistedUpdateOperation {
  const timestamp = now.toISOString();
  const total = Math.max(0, Number(update.sizeBytes || 0));
  return {
    operationId: operationIdFor(update),
    status: 'downloading',
    update,
    targetPath,
    temporaryPath,
    transferred: 0,
    total,
    percent: 0,
    etag: null,
    lastModified: null,
    lastError: null,
    createdAt: timestamp,
    updatedAt: timestamp,
  };
}

export function advanceUpdateProgress(
  operation: PersistedUpdateOperation,
  transferred: number,
  total: number,
  now = new Date(),
): PersistedUpdateOperation {
  const nextTotal = Math.max(operation.total, Number.isFinite(total) ? total : 0);
  const nextTransferred = Math.max(operation.transferred, Number.isFinite(transferred) ? transferred : 0);
  const nextPercent = nextTotal > 0 ? (nextTransferred / nextTotal) * 100 : operation.percent;
  return {
    ...operation,
    status: 'downloading',
    transferred: nextTransferred,
    total: nextTotal,
    percent: Math.max(operation.percent, clampPercent(nextPercent)),
    lastError: null,
    updatedAt: now.toISOString(),
  };
}

export function parsePersistedUpdaterState(raw: unknown): PersistedUpdaterState {
  const fallback: PersistedUpdaterState = { schemaVersion: 1, lastSuccessfulCheckAt: null, operation: null };
  if (!raw || typeof raw !== 'object') return fallback;
  const source = raw as Record<string, unknown>;
  const lastSuccessfulCheckAt = typeof source.lastSuccessfulCheckAt === 'string'
    && Number.isFinite(Date.parse(source.lastSuccessfulCheckAt))
    ? source.lastSuccessfulCheckAt
    : null;
  const candidate = source.operation;
  if (!candidate || typeof candidate !== 'object') return { ...fallback, lastSuccessfulCheckAt };
  const operation = candidate as Record<string, unknown>;
  const update = operation.update as OfficialPushUpdatePayload | undefined;
  const status = operation.status as PersistedUpdateOperationStatus;
  if (
    !OPERATION_STATUSES.has(status)
    || !update
    || typeof update.version !== 'string'
    || typeof update.sha512 !== 'string'
    || typeof operation.operationId !== 'string'
    || typeof operation.targetPath !== 'string'
    || typeof operation.temporaryPath !== 'string'
  ) return { ...fallback, lastSuccessfulCheckAt };
  const createdAt = typeof operation.createdAt === 'string' ? operation.createdAt : new Date(0).toISOString();
  const updatedAt = typeof operation.updatedAt === 'string' ? operation.updatedAt : createdAt;
  return {
    schemaVersion: 1,
    lastSuccessfulCheckAt,
    operation: {
      operationId: operation.operationId,
      status,
      update,
      targetPath: operation.targetPath,
      temporaryPath: operation.temporaryPath,
      transferred: Math.max(0, Number(operation.transferred || 0)),
      total: Math.max(0, Number(operation.total || update.sizeBytes || 0)),
      percent: clampPercent(operation.percent),
      etag: typeof operation.etag === 'string' ? operation.etag : null,
      lastModified: typeof operation.lastModified === 'string' ? operation.lastModified : null,
      lastError: typeof operation.lastError === 'string' ? operation.lastError : null,
      createdAt,
      updatedAt,
    },
  };
}

export async function reconcilePersistedUpdaterState(
  state: PersistedUpdaterState,
  currentVersion: string,
  probeTarget: (targetPath: string) => Promise<UpdateArtifactProbe>,
): Promise<PersistedUpdaterState> {
  const operation = state.operation;
  if (!operation) return state;
  const comparison = compareVersions(operation.update.version, currentVersion);
  if (comparison !== null && comparison <= 0) return { ...state, operation: null };

  if (operation.status === 'downloading') {
    return {
      ...state,
      operation: {
        ...operation,
        status: 'failed',
        lastError: '上次下载被中断，可以继续下载。',
        updatedAt: new Date().toISOString(),
      },
    };
  }
  if (operation.status !== 'ready-to-install' && operation.status !== 'installer-opened') return state;

  const artifact = await probeTarget(operation.targetPath);
  if (!artifact.exists) {
    return {
      ...state,
      operation: {
        ...operation,
        status: 'failed',
        percent: 0,
        transferred: 0,
        lastError: '已下载的安装包不存在，请重新下载。',
        updatedAt: new Date().toISOString(),
      },
    };
  }
  const expectedSize = Number(operation.update.sizeBytes || operation.total || 0);
  const sizeMatches = expectedSize <= 0 || artifact.sizeBytes === expectedSize;
  const digestMatches = artifact.sha512 === operation.update.sha512;
  if (!sizeMatches || !digestMatches) {
    return {
      ...state,
      operation: {
        ...operation,
        status: 'failed',
        lastError: '已下载的安装包校验失败，请重新下载。',
        updatedAt: new Date().toISOString(),
      },
    };
  }
  return state;
}

export function normalizeUpdaterErrorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  const lower = message.toLowerCase();
  if (lower.includes('abort') || lower.includes('timeout') || lower.includes('enotfound') || lower.includes('econn')) {
    return '当前网络无法连接益语智库官网，软件会在联网后自动重试。';
  }
  if ((lower.includes('enoent') && (lower.includes('info.plist') || lower.includes('mounted'))) || lower.includes('安装包结构')) {
    return '安装包结构与当前更新程序不兼容，请下载兼容迁移版后重试。';
  }
  if (lower.includes('sha512') || lower.includes('校验') || lower.includes('大小与官网清单')) {
    return '更新包完整性校验失败，已停止安装，请重新下载。';
  }
  if (lower.includes('eacces') || lower.includes('eperm') || lower.includes('permission')) {
    return '没有足够权限完成更新，请确认软件位于“应用程序”目录后重试。';
  }
  return '更新未能完成，请稍后重试；如果问题持续，请联系管理员查看更新日志。';
}
