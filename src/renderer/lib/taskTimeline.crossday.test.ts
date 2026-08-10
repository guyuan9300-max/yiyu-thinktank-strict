/**
 * 跨天时间段排期推导 · 回归测试
 *
 * 对齐手机版 mobile/lib/__tests__/calendar-repository-core.test.mjs 的语义。
 *
 * 跑法: node --import tsx src/renderer/lib/taskTimeline.crossday.test.ts
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { buildTaskScheduleFromStartEnd } from './taskTimeline.js';

test('无开始日 → 清空全部排期', () => {
  const r = buildTaskScheduleFromStartEnd({ startDate: null, startTime: null, endDate: null, endTime: null });
  assert.deepEqual(r, {
    dueDate: null, deadlineAt: null, scheduledStartAt: null, scheduledEndAt: null, durationMinutes: null,
  });
});

test('全天（无开始时间）→ 落 deadlineAt，单日，忽略 endDate', () => {
  const r = buildTaskScheduleFromStartEnd({
    startDate: '2026-06-10', startTime: null, endDate: '2026-06-12', endTime: null,
  });
  assert.equal(r.deadlineAt, '2026-06-10');
  assert.equal(r.dueDate, '2026-06-10');
  assert.equal(r.scheduledStartAt, null);
  assert.equal(r.scheduledEndAt, null);
  assert.equal(r.durationMinutes, null);
});

test('有开始时间、无结束时间 → 只有开始时刻', () => {
  const r = buildTaskScheduleFromStartEnd({
    startDate: '2026-06-10', startTime: '09:00', endDate: null, endTime: null,
  });
  assert.equal(r.scheduledStartAt, '2026-06-10T09:00');
  assert.equal(r.scheduledEndAt, null);
  assert.equal(r.durationMinutes, null);
});

test('同日时间段 → duration = end - start', () => {
  const r = buildTaskScheduleFromStartEnd({
    startDate: '2026-06-10', startTime: '09:00', endDate: '2026-06-10', endTime: '11:30',
  });
  assert.equal(r.scheduledStartAt, '2026-06-10T09:00');
  assert.equal(r.scheduledEndAt, '2026-06-10T11:30');
  assert.equal(r.durationMinutes, 150);
});

test('跨天时间段 → duration 可 >1440', () => {
  // 6/10 22:00 → 6/11 02:00 = 4 小时
  const r = buildTaskScheduleFromStartEnd({
    startDate: '2026-06-10', startTime: '22:00', endDate: '2026-06-11', endTime: '02:00',
  });
  assert.equal(r.scheduledStartAt, '2026-06-10T22:00');
  assert.equal(r.scheduledEndAt, '2026-06-11T02:00');
  assert.equal(r.durationMinutes, 240);
});

test('跨多天 → duration 累计', () => {
  // 6/10 09:00 → 6/12 09:00 = 2880 分钟
  const r = buildTaskScheduleFromStartEnd({
    startDate: '2026-06-10', startTime: '09:00', endDate: '2026-06-12', endTime: '09:00',
  });
  assert.equal(r.durationMinutes, 2880);
});

test('结束缺省 endDate → 视为同开始日', () => {
  const r = buildTaskScheduleFromStartEnd({
    startDate: '2026-06-10', startTime: '09:00', endDate: null, endTime: '10:00',
  });
  assert.equal(r.scheduledEndAt, '2026-06-10T10:00');
  assert.equal(r.durationMinutes, 60);
});

test('结束 <= 开始 → 丢弃 end，退化为开始时刻（防脏数据）', () => {
  const r = buildTaskScheduleFromStartEnd({
    startDate: '2026-06-10', startTime: '09:00', endDate: '2026-06-10', endTime: '08:00',
  });
  assert.equal(r.scheduledStartAt, '2026-06-10T09:00');
  assert.equal(r.scheduledEndAt, null);
  assert.equal(r.durationMinutes, null);
});

test('跨年边界 → 正确计算', () => {
  const r = buildTaskScheduleFromStartEnd({
    startDate: '2026-12-31', startTime: '22:00', endDate: '2027-01-01', endTime: '02:00',
  });
  assert.equal(r.scheduledEndAt, '2027-01-01T02:00');
  assert.equal(r.durationMinutes, 240);
});
