import type { Task } from '../../shared/types';
import {
  getTaskCalendarPlacement,
  getTaskExecutionDate,
  getTaskScheduleRange,
  splitTaskDateTime as splitCanonicalTaskDateTime,
  taskOverlapsDateWindow,
} from '../../shared/taskTime';

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
  return splitCanonicalTaskDateTime(value);
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
  return normalizeTaskTimeInput(timePart);
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
    return baseLabel;
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
    const normalizedTime = date ? resolveTaskDueTimeForDisplay(date, time) : '';
    const normalizedDue = date
      ? (normalizedTime ? `${date}T${normalizedTime}` : `${date}T00:00:00`)
      : task.dueDate;
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
    const hasExplicitTime = Boolean(
      splitTaskDueDateTime(task.scheduledStartAt).time
      || splitTaskDueDateTime(task.scheduledEndAt).time
      || splitTaskDueDateTime(task.startDate).time
      || splitTaskDueDateTime(task.dueDate).time
    );
    return {
      hasExplicitTime,
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
    const startBaseDate = startDate || dueDate;
    return {
      hasExplicitTime: false,
      startDateTime: startOfDayValue(startBaseDate),
      endDateTime: addDays(startOfDayValue(dueDate), 1),
    };
  }

  const durationDays = Math.max(1, Math.ceil(Math.max(0, task.durationMinutes ?? 0) / DAY_MINUTES));
  const fallbackStartDateTime = startOfDayValue(normalizedStartDate);
  return {
    hasExplicitTime: false,
    startDateTime: fallbackStartDateTime,
    endDateTime: addDays(fallbackStartDateTime, 1),
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
  // 纯日期任务（包括跨日）属于日历的“未安排时间”区域，不能因为
  // getTaskScheduleRange 提供了整日覆盖范围就被伪装成 00:00-24:00。
  if (!resolveTaskDateTimeRange(task).hasExplicitTime) return null;
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

// ─── 任务日期与时间 ───────────────────────────────────────────
//
// 真相源是开始日+可选开始时间 / 结束日+可选结束时间。
// 纯日期任务允许跨日，日期均为包含式；不伪造 09:00。
// 有时间时必须起止完整，durationMinutes 由 (end - start) 派生，跨天可 >1440。
// 范式对齐手机版 mobile/lib/calendar-repository-core.ts:buildScheduleFromStartEnd。

export interface TaskScheduleStartEnd {
  /** 开始日 "YYYY-MM-DD"；为空表示清除排期 */
  startDate: string | null;
  /** 开始时间 "HH:mm"；为空表示仅日期 */
  startTime: string | null;
  /** 结束日 "YYYY-MM-DD"；设置开始日后必须存在 */
  endDate: string | null;
  /** 结束时间 "HH:mm"；与开始时间同时存在或同时为空 */
  endTime: string | null;
}

export interface TaskScheduleUpdates {
  /** API 兼容字段；物理表由 scheduledStartAt 的纯日期承载。 */
  startDate: string | null;
  dueDate: string | null;
  deadlineAt: string | null;
  scheduledStartAt: string | null;
  scheduledEndAt: string | null;
  /** 跨天可 >1440；null 表示无明确时段 */
  durationMinutes: number | null;
}

export type TaskScheduleValidation = { valid: true } | { valid: false; message: string };

function isValidScheduleDate(value: string) {
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return false;
  const parsed = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return parsed.getFullYear() === Number(match[1])
    && parsed.getMonth() === Number(match[2]) - 1
    && parsed.getDate() === Number(match[3]);
}

export function validateTaskScheduleStartEnd(input: TaskScheduleStartEnd): TaskScheduleValidation {
  const startDate = (input.startDate || '').trim();
  const startTime = (input.startTime || '').trim();
  const endDate = (input.endDate || '').trim();
  const endTime = (input.endTime || '').trim();
  if (!startDate) {
    if (endDate || startTime || endTime) return { valid: false, message: '请先设置开始日期' };
    return { valid: true };
  }
  if (!isValidScheduleDate(startDate) || (endDate && !isValidScheduleDate(endDate))) {
    return { valid: false, message: '请输入有效日期（YYYY/MM/DD）' };
  }
  if (!endDate) return { valid: false, message: '请设置结束日期' };
  if (endDate < startDate) return { valid: false, message: '结束日期不能早于开始日期' };
  const hasAnyTime = Boolean(startTime || endTime);
  if (!hasAnyTime) return { valid: true };
  if (!normalizeTaskTimeInput(startTime) || !normalizeTaskTimeInput(endTime)) {
    return { valid: false, message: '开始时间和结束时间必须同时填写，格式为 24 小时制 HH:mm' };
  }
  const start = parseScheduleLocalDateTime(combineScheduleDateTime(startDate, startTime));
  const end = parseScheduleLocalDateTime(combineScheduleDateTime(endDate, endTime));
  if (!start || !end || end <= start) return { valid: false, message: '结束时间必须晚于开始时间' };
  return { valid: true };
}

export interface TaskScheduleMoveUpdate {
  dueDate: string;
  deadlineAt: null;
  scheduledStartAt: string;
  scheduledEndAt: string;
  startDate: string;
  durationMinutes: number;
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

function formatScheduleLocalDateTime(value: Date): string {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}T${String(value.getHours()).padStart(2, '0')}:${String(value.getMinutes()).padStart(2, '0')}`;
}

/**
 * 将任务的开始时刻移动到新的日期/时间，同时保留原时长。
 * 行内编辑、拖拽和其他快速排期入口应复用同一组排期字段，避免只改 dueDate 造成日历投影错位。
 */
export function buildTaskScheduleMoveUpdate(
  task: Pick<Task, 'startDate' | 'dueDate' | 'durationMinutes' | 'deadlineAt' | 'scheduledStartAt' | 'scheduledEndAt'>,
  input: { startDate: string; startTime: string },
): TaskScheduleMoveUpdate {
  const startDate = input.startDate.trim();
  const startTime = normalizeTaskTimeInput(input.startTime);
  const nextScheduledStartAt = startDate && startTime
    ? combineScheduleDateTime(startDate, startTime)
    : '';
  const nextStart = nextScheduledStartAt ? parseScheduleLocalDateTime(nextScheduledStartAt) : null;
  if (!nextStart) throw new Error('请选择有效的任务日期和时间。');

  const currentRange = getTaskScheduleRange(task);
  const durationFromRange = currentRange
    ? Math.round((currentRange.end.getTime() - currentRange.start.getTime()) / 60_000)
    : DEFAULT_TIMED_DURATION_MINUTES;
  const durationMinutes = Math.max(MIN_DURATION_MINUTES, task.durationMinutes || durationFromRange);
  const scheduledEndAt = formatScheduleLocalDateTime(
    new Date(nextStart.getTime() + durationMinutes * 60_000),
  );

  return {
    dueDate: nextScheduledStartAt,
    deadlineAt: null,
    scheduledStartAt: nextScheduledStartAt,
    scheduledEndAt,
    startDate: nextScheduledStartAt,
    durationMinutes,
  };
}

/** 由"开始日/时间 + 结束日/时间"推导任务排期字段（支持跨天）。 */
export function buildTaskScheduleFromStartEnd(input: TaskScheduleStartEnd): TaskScheduleUpdates {
  const startDate = (input.startDate || '').trim() || null;
  const startTime = (input.startTime || '').trim() || null;
  const endDate = (input.endDate || '').trim() || null;
  const endTime = (input.endTime || '').trim() || null;

  // 无开始日 → 清空全部排期
  if (!startDate) {
    return { startDate: null, dueDate: null, deadlineAt: null, scheduledStartAt: null, scheduledEndAt: null, durationMinutes: null };
  }
  // 仅日期：scheduledStartAt 保存纯日期，dueDate 保存包含式结束日期。
  // 纯日期不做 UTC 换算，也不会被界面伪造成 09:00。
  if (!startTime) {
    const inclusiveEndDate = endDate || startDate;
    return {
      startDate,
      dueDate: inclusiveEndDate,
      deadlineAt: inclusiveEndDate,
      scheduledStartAt: startDate,
      scheduledEndAt: null,
      durationMinutes: null,
    };
  }
  const scheduledStartAt = combineScheduleDateTime(startDate, startTime);
  // 调用方会先做严格校验；此处仍保持失败安全，不制造半截时段。
  if (!endTime) {
    return { startDate, dueDate: null, deadlineAt: null, scheduledStartAt: null, scheduledEndAt: null, durationMinutes: null };
  }
  const scheduledEndAt = combineScheduleDateTime(endDate ?? startDate, endTime);
  const start = parseScheduleLocalDateTime(scheduledStartAt);
  const end = parseScheduleLocalDateTime(scheduledEndAt);
  const durationMinutes = start && end ? Math.round((end.getTime() - start.getTime()) / 60_000) : null;
  // 结束 <= 开始 视为无效，丢弃 end 防脏数据（picker 已校验，这里兜底）
  if (durationMinutes == null || durationMinutes <= 0) {
    return { startDate, dueDate: null, deadlineAt: null, scheduledStartAt: null, scheduledEndAt: null, durationMinutes: null };
  }
  return { startDate, dueDate: scheduledEndAt, deadlineAt: null, scheduledStartAt, scheduledEndAt, durationMinutes };
}
