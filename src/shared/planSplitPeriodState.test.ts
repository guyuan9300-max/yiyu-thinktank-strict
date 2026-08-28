import assert from 'node:assert/strict';
import test from 'node:test';

import {
  resolvePlanSplitPeriodOnOpen,
  validatePlanSplitPeriod,
} from './planSplitPeriodState';

test('AI 拆解弹窗重新打开时保留用户已选的季度', () => {
  assert.deepEqual(resolvePlanSplitPeriodOnOpen({
    cycleType: 'quarter',
    periodKey: '2026-Q4',
  }, new Date(2026, 7, 25, 12)), {
    cycleType: 'quarter',
    periodKey: '2026-Q4',
  });
});

test('AI 拆解弹窗仅在尚无周期值时补当前类型的默认周期', () => {
  assert.deepEqual(resolvePlanSplitPeriodOnOpen({
    cycleType: 'quarter',
    periodKey: '',
  }, new Date(2026, 7, 25, 12)), {
    cycleType: 'quarter',
    periodKey: '2026-Q3',
  });
});

test('周期类型与周期值不一致时阻止解析和保存', () => {
  assert.equal(validatePlanSplitPeriod({
    cycleType: 'quarter',
    periodKey: '2026-08',
  }), '计划周期与周期类型不一致，请重新选择');

  assert.equal(validatePlanSplitPeriod({
    cycleType: 'quarter',
    periodKey: '2026-Q4',
  }), '');
});
