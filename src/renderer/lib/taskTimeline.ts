import type { Task } from '../../shared/types';
import {
  getTaskCalendarPlacement,
  getTaskExecutionDate,
  getTaskScheduleRange,
  taskOverlapsDateWindow,
} from '../../shared/taskTime';

const TASK_DEFAULT_DUE_TIME = '09:00';
const DAY_MINUTES = 24 * 60;
const MIN_DURATION_MINUTES = 15;
const DEFAULT_TIMED_DURATION_MINUTES = 60;

function startOfDayValue(date: Date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function addDays(baseDate: Date, days: number) {
  return new Date(baseDate.getFullYear(), baseDate.getMonth(), baseDate.getDate() + days);
}

export function splitTaskDueDateTime(value?: string | null) {
  if (!value) return { date: '', time: '' };
  const text = value.trim();
  if (!text) return { date: '', time: '' };
  const match = text.match(/^(\d{4}-\d{2}-\d{2})(?:[T\s](\d{2}):(\d{2}))?/);
  if (match) {
    return {
      date: match[1],
      time: match[2] && match[3] ? `${match[2]}:${match[3]}` : '',
    };
  }
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) return { date: '', time: '' };
  return {
    date: `${parsed.getFullYear()}-${String(parsed.getMonth() + 1).padStart(2, '0')}-${String(parsed.getDate()).padStart(2, '0')}`,
    time: `${String(parsed.getHours()).padStart(2, '0')}:${String(parsed.getMinutes()).padStart(2, '0')}`,
  };
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

export function parseTaskDateValue(value?: string | null) {
  if (!value) return null;
  const { date } = splitTaskDueDateTime(value);
  const match = date.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (match) {
    return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  return new Date(parsed.getFullYear(), parsed.getMonth(), parsed.getDate());
}

export function normalizeDdlToDate(label?: string | null) {
  const text = (label || '').trim();
  const now = new Date();
  if (!text || text === '待确认') return new Date(now.getFullYear(), now.getMonth(), now.getDate());
  if (text === '今天') return new Date(now.getFullYear(), now.getMonth(), now.getDate());
  if (text === '本周') return new Date(now.getFullYear(), now.getMonth(), now.getDate() + 3);
  const dayMap: Record<string, number> = { 周一: 1, 周二: 2, 周三: 3, 周四: 4, 周五: 5, 周六: 6, 周日: 0 };
  if (text in dayMap) {
    const delta = (dayMap[text] - now.getDay() + 7) % 7;
    return new Date(now.getFullYear(), now.getMonth(), now.getDate() + delta);
  }
  const match = text.match(/^(\d{2})-(\d{2})$/);
  if (match) {
    return new Date(now.getFullYear(), Number(match[1]) - 1, Number(match[2]));
  }
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) {
    return new Date(now.getFullYear(), now.getMonth(), now.getDate());
  }
  return new Date(parsed.getFullYear(), parsed.getMonth(), parsed.getDate());
}

export function normalizeDdlToDateTime(label?: string | null) {
  if (!label) return null;
  const text = label.trim();
  if (!text || text === '待确认') return null;

  const now = new Date();
  const applyTime = (date: Date, hours = 0, minutes = 0) =>
    new Date(date.getFullYear(), date.getMonth(), date.getDate(), hours, minutes);

  const todayMatch = text.match(/^今天(?:\s+(\d{1,2}):(\d{2}))?$/);
  if (todayMatch) {
    return applyTime(
      new Date(now.getFullYear(), now.getMonth(), now.getDate()),
      Number(todayMatch[1] || 0),
      Number(todayMatch[2] || 0),
    );
  }

  const weekMatch = text.match(/^本周(?:\s+(\d{1,2}):(\d{2}))?$/);
  if (weekMatch) {
    const base = normalizeDdlToDate('本周');
    return applyTime(base, Number(weekMatch[1] || 0), Number(weekMatch[2] || 0));
  }

  const weekdayMatch = text.match(/^(周[一二三四五六日])(?:\s+(\d{1,2}):(\d{2}))?$/);
  if (weekdayMatch) {
    const base = normalizeDdlToDate(weekdayMatch[1]);
    return applyTime(base, Number(weekdayMatch[2] || 0), Number(weekdayMatch[3] || 0));
  }

  const monthDayMatch = text.match(/^(\d{2})-(\d{2})(?:\s+(\d{1,2}):(\d{2}))?$/);
  if (monthDayMatch) {
    const base = normalizeDdlToDate(`${monthDayMatch[1]}-${monthDayMatch[2]}`);
    return applyTime(base, Number(monthDayMatch[3] || 0), Number(monthDayMatch[4] || 0));
  }

  const parsed = new Date(text);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function resolveTaskDueTimeForDisplay(datePart?: string | null, timePart?: string | null) {
  if (!(datePart || '').trim()) return '';
  const normalizedTime = normalizeTaskTimeInput(timePart);
  return normalizedTime || TASK_DEFAULT_DUE_TIME;
}

export function formatTaskDateTimeLabel(
  value?: string | null,
  options?: { fallbackTime?: string | null },
) {
  if (!value) return '待确认';
  const { date, time } = splitTaskDueDateTime(value);
  if (!date) return value;
  const parsedDate = parseTaskDateValue(date);
  if (!parsedDate) return value;
  const today = new Date();
  const isToday = parsedDate.getFullYear() === today.getFullYear()
    && parsedDate.getMonth() === today.getMonth()
    && parsedDate.getDate() === today.getDate();
  const baseLabel = isToday
    ? '今天'
    : `${String(parsedDate.getMonth() + 1).padStart(2, '0')}-${String(parsedDate.getDate()).padStart(2, '0')}`;
  const explicitTime = normalizeTaskTimeInput(time);
  if (explicitTime) return `${baseLabel} ${explicitTime}`;
  const fallbackTime = normalizeTaskTimeInput(options?.fallbackTime || '');
  return fallbackTime ? `${baseLabel} ${fallbackTime}` : baseLabel;
}

export function formatTaskDateWindowLabel(startValue?: string | null, dueValue?: string | null) {
  if (!dueValue) return '';
  const { date } = splitTaskDueDateTime(dueValue);
  if (!date) return formatTaskDateTimeLabel(dueValue, { fallbackTime: null });
  const normalizedStart = (startValue || '').trim();
  if (!normalizedStart || normalizedStart === date) {
    return formatTaskDateTimeLabel(dueValue, { fallbackTime: null });
  }
  const startDate = parseTaskDateValue(normalizedStart);
  if (!startDate) return formatTaskDateTimeLabel(dueValue, { fallbackTime: null });
  const startLabel = formatTaskDateTimeLabel(normalizedStart, { fallbackTime: null });
  return `${startLabel} → ${formatTaskDateTimeLabel(dueValue, { fallbackTime: null })}`;
}

export function formatTaskTimelineLabel(task: Pick<Task, 'startDate' | 'dueDate' | 'durationMinutes' | 'ddl'>) {
  if (!task.dueDate) return task.ddl || '待确认';
  if (task.startDate) {
    return formatTaskDateWindowLabel(task.startDate, task.dueDate);
  }
  const { date: dueDatePart, time: dueTimePart } = splitTaskDueDateTime(task.dueDate);
  if (!dueDatePart) {
    return formatTaskDateTimeLabel(task.dueDate, { fallbackTime: null });
  }
  const normalizedDueTime = resolveTaskDueTimeForDisplay(dueDatePart, dueTimePart);
  const baseLabel = formatTaskDateTimeLabel(dueDatePart, { fallbackTime: null });
  const startMinute = minuteOfDayFromTaskTime(normalizedDueTime);
  if (startMinute === null) {
    return `${baseLabel} ${normalizedDueTime}`.trim();
  }
  const durationMinutes = Math.max(MIN_DURATION_MINUTES, task.durationMinutes || 0);
  const endMinute = Math.min(startMinute + durationMinutes, DAY_MINUTES);
  return `${baseLabel} ${normalizedDueTime}-${formatTaskMinuteOfDay(endMinute)}`.trim();
}

export function resolveTaskTimelineDateTime(task: Pick<Task, 'startDate' | 'dueDate' | 'ddl' | 'createdAt' | 'deadlineAt' | 'scheduledStartAt' | 'scheduledEndAt' | 'durationMinutes' | 'status' | 'id'>) {
  const canonicalDate = getTaskExecutionDate(task as Task);
  if (canonicalDate) return canonicalDate;
  if (task.dueDate) {
    const { date, time } = splitTaskDueDateTime(task.dueDate);
    const normalizedDue = date ? `${date}T${resolveTaskDueTimeForDisplay(date, time)}` : task.dueDate;
    const parsedDue = new Date(normalizedDue);
    if (!Number.isNaN(parsedDue.getTime())) return parsedDue;
  }
  const createdAt = new Date(task.createdAt);
  return Number.isNaN(createdAt.getTime()) ? null : createdAt;
}

export function taskDateForCalendar(task: Pick<Task, 'id' | 'status' | 'startDate' | 'dueDate' | 'durationMinutes' | 'ddl' | 'deadlineAt' | 'scheduledStartAt' | 'scheduledEndAt' | 'completedAt'>) {
  const placement = getTaskCalendarPlacement(task as Task);
  if (placement.date) return placement.date;
  return null;
}

export type TaskDateTimeRange = {
  hasExplicitTime: boolean;
  startDateTime: Date;
  endDateTime: Date;
};

export function resolveTaskDateTimeRange(
  task: Pick<Task, 'id' | 'status' | 'startDate' | 'dueDate' | 'durationMinutes' | 'ddl' | 'createdAt' | 'deadlineAt' | 'scheduledStartAt' | 'scheduledEndAt' | 'completedAt'>,
): TaskDateTimeRange {
  const placement = getTaskCalendarPlacement(task as Task);
  if (placement.range) {
    return {
      hasExplicitTime: true,
      startDateTime: placement.range.start,
      endDateTime: placement.range.end,
    };
  }
  if (placement.date) {
    const dayStart = startOfDayValue(placement.date);
    return {
      hasExplicitTime: false,
      startDateTime: dayStart,
      endDateTime: addDays(dayStart, 1),
    };
  }
  const fallbackDate = startOfDayValue(parseTaskDateValue(task.createdAt) || new Date());
  const startParts = splitTaskDueDateTime(task.startDate);
  const dueParts = splitTaskDueDateTime(task.dueDate);
  const startDate = parseTaskDateValue(startParts.date || task.startDate) || null;
  const dueDate = parseTaskDateValue(dueParts.date || task.dueDate) || null;
  const startMinute = minuteOfDayFromTaskTime(startParts.time);
  const dueMinute = minuteOfDayFromTaskTime(resolveTaskDueTimeForDisplay(dueParts.date || task.dueDate, dueParts.time));
  const safeDuration = Math.max(MIN_DURATION_MINUTES, task.durationMinutes ?? DEFAULT_TIMED_DURATION_MINUTES);

  const dateTimeFromDateAndMinute = (date: Date, minuteOfDay: number) => {
    const safeMinute = Math.max(0, minuteOfDay);
    const dayOffset = Math.floor(safeMinute / DAY_MINUTES);
    const minuteInDay = safeMinute % DAY_MINUTES;
    return new Date(
      date.getFullYear(),
      date.getMonth(),
      date.getDate() + dayOffset,
      Math.floor(minuteInDay / 60),
      minuteInDay % 60,
    );
  };

  if (startDate && (startMinute !== null || dueMinute !== null)) {
    const startDateTime = dateTimeFromDateAndMinute(startDate, startMinute ?? 0);
    if (dueDate && dueMinute !== null) {
      const explicitEndDateTime = dateTimeFromDateAndMinute(dueDate, dueMinute);
      return {
        hasExplicitTime: true,
        startDateTime,
        endDateTime: explicitEndDateTime > startDateTime
          ? explicitEndDateTime
          : new Date(startDateTime.getTime() + safeDuration * 60_000),
      };
    }
    return {
      hasExplicitTime: true,
      startDateTime,
      endDateTime: new Date(startDateTime.getTime() + safeDuration * 60_000),
    };
  }

  if (dueDate && dueMinute !== null) {
    const startDateTime = dateTimeFromDateAndMinute(dueDate, dueMinute);
    return {
      hasExplicitTime: true,
      startDateTime,
      endDateTime: new Date(startDateTime.getTime() + safeDuration * 60_000),
    };
  }

  const normalizedStartDate = startDate || dueDate || fallbackDate;
  if (dueDate) {
    const defaultMinute = minuteOfDayFromTaskTime(TASK_DEFAULT_DUE_TIME) ?? 9 * 60;
    const startBaseDate = startDate || dueDate;
    const startDateTime = dateTimeFromDateAndMinute(startBaseDate, startMinute ?? defaultMinute);
    const endDateTime = dateTimeFromDateAndMinute(dueDate, dueMinute ?? defaultMinute);
    return {
      hasExplicitTime: true,
      startDateTime,
      endDateTime: endDateTime > startDateTime
        ? endDateTime
        : new Date(startDateTime.getTime() + safeDuration * 60_000),
    };
  }

  const durationDays = Math.max(1, Math.ceil(Math.max(0, task.durationMinutes ?? 0) / DAY_MINUTES));
  const defaultMinute = minuteOfDayFromTaskTime(TASK_DEFAULT_DUE_TIME) ?? 9 * 60;
  const fallbackStartDateTime = dateTimeFromDateAndMinute(normalizedStartDate, defaultMinute);
  return {
    hasExplicitTime: true,
    startDateTime: fallbackStartDateTime,
    endDateTime: new Date(fallbackStartDateTime.getTime() + Math.max(safeDuration, DEFAULT_TIMED_DURATION_MINUTES) * 60_000),
  };
}

export function taskOverlapsCalendarWindow(task: Task, startDate: Date, endExclusive: Date) {
  return taskOverlapsDateWindow(task, startDate, endExclusive);
}

export function taskCoversCalendarDate(task: Task, date: Date) {
  const dayStart = startOfDayValue(date);
  return taskOverlapsCalendarWindow(task, dayStart, addDays(dayStart, 1));
}

export function buildTaskDayTimedSegment(task: Task, dayDate: Date) {
  const range = getTaskScheduleRange(task);
  if (!range) return null;
  const dayStart = startOfDayValue(dayDate);
  const dayEnd = addDays(dayStart, 1);
  if (range.end <= dayStart || range.start >= dayEnd) return null;
  const segmentStart = range.start > dayStart ? range.start : dayStart;
  const segmentEnd = range.end < dayEnd ? range.end : dayEnd;
  const startMinute = segmentStart.getHours() * 60 + segmentStart.getMinutes();
  const endMinute = segmentEnd.getTime() === dayEnd.getTime()
    ? DAY_MINUTES
    : segmentEnd.getHours() * 60 + segmentEnd.getMinutes();
  if (endMinute <= startMinute) return null;
  return {
    startMinute,
    endMinute,
    durationMinutes: endMinute - startMinute,
    timeLabel: `${formatTaskMinuteOfDay(startMinute)}-${formatTaskMinuteOfDay(endMinute)}`,
  };
}

export function assignTimedTaskLanes<T extends { startMinute: number; endMinute: number }>(
  items: T[],
): Array<T & { lane: number; laneCount: number; clusterId: number }> {
  const sorted = [...items].sort((left, right) => {
    if (left.startMinute !== right.startMinute) return left.startMinute - right.startMinute;
    if (left.endMinute !== right.endMinute) return right.endMinute - left.endMinute;
    return 0;
  });
  const result = sorted.map((item) => ({ ...item, lane: 0, laneCount: 1, clusterId: 0 }));
  let active: Array<{ lane: number; endMinute: number; index: number }> = [];
  let groupIndices: number[] = [];
  let groupLaneCount = 1;
  let clusterId = 0;

  const flushGroup = () => {
    groupIndices.forEach((index) => {
      result[index].laneCount = groupLaneCount;
      result[index].clusterId = clusterId;
    });
    groupIndices = [];
    groupLaneCount = 1;
    clusterId += 1;
  };

  result.forEach((item, index) => {
    active = active.filter((entry) => entry.endMinute > item.startMinute);
    if (active.length === 0 && groupIndices.length > 0) {
      flushGroup();
    }
    const occupied = new Set(active.map((entry) => entry.lane));
    let nextLane = 0;
    while (occupied.has(nextLane)) nextLane += 1;
    item.lane = nextLane;
    active.push({ lane: nextLane, endMinute: item.endMinute, index });
    groupIndices.push(index);
    groupLaneCount = Math.max(groupLaneCount, active.length);
  });

  if (groupIndices.length > 0) flushGroup();
  return result;
}

// ─── 跨天起止时间段（任务编辑器"时间段"tab 用） ───────────────
//
// 真相源是 开始日+开始时间 / 结束日+结束时间；scheduledEndAt 可落在不同日期。
// durationMinutes 由 (end - start) 派生，跨天时可 >1440。
// 全天（无开始时间）v1 仅单日，落 deadlineAt（忽略 endDate）。
// 范式对齐手机版 mobile/lib/calendar-repository-core.ts:buildScheduleFromStartEnd。

export interface TaskScheduleStartEnd {
  /** 开始日 "YYYY-MM-DD"；为空表示清除排期 */
  startDate: string | null;
  /** 开始时间 "HH:mm"；为空表示全天 */
  startTime: string | null;
  /** 结束日 "YYYY-MM-DD"；缺省同开始日 */
  endDate: string | null;
  /** 结束时间 "HH:mm"；为空表示不设结束 */
  endTime: string | null;
}

export interface TaskScheduleUpdates {
  dueDate: string | null;
  deadlineAt: string | null;
  scheduledStartAt: string | null;
  scheduledEndAt: string | null;
  /** 跨天可 >1440；null 表示无明确时段 */
  durationMinutes: number | null;
}

function combineScheduleDateTime(date: string, time: string): string {
  return `${date}T${time}`;
}

function parseScheduleLocalDateTime(value: string): Date | null {
  const m = value.match(/^(\d{4})-(\d{2})-(\d{2})[T\s](\d{1,2}):(\d{2})/);
  if (!m) return null;
  const parsed = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4]), Number(m[5]));
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/** 由"开始日/时间 + 结束日/时间"推导任务排期字段（支持跨天）。 */
export function buildTaskScheduleFromStartEnd(input: TaskScheduleStartEnd): TaskScheduleUpdates {
  const startDate = (input.startDate || '').trim() || null;
  const startTime = (input.startTime || '').trim() || null;
  const endDate = (input.endDate || '').trim() || null;
  const endTime = (input.endTime || '').trim() || null;

  // 无开始日 → 清空全部排期
  if (!startDate) {
    return { dueDate: null, deadlineAt: null, scheduledStartAt: null, scheduledEndAt: null, durationMinutes: null };
  }
  // 全天（无开始时间）：v1 仅单日，落 deadlineAt，忽略 endDate
  if (!startTime) {
    return { dueDate: startDate, deadlineAt: startDate, scheduledStartAt: null, scheduledEndAt: null, durationMinutes: null };
  }
  const scheduledStartAt = combineScheduleDateTime(startDate, startTime);
  // 无结束时间 → 只有开始时刻，不设 end
  if (!endTime) {
    return { dueDate: scheduledStartAt, deadlineAt: null, scheduledStartAt, scheduledEndAt: null, durationMinutes: null };
  }
  const scheduledEndAt = combineScheduleDateTime(endDate ?? startDate, endTime);
  const start = parseScheduleLocalDateTime(scheduledStartAt);
  const end = parseScheduleLocalDateTime(scheduledEndAt);
  const durationMinutes = start && end ? Math.round((end.getTime() - start.getTime()) / 60_000) : null;
  // 结束 <= 开始 视为无效，丢弃 end 防脏数据（picker 已校验，这里兜底）
  if (durationMinutes == null || durationMinutes <= 0) {
    return { dueDate: scheduledStartAt, deadlineAt: null, scheduledStartAt, scheduledEndAt: null, durationMinutes: null };
  }
  return { dueDate: scheduledStartAt, deadlineAt: null, scheduledStartAt, scheduledEndAt, durationMinutes };
}
