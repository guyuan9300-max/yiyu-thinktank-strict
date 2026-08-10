import { execFile, spawn } from 'node:child_process';
import { readFile, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';
import type {
  CollabActionResult,
  CollabChangeGroup,
  CollabChangeGroupKey,
  CollabEffectPreview,
  CollabFileChange,
  CollabFileChangeType,
  CollabRemoteCommit,
  CollabRepoStatus,
  FastForwardMainPayload,
  PublishCollabBranchPayload,
  PullPreview,
  PushMainPayload,
  PushPreview,
} from '../shared/types.js';

const execFileAsync = promisify(execFile);

export const STRICT_REPOSITORY = {
  markerFile: '.yiyu-strict-repository.json',
  markerKind: 'yiyu-strict-repository',
  genesisLabel: 'blueprint-88-foundation-v8-agent-skill-contract',
  githubRepository: 'guyuan9300-max/yiyu-thinktank-strict',
  githubRepositoryNumericId: 1316010273,
  githubRepositoryNodeId: 'R_kgDOTnC5IQ',
  remoteUrl: 'https://github.com/guyuan9300-max/yiyu-thinktank-strict.git',
  targetBranch: 'main',
} as const;

type RepositoryMarker = {
  formatVersion: number;
  kind: string;
  genesisLabel: string;
  githubRepository: string;
  githubRepositoryNumericId: number;
  githubRepositoryNodeId: string;
  remoteUrl: string;
  targetBranch: string;
  localManifestHash: string;
  cloudManifestHash: string;
};

type RepositoryResolution = {
  repoPath: string | null;
  suggestedRepoPath: string | null;
  error: string | null;
};

const GROUP_LABELS: Record<CollabChangeGroupKey, string> = {
  shared_settings: '共享配置',
  renderer: '前台界面',
  desktop_shell: '桌面程序',
  local_backend: '本地后端',
  cloud_backend: '组织云端',
  scripts_docs: '检查、脚本与文档',
  other: '其他文件',
};

let githubIdentityVerifiedAt = 0;

async function run(
  repoPath: string,
  args: string[],
  options: { allowFailure?: boolean; timeoutMs?: number } = {},
): Promise<string> {
  try {
    const result = await execFileAsync('git', args, {
      cwd: repoPath,
      encoding: 'utf8',
      env: {
        ...process.env,
        GIT_TERMINAL_PROMPT: '0',
        GIT_OPTIONAL_LOCKS: '0',
      },
      maxBuffer: 16 * 1024 * 1024,
      timeout: options.timeoutMs ?? 120_000,
    });
    return result.stdout.trim();
  } catch (error) {
    if (options.allowFailure) return '';
    const detail = error as {
      stderr?: string;
      stdout?: string;
      message?: string;
    };
    const message = detail.stderr?.trim()
      || detail.stdout?.trim()
      || detail.message
      || 'Git 命令执行失败';
    throw new Error(message);
  }
}

async function runCommand(
  repoPath: string,
  command: string,
  args: string[],
  timeoutMs = 30 * 60_000,
): Promise<void> {
  try {
    await execFileAsync(command, args, {
      cwd: repoPath,
      encoding: 'utf8',
      env: {
        ...process.env,
        GIT_TERMINAL_PROMPT: '0',
      },
      maxBuffer: 32 * 1024 * 1024,
      timeout: timeoutMs,
    });
  } catch (error) {
    const detail = error as {
      stderr?: string;
      stdout?: string;
      message?: string;
    };
    const message = detail.stderr?.trim()
      || detail.stdout?.trim()
      || detail.message
      || `${command} 执行失败`;
    throw new Error(message);
  }
}

async function runNetworkGit(
  repoPath: string,
  args: string[],
  options: {
    action: string;
    attempts: number;
    timeoutMs: number;
  },
): Promise<string> {
  let lastError = '';
  for (let attempt = 1; attempt <= options.attempts; attempt += 1) {
    try {
      return await runNetworkGitProcess(repoPath, args, options.timeoutMs);
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
      if (attempt < options.attempts) {
        await new Promise((resolve) => setTimeout(resolve, attempt * 800));
      }
    }
  }
  throw new Error(
    `${options.action}失败，已自动重试 ${options.attempts} 次。请检查当前网络后重试。最后一次错误：${lastError}`,
  );
}

function appendCommandOutput(current: string, chunk: Buffer | string): string {
  const next = current + chunk.toString();
  const maxLength = 4 * 1024 * 1024;
  return next.length <= maxLength ? next : next.slice(-maxLength);
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function terminateProcessTree(pid: number): Promise<void> {
  if (process.platform === 'win32') {
    await new Promise<void>((resolve) => {
      const killer = spawn(
        'taskkill',
        ['/PID', String(pid), '/T', '/F'],
        { stdio: 'ignore', windowsHide: true },
      );
      killer.once('error', () => resolve());
      killer.once('close', () => resolve());
    });
    return;
  }

  try {
    process.kill(-pid, 'SIGTERM');
  } catch {
    // The process group may already have exited.
  }
  await wait(400);
  try {
    process.kill(-pid, 'SIGKILL');
  } catch {
    // The process group exited during the grace period.
  }
}

async function runNetworkGitProcess(
  repoPath: string,
  args: string[],
  timeoutMs: number,
): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const child = spawn('git', args, {
      cwd: repoPath,
      detached: process.platform !== 'win32',
      env: {
        ...process.env,
        GIT_TERMINAL_PROMPT: '0',
        GIT_OPTIONAL_LOCKS: '0',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });
    let stdout = '';
    let stderr = '';
    let timedOut = false;
    let settled = false;

    child.stdout?.on('data', (chunk: Buffer | string) => {
      stdout = appendCommandOutput(stdout, chunk);
    });
    child.stderr?.on('data', (chunk: Buffer | string) => {
      stderr = appendCommandOutput(stderr, chunk);
    });

    const finish = (error?: Error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      if (error) reject(error);
      else resolve(stdout.trim());
    };

    const timeout = setTimeout(() => {
      timedOut = true;
      if (!child.pid) {
        finish(new Error(`Git 网络命令超过 ${Math.ceil(timeoutMs / 1000)} 秒未响应。`));
        return;
      }
      void terminateProcessTree(child.pid).finally(() => {
        finish(new Error(
          `Git 网络命令超过 ${Math.ceil(timeoutMs / 1000)} 秒未响应，已终止本次连接。`,
        ));
      });
    }, timeoutMs);

    child.once('error', (error) => {
      if (!timedOut) finish(error);
    });
    child.once('close', (code, signal) => {
      if (timedOut) return;
      if (code === 0) {
        finish();
        return;
      }
      finish(new Error(
        stderr.trim()
        || stdout.trim()
        || `Git 网络命令退出（code=${code ?? 'null'}, signal=${signal ?? 'none'}）。`,
      ));
    });
  });
}

function configurationPath(userDataPath: string): string {
  return path.join(userDataPath, 'strict-collab-source.json');
}

async function readConfiguredPath(userDataPath: string): Promise<string | null> {
  try {
    const payload = JSON.parse(
      await readFile(configurationPath(userDataPath), 'utf8'),
    ) as { repoPath?: unknown };
    return typeof payload.repoPath === 'string' ? payload.repoPath : null;
  } catch {
    return null;
  }
}

async function rememberPath(userDataPath: string, repoPath: string): Promise<void> {
  await writeFile(
    configurationPath(userDataPath),
    `${JSON.stringify({
      formatVersion: 1,
      repository: STRICT_REPOSITORY.githubRepository,
      repositoryNumericId: STRICT_REPOSITORY.githubRepositoryNumericId,
      repoPath,
    }, null, 2)}\n`,
    { encoding: 'utf8', mode: 0o600 },
  );
}

async function readMarker(repoPath: string): Promise<RepositoryMarker> {
  let marker: RepositoryMarker;
  try {
    marker = JSON.parse(
      await readFile(path.join(repoPath, STRICT_REPOSITORY.markerFile), 'utf8'),
    ) as RepositoryMarker;
  } catch {
    throw new Error('所选目录缺少严格新版仓库身份 marker。');
  }
  const valid = marker.formatVersion === 1
    && marker.kind === STRICT_REPOSITORY.markerKind
    && marker.genesisLabel === STRICT_REPOSITORY.genesisLabel
    && marker.githubRepository === STRICT_REPOSITORY.githubRepository
    && marker.githubRepositoryNumericId === STRICT_REPOSITORY.githubRepositoryNumericId
    && marker.githubRepositoryNodeId === STRICT_REPOSITORY.githubRepositoryNodeId
    && marker.remoteUrl === STRICT_REPOSITORY.remoteUrl
    && marker.targetBranch === STRICT_REPOSITORY.targetBranch
    && /^[a-f0-9]{64}$/.test(marker.localManifestHash)
    && /^[a-f0-9]{64}$/.test(marker.cloudManifestHash);
  if (!valid) {
    throw new Error('仓库身份 marker 与严格新版协作基线不一致。');
  }
  return marker;
}

async function validateLocalRepository(repoPath: string): Promise<string> {
  const resolved = path.resolve(repoPath);
  const topLevel = await run(resolved, ['rev-parse', '--show-toplevel']);
  if (path.resolve(topLevel) !== resolved) {
    throw new Error('请选择严格新版仓库根目录，不要选择其父目录或子目录。');
  }
  await readMarker(resolved);
  const remoteUrl = await run(resolved, ['remote', 'get-url', 'origin']);
  if (remoteUrl !== STRICT_REPOSITORY.remoteUrl) {
    throw new Error(
      `origin 必须是 ${STRICT_REPOSITORY.remoteUrl}，当前目录不是严格新版协作仓库。`,
    );
  }
  return resolved;
}

async function verifyRemoteRepositoryIdentity(): Promise<void> {
  if (Date.now() - githubIdentityVerifiedAt < 5 * 60_000) return;
  const response = await fetch(
    `https://api.github.com/repos/${STRICT_REPOSITORY.githubRepository}`,
    {
      headers: {
        Accept: 'application/vnd.github+json',
        'User-Agent': 'yiyu-thinktank-strict-desktop',
      },
      signal: AbortSignal.timeout(15_000),
    },
  );
  if (!response.ok) {
    throw new Error(`无法核验 GitHub 严格仓库身份：HTTP ${response.status}`);
  }
  const payload = await response.json() as {
    id?: number;
    node_id?: string;
    full_name?: string;
    clone_url?: string;
    default_branch?: string;
  };
  if (
    payload.id !== STRICT_REPOSITORY.githubRepositoryNumericId
    || payload.node_id !== STRICT_REPOSITORY.githubRepositoryNodeId
    || payload.full_name !== STRICT_REPOSITORY.githubRepository
    || payload.clone_url !== STRICT_REPOSITORY.remoteUrl
    || payload.default_branch !== STRICT_REPOSITORY.targetBranch
  ) {
    throw new Error('GitHub 返回的仓库 ID、地址或默认分支与严格新版基线不一致。');
  }
  githubIdentityVerifiedAt = Date.now();
}

async function readRemoteMainOid(): Promise<string> {
  await verifyRemoteRepositoryIdentity();
  const response = await fetch(
    `https://api.github.com/repos/${STRICT_REPOSITORY.githubRepository}/git/ref/heads/${STRICT_REPOSITORY.targetBranch}`,
    {
      headers: {
        Accept: 'application/vnd.github+json',
        'User-Agent': 'yiyu-thinktank-strict-desktop',
      },
      signal: AbortSignal.timeout(15_000),
    },
  );
  if (!response.ok) {
    throw new Error(`读取 GitHub main 版本失败：HTTP ${response.status}`);
  }
  const payload = await response.json() as {
    object?: { sha?: string };
  };
  const oid = payload.object?.sha;
  if (!oid || !/^[a-f0-9]{40}$/.test(oid)) {
    throw new Error('GitHub main 返回了无效的提交版本。');
  }
  return oid;
}

function candidatePaths(
  requestedPath: string | null | undefined,
  configuredPath: string | null,
  appPath: string,
): string[] {
  return Array.from(new Set([
    requestedPath || '',
    configuredPath || '',
    process.env.YIYU_STRICT_SOURCE_REPO || '',
    appPath,
    path.join(
      os.homedir(),
      'Documents',
      'New project',
      'projects',
      'yiyu-thinktank-strict',
    ),
  ].filter(Boolean).map((item) => path.resolve(item))));
}

export async function resolveStrictRepository(
  requestedPath: string | null | undefined,
  userDataPath: string,
  appPath: string,
): Promise<RepositoryResolution> {
  const configuredPath = await readConfiguredPath(userDataPath);
  let firstError: string | null = null;
  for (const candidate of candidatePaths(requestedPath, configuredPath, appPath)) {
    try {
      const repoPath = await validateLocalRepository(candidate);
      await rememberPath(userDataPath, repoPath);
      return {
        repoPath,
        suggestedRepoPath: repoPath,
        error: null,
      };
    } catch (error) {
      if (!firstError && candidate === path.resolve(requestedPath || '')) {
        firstError = error instanceof Error ? error.message : String(error);
      }
    }
  }
  return {
    repoPath: null,
    suggestedRepoPath: null,
    error: firstError || '未找到身份匹配的严格新版源码仓库。',
  };
}

function groupForPath(filePath: string): CollabChangeGroupKey {
  if (filePath.startsWith('src/renderer/')) return 'renderer';
  if (filePath.startsWith('src/main/')) return 'desktop_shell';
  if (filePath.startsWith('src/shared/') || filePath === 'package.json' || filePath === 'package-lock.json') {
    return 'shared_settings';
  }
  if (filePath.startsWith('backend/') || filePath.startsWith('strict_common/')) {
    return 'local_backend';
  }
  if (filePath.startsWith('cloud_backend/')) return 'cloud_backend';
  if (
    filePath.startsWith('scripts/')
    || filePath.startsWith('tests/')
    || filePath.startsWith('.github/')
    || filePath.endsWith('.md')
  ) {
    return 'scripts_docs';
  }
  return 'other';
}

function fileChange(
  filePath: string,
  type: CollabFileChangeType,
  previousPath?: string | null,
): CollabFileChange {
  const groupKey = groupForPath(filePath);
  return {
    path: filePath,
    previousPath: previousPath || null,
    type,
    groupKey,
    groupLabel: GROUP_LABELS[groupKey],
    summary: `${GROUP_LABELS[groupKey]}：${filePath}`,
    risk: null,
  };
}

function parseNameStatus(output: string): CollabFileChange[] {
  if (!output) return [];
  const items: CollabFileChange[] = [];
  for (const line of output.split('\n')) {
    if (!line.trim()) continue;
    const [status, firstPath, secondPath] = line.split('\t');
    if (!status || !firstPath) continue;
    if (status.startsWith('R') && secondPath) {
      items.push(fileChange(secondPath, 'renamed', firstPath));
      continue;
    }
    const type: CollabFileChangeType = status.startsWith('A')
      ? 'added'
      : status.startsWith('D')
        ? 'deleted'
        : 'modified';
    items.push(fileChange(firstPath, type));
  }
  return items;
}

async function workingTreeChanges(repoPath: string): Promise<CollabFileChange[]> {
  const output = await run(repoPath, ['status', '--porcelain=v1', '--untracked-files=all']);
  if (!output) return [];
  const items: CollabFileChange[] = [];
  for (const line of output.split('\n')) {
    if (line.length < 4) continue;
    const code = line.slice(0, 2);
    const value = line.slice(3);
    if (code === '??') {
      items.push(fileChange(value, 'untracked'));
      continue;
    }
    if (code.includes('R') && value.includes(' -> ')) {
      const [previousPath, nextPath] = value.split(' -> ');
      items.push(fileChange(nextPath, 'renamed', previousPath));
      continue;
    }
    const type: CollabFileChangeType = code.includes('A')
      ? 'added'
      : code.includes('D')
        ? 'deleted'
        : 'modified';
    items.push(fileChange(value, type));
  }
  return items;
}

function uniqueFiles(files: CollabFileChange[]): CollabFileChange[] {
  const result = new Map<string, CollabFileChange>();
  for (const item of files) result.set(item.path, item);
  return Array.from(result.values()).sort((left, right) => left.path.localeCompare(right.path));
}

function changeGroups(files: CollabFileChange[]): CollabChangeGroup[] {
  const counts = new Map<CollabChangeGroupKey, number>();
  for (const file of files) {
    counts.set(file.groupKey, (counts.get(file.groupKey) || 0) + 1);
  }
  return Array.from(counts.entries()).map(([key, fileCount]) => ({
    key,
    label: GROUP_LABELS[key],
    fileCount,
  }));
}

function effectsFor(files: CollabFileChange[], mode: 'push' | 'pull'): CollabEffectPreview[] {
  return changeGroups(files).map((group) => {
    const relatedPaths = files
      .filter((file) => file.groupKey === group.key)
      .map((file) => file.path);
    return {
      id: `${mode}:${group.key}`,
      title: group.label,
      summary: `${group.fileCount} 个文件将${mode === 'push' ? '发布到' : '从'}严格新版 main`,
      visibility: group.key === 'renderer'
        ? 'visible'
        : group.key === 'shared_settings'
          ? 'mixed'
          : 'background',
      scopeLabel: group.label,
      details: relatedPaths.slice(0, 8),
      relatedPaths,
      explanationSource: 'user_feature_rules',
    };
  });
}

async function hasUnmergedPaths(repoPath: string): Promise<boolean> {
  const output = await run(
    repoPath,
    ['diff', '--name-only', '--diff-filter=U'],
    { allowFailure: true },
  );
  return Boolean(output);
}

async function countAheadBehind(repoPath: string): Promise<[number, number]> {
  const remoteExists = await run(
    repoPath,
    ['rev-parse', '--verify', '--quiet', 'refs/remotes/origin/main'],
    { allowFailure: true },
  );
  if (!remoteExists) return [0, 0];
  const output = await run(
    repoPath,
    ['rev-list', '--left-right', '--count', 'HEAD...refs/remotes/origin/main'],
  );
  const [ahead, behind] = output.split(/\s+/).map((value) => Number(value || 0));
  return [ahead || 0, behind || 0];
}

async function buildStatus(
  repoPath: string,
  suggestedRepoPath: string | null = repoPath,
): Promise<CollabRepoStatus> {
  const branch = await run(repoPath, ['branch', '--show-current']);
  const localChanges = await workingTreeChanges(repoPath);
  const unmerged = await hasUnmergedPaths(repoPath);
  const [aheadCount, behindCount] = await countAheadBehind(repoPath);
  return {
    repoPath,
    repoName: path.basename(repoPath),
    suggestedRepoPath,
    workingRepoPath: repoPath,
    workingBranch: branch || null,
    workingChangeCount: localChanges.length,
    isConfigured: true,
    isValid: true,
    branch: branch || null,
    isMainBranch: branch === STRICT_REPOSITORY.targetBranch,
    hasLocalChanges: localChanges.length > 0,
    hasUnmergedPaths: unmerged,
    aheadCount,
    behindCount,
    localChangeCount: localChanges.length,
    remoteChangeCount: behindCount,
    statusText: unmerged
      ? '存在未解决冲突，已停止协作操作。'
      : `严格新版仓库已绑定：${STRICT_REPOSITORY.githubRepository} · main`,
  };
}

function invalidStatus(
  requestedPath: string | null | undefined,
  resolution: RepositoryResolution,
): CollabRepoStatus {
  return {
    repoPath: requestedPath || null,
    repoName: requestedPath ? path.basename(requestedPath) : null,
    suggestedRepoPath: resolution.suggestedRepoPath,
    workingRepoPath: null,
    workingBranch: null,
    workingChangeCount: 0,
    isConfigured: Boolean(requestedPath),
    isValid: false,
    branch: null,
    isMainBranch: false,
    hasLocalChanges: false,
    hasUnmergedPaths: false,
    aheadCount: 0,
    behindCount: 0,
    localChangeCount: 0,
    remoteChangeCount: 0,
    statusText: resolution.error || '未绑定严格新版源码仓库。',
  };
}

export async function getStrictCollabRepoStatus(
  requestedPath: string | null | undefined,
  userDataPath: string,
  appPath: string,
): Promise<CollabRepoStatus> {
  const resolution = await resolveStrictRepository(requestedPath, userDataPath, appPath);
  if (!resolution.repoPath) return invalidStatus(requestedPath, resolution);
  return buildStatus(resolution.repoPath, resolution.suggestedRepoPath);
}

async function fetchMain(repoPath: string): Promise<void> {
  const expectedRemoteOid = await readRemoteMainOid();
  const currentRemoteOid = await run(
    repoPath,
    ['rev-parse', '--verify', 'refs/remotes/origin/main'],
    { allowFailure: true },
  );
  if (currentRemoteOid === expectedRemoteOid) return;

  await runNetworkGit(
    repoPath,
    ['fetch', '--prune', 'origin', '+refs/heads/main:refs/remotes/origin/main'],
    {
      action: '读取 GitHub main',
      attempts: 3,
      timeoutMs: 15_000,
    },
  );
  const fetchedOid = await run(repoPath, ['rev-parse', 'refs/remotes/origin/main']);
  if (fetchedOid === expectedRemoteOid) return;

  const latestRemoteOid = await readRemoteMainOid();
  if (fetchedOid !== latestRemoteOid) {
    throw new Error('GitHub main 在读取期间发生变化，请重新预览后再操作。');
  }
}

async function requireRemoteAncestor(repoPath: string): Promise<void> {
  try {
    await execFileAsync(
      'git',
      ['merge-base', '--is-ancestor', 'refs/remotes/origin/main', 'HEAD'],
      { cwd: repoPath, env: { ...process.env, GIT_TERMINAL_PROMPT: '0' } },
    );
  } catch {
    throw new Error(
      '远端 main 含有本机尚未接入的提交。请先预览并快进接收，禁止强制覆盖。',
    );
  }
}

async function localPublishFiles(repoPath: string): Promise<CollabFileChange[]> {
  const committed = parseNameStatus(
    await run(
      repoPath,
      ['diff', '--name-status', '--find-renames', 'refs/remotes/origin/main...HEAD'],
      { allowFailure: true },
    ),
  );
  return uniqueFiles([...committed, ...await workingTreeChanges(repoPath)]);
}

export async function previewStrictPush(
  repoPath: string,
): Promise<PushPreview> {
  const validated = await validateLocalRepository(repoPath);
  await fetchMain(validated);
  const status = await buildStatus(validated);
  const files = await localPublishFiles(validated);
  let executionBlockReason: string | null = null;
  if (status.hasUnmergedPaths) {
    executionBlockReason = '当前存在未解决冲突，不能推送。';
  } else {
    try {
      await requireRemoteAncestor(validated);
    } catch (error) {
      executionBlockReason = error instanceof Error ? error.message : String(error);
    }
  }
  if (!executionBlockReason && files.length === 0 && status.aheadCount === 0) {
    executionBlockReason = '当前没有可提交的本地文件改动。';
  }
  return {
    status,
    suggestedMessage: '同步严格新版功能修改',
    effects: effectsFor(files, 'push'),
    groups: changeGroups(files),
    files,
    suggestedCollabBranchName: null,
    notice: `只允许推送到 ${STRICT_REPOSITORY.githubRepository} 的 main；推送前会执行完整严格门禁。`,
    executionBlockReason,
  };
}

function sanitizeMessage(message: string): string {
  const normalized = message.trim().replace(/\s+/g, ' ');
  if (!normalized) return '同步严格新版功能修改';
  return normalized.slice(0, 160);
}

export async function pushStrictMain(
  payload: PushMainPayload,
): Promise<CollabActionResult> {
  const repoPath = await validateLocalRepository(payload.repoPath);
  await fetchMain(repoPath);
  if (await hasUnmergedPaths(repoPath)) {
    throw new Error('当前存在未解决冲突，已停止推送。');
  }
  await requireRemoteAncestor(repoPath);
  const remoteBefore = await run(repoPath, ['rev-parse', 'refs/remotes/origin/main']);
  await runCommand(repoPath, 'npm', ['run', 'verify:strict-maintenance'], 10 * 60_000);
  await run(repoPath, ['add', '--all']);
  const staged = await run(
    repoPath,
    ['diff', '--cached', '--name-only'],
    { allowFailure: true },
  );
  let createdCommit = false;
  const commitMessage = sanitizeMessage(payload.message);
  if (staged) {
    await run(repoPath, ['commit', '-m', commitMessage]);
    createdCommit = true;
  }
  await fetchMain(repoPath);
  const remoteAfter = await run(repoPath, ['rev-parse', 'refs/remotes/origin/main']);
  if (remoteAfter !== remoteBefore) {
    throw new Error(
      '远端 main 在本次安全检查期间发生更新，已停止且没有覆盖远端。请先预览远端修改。',
    );
  }
  await requireRemoteAncestor(repoPath);
  const aheadFiles = parseNameStatus(
    await run(
      repoPath,
      ['diff', '--name-status', '--find-renames', 'refs/remotes/origin/main...HEAD'],
      { allowFailure: true },
    ),
  );
  if (aheadFiles.length === 0) {
    throw new Error('当前没有可推送到 main 的提交。');
  }
  await runNetworkGit(
    repoPath,
    ['push', 'origin', `HEAD:refs/heads/${STRICT_REPOSITORY.targetBranch}`],
    {
      action: '推送 GitHub main',
      attempts: 3,
      timeoutMs: 30_000,
    },
  );
  const localHead = await run(repoPath, ['rev-parse', 'HEAD']);
  let publishedOid = '';
  for (let attempt = 0; attempt < 5; attempt += 1) {
    publishedOid = await readRemoteMainOid();
    if (publishedOid === localHead) break;
    await wait(800);
  }
  if (publishedOid !== localHead) {
    throw new Error('GitHub main 未包含本机最新提交，不能报告推送成功。');
  }
  await run(
    repoPath,
    ['update-ref', 'refs/remotes/origin/main', localHead, remoteAfter],
  );
  return {
    status: await buildStatus(repoPath),
    changedPaths: aheadFiles.map((item) => item.path),
    createdCommit,
    commitMessage,
    mergeStatus: 'pushed',
    explanation: '严格门禁通过，已快进推送到新仓 main。',
  };
}

async function remoteCommits(repoPath: string): Promise<CollabRemoteCommit[]> {
  const output = await run(
    repoPath,
    [
      'log',
      '--format=%H%x1f%h%x1f%s%x1f%aI%x1f%cI%x1f%an%x1f%ae%x1f%cn%x1f%ce',
      'HEAD..refs/remotes/origin/main',
    ],
    { allowFailure: true },
  );
  if (!output) return [];
  return output.split('\n').filter(Boolean).map((line) => {
    const [hash, shortHash, subject, authoredAt, committedAt, authorName, authorEmail, committerName, committerEmail] = line.split('\x1f');
    return {
      hash,
      shortHash,
      subject,
      authoredAt,
      committedAt,
      authorName,
      authorEmail,
      committerName,
      committerEmail,
      identityLabel: authorName,
      sourceLabel: 'origin/main',
      changedPaths: [],
      fileCount: 0,
    };
  });
}

export async function previewStrictPull(repoPath: string): Promise<PullPreview> {
  const validated = await validateLocalRepository(repoPath);
  await fetchMain(validated);
  const status = await buildStatus(validated);
  const files = parseNameStatus(
    await run(
      validated,
      ['diff', '--name-status', '--find-renames', 'HEAD..refs/remotes/origin/main'],
      { allowFailure: true },
    ),
  );
  const canFastForwardMain = status.isMainBranch
    && !status.hasLocalChanges
    && !status.hasUnmergedPaths
    && status.aheadCount === 0
    && status.behindCount > 0;
  let executionBlockReason: string | null = null;
  if (!status.isMainBranch) {
    executionBlockReason = '接收远端 main 前，请先切回本地 main 分支。';
  } else if (status.hasLocalChanges || status.hasUnmergedPaths) {
    executionBlockReason = '本机仍有未提交修改或冲突，不能快进接收 main。';
  } else if (status.aheadCount > 0) {
    executionBlockReason = '本机包含尚未推送的提交，请先安全推送。';
  } else if (status.behindCount === 0) {
    executionBlockReason = 'main 当前已经是最新。';
  }
  const commits = await remoteCommits(validated);
  return {
    status,
    suggestedMessage: '快进接收严格新版 main',
    commitSummaries: commits.map((item) => `${item.shortHash} ${item.subject}`),
    remoteCommits: commits,
    remoteBranches: [],
    syncTargetCommit: status.behindCount > 0 ? 'refs/remotes/origin/main' : null,
    syncTargetLabel: '严格新版 main',
    canFastForwardMain,
    directReceiveBlockReason: executionBlockReason,
    effects: effectsFor(files, 'pull'),
    groups: changeGroups(files),
    files,
    notice: `远端固定为 ${STRICT_REPOSITORY.githubRepository} 的 main。`,
    executionBlockReason,
  };
}

export async function fastForwardStrictMain(
  payload: FastForwardMainPayload,
): Promise<CollabActionResult> {
  const repoPath = await validateLocalRepository(payload.repoPath);
  await fetchMain(repoPath);
  const status = await buildStatus(repoPath);
  if (!status.isMainBranch) {
    throw new Error('当前不在 main 分支，不能接收远端 main。');
  }
  if (status.hasLocalChanges || status.hasUnmergedPaths) {
    throw new Error('本机仍有未提交修改或冲突，不能快进接收 main。');
  }
  if (status.aheadCount > 0) {
    throw new Error('本机包含尚未推送的提交，请先安全推送。');
  }
  const files = parseNameStatus(
    await run(
      repoPath,
      ['diff', '--name-status', '--find-renames', 'HEAD..refs/remotes/origin/main'],
      { allowFailure: true },
    ),
  );
  await run(repoPath, ['merge', '--ff-only', 'refs/remotes/origin/main']);
  return {
    status: await buildStatus(repoPath),
    changedPaths: files.map((item) => item.path),
    createdCommit: false,
    mergeStatus: 'mainFastForwarded',
    explanation: '已快进接收严格新版远端 main。',
  };
}

function safeBranchName(value: string | null | undefined): string {
  const cleaned = (value || '')
    .trim()
    .replace(/^refs\/heads\//, '')
    .replace(/[^A-Za-z0-9._/-]+/g, '-')
    .replace(/\.{2,}/g, '.')
    .replace(/\/{2,}/g, '/')
    .replace(/^[-/.]+|[-/.]+$/g, '');
  if (cleaned.startsWith('codex/')) return cleaned;
  return `codex/${cleaned || `collab-${Date.now()}`}`;
}

export async function publishStrictBranch(
  payload: PublishCollabBranchPayload,
): Promise<CollabActionResult> {
  const repoPath = await validateLocalRepository(payload.repoPath);
  await fetchMain(repoPath);
  if (await hasUnmergedPaths(repoPath)) {
    throw new Error('当前存在未解决冲突，不能发布协作分支。');
  }
  await runCommand(repoPath, 'npm', ['run', 'verify:strict-maintenance'], 10 * 60_000);
  await run(repoPath, ['add', '--all']);
  const staged = await run(
    repoPath,
    ['diff', '--cached', '--name-only'],
    { allowFailure: true },
  );
  const message = sanitizeMessage(payload.message);
  let createdCommit = false;
  if (staged) {
    await run(repoPath, ['commit', '-m', message]);
    createdCommit = true;
  }
  const branchName = safeBranchName(payload.branchName);
  await run(
    repoPath,
    ['push', 'origin', `HEAD:refs/heads/${branchName}`],
    { timeoutMs: 180_000 },
  );
  return {
    status: await buildStatus(repoPath),
    changedPaths: staged ? staged.split('\n').filter(Boolean) : [],
    createdCommit,
    commitMessage: message,
    mergeStatus: 'collabBranchPublished',
    collabBranchName: branchName,
    collabBranchRef: `refs/remotes/origin/${branchName}`,
    explanation: `已发布严格新版协作分支 ${branchName}。`,
  };
}
