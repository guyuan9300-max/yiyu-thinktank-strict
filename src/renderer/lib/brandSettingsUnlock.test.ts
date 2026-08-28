import assert from 'node:assert/strict';
import test from 'node:test';
import {
  nextBrandSettingsUnlockProgress,
  organizationBrandUnlockSessionKey,
} from './brandSettingsUnlock';

test('organization brand settings unlock after five clicks in one four-second window', () => {
  let progress = { count: 0, startedAt: 0, unlocked: false };
  for (const clickedAt of [1_000, 1_400, 1_900, 2_300]) {
    progress = nextBrandSettingsUnlockProgress(progress, clickedAt);
    assert.equal(progress.unlocked, false);
  }
  progress = nextBrandSettingsUnlockProgress(progress, 2_800);
  assert.equal(progress.unlocked, true);
  assert.equal(progress.count, 5);
});

test('organization brand settings click progress expires and stays organization-scoped', () => {
  const first = nextBrandSettingsUnlockProgress(
    { count: 4, startedAt: 1_000, unlocked: false },
    5_001,
  );
  assert.deepEqual(first, { count: 1, startedAt: 5_001, unlocked: false });
  assert.notEqual(
    organizationBrandUnlockSessionKey('organization-a'),
    organizationBrandUnlockSessionKey('organization-b'),
  );
});
