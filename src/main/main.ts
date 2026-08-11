import { createHash, randomBytes } from 'node:crypto';
import { lookup } from 'node:dns/promises';
import { createWriteStream, mkdirSync } from 'node:fs';
import {
  copyFile,
  readFile,
  writeFile,
} from 'node:fs/promises';
import { createServer, isIP } from 'node:net';
import path from 'node:path';
import { spawn, type ChildProcess } from 'node:child_process';
import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  shell,
} from 'electron';
import {
  fastForwardStrictMain,
  getStrictCollabRepoStatus,
  previewStrictPull,
  previewStrictPush,
  publishStrictBranch,
  pushStrictMain,
  resolveStrictRepository,
} from './strictCollabGit.js';
import type {
  FastForwardMainPayload,
  PublishCollabBranchPayload,
  PushMainPayload,
} from '../shared/types.js';

const APP_NAME = '益语智库AI（新版）';
const DATA_DIR_NAME = 'YiyuThinkTankStrictV1';

let mainWindow: BrowserWindow | null = null;
let backendProcess: ChildProcess | null = null;
let backendPort = 0;
let desktopToken = '';

app.setName(APP_NAME);
const explicitDataDir = process.env.YIYU_STRICT_DESKTOP_DATA_DIR?.trim();
app.setPath(
  'userData',
  explicitDataDir || path.join(app.getPath('appData'), DATA_DIR_NAME),
);

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
  mainWindow = new BrowserWindow({
    title: APP_NAME,
    width: 1440,
    height: 900,
    minWidth: 1080,
    minHeight: 700,
    backgroundColor: '#f4f6f9',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.once('ready-to-show', () => {
    mainWindow?.show();
  });
  const developmentUrl = process.env.VITE_DEV_SERVER_URL;
  if (developmentUrl) {
    void mainWindow.loadURL(developmentUrl);
  } else {
    void mainWindow.loadFile(
      path.join(app.getAppPath(), 'dist', 'renderer', 'index.html'),
    );
  }
  mainWindow.once('closed', () => {
    mainWindow = null;
  });
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

ipcMain.handle('strict:set-mini-mode', (_event, enter: boolean) => {
  if (!mainWindow) {
    return { mini: false };
  }
  if (enter) {
    mainWindow.setMinimumSize(360, 560);
    mainWindow.setSize(420, 720, true);
  } else {
    mainWindow.setMinimumSize(1080, 700);
    mainWindow.setSize(1440, 900, true);
  }
  return { mini: enter };
});

ipcMain.handle('strict:get-desktop-app-info', () => ({
  appVersion: app.getVersion(),
  isPackaged: app.isPackaged,
  platform: process.platform,
  arch: process.arch,
  appBundlePath: app.getAppPath(),
  executablePath: process.execPath,
  releasePlanPath: '',
  releaseArtifactsPath: '',
  updateChannel: 'strict',
  updaterPhase: 'manual',
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
    payload: { buffer: ArrayBuffer; extension?: string; sessionId?: string },
  ) => {
    const sessionId = payload.sessionId || randomBytes(12).toString('hex');
    const extension = (payload.extension || 'webm').replace(/[^a-z0-9]/gi, '');
    const directory = path.join(app.getPath('userData'), 'recordings');
    mkdirSync(directory, { recursive: true });
    const absolutePath = path.join(directory, `${sessionId}.${extension}`);
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
