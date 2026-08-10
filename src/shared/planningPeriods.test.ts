import assert from 'node:assert/strict';
import test from 'node:test';

import {
  formatPlanningPeriodLabel,
  isoWeekDateRange,
  isoWeekKeyForDate,
  planningCycleTypeForKey,
} from './planningPeriods';

test('ISO 周使用周一至周日并以直观日期展示', () => {
  assert.equal(isoWeekKeyForDate(new Date(2026, 7, 8, 12)), '2026-W32');
  const range = isoWeekDateRange('2026-W32');
  assert.equal(range?.start.getFullYear(), 2026);
  assert.equal(range?.start.getMonth(), 7);
  assert.equal(range?.start.getDate(), 3);
  assert.equal(range?.end.getDate(), 9);
  assert.equal(formatPlanningPeriodLabel('2026-W32'), '8月3日—8月9日');
});

test('计划周期类型由稳定内部键确定', () => {
  assert.equal(planningCycleTypeForKey('2026-08'), 'month');
  assert.equal(planningCycleTypeForKey('2026-Q3'), 'quarter');
  assert.equal(planningCycleTypeForKey('2026'), 'year');
  assert.equal(planningCycleTypeForKey('专项阶段'), 'custom');
});
