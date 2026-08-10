import { contextBridge, ipcRenderer, webUtils } from 'electron';
import type {
  DesktopRuntime,
  RenderedOfficialWebsiteCapture,
} from '../shared/types.js';

const runtime = ipcRenderer.sendSync('strict:get-runtime-sync') as DesktopRuntime;

contextBridge.exposeInMainWorld('strictDesktop', {
  getRuntime: (): Promise<DesktopRuntime> => Promise.resolve(runtime),
});

contextBridge.exposeInMainWorld('yiyuWorkbench', {
  backendBaseUrl: runtime.apiBaseUrl,
  desktopToken: runtime.desktopToken,
  setMiniMode: (enter: boolean) => ipcRenderer.invoke('strict:set-mini-mode', enter),
  setUpdateOrgIdentity: () => Promise.resolve({ ok: true }),
  setUpdateOrgCode: () => Promise.resolve({ ok: true }),
  getDesktopAppInfo: () => ipcRenderer.invoke('strict:get-desktop-app-info'),
  resumeFromStartupGate: () => ipcRenderer.invoke('strict:resume-startup-gate'),
  selectFiles: () => ipcRenderer.invoke('strict:select-files'),
  selectFolder: () => ipcRenderer.invoke('strict:select-folder'),
  selectCollabRepo: () => ipcRenderer.invoke('strict:select-collab-repo'),
  getCollabRepoStatus: (repoPath?: string | null) =>
    ipcRenderer.invoke('strict:get-collab-repo-status', repoPath ?? null),
  previewPushToMain: (repoPath: string) =>
    ipcRenderer.invoke('strict:preview-push-to-main', repoPath),
  pushSafelyToMain: (payload: unknown) =>
    ipcRenderer.invoke('strict:push-safely-to-main', payload),
  publishCollabBranch: (payload: unknown) =>
    ipcRenderer.invoke('strict:publish-collab-branch', payload),
  previewPullFromMain: (repoPath: string, targetCommit?: string | null) =>
    ipcRenderer.invoke(
      'strict:preview-pull-from-main',
      repoPath,
      targetCommit ?? null,
    ),
  fastForwardMain: (payload: unknown) =>
    ipcRenderer.invoke('strict:fast-forward-main', payload),
  startCollabPreview: () => Promise.reject(
    new Error('协作预览暂未开放；严格推送和拉取不受影响。'),
  ),
  stopCollabPreview: () => Promise.reject(
    new Error('当前没有由严格新版启动的协作预览。'),
  ),
  rebuildAndInstallFromRepo: () => Promise.reject(
    new Error('自动覆盖安装暂未开放，请先验收同步后的源码。'),
  ),
  setWorkspaceInteractionState: (payload: {
    active: boolean;
    source: string;
    detail?: string | null;
  }) => Promise.resolve({
    ...payload,
    detail: payload.detail ?? null,
    updatedAt: new Date().toISOString(),
  }),
  getDroppedFilePath: (file: File) => {
    try {
      return webUtils.getPathForFile(file) || null;
    } catch {
      return null;
    }
  },
  readTextFile: (targetPath: string) => ipcRenderer.invoke('strict:read-text-file', targetPath),
  openPath: (targetPath: string) => ipcRenderer.invoke('strict:open-path', targetPath),
  openExternalUrl: (targetUrl: string) => ipcRenderer.invoke('strict:open-external-url', targetUrl),
  renderOfficialWebsite: (targetUrl: string): Promise<RenderedOfficialWebsiteCapture> =>
    ipcRenderer.invoke('strict:render-official-website', targetUrl),
  revealInFinder: (targetPath: string) => ipcRenderer.invoke('strict:reveal-in-finder', targetPath),
  saveFileAs: (sourcePath: string, suggestedName?: string) =>
    ipcRenderer.invoke('strict:save-file-as', sourcePath, suggestedName),
  quitApp: () => ipcRenderer.invoke('strict:quit-app'),
  saveRecordingBlob: (payload: {
    buffer: ArrayBuffer;
    extension?: string;
    sessionId?: string;
  }) => ipcRenderer.invoke('strict:save-recording-blob', payload),
  readRecordingFile: (absolutePath: string) =>
    ipcRenderer.invoke('strict:read-recording-file', absolutePath),
  setRecordingActive: (payload: { active: boolean; taskTitle?: string }) =>
    Promise.resolve({ active: payload.active }),
  setBackgroundTasks: (payload: { tasks: unknown[] }) =>
    Promise.resolve({ ok: true, count: payload.tasks.length }),
  checkForUpdates: () =>
    Promise.resolve({ ok: false, reason: '严格新版暂未接入检查更新。' }),
  getCurrentReleaseMetadata: () => Promise.resolve(null),
  installOfficialPushUpdate: () =>
    Promise.resolve({ ok: false, reason: '严格新版暂未接入检查更新。' }),
  onUpdateEvent: () => () => undefined,
});
