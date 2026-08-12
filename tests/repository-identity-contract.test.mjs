import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';


const root = process.cwd();
const marker = JSON.parse(
  fs.readFileSync(path.join(root, '.yiyu-strict-repository.json'), 'utf8'),
);
const collabSource = fs.readFileSync(
  path.join(root, 'src', 'main', 'strictCollabGit.ts'),
  'utf8',
);
const mainSource = fs.readFileSync(
  path.join(root, 'src', 'main', 'main.ts'),
  'utf8',
);
const preloadSource = fs.readFileSync(
  path.join(root, 'src', 'main', 'preload.ts'),
  'utf8',
);
const packageJson = JSON.parse(
  fs.readFileSync(path.join(root, 'package.json'), 'utf8'),
);

test('repository marker pins the strict GitHub identity and main branch', () => {
  assert.deepEqual(marker, {
    formatVersion: 1,
    kind: 'yiyu-strict-repository',
    genesisLabel: 'blueprint-88-foundation-v8-agent-skill-contract',
    githubRepository: 'guyuan9300-max/yiyu-thinktank-strict',
    githubRepositoryNumericId: 1316010273,
    githubRepositoryNodeId: 'R_kgDOTnC5IQ',
    remoteUrl: 'https://github.com/guyuan9300-max/yiyu-thinktank-strict.git',
    targetBranch: 'main',
    localManifestHash: '3b55180712dac2fac2e4257937aecc3afc583398fc61a8953ed390d82cf21d39',
    cloudManifestHash: '02c3d8ffe1b0d15dea14e70ff4e2b0cf53e1a19196cb42c72f34f72f9a848594',
  });
});

test('desktop collaboration calls the strict implementation instead of placeholders', () => {
  assert.match(mainSource, /from '\.\/strictCollabGit\.js'/);
  assert.match(mainSource, /strict:push-safely-to-main/);
  assert.match(mainSource, /strict:fast-forward-main/);
  assert.match(preloadSource, /strict:get-collab-repo-status/);
  assert.match(preloadSource, /strict:preview-push-to-main/);
  assert.equal(preloadSource.includes('严格新版暂未接入协作同步'), false);
});

test('collaboration implementation has no old repository or database fallback', () => {
  for (const markerText of [
    '/api/v1',
    'YiyuThinkTankWorkbench2',
    'app.db',
    'organization_cloud_proxy',
    'yiyu-thinktank-workbench.git',
  ]) {
    assert.equal(
      collabSource.includes(markerText),
      false,
      `old collaboration marker found: ${markerText}`,
    );
  }
  assert.match(collabSource, /githubRepositoryNumericId: 1316010273/);
  assert.match(collabSource, /HEAD:refs\/heads\/\$\{STRICT_REPOSITORY\.targetBranch\}/);
  assert.equal(collabSource.includes('--force'), false);
});

test('network git retries terminate the whole process tree and verify GitHub main by API', () => {
  assert.match(collabSource, /detached: process\.platform !== 'win32'/);
  assert.match(collabSource, /process\.kill\(-pid, 'SIGKILL'\)/);
  assert.match(collabSource, /taskkill[\s\S]*'\/T'[\s\S]*'\/F'/);
  assert.match(collabSource, /async function readRemoteMainOid/);
  assert.equal(
    collabSource.includes('git/ref/heads/${STRICT_REPOSITORY.targetBranch}?strict_collab=${cacheBuster}'),
    true,
  );
  assert.match(collabSource, /'Cache-Control': 'no-cache'/);
  assert.match(collabSource, /cache: 'no-store'/);
  assert.equal(
    collabSource.includes("'update-ref', 'refs/remotes/origin/main'"),
    true,
  );
  assert.match(collabSource, /if \(trackingAfterPush !== localHead\)/);
});

test('maintenance push uses the bounded publish gate instead of stale full-suite fixtures', () => {
  assert.match(collabSource, /resolveMaintenanceNpm/);
  assert.match(collabSource, /'\/usr\/local\/bin\/npm'/);
  assert.match(collabSource, /'\/opt\/homebrew\/bin\/npm'/);
  assert.match(collabSource, /runMaintenanceGate\(repoPath\)/);
  assert.match(collabSource, /\['run', 'verify:strict-maintenance'\]/);
  assert.match(packageJson.scripts['verify:strict-maintenance'], /repository-identity-contract/);
  assert.match(packageJson.scripts['verify:strict-maintenance'], /audit:strict/);
  assert.match(packageJson.scripts['verify:strict-maintenance'], /build:renderer/);
  assert.equal(
    packageJson.scripts['verify:strict-maintenance'].includes('test:strict'),
    false,
  );
});
