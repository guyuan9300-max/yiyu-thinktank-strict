import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildFileSearchBriefAnswer,
  buildFileSearchDisplayGroups,
  isOriginalFileSearchHit,
  normalizeSearchSupportLevel,
  pickPrimarySearchHit,
} from './workspaceFileSearchPresentation.js';
import type { DataCenterSearchHit } from './types.js';

function hit(overrides: Partial<DataCenterSearchHit>): DataCenterSearchHit {
  return {
    title: '资料.docx',
    excerpt: '这是一段资料片段。',
    sourceType: 'raw_document',
    selectedForAnswer: false,
    qualityFlags: [],
    ...overrides,
  };
}

function originalHit(overrides: Partial<DataCenterSearchHit>): DataCenterSearchHit {
  return hit({
    openableKind: 'original_file',
    sourceAvailability: 'original_available',
    originalAvailable: true,
    ...overrides,
  });
}

test('file search groups duplicate hits by document and section', () => {
  const groups = buildFileSearchDisplayGroups({
    hits: [
      originalHit({ documentId: 'doc-1', sectionLabel: '正文', excerpt: '第一段', score: 6, originalPath: '/tmp/a.docx' }),
      originalHit({ documentId: 'doc-1', sectionLabel: '正文', excerpt: '第二段', score: 4, originalPath: '/tmp/a.docx' }),
    ],
    selectedHits: [],
  });

  assert.equal(groups.originalGroups.length, 1);
  assert.equal(groups.originalGroups[0]?.hits.length, 2);
});

test('file search keeps original files and drops system cards', () => {
  const groups = buildFileSearchDisplayGroups({
    hits: [
      originalHit({ title: '合同.docx', documentId: 'doc-1', sectionLabel: '正文', originalPath: '/tmp/合同.docx', score: 3 }),
      hit({ title: '合同系统卡片.md', documentId: 'doc-1', sectionLabel: '正文', openableKind: 'system_card', markdownPath: '/tmp/合同.md', score: 9 }),
    ],
    selectedHits: [],
  });

  assert.equal(groups.originalGroups.length, 1);
  assert.equal(groups.systemGroups.length, 0);
  assert.equal(groups.originalGroups[0]?.primaryHit.title, '合同.docx');
});

test('file search hides invalid and machine-readable-only sources', () => {
  const groups = buildFileSearchDisplayGroups({
    hits: [
      hit({ title: '坏资料.pdf', sourceAvailability: 'invalid_source', openableKind: 'original_file', originalPath: '/tmp/bad.pdf' }),
      hit({ title: '历史机读稿.md', sourceAvailability: 'machine_readable_only', machineReadableAvailable: true, markdownPath: '/tmp/a.md', score: 2 }),
    ],
    selectedHits: [],
  });

  assert.equal(groups.hiddenInvalidCount, 0);
  assert.equal(groups.originalGroups.length, 0);
  assert.equal(groups.systemGroups.length, 0);
});

test('file search support level never exposes raw percent semantics', () => {
  assert.equal(normalizeSearchSupportLevel(6.34), 'strong');
  assert.equal(normalizeSearchSupportLevel(2), 'reference');
  assert.equal(normalizeSearchSupportLevel(null), 'background');
});

test('file search primary hit prefers original file over higher scored system card', () => {
  const primary = pickPrimarySearchHit([
    hit({ title: '系统卡片.md', openableKind: 'system_card', markdownPath: '/tmp/card.md', score: 9 }),
    originalHit({ title: '原文件.docx', originalPath: '/tmp/raw.docx', score: 1 }),
  ]);

  assert.equal(primary.title, '原文件.docx');
  assert.equal(isOriginalFileSearchHit(primary), true);
});

test('file search brief answer ranks original files before cards', () => {
  const groups = buildFileSearchDisplayGroups({
    hits: [
      hit({ title: '系统卡片.md', openableKind: 'system_card', markdownPath: '/tmp/card.md', score: 9 }),
      originalHit({ title: '第一篇文章.docx', originalPath: '/tmp/first.docx', score: 5 }),
      originalHit({ title: '第二篇文章.docx', originalPath: '/tmp/second.docx', score: 3 }),
    ],
    selectedHits: [],
  });

  const brief = buildFileSearchBriefAnswer(groups);

  assert.equal(brief.title, '简要排序');
  assert.match(brief.lines.join('\n'), /第一篇文章\.docx/);
  assert.match(brief.lines.join('\n'), /第二篇文章\.docx/);
  assert.match(brief.note, /阅读顺序/);
});
