import { useEffect } from 'react';
import type { OfficialPushUpdatePayload, UpdateEventPayload } from '../../shared/types';

/**
 * 订阅官网更新事件并缓存当前版本提示。发现新版时由 App 显示站内通知；
 * 安装包只在用户于“关于本软件”中确认后下载，不静默安装。
 */

export const UPDATE_STATE_KEY = '__yiyuUpdateState__';
export const OFFICIAL_PUSH_STATE_EVENT = 'yiyu-official-push-state-changed';

interface CachedUpdateState {
  lastEvent: UpdateEventPayload | null;
  latestVersion: string | null;
  downloadedVersion: string | null;
  isDownloaded: boolean;
  isDownloading: boolean;
  lastError: string | null;
  officialPush: OfficialPushUpdatePayload | null;
}

declare global {
  interface Window {
    [UPDATE_STATE_KEY]?: CachedUpdateState;
  }
}

function ensureSlot(): CachedUpdateState {
  if (!window[UPDATE_STATE_KEY]) {
    window[UPDATE_STATE_KEY] = {
      lastEvent: null,
      latestVersion: null,
      downloadedVersion: null,
      isDownloaded: false,
      isDownloading: false,
      lastError: null,
      officialPush: null,
    };
  }
  return window[UPDATE_STATE_KEY]!;
}

function notifyOfficialPushStateChanged(push: OfficialPushUpdatePayload | null): void {
  window.dispatchEvent(new CustomEvent(OFFICIAL_PUSH_STATE_EVENT, { detail: push }));
}

export function setCachedOfficialPush(push: OfficialPushUpdatePayload | null): void {
  const slot = ensureSlot();
  slot.officialPush = push;
  slot.lastEvent = {
    kind: push ? 'official-push-available' : 'official-push-not-available',
    version: push?.version,
    officialPush: push,
  };
  if (push) {
    slot.latestVersion = push.version;
    slot.lastError = null;
  }
  notifyOfficialPushStateChanged(push);
}

export function UpdateNotifier(): null {
  useEffect(() => {
    const subscribe = window.yiyuWorkbench?.onUpdateEvent;
    if (typeof subscribe !== 'function') return;

    const unsubscribe = subscribe((payload: UpdateEventPayload) => {
      const slot = ensureSlot();
      slot.lastEvent = payload;
      switch (payload.kind) {
        case 'official-push-available':
          setCachedOfficialPush(payload.officialPush ?? null);
          slot.latestVersion = payload.officialPush?.version ?? payload.version ?? slot.latestVersion;
          slot.isDownloading = false;
          slot.lastError = null;
          console.log('[updater] official push available:', payload.officialPush?.title || payload.version);
          return;
        case 'official-push-not-available':
          setCachedOfficialPush(null);
          slot.isDownloading = false;
          return;
        case 'available':
          slot.latestVersion = payload.version ?? slot.latestVersion;
          slot.isDownloading = true;
          slot.lastError = null;
          console.log('[updater] new version available:', payload.version);
          return;
        case 'download-progress':
          slot.isDownloading = true;
          return;
        case 'downloaded':
          slot.downloadedVersion = payload.version ?? slot.latestVersion;
          slot.isDownloaded = true;
          slot.isDownloading = false;
          slot.lastError = null;
          console.log('[updater] installer downloaded:', payload.version);
          return;
        case 'not-available':
          slot.isDownloading = false;
          return;
        case 'error':
          slot.isDownloading = false;
          slot.lastError = payload.message ?? 'unknown update error';
          console.warn('[updater] error:', payload.message);
          return;
        case 'checking':
        default:
          return;
      }
    });

    return unsubscribe;
  }, []);

  return null;
}
