import assert from 'node:assert/strict';
import test from 'node:test';

import { transitionMiniAiQuickTaskFlow } from './miniAiQuickTaskFlow';

test('AI quick task only returns to mini after parse, review and authoritative save', () => {
  let state = transitionMiniAiQuickTaskFlow('idle', 'open');
  assert.deepEqual(state, { stage: 'ai-input', shouldReturnToMini: false });

  state = transitionMiniAiQuickTaskFlow(state.stage, 'parsed');
  assert.deepEqual(state, { stage: 'task-editor', shouldReturnToMini: false });

  state = transitionMiniAiQuickTaskFlow(state.stage, 'save-started');
  assert.deepEqual(state, { stage: 'saving', shouldReturnToMini: false });

  state = transitionMiniAiQuickTaskFlow(state.stage, 'save-succeeded');
  assert.deepEqual(state, { stage: 'idle', shouldReturnToMini: true });
});

test('closing either modal clears the one-shot return intent', () => {
  const closedFromInput = transitionMiniAiQuickTaskFlow('ai-input', 'dismissed');
  assert.deepEqual(closedFromInput, { stage: 'idle', shouldReturnToMini: false });

  const closedFromEditor = transitionMiniAiQuickTaskFlow('task-editor', 'dismissed');
  assert.deepEqual(closedFromEditor, { stage: 'idle', shouldReturnToMini: false });

  const unrelatedSave = transitionMiniAiQuickTaskFlow(closedFromEditor.stage, 'save-succeeded');
  assert.equal(unrelatedSave.shouldReturnToMini, false);
});

test('failed save restores review state and a retry may return to mini', () => {
  const failed = transitionMiniAiQuickTaskFlow('saving', 'save-failed');
  assert.deepEqual(failed, { stage: 'task-editor', shouldReturnToMini: false });

  const retrying = transitionMiniAiQuickTaskFlow(failed.stage, 'save-started');
  const retried = transitionMiniAiQuickTaskFlow(retrying.stage, 'save-succeeded');
  assert.equal(retried.shouldReturnToMini, true);
});

test('late save callbacks cannot collapse a newer AI input session', () => {
  const reopened = transitionMiniAiQuickTaskFlow('saving', 'open');
  assert.equal(reopened.stage, 'ai-input');

  const lateSuccess = transitionMiniAiQuickTaskFlow(reopened.stage, 'save-succeeded');
  assert.deepEqual(lateSuccess, { stage: 'ai-input', shouldReturnToMini: false });
});
