/**
 * 跨天时间段排期推导 · 回归测试
 *
 * 对齐手机版 mobile/lib/__tests__/calendar-repository-core.test.mjs 的语义。
 *
 * 跑法: node --import tsx src/renderer/lib/taskTimeline.crossday.test.ts
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import { buildTaskScheduleFromStartEnd, validateTaskScheduleStartEnd } from './taskTimeline.js';

test('无开始日 → 清空全部排期', () => {
  const r = buildTaskScheduleFromStartEnd({ startDate: null, startTime: null, endDate: null, endTime: null });
  assert.deepEqual(r, {
    startDate: null, dueDate: null, deadlineAt: null, scheduledStartAt: null, scheduledEndAt: null, durationMinutes: null,
  });
});

test('仅日期任务保留完整起止日期，不伪造时间', () => {
  const r = buildTaskScheduleFromStartEnd({
    startDate: '2026-06-10', startTime: null, endDate: '2026-06-12', endTime: null,
  });
  assert.equal(r.startDate, '2026-06-10');
  assert.equal(r.deadlineAt, '2026-06-12');
  assert.equal(r.dueDate, '2026-06-12');
  assert.equal(r.scheduledStartAt, '2026-06-10');
  assert.equal(r.scheduledEndAt, null);
  assert.equal(r.durationMinutes, null);
});

test('有开始时间但没有完整结束时间 → 严格校验阻止保存', () => {
  const validation = validateTaskScheduleStartEnd({
    startDate: '2026-06-10', startTime: '09:00', endDate: null, endTime: null,
  });
  assert.equal(validation.valid, false);
});

test('同日时间段 → duration = end - start', () => {
  const r = buildTaskScheduleFromStartEnd({
    startDate: '2026-06-10', startTime: '09:00', endDate: '2026-06-10', endTime: '11:30',
  });
  assert.equal(r.scheduledStartAt, '2026-06-10T09:00');
  assert.equal(r.scheduledEndAt, '2026-06-10T11:30');
  assert.equal(r.durationMinutes, 150);
});

test('正午 12 点保持 24 小时制，不得变成 00 点', () => {
  const r = buildTaskScheduleFromStartEnd({
    startDate: '2026-08-20', startTime: '12:00', endDate: '2026-08-20', endTime: '13:00',
  });
  assert.equal(r.scheduledStartAt, '2026-08-20T12:00');
  assert.equal(r.scheduledEndAt, '2026-08-20T13:00');
  assert.equal(r.durationMinutes, 60);
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

test('结束缺省 endDate → 严格校验阻止保存', () => {
  const validation = validateTaskScheduleStartEnd({
    startDate: '2026-06-10', startTime: '09:00', endDate: null, endTime: '10:00',
  });
  assert.equal(validation.valid, false);
});

test('结束 <= 开始 → 严格校验阻止保存', () => {
  const validation = validateTaskScheduleStartEnd({
    startDate: '2026-06-10', startTime: '09:00', endDate: '2026-06-10', endTime: '08:00',
  });
  assert.equal(validation.valid, false);
});

test('不设置日期和时间 → 合法无排期', () => {
  assert.deepEqual(
    validateTaskScheduleStartEnd({ startDate: null, startTime: null, endDate: null, endTime: null }),
    { valid: true },
  );
});

test('单日仅日期与跨日仅日期均合法', () => {
  assert.deepEqual(
    validateTaskScheduleStartEnd({ startDate: '2026-06-10', startTime: null, endDate: '2026-06-10', endTime: null }),
    { valid: true },
  );
  assert.deepEqual(
    validateTaskScheduleStartEnd({ startDate: '2026-06-10', startTime: null, endDate: '2026-06-12', endTime: null }),
    { valid: true },
  );
});

test('有结束信息却无开始日期 → 严格校验阻止保存', () => {
  const validation = validateTaskScheduleStartEnd({
    startDate: null, startTime: null, endDate: '2026-06-10', endTime: null,
  });
  assert.equal(validation.valid, false);
});

test('跨年边界 → 正确计算', () => {
  const r = buildTaskScheduleFromStartEnd({
    startDate: '2026-12-31', startTime: '22:00', endDate: '2027-01-01', endTime: '02:00',
  });
  assert.equal(r.scheduledEndAt, '2027-01-01T02:00');
  assert.equal(r.durationMinutes, 240);
});
