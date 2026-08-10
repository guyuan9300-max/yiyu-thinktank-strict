import assert from 'node:assert/strict';
import test from 'node:test';

import {
  gc13BoundarySummary,
  gc13StateCopy,
  type GC13GrowthSnapshot,
} from './gc13GrowthContract';

const snapshot: GC13GrowthSnapshot = {
  schema: 'yiyu.gc13.growth-snapshot.v1',
  memberId: 'membership-gc13',
  evidence: [{
    evidenceId: 'evidence-1',
    summary: '形成了稳定复盘方法。',
    category: 'reflection',
    validationState: 'validated',
    sourceType: 'manual_reflection',
    sourceId: 'manual-1',
    sourceVersion: 1,
    contentHash: 'a'.repeat(64),
    contributionScore: 1,
    version: 1,
    createdAt: '2026-08-07T00:00:00Z',
  }],
  rules: [],
  readModel: {
    state: 'failed_retryable',
    models: [],
    metrics: [],
    badges: [],
    abilities: [],
    overview: null,
  },
  rebuild: {
    state: 'failed_retryable',
    retryable: true,
    message: '成长指标重算暂时失败；成长证据已保留，可以重试',
  },
  companion: {
    agentId: 'agent-growth',
    agentKind: 'growth_companion',
    mode: 'base_mode',
    state: 'base_mode',
    baseMode: '保留确定性成长记录',
    allowedPreferences: [],
    sourceLabels: ['成长证据', '成长规则'],
    boundaries: ['不读取项目协作记忆', '不把成长数据转换成 Skill'],
  },
  weeklyReviewAdapter: {
    contract: 'yiyu.gc13.weekly-review-candidate-port.v1',
    status: 'awaiting_b_thread',
    acceptedSourceType: 'weekly_review_candidate',
    readsWeeklyReviewTables: false,
    writesCandidateBeforeMemberConfirmation: false,
  },
  skillBoundary: { autoCreatesSkill: false, readsAutomationRules: false },
  projectMemoryBoundary: { consumed: false, copiedIntoGrowthEvidence: false },
};

test('GC-13 failure copy preserves evidence and offers retry', () => {
  assert.deepEqual(gc13StateCopy('failed_retryable'), {
    label: '更新失败，可重试',
    tone: 'error',
  });
  assert.equal(snapshot.evidence.length, 1);
  assert.match(snapshot.rebuild.message, /证据已保留/);
});

test('GC-13 presentation keeps Skill, project memory and weekly-review boundaries explicit', () => {
  const boundary = gc13BoundarySummary(snapshot);
  assert.equal(boundary.neverCreatesSkill, true);
  assert.equal(boundary.neverConsumesProjectMemory, true);
  assert.equal(boundary.waitsForWeeklyReviewAdapter, true);
  assert.equal(snapshot.weeklyReviewAdapter.readsWeeklyReviewTables, false);
});
