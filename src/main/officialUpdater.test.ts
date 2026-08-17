import assert from 'node:assert/strict';
import test from 'node:test';
import {
  buildOfficialUpdate,
  compareStrictVersions,
  parseStrictVersion,
} from './officialUpdater.js';

const validManifest = {
  releaseId: 'release-test',
  version: '0.29.3',
  fileName: 'yiyu-thinktank-strict-0.29.3-arm64.dmg',
  sizeBytes: 1024,
  sha512: Buffer.alloc(64, 7).toString('base64'),
  downloadUrl: 'https://yiyu.love/desktop-updates/packages/release/release-test/mac',
  userNotes: { fixes: ['测试更新发现'] },
};

test('官网更新仅接受可比较的严格语义版本', () => {
  assert.deepEqual(parseStrictVersion('v0.29.3'), [0, 29, 3]);
  assert.equal(compareStrictVersions('0.29.3', '0.29.2'), 1);
  assert.equal(compareStrictVersions('0.29.2', '0.29.2'), 0);
  assert.equal(compareStrictVersions('0.29.1', '0.29.2'), -1);
  assert.equal(compareStrictVersions('latest', '0.29.2'), null);
});

test('官网发布更高版本后生成可提示的正式更新', () => {
  const update = buildOfficialUpdate(validManifest, '0.29.2');
  assert.equal(update?.version, '0.29.3');
  assert.equal(update?.relation, 'upgrade');
  assert.equal(update?.downloadUrl, validManifest.downloadUrl);
});

test('同版本或较低版本不得提示更新', () => {
  assert.equal(buildOfficialUpdate({ ...validManifest, version: '0.29.2' }, '0.29.2'), null);
  assert.equal(buildOfficialUpdate({ ...validManifest, version: '0.29.1' }, '0.29.2'), null);
});

test('更高版本清单缺少完整性字段或 HTTPS 地址时阻断', () => {
  assert.throws(
    () => buildOfficialUpdate({ ...validManifest, sha512: null }, '0.29.2'),
    /SHA512/,
  );
  assert.throws(
    () => buildOfficialUpdate({ ...validManifest, downloadUrl: 'http://example.com/update.dmg' }, '0.29.2'),
    /HTTPS/,
  );
});
