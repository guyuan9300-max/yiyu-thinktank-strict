import assert from 'node:assert/strict';
import test from 'node:test';

import {
  clearRuntimeUiSessionValuesForTest,
  readRuntimeUiSessionValue,
  writeRuntimeUiSessionValue,
} from './runtimeUiSessionStore';

test('runtime UI state is isolated by its full sandbox/member/page key', () => {
  clearRuntimeUiSessionValuesForTest();
  const yiyu = 'sandbox-yiyu:membership-a:tasks:list';
  const xingcong = 'sandbox-xingcong:membership-a:tasks:list';
  writeRuntimeUiSessionValue(yiyu, { search: '日慈', collapsed: false });
  assert.deepEqual(readRuntimeUiSessionValue(yiyu, {}), { search: '日慈', collapsed: false });
  assert.deepEqual(readRuntimeUiSessionValue(xingcong, { search: '', collapsed: true }), {
    search: '',
    collapsed: true,
  });
});

test('clearing the renderer-session store restores product defaults', () => {
  clearRuntimeUiSessionValuesForTest();
  writeRuntimeUiSessionValue('scope', 'changed');
  clearRuntimeUiSessionValuesForTest();
  assert.equal(readRuntimeUiSessionValue('scope', 'default'), 'default');
});

