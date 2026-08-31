import assert from 'node:assert/strict';
import test from 'node:test';

import { requestPauseRunningTaskTimers } from './taskTimerShutdown.js';

test('桌面退出前通过已授权本地链路暂停当前成员计时', async () => {
  let capturedUrl = '';
  let capturedInit: RequestInit | undefined;
  const result = await requestPauseRunningTaskTimers({
    port: 51943,
    desktopToken: 'desktop-test-token',
    reason: 'app_quit',
    fetchImpl: (async (url, init) => {
      capturedUrl = String(url);
      capturedInit = init;
      return new Response(JSON.stringify({ state: 'paused', pausedCount: 2 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }) as typeof fetch,
  });

  assert.equal(
    capturedUrl,
    'http://127.0.0.1:51943/api/v2/ui/tasks/timers/pause-running',
  );
  assert.equal(capturedInit?.method, 'POST');
  assert.equal(
    (capturedInit?.headers as Record<string, string>)['X-Yiyu-Desktop-Token'],
    'desktop-test-token',
  );
  assert.match(
    (capturedInit?.headers as Record<string, string>)['Idempotency-Key'],
    /^desktop-timer-pause-app_quit-/,
  );
  assert.deepEqual(JSON.parse(String(capturedInit?.body)), { reason: 'app_quit' });
  assert.deepEqual(result, { state: 'paused', pausedCount: 2 });
});

test('退出前暂停失败时不会伪装成成功', async () => {
  await assert.rejects(
    requestPauseRunningTaskTimers({
      port: 51943,
      desktopToken: 'desktop-test-token',
      reason: 'window_closed',
      fetchImpl: (async () => new Response('{}', { status: 503 })) as typeof fetch,
    }),
    /状态码 503/,
  );
});
