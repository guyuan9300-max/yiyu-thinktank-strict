import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  clientSelectionStorageKey,
  clearStoredClientId,
  readStoredClientId,
  writeStoredClientId,
} from './clientSelection';

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => void values.set(key, value),
    removeItem: (key: string) => void values.delete(key),
  };
}

test('last selected client is isolated by sandbox and organization', () => {
  const storage = memoryStorage();
  writeStoredClientId(storage, { sandboxId: 'sandbox-a', organizationId: 'org-a' }, 'client-a');
  writeStoredClientId(storage, { sandboxId: 'sandbox-b', organizationId: 'org-b' }, 'client-b');

  assert.equal(readStoredClientId(storage, { sandboxId: 'sandbox-a', organizationId: 'org-a' }), 'client-a');
  assert.equal(readStoredClientId(storage, { sandboxId: 'sandbox-b', organizationId: 'org-b' }), 'client-b');
  assert.equal(readStoredClientId(storage, { sandboxId: 'sandbox-a', organizationId: 'org-b' }), null);
});

test('incomplete scope is never persisted as a global fallback', () => {
  const storage = memoryStorage();
  assert.equal(clientSelectionStorageKey({ sandboxId: 'sandbox-a', organizationId: null }), null);
  writeStoredClientId(storage, { sandboxId: 'sandbox-a', organizationId: null }, 'client-a');
  assert.equal(readStoredClientId(storage, { sandboxId: 'sandbox-a', organizationId: null }), null);
});

test('stored selection can be cleared without touching another workspace', () => {
  const storage = memoryStorage();
  const scopeA = { sandboxId: 'sandbox-a', organizationId: 'org-a' };
  const scopeB = { sandboxId: 'sandbox-b', organizationId: 'org-b' };
  writeStoredClientId(storage, scopeA, 'client-a');
  writeStoredClientId(storage, scopeB, 'client-b');
  clearStoredClientId(storage, scopeA);

  assert.equal(readStoredClientId(storage, scopeA), null);
  assert.equal(readStoredClientId(storage, scopeB), 'client-b');
});

test('renderer never assigns project scope by matching a project name', () => {
  const appSource = readFileSync(fileURLToPath(new URL('../App.tsx', import.meta.url)), 'utf8');
  const commandModalSource = readFileSync(
    fileURLToPath(new URL('../components/ai_command/AICommandModal.tsx', import.meta.url)),
    'utf8',
  );

  assert.equal(appSource.includes('function inferTaskClient('), false);
  assert.equal(commandModalSource.includes('clientsForResolve.find((c) => c.name === parsed.client_name)'), false);
  assert.match(commandModalSource, /setChosenClientId\(defaultClientId \|\| null\)/);
});
