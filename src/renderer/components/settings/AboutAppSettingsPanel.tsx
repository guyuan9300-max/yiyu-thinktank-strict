import React, { useEffect, useState } from 'react';
import { AlertCircle, Bell, CheckCircle2, Download, RefreshCw } from 'lucide-react';
import type {
  DesktopAppInfo,
  OfficialPushUpdatePayload,
  ReleaseVersionMetadata,
  UpdateEventPayload,
} from '../../../shared/types';
import { OFFICIAL_PUSH_STATE_EVENT, UPDATE_STATE_KEY } from '../UpdateNotifier';
import { UpdateContentCard } from './UpdateContentCard';

interface Props {
  desktopAppInfo: DesktopAppInfo | null;
}

type UpdateUiState =
  | { kind: 'idle' }
  | { kind: 'checking' }
  | { kind: 'downloading'; version?: string; percent?: number }
  | { kind: 'official-push'; push: OfficialPushUpdatePayload; installing?: boolean }
  | { kind: 'official-push-opened'; version?: string | null; fileName?: string | null }
  | { kind: 'up-to-date' }
  | { kind: 'error'; message: string };

function formatPercent(percent: number | undefined): string {
  if (typeof percent !== 'number' || !Number.isFinite(percent)) return '0%';
  return `${Math.max(0, Math.min(100, percent)).toFixed(0)}%`;
}

function formatSize(sizeBytes: number | null | undefined): string | null {
  if (typeof sizeBytes !== 'number' || !Number.isFinite(sizeBytes) || sizeBytes <= 0) return null;
  if (sizeBytes >= 1024 * 1024 * 1024) return `${(sizeBytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
  return `${(sizeBytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatPublishedAt(value: string | null | undefined): string {
  if (!value) return '—';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '—';
  return parsed.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
}

function pushRelationLabel(relation: OfficialPushUpdatePayload['relation']): string {
  if (relation === 'upgrade') return '升级版本';
  if (relation === 'switch-custom') return '组织定制版';
  if (relation === 'different') return '指定版本';
  return '官方版本';
}

function initialUpdateState(): UpdateUiState {
  const cachedPush = typeof window !== 'undefined' ? window[UPDATE_STATE_KEY]?.officialPush : null;
  return cachedPush ? { kind: 'official-push', push: cachedPush } : { kind: 'idle' };
}

export function AboutAppSettingsPanel({ desktopAppInfo }: Props): React.ReactElement {
  const [updateState, setUpdateState] = useState<UpdateUiState>(() => initialUpdateState());
  const [checkBusy, setCheckBusy] = useState(false);
  const [releaseMetadata, setReleaseMetadata] = useState<ReleaseVersionMetadata | null>(null);

  useEffect(() => {
    let active = true;
    void window.yiyuWorkbench?.getCurrentReleaseMetadata?.()
      .then((metadata) => {
        if (active) setReleaseMetadata(metadata);
      })
      .catch(() => {
        if (active) setReleaseMetadata(null);
      });
    return () => { active = false; };
  }, [desktopAppInfo?.appVersion]);

  useEffect(() => {
    const subscribe = window.yiyuWorkbench?.onUpdateEvent;
    if (typeof subscribe !== 'function') return;
    return subscribe((payload: UpdateEventPayload) => {
      switch (payload.kind) {
        case 'checking':
          setUpdateState({ kind: 'checking' });
          return;
        case 'download-progress':
          setUpdateState((prev) => ({
            kind: 'downloading',
            version: prev.kind === 'official-push' ? prev.push.version : prev.kind === 'downloading' ? prev.version : undefined,
            percent: payload.percent,
          }));
          return;
        case 'official-push-available':
          if (payload.officialPush) setUpdateState({ kind: 'official-push', push: payload.officialPush });
          return;
        case 'official-push-not-available':
        case 'not-available':
          setUpdateState({ kind: 'up-to-date' });
          return;
        case 'error':
          setUpdateState({ kind: 'error', message: payload.message ?? '未知错误' });
          return;
        default:
          return;
      }
    });
  }, []);

  const handleCheck = async () => {
    const trigger = window.yiyuWorkbench?.checkForUpdates;
    if (typeof trigger !== 'function') return;
    setCheckBusy(true);
    setUpdateState({ kind: 'checking' });
    try {
      const result = await trigger();
      if (!result.ok) setUpdateState({ kind: 'error', message: result.reason ?? '检查失败' });
    } finally {
      setCheckBusy(false);
    }
  };

  const handleInstallOfficialPush = async (push: OfficialPushUpdatePayload) => {
    const trigger = window.yiyuWorkbench?.installOfficialPushUpdate;
    if (typeof trigger !== 'function') {
      setUpdateState({ kind: 'error', message: '当前安装包还不支持官网更新，请先安装迁移版本。' });
      return;
    }
    setUpdateState({ kind: 'official-push', push, installing: true });
    const result = await trigger();
    if (!result.ok) {
      setUpdateState({ kind: 'error', message: result.reason ?? '下载安装包失败' });
      return;
    }
    setUpdateState({
      kind: 'official-push-opened',
      version: result.version ?? push.version,
      fileName: result.fileName ?? push.fileName ?? null,
    });
  };

  const handleDismissOfficialPush = () => {
    if (typeof window !== 'undefined' && window[UPDATE_STATE_KEY]) {
      window[UPDATE_STATE_KEY]!.officialPush = null;
      window.dispatchEvent(new CustomEvent(OFFICIAL_PUSH_STATE_EVENT, { detail: null }));
    }
    setUpdateState({ kind: 'idle' });
  };

  const appVersion = desktopAppInfo?.appVersion ?? '未知';
  const platformLabel = desktopAppInfo
    ? `${desktopAppInfo.platform} · ${desktopAppInfo.arch}${desktopAppInfo.isPackaged ? '' : ' · 开发模式'}`
    : '—';
  const installHint = desktopAppInfo?.platform === 'win32'
    ? '请先关闭旧软件，再将新版安装到原目录。'
    : '请先关闭旧软件，再将应用拖入“应用程序”并选择替换。';

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6">
      <div className="rounded-2xl border border-gray-100 bg-white p-6">
        <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">ABOUT</p>
        <h2 className="mt-2 text-[20px] font-light tracking-tight text-gray-900">关于本软件</h2>
        <p className="mt-1.5 text-[12px] text-gray-500">益语智库自用平台 V2.0 · 桌面版</p>
        <dl className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <dt className="text-[11px] font-bold uppercase tracking-[0.12em] text-gray-400">当前版本</dt>
            <dd className="mt-1 text-[15px] font-medium text-gray-900">{appVersion}</dd>
          </div>
          <div>
            <dt className="text-[11px] font-bold uppercase tracking-[0.12em] text-gray-400">运行环境</dt>
            <dd className="mt-1 text-[13px] text-gray-700">{platformLabel}</dd>
          </div>
          <div>
            <dt className="text-[11px] font-bold uppercase tracking-[0.12em] text-gray-400">最近更新时间</dt>
            <dd className="mt-1 text-[13px] text-gray-700">{formatPublishedAt(releaseMetadata?.publishedAt)}</dd>
          </div>
          {desktopAppInfo?.frontendBuildVersion && (
            <div>
              <dt className="text-[11px] font-bold uppercase tracking-[0.12em] text-gray-400">构建版本</dt>
              <dd className="mt-1 truncate text-[12px] text-gray-500" title={desktopAppInfo.frontendBuildVersion}>
                {desktopAppInfo.frontendBuildVersion}
              </dd>
            </div>
          )}
        </dl>
      </div>

      <div className="rounded-2xl border border-gray-100 bg-white p-6">
        <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">UPDATES</p>
        <h3 className="mt-2 text-[18px] font-light tracking-tight text-gray-900">软件更新</h3>
        <p className="mt-1.5 text-[12px] leading-6 text-gray-500">
          软件会每 24 小时检查一次官网发布的新版本。发现更新时会通知你；也可以点击“检查更新”查看更新内容并下载安装包。
          安装前请关闭旧软件：Windows 请将新版安装到原目录；macOS 请将应用拖入“应用程序”并选择替换。
        </p>

        <div className="mt-5 space-y-3">
          {updateState.kind === 'checking' && (
            <div className="flex items-center gap-2 rounded-md bg-gray-50 px-3 py-2 text-[12px] text-gray-600">
              <RefreshCw size={14} className="animate-spin" />正在检查更新…
            </div>
          )}
          {updateState.kind === 'downloading' && (
            <div className="rounded-md bg-indigo-50 px-3 py-2 text-[12px] text-indigo-700">
              正在下载安装包{updateState.version ? ` ${updateState.version}` : ''}
              {typeof updateState.percent === 'number' && <span className="ml-2 font-medium">{formatPercent(updateState.percent)}</span>}
            </div>
          )}
          {updateState.kind === 'official-push-opened' && (
            <div className="flex items-start gap-2 rounded-md bg-emerald-50 px-3 py-2 text-[12px] text-emerald-700">
              <CheckCircle2 size={14} className="mt-[2px] shrink-0" />
              <span>版本 {updateState.version || ''} 的安装包已下载并打开。{installHint}</span>
            </div>
          )}
          {updateState.kind === 'official-push' && (
            <div className="rounded-md border border-blue-100 bg-blue-50 px-3 py-3 text-[12px] text-blue-800">
              <div className="flex items-start gap-2">
                <Bell size={15} className="mt-[2px] shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{updateState.push.title}</span>
                    <span className="rounded-full bg-white/80 px-2 py-0.5 text-[10px] font-semibold text-blue-700">
                      {pushRelationLabel(updateState.push.relation)}
                    </span>
                  </div>
                  <p className="mt-1 leading-relaxed text-blue-700/90">
                    当前版本 {updateState.push.currentVersion}，可更新至 {updateState.push.version}
                    {formatSize(updateState.push.sizeBytes) ? ` · ${formatSize(updateState.push.sizeBytes)}` : ''}。
                  </p>
                  <UpdateContentCard version={updateState.push.version} userNotes={updateState.push.userNotes} />
                </div>
              </div>
            </div>
          )}
          {updateState.kind === 'up-to-date' && (
            <div className="flex items-center gap-2 rounded-md bg-emerald-50 px-3 py-2 text-[12px] text-emerald-700">
              <CheckCircle2 size={14} />当前已经是最新版本。
            </div>
          )}
          {updateState.kind === 'error' && (
            <div className="flex items-start gap-2 rounded-md bg-rose-50 px-3 py-2 text-[12px] text-rose-700">
              <AlertCircle size={14} className="mt-[2px] shrink-0" />
              <span className="break-words">检查更新失败：{updateState.message}</span>
            </div>
          )}
        </div>

        <div className="mt-5 flex flex-wrap gap-3">
          <button
            type="button"
            onClick={handleCheck}
            disabled={checkBusy || updateState.kind === 'checking' || updateState.kind === 'downloading'}
            className="inline-flex items-center gap-2 rounded-md border border-gray-200 bg-white px-4 py-2 text-[13px] font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCw size={14} className={checkBusy || updateState.kind === 'checking' ? 'animate-spin' : ''} />检查更新
          </button>
          {updateState.kind === 'official-push' && (
            <>
              <button
                type="button"
                onClick={() => handleInstallOfficialPush(updateState.push)}
                disabled={updateState.installing}
                className="inline-flex items-center gap-2 rounded-md bg-[#5B7BFE] px-4 py-2 text-[13px] font-medium text-white hover:bg-[#4A6AEF] disabled:opacity-60"
              >
                <Download size={14} />{updateState.installing ? '正在准备…' : '下载安装包'}
              </button>
              <button
                type="button"
                onClick={handleDismissOfficialPush}
                className="inline-flex items-center gap-2 rounded-md border border-gray-200 bg-white px-4 py-2 text-[13px] font-medium text-gray-600 hover:bg-gray-50"
              >
                稍后处理
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
