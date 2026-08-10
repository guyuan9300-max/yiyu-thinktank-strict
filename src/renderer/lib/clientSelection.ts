export type ClientSelectionStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

export type ClientSelectionScope = {
  sandboxId?: string | null;
  organizationId?: string | null;
};

const LAST_CLIENT_STORAGE_PREFIX = 'yiyu.workspace.lastClient.v2.';

function normalizedScopePart(value?: string | null): string {
  return (value || '').trim();
}

export function clientSelectionStorageKey(scope: ClientSelectionScope): string | null {
  const sandboxId = normalizedScopePart(scope.sandboxId);
  const organizationId = normalizedScopePart(scope.organizationId);
  if (!sandboxId || !organizationId) return null;
  return `${LAST_CLIENT_STORAGE_PREFIX}${encodeURIComponent(sandboxId)}.${encodeURIComponent(organizationId)}`;
}

export function readStoredClientId(
  storage: ClientSelectionStorage,
  scope: ClientSelectionScope,
): string | null {
  const key = clientSelectionStorageKey(scope);
  if (!key) return null;
  try {
    const value = storage.getItem(key)?.trim() || '';
    return value || null;
  } catch {
    return null;
  }
}

export function writeStoredClientId(
  storage: ClientSelectionStorage,
  scope: ClientSelectionScope,
  clientId: string,
): void {
  const key = clientSelectionStorageKey(scope);
  const normalizedClientId = clientId.trim();
  if (!key || !normalizedClientId) return;
  try {
    storage.setItem(key, normalizedClientId);
  } catch {
    // A disabled or full localStorage must not block project navigation.
  }
}

export function clearStoredClientId(
  storage: ClientSelectionStorage,
  scope: ClientSelectionScope,
): void {
  const key = clientSelectionStorageKey(scope);
  if (!key) return;
  try {
    storage.removeItem(key);
  } catch {
    // ignore
  }
}
