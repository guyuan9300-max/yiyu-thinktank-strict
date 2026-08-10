import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { classifyWorkingTreeChanges } from './strictCollabGit.js';

describe('classifyWorkingTreeChanges', () => {
  it('separates permission-only noise from real code changes', () => {
    const result = classifyWorkingTreeChanges(
      ' M README.md',
      '0\t0\tREADME.md',
      ' mode change 100644 => 100755 README.md',
    );

    assert.deepEqual(result.changes, []);
    assert.equal(result.permissionOnlyChangeCount, 1);
  });

  it('keeps a content change even when the file mode also changed', () => {
    const result = classifyWorkingTreeChanges(
      ' M src/main/main.ts',
      '2\t1\tsrc/main/main.ts',
      ' mode change 100644 => 100755 src/main/main.ts',
    );

    assert.equal(result.permissionOnlyChangeCount, 0);
    assert.deepEqual(
      result.changes.map(({ path, type }) => ({ path, type })),
      [{ path: 'src/main/main.ts', type: 'modified' }],
    );
  });

  it('keeps untracked, added and renamed files as real changes', () => {
    const result = classifyWorkingTreeChanges(
      [
        '?? empty.txt',
        'A  src/new.ts',
        'R  src/old.ts -> src/renamed.ts',
      ].join('\n'),
      '',
      '',
    );

    assert.equal(result.permissionOnlyChangeCount, 0);
    assert.deepEqual(
      result.changes.map(({ path, previousPath, type }) => ({ path, previousPath, type })),
      [
        { path: 'empty.txt', previousPath: null, type: 'untracked' },
        { path: 'src/new.ts', previousPath: null, type: 'added' },
        { path: 'src/renamed.ts', previousPath: 'src/old.ts', type: 'renamed' },
      ],
    );
  });
});
