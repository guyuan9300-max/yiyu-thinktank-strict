import test from 'node:test';
import assert from 'node:assert/strict';

import { filterSharedTasks, isPersonalOnlyTask } from './taskVisibility.js';

test('isPersonalOnlyTask treats PERSONAL_ONLY scope and self tags as private', () => {
  assert.equal(isPersonalOnlyTask({ scopeMode: 'PERSONAL_ONLY', tags: [] }), true);
  assert.equal(isPersonalOnlyTask({ scopeMode: 'COLLAB_SHARED', tags: [{ scope: 'self' }] }), true);
  assert.equal(isPersonalOnlyTask({ scopeMode: 'COLLAB_SHARED', tags: [{ scope: 'org' }] }), false);
});

test('filterSharedTasks removes private tasks from cross-module lists', () => {
  const tasks = [
    { id: 'shared', title: '共享任务', scopeMode: 'COLLAB_SHARED', tags: [] },
    { id: 'personal_scope', title: '私人任务', scopeMode: 'PERSONAL_ONLY', tags: [] },
    { id: 'self_tag', title: '自用标签任务', scopeMode: 'COLLAB_SHARED', tags: [{ scope: 'self' }] },
  ];

  assert.deepEqual(
    filterSharedTasks(tasks).map((task) => task.id),
    ['shared'],
  );
});
