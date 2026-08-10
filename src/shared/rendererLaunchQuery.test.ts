import assert from 'node:assert/strict';
import test from 'node:test';

import { buildRendererLaunchQuery, removeSettingsLaunchNavigation } from './rendererLaunchQuery.js';

test('packaged launch removes settings tab and keeps default workspace thread', () => {
  assert.equal(
    buildRendererLaunchQuery('tab=settings&settingsSection=overview', { packaged: true }),
    '?workspaceThread=latest',
  );
});

test('legacy activeTab=settings launch is also removed', () => {
  assert.equal(
    buildRendererLaunchQuery('?activeTab=settings&section=system_logs', { packaged: true }),
    '?workspaceThread=latest',
  );
});

test('non-settings launch tab is preserved', () => {
  assert.equal(
    buildRendererLaunchQuery('tab=strategic_accompaniment&workspaceThread=fresh', { packaged: true }),
    '?tab=strategic_accompaniment&workspaceThread=fresh',
  );
});

test('unpackaged launch does not add workspace thread', () => {
  assert.equal(buildRendererLaunchQuery('tab=settings&settingsSection=overview', { packaged: false }), '');
});

test('settings cleanup removes both modern and legacy settings keys', () => {
  const params = new URLSearchParams('tab=tasks&activeTab=settings&settingsSection=overview&section=tasks&clientId=1');

  assert.equal(removeSettingsLaunchNavigation(params), true);
  assert.equal(params.toString(), 'clientId=1');
});
