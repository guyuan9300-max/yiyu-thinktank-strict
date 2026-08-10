import assert from 'node:assert/strict';
import test from 'node:test';

import { formatTaskContextMaterialBoundary } from './taskContextPresentation.mts';

test('formatTaskContextMaterialBoundary preserves legacy text boundaries', () => {
  assert.equal(
    formatTaskContextMaterialBoundary(' 仅使用组织摘要 '),
    '仅使用组织摘要',
  );
});

test('formatTaskContextMaterialBoundary renders strict object boundaries as text', () => {
  const rendered = formatTaskContextMaterialBoundary({
    sourceFileContentIncluded: false,
    sourceFilePathsIncluded: false,
    storageLocatorsIncluded: false,
    unpublishedDocumentContentIncluded: false,
    localPrivateSource: false,
    localPrivateUploadedToOrganizationCloud: false,
    localSourcePathsIncludedInContext: false,
  });

  assert.match(rendered, /未包含源文件正文/);
  assert.match(rendered, /未使用本机私有源资料/);
  assert.match(rendered, /本机私有资料未上传组织云/);
  assert.equal(rendered.includes('[object Object]'), false);
});
