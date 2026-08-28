import assert from 'node:assert/strict';
import test from 'node:test';

import { shouldLoadTaskContextBrief } from './taskContextBriefPolicy.js';

test('task context brief loading rejects drafts and personal-only tasks', () => {
  assert.equal(shouldLoadTaskContextBrief({ isLocalDraft: true, clientId: 'project-1' }), false);
  assert.equal(shouldLoadTaskContextBrief({ isLocalDraft: false, scopeMode: 'PERSONAL_ONLY', clientId: 'project-1' }), false);
});

test('task context brief loading requires authority context and refreshes previews', () => {
  assert.equal(shouldLoadTaskContextBrief({ isLocalDraft: false }), false);
  assert.equal(shouldLoadTaskContextBrief({ isLocalDraft: false, eventLineId: 'event-1' }), true);
  assert.equal(shouldLoadTaskContextBrief({ isLocalDraft: false, clientId: 'project-1' }, ['ready']), false);
  assert.equal(shouldLoadTaskContextBrief({ isLocalDraft: false, clientId: 'project-1' }, ['preview_only']), true);
});
