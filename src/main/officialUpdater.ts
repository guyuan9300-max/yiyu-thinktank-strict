import crypto from 'node:crypto';
import { once } from 'node:events';
import fs from 'node:fs';
import path from 'node:path';
import { app, BrowserWindow, ipcMain, shell } from 'electron';
import type {
  OfficialPushUpdatePayload,
  ReleaseVersionMetadata,
  UpdateEventPayload,
  UpdateOrgIdentity,
} from '../shared/types.js';

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
let intervalTimer: NodeJS.Timeout | null = null;

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
    const parsed = JSON.parse(fs.readFileSync(statePath(), 'utf8')) as { lastSuccessfulCheckAt?: string };
    const timestamp = Date.parse(parsed.lastSuccessfulCheckAt || '');
    lastSuccessfulCheckAt = Number.isFinite(timestamp) ? timestamp : 0;
  } catch {
    lastSuccessfulCheckAt = 0;
  }
}

function markSuccessfulCheck(): void {
  lastSuccessfulCheckAt = Date.now();
  try {
    const target = statePath();
    fs.mkdirSync(path.dirname(target), { recursive: true });
    const temporary = `${target}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify({
      lastSuccessfulCheckAt: new Date(lastSuccessfulCheckAt).toISOString(),
    }), 'utf8');
    fs.renameSync(temporary, target);
  } catch (error) {
    updaterLog(`state-persist-failed ${error instanceof Error ? error.message : String(error)}`);
  }
}

function broadcast(payload: UpdateEventPayload): void {
  if (!mainWindowRef || mainWindowRef.isDestroyed()) return;
  mainWindowRef.webContents.send(UPDATE_EVENT_CHANNEL, payload);
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
  const message = error instanceof Error ? error.message : String(error);
  const lower = message.toLowerCase();
  if (lower.includes('abort') || lower.includes('timeout') || lower.includes('enotfound') || lower.includes('econn')) {
    return '当前网络无法连接益语智库官网，软件会在联网后自动重试。';
  }
  if (lower.includes('sha512') || lower.includes('校验')) {
    return '更新包校验信息不完整或不正确，已停止更新。';
  }
  return message;
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

async function writeChunk(stream: fs.WriteStream, chunk: Buffer): Promise<void> {
  if (!stream.write(chunk)) await once(stream, 'drain');
}

async function downloadInstaller(update: OfficialPushUpdatePayload): Promise<{ targetPath: string; fileName: string }> {
  const downloadUrl = String(update.downloadUrl || '').trim();
  if (!downloadUrl) throw new Error('官网没有返回安装包地址。');
  const fileName = safeInstallerName(update.fileName, update.version);
  const directory = path.join(app.getPath('userData'), 'official-update-downloads');
  fs.mkdirSync(directory, { recursive: true });
  const targetPath = path.join(directory, fileName);
  const temporaryPath = `${targetPath}.download`;
  fs.rmSync(temporaryPath, { force: true });

  const response = await fetch(downloadUrl, { cache: 'no-store', headers: { Accept: 'application/octet-stream' } });
  if (!response.ok) throw new Error(`安装包下载失败：${response.status}`);
  const expectedSize = Number(update.sizeBytes || response.headers.get('content-length') || 0);
  const hash = crypto.createHash('sha512');
  const stream = fs.createWriteStream(temporaryPath);
  let transferred = 0;
  try {
    if (!response.body) throw new Error('官网安装包没有可读取的数据流。');
    const reader = response.body.getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value) continue;
      const chunk = Buffer.from(value);
      transferred += chunk.length;
      hash.update(chunk);
      await writeChunk(stream, chunk);
      broadcast({
        kind: 'download-progress',
        version: update.version,
        percent: expectedSize > 0 ? (transferred / expectedSize) * 100 : undefined,
        transferred,
        total: expectedSize || undefined,
      });
    }
    await new Promise<void>((resolve, reject) => {
      stream.once('error', reject);
      stream.end(resolve);
    });
  } catch (error) {
    stream.destroy();
    fs.rmSync(temporaryPath, { force: true });
    throw error;
  }
  if (expectedSize > 0 && transferred !== expectedSize) {
    fs.rmSync(temporaryPath, { force: true });
    throw new Error('安装包下载大小与官网清单不一致。');
  }
  const actualSha512 = hash.digest('base64');
  if (actualSha512 !== normalizeSha512(update.sha512)) {
    fs.rmSync(temporaryPath, { force: true });
    throw new Error('安装包 SHA512 校验失败，已删除不完整文件。');
  }
  fs.rmSync(targetPath, { force: true });
  fs.renameSync(temporaryPath, targetPath);
  broadcast({ kind: 'downloaded', version: update.version });
  updaterLog(`download-ready version=${update.version} file=${fileName} bytes=${transferred}`);
  return { targetPath, fileName };
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

  ipcMain.handle('yiyu-workbench:update.installOfficialPush', async () => {
    if (!app.isPackaged) return { ok: false, reason: '开发版只验证更新发现；请在安装版下载并打开正式安装包。' };
    try {
      const officialPush = await checkOfficialUpdate(true) || lastOfficialPush;
      if (!officialPush) return { ok: false, reason: '当前没有高于本机版本的正式安装包。' };
      const downloaded = await downloadInstaller(officialPush);
      const openError = await shell.openPath(downloaded.targetPath);
      if (openError) return { ok: false, version: officialPush.version, fileName: downloaded.fileName, reason: `安装包已下载但无法打开：${openError}` };
      return { ok: true, version: officialPush.version, fileName: downloaded.fileName };
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
