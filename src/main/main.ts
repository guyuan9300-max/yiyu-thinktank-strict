import { createHash, randomBytes } from 'node:crypto';
import { lookup } from 'node:dns/promises';
import {
  appendFileSync,
  constants as fsConstants,
  createWriteStream,
  existsSync,
  lstatSync,
  mkdirSync,
  readlinkSync,
  realpathSync,
  rmSync,
  symlinkSync,
} from 'node:fs';
import {
  access,
  chmod,
  copyFile,
  readFile,
  writeFile,
} from 'node:fs/promises';
import { createServer, isIP } from 'node:net';
import path from 'node:path';
import { execFileSync, spawn, type ChildProcess } from 'node:child_process';
import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  screen,
  shell,
} from 'electron';
import {
  fastForwardStrictMain,
  getStrictCollabRepoStatus,
  previewStrictPull,
  previewStrictPush,
  publishStrictBranch,
  pushStrictMain,
  maintenanceEnvironment,
  resolveMaintenanceNpm,
  resolveMaintenanceUv,
  resolveStrictRepository,
} from './strictCollabGit.js';
import {
  setOfficialUpdateIdentity,
  setupOfficialUpdater,
} from './officialUpdater.js';
import {
  MAIN_WINDOW_ASPECT_RATIO,
  MAIN_WINDOW_DEFAULT_SIZE,
  MAIN_WINDOW_MINIMUM_SIZE,
  normalizeMainWindowBounds,
  resolveMainWindowMinimumSize,
} from './mainWindowLayout.js';
import { buildMiniWindowBounds } from './miniWindowLayout.js';
import type {
  FastForwardMainPayload,
  PublishCollabBranchPayload,
  PushMainPayload,
  StrictRebuildInstallResult,
  UpdateOrgIdentity,
} from '../shared/types.js';

const APP_NAME = '益语智库AI（新版）';
const DATA_DIR_NAME = 'YiyuThinkTankStrictV1';
const APP_BUNDLE_ID = 'com.yiyu.thinktank.strict';

let mainWindow: BrowserWindow | null = null;
let preMiniWindowState: {
  bounds: { x: number; y: number; width: number; height: number };
  maximized: boolean;
  fullScreen: boolean;
  alwaysOnTop: boolean;
} | null = null;
let mainWindowResizeGuardTimer: ReturnType<typeof setTimeout> | null = null;
let backendProcess: ChildProcess | null = null;
let backendPort = 0;
let desktopToken = '';

// Keep Chromium/native controls deterministic across colleagues' macOS region
// settings. Business timestamps remain ISO values; this only fixes the UI
// locale so a 12-hour system preference cannot silently turn 12:00 into 0:00.
app.commandLine.appendSwitch('lang', 'zh-CN');
app.setName(APP_NAME);
const explicitDataDir = process.env.YIYU_STRICT_DESKTOP_DATA_DIR?.trim();
app.setPath(
  'userData',
  explicitDataDir || path.join(app.getPath('appData'), DATA_DIR_NAME),
);

function readBundleIdentity(bundlePath: string): { bundleId: string; version: string } | null {
  const plistPath = path.join(bundlePath, 'Contents', 'Info.plist');
  if (!existsSync(plistPath)) return null;
  try {
    const read = (key: string) => execFileSync(
      '/usr/libexec/PlistBuddy',
      ['-c', `Print :${key}`, plistPath],
      { encoding: 'utf8', timeout: 3_000 },
    ).trim();
    const bundleId = read('CFBundleIdentifier');
    const version = read('CFBundleShortVersionString');
    return bundleId && version ? { bundleId, version } : null;
  } catch {
    return null;
  }
}

function compareReleaseVersions(left: string, right: string): number {
  const parse = (value: string) => value.split('.').map((part) => Number(part) || 0);
  const a = parse(left);
  const b = parse(right);
  for (let index = 0; index < Math.max(a.length, b.length); index += 1) {
    const delta = (a[index] || 0) - (b[index] || 0);
    if (delta !== 0) return delta;
  }
  return 0;
}

/**
 * Finder can leave an old bundle in ~/Applications while a DMG replaces the
 * canonical /Applications bundle. Once the canonical new bundle is genuinely
 * running, remove only an older/equal bundle with this exact bundle id and turn
 * its former path into a redirect. Old Dock entries then open the new bundle.
 */
function convergeDuplicateMacInstallations(): void {
  if (!app.isPackaged || process.platform !== 'darwin') return;
  const currentBundlePath = path.resolve(path.dirname(process.execPath), '..', '..');
  const canonicalBundlePath = path.join('/Applications', `${APP_NAME}.app`);
  if (currentBundlePath !== canonicalBundlePath) return;

  const currentIdentity = readBundleIdentity(currentBundlePath);
  if (!currentIdentity || currentIdentity.bundleId !== APP_BUNDLE_ID) return;
  const legacyBundlePath = path.join(app.getPath('home'), 'Applications', `${APP_NAME}.app`);
  if (!existsSync(legacyBundlePath)) return;
  const logPath = path.join(app.getPath('userData'), 'runtime', 'logs', 'install-convergence.log');

  try {
    const stat = lstatSync(legacyBundlePath);
    if (stat.isSymbolicLink()) {
      const target = path.resolve(path.dirname(legacyBundlePath), readlinkSync(legacyBundlePath));
      if (realpathSync(target) === realpathSync(currentBundlePath)) return;
    }
    const legacyIdentity = readBundleIdentity(legacyBundlePath);
    if (!legacyIdentity || legacyIdentity.bundleId !== APP_BUNDLE_ID) return;
    if (compareReleaseVersions(legacyIdentity.version, currentIdentity.version) > 0) return;

    rmSync(legacyBundlePath, { recursive: true, force: true });
    symlinkSync(currentBundlePath, legacyBundlePath, 'dir');
    mkdirSync(path.dirname(logPath), { recursive: true });
    appendFileSync(
      logPath,
      `[${new Date().toISOString()}] running=${currentIdentity.version} removed=${legacyIdentity.version} redirected=${legacyBundlePath}\n`,
      'utf8',
    );
  } catch (error) {
    mkdirSync(path.dirname(logPath), { recursive: true });
    appendFileSync(
      logPath,
      `[${new Date().toISOString()}] convergence-failed ${error instanceof Error ? error.message : String(error)}\n`,
      'utf8',
    );
  }
}

function runUpdateCommand(
  command: string,
  args: string[],
  cwd: string,
  logPath: string,
  timeoutMs: number,
  extraPathDirectories: string[] = [],
): Promise<void> {
  return new Promise((resolve, reject) => {
    const logStream = createWriteStream(logPath, { flags: 'a' });
    const baseEnvironment = maintenanceEnvironment();
    const commandEnvironment = {
      ...baseEnvironment,
      PATH: [...new Set([
        ...extraPathDirectories,
        ...(baseEnvironment.PATH || '').split(path.delimiter).filter(Boolean),
      ])].join(path.delimiter),
    };
    const child = spawn(command, args, {
      cwd,
      env: commandEnvironment,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    child.stdout?.pipe(logStream, { end: false });
    child.stderr?.pipe(logStream, { end: false });
    let settled = false;
    const finish = (error?: Error) => {
      if (settled) return;
      settled = true;
      logStream.end();
      if (error) reject(error);
      else resolve();
    };
    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      finish(new Error('自动更新构建超时，请查看更新日志后重试。'));
    }, timeoutMs);
    child.once('error', (error) => {
      clearTimeout(timer);
      finish(error);
    });
    child.once('close', (code) => {
      clearTimeout(timer);
      finish(code === 0 ? undefined : new Error(`自动更新命令失败（退出码 ${code ?? '未知'}），请查看更新日志。`));
    });
  });
}

async function rebuildAndInstallStrictApp(repoPath: string): Promise<StrictRebuildInstallResult> {
  if (!app.isPackaged) {
    return {
      state: 'not_packaged',
      installed: false,
      message: '当前是开发版，源码更新会由开发进程直接加载，无需覆盖安装。',
    };
  }
  if (process.platform !== 'darwin' || process.arch !== 'arm64') {
    throw new Error('当前自动覆盖安装仅支持 Apple Silicon 版 macOS。');
  }

  const resolution = await resolveStrictRepository(
    repoPath,
    app.getPath('userData'),
    app.getAppPath(),
  );
  if (!resolution.repoPath) {
    throw new Error(resolution.error || '严格新版源码目录无效。');
  }
  const status = await getStrictCollabRepoStatus(
    resolution.repoPath,
    app.getPath('userData'),
    app.getAppPath(),
  );
  if (!status.isMainBranch || status.hasUnmergedPaths || status.hasLocalChanges) {
    throw new Error('自动更新只允许使用无冲突、无未提交修改的 main 源码。');
  }
  if ((status.aheadCount || 0) !== 0 || (status.behindCount || 0) !== 0) {
    throw new Error('源码尚未与 GitHub main 完全同步，不能覆盖安装。');
  }

  const currentBundlePath = path.resolve(path.dirname(process.execPath), '..', '..');
  if (path.dirname(currentBundlePath) !== '/Applications' || path.extname(currentBundlePath) !== '.app') {
    throw new Error('请先把益语智库AI（新版）安装到 /Applications，再使用自动更新。');
  }

  const updateId = new Date().toISOString().replace(/[^0-9]/g, '');
  const updateRoot = path.join(app.getPath('userData'), 'updates', updateId);
  const logPath = path.join(updateRoot, 'update.log');
  mkdirSync(updateRoot, { recursive: true });
  await writeFile(
    logPath,
    `${new Date().toISOString()} start ${resolution.repoPath}\n`,
    { encoding: 'utf8', mode: 0o600 },
  );

  let npmPath: string;
  let uvPath: string;
  try {
    [npmPath, uvPath] = await Promise.all([
      resolveMaintenanceNpm(),
      resolveMaintenanceUv(),
    ]);
  } catch (error) {
    const message = error instanceof Error ? error.message : '本机缺少源码构建环境。';
    await writeFile(logPath, `${new Date().toISOString()} toolchain blocked: ${message}\n`, { flag: 'a' });
    return {
      state: 'blocked_missing_toolchain',
      installed: false,
      message: `源码已经接收，但不能在本机自动构建：${message}`,
      logPath,
    };
  }

  const toolDirectories = [path.dirname(npmPath), path.dirname(uvPath)];
  try {
    await access(path.join(resolution.repoPath, 'node_modules', '.bin', 'electron-builder'), fsConstants.X_OK);
  } catch {
    await runUpdateCommand(
      npmPath,
      ['ci'],
      resolution.repoPath,
      logPath,
      20 * 60_000,
      toolDirectories,
    );
  }

  await runUpdateCommand(
    npmPath,
    ['run', 'dist:mac-local'],
    resolution.repoPath,
    logPath,
    30 * 60_000,
    toolDirectories,
  );

  const candidatePath = path.join(
    resolution.repoPath,
    'dist',
    'mac-arm64',
    `${APP_NAME}.app`,
  );
  await access(path.join(candidatePath, 'Contents', 'Info.plist'));

  try {
    await access('/Applications', fsConstants.W_OK);
  } catch {
    shell.showItemInFolder(candidatePath);
    return {
      state: 'built_admin_required',
      installed: false,
      message: '源码已更新并构建完成，但当前 macOS 账号无权覆盖 /Applications。已在 Finder 中显示构建产物，请由电脑管理员完成覆盖安装。',
      artifactPath: candidatePath,
      logPath,
    };
  }

  const stagedBundlePath = path.join('/Applications', `.${APP_NAME}.update-${updateId}.app`);
  await runUpdateCommand(
    '/usr/bin/ditto',
    [candidatePath, stagedBundlePath],
    resolution.repoPath,
    logPath,
    5 * 60_000,
  );
  await access(path.join(stagedBundlePath, 'Contents', 'Info.plist'));

  const backupBundlePath = path.join('/Applications', `.${APP_NAME}.previous.app`);
  const updaterScriptPath = path.join(updateRoot, 'install-update.sh');
  await writeFile(
    updaterScriptPath,
    `#!/bin/sh
set -eu
STAGED="$1"
TARGET="$2"
BACKUP="$3"
OLD_PID="$4"
LOG_FILE="$5"
exec >>"$LOG_FILE" 2>&1
case "$STAGED" in /Applications/*.app) ;; *) exit 64 ;; esac
case "$TARGET" in /Applications/*.app) ;; *) exit 64 ;; esac
COUNT=0
while kill -0 "$OLD_PID" 2>/dev/null; do
  COUNT=$((COUNT + 1))
  [ "$COUNT" -ge 300 ] && exit 70
  sleep 0.2
done
[ -e "$BACKUP" ] && /bin/rm -rf "$BACKUP"
[ -e "$TARGET" ] && /bin/mv "$TARGET" "$BACKUP"
if /bin/mv "$STAGED" "$TARGET"; then
  /usr/bin/xattr -dr com.apple.quarantine "$TARGET" 2>/dev/null || true
  /usr/bin/open "$TARGET"
  exit 0
fi
[ ! -e "$TARGET" ] && [ -e "$BACKUP" ] && /bin/mv "$BACKUP" "$TARGET"
/usr/bin/open "$TARGET" 2>/dev/null || true
exit 71
`,
    { encoding: 'utf8', mode: 0o700 },
  );
  await chmod(updaterScriptPath, 0o700);

  const updater = spawn(
    '/bin/sh',
    [
      updaterScriptPath,
      stagedBundlePath,
      currentBundlePath,
      backupBundlePath,
      String(process.pid),
      logPath,
    ],
    { detached: true, stdio: 'ignore' },
  );
  updater.unref();
  setTimeout(() => app.quit(), 300);
  return {
    state: 'installing',
    installed: true,
    message: '构建完成，正在覆盖安装并重启软件。',
    artifactPath: candidatePath,
    logPath,
  };
}

const singleInstance = app.requestSingleInstanceLock();
if (!singleInstance) {
  app.quit();
}

function availablePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      if (!address || typeof address === 'string') {
        server.close();
        reject(new Error('无法分配本地端口'));
        return;
      }
      const port = address.port;
      server.close((error) => {
        if (error) {
          reject(error);
          return;
        }
        resolve(port);
      });
    });
  });
}

function normalizedWebsiteHost(hostname: string): string {
  return hostname.trim().toLowerCase().replace(/^www\./, '');
}

function privateNetworkAddress(address: string): boolean {
  if (isIP(address) === 4) {
    const parts = address.split('.').map(Number);
    return parts[0] === 10
      || parts[0] === 127
      || (parts[0] === 169 && parts[1] === 254)
      || (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31)
      || (parts[0] === 192 && parts[1] === 168)
      || parts[0] === 0
      || parts[0] >= 224;
  }
  if (isIP(address) === 6) {
    const normalized = address.toLowerCase();
    return normalized === '::1'
      || normalized === '::'
      || normalized.startsWith('fc')
      || normalized.startsWith('fd')
      || /^fe[89ab]/.test(normalized)
      || normalized.startsWith('ff');
  }
  return true;
}

async function publicWebsiteUrl(value: string): Promise<URL> {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error('官网地址格式无效');
  }
  if (!['http:', 'https:'].includes(parsed.protocol) || !parsed.hostname) {
    throw new Error('官网地址必须使用公开的 HTTP 或 HTTPS');
  }
  const addresses = await lookup(parsed.hostname, { all: true });
  if (!addresses.length || addresses.some((item) => privateNetworkAddress(item.address))) {
    throw new Error('官网地址不能指向本机或内网');
  }
  parsed.hash = '';
  return parsed;
}

type RenderedWebsitePage = {
  title: string;
  url: string;
  text: string;
  contentHash: string;
  capturedAt: string;
  discoveredUrl: string;
  canonicalPublicUrl: string;
};

function officialWebsiteLinkPriority(rawUrl: string): [number, number, number, string] {
  const parsed = new URL(rawUrl);
  const route = String(parsed.searchParams.get('page') || '').toLowerCase();
  const path = parsed.pathname.replace(/^\/+|\/+$/g, '').toLowerCase();
  const haystack = `${route} ${path}`;
  const groups: Array<[number, string[]]> = [
    [0, ['about', 'about-us', 'guanyu', 'organization-profile']],
    [1, ['team', 'people', 'governance']],
    [2, ['project', 'program', 'service']],
    [3, ['mission', 'vision', 'history']],
    [4, ['contact', 'workbench']],
    [8, ['report', 'article', 'news']],
  ];
  let category = !path && !parsed.search ? -1 : 12;
  for (const [rank, terms] of groups) {
    if (terms.some((term) => haystack.includes(term))) {
      category = rank;
      break;
    }
  }
  const isRoot = !path && !parsed.search;
  const crawlOnly = isRoot ? -1 : path.startsWith('share/') ? 2 : path.startsWith('seo/') ? 1 : 0;
  const depth = path.split('/').filter(Boolean).length;
  return [crawlOnly, category, depth, rawUrl.toLowerCase()];
}

function compareOfficialWebsiteLinks(left: string, right: string): number {
  const a = officialWebsiteLinkPriority(left);
  const b = officialWebsiteLinkPriority(right);
  for (let index = 0; index < a.length; index += 1) {
    if (a[index] < b[index]) return -1;
    if (a[index] > b[index]) return 1;
  }
  return 0;
}

async function renderOfficialWebsite(targetUrl: string): Promise<{
  pages: RenderedWebsitePage[];
  state: 'ready' | 'failed_retryable' | 'blocked';
  message: string;
}> {
  const root = await publicWebsiteUrl(targetUrl);
  const rootHost = normalizedWebsiteHost(root.hostname);
  const partition = `yiyu-official-capture-${randomBytes(8).toString('hex')}`;
  const hidden = new BrowserWindow({
    show: false,
    width: 1280,
    height: 900,
    webPreferences: {
      partition,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });
  const captureSession = hidden.webContents.session;
  hidden.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  hidden.webContents.session.on('will-download', (event) => event.preventDefault());
  hidden.webContents.session.webRequest.onBeforeRequest(
    { urls: ['http://*/*', 'https://*/*'] },
    (details, callback) => {
      void publicWebsiteUrl(details.url)
        .then(() => callback({ cancel: false }))
        .catch(() => callback({ cancel: true }));
    },
  );

  const queue = [root.toString()];
  const seen = new Set<string>();
  const bodyHashes = new Set<string>();
  const pages: RenderedWebsitePage[] = [];
  const deadline = Date.now() + 90_000;
  try {
    while (queue.length && pages.length < 16 && Date.now() < deadline) {
      const requested = queue.shift()!;
      const key = requested.replace(/\/$/, '').toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      try {
        await hidden.loadURL(requested, { userAgent: 'YiyuThinkTankStrict/1.0' });
      } catch {
        continue;
      }
      let previousSize = -1;
      let stableCount = 0;
      const settleDeadline = Date.now() + 12_000;
      while (Date.now() < settleDeadline && stableCount < 3) {
        await new Promise((resolve) => setTimeout(resolve, 500));
        const size = await hidden.webContents.executeJavaScript(
          'document.body ? document.body.innerText.length : 0',
          true,
        ) as number;
        if (size === previousSize && size >= 80) stableCount += 1;
        else stableCount = 0;
        previousSize = size;
      }
      const snapshot = await hidden.webContents.executeJavaScript(`(() => {
        const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
        return {
          title: clean(document.title),
          url: location.href,
          canonicalUrl: clean(
            document.querySelector('link[rel="canonical"]')?.getAttribute('href')
            || document.querySelector('meta[property="og:url"]')?.getAttribute('content')
            || '',
          ),
          text: clean(document.body ? document.body.innerText : '').slice(0, 12000),
          links: Array.from(document.querySelectorAll('a[href]'))
            .map((node) => node.href)
            .filter(Boolean)
            .slice(0, 300),
        };
      })()`, true) as { title: string; url: string; canonicalUrl: string; text: string; links: string[] };
      const finalUrl = await publicWebsiteUrl(snapshot.url);
      if (normalizedWebsiteHost(finalUrl.hostname) !== rootHost) continue;
      let canonicalPublicUrl = '';
      try {
        const canonical = await publicWebsiteUrl(snapshot.canonicalUrl || finalUrl.toString());
        const canonicalPath = canonical.pathname.replace(/^\/+|\/+$/g, '').toLowerCase();
        if (
          normalizedWebsiteHost(canonical.hostname) === rootHost
          && !canonicalPath.startsWith('seo/')
          && !canonicalPath.includes('sitemap')
        ) {
          canonicalPublicUrl = canonical.toString();
        }
      } catch {
        // Crawl mirrors may not expose a verified user-facing URL. Keep the
        // captured evidence, but do not invent a public link for the user.
      }
      const looksMissing = /(?:页面不存在|未找到页面|\b404\b|\bnot found\b)/i.test(
        `${snapshot.title}\n${snapshot.text.slice(0, 180)}`,
      );
      if (snapshot.text.length >= 100 && !looksMissing) {
        const bodyHash = createHash('sha256').update(`${snapshot.title}\n${snapshot.text}`).digest('hex');
        if (!bodyHashes.has(bodyHash)) {
          bodyHashes.add(bodyHash);
          pages.push({
            title: snapshot.title || finalUrl.pathname.split('/').filter(Boolean).pop() || rootHost,
            url: finalUrl.toString(),
            text: snapshot.text,
            contentHash: createHash('sha256').update(`${snapshot.title}\n${snapshot.text}\n${finalUrl.toString()}`).digest('hex'),
            capturedAt: new Date().toISOString(),
            discoveredUrl: requested,
            canonicalPublicUrl,
          });
        }
      }
      const prioritizedLinks = [...new Set(snapshot.links)].sort(compareOfficialWebsiteLinks);
      for (const rawLink of prioritizedLinks) {
        try {
          const link = await publicWebsiteUrl(rawLink);
          if (normalizedWebsiteHost(link.hostname) !== rootHost) continue;
          if (/\.(?:jpg|jpeg|png|gif|svg|pdf|zip|docx?|xlsx?)(?:$|\?)/i.test(link.pathname)) continue;
          const candidate = link.toString();
          const candidateKey = candidate.replace(/\/$/, '').toLowerCase();
          if (!seen.has(candidateKey) && !queue.includes(candidate)) queue.push(candidate);
        } catch {
          // A page may contain mailto, local-development or malformed links.
        }
      }
    }
  } finally {
    hidden.destroy();
    await captureSession.clearStorageData().catch(() => undefined);
  }
  if (!pages.length) {
    return { pages: [], state: 'failed_retryable', message: '动态官网没有返回可读取正文，可稍后重试' };
  }
  return {
    pages,
    state: 'ready',
    message: `已等待官网加载完成并读取 ${pages.length} 个动态页面`,
  };
}

function backendCommand(): { command: string; args: string[] } {
  if (app.isPackaged) {
    const executable = path.join(
      process.resourcesPath,
      'backend-dist',
      process.platform === 'win32'
        ? 'yiyu-strict-backend.exe'
        : 'yiyu-strict-backend',
    );
    return { command: executable, args: [] };
  }
  const projectRoot = app.getAppPath();
  const python = path.join(
    projectRoot,
    '.venv',
    process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
  );
  return {
    command: python,
    args: ['-m', 'backend.app.main'],
  };
}

async function waitForBackend(port: number): Promise<void> {
  const deadline = Date.now() + 30_000;
  let lastError = '本地后端尚未响应';
  while (Date.now() < deadline) {
    if (backendProcess?.exitCode !== null && backendProcess?.exitCode !== undefined) {
      throw new Error(`本地后端已退出，退出码=${backendProcess.exitCode}`);
    }
    try {
      const response = await fetch(`http://127.0.0.1:${port}/api/v2/health`);
      if (response.ok) {
        const payload = await response.json() as { apiVersion?: string };
        if (payload.apiVersion !== 'v2') {
          throw new Error('本地后端没有返回严格 v2 身份');
        }
        return;
      }
      lastError = `本地后端状态码 ${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, 180));
  }
  throw new Error(`本地后端启动超时：${lastError}`);
}

async function startBackend(): Promise<void> {
  backendPort = await availablePort();
  desktopToken = randomBytes(32).toString('base64url');
  const dataDir = app.getPath('userData');
  const logDir = path.join(dataDir, 'logs');
  mkdirSync(logDir, { recursive: true });
  const logStream = createWriteStream(path.join(logDir, 'strict-backend.log'), {
    flags: 'a',
  });
  const { command, args } = backendCommand();
  backendProcess = spawn(command, args, {
    cwd: app.isPackaged ? path.dirname(command) : app.getAppPath(),
    env: {
      ...process.env,
      PYTHONUNBUFFERED: '1',
      YIYU_STRICT_DATA_DIR: dataDir,
      YIYU_STRICT_LOCAL_API_TOKEN: desktopToken,
      YIYU_STRICT_LOCAL_HOST: '127.0.0.1',
      YIYU_STRICT_LOCAL_PORT: String(backendPort),
      YIYU_STRICT_SECRET_NAMESPACE: 'com.yiyu.thinktank.strict.v1',
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  backendProcess.stdout?.pipe(logStream);
  backendProcess.stderr?.pipe(logStream);
  await waitForBackend(backendPort);
}

function createWindow(): void {
  const developmentUrl = process.env.VITE_DEV_SERVER_URL;
  mainWindow = new BrowserWindow({
    title: APP_NAME,
    width: MAIN_WINDOW_DEFAULT_SIZE.width,
    height: MAIN_WINDOW_DEFAULT_SIZE.height,
    minWidth: MAIN_WINDOW_MINIMUM_SIZE.width,
    minHeight: MAIN_WINDOW_MINIMUM_SIZE.height,
    backgroundColor: '#f4f6f9',
    // Keep the development window observable even while Vite compiles the
    // large renderer; packaged builds still wait for ready-to-show.
    show: Boolean(developmentUrl),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  applyMainWindowLayout(mainWindow);
  mainWindow.on('resize', () => {
    scheduleMainWindowLayoutGuard(mainWindow);
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: 'deny' };
  });
  setupOfficialUpdater(mainWindow);
  mainWindow.once('ready-to-show', () => {
    mainWindow?.show();
  });
  if (developmentUrl) {
    void mainWindow.loadURL(developmentUrl);
  } else {
    void mainWindow.loadFile(
      path.join(app.getAppPath(), 'dist', 'renderer', 'index.html'),
    );
  }
  mainWindow.once('closed', () => {
    if (mainWindowResizeGuardTimer) {
      clearTimeout(mainWindowResizeGuardTimer);
      mainWindowResizeGuardTimer = null;
    }
    mainWindow = null;
    preMiniWindowState = null;
  });
}

function scheduleMainWindowLayoutGuard(window: BrowserWindow | null): void {
  if (
    !window
    || window.isDestroyed()
    || preMiniWindowState
    || window.isMaximized()
    || window.isFullScreen()
  ) {
    return;
  }
  if (mainWindowResizeGuardTimer) {
    clearTimeout(mainWindowResizeGuardTimer);
  }
  // Native aspect/minimum constraints cover ordinary dragging. This trailing
  // guard also repairs accessibility tools and delayed macOS resize callbacks
  // that can temporarily bypass those native constraints.
  mainWindowResizeGuardTimer = setTimeout(() => {
    mainWindowResizeGuardTimer = null;
    if (
      window.isDestroyed()
      || preMiniWindowState
      || window.isMaximized()
      || window.isFullScreen()
    ) {
      return;
    }
    const currentBounds = window.getBounds();
    const display = screen.getDisplayMatching(currentBounds);
    const normalizedBounds = normalizeMainWindowBounds(
      currentBounds,
      display.workArea,
    );
    if (
      currentBounds.x !== normalizedBounds.x
      || currentBounds.y !== normalizedBounds.y
      || currentBounds.width !== normalizedBounds.width
      || currentBounds.height !== normalizedBounds.height
    ) {
      applyMainWindowLayout(window, currentBounds);
    }
  }, 120);
}

function centeredDefaultMainWindowBounds(window: BrowserWindow): Electron.Rectangle {
  const workArea = screen.getDisplayMatching(window.getBounds()).workArea;
  return {
    x: workArea.x + Math.round((workArea.width - MAIN_WINDOW_DEFAULT_SIZE.width) / 2),
    y: workArea.y + Math.round((workArea.height - MAIN_WINDOW_DEFAULT_SIZE.height) / 2),
    ...MAIN_WINDOW_DEFAULT_SIZE,
  };
}

function applyMainWindowLayout(
  window: BrowserWindow,
  requestedBounds: Electron.Rectangle = window.getBounds(),
): void {
  const display = screen.getDisplayMatching(requestedBounds);
  const minimumSize = resolveMainWindowMinimumSize(display.workArea);
  const bounds = normalizeMainWindowBounds(requestedBounds, display.workArea);

  // Mini mode intentionally uses a tall portrait window. Clear its temporary
  // constraints before restoring the full workspace, then lock the full UI to
  // the design baseline so users cannot flatten the typography by dragging.
  window.setAspectRatio(0);
  window.setMinimumSize(1, 1);
  window.setBounds(bounds, true);
  window.setMinimumSize(minimumSize.width, minimumSize.height);
  window.setAspectRatio(MAIN_WINDOW_ASPECT_RATIO);
}

function applyMiniWindowLayout(window: BrowserWindow): void {
  const display = screen.getDisplayMatching(window.getBounds());
  const sourceWindowHeight = preMiniWindowState?.bounds.height
    ?? window.getBounds().height;
  const bounds = buildMiniWindowBounds(display.workArea, sourceWindowHeight);
  window.setAspectRatio(0);
  window.setMinimumSize(
    Math.min(360, bounds.width),
    Math.min(320, bounds.height),
  );
  window.setBounds(bounds, true);
  if (process.platform === 'darwin') {
    window.setAlwaysOnTop(true, 'floating');
  } else {
    window.setAlwaysOnTop(true);
  }
}

function reflowMiniWindow(): void {
  if (!mainWindow || mainWindow.isDestroyed() || !preMiniWindowState) {
    return;
  }
  applyMiniWindowLayout(mainWindow);
}

ipcMain.handle('strict:get-runtime', () => ({
  apiBaseUrl: `http://127.0.0.1:${backendPort}`,
  desktopToken,
  appName: APP_NAME,
  appVersion: app.getVersion(),
}));

ipcMain.on('strict:get-runtime-sync', (event) => {
  event.returnValue = {
    apiBaseUrl: `http://127.0.0.1:${backendPort}`,
    desktopToken,
    appName: APP_NAME,
    appVersion: app.getVersion(),
  };
});

ipcMain.handle('strict:set-mini-mode', async (_event, enter: boolean, _requestedHeight?: number) => {
  if (!mainWindow) {
    return { mini: false };
  }
  const window = mainWindow;
  if (enter) {
    // 已经处于精简模式时只是“今天/日历”切页，重新按当前显示器排版；不能覆盖
    // preMiniWindowState，否则退出精简模式时无法回到原窗口。
    if (preMiniWindowState) {
      applyMiniWindowLayout(window);
      return { mini: true };
    }
    preMiniWindowState = {
      bounds: window.getBounds(),
      maximized: window.isMaximized(),
      fullScreen: window.isFullScreen(),
      alwaysOnTop: window.isAlwaysOnTop(),
    };
    // macOS 离开原生全屏是异步的。过去在 setFullScreen(false) 后立即
    // setSize，尺寸请求会被系统吞掉，于是迷你内容被拉伸在整块全屏里。
    // 等窗口真正退出全屏/最大化后再设置固定窄窗尺寸。
    if (preMiniWindowState.fullScreen) {
      await new Promise<void>((resolve) => {
        let settled = false;
        const finish = () => {
          if (settled) return;
          settled = true;
          window.removeListener('leave-full-screen', finish);
          resolve();
        };
        window.once('leave-full-screen', finish);
        window.setFullScreen(false);
        setTimeout(finish, 1500);
      });
    }
    if (window.isMaximized()) {
      await new Promise<void>((resolve) => {
        let settled = false;
        const finish = () => {
          if (settled) return;
          settled = true;
          window.removeListener('unmaximize', finish);
          resolve();
        };
        window.once('unmaximize', finish);
        window.unmaximize();
        setTimeout(finish, 500);
      });
    }
    applyMiniWindowLayout(window);
  } else {
    const previous = preMiniWindowState;
    preMiniWindowState = null;
    window.setAlwaysOnTop(previous?.alwaysOnTop ?? false);
    applyMainWindowLayout(
      window,
      previous?.bounds ?? centeredDefaultMainWindowBounds(window),
    );
    if (previous?.maximized) window.maximize();
    if (previous?.fullScreen) window.setFullScreen(true);
  }
  return { mini: enter };
});

ipcMain.handle(
  'strict:set-update-org-identity',
  async (_event, identity: UpdateOrgIdentity | null) => {
    await setOfficialUpdateIdentity(identity);
    return { ok: true };
  },
);

ipcMain.handle(
  'strict:set-update-org-code',
  async (_event, organizationSlug: string | null) => {
    await setOfficialUpdateIdentity(organizationSlug ? { organizationSlug } : null);
    return { ok: true };
  },
);

ipcMain.handle('strict:get-desktop-app-info', () => ({
  appVersion: app.getVersion(),
  isPackaged: app.isPackaged,
  platform: process.platform,
  arch: process.arch,
  appBundlePath: app.getAppPath(),
  executablePath: process.execPath,
  releasePlanPath: '',
  releaseArtifactsPath: '',
  updateChannel: 'stable',
  updaterPhase: 'ready_for_in_app_update',
  recommendedInstallPath: process.platform === 'darwin' ? '/Applications' : '',
  installStatus: 'ok',
  installWarning: null,
  currentRendererEntry: null,
  currentRendererHash: null,
  backendSourceHash: null,
  startupGateStatus: 'ready',
  startupGateReason: '严格新版后端已启动。',
  installReceiptStatus: 'ready',
  installSmokeStatus: 'ready',
  detectedAppPaths: [],
  legacyAppPaths: [],
}));

ipcMain.handle('strict:render-official-website', async (_event, targetUrl: string) => {
  try {
    return await renderOfficialWebsite(String(targetUrl || '').trim());
  } catch (error) {
    return {
      pages: [],
      state: 'blocked',
      message: error instanceof Error ? error.message : '动态官网读取失败',
    };
  }
});

ipcMain.handle('strict:resume-startup-gate', () => ({
  resumed: true,
  loadMode: 'normal',
  appInfo: null,
}));

ipcMain.handle('strict:select-files', async () => {
  if (!mainWindow) {
    return [];
  }
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile', 'multiSelections'],
  });
  return result.canceled ? [] : result.filePaths;
});

ipcMain.handle('strict:select-folder', async () => {
  if (!mainWindow) {
    return null;
  }
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory', 'createDirectory'],
  });
  return result.canceled ? null : (result.filePaths[0] ?? null);
});

ipcMain.handle('strict:select-collab-repo', async () => {
  if (!mainWindow) return null;
  const result = await dialog.showOpenDialog(mainWindow, {
    title: '选择严格新版源码仓库',
    properties: ['openDirectory'],
  });
  if (result.canceled || !result.filePaths[0]) return null;
  const resolution = await resolveStrictRepository(
    result.filePaths[0],
    app.getPath('userData'),
    app.getAppPath(),
  );
  if (!resolution.repoPath) {
    throw new Error(
      resolution.error || '所选目录不是严格新版协作仓库。',
    );
  }
  return resolution.repoPath;
});

ipcMain.handle(
  'strict:get-collab-repo-status',
  (_event, repoPath?: string | null) =>
    getStrictCollabRepoStatus(
      repoPath,
      app.getPath('userData'),
      app.getAppPath(),
    ),
);

ipcMain.handle(
  'strict:preview-push-to-main',
  (_event, repoPath: string) => previewStrictPush(repoPath),
);

ipcMain.handle(
  'strict:push-safely-to-main',
  (_event, payload: PushMainPayload) => pushStrictMain(payload),
);

ipcMain.handle(
  'strict:publish-collab-branch',
  (_event, payload: PublishCollabBranchPayload) =>
    publishStrictBranch(payload),
);

ipcMain.handle(
  'strict:preview-pull-from-main',
  (_event, repoPath: string) => previewStrictPull(repoPath),
);

ipcMain.handle(
  'strict:fast-forward-main',
  (_event, payload: FastForwardMainPayload) =>
    fastForwardStrictMain(payload),
);

ipcMain.handle(
  'strict:rebuild-and-install-from-repo',
  (_event, repoPath: string) => rebuildAndInstallStrictApp(repoPath),
);

ipcMain.handle('strict:read-text-file', async (_event, targetPath: string) =>
  readFile(targetPath, 'utf8'));

ipcMain.handle('strict:open-path', async (_event, targetPath: string) => {
  const error = await shell.openPath(targetPath);
  return error.length === 0;
});

ipcMain.handle('strict:open-external-url', async (_event, targetUrl: string) => {
  await shell.openExternal(targetUrl);
  return true;
});

ipcMain.handle('strict:reveal-in-finder', (_event, targetPath: string) => {
  shell.showItemInFolder(targetPath);
  return true;
});

ipcMain.handle(
  'strict:save-file-as',
  async (_event, sourcePath: string, suggestedName?: string) => {
    if (!mainWindow) {
      return null;
    }
    const result = await dialog.showSaveDialog(mainWindow, {
      defaultPath: suggestedName || path.basename(sourcePath),
    });
    if (result.canceled || !result.filePath) {
      return null;
    }
    await copyFile(sourcePath, result.filePath);
    return result.filePath;
  },
);

ipcMain.handle('strict:quit-app', () => {
  app.quit();
  return true;
});

ipcMain.handle(
  'strict:save-recording-blob',
  async (
    _event,
    payload: {
      buffer: ArrayBuffer;
      extension?: string;
      sessionId?: string;
      scopeId?: string;
      suggestedBaseName?: string;
    },
  ) => {
    const sessionId = payload.sessionId || randomBytes(12).toString('hex');
    const extension = (payload.extension || 'webm').replace(/[^a-z0-9]/gi, '');
    const scopeId = (payload.scopeId || sessionId).replace(/[^a-z0-9._-]/gi, '').slice(0, 96) || sessionId;
    const normalizedBaseName = (payload.suggestedBaseName || '任务录音')
      .replace(/[\\/:*?"<>|\u0000-\u001f]/g, ' ')
      .replace(/\s+/g, ' ')
      .replace(/^[.\s]+|[.\s]+$/g, '');
    // 文件名只承载任务识别所需的短标题；任务、工作台和转写投影均以
    // 这个实际文件名为准，避免 UUID、日期后缀和三套显示名称漂移。
    const suggestedBaseName = Array.from(normalizedBaseName || '任务录音')
      .slice(0, 48)
      .join('')
      .replace(/[.\s]+$/g, '')
      || '任务录音';
    const directory = path.join(app.getPath('userData'), 'recordings', scopeId);
    mkdirSync(directory, { recursive: true });
    const absolutePath = path.join(directory, `${suggestedBaseName}.${extension}`);
    const bytes = Buffer.from(payload.buffer);
    await writeFile(absolutePath, bytes);
    return { absolutePath, sizeBytes: bytes.byteLength, sessionId };
  },
);

ipcMain.handle('strict:read-recording-file', async (_event, absolutePath: string) => {
  const bytes = await readFile(absolutePath);
  return {
    buffer: new Uint8Array(bytes),
    sizeBytes: bytes.byteLength,
    name: path.basename(absolutePath),
  };
});

app.on('second-instance', () => {
  if (!mainWindow || mainWindow.isDestroyed()) {
    mainWindow = null;
    return;
  }
  if (mainWindow.isMinimized()) {
    mainWindow.restore();
  }
  mainWindow.focus();
});

app.whenReady().then(async () => {
  try {
    screen.on('display-added', reflowMiniWindow);
    screen.on('display-removed', reflowMiniWindow);
    screen.on('display-metrics-changed', reflowMiniWindow);
    convergeDuplicateMacInstallations();
    await startBackend();
    createWindow();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const failure = new BrowserWindow({
      width: 620,
      height: 360,
      title: '本地后端启动失败',
      webPreferences: { sandbox: true },
    });
    const safeMessage = message.replace(/[&<>"']/g, (character) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    })[character] ?? character);
    await failure.loadURL(
      `data:text/html;charset=utf-8,${encodeURIComponent(
        `<main style="font-family:-apple-system;padding:40px"><h1>本地后端启动失败</h1><p>${safeMessage}</p><p>诊断日志：${path.join(app.getPath('userData'), 'logs', 'strict-backend.log')}</p></main>`,
      )}`,
    );
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0 && backendProcess) {
    createWindow();
  }
});

app.on('before-quit', () => {
  backendProcess?.kill('SIGTERM');
  backendProcess = null;
});
