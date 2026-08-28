import React, { useEffect, useRef, useState } from 'react';
import { AlertCircle, Bell, CheckCircle2, Download, PackageOpen, RefreshCw } from 'lucide-react';
import type {
  DesktopAppInfo,
  OfficialPushUpdatePayload,
  OfficialUpdateStatusSnapshot,
  ReleaseVersionMetadata,
  UpdateEventPayload,
} from '../../../shared/types';
import { OFFICIAL_PUSH_STATE_EVENT, UPDATE_STATE_KEY } from '../UpdateNotifier';
import {
  nextBrandSettingsUnlockProgress,
  organizationBrandUnlockSessionKey,
  type BrandSettingsUnlockProgress,
} from '../../lib/brandSettingsUnlock';
import { BrandLogoMark } from './BrandLogoSettingsCard';
import { OrganizationBrandSettingsCard } from './OrganizationBrandSettingsCard';
import { UpdateContentCard } from './UpdateContentCard';

interface Props {
  desktopAppInfo: DesktopAppInfo | null;
  organizationScopeKey: string;
  canManageOrganizationBrand: boolean;
}

type UpdateUiState =
  | { kind: 'idle' }
  | { kind: 'checking' }
  | { kind: 'downloading'; version?: string; percent?: number; status?: OfficialUpdateStatusSnapshot | null }
  | { kind: 'official-push'; push: OfficialPushUpdatePayload }
  | { kind: 'ready-to-install'; status: OfficialUpdateStatusSnapshot }
  | { kind: 'official-push-opened'; status: OfficialUpdateStatusSnapshot }
  | { kind: 'up-to-date' }
  | { kind: 'error'; message: string; status?: OfficialUpdateStatusSnapshot | null };

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
  const cachedStatus = typeof window !== 'undefined' ? window[UPDATE_STATE_KEY]?.status : null;
  if (cachedStatus?.status === 'ready-to-install') return { kind: 'ready-to-install', status: cachedStatus };
  if (cachedStatus?.status === 'installer-opened') return { kind: 'official-push-opened', status: cachedStatus };
  if (cachedStatus?.status === 'downloading') {
    return { kind: 'downloading', version: cachedStatus.version, percent: cachedStatus.percent, status: cachedStatus };
  }
  if (cachedStatus?.status === 'failed') return { kind: 'error', message: cachedStatus.message || '更新未完成', status: cachedStatus };
  const cachedPush = typeof window !== 'undefined' ? window[UPDATE_STATE_KEY]?.officialPush : null;
  return cachedPush ? { kind: 'official-push', push: cachedPush } : { kind: 'idle' };
}

function stateFromStatus(status: OfficialUpdateStatusSnapshot | null): UpdateUiState | null {
  if (!status) return null;
  if (status.status === 'ready-to-install') return { kind: 'ready-to-install', status };
  if (status.status === 'installer-opened') return { kind: 'official-push-opened', status };
  if (status.status === 'downloading') {
    return { kind: 'downloading', version: status.version, percent: status.percent, status };
  }
  return { kind: 'error', message: status.message || '更新未完成，可以重新下载。', status };
}

export function AboutAppSettingsPanel({
  desktopAppInfo,
  organizationScopeKey,
  canManageOrganizationBrand,
}: Props): React.ReactElement {
  const [updateState, setUpdateState] = useState<UpdateUiState>(() => initialUpdateState());
  const [checkBusy, setCheckBusy] = useState(false);
  const [updateActionBusy, setUpdateActionBusy] = useState(false);
  const [releaseMetadata, setReleaseMetadata] = useState<ReleaseVersionMetadata | null>(null);
  const [brandSettingsUnlocked, setBrandSettingsUnlocked] = useState(false);
  const unlockProgressRef = useRef<BrandSettingsUnlockProgress>({ count: 0, startedAt: 0, unlocked: false });

  useEffect(() => {
    unlockProgressRef.current = { count: 0, startedAt: 0, unlocked: false };
    if (!canManageOrganizationBrand || !organizationScopeKey || typeof window === 'undefined') {
      setBrandSettingsUnlocked(false);
      return;
    }
    setBrandSettingsUnlocked(
      window.sessionStorage.getItem(organizationBrandUnlockSessionKey(organizationScopeKey)) === '1',
    );
  }, [canManageOrganizationBrand, organizationScopeKey]);

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
    let active = true;
    void window.yiyuWorkbench?.getOfficialUpdateStatus?.()
      .then((status) => {
        if (!active) return;
        const restored = stateFromStatus(status);
        if (restored) setUpdateState(restored);
      })
      .catch(() => undefined);
    return () => { active = false; };
  }, [desktopAppInfo?.appVersion]);

  useEffect(() => {
    const subscribe = window.yiyuWorkbench?.onUpdateEvent;
    if (typeof subscribe !== 'function') return;
    return subscribe((payload: UpdateEventPayload) => {
      switch (payload.kind) {
        case 'checking':
          setUpdateState((previous) => (
            previous.kind === 'ready-to-install' || previous.kind === 'official-push-opened'
              ? previous
              : { kind: 'checking' }
          ));
          return;
        case 'download-progress':
          setUpdateState((prev) => ({
            kind: 'downloading',
            version: prev.kind === 'official-push' ? prev.push.version : prev.kind === 'downloading' ? prev.version : undefined,
            percent: payload.percent,
            status: payload.updateStatus,
          }));
          return;
        case 'update-status':
        case 'ready-to-install':
        case 'downloaded':
        case 'installer-opened': {
          const restored = stateFromStatus(payload.updateStatus ?? null);
          if (restored) setUpdateState(restored);
          return;
        }
        case 'official-push-available':
          if (payload.officialPush) {
            setUpdateState((previous) => (
              previous.kind === 'ready-to-install' || previous.kind === 'official-push-opened' || previous.kind === 'downloading'
                ? previous
                : { kind: 'official-push', push: payload.officialPush! }
            ));
          }
          return;
        case 'official-push-not-available':
        case 'not-available':
          setUpdateState((previous) => (
            previous.kind === 'ready-to-install' || previous.kind === 'official-push-opened'
              ? previous
              : { kind: 'up-to-date' }
          ));
          return;
        case 'error':
          setUpdateState((previous) => (
            previous.kind === 'ready-to-install'
              ? previous
              : { kind: 'error', message: payload.message ?? '未知错误', status: payload.updateStatus }
          ));
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

  const handleDownloadOfficialPush = async (push: OfficialPushUpdatePayload) => {
    const trigger = window.yiyuWorkbench?.downloadOfficialPushUpdate;
    if (typeof trigger !== 'function') {
      setUpdateState({ kind: 'error', message: '当前安装包还不支持官网更新，请先安装迁移版本。' });
      return;
    }
    setUpdateActionBusy(true);
    setUpdateState({ kind: 'downloading', version: push.version, percent: 0 });
    try {
      const result = await trigger();
      if (!result.ok) {
        setUpdateState({ kind: 'error', message: result.reason ?? '下载安装包失败', status: result.status });
        return;
      }
      const restored = stateFromStatus(result.status ?? null);
      if (restored) setUpdateState(restored);
    } finally {
      setUpdateActionBusy(false);
    }
  };

  const handleInstallDownloadedUpdate = async () => {
    const trigger = window.yiyuWorkbench?.installDownloadedOfficialUpdate;
    if (typeof trigger !== 'function') {
      setUpdateState({ kind: 'error', message: '当前版本不能打开已下载的安装包，请重新下载安装迁移版。' });
      return;
    }
    setUpdateActionBusy(true);
    try {
      const result = await trigger();
      if (!result.ok) {
        setUpdateState({ kind: 'error', message: result.reason ?? '无法打开安装程序', status: result.status });
        return;
      }
      const restored = stateFromStatus(result.status ?? null);
      if (restored) setUpdateState(restored);
    } finally {
      setUpdateActionBusy(false);
    }
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
    : '安装盘已经打开。请双击“安装或更新益语智库AI”，再点击“安装并打开”。';

  const handleAboutLogoClick = () => {
    if (!canManageOrganizationBrand || !organizationScopeKey || brandSettingsUnlocked) return;
    const next = nextBrandSettingsUnlockProgress(unlockProgressRef.current, Date.now());
    unlockProgressRef.current = next;
    if (!next.unlocked) return;
    window.sessionStorage.setItem(organizationBrandUnlockSessionKey(organizationScopeKey), '1');
    setBrandSettingsUnlocked(true);
  };

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6">
      <div className="rounded-2xl border border-gray-100 bg-white p-6">
        <div className="flex items-start gap-4">
          {canManageOrganizationBrand ? (
            <button
              type="button"
              onClick={handleAboutLogoClick}
              className="shrink-0 rounded-xl p-1 outline-none transition focus-visible:ring-2 focus-visible:ring-indigo-200"
              aria-label="关于本软件 Logo"
            >
              <BrandLogoMark className="h-12 w-12" organizationScopeKey={organizationScopeKey} />
            </button>
          ) : (
            <BrandLogoMark className="h-12 w-12" organizationScopeKey={organizationScopeKey} />
          )}
          <div className="min-w-0 pt-0.5">
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">ABOUT</p>
            <h2 className="mt-2 text-[20px] font-light tracking-tight text-gray-900">关于本软件</h2>
            <p className="mt-1.5 text-[12px] text-gray-500">益语智库自用平台 V2.0 · 桌面版</p>
          </div>
        </div>
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

      {brandSettingsUnlocked && canManageOrganizationBrand && organizationScopeKey && (
        <OrganizationBrandSettingsCard organizationScopeKey={organizationScopeKey} />
      )}

      <div className="rounded-2xl border border-gray-100 bg-white p-6">
        <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">UPDATES</p>
        <h3 className="mt-2 text-[18px] font-light tracking-tight text-gray-900">软件更新</h3>
        <p className="mt-1.5 text-[12px] leading-6 text-gray-500">
          软件启动后会检查官网发布的新版本，联网使用期间也会定期重试。下载由后台持续执行，离开本页不会中断；
          下载完成后，下次打开软件或返回本页都会显示“立即安装”。macOS 打开安装盘后，双击“安装或更新益语智库AI”，
          再点击“安装并打开”，无需拖拽或手动删除旧版。
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
          {updateState.kind === 'ready-to-install' && (
            <div className="flex items-start gap-2 rounded-md border border-emerald-100 bg-emerald-50 px-3 py-3 text-[12px] text-emerald-800">
              <CheckCircle2 size={15} className="mt-[2px] shrink-0" />
              <div>
                <p className="font-semibold">版本 {updateState.status.version} 已下载完成，可以安装。</p>
                <p className="mt-1 text-emerald-700">安装包已通过大小和 SHA512 校验；关闭本页或重启软件后仍会保留。</p>
              </div>
            </div>
          )}
          {updateState.kind === 'official-push-opened' && (
            <div className="flex items-start gap-2 rounded-md bg-emerald-50 px-3 py-2 text-[12px] text-emerald-700">
              <CheckCircle2 size={14} className="mt-[2px] shrink-0" />
              <span>版本 {updateState.status.version} 的安装盘已经打开。{installHint}如果安装盘被关闭，可以再次打开。</span>
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
              <span className="break-words">更新未完成：{updateState.message}</span>
            </div>
          )}
        </div>

        <div className="mt-5 flex flex-wrap gap-3">
          <button
            type="button"
            onClick={handleCheck}
            disabled={checkBusy || updateActionBusy || updateState.kind === 'checking' || updateState.kind === 'downloading'}
            className="inline-flex items-center gap-2 rounded-md border border-gray-200 bg-white px-4 py-2 text-[13px] font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCw size={14} className={checkBusy || updateState.kind === 'checking' ? 'animate-spin' : ''} />检查更新
          </button>
          {updateState.kind === 'official-push' && (
            <>
              <button
                type="button"
                onClick={() => handleDownloadOfficialPush(updateState.push)}
                disabled={updateActionBusy}
                className="inline-flex items-center gap-2 rounded-md bg-[#5B7BFE] px-4 py-2 text-[13px] font-medium text-white hover:bg-[#4A6AEF] disabled:opacity-60"
              >
                <Download size={14} />{updateActionBusy ? '正在准备…' : '下载最新版'}
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
          {updateState.kind === 'ready-to-install' && (
            <button
              type="button"
              onClick={handleInstallDownloadedUpdate}
              disabled={updateActionBusy}
              className="inline-flex items-center gap-2 rounded-md bg-[#5B7BFE] px-4 py-2 text-[13px] font-medium text-white hover:bg-[#4A6AEF] disabled:opacity-60"
            >
              <PackageOpen size={14} />{updateActionBusy ? '正在打开…' : '立即安装'}
            </button>
          )}
          {updateState.kind === 'official-push-opened' && (
            <button
              type="button"
              onClick={handleInstallDownloadedUpdate}
              disabled={updateActionBusy}
              className="inline-flex items-center gap-2 rounded-md bg-[#5B7BFE] px-4 py-2 text-[13px] font-medium text-white hover:bg-[#4A6AEF] disabled:opacity-60"
            >
              <PackageOpen size={14} />{updateActionBusy ? '正在打开…' : '重新打开安装程序'}
            </button>
          )}
          {updateState.kind === 'error' && updateState.status && (
            <button
              type="button"
              onClick={() => handleDownloadOfficialPush(updateState.status!.update)}
              disabled={updateActionBusy}
              className="inline-flex items-center gap-2 rounded-md bg-[#5B7BFE] px-4 py-2 text-[13px] font-medium text-white hover:bg-[#4A6AEF] disabled:opacity-60"
            >
              <Download size={14} />{updateActionBusy ? '正在重试…' : updateState.status.canResume ? '继续下载' : '重新下载'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
