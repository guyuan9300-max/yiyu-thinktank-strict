import assert from 'node:assert/strict';
import test from 'node:test';

import { ScopedAsyncGate, scopedAsyncKey } from './scopedAsyncGate';

test('late response from the previous project is rejected', () => {
  const gate = new ScopedAsyncGate();
  const projectA = scopedAsyncKey('sandbox-1', 'organization-1', 'project-a');
  const projectB = scopedAsyncKey('sandbox-1', 'organization-1', 'project-b');
  const requestA = gate.begin(projectA);
  assert.equal(gate.accepts(requestA), true);

  const requestB = gate.begin(projectB);
  assert.equal(requestA.signal.aborted, true);
  assert.equal(gate.accepts(requestA), false);
  assert.equal(gate.accepts(requestB), true);
});

test('a newer request supersedes an older request in the same project', () => {
  const gate = new ScopedAsyncGate();
  const scope = scopedAsyncKey('sandbox-1', 'organization-1', 'project-a');
  const first = gate.begin(scope);
  const second = gate.begin(scope);
  assert.equal(first.signal.aborted, true);
  assert.equal(gate.accepts(first), false);
  assert.equal(gate.accepts(second), true);
});
