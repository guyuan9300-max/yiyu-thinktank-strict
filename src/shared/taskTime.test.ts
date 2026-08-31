import assert from 'node:assert/strict';
import test from 'node:test';

import {
  formatTaskCardScheduleLabel,
  getTaskCalendarPlacement,
  getTaskDeadline,
  getTaskDisplayTime,
  getTaskReportPeriod,
  getTaskScheduleRange,
  isTaskInCurrentWeek,
  isTaskOverdue,
  isTaskToday,
  resolveTaskEditorDueTime,
  resolveTaskEditorDueTimeAfterDateChange,
  resolveTaskEditorTimeCommit,
  resolveTaskEditorTimeDisplay,
  splitTaskDateTime,
} from './taskTime.js';
import type { Task } from './types.js';

function task(overrides: Partial<Task>): Task {
  return {
    id: 'task-1',
    title: 'Task',
    desc: '',
    status: 'todo',
    priority: 'normal',
    listId: 'list-1',
    listName: 'List',
    listColor: '#5B7BFE',
    ddl: '待确认',
    ownerName: 'User',
    sourceType: 'manual',
    evidenceCount: 0,
    tags: [],
    attachments: [],
    collaborators: [],
    collaborationSummary: {},
    createdAt: '2026-04-01T00:00:00',
    updatedAt: '2026-04-01T00:00:00',
    ...overrides,
  };
}

test('date-only legacy dueDate becomes a deadline-only calendar item', () => {
  const record = task({ dueDate: '2026-04-20', deadlineAt: null, scheduledStartAt: null });
  const deadline = getTaskDeadline(record);

  assert.equal(deadline?.getFullYear(), 2026);
  assert.equal(deadline?.getMonth(), 3);
  assert.equal(deadline?.getDate(), 20);
  assert.equal(getTaskScheduleRange(record), null);
  assert.equal(getTaskCalendarPlacement(record).kind, 'deadlineOnly');
});

test('timed legacy dueDate becomes a scheduled calendar block', () => {
  const record = task({ dueDate: '2026-04-20T10:00', durationMinutes: 45 });
  const range = getTaskScheduleRange(record);

  assert.equal(range?.start.getFullYear(), 2026);
  assert.equal(range?.start.getMonth(), 3);
  assert.equal(range?.start.getDate(), 20);
  assert.equal(range?.start.getHours(), 10);
  assert.equal(range?.start.getMinutes(), 0);
  assert.equal(range ? (range.end.getTime() - range.start.getTime()) / 60_000 : 0, 45);
  assert.equal(getTaskCalendarPlacement(record).kind, 'scheduled');
});

test('completed tasks are never overdue even when deadline is in the past', () => {
  const record = task({ status: 'done', deadlineAt: '2026-04-20' });

  assert.equal(isTaskOverdue(record, new Date(2026, 3, 27)), false);
});

test('scheduled tasks become overdue from their actual scheduled end', () => {
  const record = task({
    dueDate: '2026-04-20',
    scheduledStartAt: '2026-04-20T10:00',
    scheduledEndAt: '2026-04-20T11:00',
    deadlineAt: null,
  });

  assert.equal(isTaskOverdue(record, new Date(2026, 3, 27)), true);
});

test('scheduled tasks ignore a stale earlier compatibility deadline after rescheduling', () => {
  const record = task({
    dueDate: '2026-04-27T10:00',
    scheduledStartAt: '2026-04-27T10:00',
    scheduledEndAt: '2026-04-27T11:00',
    deadlineAt: '2026-04-20',
  });

  assert.equal(isTaskOverdue(record, new Date(2026, 3, 27)), false);
});

test('today and current week use scheduled time before deadline', () => {
  const today = new Date(2026, 3, 27);
  const todayTask = task({ scheduledStartAt: '2026-04-27T10:00', deadlineAt: '2026-04-30' });
  const weekTask = task({ scheduledStartAt: '2026-04-30T10:00', deadlineAt: '2026-05-10' });

  assert.equal(isTaskToday(todayTask, today), true);
  assert.equal(isTaskInCurrentWeek(todayTask, today), false);
  assert.equal(isTaskInCurrentWeek(weekTask, today), true);
});

test('local drafts are marked as saving draft placement', () => {
  const record = task({ id: 'local-draft:123', scheduledStartAt: '2026-04-27T10:00' });

  assert.equal(getTaskCalendarPlacement(record).kind, 'savingDraft');
});

test('task display time shows date without time for date-only deadline', () => {
  const record = task({ deadlineAt: '2026-05-03', dueDate: '2026-05-03' });

  assert.deepEqual(getTaskDisplayTime(record), {
    kind: 'deadline',
    dateLabel: '2026-05-03',
    timeLabel: '',
  });
});

test('task display time includes explicit scheduled time range', () => {
  const record = task({
    scheduledStartAt: '2026-05-03T14:30',
    scheduledEndAt: '2026-05-03T16:00',
  });

  assert.deepEqual(getTaskDisplayTime(record), {
    kind: 'scheduled',
    dateLabel: '2026-05-03',
    timeLabel: '14:30-16:00',
  });
});

test('timezone-aware migrated meeting time is rendered in Asia/Shanghai wall time', () => {
  assert.deepEqual(splitTaskDateTime('2026-08-19T07:00:00Z'), {
    date: '2026-08-19',
    time: '15:00',
  });
  assert.deepEqual(splitTaskDateTime('2026-08-19T15:00:00+08:00'), {
    date: '2026-08-19',
    time: '15:00',
  });

  const record = task({
    sourceType: 'meeting_migration',
    scheduledStartAt: '2026-08-19T07:00:00Z',
    scheduledEndAt: '2026-08-19T08:00:00Z',
  });
  assert.deepEqual(getTaskDisplayTime(record), {
    kind: 'scheduled',
    dateLabel: '2026-08-19',
    timeLabel: '15:00-16:00',
  });
});

test('task display time is hidden when task has no date', () => {
  const record = task({ dueDate: null, deadlineAt: null, scheduledStartAt: null });

  assert.equal(getTaskDisplayTime(record), null);
});

test('date-only task card does not pretend that the user set 09:00', () => {
  const record = task({ dueDate: '2026-08-14', deadlineAt: '2026-08-14' });

  assert.equal(formatTaskCardScheduleLabel(record), '');
  assert.equal(formatTaskCardScheduleLabel(record, true), '2026/08/14');
});

test('task editor keeps 09:00 as presentation only until a time is explicit', () => {
  assert.equal(resolveTaskEditorDueTime('2026-08-14'), '');
  assert.equal(resolveTaskEditorDueTime('2026-08-14T09:00'), '09:00');
  assert.equal(resolveTaskEditorDueTimeAfterDateChange('2026-08-15', ''), '');
  assert.equal(resolveTaskEditorDueTimeAfterDateChange('2026-08-15', '14:30'), '14:30');
  assert.equal(resolveTaskEditorDueTimeAfterDateChange('', '14:30'), '');
  assert.equal(resolveTaskEditorTimeDisplay('', '09:00'), '09:00');
  assert.equal(resolveTaskEditorTimeDisplay('14:30', '09:00'), '14:30');
  assert.equal(resolveTaskEditorTimeCommit('09:00', false), null);
  assert.equal(resolveTaskEditorTimeCommit('09:00', true), '09:00');
});

test('card label shows the full range for a cross-day task', () => {
  const record = task({
    scheduledStartAt: '2026-08-14T15:00',
    scheduledEndAt: '2026-08-16T10:00',
  });

  assert.equal(formatTaskCardScheduleLabel(record), '2026/08/14 15:00 – 2026/08/16 10:00');
});

test('report period spans the earliest and latest business dates across tasks', () => {
  const period = getTaskReportPeriod([
    task({ scheduledStartAt: '2026-08-08T09:30', scheduledEndAt: '2026-08-08T11:00' }),
    task({ id: 'task-2', dueDate: '2026-08-31', deadlineAt: '2026-08-31' }),
    task({ id: 'task-3', dueDate: null, deadlineAt: null, ddl: '待确认' }),
  ]);

  assert.deepEqual(period, { start: '2026-08-08', end: '2026-08-31' });
});

test('report period keeps a date-only multi-day task end inclusive', () => {
  const period = getTaskReportPeriod([
    task({ startDate: '2026-08-05', dueDate: '2026-08-07', deadlineAt: null }),
  ]);

  assert.deepEqual(period, { start: '2026-08-05', end: '2026-08-07' });
});

test('report period converts timezone-aware task times to Asia Shanghai dates', () => {
  const period = getTaskReportPeriod([
    task({
      scheduledStartAt: '2026-08-31T16:30:00Z',
      scheduledEndAt: '2026-08-31T17:30:00Z',
    }),
  ]);

  assert.deepEqual(period, { start: '2026-09-01', end: '2026-09-01' });
});
