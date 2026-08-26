import type { Task } from './types.js';

export type TaskCalendarPlacementKind = 'scheduled' | 'deadlineOnly' | 'none' | 'savingDraft';

export type TaskScheduleRange = {
  start: Date;
  end: Date;
  hasExplicitEnd: boolean;
};

export type TaskCalendarPlacement = {
  kind: TaskCalendarPlacementKind;
  date: Date | null;
  range: TaskScheduleRange | null;
};

export type TaskDisplayTime = {
  kind: 'scheduled' | 'deadline';
  dateLabel: string;
  timeLabel: string;
};

const DAY_MINUTES = 24 * 60;
const DEFAULT_DURATION_MINUTES = 60;
const MIN_DURATION_MINUTES = 15;
const LOCAL_DRAFT_PREFIX = 'local-draft:';

type TaskTimeInput = {
  id?: string | null;
  status?: Task['status'] | null;
  startDate?: string | null;
  dueDate?: string | null;
  durationMinutes?: number | null;
  deadlineAt?: string | null;
  scheduledStartAt?: string | null;
  scheduledEndAt?: string | null;
  completedAt?: string | null;
  updatedAt?: string | null;
};

export function isLocalDraftTaskId(taskId?: string | null) {
  return Boolean(taskId && taskId.startsWith(LOCAL_DRAFT_PREFIX));
}

export function splitTaskDateTime(value?: string | null) {
  if (!value) return { date: '', time: '' };
  const text = value.trim();
  if (!text) return { date: '', time: '' };
  // 组织云旧会议以及部分外部日历会返回带时区的 ISO 时间。
  // 这类值必须先换算为产品统一的 Asia/Shanghai 墙上时间，再交给
  // 任务/月历/周历使用；直接截取字符串会把 UTC 07:00 错画成 07:00，
  // 而不是正确的 15:00。
  if (/(?:Z|[+-]\d{2}:?\d{2})$/i.test(text)) {
    const zoned = new Date(text);
    if (!Number.isNaN(zoned.getTime())) {
      const parts = new Intl.DateTimeFormat('en-CA', {
        timeZone: 'Asia/Shanghai',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hourCycle: 'h23',
      }).formatToParts(zoned);
      const part = (type: string) => parts.find((item) => item.type === type)?.value || '';
      return {
        date: `${part('year')}-${part('month')}-${part('day')}`,
        time: `${part('hour')}:${part('minute')}`,
      };
    }
  }
  const match = text.match(/^(\d{4}-\d{2}-\d{2})(?:[T\s](\d{1,2}):(\d{2}))?/);
  if (match) {
    return {
      date: match[1],
      time: match[2] && match[3] ? `${String(Number(match[2])).padStart(2, '0')}:${match[3]}` : '',
    };
  }
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) return { date: '', time: '' };
  return {
    date: formatDateInputValue(parsed),
    time: `${String(parsed.getHours()).padStart(2, '0')}:${String(parsed.getMinutes()).padStart(2, '0')}`,
  };
}

export function hasExplicitTaskTime(value?: string | null) {
  return Boolean(splitTaskDateTime(value).time);
}

export function normalizeTaskTimeInput(timePart?: string | null) {
  const normalized = (timePart || '').trim();
  if (!normalized) return '';
  const match = normalized.match(/^(\d{1,2}):(\d{2})$/);
  if (!match) return '';
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (Number.isNaN(hours) || Number.isNaN(minutes) || hours < 0 || hours > 23 || minutes < 0 || minutes > 59) {
    return '';
  }
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
}

export function minuteOfDayFromTaskTime(timePart?: string | null) {
  const normalized = normalizeTaskTimeInput(timePart);
  if (!normalized) return null;
  const [hoursText, minutesText] = normalized.split(':');
  return Number(hoursText) * 60 + Number(minutesText);
}

export function formatTaskMinuteOfDay(minuteOfDay: number) {
  const safeMinute = Math.max(0, Math.min(DAY_MINUTES, minuteOfDay));
  const hours = Math.floor(safeMinute / 60);
  const minutes = safeMinute % 60;
  return `${String(Math.min(hours, 24)).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
}

function formatTaskClockTime(date: Date) {
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
}

function formatTaskNumericDateTime(date: Date) {
  return `${date.getFullYear()}/${String(date.getMonth() + 1).padStart(2, '0')}/${String(date.getDate()).padStart(2, '0')} ${formatTaskClockTime(date)}`;
}

function formatTaskClockRange(start: Date, end: Date) {
  const startLabel = formatTaskClockTime(start);
  const endLabel = formatTaskClockTime(end);
  return startLabel === endLabel ? startLabel : `${startLabel}-${endLabel}`;
}

export function formatDateInputValue(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

export function startOfTaskDay(date: Date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

export function addTaskDays(baseDate: Date, days: number) {
  return new Date(baseDate.getFullYear(), baseDate.getMonth(), baseDate.getDate() + days);
}

export function parseTaskDateValue(value?: string | null) {
  if (!value) return null;
  const { date } = splitTaskDateTime(value);
  const match = date.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (match) {
    return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  return startOfTaskDay(parsed);
}

export function parseTaskDateTimeValue(value?: string | null) {
  if (!value) return null;
  const { date, time } = splitTaskDateTime(value);
  if (!date) return null;
  const dateValue = parseTaskDateValue(date);
  if (!dateValue) return null;
  const minute = minuteOfDayFromTaskTime(time) ?? 0;
  return new Date(dateValue.getFullYear(), dateValue.getMonth(), dateValue.getDate(), Math.floor(minute / 60), minute % 60);
}

function addMinutes(base: Date, minutes: number) {
  return new Date(base.getTime() + minutes * 60_000);
}

function durationFromTask(task: Pick<TaskTimeInput, 'durationMinutes'>) {
  return Math.max(MIN_DURATION_MINUTES, task.durationMinutes ?? DEFAULT_DURATION_MINUTES);
}

function legacyDateOnlyDeadline(task: TaskTimeInput) {
  if (!task.dueDate) return null;
  if (task.scheduledStartAt || task.scheduledEndAt) return null;
  if (task.startDate || hasExplicitTaskTime(task.dueDate)) return null;
  return parseTaskDateValue(task.dueDate);
}

function legacyScheduleStart(task: TaskTimeInput) {
  if (task.startDate) {
    return parseTaskDateTimeValue(task.startDate) || parseTaskDateValue(task.startDate);
  }
  if (task.dueDate && hasExplicitTaskTime(task.dueDate)) {
    return parseTaskDateTimeValue(task.dueDate);
  }
  return null;
}

export function getTaskDeadline(task: TaskTimeInput) {
  return parseTaskDateTimeValue(task.deadlineAt) || parseTaskDateValue(task.deadlineAt) || legacyDateOnlyDeadline(task);
}

export function getTaskScheduleRange(task: TaskTimeInput): TaskScheduleRange | null {
  const startValue = task.scheduledStartAt || task.startDate;
  const legacyTimedStartValue = !startValue && task.dueDate && hasExplicitTaskTime(task.dueDate)
    ? task.dueDate
    : null;
  const effectiveStartValue = startValue || legacyTimedStartValue;
  const start = parseTaskDateTimeValue(effectiveStartValue) || legacyScheduleStart(task);
  if (!start) return null;
  const hasExplicitStartTime = hasExplicitTaskTime(effectiveStartValue);
  if (!hasExplicitStartTime) {
    const inclusiveEnd = parseTaskDateValue(task.dueDate)
      || parseTaskDateValue(task.deadlineAt)
      || startOfTaskDay(start);
    const safeInclusiveEnd = inclusiveEnd >= startOfTaskDay(start) ? inclusiveEnd : startOfTaskDay(start);
    return {
      start: startOfTaskDay(start),
      end: addTaskDays(startOfTaskDay(safeInclusiveEnd), 1),
      hasExplicitEnd: safeInclusiveEnd.getTime() !== startOfTaskDay(start).getTime(),
    };
  }
  const explicitEnd = parseTaskDateTimeValue(task.scheduledEndAt);
  if (explicitEnd && explicitEnd > start) {
    return { start, end: explicitEnd, hasExplicitEnd: true };
  }
  return { start, end: addMinutes(start, durationFromTask(task)), hasExplicitEnd: false };
}

export function getTaskExecutionDate(task: TaskTimeInput) {
  return getTaskScheduleRange(task)?.start || getTaskDeadline(task);
}

function parseTaskDateTimeOrDate(value?: string | null) {
  return parseTaskDateTimeValue(value) || parseTaskDateValue(value);
}

export function getTaskReviewDate(task: TaskTimeInput) {
  // 任务的"周归属"= 任务自身的执行日期(scheduledStart/due),不能用 completedAt。
  // 否则"上周的任务本周点完成"会被错误地归到本周。用户预期:任务是哪一周的就一直是哪一周的,
  // 完成动作不影响周归属。
  // 历史 fallback(done 时用 completedAt/updatedAt)只为处理"无日期任务完成后无处归属"的边缘场景,
  // 但那种任务本来就该停留在 done 组,而不是被强行塞进当周。
  return getTaskExecutionDate(task);
}

export function isTaskDone(task: Pick<TaskTimeInput, 'status'>) {
  return task.status === 'done';
}

export function isTaskOverdue(task: TaskTimeInput, today = new Date()) {
  if (isTaskDone(task)) return false;
  // 有正式排期的任务以实际结束日期判断逾期；只有没有排期时才退回截止日期。
  // 这样拖动任务后不会继续被旧 deadlineAt 标成逾期，也避免同一任务出现两套时间权威。
  const range = getTaskScheduleRange(task);
  const rangeHasTime = Boolean(hasExplicitTaskTime(task.scheduledStartAt) || hasExplicitTaskTime(task.startDate));
  const overdueAt = range
    ? (rangeHasTime ? range.end : new Date(range.end.getTime() - 1))
    : getTaskDeadline(task);
  if (!overdueAt) return false;
  return startOfTaskDay(overdueAt).getTime() < startOfTaskDay(today).getTime();
}

export function isSameTaskDay(left: Date, right: Date) {
  return startOfTaskDay(left).getTime() === startOfTaskDay(right).getTime();
}

export function isTaskToday(task: TaskTimeInput, today = new Date()) {
  const date = getTaskExecutionDate(task);
  return Boolean(date && isSameTaskDay(date, today));
}

export function startOfTaskWeek(baseDate: Date) {
  const dayIndex = (baseDate.getDay() + 6) % 7;
  return addTaskDays(startOfTaskDay(baseDate), -dayIndex);
}

export function isTaskInCurrentWeek(task: TaskTimeInput, today = new Date()) {
  const date = getTaskExecutionDate(task);
  if (!date || isSameTaskDay(date, today)) return false;
  const weekStart = startOfTaskWeek(today).getTime();
  const nextWeekStart = addTaskDays(startOfTaskWeek(today), 7).getTime();
  const dateTime = startOfTaskDay(date).getTime();
  return dateTime >= weekStart && dateTime < nextWeekStart;
}

export function isTaskInCurrentReviewWeek(task: TaskTimeInput, today = new Date()) {
  const date = getTaskReviewDate(task);
  if (!date) return false;
  const weekStart = startOfTaskWeek(today).getTime();
  const nextWeekStart = addTaskDays(startOfTaskWeek(today), 7).getTime();
  const dateTime = startOfTaskDay(date).getTime();
  return dateTime >= weekStart && dateTime < nextWeekStart;
}

export function getTaskCalendarPlacement(task: TaskTimeInput): TaskCalendarPlacement {
  if (isLocalDraftTaskId(task.id)) {
    const range = getTaskScheduleRange(task);
    const deadline = getTaskDeadline(task);
    return { kind: 'savingDraft', date: range?.start || deadline || null, range };
  }
  const range = getTaskScheduleRange(task);
  if (range) return { kind: 'scheduled', date: range.start, range };
  const deadline = getTaskDeadline(task);
  if (deadline) return { kind: 'deadlineOnly', date: deadline, range: null };
  return { kind: 'none', date: null, range: null };
}

export function getTaskDisplayTime(task: TaskTimeInput): TaskDisplayTime | null {
  const range = getTaskScheduleRange(task);
  if (range) {
    const hasExplicitScheduleTime = Boolean(
      hasExplicitTaskTime(task.scheduledStartAt)
        || hasExplicitTaskTime(task.scheduledEndAt)
        || hasExplicitTaskTime(task.startDate)
        || hasExplicitTaskTime(task.dueDate),
    );
    return {
      kind: 'scheduled',
      dateLabel: formatDateInputValue(range.start),
      timeLabel: hasExplicitScheduleTime ? formatTaskClockRange(range.start, range.end) : '',
    };
  }

  const deadline = getTaskDeadline(task);
  if (!deadline) return null;
  const hasExplicitDeadlineTime = Boolean(
    hasExplicitTaskTime(task.deadlineAt)
      || (!task.deadlineAt && hasExplicitTaskTime(task.dueDate)),
  );
  return {
    kind: 'deadline',
    dateLabel: formatDateInputValue(deadline),
    timeLabel: hasExplicitDeadlineTime ? formatTaskClockTime(deadline) : '',
  };
}

/**
 * 卡片统一时间口径：仅日期不伪造钟点；明确时段才显示 24 小时时间。
 */
export function formatTaskCardScheduleLabel(task: TaskTimeInput, includeSingleDate = false) {
  const range = getTaskScheduleRange(task);
  if (range) {
    const hasExplicitStart = Boolean(
      hasExplicitTaskTime(task.scheduledStartAt)
        || hasExplicitTaskTime(task.startDate)
        || (!task.scheduledStartAt && hasExplicitTaskTime(task.dueDate)),
    );
    const start = new Date(range.start);
    if (!hasExplicitStart) {
      const inclusiveEnd = new Date(range.end.getTime() - 1);
      const startLabel = formatDateInputValue(start).replaceAll('-', '/');
      const endLabel = formatDateInputValue(inclusiveEnd).replaceAll('-', '/');
      if (!includeSingleDate) return '';
      return startLabel === endLabel ? startLabel : `${startLabel} – ${endLabel}`;
    }
    const end = range.hasExplicitEnd
      ? new Date(range.end)
      : addMinutes(start, durationFromTask(task));
    if (startOfTaskDay(start).getTime() !== startOfTaskDay(new Date(end.getTime() - 1)).getTime()) {
      return `${formatTaskNumericDateTime(start)} – ${formatTaskNumericDateTime(end)}`;
    }
    return includeSingleDate ? formatTaskNumericDateTime(start) : formatTaskClockTime(start);
  }

  const deadline = getTaskDeadline(task);
  if (!deadline) return '无日期';
  const display = new Date(deadline);
  if (!hasExplicitTaskTime(task.deadlineAt) && !hasExplicitTaskTime(task.dueDate)) {
    return includeSingleDate ? formatDateInputValue(display).replaceAll('-', '/') : '';
  }
  return includeSingleDate ? formatTaskNumericDateTime(display) : formatTaskClockTime(display);
}

export function formatScheduleCardLabel(startValue: Date, endValue: Date, includeSingleDate = false) {
  const start = new Date(startValue);
  const end = endValue > start ? new Date(endValue) : addMinutes(start, DEFAULT_DURATION_MINUTES);
  if (startOfTaskDay(start).getTime() !== startOfTaskDay(new Date(end.getTime() - 1)).getTime()) {
    return `${formatTaskNumericDateTime(start)} – ${formatTaskNumericDateTime(end)}`;
  }
  return includeSingleDate ? formatTaskNumericDateTime(start) : formatTaskClockTime(start);
}

export function taskOverlapsDateWindow(task: TaskTimeInput, startDate: Date, endExclusive: Date) {
  const placement = getTaskCalendarPlacement(task);
  if (placement.kind === 'none' || !placement.date) return false;
  if (placement.range) {
    return placement.range.end > startDate && placement.range.start < endExclusive;
  }
  const dayStart = startOfTaskDay(placement.date);
  return dayStart >= startOfTaskDay(startDate) && dayStart < startOfTaskDay(endExclusive);
}
