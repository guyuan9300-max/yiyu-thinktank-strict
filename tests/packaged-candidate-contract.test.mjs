import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import { extractFile, listPackage } from '@electron/asar';


const root = process.cwd();
const app = path.join(
  root,
  'dist',
  'mac-arm64',
  '益语智库AI（新版）.app',
);
const resources = path.join(app, 'Contents', 'Resources');
const archive = path.join(resources, 'app.asar');

test('packaged candidate has the strict app identity', () => {
  assert.ok(fs.statSync(app).isDirectory(), 'packaged app is missing');
  const info = fs.readFileSync(path.join(app, 'Contents', 'Info.plist'), 'utf8');
  assert.match(info, /<string>com\.yiyu\.thinktank\.strict<\/string>/);
  assert.match(info, /<string>益语智库AI（新版）<\/string>/);
});

test('packaged resources contain only the asar, icon, and frozen backend', () => {
  assert.deepEqual(
    fs.readdirSync(resources).sort(),
    ['app.asar', 'backend-dist', 'icon.icns'],
  );
  const backend = path.join(resources, 'backend-dist', 'yiyu-strict-backend');
  const stat = fs.statSync(backend);
  assert.ok(stat.isFile() && stat.size > 1_000_000);
  assert.notEqual(stat.mode & 0o111, 0, 'frozen backend is not executable');
});

test('app asar contains no source backend, database, or build intermediates', () => {
  const entries = listPackage(archive);
  const forbidden = entries.filter((entry) => (
    /(?:pyinstaller|node_modules|__pycache__)/i.test(entry)
    || /\.(?:py|pyc|db|sqlite|sqlite3)$/i.test(entry)
    || entry.startsWith('/backend')
    || entry.startsWith('/cloud_backend')
    || entry.startsWith('/strict_common')
    || entry.startsWith('/contracts')
  ));
  assert.deepEqual(forbidden, []);
  assert.deepEqual(entries, [
    '/build',
    '/build/main',
    '/build/main/main',
    '/build/main/main/main.js',
    '/build/main/main/preload.js',
    '/build/main/main/strictCollabGit.js',
    '/build/main/shared',
    '/build/main/shared/types.js',
    '/dist',
    '/dist/renderer',
    '/dist/renderer/assets',
    ...entries.filter((entry) => entry.startsWith('/dist/renderer/assets/')),
    '/dist/renderer/index.html',
    '/package.json',
  ]);
  assert.ok(entries.includes('/build/main/main/main.js'));
  assert.ok(entries.includes('/build/main/main/preload.js'));
  assert.ok(entries.includes('/dist/renderer/index.html'));
});

test('compiled runtime has no legacy endpoint or data fallback', () => {
  const entries = listPackage(archive).filter((entry) => /\.(?:js|html)$/.test(entry));
  const source = entries
    .map((entry) => extractFile(archive, entry.replace(/^\//, '')).toString('utf8'))
    .join('\n');
  for (const marker of [
    '/api/v1',
    'YiyuThinkTankWorkbench2',
    'organization_cloud_proxy',
    'latest.yml',
    'active_business_sandbox_id',
  ]) {
    assert.equal(source.includes(marker), false, `legacy marker packaged: ${marker}`);
  }
  assert.ok(source.includes('/api/v2'));
  assert.ok(source.includes('YiyuThinkTankStrictV1'));
});
