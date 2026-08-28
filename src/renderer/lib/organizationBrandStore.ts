import { useCallback, useEffect, useSyncExternalStore } from 'react';
import type { OrganizationBrandSettings } from '../../shared/types';
import { getOrganizationBrandSettings } from './api';

export const DEFAULT_ORGANIZATION_BRAND_NAME = '益语智库';

export interface OrganizationBrandState extends OrganizationBrandSettings {
  status: 'idle' | 'loading' | 'ready' | 'error';
  errorMessage: string | null;
}

const EMPTY_STATE: OrganizationBrandState = Object.freeze({
  displayName: '',
  logoDataUrl: '',
  version: 0,
  expectedVersion: 0,
  effectiveScopeKind: null,
  status: 'idle',
  errorMessage: null,
});

const states = new Map<string, OrganizationBrandState>();
const listeners = new Map<string, Set<() => void>>();
const requests = new Map<string, Promise<OrganizationBrandState>>();

function normalizedScopeKey(scopeKey: string | null | undefined): string {
  return String(scopeKey || '').trim();
}

function getState(scopeKey: string): OrganizationBrandState {
  return states.get(scopeKey) ?? EMPTY_STATE;
}

function emit(scopeKey: string): void {
  listeners.get(scopeKey)?.forEach((listener) => listener());
}

function replaceState(scopeKey: string, state: OrganizationBrandState): OrganizationBrandState {
  states.set(scopeKey, state);
  emit(scopeKey);
  return state;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  return '组织品牌设置暂时无法读取';
}

export async function loadOrganizationBrand(
  rawScopeKey: string | null | undefined,
  options?: { force?: boolean },
): Promise<OrganizationBrandState> {
  const scopeKey = normalizedScopeKey(rawScopeKey);
  if (!scopeKey) return EMPTY_STATE;
  const current = getState(scopeKey);
  if (!options?.force && current.status === 'ready') return current;
  const existing = requests.get(scopeKey);
  if (existing) return existing;

  replaceState(scopeKey, { ...current, status: 'loading', errorMessage: null });
  const operation = getOrganizationBrandSettings()
    .then((settings) => replaceState(scopeKey, {
      ...settings,
      displayName: String(settings.displayName || '').trim(),
      logoDataUrl: String(settings.logoDataUrl || '').trim(),
      status: 'ready',
      errorMessage: null,
    }))
    .catch((error) => replaceState(scopeKey, {
      ...getState(scopeKey),
      status: 'error',
      errorMessage: errorMessage(error),
    }))
    .finally(() => {
      requests.delete(scopeKey);
    });
  requests.set(scopeKey, operation);
  return operation;
}

export function publishOrganizationBrand(
  rawScopeKey: string | null | undefined,
  settings: OrganizationBrandSettings,
): OrganizationBrandState {
  const scopeKey = normalizedScopeKey(rawScopeKey);
  if (!scopeKey) return EMPTY_STATE;
  return replaceState(scopeKey, {
    ...settings,
    displayName: String(settings.displayName || '').trim(),
    logoDataUrl: String(settings.logoDataUrl || '').trim(),
    status: 'ready',
    errorMessage: null,
  });
}

export function useOrganizationBrand(rawScopeKey: string | null | undefined): OrganizationBrandState {
  const scopeKey = normalizedScopeKey(rawScopeKey);
  const subscribe = useCallback((listener: () => void) => {
    if (!scopeKey) return () => undefined;
    const scopedListeners = listeners.get(scopeKey) ?? new Set<() => void>();
    scopedListeners.add(listener);
    listeners.set(scopeKey, scopedListeners);
    return () => {
      scopedListeners.delete(listener);
      if (scopedListeners.size === 0) listeners.delete(scopeKey);
    };
  }, [scopeKey]);
  const snapshot = useCallback(() => getState(scopeKey), [scopeKey]);

  useEffect(() => {
    if (scopeKey) void loadOrganizationBrand(scopeKey);
  }, [scopeKey]);

  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

export function organizationBrandDisplayName(state: OrganizationBrandState): string {
  return state.displayName || DEFAULT_ORGANIZATION_BRAND_NAME;
}
