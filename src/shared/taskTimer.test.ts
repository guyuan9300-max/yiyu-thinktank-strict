import test from 'node:test';
import assert from 'node:assert/strict';

import {
  formatTaskTimerDuration,
  projectedTaskTimerSeconds,
} from './taskTimer.js';

test('task timer renders a stable stopwatch duration', () => {
  assert.equal(formatTaskTimerDuration(0), '00:00:00');
  assert.equal(formatTaskTimerDuration(3661.9), '01:01:01');
  assert.equal(formatTaskTimerDuration(-10), '00:00:00');
});

test('running task timer advances only after the server observation time', () => {
  const observedAt = '2026-08-24T01:00:00.000Z';
  assert.equal(projectedTaskTimerSeconds({
    state: 'running',
    elapsedSeconds: 90,
    activeStartedAt: '2026-08-24T00:58:30.000Z',
    latestRunId: 'run-1',
    version: 1,
    observedAt,
  }, Date.parse('2026-08-24T01:00:12.900Z')), 102);
});

test('paused and stopped task timers remain at the confirmed total', () => {
  for (const state of ['paused', 'stopped'] as const) {
    assert.equal(projectedTaskTimerSeconds({
      state,
      elapsedSeconds: 125,
      latestRunId: 'run-1',
      version: 2,
      observedAt: '2026-08-24T01:00:00.000Z',
    }, Date.parse('2026-08-24T02:00:00.000Z')), 125);
  }
});
