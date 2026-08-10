import assert from 'node:assert/strict';
import test from 'node:test';

import {
  getTaskCalendarPlacement,
  getTaskDeadline,
  getTaskDisplayTime,
  getTaskScheduleRange,
  isTaskInCurrentWeek,
  isTaskOverdue,
  isTaskToday,
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

test('overdue checks deadline only, not past scheduled time', () => {
  const record = task({
    dueDate: '2026-04-20',
    scheduledStartAt: '2026-04-20T10:00',
    scheduledEndAt: '2026-04-20T11:00',
    deadlineAt: null,
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

test('task display time is hidden when task has no date', () => {
  const record = task({ dueDate: null, deadlineAt: null, scheduledStartAt: null });

  assert.equal(getTaskDisplayTime(record), null);
});
