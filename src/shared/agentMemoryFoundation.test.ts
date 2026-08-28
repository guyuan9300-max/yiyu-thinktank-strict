import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { createAgentMemoryService } from './agentMemoryService.js';
import {
  inspectStrict88AgentMemorySchema,
  STRICT_88_AGENT_MEMORY_REQUIRED_FIELDS,
  STRICT_88_AGENT_MEMORY_TABLE_BINDINGS,
} from './agentMemoryStrict88Adapter.js';
import { chunkAgentWikiText } from './agentMemoryWikiChunker.js';

test('schema readiness covers every table bound by the Agent Memory adapter', () => {
  const boundTables = new Set(Object.values(STRICT_88_AGENT_MEMORY_TABLE_BINDINGS).flat());
  const checkedTables = new Set(Object.keys(STRICT_88_AGENT_MEMORY_REQUIRED_FIELDS));

  assert.deepEqual([...checkedTables].sort(), [...boundTables].sort());
  const inventory = Object.entries(STRICT_88_AGENT_MEMORY_REQUIRED_FIELDS).map(
    ([name, fields]) => ({ name, fields }),
  );
  assert.equal(inspectStrict88AgentMemorySchema(inventory).ready, true);
  assert.equal(inspectStrict88AgentMemorySchema(inventory.slice(1)).ready, false);
});

test('both current manifests expose the reviewed Agent Memory foundation', () => {
  for (const side of ['local', 'cloud']) {
    const manifest = JSON.parse(
      readFileSync(new URL(`../../contracts/strict-${side}-schema-manifest.v1.json`, import.meta.url), 'utf8'),
    ) as {
      contractVersion: string;
      allowedTables: Array<{ name: string; fields: Array<{ name: string }> }>;
    };
    const inventory = manifest.allowedTables.map((table) => ({
      name: table.name,
      fields: table.fields.map((field) => field.name),
    }));
    assert.equal(manifest.contractVersion, '10');
    assert.equal(manifest.allowedTables.length, 88);
    assert.deepEqual(inspectStrict88AgentMemorySchema(inventory), {
      ready: true,
      missingTables: [],
      missingFields: [],
    });
  }
});

test('wiki chunks never exceed the configured hard character limit', () => {
  const text = `${'甲'.repeat(1_000)}\n\n${'乙'.repeat(1_000)}`;
  const chunks = chunkAgentWikiText(text, { targetChars: 1_000, overlapChars: 400 });

  assert.ok(chunks.length > 1);
  assert.ok(chunks.every((chunk) => chunk.length <= 1_000));
});

test('local originals and published organization knowledge use separate wiki ports', async () => {
  const calls: string[] = [];
  const makeWikiPort = (label: string) => ({
    replaceSourceProjection: async () => {
      calls.push(label);
      return { knowledgeDocumentId: `document-${label}`, documentVersion: 1 };
    },
    search: async () => [],
    invalidateSource: async () => undefined,
  });
  const unused = async () => {
    throw new Error('unexpected data-plane call');
  };
  const dataPlane = {
    facts: { search: unused, write: unused, revoke: unused },
    skills: { get: unused, listEnabled: unused, publish: unused, setEnabled: unused },
    localWiki: makeWikiPort('local'),
    organizationWiki: makeWikiPort('organization'),
    cache: { get: unused, put: unused, invalidate: unused },
    audit: { record: async () => undefined },
    resolveBuiltinAgent: async () => ({
      botId: 'bot-project-workspace',
      agentKind: 'project_workspace' as const,
      handle: 'project-workspace',
      enabled: true,
      version: 1,
    }),
    resolveOrganizationModel: unused,
    invokeOrganizationModel: unused,
  };
  const service = createAgentMemoryService(
    dataPlane,
    { ready: true, missingTables: [], missingFields: [] },
  );
  const context = {
    scope: {
      scopeKind: 'client' as const,
      sandboxId: 'sandbox-test',
      cloudInstanceId: 'cloud-test',
      organizationId: 'organization-test',
      principalId: 'principal-test',
      clientId: 'client-test',
    },
    agentKind: 'project_workspace' as const,
  };

  await service.buildWiki(context, {
    contentBoundary: 'local_original',
    sourceAssetId: 'local-source',
    documentVersionId: 'local-version',
    title: '本地原件',
    content: '只允许本机处理',
    contentHash: 'local-hash',
  }, { operationKey: 'local-build', expectedDocumentVersion: null });
  await service.buildWiki(context, {
    contentBoundary: 'organization_published',
    sourceAssetId: 'published-source',
    documentVersionId: 'published-version',
    title: '已发布组织知识',
    content: '允许进入组织知识投影',
    contentHash: 'published-hash',
  }, { operationKey: 'organization-build', expectedDocumentVersion: null });

  assert.deepEqual(calls, ['local', 'organization']);
});
