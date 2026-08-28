import assert from 'node:assert/strict';
import test from 'node:test';

import {
  isActiveKnowledgeJobStatus,
  shouldPollKnowledgeProgress,
} from './knowledgeJobState.js';

test('only queued and running knowledge jobs are active', () => {
  assert.equal(isActiveKnowledgeJobStatus('queued'), true);
  assert.equal(isActiveKnowledgeJobStatus('running'), true);
  assert.equal(isActiveKnowledgeJobStatus('interrupted'), false);
  assert.equal(isActiveKnowledgeJobStatus('blocked'), false);
  assert.equal(isActiveKnowledgeJobStatus('completed'), false);
});

test('terminal jobs never keep the progress poller alive', () => {
  assert.equal(
    shouldPollKnowledgeProgress({
      isSubmitting: false,
      pendingJobs: 0,
      runningJobs: 0,
    }),
    false,
  );
  assert.equal(
    shouldPollKnowledgeProgress({
      isSubmitting: false,
      pendingJobs: 1,
      runningJobs: 0,
    }),
    true,
  );
  assert.equal(
    shouldPollKnowledgeProgress({
      isSubmitting: true,
      pendingJobs: 0,
      runningJobs: 0,
    }),
    true,
  );
});
