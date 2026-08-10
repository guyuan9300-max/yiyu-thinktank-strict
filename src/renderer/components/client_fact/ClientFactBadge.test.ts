/**
 * v2.2 F1.6 (B 门最小推进) · ClientFactBadge 逻辑测试
 *
 * 用 node:test (跟现有 src/shared/*.test.ts 一致), 不引入新测试基础设施。
 * 测的是 readCount + COUNTS_KEY_MAP 的纯逻辑, React render 由 TS 编译 + 手动验证保证。
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { COUNTS_KEY_MAP, readCount, type FactField } from './ClientFactBadge.js';

test('COUNTS_KEY_MAP 映射 5 个 camelCase 字段到 backend snake_case', () => {
  assert.equal(COUNTS_KEY_MAP.eventLines, 'event_lines');
  assert.equal(COUNTS_KEY_MAP.tasks, 'tasks');
  assert.equal(COUNTS_KEY_MAP.commitments, 'commitments');
  assert.equal(COUNTS_KEY_MAP.dnaDocs, 'dna_documents');
  assert.equal(COUNTS_KEY_MAP.atomicFacts, 'atomic_facts');
});

test('readCount null/undefined counts 返回 0', () => {
  assert.equal(readCount(null, 'eventLines'), 0);
  assert.equal(readCount(undefined, 'tasks'), 0);
});

test('readCount 字段存在返回正确数字', () => {
  const counts = { event_lines: 5, tasks: 12, commitments: 3, dna_documents: 7, atomic_facts: 240 };
  assert.equal(readCount(counts, 'eventLines'), 5);
  assert.equal(readCount(counts, 'tasks'), 12);
  assert.equal(readCount(counts, 'commitments'), 3);
  assert.equal(readCount(counts, 'dnaDocs'), 7);
  assert.equal(readCount(counts, 'atomicFacts'), 240);
});

test('readCount 字段缺失返回 0', () => {
  const counts = { event_lines: 5 };
  assert.equal(readCount(counts, 'tasks'), 0);
  assert.equal(readCount(counts, 'commitments'), 0);
});

test('readCount 字段值是 string/null 返回 0 (健壮性)', () => {
  const counts = { event_lines: 'invalid' as unknown, tasks: null as unknown };
  assert.equal(readCount(counts as Record<string, unknown>, 'eventLines'), 0);
  assert.equal(readCount(counts as Record<string, unknown>, 'tasks'), 0);
});

test('readCount 字段值是 NaN/Infinity 返回 0 (Number.isFinite 守门)', () => {
  const counts = { event_lines: NaN, tasks: Infinity };
  assert.equal(readCount(counts, 'eventLines'), 0);
  assert.equal(readCount(counts, 'tasks'), 0);
});

test('全部 FactField 类型枚举有效', () => {
  const fields: FactField[] = ['eventLines', 'tasks', 'commitments', 'dnaDocs', 'atomicFacts'];
  assert.equal(fields.length, 5);
  for (const field of fields) {
    assert.ok(COUNTS_KEY_MAP[field], `${field} should map to a backend key`);
  }
});
