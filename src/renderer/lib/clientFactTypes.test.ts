/**
 * v2.2 F1.5 验证 · clientFactTypes 工具函数
 *
 * 跑法: node --import tsx src/renderer/lib/clientFactTypes.test.ts
 * 或集成到现有 test runner (node:test)
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { isLiteBundle, type ClientFactBundle } from './clientFactTypes.js';

function buildFullBundle(): ClientFactBundle {
  return {
    client: {
      id: 'client_x',
      name: '测试客户',
      alias: 'test',
      domain: '',
      type: '',
      intro: '',
      stage: 'active',
      color: '#5B7BFE',
      created_at: '2026-05-01',
      updated_at: '2026-05-21',
    },
    event_lines: [
      {
        id: 'eline_1',
        name: 'L1',
        kind: 'custom',
        status: 'active',
        stage: '',
        summary: '',
        intent: '',
        current_blocker: '',
        recent_decision: '',
        next_step: '',
        evidence_count: 0,
        owner_id: null,
        owner_name: null,
        primary_client_id: 'client_x',
        primary_client_name: '测试客户',
        created_at: '2026-05-01',
        updated_at: '2026-05-01',
      },
    ],
    tasks: [],
    commitments: [],
    dna_documents: [],
    atomic_facts: [],
    key_decisions: [],
    snapshot_at: '2026-05-21T22:00:00Z',
    sources: { client: 'ClientRepository' },
    counts: {
      event_lines: 1,
      tasks: 0,
      commitments: 0,
      dna_documents: 0,
      atomic_facts: 0,
    },
  };
}

function buildLiteBundle(): ClientFactBundle {
  return {
    ...buildFullBundle(),
    event_lines: [],
    counts: {
      event_lines: 1,
      tasks: 14,
      commitments: 36,
      dna_documents: 4,
      atomic_facts: 197,
    },
  };
}

function buildEmptyBundle(): ClientFactBundle {
  return {
    ...buildFullBundle(),
    event_lines: [],
    counts: {
      event_lines: 0,
      tasks: 0,
      commitments: 0,
      dna_documents: 0,
      atomic_facts: 0,
    },
  };
}

test('isLiteBundle returns false for null/undefined', () => {
  assert.equal(isLiteBundle(null), false);
  assert.equal(isLiteBundle(undefined), false);
});

test('isLiteBundle returns false for full bundle (有 list 数据)', () => {
  const bundle = buildFullBundle();
  assert.equal(isLiteBundle(bundle), false);
});

test('isLiteBundle returns true for lite bundle (list 空但 counts > 0)', () => {
  const bundle = buildLiteBundle();
  assert.equal(isLiteBundle(bundle), true);
});

test('isLiteBundle returns false for completely empty bundle (list 空 + counts 全 0)', () => {
  const bundle = buildEmptyBundle();
  // 既不是 lite 也不是 full — 是真正空的客户
  assert.equal(isLiteBundle(bundle), false);
});

test('ClientFactBundle shape — 关键字段都存在', () => {
  const bundle = buildFullBundle();
  // type-level + runtime check
  assert.ok(bundle.client);
  assert.ok(Array.isArray(bundle.event_lines));
  assert.ok(Array.isArray(bundle.tasks));
  assert.ok(Array.isArray(bundle.commitments));
  assert.ok(Array.isArray(bundle.dna_documents));
  assert.ok(Array.isArray(bundle.atomic_facts));
  assert.ok(Array.isArray(bundle.key_decisions));
  assert.equal(typeof bundle.snapshot_at, 'string');
  assert.equal(typeof bundle.sources, 'object');
  assert.equal(typeof bundle.counts, 'object');
});
