import assert from 'node:assert/strict';
import test from 'node:test';
import type { OfficialPushUpdatePayload } from '../shared/types.js';
import {
  advanceUpdateProgress,
  createPersistedUpdateOperation,
  normalizeUpdaterErrorMessage,
  parsePersistedUpdaterState,
  reconcilePersistedUpdaterState,
} from './officialUpdaterState.js';

const update: OfficialPushUpdatePayload = {
  title: '发现益语智库新版本：0.29.5',
  releaseId: 'release-0.29.5',
  version: '0.29.5',
  releaseVersion: '0.29.5',
  currentVersion: '0.29.4',
  packageKind: 'release',
  fileName: 'yiyu-thinktank-strict-0.29.5-arm64.dmg',
  sizeBytes: 1_000,
  sha512: Buffer.alloc(64, 5).toString('base64'),
  downloadUrl: 'https://yiyu.love/desktop-updates/packages/release/release-0.29.5/mac',
  userNotes: { fixes: ['更新恢复测试'] },
  relation: 'upgrade',
};

test('下载完成状态可序列化并在重启后恢复为待安装', () => {
  const operation = createPersistedUpdateOperation(update, '/safe/update.dmg', '/safe/update.dmg.download');
  operation.status = 'ready-to-install';
  operation.transferred = 1_000;
  operation.percent = 100;
  const restored = parsePersistedUpdaterState(JSON.parse(JSON.stringify({
    schemaVersion: 1,
    lastSuccessfulCheckAt: '2026-08-27T00:00:00.000Z',
    operation,
  })));

  assert.equal(restored.operation?.status, 'ready-to-install');
  assert.equal(restored.operation?.operationId, operation.operationId);
  assert.equal(restored.operation?.update.version, '0.29.5');
  assert.equal(restored.operation?.percent, 100);
});

test('同一发布重试沿用稳定操作编号且进度只能单调前进', () => {
  const first = createPersistedUpdateOperation(update, '/safe/update.dmg', '/safe/update.dmg.download');
  const retried = createPersistedUpdateOperation(update, '/safe/update.dmg', '/safe/update.dmg.download');
  assert.equal(first.operationId, retried.operationId);

  const progressed = advanceUpdateProgress({ ...first, transferred: 600, percent: 60 }, 400, 1_000);
  assert.equal(progressed.transferred, 600);
  assert.equal(progressed.percent, 60);
  assert.equal(progressed.total, 1_000);
});

test('当前版本已经追上待安装版本时清理过期回执', async () => {
  const operation = createPersistedUpdateOperation(update, '/safe/update.dmg', '/safe/update.dmg.download');
  operation.status = 'ready-to-install';
  const reconciled = await reconcilePersistedUpdaterState(
    { schemaVersion: 1, lastSuccessfulCheckAt: null, operation },
    '0.29.5',
    async () => ({ exists: true, sizeBytes: 1_000, sha512: update.sha512! }),
  );
  assert.equal(reconciled.operation, null);
});

test('待安装文件缺失或校验失败时不再误报可以安装', async () => {
  const operation = createPersistedUpdateOperation(update, '/safe/update.dmg', '/safe/update.dmg.download');
  operation.status = 'ready-to-install';
  const missing = await reconcilePersistedUpdaterState(
    { schemaVersion: 1, lastSuccessfulCheckAt: null, operation },
    '0.29.4',
    async () => ({ exists: false, sizeBytes: 0, sha512: null }),
  );
  assert.equal(missing.operation?.status, 'failed');
  assert.match(missing.operation?.lastError || '', /重新下载/);

  const corrupt = await reconcilePersistedUpdaterState(
    { schemaVersion: 1, lastSuccessfulCheckAt: null, operation },
    '0.29.4',
    async () => ({ exists: true, sizeBytes: 999, sha512: 'wrong' }),
  );
  assert.equal(corrupt.operation?.status, 'failed');
  assert.match(corrupt.operation?.lastError || '', /校验/);
});

test('用户错误信息不泄露本机绝对路径并识别旧安装包结构错误', () => {
  const raw = "ENOENT: no such file or directory, access '/Users/clz/Library/Application Support/Yiyu/mounted/益语智库AI（新版）.app/Contents/Info.plist'";
  const message = normalizeUpdaterErrorMessage(new Error(raw));
  assert.equal(message.includes('/Users/clz'), false);
  assert.match(message, /安装包结构与当前更新程序不兼容/);
});
