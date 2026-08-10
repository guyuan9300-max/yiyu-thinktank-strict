import assert from 'node:assert/strict';
import test from 'node:test';

import {
  assertEventLineClient,
  canAttachTaskToEventLine,
  primaryActionTaskStatus,
  weeklyReviewStableKey,
} from './gc06Contract';

test('weekly review identity is membership plus planning cycle', () => {
  assert.equal(weeklyReviewStableKey('member-1', 'cycle-1'), 'member-1::cycle-1');
  assert.throws(() => weeklyReviewStableKey('', 'cycle-1'));
});

test('event line client and task binding contracts are strict', () => {
  assert.equal(assertEventLineClient(' client-1 '), 'client-1');
  assert.throws(() => assertEventLineClient(null));
  assert.equal(canAttachTaskToEventLine('client-1', 'client-1'), true);
  assert.equal(canAttachTaskToEventLine(null, 'client-1'), false);
  assert.equal(canAttachTaskToEventLine('client-2', 'client-1'), false);
});

test('primary action reports the formal task adapter boundary', () => {
  const base = {
    id: 'action-1', recordKind: 'plan_action' as const, planningCycleId: 'cycle-1',
    clientId: null, decisionState: 'proposed', title: '下一步', statement: '',
    expectedOutput: '', ownerMembershipId: null, version: 1,
  };
  assert.equal(primaryActionTaskStatus({ ...base, taskId: null }).connected, false);
  assert.equal(primaryActionTaskStatus({ ...base, taskId: 'task-1' }).connected, true);
});
