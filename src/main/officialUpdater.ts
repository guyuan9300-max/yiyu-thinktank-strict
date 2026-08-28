import crypto from 'node:crypto';
import { once } from 'node:events';
import fs from 'node:fs';
import path from 'node:path';
import { app, BrowserWindow, ipcMain, shell } from 'electron';
import type {
  OfficialPushUpdatePayload,
  OfficialUpdateStatusSnapshot,
  ReleaseVersionMetadata,
  UpdateEventPayload,
  UpdateOrgIdentity,
} from '../shared/types.js';
import {
  advanceUpdateProgress,
  createPersistedUpdateOperation,
  normalizeUpdaterErrorMessage,
  parsePersistedUpdaterState,
  reconcilePersistedUpdaterState,
  type PersistedUpdateOperation,
  type PersistedUpdaterState,
} from './officialUpdaterState.js';

const RELEASE_SERVICE_BASE_URL = 'https://yiyu.love';
const UPDATE_EVENT_CHANNEL = 'yiyu-workbench:update-event';
const INITIAL_CHECK_DELAY_MS = 8_000;
const AUTOMATIC_CHECK_INTERVAL_MS = 60 * 60 * 1000;
const FETCH_TIMEOUT_MS = 12_000;

type UpdatePlatform = 'mac' | 'windows';

interface CentralReleaseUpdatePayload {
  releaseId?: string | null;
  version?: string | null;
  releaseVersion?: string | null;
  packageKind?: string | null;
  customPackageId?: string | null;
  customPackageName?: string | null;
  fileName?: string | null;
  sizeBytes?: number | null;
  sha512?: string | null;
  downloadUrl?: string | null;
  releaseDate?: string | null;
  publishedAt?: string | null;
  userNotes?: Record<string, string[]> | null;
}

function resolveUpdatePlatform(): UpdatePlatform | null {
  if (process.platform === 'darwin') return 'mac';
  if (process.platform === 'win32') return 'windows';
  return null;
}

const UPDATE_PLATFORM = resolveUpdatePlatform();
const PUBLIC_FEED_BASE_URL = `${RELEASE_SERVICE_BASE_URL}/desktop-updates/public/${UPDATE_PLATFORM || 'mac'}/`;
const INSTALLER_EXTENSION = UPDATE_PLATFORM === 'windows' ? 'exe' : 'dmg';

let mainWindowRef: BrowserWindow | null = null;
let setupDone = false;
let currentFeedBaseUrl = PUBLIC_FEED_BASE_URL;
let currentOrganizationCode: string | null = null;
let currentIdentityKey: string | null = null;
let lastOfficialPush: OfficialPushUpdatePayload | null = null;
let lastSuccessfulCheckAt = 0;
let automaticCheckInFlight: Promise<void> | null = null;
let downloadInFlight: Promise<{ targetPath: string; fileName: string; status: OfficialUpdateStatusSnapshot }> | null = null;
let intervalTimer: NodeJS.Timeout | null = null;
let artifactProbeCache: {
  targetPath: string;
  sizeBytes: number;
  mtimeMs: number;
  sha512: string;
} | null = null;
let persistedState: PersistedUpdaterState = {
  schemaVersion: 1,
  lastSuccessfulCheckAt: null,
  operation: null,
};

function updaterLog(message: string): void {
  try {
    const logPath = path.join(app.getPath('userData'), 'runtime', 'logs', 'official-updater.log');
    fs.mkdirSync(path.dirname(logPath), { recursive: true });
    fs.appendFileSync(logPath, `[${new Date().toISOString()}] ${message}\n`, 'utf8');
  } catch {
    // Logging must never block an update check.
  }
}

function statePath(): string {
  return path.join(app.getPath('userData'), 'runtime', 'official-update-state.json');
}

function loadState(): void {
  try {
    persistedState = parsePersistedUpdaterState(JSON.parse(fs.readFileSync(statePath(), 'utf8')));
    const timestamp = Date.parse(persistedState.lastSuccessfulCheckAt || '');
    lastSuccessfulCheckAt = Number.isFinite(timestamp) ? timestamp : 0;
  } catch {
    persistedState = { schemaVersion: 1, lastSuccessfulCheckAt: null, operation: null };
    lastSuccessfulCheckAt = 0;
  }
}

function persistState(): void {
  try {
    const target = statePath();
    fs.mkdirSync(path.dirname(target), { recursive: true });
    const temporary = `${target}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(persistedState, null, 2), 'utf8');
    fs.renameSync(temporary, target);
  } catch (error) {
    updaterLog(`state-persist-failed ${error instanceof Error ? error.message : String(error)}`);
  }
}

function markSuccessfulCheck(): void {
  lastSuccessfulCheckAt = Date.now();
  persistedState = {
    ...persistedState,
    lastSuccessfulCheckAt: new Date(lastSuccessfulCheckAt).toISOString(),
  };
  persistState();
}

function toStatusSnapshot(operation: PersistedUpdateOperation | null): OfficialUpdateStatusSnapshot | null {
  if (!operation) return null;
  return {
    operationId: operation.operationId,
    status: operation.status,
    update: operation.update,
    version: operation.update.version,
    fileName: path.basename(operation.targetPath),
    transferred: operation.transferred,
    total: operation.total,
    percent: operation.percent,
    canResume: operation.status === 'failed' && operation.transferred > 0,
    message: operation.lastError,
    updatedAt: operation.updatedAt,
  };
}

function setPersistedOperation(operation: PersistedUpdateOperation | null): OfficialUpdateStatusSnapshot | null {
  persistedState = { ...persistedState, operation };
  persistState();
  return toStatusSnapshot(operation);
}

function broadcast(payload: UpdateEventPayload): void {
  if (!mainWindowRef || mainWindowRef.isDestroyed()) return;
  mainWindowRef.webContents.send(UPDATE_EVENT_CHANNEL, payload);
}

function broadcastStatus(kind: UpdateEventPayload['kind'] = 'update-status'): OfficialUpdateStatusSnapshot | null {
  const updateStatus = toStatusSnapshot(persistedState.operation);
  broadcast({
    kind,
    version: updateStatus?.version,
    percent: updateStatus?.percent,
    transferred: updateStatus?.transferred,
    total: updateStatus?.total,
    message: updateStatus?.message || undefined,
    officialPush: updateStatus?.update || null,
    updateStatus,
  });
  return updateStatus;
}

export function parseStrictVersion(value: string | null | undefined): [number, number, number] | null {
  const matched = String(value || '').trim().match(/^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$/);
  return matched ? [Number(matched[1]), Number(matched[2]), Number(matched[3])] : null;
}

export function compareStrictVersions(left: string | null | undefined, right: string | null | undefined): number | null {
  const a = parseStrictVersion(left);
  const b = parseStrictVersion(right);
  if (!a || !b) return null;
  for (let index = 0; index < a.length; index += 1) {
    if (a[index] > b[index]) return 1;
    if (a[index] < b[index]) return -1;
  }
  return 0;
}

function normalizeSha512(value: string | null | undefined): string | null {
  const raw = String(value || '').trim();
  if (!raw) return null;
  return /^[a-f0-9]{128}$/i.test(raw) ? Buffer.from(raw, 'hex').toString('base64') : raw;
}

export function buildOfficialUpdate(
  update: CentralReleaseUpdatePayload,
  currentVersion = app.getVersion(),
): OfficialPushUpdatePayload | null {
  const version = String(update.releaseVersion || update.version || '').trim();
  if (!version) throw new Error('官网更新清单缺少版本号。');
  const comparison = compareStrictVersions(version, currentVersion);
  if (comparison == null) throw new Error(`官网版本号格式无效：${version}`);
  if (comparison <= 0) return null;

  const downloadUrl = String(update.downloadUrl || '').trim();
  const sha512 = normalizeSha512(update.sha512);
  if (!downloadUrl || !sha512 || !update.fileName || !Number(update.sizeBytes || 0)) {
    throw new Error('官网已发布更高版本，但安装包地址、文件名、大小或 SHA512 不完整。');
  }
  const parsedDownloadUrl = new URL(downloadUrl);
  if (parsedDownloadUrl.protocol !== 'https:') {
    throw new Error('官网安装包不是 HTTPS 地址，已停止下载。');
  }

  const packageKind: OfficialPushUpdatePayload['packageKind'] =
    update.packageKind === 'custom' || update.customPackageId ? 'custom' : 'release';
  const customName = String(update.customPackageName || '').trim();
  return {
    title: packageKind === 'custom'
      ? `收到组织定制版：${customName || version}`
      : `发现益语智库新版本：${version}`,
    releaseId: update.releaseId || null,
    version,
    releaseVersion: version,
    currentVersion,
    packageKind,
    customPackageId: update.customPackageId || null,
    customPackageName: customName || null,
    fileName: update.fileName,
    sizeBytes: Number(update.sizeBytes),
    sha512,
    downloadUrl,
    publishedAt: update.publishedAt || update.releaseDate || null,
    userNotes: update.userNotes && typeof update.userNotes === 'object' ? update.userNotes : {},
    organizationCode: currentOrganizationCode,
    relation: packageKind === 'custom' ? 'switch-custom' : 'upgrade',
  };
}

async function fetchJson<T>(targetUrl: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const response = await fetch(targetUrl, {
      ...init,
      cache: 'no-store',
      headers: {
        Accept: 'application/json',
        ...(init?.headers || {}),
      },
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`官网更新服务返回 ${response.status}`);
    const contentType = response.headers.get('content-type') || '';
    if (!contentType.includes('json')) throw new Error('官网更新入口返回了非 JSON 内容。');
    return await response.json() as T;
  } finally {
    clearTimeout(timer);
  }
}

async function checkOfficialUpdate(broadcastResult: boolean): Promise<OfficialPushUpdatePayload | null> {
  try {
    const payload = await fetchJson<CentralReleaseUpdatePayload>(new URL('latest', currentFeedBaseUrl).toString());
    const update = buildOfficialUpdate(payload);
    lastOfficialPush = update;
    updaterLog(update
      ? `update-detected version=${update.version} org=${currentOrganizationCode || 'public'}`
      : `up-to-date version=${app.getVersion()} org=${currentOrganizationCode || 'public'}`);
    if (broadcastResult) {
      broadcast(update
        ? { kind: 'official-push-available', version: update.version, officialPush: update }
        : { kind: 'official-push-not-available', officialPush: null });
    } else if (update) {
      broadcast({ kind: 'official-push-available', version: update.version, officialPush: update });
    }
    return update;
  } catch (error) {
    const message = normalizeUpdateError(error);
    updaterLog(`check-failed ${message}`);
    if (broadcastResult) broadcast({ kind: 'error', message });
    throw new Error(message);
  }
}

function normalizeUpdateError(error: unknown): string {
  return normalizeUpdaterErrorMessage(error);
}

async function automaticCheck(): Promise<void> {
  if (automaticCheckInFlight) return automaticCheckInFlight;
  automaticCheckInFlight = (async () => {
    await checkOfficialUpdate(false);
    markSuccessfulCheck();
  })();
  try {
    await automaticCheckInFlight;
  } finally {
    automaticCheckInFlight = null;
  }
}

export async function setOfficialUpdateIdentity(identity: UpdateOrgIdentity | null): Promise<void> {
  const normalized = {
    organizationId: String(identity?.organizationId || '').trim(),
    organizationSlug: String(identity?.organizationSlug || '').trim(),
    organizationName: String(identity?.organizationName || '').trim(),
    cloudBackendUrl: String(identity?.cloudBackendUrl || '').trim(),
    platform: UPDATE_PLATFORM || 'mac',
  };
  const identityKey = JSON.stringify(normalized);
  if (identityKey === currentIdentityKey) return;
  currentIdentityKey = identityKey;
  lastOfficialPush = null;

  if (!normalized.organizationId && !normalized.organizationSlug) {
    currentOrganizationCode = null;
    currentFeedBaseUrl = PUBLIC_FEED_BASE_URL;
    return;
  }
  try {
    const resolved = await fetchJson<{ canonicalOrgCode?: string; updateFeedBaseUrl?: string }>(
      `${RELEASE_SERVICE_BASE_URL}/desktop-updates/organizations/resolve`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(normalized),
      },
    );
    currentOrganizationCode = String(resolved.canonicalOrgCode || '').trim() || null;
    const resolvedFeed = String(resolved.updateFeedBaseUrl || '').trim();
    currentFeedBaseUrl = resolvedFeed.startsWith('https://') ? resolvedFeed : PUBLIC_FEED_BASE_URL;
  } catch (error) {
    currentOrganizationCode = null;
    currentFeedBaseUrl = PUBLIC_FEED_BASE_URL;
    updaterLog(`organization-feed-fallback ${normalizeUpdateError(error)}`);
  }
}

function safeInstallerName(value: string | null | undefined, version: string): string {
  const safe = String(value || '').trim().replace(/[\\/:\0]/g, '-').replace(/^\.+$/, '').slice(0, 180);
  return safe || `yiyu-thinktank-strict-${version}.${INSTALLER_EXTENSION}`;
}

function downloadPaths(update: OfficialPushUpdatePayload): { targetPath: string; temporaryPath: string; fileName: string } {
  const fileName = safeInstallerName(update.fileName, update.version);
  const directory = path.join(app.getPath('userData'), 'official-update-downloads');
  return {
    fileName,
    targetPath: path.join(directory, fileName),
    temporaryPath: path.join(directory, `${fileName}.download`),
  };
}

async function sha512ForFile(filePath: string): Promise<string> {
  const hash = crypto.createHash('sha512');
  const stream = fs.createReadStream(filePath);
  for await (const chunk of stream) hash.update(chunk as Buffer);
  return hash.digest('base64');
}

async function probeDownloadedArtifact(targetPath: string): Promise<{ exists: boolean; sizeBytes: number; sha512: string | null }> {
  try {
    const stats = await fs.promises.stat(targetPath);
    if (!stats.isFile()) return { exists: false, sizeBytes: 0, sha512: null };
    if (artifactProbeCache
      && artifactProbeCache.targetPath === targetPath
      && artifactProbeCache.sizeBytes === stats.size
      && artifactProbeCache.mtimeMs === stats.mtimeMs) {
      return { exists: true, sizeBytes: stats.size, sha512: artifactProbeCache.sha512 };
    }
    const sha512 = await sha512ForFile(targetPath);
    artifactProbeCache = { targetPath, sizeBytes: stats.size, mtimeMs: stats.mtimeMs, sha512 };
    return { exists: true, sizeBytes: stats.size, sha512 };
  } catch {
    if (artifactProbeCache?.targetPath === targetPath) artifactProbeCache = null;
    return { exists: false, sizeBytes: 0, sha512: null };
  }
}

async function reconcileUpdateState(): Promise<OfficialUpdateStatusSnapshot | null> {
  const operation = persistedState.operation;
  if (operation) {
    const expected = downloadPaths(operation.update);
    const hasSafePaths = path.resolve(operation.targetPath) === path.resolve(expected.targetPath)
      && path.resolve(operation.temporaryPath) === path.resolve(expected.temporaryPath);
    if (!hasSafePaths) {
      persistedState = { ...persistedState, operation: null };
      persistState();
      updaterLog(`discarded-unsafe-receipt operation=${operation.operationId}`);
      return null;
    }
  }
  const previous = JSON.stringify(persistedState);
  persistedState = await reconcilePersistedUpdaterState(persistedState, app.getVersion(), probeDownloadedArtifact);
  if (JSON.stringify(persistedState) !== previous) persistState();
  return toStatusSnapshot(persistedState.operation);
}

async function writeChunk(stream: fs.WriteStream, chunk: Buffer): Promise<void> {
  if (!stream.write(chunk)) await once(stream, 'drain');
}

async function downloadInstallerOnce(update: OfficialPushUpdatePayload): Promise<{
  targetPath: string;
  fileName: string;
  status: OfficialUpdateStatusSnapshot;
}> {
  const downloadUrl = String(update.downloadUrl || '').trim();
  if (!downloadUrl) throw new Error('官网没有返回安装包地址。');
  const { fileName, targetPath, temporaryPath } = downloadPaths(update);
  fs.mkdirSync(path.dirname(targetPath), { recursive: true });

  const existingStatus = await reconcileUpdateState();
  if (existingStatus?.operationId === createPersistedUpdateOperation(update, targetPath, temporaryPath).operationId
    && (existingStatus.status === 'ready-to-install' || existingStatus.status === 'installer-opened')) {
    return { targetPath, fileName, status: existingStatus };
  }

  let operation = createPersistedUpdateOperation(update, targetPath, temporaryPath);
  let partialSize = 0;
  try {
    const partialStats = await fs.promises.stat(temporaryPath);
    partialSize = partialStats.isFile() ? partialStats.size : 0;
  } catch {
    partialSize = 0;
  }
  const expectedSize = Math.max(0, Number(update.sizeBytes || 0));
  if (partialSize > 0 && expectedSize > 0 && partialSize >= expectedSize) {
    await fs.promises.rm(temporaryPath, { force: true });
    partialSize = 0;
  }
  operation = advanceUpdateProgress(operation, partialSize, expectedSize);
  const previous = persistedState.operation;
  if (previous?.operationId === operation.operationId) {
    operation.etag = previous.etag;
    operation.lastModified = previous.lastModified;
    operation.createdAt = previous.createdAt;
  }
  setPersistedOperation(operation);
  broadcastStatus('update-status');

  const headers: Record<string, string> = { Accept: 'application/octet-stream' };
  if (partialSize > 0) {
    headers.Range = `bytes=${partialSize}-`;
    const validator = operation.etag || operation.lastModified;
    if (validator) headers['If-Range'] = validator;
  }

  let stream: fs.WriteStream | null = null;
  try {
    let response = await fetch(downloadUrl, { cache: 'no-store', headers });
    if (partialSize > 0 && response.status === 416) {
      await response.body?.cancel().catch(() => undefined);
      await fs.promises.rm(temporaryPath, { force: true });
      partialSize = 0;
      operation = { ...operation, transferred: 0, percent: 0, etag: null, lastModified: null };
      response = await fetch(downloadUrl, { cache: 'no-store', headers: { Accept: 'application/octet-stream' } });
    }
    if (!response.ok) throw new Error(`安装包下载失败：${response.status}`);
    if (!response.body) throw new Error('官网安装包没有可读取的数据流。');

    let rangeStart = Number(response.headers.get('content-range')?.match(/^bytes\s+(\d+)-/i)?.[1] || -1);
    if (partialSize > 0 && response.status === 206 && rangeStart !== partialSize) {
      await response.body.cancel().catch(() => undefined);
      await fs.promises.rm(temporaryPath, { force: true });
      partialSize = 0;
      operation = { ...operation, transferred: 0, percent: 0, etag: null, lastModified: null };
      response = await fetch(downloadUrl, { cache: 'no-store', headers: { Accept: 'application/octet-stream' } });
      if (!response.ok || !response.body) throw new Error(`安装包重新下载失败：${response.status}`);
      rangeStart = -1;
    }
    const resumed = partialSize > 0 && response.status === 206 && rangeStart === partialSize;
    if (partialSize > 0 && !resumed) {
      await fs.promises.rm(temporaryPath, { force: true });
      partialSize = 0;
      operation = { ...operation, transferred: 0, percent: 0 };
    }
    operation = {
      ...operation,
      etag: response.headers.get('etag') || operation.etag,
      lastModified: response.headers.get('last-modified') || operation.lastModified,
    };
    stream = fs.createWriteStream(temporaryPath, { flags: resumed ? 'a' : 'w' });
    let transferred = partialSize;
    let lastBroadcastAt = 0;
    let lastPersistedPercent = Math.floor(operation.percent);
    const reader = response.body.getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value) continue;
      const chunk = Buffer.from(value);
      transferred += chunk.length;
      await writeChunk(stream, chunk);
      operation = advanceUpdateProgress(operation, transferred, expectedSize);
      const now = Date.now();
      const integerPercent = Math.floor(operation.percent);
      if (integerPercent > lastPersistedPercent || now - lastBroadcastAt >= 1_000) {
        persistedState = { ...persistedState, operation };
        persistState();
        lastPersistedPercent = integerPercent;
        lastBroadcastAt = now;
        broadcast({
          kind: 'download-progress',
          version: update.version,
          percent: operation.percent,
          transferred: operation.transferred,
          total: operation.total || undefined,
          officialPush: update,
          updateStatus: toStatusSnapshot(operation),
        });
      }
    }
    await new Promise<void>((resolve, reject) => {
      stream!.once('error', reject);
      stream!.end(resolve);
    });
    stream = null;

    const finalStats = await fs.promises.stat(temporaryPath);
    if (expectedSize > 0 && finalStats.size !== expectedSize) {
      throw new Error('安装包下载大小与官网清单不一致。');
    }
    const actualSha512 = await sha512ForFile(temporaryPath);
    if (actualSha512 !== normalizeSha512(update.sha512)) {
      await fs.promises.rm(temporaryPath, { force: true });
      throw new Error('安装包 SHA512 校验失败，已删除不完整文件。');
    }
    await fs.promises.rm(targetPath, { force: true });
    if (artifactProbeCache?.targetPath === targetPath) artifactProbeCache = null;
    await fs.promises.rename(temporaryPath, targetPath);
    const installedStats = await fs.promises.stat(targetPath);
    artifactProbeCache = {
      targetPath,
      sizeBytes: installedStats.size,
      mtimeMs: installedStats.mtimeMs,
      sha512: actualSha512,
    };
    operation = {
      ...operation,
      status: 'ready-to-install',
      transferred: finalStats.size,
      total: expectedSize || finalStats.size,
      percent: 100,
      lastError: null,
      updatedAt: new Date().toISOString(),
    };
    const status = setPersistedOperation(operation)!;
    broadcast({
      kind: 'downloaded',
      version: update.version,
      percent: 100,
      transferred: status.transferred,
      total: status.total,
      officialPush: update,
      updateStatus: status,
    });
    broadcastStatus('ready-to-install');
    updaterLog(`download-ready operation=${operation.operationId} version=${update.version} file=${fileName} bytes=${finalStats.size}`);
    return { targetPath, fileName, status };
  } catch (error) {
    stream?.destroy();
    let retainedBytes = 0;
    try {
      retainedBytes = (await fs.promises.stat(temporaryPath)).size;
    } catch {
      retainedBytes = 0;
    }
    const message = normalizeUpdateError(error);
    operation = {
      ...operation,
      status: 'failed',
      transferred: retainedBytes,
      percent: expectedSize > 0 ? Math.min(99, (retainedBytes / expectedSize) * 100) : operation.percent,
      lastError: retainedBytes > 0 ? `${message} 已保留下载进度，可重试。` : message,
      updatedAt: new Date().toISOString(),
    };
    setPersistedOperation(operation);
    broadcastStatus('update-status');
    updaterLog(`download-failed operation=${operation.operationId} detail=${error instanceof Error ? error.message : String(error)}`);
    throw new Error(operation.lastError || message);
  }
}

async function downloadInstaller(update: OfficialPushUpdatePayload): Promise<{
  targetPath: string;
  fileName: string;
  status: OfficialUpdateStatusSnapshot;
}> {
  if (downloadInFlight) return downloadInFlight;
  downloadInFlight = downloadInstallerOnce(update);
  try {
    return await downloadInFlight;
  } finally {
    downloadInFlight = null;
  }
}

async function currentReleaseMetadata(): Promise<ReleaseVersionMetadata | null> {
  if (!UPDATE_PLATFORM) return null;
  const url = new URL('/desktop-updates/releases/metadata', RELEASE_SERVICE_BASE_URL);
  url.searchParams.set('version', app.getVersion());
  url.searchParams.set('platform', UPDATE_PLATFORM);
  return fetchJson<ReleaseVersionMetadata | null>(url.toString()).catch(() => null);
}

export function setupOfficialUpdater(mainWindow: BrowserWindow): void {
  mainWindowRef = mainWindow;
  if (setupDone) return;
  setupDone = true;
  loadState();
  void reconcileUpdateState()
    .then(() => broadcastStatus('update-status'))
    .catch((error) => updaterLog(`state-reconcile-failed ${error instanceof Error ? error.message : String(error)}`));

  ipcMain.handle('yiyu-workbench:update.check', async () => {
    if (!UPDATE_PLATFORM) return { ok: false, reason: '当前系统暂不支持官网更新。' };
    broadcast({ kind: 'checking' });
    try {
      const officialPush = await checkOfficialUpdate(true);
      markSuccessfulCheck();
      return { ok: true, version: officialPush?.version ?? app.getVersion(), officialPush };
    } catch (error) {
      return { ok: false, reason: normalizeUpdateError(error) };
    }
  });

  ipcMain.handle('yiyu-workbench:update.currentReleaseMetadata', currentReleaseMetadata);

  ipcMain.handle('yiyu-workbench:update.status', async () => {
    const status = await reconcileUpdateState();
    if (status) lastOfficialPush = status.update;
    return status;
  });

  ipcMain.handle('yiyu-workbench:update.downloadOfficialPush', async () => {
    if (!app.isPackaged) return { ok: false, reason: '开发版只验证更新发现；请在正式安装版中下载更新。' };
    try {
      const restoredStatus = await reconcileUpdateState();
      if (restoredStatus && (restoredStatus.status === 'ready-to-install' || restoredStatus.status === 'installer-opened')) {
        return {
          ok: true,
          version: restoredStatus.version,
          fileName: restoredStatus.fileName,
          status: restoredStatus,
        };
      }
      const officialPush = await checkOfficialUpdate(true) || lastOfficialPush || restoredStatus?.update;
      if (!officialPush) return { ok: false, reason: '当前没有高于本机版本的正式安装包。' };
      const downloaded = await downloadInstaller(officialPush);
      return {
        ok: true,
        version: officialPush.version,
        fileName: downloaded.fileName,
        status: downloaded.status,
      };
    } catch (error) {
      return { ok: false, reason: normalizeUpdateError(error) };
    }
  });

  ipcMain.handle('yiyu-workbench:update.installDownloadedOfficial', async () => {
    if (!app.isPackaged) return { ok: false, reason: '开发版不会打开正式更新安装包。' };
    try {
      const status = await reconcileUpdateState();
      const operation = persistedState.operation;
      if (!status || !operation || (status.status !== 'ready-to-install' && status.status !== 'installer-opened')) {
        return { ok: false, reason: status?.message || '尚未完成安装包下载，请先下载最新版。', status };
      }
      const openError = await shell.openPath(operation.targetPath);
      if (openError) {
        updaterLog(`installer-open-failed operation=${operation.operationId} detail=${openError}`);
        return { ok: false, version: status.version, fileName: status.fileName, reason: normalizeUpdateError(new Error(openError)), status };
      }
      const openedOperation: PersistedUpdateOperation = {
        ...operation,
        status: 'installer-opened',
        lastError: null,
        updatedAt: new Date().toISOString(),
      };
      const openedStatus = setPersistedOperation(openedOperation)!;
      broadcastStatus('installer-opened');
      updaterLog(`installer-opened operation=${operation.operationId} version=${status.version}`);
      return { ok: true, version: status.version, fileName: status.fileName, status: openedStatus };
    } catch (error) {
      return { ok: false, reason: normalizeUpdateError(error) };
    }
  });

  setTimeout(() => {
    void automaticCheck().catch(() => undefined);
  }, INITIAL_CHECK_DELAY_MS);
  intervalTimer = setInterval(() => {
    if (Date.now() - lastSuccessfulCheckAt < AUTOMATIC_CHECK_INTERVAL_MS) return;
    void automaticCheck().catch(() => undefined);
  }, AUTOMATIC_CHECK_INTERVAL_MS);
  mainWindow.on('focus', () => {
    if (Date.now() - lastSuccessfulCheckAt < AUTOMATIC_CHECK_INTERVAL_MS) return;
    void automaticCheck().catch(() => undefined);
  });

  app.once('before-quit', () => {
    if (intervalTimer) clearInterval(intervalTimer);
    intervalTimer = null;
  });
}
