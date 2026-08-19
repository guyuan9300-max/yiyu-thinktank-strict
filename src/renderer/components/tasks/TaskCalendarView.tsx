import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Plus,
  Search,
  MoveVertical,
} from 'lucide-react';

import type { Task } from '../../../shared/types';
import type { GC06Meeting } from '../gc06/gc06Contract';
import { formatMonthTitle } from '../../../shared/calendar';
import { getChinaCalendarMarkers, type ChinaCalendarMarker } from '../../../shared/china-calendar';
import {
  formatScheduleCardLabel,
  formatTaskCardScheduleLabel,
  getTaskCalendarPlacement,
  getTaskScheduleRange,
} from '../../../shared/taskTime';
import {
  assignTimedTaskLanes,
  buildTaskDayTimedSegment,
  formatTaskMinuteOfDay as formatMinuteOfDay,
  formatTaskTimelineLabel,
  normalizeTaskTimeInput,
  resolveTaskDateTimeRange,
  splitTaskDueDateTime,
  taskDateForCalendar as resolveTaskCalendarDate,
  taskOverlapsCalendarWindow,
} from '../../lib/taskTimeline';
import { useRuntimeUiSessionState } from '../../lib/runtimeUiSessionStore';

type CalendarDisplayMode = 'month' | 'week';

type TaskCalendarViewProps = {
  uiSessionScopeKey: string;
  tasks: Task[];
  meetings?: GC06Meeting[];
  clientColorById?: Record<string, string>;
  currentUserId?: string | null;
  calendarDisplayMode: CalendarDisplayMode;
  onSetCalendarDisplayMode: (mode: CalendarDisplayMode) => void;
  calendarDate: Date;
  selectedDate: Date;
  onSelectDate: (date: Date) => void;
  onShiftMonth: (delta: number) => void;
  onAlignCalendarDate: (date: Date) => void;
  onGoToToday: () => void;
  onOpenTaskEditor: (task?: Task, dueDate?: string, options?: { durationMinutes?: number }) => void;
  onOpenMeetingEditor?: (meeting: GC06Meeting) => void;
  onToggleMeetingStatus?: (meeting: GC06Meeting) => Promise<void>;
  onRescheduleMeeting?: (meeting: GC06Meeting, startDate: string) => Promise<void>;
  onCalendarNotice?: (kind: 'info' | 'error', message: string) => void;
  onToggleTaskStatus: (taskId: string, nextDone?: boolean) => Promise<void>;
  onRescheduleTask: (
    task: Task,
    dueDate: string,
    options?: { preserveCalendarViewport?: boolean },
  ) => Promise<void>;
  onUpdateTaskDuration: (task: Task, durationMinutes: number) => Promise<void>;
  onApproveTaskReview: (taskId: string) => Promise<void>;
  onReturnTaskReview: (taskId: string) => Promise<void>;
  isTaskOverdue: (task: Task, today?: Date) => boolean;
  showCollaborativeTasks: boolean;
  onToggleCollaborativeTasks: () => void;
};

const sourceTypeLabels: Record<string, string> = {
  manual: '手动',
  meeting: '会议',
  goal: '目标',
  topic_candidate: '资讯',
  knowledge_chunk: '知识片段',
  knowledge_document: '知识文档',
  chat: '聊天',
};

const DAY_TIMELINE_SLOT_MINUTES = 15;
const DAY_TIMELINE_SLOT_HEIGHT = 14;
const DAY_TIMELINE_DEFAULT_DURATION_MINUTES = 60;
const DAY_TIMELINE_DEFAULT_START_MINUTE = 8 * 60;
const DAY_MINUTES = 24 * 60;
const WEEK_MAX_VISIBLE_COLUMNS = 3; // 同时段最多可见 3 个任务卡,第 4 个起聚合 +N
const WEEK_OVERLAP_INDENT_RATIO = 0.18; // indent 风格:每个后续重叠任务向右偏移 18%
const WEEK_OVERLAP_INDENT_THRESHOLD = 2; // 重叠任务数 ≤ 2 时均分宽度,≥ 3 用 indent 风格
const DEFAULT_UNLINKED_TASK_COLOR = '#5B7BFE';
const LOCAL_DRAFT_NOTICE = '任务正在保存，稍后再调整时间。';

// 月视图跨天连续条单车道高度(px)
const MONTH_MULTIDAY_BAR_HEIGHT = 22;

// 任务是否跨天(整段 range 覆盖 >1 个自然日)。跨天任务在月视图渲染成连续条而非每格 chip。
function isMultiDayCalendarTask(task: Task): boolean {
  const range = resolveTaskDateTimeRange(task);
  const startDay = startOfDayValue(range.startDateTime).getTime();
  // 末尾减 1ms：end 落在次日 00:00 时，最后覆盖日仍是前一天（与 monthTasksByDateKey 的 cursor<end 一致）
  const lastDay = startOfDayValue(new Date(range.endDateTime.getTime() - 1)).getTime();
  return lastDay > startDay;
}

function formatMonthTaskStartTime(task: Task) {
  const range = resolveTaskDateTimeRange(task);
  return `${String(range.startDateTime.getHours()).padStart(2, '0')}:${String(range.startDateTime.getMinutes()).padStart(2, '0')}`;
}

function isLocalDraftTaskId(taskId?: string | null) {
  return Boolean(taskId && taskId.startsWith('local-draft:'));
}

type WeekCreateSelection = {
  dayKey: number;
  dayDate: Date;
  startMinute: number;
  endMinute: number;
};

type TimedWeekTask = {
  task: Task;
  dayIndex: number;
  dayDate: Date;
  startMinute: number;
  endMinute: number;
  durationMinutes: number;
  timeLabel: string;
  lane: number;
  laneCount: number;
  clusterId: number;
};

type TimedWeekMeeting = {
  meeting: GC06Meeting;
  dayIndex: number;
  dayDate: Date;
  startMinute: number;
  endMinute: number;
  lane: number;
  laneCount: number;
};

function meetingDateTimeRange(meeting: GC06Meeting) {
  const startDateTime = new Date(meeting.startsAt);
  const parsedEnd = new Date(meeting.endsAt);
  return {
    startDateTime,
    endDateTime: parsedEnd.getTime() > startDateTime.getTime()
      ? parsedEnd
      : new Date(startDateTime.getTime() + DAY_TIMELINE_DEFAULT_DURATION_MINUTES * 60_000),
  };
}

function formatMeetingScheduleLabel(meeting: GC06Meeting) {
  const range = meetingDateTimeRange(meeting);
  return formatScheduleCardLabel(range.startDateTime, range.endDateTime);
}

function isMultiDayCalendarMeeting(meeting: GC06Meeting) {
  const range = meetingDateTimeRange(meeting);
  return startOfDayValue(new Date(range.endDateTime.getTime() - 1)).getTime()
    > startOfDayValue(range.startDateTime).getTime();
}

function buildMeetingDayTimedSegment(meeting: GC06Meeting, dayDate: Date) {
  const range = meetingDateTimeRange(meeting);
  const dayStart = startOfDayValue(dayDate);
  const dayEnd = addDays(dayStart, 1);
  if (range.endDateTime <= dayStart || range.startDateTime >= dayEnd) return null;
  const segmentStart = range.startDateTime > dayStart ? range.startDateTime : dayStart;
  const segmentEnd = range.endDateTime < dayEnd ? range.endDateTime : dayEnd;
  const startMinute = segmentStart.getHours() * 60 + segmentStart.getMinutes();
  const endMinute = segmentEnd.getTime() === dayEnd.getTime()
    ? DAY_MINUTES
    : segmentEnd.getHours() * 60 + segmentEnd.getMinutes();
  return endMinute > startMinute ? { startMinute, endMinute } : null;
}

type WeekTaskDisplayItem =
  | {
      kind: 'task';
      taskItem: TimedWeekTask;
      column: number;
      columnCount: number;
    }
  | {
      kind: 'aggregate';
      key: string;
      hiddenItems: TimedWeekTask[];
      startMinute: number;
      endMinute: number;
      column: number;
      columnCount: number;
      summary: string;
    };

function formatDateInputValue(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function combineDateAndTime(date: Date, minuteOfDay: number) {
  return `${formatDateInputValue(date)}T${formatMinuteOfDay(minuteOfDay)}`;
}

function hasTaskExplicitTime(task: Pick<Task, 'startDate' | 'dueDate'>) {
  const placement = getTaskCalendarPlacement(task as Task);
  if (placement.kind === 'scheduled' || placement.kind === 'savingDraft') return true;
  const startParts = splitTaskDueDateTime(task.startDate);
  const dueParts = splitTaskDueDateTime(task.dueDate);
  return Boolean(normalizeTaskTimeInput(startParts.time) || normalizeTaskTimeInput(dueParts.time));
}

function minuteOfDayFromClientPosition(column: HTMLDivElement, clientY: number) {
  const rect = column.getBoundingClientRect();
  const clampedOffsetY = Math.max(0, Math.min(rect.height - 1, clientY - rect.top));
  const slotCount = (24 * 60) / DAY_TIMELINE_SLOT_MINUTES;
  const slotIndex = Math.max(
    0,
    Math.min(slotCount - 1, Math.floor(clampedOffsetY / DAY_TIMELINE_SLOT_HEIGHT)),
  );
  return slotIndex * DAY_TIMELINE_SLOT_MINUTES;
}

function buildSelectionRange(anchorMinute: number, currentMinute: number): { startMinute: number; endMinute: number } {
  if (currentMinute >= anchorMinute) {
    return {
      startMinute: anchorMinute,
      endMinute: Math.min(currentMinute + DAY_TIMELINE_SLOT_MINUTES, 24 * 60),
    };
  }
  return {
    startMinute: currentMinute,
    endMinute: Math.min(anchorMinute + DAY_TIMELINE_SLOT_MINUTES, 24 * 60),
  };
}

function isSameDay(left: Date, right: Date) {
  return left.getFullYear() === right.getFullYear()
    && left.getMonth() === right.getMonth()
    && left.getDate() === right.getDate();
}

function addDays(baseDate: Date, days: number) {
  return new Date(baseDate.getFullYear(), baseDate.getMonth(), baseDate.getDate() + days);
}

function startOfDayValue(date: Date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function startOfWeek(baseDate: Date) {
  const dayIndex = (baseDate.getDay() + 6) % 7;
  return addDays(baseDate, -dayIndex);
}

function isDateWithinRange(date: Date, startDate: Date, endDate: Date) {
  const time = new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
  return time >= startDate.getTime() && time <= endDate.getTime();
}

function formatWeekRangeTitle(startDate: Date, endDate: Date) {
  const sameMonth = startDate.getMonth() === endDate.getMonth() && startDate.getFullYear() === endDate.getFullYear();
  if (sameMonth) {
    return `${startDate.getFullYear()}年 ${startDate.getMonth() + 1}月 · ${startDate.getDate()}-${endDate.getDate()}日`;
  }
  return `${startDate.getFullYear()}年 ${startDate.getMonth() + 1}月${startDate.getDate()}日 - ${endDate.getMonth() + 1}月${endDate.getDate()}日`;
}

function sourceLabel(sourceType: string) {
  return sourceTypeLabels[sourceType] || sourceType || '任务';
}

function controlLevelLabel(task: Task) {
  const level = task.orgContext?.controlLevel;
  if (level === 'leader_control') return '负责人控制';
  if (level === 'department_control') return '部门控制';
  if (level === 'organization_control') return '机构控制';
  return '';
}

function taskOrgSummary(task: Task) {
  const parts: string[] = [];
  const control = controlLevelLabel(task);
  if (task.orgContext?.needsReview) parts.push('待复核');
  if (control) parts.push(control);
  if (task.orgContext?.isCrossDepartment) parts.push('跨部门');
  return parts.join(' · ');
}

function isTransportItineraryTask(task: Task) {
  const text = `${task.title || ''}\n${task.desc || ''}`.trim();
  if (!text) return false;
  return /(飞[\u4e00-\u9fff]{1,8}|飞去[\u4e00-\u9fff]{1,8}|飞往[\u4e00-\u9fff]{1,8}|航班|机票|火车去[\u4e00-\u9fff]{1,8}|高铁去[\u4e00-\u9fff]{1,8}|动车去[\u4e00-\u9fff]{1,8}|坐火车去[\u4e00-\u9fff]{1,8}|坐高铁去[\u4e00-\u9fff]{1,8}|乘火车去[\u4e00-\u9fff]{1,8}|乘高铁去[\u4e00-\u9fff]{1,8})/.test(text);
}

function calendarTaskAccentColor(task: Task, clientColorById?: Record<string, string>) {
  if (isTransportItineraryTask(task)) return '#16A34A';
  const normalizedClientId = (task.clientId || '').trim();
  if (!normalizedClientId) return DEFAULT_UNLINKED_TASK_COLOR;
  const clientColor = (clientColorById?.[normalizedClientId] || '').trim();
  if (clientColor) return clientColor;
  return DEFAULT_UNLINKED_TASK_COLOR;
}

function calendarChipStyle(task: Task, clientColorById?: Record<string, string>) {
  if (task.status === 'done') {
    return {
      color: '#94A3B8',
      backgroundColor: '#F8FAFC',
      borderColor: '#E2E8F0',
    };
  }
  const accentColor = calendarTaskAccentColor(task, clientColorById);
  return {
    color: accentColor,
    backgroundColor: `${accentColor}14`,
    borderColor: `${accentColor}22`,
  };
}

function calendarMarkerClassName(marker: ChinaCalendarMarker) {
  if (marker.kind === 'festival') return 'bg-rose-50 text-rose-600 border-rose-100';
  if (marker.kind === 'offday') return 'bg-orange-50 text-orange-700 border-orange-100';
  return 'bg-slate-100 text-slate-600 border-slate-200';
}

function sortTasksForCalendar(items: Task[]) {
  const statusRank: Record<Task['status'], number> = {
    inbox: 0,
    doing: 1,
    todo: 2,
    done: 3,
    rejected: 4,
  };
  const priorityRank: Record<Task['priority'], number> = {
    high: 0,
    normal: 1,
    low: 2,
  };
  return [...items].sort((left, right) => {
    const leftRange = resolveTaskDateTimeRange(left);
    const rightRange = resolveTaskDateTimeRange(right);
    if (leftRange.startDateTime.getTime() !== rightRange.startDateTime.getTime()) {
      return leftRange.startDateTime.getTime() - rightRange.startDateTime.getTime();
    }
    if (leftRange.hasExplicitTime !== rightRange.hasExplicitTime) {
      return leftRange.hasExplicitTime ? -1 : 1;
    }
    const leftDone = left.status === 'done';
    const rightDone = right.status === 'done';
    if (leftDone !== rightDone) return leftDone ? 1 : -1;

    const statusDelta = statusRank[left.status] - statusRank[right.status];
    if (statusDelta !== 0) return statusDelta;

    const priorityDelta = priorityRank[left.priority] - priorityRank[right.priority];
    if (priorityDelta !== 0) return priorityDelta;

    return new Date(right.updatedAt).getTime() - new Date(left.updatedAt).getTime();
  });
}

function buildWeekTaskDisplayItems(items: TimedWeekTask[]) {
  const clusters = new Map<number, TimedWeekTask[]>();
  items.forEach((item) => {
    const existing = clusters.get(item.clusterId) || [];
    existing.push(item);
    clusters.set(item.clusterId, existing);
  });

  const displayItems: WeekTaskDisplayItem[] = [];
  [...clusters.entries()]
    .sort((left, right) => {
      const leftStart = Math.min(...left[1].map((item) => item.startMinute));
      const rightStart = Math.min(...right[1].map((item) => item.startMinute));
      return leftStart - rightStart;
    })
    .forEach(([clusterId, clusterItems]) => {
      const sortedClusterItems = [...clusterItems].sort((left, right) => {
        if (left.startMinute !== right.startMinute) return left.startMinute - right.startMinute;
        if (left.endMinute !== right.endMinute) return left.endMinute - right.endMinute;
        return left.lane - right.lane;
      });
      // 同 cluster 最多显示 WEEK_MAX_VISIBLE_COLUMNS (= 3) 个任务卡,第 4+ 个聚合 +N。
      // ≤ 3 个:直接显示;≥ 4 个:前 3 个用 indent 风格 + 右上角 +N 小 chip。
      const visibleCount = Math.min(sortedClusterItems.length, WEEK_MAX_VISIBLE_COLUMNS);
      const hasOverflow = sortedClusterItems.length > WEEK_MAX_VISIBLE_COLUMNS;
      const effectiveColumnCount = visibleCount; // 渲染时所有显示卡片都按这个 columnCount 算 indent / 宽度

      sortedClusterItems
        .filter((taskItem) => taskItem.lane < visibleCount)
        .forEach((taskItem) => {
          displayItems.push({
            kind: 'task',
            taskItem,
            column: taskItem.lane,
            columnCount: effectiveColumnCount,
          });
        });

      if (hasOverflow) {
        const hiddenItems = sortedClusterItems.filter((taskItem) => taskItem.lane >= visibleCount);
        const startMinute = Math.min(...hiddenItems.map((item) => item.startMinute));
        const endMinute = Math.max(...hiddenItems.map((item) => item.endMinute));
        const hiddenTitles = hiddenItems.map((item) => item.task.title).filter(Boolean);
        displayItems.push({
          kind: 'aggregate',
          key: `aggregate-${clusterId}`,
          hiddenItems,
          startMinute,
          endMinute,
          // 聚合 chip 渲染时作为浮在最右上的 +N,column / columnCount 仅用来定位顶部
          column: visibleCount - 1,
          columnCount: effectiveColumnCount,
          summary: hiddenTitles.slice(0, 4).join('、'),
        });
      }
    });

  return displayItems;
}

export function TaskCalendarView({
  uiSessionScopeKey,
  tasks,
  meetings = [],
  clientColorById,
  currentUserId: _currentUserId,
  calendarDisplayMode,
  onSetCalendarDisplayMode,
  calendarDate,
  selectedDate,
  onSelectDate,
  onShiftMonth,
  onAlignCalendarDate,
  onGoToToday,
  onOpenTaskEditor,
  onOpenMeetingEditor,
  onToggleMeetingStatus,
  onRescheduleMeeting,
  onCalendarNotice,
  onToggleTaskStatus,
  onRescheduleTask,
  onUpdateTaskDuration,
  onApproveTaskReview: _onApproveTaskReview,
  onReturnTaskReview: _onReturnTaskReview,
  isTaskOverdue,
  showCollaborativeTasks,
  onToggleCollaborativeTasks,
}: TaskCalendarViewProps) {
  const [isJumpPickerOpen, setIsJumpPickerOpen] = useState(false);
  const [draggingTaskId, setDraggingTaskId] = useState<string | null>(null);
  const [dragTargetDay, setDragTargetDay] = useState<number | null>(null);
  const [dragTargetMinute, setDragTargetMinute] = useState<number | null>(null);
  // 5/26 加: +N 聚合 popover 展开. 同时段超过 3 个任务时, 点 +N 弹出 dropdown 列出隐藏任务
  // 之前只 toast 一行不能点单个, 现在点这个 popover 里的每个任务可直接打开 editor
  const [expandedAggregateKey, setExpandedAggregateKey] = useState<string | null>(null);
  useEffect(() => {
    if (!expandedAggregateKey) return;
    const handleOutsideClick = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      if (!target) return;
      if (target.closest('[data-aggregate-popover]')) return;
      if (target.closest('[data-aggregate-trigger]')) return;
      setExpandedAggregateKey(null);
    };
    window.addEventListener('mousedown', handleOutsideClick);
    return () => window.removeEventListener('mousedown', handleOutsideClick);
  }, [expandedAggregateKey]);
  const [expandedCalendarDayKeys, setExpandedCalendarDayKeys] = useRuntimeUiSessionState<string[]>(`${uiSessionScopeKey}:expanded-days`, []);
  const expandedCalendarDays = useMemo(() => new Set(expandedCalendarDayKeys), [expandedCalendarDayKeys]);
  const setExpandedCalendarDays = useCallback((update: Set<string> | ((previous: Set<string>) => Set<string>)) => {
    setExpandedCalendarDayKeys((previousKeys) => {
      const previous = new Set(previousKeys);
      const next = typeof update === 'function' ? update(previous) : update;
      return Array.from(next);
    });
  }, [setExpandedCalendarDayKeys]);
  const dragDropHandledRef = useRef(false);
  const [resizingTaskId, setResizingTaskId] = useState<string | null>(null);
  const [resizePreviewMinutes, setResizePreviewMinutes] = useState<number | null>(null);
  // 5/26 加 ⑨: 顶部 resize 改 startMinute, preview 需要显示新 start
  const [resizePreviewStartMinute, setResizePreviewStartMinute] = useState<number | null>(null);
  const [weekCreateSelection, setWeekCreateSelection] = useState<WeekCreateSelection | null>(null);
  const [visibleWeekPageIndex, setVisibleWeekPageIndex] = useRuntimeUiSessionState(`${uiSessionScopeKey}:visible-week-page`, 1);
  const [isWeekPaging, setIsWeekPaging] = useState(false);
  const resizePreviewRef = useRef<number | null>(null);
  // 5/26 ⑨: top resize 时, mouseUp 用 ref 读最新 startMinute (state 会有 stale closure)
  const resizePreviewStartMinuteRef = useRef<number | null>(null);
  const resizeDraftRef = useRef<{ taskId: string; startY: number; startMinute: number; baseDuration: number; mode: 'top' | 'bottom'; dayDate?: Date } | null>(null);
  // 鼠标进入 resize handle 时,提前把任务卡的 draggable 改成 false。
  // 这是关键 — isResizing state 在 mousedown 后才更新,但 React re-render 跟不上 native dragstart,
  // 所以必须在 mouseenter handle 时就提前禁用 drag,让 mousedown 直接进入 resize 模式而不是 drag。
  const [resizeHoverTaskId, setResizeHoverTaskId] = useState<string | null>(null);
  // now indicator:当前时间在一天里的分钟数. 5/26 把 60s→15s, 避免长时间累积漂移
  // 15px/min slot 高度下 60s 误差 ≈ 14px 跳一下,15s 误差 ≈ 3.5px 顺滑
  const [currentMinuteOfDay, setCurrentMinuteOfDay] = useState<number>(() => {
    const now = new Date();
    return now.getHours() * 60 + now.getMinutes();
  });
  useEffect(() => {
    // 闭包内私有持有 interval id,避免之前用 window.__taskCalendarNowInterval 的
    // 全局变量被两个 TaskCalendarView 实例互相覆盖、unmount 时清不掉的问题.
    let intervalId: number | null = null;
    let cancelled = false;
    const tick = () => {
      const now = new Date();
      setCurrentMinuteOfDay(now.getHours() * 60 + now.getMinutes());
    };
    // 15s 一次顺滑刷新(60s 误差≈14px 跳一下,15s≈3.5px 顺滑) + cancelled 防 unmount 后 race
    intervalId = window.setInterval(() => {
      if (cancelled) return;
      tick();
    }, 15000);
    return () => {
      cancelled = true;
      if (intervalId !== null) {
        window.clearInterval(intervalId);
      }
    };
  }, []);
  const weekCreateDraftRef = useRef<{
    dayKey: number;
    dayDate: Date;
    anchorMinute: number;
    column: HTMLDivElement;
  } | null>(null);
  const weekCreateSelectionRef = useRef<WeekCreateSelection | null>(null);
  const weekCreateCleanupRef = useRef<(() => void) | null>(null);
  const weekTimelineScrollRef = useRef<HTMLDivElement | null>(null);
  const weekPagerRef = useRef<HTMLDivElement | null>(null);
  const calendarToolbarRef = useRef<HTMLDivElement | null>(null);
  const [calendarToolbarHeight, setCalendarToolbarHeight] = useState(0);
  const weekPagerIdleTimerRef = useRef<number | null>(null);
  const weekPagerVerticalSyncRef = useRef(false);
  const weekPagerGestureDeadlineRef = useRef(0);
  // 标记"刚刚 drop 完"的时间戳：浏览器在 drop 之后还会派发 click，
  // 没有这个标志位就会让 handleCreateTaskFromWeekSlot 误以为用户点击空槽要建任务。
  const justDroppedAtRef = useRef(0);
  // AUDIT-20260518-022 修复: today 不能用 useMemo([], []) 固定到首次渲染时间.
  // 软件长时间打开跨过零点后, today 仍指向昨天, 月历高亮和"今日"判断全错.
  // 改为 state + 分钟级 interval + window focus 重算, 跨零点立刻刷新.
  const [today, setToday] = useState(() => new Date());
  useEffect(() => {
    const node = calendarToolbarRef.current;
    if (!node) return;
    const update = () => setCalendarToolbarHeight(Math.ceil(node.getBoundingClientRect().height));
    update();
    const observer = new ResizeObserver(update);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const ymd = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
    const refresh = () => {
      const now = new Date();
      setToday((prev) => (ymd(prev) === ymd(now) ? prev : now));
    };
    // 分钟级 tick: 至少能在跨过零点的一分钟内捕捉到日期变化
    const interval = window.setInterval(refresh, 60_000);
    // 窗口重新激活时也立刻校准 (用户合电脑后打开就是新的一天)
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', refresh);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener('focus', refresh);
      document.removeEventListener('visibilitychange', refresh);
    };
  }, []);
  const taskDateForCalendar = resolveTaskCalendarDate;

  // 月视图打开时，让"今天"滚动到视口上 1/3 位置（过去 1/3 + 未来 2/3）。
  // 只在 calendarDisplayMode 切换到 'month' 时触发，用户主动切月份不打扰。
  const prevDisplayModeRef = useRef<CalendarDisplayMode | null>(null);
  useEffect(() => {
    const prev = prevDisplayModeRef.current;
    prevDisplayModeRef.current = calendarDisplayMode;
    if (calendarDisplayMode !== 'month') return;
    // 从 week 或首次 mount 进入 month → 触发滚动
    if (prev === 'month') return;
    // 仅当查看的是今天所在月时才滚（用户翻到别的月不要跳回）
    if (
      calendarDate.getFullYear() !== today.getFullYear() ||
      calendarDate.getMonth() !== today.getMonth()
    ) return;

    const todayStr = formatDateInputValue(today);
    const align = () => {
      const todayEl = document.querySelector(`[data-day-drop="${todayStr}"]`) as HTMLElement | null;
      if (!todayEl) return;
      // 找最近的可滚动祖先
      let scrollable: HTMLElement | null = todayEl.parentElement;
      while (scrollable && scrollable !== document.body) {
        const style = getComputedStyle(scrollable);
        if (/(auto|scroll)/.test(style.overflowY) && scrollable.scrollHeight > scrollable.clientHeight) {
          break;
        }
        scrollable = scrollable.parentElement;
      }
      if (!scrollable || scrollable === document.body) return;
      const scrollableRect = scrollable.getBoundingClientRect();
      const todayRect = todayEl.getBoundingClientRect();
      const offsetFromTop = todayRect.top - scrollableRect.top;
      const targetOffset = scrollableRect.height * (1 / 3);
      scrollable.scrollBy({
        top: offsetFromTop - targetOffset,
        behavior: 'smooth',
      });
    };
    // 等两帧确保月视图布局完成
    requestAnimationFrame(() => requestAnimationFrame(align));
  }, [calendarDisplayMode, calendarDate, today]);
  const visibleTasks = useMemo(
    () => tasks.filter((task) => {
      if (task.status === 'rejected') return false;
      const placement = getTaskCalendarPlacement(task);
      return placement.kind !== 'none' && Boolean(placement.date);
    }),
    [tasks],
  );
  const activeMonthDate = useMemo(() => new Date(calendarDate.getFullYear(), calendarDate.getMonth(), 1), [calendarDate]);

  const monthTasksByDateKey = useMemo(() => {
    const mapping = new Map<string, Task[]>();
    visibleTasks.forEach((task) => {
      const range = resolveTaskDateTimeRange(task);
      for (
        let cursor = startOfDayValue(range.startDateTime);
        cursor.getTime() < range.endDateTime.getTime();
        cursor = addDays(cursor, 1)
      ) {
        const date = cursor;
        const key = formatDateInputValue(date);
        const existing = mapping.get(key) || [];
        existing.push(task);
        mapping.set(key, existing);
      }
    });
    mapping.forEach((dayTasks, key) => {
      mapping.set(key, sortTasksForCalendar(dayTasks));
    });
    return mapping;
  }, [visibleTasks]);
  const meetingsByDateKey = useMemo(() => {
    const mapping = new Map<string, GC06Meeting[]>();
    meetings.forEach((meeting) => {
      if (meeting.status === 'cancelled' || meeting.status === 'deleted') return;
      const range = meetingDateTimeRange(meeting);
      for (
        let cursor = startOfDayValue(range.startDateTime);
        cursor.getTime() < range.endDateTime.getTime();
        cursor = addDays(cursor, 1)
      ) {
        const key = formatDateInputValue(cursor);
        mapping.set(key, [...(mapping.get(key) || []), meeting]);
      }
    });
    mapping.forEach((items, key) => {
      mapping.set(key, [...items].sort((left, right) => left.startsAt.localeCompare(right.startsAt)));
    });
    return mapping;
  }, [meetings]);

  const tasksByDay = useMemo(() => {
    const mapping = new Map<number, Task[]>();
    const daysInMonth = new Date(activeMonthDate.getFullYear(), activeMonthDate.getMonth() + 1, 0).getDate();
    for (let day = 1; day <= daysInMonth; day += 1) {
      const date = new Date(activeMonthDate.getFullYear(), activeMonthDate.getMonth(), day);
      const dayTasks = monthTasksByDateKey.get(formatDateInputValue(date)) || [];
      if (dayTasks.length > 0) mapping.set(day, dayTasks);
    }
    mapping.forEach((dayTasks, day) => {
      mapping.set(day, sortTasksForCalendar(dayTasks));
    });
    return mapping;
  }, [activeMonthDate, monthTasksByDateKey]);

  const monthTasks = useMemo(() => {
    const monthStart = new Date(activeMonthDate.getFullYear(), activeMonthDate.getMonth(), 1);
    const monthEndExclusive = new Date(activeMonthDate.getFullYear(), activeMonthDate.getMonth() + 1, 1);
    return sortTasksForCalendar(
      visibleTasks.filter((task) => taskOverlapsCalendarWindow(task, monthStart, monthEndExclusive)),
    );
  }, [activeMonthDate, visibleTasks]);

  const monthTimelineWeeks = useMemo(() => {
    const firstRenderedMonth = new Date(calendarDate.getFullYear(), calendarDate.getMonth(), 1);
    const rangeStart = startOfWeek(firstRenderedMonth);
    const lastRenderedDay = new Date(firstRenderedMonth.getFullYear(), firstRenderedMonth.getMonth() + 1, 0);
    const rangeEnd = addDays(lastRenderedDay, 6 - ((lastRenderedDay.getDay() + 6) % 7));
    const weeks: Array<{
      key: string;
      days: Array<{
        date: Date;
        dayTasks: Task[];
      }>;
    }> = [];
    for (let cursor = rangeStart; cursor.getTime() <= rangeEnd.getTime(); cursor = addDays(cursor, 7)) {
      const weekStart = cursor;
      weeks.push({
        key: formatDateInputValue(weekStart),
        days: Array.from({ length: 7 }, (_, index) => {
          const date = addDays(weekStart, index);
          return {
            date,
            dayTasks: monthTasksByDateKey.get(formatDateInputValue(date)) || [],
          };
        }),
      });
    }
    return weeks;
  }, [calendarDate, monthTasksByDateKey]);

  // 月视图跨天"连续条"布局：按周行给每个跨天任务分配车道(lane)，并算出每个日格在每条车道上的渲染片段。
  // 同一任务整周占同一 lane → 相邻格的满格出血片段首尾相连，视觉上是一条横跨多格的条。
  type MonthDayBarSlot = {
    task: Task;
    roundLeft: boolean;   // 真正的起始端(本周内且非上周接续) → 左圆角
    roundRight: boolean;  // 真正的结束端 → 右圆角
    continuesLeft: boolean;  // 左端是"上周接续"→ 显示左箭头
    continuesRight: boolean; // 右端是"下周接续"→ 显示右箭头
    showTitle: boolean;   // 只在起始格显示标题
  };
  const monthMultiDayBarsByDateKey = useMemo(() => {
    const result = new Map<string, Array<MonthDayBarSlot | null>>();
    const DAY_MS = 24 * 60 * 60 * 1000;
    monthTimelineWeeks.forEach((week) => {
      const weekStart = startOfDayValue(week.days[0].date);
      const weekEndDay = startOfDayValue(week.days[6].date);
      const seen = new Set<string>();
      const bars: Array<{ task: Task; firstCol: number; lastCol: number; continuesLeft: boolean; continuesRight: boolean }> = [];
      week.days.forEach(({ dayTasks }) => {
        dayTasks.forEach((task) => {
          if (seen.has(task.id)) return;
          if (!isMultiDayCalendarTask(task)) return;
          seen.add(task.id);
          const range = resolveTaskDateTimeRange(task);
          const startDay = startOfDayValue(range.startDateTime);
          const lastDay = startOfDayValue(new Date(range.endDateTime.getTime() - 1));
          const firstCol = Math.max(0, Math.round((startDay.getTime() - weekStart.getTime()) / DAY_MS));
          const lastCol = Math.min(6, Math.round((lastDay.getTime() - weekStart.getTime()) / DAY_MS));
          if (lastCol < 0 || firstCol > 6 || lastCol < firstCol) return;
          bars.push({
            task,
            firstCol,
            lastCol,
            continuesLeft: startDay.getTime() < weekStart.getTime(),
            continuesRight: lastDay.getTime() > weekEndDay.getTime(),
          });
        });
      });
      // 贪心车道分配：按起始列升序、跨度大的优先，放到第一条"上一段已结束"的车道
      bars.sort((a, b) => a.firstCol - b.firstCol || (b.lastCol - b.firstCol) - (a.lastCol - a.firstCol));
      const laneEndCol: number[] = [];
      const placed: Array<{ bar: (typeof bars)[number]; lane: number }> = [];
      bars.forEach((bar) => {
        let lane = 0;
        while (lane < laneEndCol.length && laneEndCol[lane] >= bar.firstCol) lane += 1;
        laneEndCol[lane] = bar.lastCol;
        placed.push({ bar, lane });
      });
      const laneCount = laneEndCol.length;
      week.days.forEach((dayObj, col) => {
        const slots: Array<MonthDayBarSlot | null> = new Array(laneCount).fill(null);
        placed.forEach(({ bar, lane }) => {
          if (col < bar.firstCol || col > bar.lastCol) return;
          slots[lane] = {
            task: bar.task,
            roundLeft: col === bar.firstCol && !bar.continuesLeft,
            roundRight: col === bar.lastCol && !bar.continuesRight,
            continuesLeft: col === bar.firstCol && bar.continuesLeft,
            continuesRight: col === bar.lastCol && bar.continuesRight,
            showTitle: col === bar.firstCol,
          };
        });
        result.set(formatDateInputValue(dayObj.date), slots);
      });
    });
    return result;
  }, [monthTimelineWeeks]);

  type MonthMeetingBarSlot = {
    meeting: GC06Meeting;
    roundLeft: boolean;
    roundRight: boolean;
    continuesLeft: boolean;
    continuesRight: boolean;
    showTitle: boolean;
  };
  const monthMultiDayMeetingBarsByDateKey = useMemo(() => {
    const result = new Map<string, Array<MonthMeetingBarSlot | null>>();
    const dayMs = 24 * 60 * 60 * 1000;
    monthTimelineWeeks.forEach((week) => {
      const weekStart = startOfDayValue(week.days[0].date);
      const weekEndDay = startOfDayValue(week.days[6].date);
      const bars = meetings
        .filter((meeting) => meeting.status !== 'cancelled' && meeting.status !== 'deleted' && isMultiDayCalendarMeeting(meeting))
        .map((meeting) => {
          const range = meetingDateTimeRange(meeting);
          const startDay = startOfDayValue(range.startDateTime);
          const lastDay = startOfDayValue(new Date(range.endDateTime.getTime() - 1));
          return {
            meeting,
            firstCol: Math.max(0, Math.round((startDay.getTime() - weekStart.getTime()) / dayMs)),
            lastCol: Math.min(6, Math.round((lastDay.getTime() - weekStart.getTime()) / dayMs)),
            continuesLeft: startDay < weekStart,
            continuesRight: lastDay > weekEndDay,
          };
        })
        .filter((bar) => bar.lastCol >= 0 && bar.firstCol <= 6 && bar.lastCol >= bar.firstCol)
        .sort((left, right) => left.firstCol - right.firstCol || (right.lastCol - right.firstCol) - (left.lastCol - left.firstCol));
      const laneEnds: number[] = [];
      const placed = bars.map((bar) => {
        let lane = 0;
        while (lane < laneEnds.length && laneEnds[lane] >= bar.firstCol) lane += 1;
        laneEnds[lane] = bar.lastCol;
        return { bar, lane };
      });
      week.days.forEach((day, column) => {
        const slots: Array<MonthMeetingBarSlot | null> = new Array(laneEnds.length).fill(null);
        placed.forEach(({ bar, lane }) => {
          if (column < bar.firstCol || column > bar.lastCol) return;
          slots[lane] = {
            meeting: bar.meeting,
            roundLeft: column === bar.firstCol && !bar.continuesLeft,
            roundRight: column === bar.lastCol && !bar.continuesRight,
            continuesLeft: column === bar.firstCol && bar.continuesLeft,
            continuesRight: column === bar.lastCol && bar.continuesRight,
            showTitle: column === bar.firstCol,
          };
        });
        result.set(formatDateInputValue(day.date), slots);
      });
    });
    return result;
  }, [meetings, monthTimelineWeeks]);

  const weekStartDate = useMemo(() => startOfWeek(selectedDate), [selectedDate]);
  const weekPages = useMemo(() => {
    return [-7, 0, 7].map((offsetDays) => {
      const startDate = addDays(weekStartDate, offsetDays);
      const days = Array.from({ length: 7 }, (_, index) => addDays(startDate, index));
      const endDate = days[6];
      const tasks = sortTasksForCalendar(
        visibleTasks.filter((task) => taskOverlapsCalendarWindow(task, startDate, addDays(endDate, 1))),
      );
      const timedTasks = days.flatMap((day, dayIndex) => {
        const items = tasks
          .map((task) => {
            const segment = buildTaskDayTimedSegment(task, day);
            if (!segment) return null;
            return {
              task,
              dayIndex,
              dayDate: day,
              ...segment,
            };
          })
          .filter((item): item is { task: Task; dayIndex: number; dayDate: Date; startMinute: number; endMinute: number; durationMinutes: number; timeLabel: string } => Boolean(item));
        return assignTimedTaskLanes(items) as TimedWeekTask[];
      });
      // "未安排时间"任务：有 dueDate / deadlineAt / startDate 落在本周，但没有 scheduledStartAt，
      // 因此不会出现在下方时间网格里 —— 在表头下加一行让用户能看到 + 拖下去分配时间。
      const unscheduledByDay: Task[][] = days.map((day) => {
        const dayStart = new Date(day.getFullYear(), day.getMonth(), day.getDate());
        const dayEnd = addDays(dayStart, 1);
        return tasks.filter((task) => {
          if (getTaskScheduleRange(task)) return false;
          const tDate = resolveTaskCalendarDate(task);
          if (!tDate) return false;
          return tDate >= dayStart && tDate < dayEnd;
        });
      });
      return {
        key: `${startDate.toISOString()}-${offsetDays}`,
        offsetDays,
        startDate,
        endDate,
        days,
        title: formatWeekRangeTitle(startDate, endDate),
        tasks,
        timedTasks,
        unscheduledByDay,
      };
    });
  }, [visibleTasks, weekStartDate]);
  const currentWeekPage = weekPages[1];
  const visibleWeekPage = weekPages[visibleWeekPageIndex] ?? currentWeekPage;
  const weekStartKey = weekStartDate.getTime();
  const weekDays = visibleWeekPage.days;
  const weekEndDate = visibleWeekPage.endDate;
  const weekTasks = visibleWeekPage.tasks;
  const weekTimedTasks = visibleWeekPage.timedTasks;
  // resize useEffect 不能依赖 weekTimedTasks / onUpdateTaskDuration / onCalendarNotice,
  // 否则后台任务刷新 / 父组件 re-render 会重新注册 listener, 丢拖拽中状态.
  // 用 ref 同步, handler 内读 ref.current.
  const weekTimedTasksRef = useRef(weekTimedTasks);
  weekTimedTasksRef.current = weekTimedTasks;
  const onUpdateTaskDurationRef = useRef(onUpdateTaskDuration);
  onUpdateTaskDurationRef.current = onUpdateTaskDuration;
  const onCalendarNoticeRef = useRef(onCalendarNotice);
  onCalendarNoticeRef.current = onCalendarNotice;
  const onRescheduleTaskRef = useRef(onRescheduleTask);
  onRescheduleTaskRef.current = onRescheduleTask;
  const weekDisplayItemsByDay = useMemo(() => {
    const mapping = new Map<number, WeekTaskDisplayItem[]>();
    visibleWeekPage.days.forEach((_, dayIndex) => {
      const items = visibleWeekPage.timedTasks.filter((item) => item.dayIndex === dayIndex) as TimedWeekTask[];
      mapping.set(dayIndex, buildWeekTaskDisplayItems(items));
    });
    return mapping;
  }, [visibleWeekPage.days, visibleWeekPage.timedTasks]);
  const weekMeetingItemsByDay = useMemo(() => {
    const mapping = new Map<number, TimedWeekMeeting[]>();
    visibleWeekPage.days.forEach((day, dayIndex) => {
      const items = meetings
        .filter((meeting) => meeting.status !== 'cancelled' && meeting.status !== 'deleted')
        .map((meeting) => {
          const segment = buildMeetingDayTimedSegment(meeting, day);
          return segment ? { meeting, dayIndex, dayDate: day, ...segment } : null;
        })
        .filter((item): item is Omit<TimedWeekMeeting, 'lane' | 'laneCount'> => Boolean(item));
      mapping.set(dayIndex, assignTimedTaskLanes(items) as TimedWeekMeeting[]);
    });
    return mapping;
  }, [meetings, visibleWeekPage.days]);
  const draggedTask = useMemo(
    () => (calendarDisplayMode === 'week' ? weekTasks : visibleTasks).find((task) => task.id === draggingTaskId) || null,
    [calendarDisplayMode, draggingTaskId, visibleTasks, weekTasks],
  );

  const draggedMeeting = useMemo(() => {
    if (!draggingTaskId?.startsWith('meeting:')) return null;
    const meetingId = draggingTaskId.slice('meeting:'.length);
    return meetings.find((meeting) => meeting.id === meetingId) || null;
  }, [draggingTaskId, meetings]);

  const draggedScheduleTitle = draggedTask?.title || draggedMeeting?.title || '';

  const draggedDurationMinutes = useMemo(() => {
    if (draggedMeeting) {
      const startsAt = new Date(draggedMeeting.startsAt).getTime();
      const endsAt = new Date(draggedMeeting.endsAt).getTime();
      if (Number.isFinite(startsAt) && Number.isFinite(endsAt) && endsAt > startsAt) {
        return Math.max(DAY_TIMELINE_SLOT_MINUTES, Math.round((endsAt - startsAt) / 60_000));
      }
      return DAY_TIMELINE_DEFAULT_DURATION_MINUTES;
    }
    if (!draggedTask) return DAY_TIMELINE_DEFAULT_DURATION_MINUTES;
    const timedMatch = weekTimedTasks.find((item) => item.task.id === draggedTask.id);
    if (timedMatch) return timedMatch.durationMinutes;
    return Math.max(DAY_TIMELINE_SLOT_MINUTES, draggedTask.durationMinutes ?? DAY_TIMELINE_DEFAULT_DURATION_MINUTES);
  }, [draggedMeeting, draggedTask, weekTimedTasks]);

  useEffect(() => {
    if (calendarDisplayMode !== 'week') return;
    const nextScrollTop = Math.max(0, (DAY_TIMELINE_DEFAULT_START_MINUTE / DAY_TIMELINE_SLOT_MINUTES) * DAY_TIMELINE_SLOT_HEIGHT - 12);
    const pager = weekPagerRef.current;
    if (!pager) return;
    pager.querySelectorAll<HTMLElement>('[data-week-scroll="true"]').forEach((node) => {
      node.scrollTop = nextScrollTop;
    });
  }, [calendarDisplayMode, selectedDate]);

  useEffect(() => {
    if (!resizingTaskId || !resizeDraftRef.current) return;

    const handleMouseMove = (event: MouseEvent) => {
      const draft = resizeDraftRef.current;
      if (!draft) return;
      const deltaY = event.clientY - draft.startY;
      const deltaSlots = Math.round(deltaY / DAY_TIMELINE_SLOT_HEIGHT);
      document.body.style.cursor = 'ns-resize';
      document.body.style.userSelect = 'none';
      if (draft.mode === 'top') {
        // 5/26 ⑨: 顶部 resize — 改 startMinute. deltaY > 0 → start 向后移(任务变短)
        //                          deltaY < 0 → start 向前移(任务变长, 占用更早的时间)
        const baseEnd = draft.startMinute + draft.baseDuration;
        const nextStart = Math.max(0, Math.min(baseEnd - DAY_TIMELINE_SLOT_MINUTES, draft.startMinute + deltaSlots * DAY_TIMELINE_SLOT_MINUTES));
        const nextDuration = baseEnd - nextStart;
        resizePreviewRef.current = nextDuration;
        resizePreviewStartMinuteRef.current = nextStart;
        setResizePreviewMinutes(nextDuration);
        setResizePreviewStartMinute(nextStart);
      } else {
        // 不再把时长卡在当天内（旧 maxDuration = 24*60 - startMinute 会把跨天任务一碰就缩成同天）。
        // 允许向下拖过午夜 → 时长 >当天剩余，落库后按跨天分段渲染。保底不小于一格。
        const nextDuration = Math.max(
          DAY_TIMELINE_SLOT_MINUTES,
          draft.baseDuration + deltaSlots * DAY_TIMELINE_SLOT_MINUTES,
        );
        resizePreviewRef.current = nextDuration;
        setResizePreviewMinutes(nextDuration);
      }
    };

    const handleMouseUp = () => {
      const draft = resizeDraftRef.current;
      // 从 ref 读最新 weekTimedTasks, 避免 effect 依赖它导致拖拽中重订阅
      const task = weekTimedTasksRef.current.find((item) => item.task.id === draft?.taskId)?.task;
      const nextDuration = resizePreviewRef.current ?? draft?.baseDuration ?? null;
      const nextStartMinute = draft?.mode === 'top' ? resizePreviewStartMinuteRef.current : null;
      resizeDraftRef.current = null;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      // 5/26 修: resize 期间如果用户曾经 dragOver 过别的 slot, 拖拽预览框 state 不会自动清,
      // 会留个蓝边方块在屏幕上. 这里 resize 结束顺手清掉
      setDragTargetDay(null);
      setDragTargetMinute(null);

      const clearPreview = () => {
        resizePreviewRef.current = null;
        resizePreviewStartMinuteRef.current = null;
        setResizingTaskId(null);
        setResizePreviewMinutes(null);
        setResizePreviewStartMinute(null);
        setResizeHoverTaskId(null);
      };

      // 5/26 ⑨: 顶部 resize 改 startDate + duration. 其他模式只改 duration.
      const changed = !!(task && draft && nextDuration && (
        nextDuration !== draft.baseDuration ||
        (draft.mode === 'top' && nextStartMinute !== null && nextStartMinute !== draft.startMinute)
      ));

      if (changed && task && draft && nextDuration) {
        if (isLocalDraftTaskId(task.id)) {
          clearPreview();
          onCalendarNoticeRef.current?.('info', LOCAL_DRAFT_NOTICE);
          return;
        }
        // 注意:这里 await 期间 isResizing 仍为 true,
        // 任务卡 height/top 由 effectiveDuration / effectiveStart 决定
        void (async () => {
          try {
            if (draft.mode === 'top' && draft.dayDate && nextStartMinute !== null) {
              const newDueDate = combineDateAndTime(draft.dayDate, nextStartMinute);
              await Promise.all([
                onRescheduleTaskRef.current(task, newDueDate, { preserveCalendarViewport: true }),
                onUpdateTaskDurationRef.current(task, nextDuration),
              ]);
            } else {
              await onUpdateTaskDurationRef.current(task, nextDuration);
            }
          } catch {
            // 回滚由父组件 loadTaskBlock 或 catch 提示处理
          } finally {
            clearPreview();
          }
        })();
      } else {
        clearPreview();
      }
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    // 只依赖 resizingTaskId (开始/结束 resize 的"事件" 信号),
    // 其他状态/callback 通过 ref 访问最新值, 拖拽中即便父组件 re-render 也不丢 listener.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resizingTaskId]);

  const cleanupWeekCreateInteraction = useCallback(() => {
    weekCreateCleanupRef.current?.();
    weekCreateCleanupRef.current = null;
    weekCreateDraftRef.current = null;
    weekCreateSelectionRef.current = null;
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  }, []);

  useEffect(() => {
    return () => {
      cleanupWeekCreateInteraction();
    };
  }, [cleanupWeekCreateInteraction]);

  const timelineSlotMinutes = useMemo(
    () => Array.from({ length: (24 * 60) / DAY_TIMELINE_SLOT_MINUTES }, (_, index) => index * DAY_TIMELINE_SLOT_MINUTES),
    [],
  );
  const hourLineMinutes = useMemo(
    () => timelineSlotMinutes.filter((minute) => minute % 60 === 0),
    [timelineSlotMinutes],
  );

  const monthStats = useMemo(() => {
    return {
      dayCount: tasksByDay.size,
      total: monthTasks.length,
      open: monthTasks.filter((task) => task.status !== 'done').length,
      done: monthTasks.filter((task) => task.status === 'done').length,
      overdue: monthTasks.filter((task) => isTaskOverdue(task, today)).length,
      highPriority: monthTasks.filter((task) => task.status !== 'done' && task.priority === 'high').length,
    };
  }, [isTaskOverdue, monthTasks, tasksByDay.size, today]);

  const weekStats = useMemo(() => {
    return {
      total: weekTasks.length,
      open: weekTasks.filter((task) => task.status !== 'done').length,
      done: weekTasks.filter((task) => task.status === 'done').length,
      overdue: weekTasks.filter((task) => isTaskOverdue(task, today)).length,
      highPriority: weekTasks.filter((task) => task.status !== 'done' && task.priority === 'high').length,
    };
  }, [isTaskOverdue, today, weekTasks]);
  const visibleWeekStats = useMemo(() => {
    return {
      total: visibleWeekPage.tasks.length,
      open: visibleWeekPage.tasks.filter((task) => task.status !== 'done').length,
      done: visibleWeekPage.tasks.filter((task) => task.status === 'done').length,
      overdue: visibleWeekPage.tasks.filter((task) => isTaskOverdue(task, today)).length,
      highPriority: visibleWeekPage.tasks.filter((task) => task.status !== 'done' && task.priority === 'high').length,
    };
  }, [isTaskOverdue, today, visibleWeekPage]);

  const handleDateJump = (value: string) => {
    const nextDate = new Date(value);
    if (Number.isNaN(nextDate.getTime())) return;
    onSelectDate(nextDate);
    onAlignCalendarDate(nextDate);
    setIsJumpPickerOpen(false);
  };

  const handleShiftPeriod = (delta: number) => {
    if (calendarDisplayMode === 'week') {
      const nextDate = addDays(selectedDate, delta * 7);
      onSelectDate(nextDate);
      onAlignCalendarDate(nextDate);
      return;
    }
    onShiftMonth(delta);
  };

  const handleDaySelect = (date: Date) => {
    if (calendarDisplayMode === 'week') {
      onSelectDate(date);
      return;
    }
    onSelectDate(date);
    if (date.getFullYear() !== calendarDate.getFullYear() || date.getMonth() !== calendarDate.getMonth()) {
      onAlignCalendarDate(date);
    }
  };

  const handleTaskDrop = async (task: Task, cellDate: Date) => {
    if (isLocalDraftTaskId(task.id)) {
      setDragTargetDay(null);
      setDragTargetMinute(null);
      setDraggingTaskId(null);
      onCalendarNotice?.('info', LOCAL_DRAFT_NOTICE);
      return;
    }
    const nextDueDate = formatDateInputValue(cellDate);
    const currentTaskDate = taskDateForCalendar(task);
    if (
      currentTaskDate
      &&
      currentTaskDate.getFullYear() === cellDate.getFullYear()
      && currentTaskDate.getMonth() === cellDate.getMonth()
      && currentTaskDate.getDate() === cellDate.getDate()
    ) {
      return;
    }
    try {
      await onRescheduleTask(task, nextDueDate);
      // 仅成功才切换选中日期；失败时 handleRescheduleTask 已做 UI 回滚 + flash 错误，
      // 这里如果照常 onSelectDate 视口会飞到目标月，用户以为任务消失。
      onSelectDate(cellDate);
    } catch {
      // 回滚已由 App 层处理，这里只阻止 unhandled rejection 和视口跳转。
    }
  };

  const handleMeetingDrop = async (meeting: GC06Meeting, cellDate: Date) => {
    if (isSameDay(new Date(meeting.startsAt), cellDate)) return;
    try {
      await onRescheduleMeeting?.(meeting, formatDateInputValue(cellDate));
      onSelectDate(cellDate);
    } catch {
      // App 层显示真实错误并刷新会议投影；这里不让视口跳到未保存的日期。
    }
  };

  const handleTimelineTaskDrop = async (task: Task, minuteOfDay: number) => {
    if (isLocalDraftTaskId(task.id)) {
      setDragTargetMinute(null);
      setDraggingTaskId(null);
      onCalendarNotice?.('info', LOCAL_DRAFT_NOTICE);
      return;
    }
    const nextDueDate = combineDateAndTime(selectedDate, minuteOfDay);
    setDragTargetMinute(null);
    setDraggingTaskId(null);
    try {
      await onRescheduleTask(task, nextDueDate);
    } catch {
      // 同上，rollback 已处理。
    }
  };

  const handleWeekTimelineTaskDrop = async (task: Task, dayDate: Date, minuteOfDay: number) => {
    if (isLocalDraftTaskId(task.id)) {
      setDragTargetMinute(null);
      setDragTargetDay(null);
      setDraggingTaskId(null);
      onCalendarNotice?.('info', LOCAL_DRAFT_NOTICE);
      return;
    }
    const nextDueDate = combineDateAndTime(dayDate, minuteOfDay);
    setDragTargetMinute(null);
    setDragTargetDay(null);
    setDraggingTaskId(null);
    try {
      await onRescheduleTask(task, nextDueDate, { preserveCalendarViewport: true });
      // 仅成功才切换选中日期。原代码把 onSelectDate 放在 await 前面，
      // 失败时视口已经跳到目标日但任务实际还在原位，用户以为任务消失了。
      onSelectDate(dayDate);
    } catch {
      // rollback 已处理。
    }
  };

  const handleWeekTimelineMeetingDrop = async (meeting: GC06Meeting, dayDate: Date, minuteOfDay: number) => {
    const nextStartDateTime = combineDateAndTime(dayDate, minuteOfDay);
    setDragTargetMinute(null);
    setDragTargetDay(null);
    setDraggingTaskId(null);
    try {
      await onRescheduleMeeting?.(meeting, nextStartDateTime);
      onSelectDate(dayDate);
    } catch {
      // App 层负责回滚会议投影并显示真实错误。
    }
  };

  const resolveDraggedTaskId = (event: React.DragEvent) => {
    const transferTaskId = event.dataTransfer.getData('text/plain').trim();
    return transferTaskId || draggingTaskId || null;
  };

  const handleStartWeekTaskResize = (
    taskId: string,
    startMinute: number,
    baseDuration: number,
    event: React.MouseEvent,
    options?: { mode?: 'top' | 'bottom'; dayDate?: Date },
  ) => {
    event.preventDefault();
    event.stopPropagation();
    if (isLocalDraftTaskId(taskId)) {
      onCalendarNotice?.('info', LOCAL_DRAFT_NOTICE);
      return;
    }
    const mode = options?.mode || 'bottom';
    resizeDraftRef.current = {
      taskId,
      startY: event.clientY,
      startMinute,
      baseDuration,
      mode,
      dayDate: options?.dayDate,
    };
    resizePreviewRef.current = baseDuration;
    resizePreviewStartMinuteRef.current = mode === 'top' ? startMinute : null;
    setResizingTaskId(taskId);
    setResizePreviewMinutes(baseDuration);
    setResizePreviewStartMinute(mode === 'top' ? startMinute : null);
  };

  const handleStartWeekCreateSelection = (
    day: Date,
    column: HTMLDivElement,
    event: React.MouseEvent<HTMLDivElement>,
  ) => {
    if (event.button !== 0) return; // 只左键
    if (draggingTaskId || resizingTaskId) return;
    // 5/26 接通: 点击源如果是任务卡 / +N chip / popover, 跳过, 让原 handler 接
    const target = event.target as HTMLElement | null;
    if (target?.closest('[data-task-card="true"]')) return;
    if (target?.closest('[data-aggregate-trigger="true"]')) return;
    if (target?.closest('[data-aggregate-popover="true"]')) return;
    event.preventDefault();
    event.stopPropagation();
    const anchorMinute = minuteOfDayFromClientPosition(column, event.clientY);
    const anchorClientY = event.clientY;
    const dayKey = day.getTime();
    cleanupWeekCreateInteraction();
    const initialSelection = {
      dayKey,
      dayDate: day,
      startMinute: anchorMinute,
      endMinute: Math.min(anchorMinute + DAY_TIMELINE_SLOT_MINUTES, 24 * 60),
    };
    weekCreateDraftRef.current = {
      dayKey,
      dayDate: day,
      anchorMinute,
      column,
    };
    weekCreateSelectionRef.current = initialSelection;
    setWeekCreateSelection(initialSelection);
    document.body.style.cursor = 'ns-resize';
    document.body.style.userSelect = 'none';

    // 5/26: moved 标志 — 鼠标真有纵向移动 > 4px 才算"拖选". 移动很小算单击, 交给 onClick 走默认 60min.
    // 这跟 Google Calendar/Outlook 的标准行为一致.
    let hasMoved = false;
    const MOVE_THRESHOLD = 4;

    const updateSelectionFromPointer = (clientY: number) => {
      const draft = weekCreateDraftRef.current;
      if (!draft) return;
      // 锁列 — 行业标准: mouseMove 不跟随 cursor 横移列, column 锁死在 mouseDown 那一天.
      // 鼠标垂直坐标用 draft.column 算 minute, 超出列边界(列顶/列底) clamp 到 0/24:00.
      const currentMinute = minuteOfDayFromClientPosition(draft.column, clientY);
      const nextRange = buildSelectionRange(draft.anchorMinute, currentMinute);
      const nextSelection = {
        dayKey: draft.dayKey,
        dayDate: draft.dayDate,
        startMinute: nextRange.startMinute,
        endMinute: nextRange.endMinute,
      };
      weekCreateSelectionRef.current = nextSelection;
      setWeekCreateSelection(nextSelection);
    };

    const handleWindowMouseMove = (moveEvent: MouseEvent) => {
      if (!hasMoved && Math.abs(moveEvent.clientY - anchorClientY) > MOVE_THRESHOLD) {
        hasMoved = true;
      }
      if (hasMoved) updateSelectionFromPointer(moveEvent.clientY);
    };

    const handleWindowKeyDown = (keyEvent: KeyboardEvent) => {
      // Esc 取消拖选 (行业标准)
      if (keyEvent.key === 'Escape') {
        cleanupWeekCreateInteraction();
        setWeekCreateSelection(null);
        hasMoved = false;
      }
    };

    const handleWindowMouseUp = () => {
      const draft = weekCreateDraftRef.current;
      const selection = weekCreateSelectionRef.current;
      const moved = hasMoved;
      cleanupWeekCreateInteraction();
      setWeekCreateSelection(null);
      if (!draft || !selection) return;
      // 没真拖动 → 当作单击, 不在这里 openTaskEditor (让外层 onClick 走 handleCreateTaskFromWeekSlot 默认 60min)
      if (!moved) return;
      // 防止 mouseUp 后 chrome 自动派发 click 触发 handleCreateTaskFromWeekSlot 双开 editor
      justDroppedAtRef.current = Date.now();
      const durationMinutes = Math.max(DAY_TIMELINE_SLOT_MINUTES, selection.endMinute - selection.startMinute);
      const dueDate = combineDateAndTime(draft.dayDate, selection.startMinute);
      window.requestAnimationFrame(() => {
        onSelectDate(draft.dayDate);
        onOpenTaskEditor(undefined, dueDate, { durationMinutes });
      });
    };

    window.addEventListener('mousemove', handleWindowMouseMove);
    window.addEventListener('mouseup', handleWindowMouseUp);
    window.addEventListener('keydown', handleWindowKeyDown);
    weekCreateCleanupRef.current = () => {
      window.removeEventListener('mousemove', handleWindowMouseMove);
      window.removeEventListener('mouseup', handleWindowMouseUp);
      window.removeEventListener('keydown', handleWindowKeyDown);
    };
  };

  const handleGoToToday = () => {
    onGoToToday();
  };

  const handleCreateTaskFromWeekSlot = useCallback(
    (day: Date, startMinute: number) => {
      if (draggingTaskId || resizingTaskId) return;
      // 刚刚有 drop 落到这个 slot —— 浏览器之后还会派发 click，过滤掉避免双触发。
      if (Date.now() - justDroppedAtRef.current < 250) return;
      const durationMinutes = DAY_TIMELINE_DEFAULT_DURATION_MINUTES;
      const endMinute = Math.min(startMinute + durationMinutes, DAY_MINUTES);
      const hasOverlap = visibleWeekPage.timedTasks.some(
        (item) =>
          item.dayDate.getTime() === day.getTime()
          && item.startMinute < endMinute
          && item.endMinute > startMinute,
      );
      if (hasOverlap) {
        onCalendarNotice?.('info', '这个时间段已经有任务了，请点空闲时间再新建，或先调整现有任务。');
        return;
      }
      window.requestAnimationFrame(() => {
        onSelectDate(day);
        onOpenTaskEditor(undefined, combineDateAndTime(day, startMinute), { durationMinutes });
      });
    },
    [draggingTaskId, onCalendarNotice, onOpenTaskEditor, onSelectDate, resizingTaskId, visibleWeekPage.timedTasks],
  );

  const centerWeekPager = (behavior: ScrollBehavior = 'auto') => {
    const pager = weekPagerRef.current;
    if (!pager) return;
    const pageWidth = pager.clientWidth;
    if (!pageWidth) return;
    pager.scrollTo({ left: pageWidth, behavior });
  };

  useEffect(() => {
    if (calendarDisplayMode !== 'week') return;
    let frame = 0;
    frame = window.requestAnimationFrame(() => {
      setVisibleWeekPageIndex(1);
      centerWeekPager('auto');
    });
    return () => {
      window.cancelAnimationFrame(frame);
      if (weekPagerIdleTimerRef.current) {
        window.clearTimeout(weekPagerIdleTimerRef.current);
        weekPagerIdleTimerRef.current = null;
      }
    };
  }, [calendarDisplayMode, weekStartKey]);

  useEffect(() => {
    if (calendarDisplayMode !== 'week' || isWeekPaging) return;
    if (Date.now() < weekPagerGestureDeadlineRef.current) return;
    const pager = weekPagerRef.current;
    if (!pager || pager.clientWidth <= 0) return;
    const centerOffset = Math.abs(pager.scrollLeft - pager.clientWidth);
    if (centerOffset < 2) return;
    let frame = 0;
    frame = window.requestAnimationFrame(() => {
      setVisibleWeekPageIndex(1);
      centerWeekPager('auto');
    });
    return () => {
      window.cancelAnimationFrame(frame);
    };
  }, [calendarDisplayMode, isWeekPaging, weekTasks, weekTimedTasks]);

  const handleWeekVerticalScroll = (event: React.UIEvent<HTMLDivElement>) => {
    if (weekPagerVerticalSyncRef.current) return;
    const source = event.currentTarget;
    const pager = weekPagerRef.current;
    if (!pager) return;
    weekPagerVerticalSyncRef.current = true;
    pager.querySelectorAll<HTMLElement>('[data-week-scroll="true"]').forEach((node) => {
      if (node !== source) node.scrollTop = source.scrollTop;
    });
    window.requestAnimationFrame(() => {
      weekPagerVerticalSyncRef.current = false;
    });
  };

  const finalizeWeekPagerScroll = () => {
    const pager = weekPagerRef.current;
    if (!pager) return;
    const pageWidth = pager.clientWidth;
    if (!pageWidth) return;
    weekPagerGestureDeadlineRef.current = 0;
    const pageIndex = Math.round(pager.scrollLeft / pageWidth);
    if (pageIndex === 0) {
      onSelectDate(addDays(selectedDate, -7));
      return;
    }
    if (pageIndex === 2) {
      onSelectDate(addDays(selectedDate, 7));
      return;
    }
    centerWeekPager('smooth');
  };

  const handleWeekPagerScroll = () => {
    const pager = weekPagerRef.current;
    if (!pager || pager.clientWidth <= 0) return;
    const now = Date.now();
    const isUserGesture = now < weekPagerGestureDeadlineRef.current;
    const centerOffset = Math.abs(pager.scrollLeft - pager.clientWidth);
    if (!isUserGesture) {
      if (centerOffset < 6) return;
      return;
    }
    weekPagerGestureDeadlineRef.current = now + 180;
    // 用 functional setState 短路相同值：每个 scroll 事件都进这里（几十次/秒），
    // 如果状态没真的变化就不要触发整组件重渲染（这是滑动卡顿的主因）。
    setIsWeekPaging((prev) => (prev ? prev : true));
    const pageIndex = Math.max(0, Math.min(2, Math.round(pager.scrollLeft / pager.clientWidth)));
    setVisibleWeekPageIndex((prev) => {
      if (prev === pageIndex) return prev;
      // 5/26 修: 跨页时 dragTargetDay 还是上一页某天的 timestamp, 拖拽预览框会画到错位置.
      // 翻页清掉 preview state, 用户在新一页 dragOver 时会重新设
      setDragTargetDay(null);
      setDragTargetMinute(null);
      return pageIndex;
    });
    if (weekPagerIdleTimerRef.current) {
      window.clearTimeout(weekPagerIdleTimerRef.current);
    }
    weekPagerIdleTimerRef.current = window.setTimeout(() => {
      setIsWeekPaging(false);
      finalizeWeekPagerScroll();
    }, 120);
  };

  const handleWeekScrollWheel = (event: React.WheelEvent<HTMLDivElement>) => {
    if (Math.abs(event.deltaX) <= Math.abs(event.deltaY) || Math.abs(event.deltaX) < 6) return;
    const pager = weekPagerRef.current;
    if (!pager) return;
    weekPagerGestureDeadlineRef.current = Date.now() + 280;
    pager.scrollLeft += event.deltaX;
    event.preventDefault();
  };

  const handleWeekTaskSelect = (event?: React.MouseEvent) => {
    event?.preventDefault();
    event?.stopPropagation();
  };

  const periodStats = calendarDisplayMode === 'week' ? visibleWeekStats : monthStats;
  const periodTitle = calendarDisplayMode === 'week' ? visibleWeekPage.title : formatMonthTitle(activeMonthDate);

  return (
    <div className="w-full min-w-0 grid grid-cols-1 gap-6 items-start transition-all xl:grid-cols-[minmax(0,1fr)]">
      <div className="min-w-0 w-full bg-white border border-gray-100 rounded-2xl overflow-visible">
        <div ref={calendarToolbarRef} className="sticky top-0 z-40 flex flex-col gap-3 rounded-t-2xl border-b border-gray-100 bg-white/95 px-5 py-5 backdrop-blur lg:px-6">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                <div className="inline-flex rounded-md bg-white p-0.5 ring-1 ring-inset ring-gray-200">
                  {(['month', 'week'] as CalendarDisplayMode[]).map((mode) => (
                    <button
                      key={mode}
                      type="button"
                      className={`rounded px-3 py-1 text-[11px] font-medium uppercase tracking-[0.14em] transition-colors ${calendarDisplayMode === mode ? 'bg-[#5B7BFE]/10 text-[#3652c9]' : 'text-gray-500 hover:text-gray-800'}`}
                      onClick={() => onSetCalendarDisplayMode(mode)}
                    >
                      {mode === 'month' ? '月' : '周'}
                    </button>
                  ))}
                </div>
                <h2 className={`text-[20px] lg:text-[22px] font-light tracking-tight text-gray-900 transition-all duration-200 ${isWeekPaging && calendarDisplayMode === 'week' ? 'opacity-90 translate-x-[1px]' : 'opacity-100 translate-x-0'}`}>{periodTitle}</h2>
              </div>
              <div className={`flex flex-wrap gap-1.5 text-[10px] font-medium transition-opacity duration-200 ${isWeekPaging && calendarDisplayMode === 'week' ? 'opacity-85' : 'opacity-100'}`}>
                <span className="rounded-full px-2.5 py-[2px] text-gray-600 ring-1 ring-inset ring-gray-200 uppercase tracking-[0.10em]">{calendarDisplayMode === 'week' ? '本周' : '本月'} · {periodStats.total}</span>
                <span className="rounded-full px-2.5 py-[2px] text-emerald-700 ring-1 ring-inset ring-emerald-200 uppercase tracking-[0.10em]">完成 {periodStats.done}</span>
                <span className="rounded-full px-2.5 py-[2px] text-amber-700 ring-1 ring-inset ring-amber-200 uppercase tracking-[0.10em]">待推进 {periodStats.open}</span>
                <span className="rounded-full px-2.5 py-[2px] text-rose-700 ring-1 ring-inset ring-rose-200 uppercase tracking-[0.10em]">逾期 {periodStats.overdue}</span>
                <span className="rounded-full px-2.5 py-[2px] text-violet-700 ring-1 ring-inset ring-violet-200 uppercase tracking-[0.10em]">高优 {periodStats.highPriority}</span>
              </div>
            </div>

            <div className="flex flex-wrap items-center justify-end gap-2 self-start lg:self-auto">
              <button
                type="button"
                role="switch"
                aria-checked={showCollaborativeTasks}
                aria-label={showCollaborativeTasks ? '隐藏个人任务' : '显示全部任务'}
                onClick={onToggleCollaborativeTasks}
                className="group relative flex items-center overflow-visible"
              >
                <span className="pointer-events-none absolute left-0 top-1/2 -translate-x-[calc(100%+8px)] -translate-y-1/2 text-[11px] font-medium text-gray-400 whitespace-nowrap opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-visible:opacity-100 group-active:opacity-100">
                  {showCollaborativeTasks ? '隐藏个人任务' : '显示全部任务'}
                </span>
                <span
                  className={`relative inline-flex h-6 w-10 items-center rounded-full transition-colors duration-200 ${
                    showCollaborativeTasks ? 'bg-[#5B7BFE]' : 'bg-gray-300'
                  }`}
                >
                  <span
                    className={`inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200 ${
                      showCollaborativeTasks ? 'translate-x-5' : 'translate-x-1'
                    }`}
                  />
                </span>
              </button>
              <div className="relative flex items-center gap-1.5">
              <button
                type="button"
                className="h-9 w-9 rounded-md text-gray-500 ring-1 ring-inset ring-gray-200 hover:text-[#5B7BFE] hover:ring-[#5B7BFE]/30 transition-colors"
                onClick={() => handleShiftPeriod(-1)}
              >
                <ChevronLeft size={16} className="mx-auto" />
              </button>
              <button
                type="button"
                className="h-9 px-3 rounded-md bg-white text-[11.5px] font-medium text-gray-700 whitespace-nowrap ring-1 ring-inset ring-gray-200 hover:text-[#5B7BFE] hover:ring-[#5B7BFE]/30 transition-colors"
                onClick={handleGoToToday}
              >
                今天
              </button>
              <button
                type="button"
                aria-label="跳转日期"
                className={`h-9 w-9 rounded-md ring-1 ring-inset transition-colors ${
                  isJumpPickerOpen
                    ? 'ring-[#5B7BFE]/40 bg-[#5B7BFE]/10 text-[#3652c9]'
                    : 'ring-gray-200 bg-white text-gray-700 hover:text-[#5B7BFE] hover:ring-[#5B7BFE]/30'
                }`}
                onClick={() => setIsJumpPickerOpen((prev) => !prev)}
              >
                <Search size={14} className="mx-auto" />
              </button>
              <button
                type="button"
                className="h-9 w-9 rounded-md text-gray-500 ring-1 ring-inset ring-gray-200 hover:text-[#5B7BFE] hover:ring-[#5B7BFE]/30 transition-colors"
                onClick={() => handleShiftPeriod(1)}
              >
                <ChevronRight size={16} className="mx-auto" />
              </button>

              {isJumpPickerOpen && (
                <div className="absolute top-11 right-0 z-20 w-[280px] rounded-[24px] border border-gray-200 bg-white p-4 shadow-[0_20px_50px_rgba(15,23,42,0.12)]">
                  <p className="text-[12px] font-bold text-gray-500 mb-3">跳到任意日期</p>
                  <input
                    type="date"
                    value={formatDateInputValue(selectedDate)}
                    onChange={(event) => handleDateJump(event.target.value)}
                    className="w-full bg-gray-50 border border-gray-200 rounded-2xl px-4 py-3 text-[13px] font-bold outline-none"
                  />
                  <button
                    type="button"
                    className="mt-3 w-full rounded-2xl bg-[#5B7BFE] text-white text-[13px] font-bold h-11 shadow-[0_6px_18px_rgba(91,123,254,0.28)]"
                    onClick={() => {
                      handleGoToToday();
                      setIsJumpPickerOpen(false);
                    }}
                  >
                    回到今天
                  </button>
                </div>
              )}
              </div>
            </div>
          </div>

        </div>

        {calendarDisplayMode === 'month' ? (
          <>
            <div
              className="sticky z-30 grid grid-cols-7 border-b border-gray-100 bg-white/95 px-5 pt-5 pb-3 text-center text-[10px] font-semibold uppercase tracking-[0.18em] text-gray-400 backdrop-blur lg:px-6"
              style={{ top: `${calendarToolbarHeight}px` }}
            >
              {['周一', '周二', '周三', '周四', '周五', '周六', '周日'].map((day) => (
                <div key={day}>{day}</div>
              ))}
            </div>

            <div>
              {monthTimelineWeeks.map((week) => (
                <div key={week.key} className="grid w-full grid-cols-7">
                  {week.days.map(({ date: cellDate, dayTasks }) => {
                    const isActiveSelection = isSameDay(cellDate, selectedDate);
                    const isToday = isSameDay(cellDate, today);
                    const isMonthAnchor = cellDate.getDate() === 1;
                    // 跨天任务走"连续条"车道，不再进单格 chip 列表
                    const cellChipTasks = dayTasks.filter((task) => !isMultiDayCalendarTask(task));
                    const cellBarSlots = monthMultiDayBarsByDateKey.get(formatDateInputValue(cellDate)) || [];
                    const meetingBarSlots = monthMultiDayMeetingBarsByDateKey.get(formatDateInputValue(cellDate)) || [];
                    const overflowCount = Math.max(cellChipTasks.length - 4, 0);
                    const chinaCalendarMarkers = getChinaCalendarMarkers(cellDate);
                    return (
                      <div
                        key={formatDateInputValue(cellDate)}
                        role="button"
                        tabIndex={0}
                        onClick={() => {
                          handleDaySelect(cellDate);
                        }}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault();
                            handleDaySelect(cellDate);
                          }
                        }}
                        onDragOver={(event) => {
                          const draggedTaskId = resolveDraggedTaskId(event);
                          if (!draggedTaskId) return;
                          if (isLocalDraftTaskId(draggedTaskId)) return;
                          event.preventDefault();
                          if (dragTargetDay !== cellDate.getTime()) {
                            setDragTargetDay(cellDate.getTime());
                          }
                        }}
                        onDragLeave={() => {
                          if (dragTargetDay === cellDate.getTime()) {
                            setDragTargetDay(null);
                          }
                        }}
                        onDrop={(event) => {
                          const draggedTaskId = resolveDraggedTaskId(event);
                          if (!draggedTaskId) return;
                          event.preventDefault();
                          const droppedMeeting = draggedTaskId.startsWith('meeting:')
                            ? meetings.find((item) => item.id === draggedTaskId.slice('meeting:'.length))
                            : null;
                          const droppedTask = droppedMeeting ? null : visibleTasks.find((item) => item.id === draggedTaskId);
                          dragDropHandledRef.current = true;
                          setDragTargetDay(null);
                          setDraggingTaskId(null);
                          if (droppedMeeting) {
                            void handleMeetingDrop(droppedMeeting, cellDate);
                          } else if (droppedTask) {
                            void handleTaskDrop(droppedTask, cellDate);
                          }
                        }}
                        data-calendar-date={formatDateInputValue(cellDate)}
                        data-day-drop={formatDateInputValue(cellDate)}
                        className={`relative min-h-[146px] rounded-none border-r border-b border-gray-100 bg-transparent p-2.5 text-left align-top outline-none transition-colors focus:outline-none focus-visible:outline-none cursor-pointer hover:bg-slate-50 ${
                          isActiveSelection ? 'bg-blue-50/40' : ''
                        } ${
                          dragTargetDay === cellDate.getTime() ? 'bg-blue-100 ring-2 ring-inset ring-[#5B7BFE]/40' : ''
                        }`}
                      >
                        <div className="relative z-10 flex h-full flex-col">
                          <div className="flex items-start justify-between gap-2">
                            <div className="flex items-center gap-2">
                              <div className={`h-7 min-w-7 rounded-full flex items-center justify-center px-1 text-[13px] font-medium ${
                                isToday ? 'bg-rose-500 text-white' : isActiveSelection ? 'bg-[#5B7BFE] text-white' : 'text-gray-700'
                              }`}>
                                {cellDate.getDate()}
                              </div>
                              {isMonthAnchor && (
                                <span className="text-[9px] font-semibold uppercase tracking-[0.18em] text-gray-400">
                                  {cellDate.getMonth() + 1}月
                                </span>
                              )}
                            </div>
                            {chinaCalendarMarkers.length > 0 && (
                              <div className="flex flex-nowrap justify-end gap-1 max-w-[55%] overflow-hidden">
                                {chinaCalendarMarkers.slice(0, 2).map((marker) => (
                                  <span
                                    key={`${formatDateInputValue(cellDate)}-${marker.kind}-${marker.label}`}
                                    className={`rounded-full border px-1.5 py-0.5 text-[10px] font-semibold leading-none ${calendarMarkerClassName(marker)}`}
                                  >
                                    {marker.label}
                                  </span>
                                ))}
                              </div>
                            )}
                          </div>

                          {meetingBarSlots.length > 0 && (
                            <div className="mt-2 flex flex-col gap-1">
                              {meetingBarSlots.map((slot, lane) => {
                                if (!slot) return <div key={`meeting-barspace-${lane}`} style={{ height: MONTH_MULTIDAY_BAR_HEIGHT }} aria-hidden="true" />;
                                return (
                                  <div
                                    key={`meeting-bar-${lane}-${slot.meeting.id}`}
                                    role="button"
                                    tabIndex={0}
                                    draggable
                                    title={`${formatMeetingScheduleLabel(slot.meeting)} ${slot.meeting.title}`}
                                    onMouseDown={(event) => event.stopPropagation()}
                                    onDragStart={(event) => {
                                      event.stopPropagation();
                                      event.dataTransfer.effectAllowed = 'move';
                                      event.dataTransfer.setData('text/plain', `meeting:${slot.meeting.id}`);
                                      dragDropHandledRef.current = false;
                                      setDraggingTaskId(`meeting:${slot.meeting.id}`);
                                    }}
                                    onDragEnd={() => {
                                      setDraggingTaskId(null);
                                      setDragTargetDay(null);
                                      dragDropHandledRef.current = false;
                                    }}
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      onOpenMeetingEditor?.(slot.meeting);
                                    }}
                                    className={`flex cursor-grab items-center gap-1 overflow-hidden whitespace-nowrap border-y border-violet-200 bg-violet-50 px-2 text-[10px] font-semibold leading-none text-violet-700 shadow-[0_1px_2px_rgba(15,23,42,0.04)] active:cursor-grabbing ${slot.roundLeft ? 'ml-0 rounded-l-md border-l' : '-ml-2.5'} ${slot.roundRight ? 'mr-0 rounded-r-md border-r' : '-mr-2.5'}`}
                                    style={{ height: MONTH_MULTIDAY_BAR_HEIGHT }}
                                  >
                                    {slot.continuesLeft && <ChevronLeft size={10} strokeWidth={2.5} className="shrink-0 opacity-70" aria-hidden="true" />}
                                    {slot.showTitle ? <span className="overflow-hidden text-ellipsis">{formatMeetingScheduleLabel(slot.meeting)} {slot.meeting.title}</span> : <span className="flex-1" aria-hidden="true" />}
                                    {slot.continuesRight && <ChevronRight size={10} strokeWidth={2.5} className="ml-auto shrink-0 opacity-70" aria-hidden="true" />}
                                  </div>
                                );
                              })}
                            </div>
                          )}

                          {/* 跨天任务连续条：放在可滚动 chip 容器之外，满格出血(-mx-2.5)使相邻格首尾相连；空车道等高占位保持跨格对齐 */}
                          {cellBarSlots.length > 0 && (
                            <div className="mt-2 flex flex-col gap-1">
                            {cellBarSlots.map((slot, lane) => {
                              if (!slot) {
                                return <div key={`barspace-${lane}`} style={{ height: MONTH_MULTIDAY_BAR_HEIGHT }} aria-hidden="true" />;
                              }
                              const barStyle = calendarChipStyle(slot.task, clientColorById);
                              return (
                                <div
                                  key={`bar-${lane}-${slot.task.id}`}
                                  data-no-month-range-drag="true"
                                  role="button"
                                  tabIndex={0}
                                  title={slot.task.title}
                                  draggable={!isLocalDraftTaskId(slot.task.id)}
                                  onMouseDown={(event) => event.stopPropagation()}
                                  onDragStart={(event) => {
                                    event.stopPropagation();
                                    if (isLocalDraftTaskId(slot.task.id)) {
                                      event.preventDefault();
                                      onCalendarNotice?.('info', LOCAL_DRAFT_NOTICE);
                                      return;
                                    }
                                    // 跨天条拖拽：落到哪个日格，哪天就是任务的起始日（duration 保留，结束随之顺移）
                                    event.dataTransfer.effectAllowed = 'move';
                                    event.dataTransfer.setData('text/plain', slot.task.id);
                                    dragDropHandledRef.current = false;
                                    setDraggingTaskId(slot.task.id);
                                  }}
                                  onDragEnd={() => {
                                    if (!dragDropHandledRef.current) {
                                      setDraggingTaskId(null);
                                      setDragTargetDay(null);
                                    }
                                    dragDropHandledRef.current = false;
                                  }}
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    if (isLocalDraftTaskId(slot.task.id)) {
                                      onCalendarNotice?.('info', LOCAL_DRAFT_NOTICE);
                                      return;
                                    }
                                    onOpenTaskEditor(slot.task);
                                  }}
                                  onKeyDown={(event) => {
                                    if (event.key === 'Enter' || event.key === ' ') {
                                      event.preventDefault();
                                      onOpenTaskEditor(slot.task);
                                    }
                                  }}
                                  className={`flex items-center gap-0.5 overflow-hidden whitespace-nowrap border-y px-2 text-[11px] font-semibold leading-none shadow-[0_1px_2px_rgba(15,23,42,0.04)] ${isLocalDraftTaskId(slot.task.id) ? 'cursor-default' : 'cursor-grab active:cursor-grabbing'} ${draggingTaskId === slot.task.id ? 'opacity-50' : ''} ${slot.roundLeft ? 'ml-0 rounded-l-md border-l' : '-ml-2.5'} ${slot.roundRight ? 'mr-0 rounded-r-md border-r' : '-mr-2.5'} ${slot.task.status === 'done' ? 'line-through' : ''}`}
                                  style={{ height: MONTH_MULTIDAY_BAR_HEIGHT, color: barStyle.color, backgroundColor: barStyle.backgroundColor, borderColor: barStyle.borderColor }}
                                >
                                  {slot.continuesLeft && (
                                    <ChevronLeft size={10} strokeWidth={2.5} className="shrink-0 opacity-70" aria-hidden="true" />
                                  )}
                                  {slot.showTitle ? (
                                    <span className="overflow-hidden text-ellipsis">{formatMonthTaskStartTime(slot.task)} {slot.task.title}</span>
                                  ) : (
                                    <span className="flex-1" aria-hidden="true" />
                                  )}
                                  {slot.continuesRight && (
                                    <ChevronRight size={10} strokeWidth={2.5} className="ml-auto shrink-0 opacity-70" aria-hidden="true" />
                                  )}
                                </div>
                              );
                            })}
                            </div>
                          )}

                          <div className={`mt-2.5 flex min-h-0 flex-1 flex-col gap-1 ${
                            expandedCalendarDays.has(formatDateInputValue(cellDate))
                              ? 'max-h-[260px] overflow-y-auto pr-0.5'
                              : ''
                          }`}>
                            {(meetingsByDateKey.get(formatDateInputValue(cellDate)) || []).filter((meeting) => !isMultiDayCalendarMeeting(meeting)).map((meeting) => (
                              <div
                                key={`meeting-${meeting.id}`}
                                draggable
                                role="button"
                                tabIndex={0}
                                onDragStart={(event) => {
                                  event.stopPropagation();
                                  event.dataTransfer.effectAllowed = 'move';
                                  event.dataTransfer.setData('text/plain', `meeting:${meeting.id}`);
                                  dragDropHandledRef.current = false;
                                  setDraggingTaskId(`meeting:${meeting.id}`);
                                }}
                                onDragEnd={() => {
                                  setDraggingTaskId(null);
                                  setDragTargetDay(null);
                                  dragDropHandledRef.current = false;
                                }}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  onOpenMeetingEditor?.(meeting);
                                }}
                                onKeyDown={(event) => {
                                  if (event.key === 'Enter' || event.key === ' ') {
                                    event.preventDefault();
                                    onOpenMeetingEditor?.(meeting);
                                  }
                                }}
                                className="flex max-w-full items-center gap-1 rounded-md border border-violet-200 bg-violet-50 px-2 py-1 text-left text-[10px] font-semibold text-violet-700 hover:bg-violet-100"
                                title={`${formatMeetingScheduleLabel(meeting)} ${meeting.title}`}
                              >
                                <button type="button" onClick={(event) => { event.stopPropagation(); void onToggleMeetingStatus?.(meeting); }} className={`flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border ${meeting.status === 'completed' ? 'border-emerald-500 bg-emerald-500 text-[8px] text-white' : 'border-violet-300 bg-white text-transparent'}`} aria-label={meeting.status === 'completed' ? `重开会议${meeting.title}` : `完成会议${meeting.title}`}>✓</button>
                                <span className={`min-w-0 flex-1 truncate text-left ${meeting.status === 'completed' ? 'text-gray-400 line-through' : ''}`}>{formatMeetingScheduleLabel(meeting)} {meeting.title}</span>
                              </div>
                            ))}
                            {/* 5/26: 展开时给个 max-h + 内部 scroll, 防止把整行 row 撑高搞乱 month grid. */}
                            {cellChipTasks.slice(0, expandedCalendarDays.has(formatDateInputValue(cellDate)) ? cellChipTasks.length : 4).map((task) => {
                              const timedSegment = buildTaskDayTimedSegment(task, cellDate);
                              const timePrefix = `${formatTaskCardScheduleLabel(task)} `;
                              const isTaskLocalDraft = isLocalDraftTaskId(task.id);
                              // 5/26 ⑯: 跨天任务首尾箭头标识. 看 task 的整段 range 跨不跨天.
                              const taskRange = resolveTaskDateTimeRange(task);
                              const taskStartDay = startOfDayValue(taskRange.startDateTime);
                              const taskEndDay = startOfDayValue(addDays(taskRange.endDateTime, taskRange.endDateTime.getTime() === startOfDayValue(taskRange.endDateTime).getTime() ? -1 : 0));
                              const cellDayValue = startOfDayValue(cellDate);
                              const isMultiDay = taskStartDay.getTime() !== taskEndDay.getTime();
                              const isFirstDay = isMultiDay && cellDayValue.getTime() === taskStartDay.getTime();
                              const isLastDay = isMultiDay && cellDayValue.getTime() === taskEndDay.getTime();
                              const isMiddleDay = isMultiDay && !isFirstDay && !isLastDay;
                              return (
                                <div
                                  key={task.id}
                                  data-no-month-range-drag="true"
                                  draggable={!isTaskLocalDraft}
                                  onMouseDown={(event) => {
                                    event.stopPropagation();
                                  }}
                                  onDragStart={(event) => {
                                    event.stopPropagation();
                                    if (isTaskLocalDraft) {
                                      event.preventDefault();
                                      onCalendarNotice?.('info', LOCAL_DRAFT_NOTICE);
                                      return;
                                    }
                                    event.dataTransfer.effectAllowed = 'move';
                                    event.dataTransfer.setData('text/plain', task.id);
                                    dragDropHandledRef.current = false;
                                    setDraggingTaskId(task.id);
                                  }}
                                  onDragEnd={() => {
                                    if (!dragDropHandledRef.current) {
                                      setDraggingTaskId(null);
                                      setDragTargetDay(null);
                                    }
                                    dragDropHandledRef.current = false;
                                  }}
                                  className={`group relative block max-w-full rounded-md border pl-2 pr-1 py-0.5 text-[11px] font-semibold text-left leading-[1.35] ${isTaskLocalDraft ? 'cursor-default' : 'cursor-grab active:cursor-grabbing'} ${
                                    task.status === 'done' ? 'line-through' : 'shadow-[0_1px_2px_rgba(15,23,42,0.04)]'
                                  } ${draggingTaskId === task.id ? 'opacity-50' : ''} ${
                                    isTaskOverdue(task, today) && task.status !== 'done' ? 'ring-1 ring-rose-400' : ''
                                  } ${
                                    task.priority === 'high' && task.status !== 'done' ? 'border-l-[3px]' : ''
                                  }`}
                                  style={calendarChipStyle(task, clientColorById)}
                                  title={`${timePrefix}${task.title}${taskOrgSummary(task) ? ` · ${taskOrgSummary(task)}` : ''}`}
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    if (isTaskLocalDraft) {
                                      onCalendarNotice?.('info', LOCAL_DRAFT_NOTICE);
                                      return;
                                    }
                                    onOpenTaskEditor(task);
                                  }}
                                >
                                  <button
                                    type="button"
                                    data-no-month-range-drag="true"
                                    className={`absolute left-1.5 top-1/2 z-10 flex h-3.5 w-3.5 -translate-y-1/2 items-center justify-center rounded-[4px] border transition ${
                                      task.status === 'done'
                                        ? 'border-[#CBD5E1] bg-[#CBD5E1] text-white'
                                        : 'border-current bg-white/85 hover:bg-white'
                                    }`}
                                    onMouseDown={(event) => {
                                      event.stopPropagation();
                                    }}
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      if (isTaskLocalDraft) {
                                        onCalendarNotice?.('info', LOCAL_DRAFT_NOTICE);
                                        return;
                                      }
                                      void onToggleTaskStatus(task.id);
                                    }}
                                    title={task.status === 'done' ? '取消完成' : '标记完成'}
                                    aria-label={task.status === 'done' ? `取消完成 ${task.title}` : `完成 ${task.title}`}
                                  >
                                    {task.status === 'done' ? <Check size={10} strokeWidth={3} /> : null}
                                  </button>
                                  <span className="flex items-center gap-0.5 overflow-hidden whitespace-nowrap pl-5 pr-1">
                                    {/* 5/26 ⑯: 跨天任务首尾箭头 — 用户能立刻识别"这个任务是跨多天的, 这是端点之一" */}
                                    {(isLastDay || isMiddleDay) && (
                                      <ChevronLeft size={9} strokeWidth={2.5} className="shrink-0 opacity-70" aria-hidden="true" />
                                    )}
                                    <span className="overflow-hidden whitespace-nowrap text-ellipsis flex-1 min-w-0">{timePrefix}{task.title}</span>
                                    {(isFirstDay || isMiddleDay) && (
                                      <ChevronRight size={9} strokeWidth={2.5} className="shrink-0 opacity-70" aria-hidden="true" />
                                    )}
                                  </span>
                                </div>
                              );
                            })}
                            {overflowCount > 0 && !expandedCalendarDays.has(formatDateInputValue(cellDate)) && (
                              <button
                                type="button"
                                data-no-month-range-drag="true"
                                className="rounded-md bg-[#FAFAFA] px-2 py-1 text-[10.5px] font-medium text-gray-500 text-left ring-1 ring-inset ring-gray-200 hover:bg-gray-100 transition-colors cursor-pointer"
                                onMouseDown={(event) => {
                                  event.stopPropagation();
                                }}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  setExpandedCalendarDays((prev) => { const next = new Set(prev); next.add(formatDateInputValue(cellDate)); return next; });
                                }}
                              >
                                + {overflowCount} 条更多
                              </button>
                            )}
                            {expandedCalendarDays.has(formatDateInputValue(cellDate)) && cellChipTasks.length > 4 && (
                              <button
                                type="button"
                                data-no-month-range-drag="true"
                                className="rounded-md bg-[#FAFAFA] px-2 py-1 text-[10.5px] font-medium text-gray-500 text-left ring-1 ring-inset ring-gray-200 hover:bg-gray-100 transition-colors cursor-pointer"
                                onMouseDown={(event) => {
                                  event.stopPropagation();
                                }}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  setExpandedCalendarDays((prev) => { const next = new Set(prev); next.delete(formatDateInputValue(cellDate)); return next; });
                                }}
                              >
                                收起
                              </button>
                            )}
                            <button
                              type="button"
                              aria-label={`${cellDate.getDate()}日新建任务`}
                              className="group/add min-h-[18px] flex-1 rounded-lg bg-transparent hover:bg-blue-50/50 transition-colors flex items-center justify-center"
                              onMouseDown={(event) => {
                                event.stopPropagation();
                              }}
                              onClick={(event) => {
                                event.stopPropagation();
                                onOpenTaskEditor(undefined, formatDateInputValue(cellDate));
                              }}
                            >
                              <span className="text-[18px] text-blue-300 opacity-0 group-hover/add:opacity-100 transition-opacity font-light">+</span>
                            </button>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          </>
        ) : (
          <div className="border-t border-gray-100">
            <div
              className="sticky z-30 grid grid-cols-[56px_repeat(7,minmax(0,1fr))] border-b border-gray-100 bg-white/95 backdrop-blur"
              style={{ top: `${calendarToolbarHeight}px` }}
            >
              <div />
              {visibleWeekPage.days.map((day) => {
                const isActive = isSameDay(day, selectedDate);
                const isToday = isSameDay(day, today);
                const chinaCalendarMarkers = getChinaCalendarMarkers(day);
                const weekdayShort = ['SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT'][day.getDay()];
                return (
                  <button
                    key={`fixed-${day.toISOString()}`}
                    type="button"
                    className={`relative border-l border-gray-100 px-2 pt-4 pb-3 text-center transition-colors ${
                      isActive ? 'bg-[#5B7BFE]/[0.04]' : 'hover:bg-gray-50/60'
                    }`}
                    onClick={() => handleDaySelect(day)}
                  >
                    <p className={`text-[10px] font-bold uppercase tracking-[0.18em] transition-colors ${
                      isToday ? 'text-[#5B7BFE]' : isActive ? 'text-gray-700' : 'text-gray-400'
                    }`}>
                      {weekdayShort}
                    </p>
                    <div className="mt-2.5 flex items-center justify-center">
                      <span className={`text-[28px] leading-none font-light tracking-tight transition-colors ${
                        isToday ? 'text-[#5B7BFE]' : isActive ? 'text-gray-900' : 'text-gray-700'
                      }`}>
                        {String(day.getDate()).padStart(2, '0')}
                      </span>
                    </div>
                    {chinaCalendarMarkers.length > 0 && (
                      <div className="mt-2 flex flex-wrap items-center justify-center gap-1">
                        {chinaCalendarMarkers.slice(0, 2).map((marker) => (
                          <span
                            key={`fixed-${day.toISOString()}-${marker.kind}-${marker.label}`}
                            className={`rounded-full border px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.06em] leading-none ${calendarMarkerClassName(marker)}`}
                          >
                            {marker.label}
                          </span>
                        ))}
                      </div>
                    )}
                    <span className={`pointer-events-none absolute left-0 right-0 -bottom-px h-[2px] rounded-full transition-colors ${
                      isToday ? 'bg-[#5B7BFE]' : isActive ? 'bg-[#9FB2FF]' : 'bg-transparent'
                    }`} />
                  </button>
                );
              })}
            </div>
            <div
              ref={weekPagerRef}
              className="overflow-x-auto overscroll-x-contain snap-x snap-proximity"
              onScroll={handleWeekPagerScroll}
            >
              <div className="flex min-w-full">
                {weekPages.map((page) => (
                  <div
                    key={page.key}
                    className={`min-w-full snap-center transition-opacity duration-200 ${
                      isWeekPaging
                        ? page === visibleWeekPage
                          ? 'opacity-100'
                          : 'opacity-75'
                        : 'opacity-100'
                    }`}
                    onWheel={handleWeekScrollWheel}
                    // CSS containment：告诉浏览器每页的 layout/paint/style 各自独立。
                    // 横滑时浏览器不需要把 3 页全部重排版/重绘，只重画当前可见的那一页，
                    // 显著减轻 paint 负担。translateZ(0) 把每页推到 GPU 层，进一步降低 CPU。
                    style={{
                      contain: 'layout paint style',
                      transform: 'translateZ(0)',
                      willChange: 'opacity',
                    }}
                  >
                    {/* 未安排时间行 —— 有 dueDate 但没 scheduledStartAt 的任务在这里露面；
                        卡片可拖到下方时间格，复用 handleWeekTimelineTaskDrop 自动分配时间。 */}
                    {page.unscheduledByDay.some((items) => items.length > 0) && (
                      <div className="grid grid-cols-[56px_repeat(7,minmax(0,1fr))] border-b border-gray-100 bg-amber-50/30">
                        <div className="flex items-center justify-end border-r border-gray-100 px-2 py-1.5 text-right text-[10px] font-semibold text-amber-700">
                          未安排
                        </div>
                        {page.days.map((day, dayIndex) => {
                          const items = page.unscheduledByDay[dayIndex] || [];
                          // 5/26 修: 之前不限条数, 4+ 条时 flex-shrink 把高度均匀压扁, 文字看着挤.
                          // 改成最多 3 条 + 第 4 条起聚合 "+N", 点 +N 展开列下方显示剩余的 (复用 expandedAggregateKey).
                          const UNSCHED_MAX = 3;
                          const visibleItems = items.slice(0, UNSCHED_MAX);
                          const hiddenItems = items.slice(UNSCHED_MAX);
                          const unschedAggregateKey = `unsched-${page.key}-${dayIndex}`;
                          const isUnschedExpanded = expandedAggregateKey === unschedAggregateKey;
                          const renderItems = isUnschedExpanded ? items : visibleItems;
                          const renderTaskCard = (task: Task) => {
                            const isTaskLocalDraft = isLocalDraftTaskId(task.id);
                            const isDone = task.status === 'done';
                            return (
                              <div
                                key={`${page.key}-unsched-${day.toISOString()}-${task.id}`}
                                role="button"
                                tabIndex={0}
                                draggable={!isTaskLocalDraft}
                                onDragStart={(event) => {
                                  if (isTaskLocalDraft) {
                                    event.preventDefault();
                                    onCalendarNotice?.('info', LOCAL_DRAFT_NOTICE);
                                    return;
                                  }
                                  event.dataTransfer.effectAllowed = 'move';
                                  event.dataTransfer.setData('text/plain', task.id);
                                  try {
                                    // 5/26 改: 之前用 1×1 transparent ghost, 拖动看不见自己抓的是什么.
                                    // 改成 clone 真 chip 当 ghost — 半透明 + 微旋转 (跟滴答/Google 一致)
                                    const sourceEl = event.currentTarget as HTMLElement;
                                    const ghostEl = sourceEl.cloneNode(true) as HTMLElement;
                                    const rect = sourceEl.getBoundingClientRect();
                                    ghostEl.style.cssText = `position:fixed; top:-9999px; left:-9999px; width:${rect.width}px; opacity:0.85; transform:rotate(-1.5deg); box-shadow:0 8px 16px rgba(15,23,42,0.18); pointer-events:none; z-index:9999;`;
                                    document.body.appendChild(ghostEl);
                                    const offsetX = Math.min(rect.width / 2, 80);
                                    const offsetY = Math.min(rect.height / 2, 12);
                                    event.dataTransfer.setDragImage(ghostEl, offsetX, offsetY);
                                    window.setTimeout(() => {
                                      try { document.body.removeChild(ghostEl); } catch { /* ignore */ }
                                    }, 0);
                                  } catch {
                                    // ignore
                                  }
                                  dragDropHandledRef.current = false;
                                  setDraggingTaskId(task.id);
                                }}
                                onDragEnd={() => {
                                  if (!dragDropHandledRef.current) {
                                    setDraggingTaskId(null);
                                    setDragTargetDay(null);
                                    setDragTargetMinute(null);
                                  }
                                  dragDropHandledRef.current = false;
                                }}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  onOpenTaskEditor(task);
                                }}
                                onKeyDown={(event) => {
                                  if (event.key === 'Enter' || event.key === ' ') {
                                    event.preventDefault();
                                    onOpenTaskEditor(task);
                                  }
                                }}
                                className={`shrink-0 rounded-lg border px-2 py-1 text-[11px] font-semibold leading-4 line-clamp-1 transition ${isTaskLocalDraft ? 'cursor-default opacity-60' : 'cursor-grab active:cursor-grabbing hover:shadow-sm'} ${isDone ? 'border-slate-200 bg-slate-50 text-slate-400 line-through' : 'border-amber-200 bg-white text-amber-900 hover:border-amber-300'} ${draggingTaskId === task.id ? 'opacity-50' : ''}`}
                                title={`未安排时间：${task.title}\n— 拖到下方时间格即可分配时间\n— 点击进入编辑器`}
                                aria-label={`未安排任务 ${task.title}：拖到时间格分配时间，或点击编辑`}
                              >
                                {task.title}
                              </div>
                            );
                          };
                          return (
                            <div
                              key={`${page.key}-unsched-${day.toISOString()}`}
                              className={`flex flex-col gap-1 border-l border-gray-100 px-1.5 py-1.5 min-h-[28px] ${isUnschedExpanded ? 'max-h-[280px] overflow-y-auto' : 'max-h-[88px] overflow-hidden'}`}
                            >
                              {renderItems.map((task) => renderTaskCard(task))}
                              {hiddenItems.length > 0 && (
                                <button
                                  type="button"
                                  data-aggregate-trigger="true"
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    setExpandedAggregateKey((prev) => (prev === unschedAggregateKey ? null : unschedAggregateKey));
                                  }}
                                  className={`shrink-0 rounded-lg border border-dashed px-2 py-1 text-[10px] font-semibold leading-4 transition ${
                                    isUnschedExpanded
                                      ? 'border-amber-400 bg-amber-100 text-amber-800 hover:bg-amber-200'
                                      : 'border-amber-300 bg-amber-50/50 text-amber-700 hover:bg-amber-100 hover:border-amber-400'
                                  }`}
                                  aria-expanded={isUnschedExpanded}
                                  aria-label={isUnschedExpanded ? '收起未安排任务' : `还有 ${hiddenItems.length} 条未安排任务,点开查看`}
                                >
                                  {isUnschedExpanded ? '收起' : `+${hiddenItems.length} 条`}
                                </button>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}

                    <div
                      className="max-h-[860px] overflow-y-auto"
                      data-week-scroll="true"
                      onScroll={handleWeekVerticalScroll}
                    >
                      <div className="relative grid grid-cols-[60px_repeat(7,minmax(0,1fr))] bg-white">
                        {(draggedTask || draggedMeeting) && dragTargetMinute !== null && (
                          <div
                            className="pointer-events-none absolute left-0 right-0 z-[75] flex items-center"
                            style={{ top: `${(dragTargetMinute / DAY_TIMELINE_SLOT_MINUTES) * DAY_TIMELINE_SLOT_HEIGHT}px` }}
                            aria-hidden="true"
                          >
                            <span className="w-[60px] -translate-y-1/2 pr-2 text-right text-[10px] font-bold tabular-nums text-[#5B7BFE]">
                              {formatMinuteOfDay(dragTargetMinute)}
                            </span>
                            <span className="flex-1 border-t-2 border-dashed border-[#5B7BFE]" />
                          </div>
                        )}
                        <div className="relative">
                          {/* 时间标签:垂直中心对齐 hour line,而不是贴在 line 上。
                              用绝对定位让数字的水平中线刚好落在 hour line 上,符合 Google Calendar / TickTick 习惯。 */}
                          {hourLineMinutes.map((minute) => (
                            <span
                              key={`${page.key}-time-label-${minute}`}
                              className="pointer-events-none absolute right-3 -translate-y-1/2 text-[10px] font-medium tracking-tight tabular-nums text-gray-400 select-none"
                              style={{ top: `${(minute / DAY_TIMELINE_SLOT_MINUTES) * DAY_TIMELINE_SLOT_HEIGHT}px` }}
                            >
                              {formatMinuteOfDay(minute)}
                            </span>
                          ))}
                          {/* 时间列右侧 hairline */}
                          <div className="absolute inset-y-0 right-0 w-px bg-gray-100" />
                          {/* 占位高度,与右侧时间格保持一致 */}
                          <div style={{ height: `${timelineSlotMinutes.length * DAY_TIMELINE_SLOT_HEIGHT}px` }} />
                        </div>
                        {page.days.map((day, dayIndex) => (
                          <div
                            key={`${page.key}-column-${day.toISOString()}`}
                            className="relative border-r last:border-r-0 border-gray-100"
                            style={{ height: `${timelineSlotMinutes.length * DAY_TIMELINE_SLOT_HEIGHT}px` }}
                            data-week-day-key={day.getTime()}
                            onMouseDown={(event) => {
                              // 5/26 接通: 行业标准的"拖选时段"手势.
                              // mouseDown 启动 selection, 内部用 hasMoved threshold 区分单击/拖选,
                              // 单击交给 slot 的 onClick 走默认 60min, 真拖选 (>4px) 才创建自定义 duration.
                              handleStartWeekCreateSelection(day, event.currentTarget as HTMLDivElement, event);
                            }}
                          >
                            {/* 5/26: 拖选高亮预览框 — 行业标准 (Google/Outlook), 蓝色半透明矩形 + 上下浮动时间标签 */}
                            {weekCreateSelection && weekCreateSelection.dayKey === day.getTime() && (() => {
                              const sel = weekCreateSelection;
                              const top = (sel.startMinute / DAY_TIMELINE_SLOT_MINUTES) * DAY_TIMELINE_SLOT_HEIGHT;
                              const height = Math.max(
                                DAY_TIMELINE_SLOT_HEIGHT,
                                ((sel.endMinute - sel.startMinute) / DAY_TIMELINE_SLOT_MINUTES) * DAY_TIMELINE_SLOT_HEIGHT,
                              );
                              const startLabel = formatMinuteOfDay(sel.startMinute);
                              const endLabel = formatMinuteOfDay(sel.endMinute);
                              return (
                                <div
                                  className="pointer-events-none absolute inset-x-1 z-[65] rounded-md border-2 border-[#5B7BFE] bg-[#5B7BFE]/15"
                                  style={{ top: `${top}px`, height: `${height}px` }}
                                  aria-hidden="true"
                                >
                                  <span className="absolute -top-[18px] left-1 inline-block rounded bg-[#5B7BFE] px-1.5 py-[1px] text-[10px] font-semibold text-white tabular-nums leading-none">
                                    {startLabel}
                                  </span>
                                  <span className="absolute -bottom-[18px] right-1 inline-block rounded bg-[#5B7BFE] px-1.5 py-[1px] text-[10px] font-semibold text-white tabular-nums leading-none">
                                    {endLabel}
                                  </span>
                                </div>
                              );
                            })()}
                            {hourLineMinutes.map((minute) => (
                              <div
                                key={`${page.key}-hour-line-${day.toISOString()}-${minute}`}
                                className="pointer-events-none absolute left-0 right-0 border-t border-gray-100"
                                style={{ top: `${(minute / DAY_TIMELINE_SLOT_MINUTES) * DAY_TIMELINE_SLOT_HEIGHT}px` }}
                              />
                            ))}
                            {/* Now indicator:只在今天那一列显示一条 rose 水平线 + 左侧小圆点,实时跟随当前分钟 */}
                            {isSameDay(day, today) && (
                              <div
                                className="pointer-events-none absolute left-0 right-0 z-[60]"
                                style={{ top: `${(currentMinuteOfDay / DAY_TIMELINE_SLOT_MINUTES) * DAY_TIMELINE_SLOT_HEIGHT}px` }}
                                aria-hidden="true"
                              >
                                <span className="absolute -left-[5px] top-1/2 -translate-y-1/2 inline-block h-[10px] w-[10px] rounded-full bg-rose-500 ring-2 ring-white" />
                                <div className="absolute left-0 right-0 top-1/2 -translate-y-1/2 h-[1.5px] bg-rose-500" />
                              </div>
                            )}
                            {timelineSlotMinutes.map((minute) => (
                              <div
                                key={`${page.key}-${day.toISOString()}-${minute}`}
                                className={`group/slot relative cursor-pointer transition-colors ${dragTargetDay === day.getTime() && dragTargetMinute === minute ? 'bg-blue-50/70' : 'bg-transparent hover:bg-blue-50/40'}`}
                                style={{ height: `${DAY_TIMELINE_SLOT_HEIGHT}px` }}
                                onClick={() => {
                                  handleCreateTaskFromWeekSlot(day, minute);
                                }}
                                onDragOver={(event) => {
                                  const draggedTaskId = resolveDraggedTaskId(event);
                                  if (!draggedTaskId) return;
                                  if (isLocalDraftTaskId(draggedTaskId)) return;
                                  event.preventDefault();
                                  if (dragTargetDay !== day.getTime()) setDragTargetDay(day.getTime());
                                  if (dragTargetMinute !== minute) setDragTargetMinute(minute);
                                }}
                                onDragLeave={() => {
                                  if (dragTargetDay === day.getTime() && dragTargetMinute === minute) {
                                    setDragTargetMinute(null);
                                  }
                                }}
                                onDrop={(event) => {
                                  const draggedTaskId = resolveDraggedTaskId(event);
                                  if (!draggedTaskId) return;
                                  event.preventDefault();
                                  const droppedMeeting = draggedTaskId.startsWith('meeting:')
                                    ? meetings.find((meeting) => meeting.id === draggedTaskId.slice('meeting:'.length))
                                    : null;
                                  const droppedTask = droppedMeeting
                                    ? null
                                    : page.tasks.find((task) => task.id === draggedTaskId) || visibleTasks.find((task) => task.id === draggedTaskId);
                                  if (!droppedMeeting && !droppedTask) {
                                    setDragTargetMinute(null);
                                    setDragTargetDay(null);
                                    setDraggingTaskId(null);
                                    return;
                                  }
                                  // 标记刚 drop，下面紧跟的 click 事件被 handleCreateTaskFromWeekSlot 过滤掉。
                                  justDroppedAtRef.current = Date.now();
                                  // 像素吸附：cursor 落在 slot 底部 ≤3px 时归到下一个 slot，
                                  // 防止用户对准整点横线（slot 边界）放手却被吃成上一格的 8:45。
                                  const rect = event.currentTarget.getBoundingClientRect();
                                  const offsetY = event.clientY - rect.top;
                                  const snappedMinute = offsetY > rect.height - 3 && minute + DAY_TIMELINE_SLOT_MINUTES < DAY_MINUTES
                                    ? minute + DAY_TIMELINE_SLOT_MINUTES
                                    : minute;
                                  if (droppedMeeting) {
                                    void handleWeekTimelineMeetingDrop(droppedMeeting, day, snappedMinute);
                                  } else if (droppedTask) {
                                    void handleWeekTimelineTaskDrop(droppedTask, day, snappedMinute);
                                  }
                                }}
                                title={`${formatDateInputValue(day)} ${formatMinuteOfDay(minute)} 新建任务`}
                              >
                                <span className="pointer-events-none absolute inset-x-1 top-1/2 flex -translate-y-1/2 items-center justify-center opacity-0 transition-opacity group-hover/slot:opacity-100">
                                  <span className="flex h-4 w-4 items-center justify-center rounded-full bg-white/95 text-[#5B7BFE] shadow-sm ring-1 ring-[#5B7BFE]/15">
                                    <Plus size={11} strokeWidth={2.5} />
                                  </span>
                                </span>
                              </div>
                            ))}
                            {(draggedTask || draggedMeeting) && dragTargetDay === day.getTime() && dragTargetMinute !== null && (
                              <div
                                className="pointer-events-none absolute left-1.5 right-1.5 z-[40] rounded-md border-2 border-dashed border-[#5B7BFE] bg-[#5B7BFE]/[0.06]"
                                style={{
                                  top: `${(dragTargetMinute / DAY_TIMELINE_SLOT_MINUTES) * DAY_TIMELINE_SLOT_HEIGHT + 2}px`,
                                  minHeight: `${Math.max(40, (draggedDurationMinutes / DAY_TIMELINE_SLOT_MINUTES) * DAY_TIMELINE_SLOT_HEIGHT - 4)}px`,
                                  boxShadow: 'inset 3px 0 0 0 #5B7BFE',
                                }}
                              >
                                <div className="flex h-full items-start justify-between gap-2 px-2.5 py-1.5 text-[#5B7BFE]">
                                  <span className="min-w-0 flex-1 text-[12px] font-semibold leading-[1.35] line-clamp-2">{draggedScheduleTitle}</span>
                                  <span className="shrink-0 text-[9.5px] font-semibold tracking-[0.02em] opacity-75">{`${formatMinuteOfDay(dragTargetMinute)}-${formatMinuteOfDay(Math.min(dragTargetMinute + draggedDurationMinutes, 24 * 60))}`}</span>
                                </div>
                              </div>
                            )}
                            {(weekMeetingItemsByDay.get(dayIndex) || []).map((item) => {
                              const top = (item.startMinute / DAY_TIMELINE_SLOT_MINUTES) * DAY_TIMELINE_SLOT_HEIGHT;
                              const height = Math.max(40, ((item.endMinute - item.startMinute) / DAY_TIMELINE_SLOT_MINUTES) * DAY_TIMELINE_SLOT_HEIGHT - 4);
                              const width = `calc((100% - 12px) / ${Math.max(1, item.laneCount)})`;
                              const left = `calc(6px + (${item.lane} * (100% - 12px) / ${Math.max(1, item.laneCount)}))`;
                              return (
                                <button
                                  key={`week-meeting-${item.meeting.id}-${dayIndex}`}
                                  type="button"
                                  draggable
                                  onMouseDown={(event) => event.stopPropagation()}
                                  onDragStart={(event) => {
                                    event.stopPropagation();
                                    const meetingDragId = `meeting:${item.meeting.id}`;
                                    event.dataTransfer.effectAllowed = 'move';
                                    event.dataTransfer.setData('text/plain', meetingDragId);
                                    const dragGhost = event.currentTarget.cloneNode(true) as HTMLElement;
                                    dragGhost.style.position = 'fixed';
                                    dragGhost.style.left = '-9999px';
                                    dragGhost.style.top = '-9999px';
                                    dragGhost.style.width = `${Math.max(170, event.currentTarget.offsetWidth)}px`;
                                    dragGhost.style.height = `${Math.max(44, event.currentTarget.offsetHeight)}px`;
                                    dragGhost.style.opacity = '0.96';
                                    dragGhost.style.pointerEvents = 'none';
                                    document.body.appendChild(dragGhost);
                                    event.dataTransfer.setDragImage(dragGhost, 18, 16);
                                    requestAnimationFrame(() => dragGhost.remove());
                                    setDraggingTaskId(meetingDragId);
                                    setDragTargetDay(day.getTime());
                                    setDragTargetMinute(item.startMinute);
                                  }}
                                  onDragEnd={() => {
                                    setDraggingTaskId(null);
                                    setDragTargetDay(null);
                                    setDragTargetMinute(null);
                                  }}
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    onOpenMeetingEditor?.(item.meeting);
                                  }}
                                  className={`absolute overflow-hidden rounded-md border border-violet-200 bg-violet-50 px-2 py-1.5 text-left text-violet-700 shadow-[inset_3px_0_0_0_#8B5CF6] hover:bg-violet-100 ${item.meeting.status === 'completed' ? 'line-through opacity-70' : ''}`}
                                  style={{ top: `${top + 2}px`, height: `${height}px`, left, width, zIndex: 20 }}
                                  title={`客户会议 · ${item.meeting.title}`}
                                >
                                  <div className="text-[9.5px] font-semibold opacity-75">{formatMinuteOfDay(item.startMinute)}-{formatMinuteOfDay(item.endMinute)}</div>
                                  <div className="mt-0.5 line-clamp-2 text-[11px] font-semibold leading-[1.35]">客户会议 · {item.meeting.title}</div>
                                </button>
                              );
                            })}
                            {(weekDisplayItemsByDay.get(dayIndex) || []).map((displayItem) => {
                              const horizontalInset = 6;
                              const usableWidthExpr = `(100% - ${horizontalInset * 2}px)`;
                              // 重叠 ≤ 2:均分宽度;重叠 ≥ 3:indent 风格(每个 65%+ 宽,横向偏移 18%)。
                              // indent 让所有任务标题前 65% 字可见,符合 TickTick/Linear 习惯。
                              const useIndent = displayItem.columnCount > WEEK_OVERLAP_INDENT_THRESHOLD;
                              const width = useIndent
                                ? `calc(${usableWidthExpr} * ${(1 - WEEK_OVERLAP_INDENT_RATIO * (displayItem.columnCount - 1)).toFixed(4)})`
                                : `calc(${usableWidthExpr} / ${displayItem.columnCount})`;
                              const left = useIndent
                                ? `calc(${horizontalInset}px + ${usableWidthExpr} * ${(WEEK_OVERLAP_INDENT_RATIO * displayItem.column).toFixed(4)})`
                                : `calc(${horizontalInset}px + (${displayItem.column} * ${usableWidthExpr} / ${displayItem.columnCount}))`;
                              const overlapZIndex = useIndent ? displayItem.column + 1 : 1;

                              if (displayItem.kind === 'aggregate') {
                                const top = (displayItem.startMinute / DAY_TIMELINE_SLOT_MINUTES) * DAY_TIMELINE_SLOT_HEIGHT;
                                const aggregateTitle = displayItem.summary
                                  ? `还有 ${displayItem.hiddenItems.length} 条重叠任务：${displayItem.summary}`
                                  : `还有 ${displayItem.hiddenItems.length} 条重叠任务`;
                                // 聚合 chip:不占大块面积,变成贴在最后一个任务卡右上角的 22×22 圆形 +N。
                                // 5/26 改: 点 +N 不再只 toast, 改为弹 popover 列出隐藏任务, 每个可点开 editor
                                const isExpanded = expandedAggregateKey === displayItem.key;
                                return (
                                  <React.Fragment key={displayItem.key}>
                                    <button
                                      type="button"
                                      data-aggregate-trigger="true"
                                      className={`absolute inline-flex items-center justify-center rounded-full border text-[10px] font-bold shadow-[0_2px_6px_rgba(91,123,254,0.15)] transition-colors duration-150 ${
                                        isExpanded
                                          ? 'border-[#5B7BFE] bg-[#5B7BFE] text-white'
                                          : 'border-[#5B7BFE]/30 bg-white text-[#5B7BFE] hover:bg-[#5B7BFE] hover:text-white hover:border-[#5B7BFE]'
                                      }`}
                                      style={{
                                        top: `${top + 4}px`,
                                        left: `calc(${left} + ${width} - 26px)`,
                                        width: 22,
                                        height: 22,
                                        zIndex: isExpanded ? 70 : 51,
                                      }}
                                      onMouseDown={(event) => {
                                        event.stopPropagation();
                                      }}
                                      onClick={(event) => {
                                        event.stopPropagation();
                                        setExpandedAggregateKey((prev) => (prev === displayItem.key ? null : displayItem.key));
                                      }}
                                      title={aggregateTitle}
                                      aria-label={aggregateTitle}
                                      aria-expanded={isExpanded}
                                    >
                                      +{displayItem.hiddenItems.length}
                                    </button>
                                    {isExpanded && (
                                      <div
                                        data-aggregate-popover="true"
                                        className="absolute rounded-md border border-gray-200 bg-white shadow-lg overflow-hidden"
                                        style={{
                                          top: `${top + 30}px`,
                                          left: `calc(${left} + ${width} - 220px)`,
                                          width: 216,
                                          zIndex: 80,
                                          maxHeight: 240,
                                          overflowY: 'auto',
                                        }}
                                        onMouseDown={(event) => event.stopPropagation()}
                                      >
                                        <div className="px-2 py-1.5 border-b border-gray-100 bg-gray-50 text-[10px] text-gray-500 font-medium">
                                          重叠 {displayItem.hiddenItems.length + 1} 条任务
                                        </div>
                                        {displayItem.hiddenItems.map((hiddenItem) => (
                                          <button
                                            key={hiddenItem.task.id}
                                            type="button"
                                            className="w-full text-left px-2 py-1.5 hover:bg-[#5B7BFE]/8 border-b border-gray-50 last:border-0"
                                            onClick={(event) => {
                                              event.stopPropagation();
                                              setExpandedAggregateKey(null);
                                              onOpenTaskEditor(hiddenItem.task);
                                            }}
                                          >
                                            <div className="text-[10px] text-gray-400 tabular-nums">{hiddenItem.timeLabel}</div>
                                            <div className={`text-[12px] leading-[1.3] line-clamp-2 ${hiddenItem.task.status === 'done' ? 'text-gray-400 line-through' : 'text-gray-700 font-medium'}`}>
                                              {hiddenItem.task.title}
                                            </div>
                                          </button>
                                        ))}
                                      </div>
                                    )}
                                  </React.Fragment>
                                );
                              }

                              const { task, startMinute, durationMinutes } = displayItem.taskItem;
                              const isResizingThis = resizingTaskId === task.id;
                              const effectiveDuration = isResizingThis && resizePreviewMinutes ? resizePreviewMinutes : durationMinutes;
                              // 5/26 ⑨: 顶部 resize 改 startMinute, effectiveStart 用 preview
                              const effectiveStart = isResizingThis && resizePreviewStartMinute !== null ? resizePreviewStartMinute : startMinute;
                              const effectiveEndMinute = Math.min(effectiveStart + effectiveDuration, 24 * 60);
                              const effectiveTimeLabel = `${formatMinuteOfDay(effectiveStart)}-${formatMinuteOfDay(effectiveEndMinute)}`;
                              const top = (effectiveStart / DAY_TIMELINE_SLOT_MINUTES) * DAY_TIMELINE_SLOT_HEIGHT;
                              const height = Math.max(40, ((effectiveEndMinute - effectiveStart) / DAY_TIMELINE_SLOT_MINUTES) * DAY_TIMELINE_SLOT_HEIGHT - 4);
                              const chipStyle = calendarChipStyle(task, clientColorById);
                              const isResizing = resizingTaskId === task.id;
                              const isTaskLocalDraft = isLocalDraftTaskId(task.id);
                              const isCrossDayTask = isMultiDayCalendarTask(task);
                              return (
                                <div
                                  key={task.id}
                                  role="button"
                                  tabIndex={0}
                                  draggable={!isTaskLocalDraft && !isResizing}
                                  onDragStart={(event) => {
                                    event.stopPropagation();
                                    // 拒绝在 resize 进行中的 drag 启动 —— 否则 resize handle 的 mousedown
                                    // 会被浏览器的 HTML5 drag-and-drop 抢走,resize 失败。
                                    if (resizeDraftRef.current?.taskId === task.id) {
                                      event.preventDefault();
                                      return;
                                    }
                                    if (isTaskLocalDraft) {
                                      event.preventDefault();
                                      onCalendarNotice?.('info', LOCAL_DRAFT_NOTICE);
                                      return;
                                    }
                                    event.dataTransfer.effectAllowed = 'move';
                                    event.dataTransfer.setData('text/plain', task.id);
                                    // 5/26 改: clone 真 chip 当 ghost, 跟月视图一致
                                    try {
                                      const sourceEl = event.currentTarget as HTMLElement;
                                      const ghostEl = sourceEl.cloneNode(true) as HTMLElement;
                                      const rect = sourceEl.getBoundingClientRect();
                                      ghostEl.style.cssText = `position:fixed; top:-9999px; left:-9999px; width:${rect.width}px; opacity:0.85; transform:rotate(-1.5deg); box-shadow:0 8px 16px rgba(15,23,42,0.18); pointer-events:none; z-index:9999;`;
                                      document.body.appendChild(ghostEl);
                                      const offsetX = Math.min(rect.width / 2, 80);
                                      const offsetY = Math.min(rect.height / 2, 12);
                                      event.dataTransfer.setDragImage(ghostEl, offsetX, offsetY);
                                      window.setTimeout(() => {
                                        try { document.body.removeChild(ghostEl); } catch { /* ignore */ }
                                      }, 0);
                                    } catch {
                                      // 某些浏览器/Electron 版本不支持 setDragImage,优雅降级
                                    }
                                    dragDropHandledRef.current = false;
                                    setDraggingTaskId(task.id);
                                  }}
                                  onDragEnd={() => {
                                    if (!dragDropHandledRef.current) {
                                      setDraggingTaskId(null);
                                      setDragTargetDay(null);
                                      setDragTargetMinute(null);
                                    }
                                    dragDropHandledRef.current = false;
                                  }}
                                  className={`group absolute cursor-pointer rounded-md px-2.5 py-1.5 pb-2.5 text-left overflow-hidden transition-[box-shadow,opacity,background-color,border-color] duration-150 ${isTaskLocalDraft ? 'cursor-default' : ''} ${isResizing ? 'cursor-ns-resize ring-2 ring-[#5B7BFE]/30' : draggingTaskId === task.id ? 'opacity-30' : ''} hover:shadow-[0_2px_8px_rgba(15,23,42,0.06)]`}
                                  style={{
                                    top: `${top + 2}px`,
                                    left,
                                    width,
                                    minHeight: `${height}px`,
                                    color: task.status === 'done' ? '#94A3B8' : chipStyle.color,
                                    backgroundColor: task.status === 'done' ? '#F8FAFC' : `${chipStyle.color}0F`,
                                    boxShadow: `inset 3px 0 0 0 ${task.status === 'done' ? '#CBD5E1' : chipStyle.color}`,
                                    zIndex: draggingTaskId === task.id || isResizing ? 50 : overlapZIndex,
                                  }}
                                  onMouseDown={(event) => {
                                    event.stopPropagation();
                                  }}
                                  onClick={(event) => {
                                    // 5/26 修: 之前 onClick 调 handleWeekTaskSelect (空函数),
                                    // 点任务卡身体没反应; 月视图直接点卡能开, 周视图退化.
                                    // 改为点身体直接打开 task editor (跟月视图一致)
                                    event.preventDefault();
                                    event.stopPropagation();
                                    if (draggingTaskId === task.id || resizingTaskId === task.id) return;
                                    onOpenTaskEditor(task);
                                  }}
                                  title={`${effectiveTimeLabel} ${task.title}`}
                                  aria-label={`${effectiveTimeLabel} ${task.title}`}
                                >
                                  {/* 左侧 3px 实色锚线 + 浅色 wash 是参考 dashboard 设计的标志组合。
                                      时间小字号置上,标题 medium 字重突出,符合 Linear/Linear-style 设计。 */}
                                  <div className="pl-1 pr-1">
                                    <div className="text-[9.5px] font-semibold tracking-[0.02em] opacity-75">{effectiveTimeLabel}</div>
                                    <div className={`mt-0.5 text-[12px] leading-[1.35] line-clamp-2 break-words ${task.status === 'done' ? 'font-medium line-through' : 'font-semibold'}`}>{task.title}</div>
                                  </div>
                                  <div className="absolute right-2 top-2 flex items-center gap-1 opacity-0 transition group-hover:opacity-100">
                                    <button
                                      type="button"
                                      className={`flex h-4 w-4 items-center justify-center rounded-[4px] border bg-white/90 hover:bg-white ${task.status === 'done' ? 'border-[#CBD5E1] text-[#64748B]' : 'border-current'}`}
                                      onMouseDown={(event) => {
                                        event.stopPropagation();
                                      }}
                                      onClick={(event) => {
                                        event.stopPropagation();
                                        if (isTaskLocalDraft) {
                                          onCalendarNotice?.('info', LOCAL_DRAFT_NOTICE);
                                          return;
                                        }
                                        void onToggleTaskStatus(task.id);
                                      }}
                                      title={task.status === 'done' ? '取消完成' : '标记完成'}
                                      aria-label={task.status === 'done' ? `取消完成 ${task.title}` : `完成 ${task.title}`}
                                    >
                                      <Check size={9} strokeWidth={3} />
                                    </button>
                                  </div>
                                  {/* 5/26 ⑨: 顶部 resize 区 (新增) — 拖顶部改 startMinute, end 不变 */}
                                  <div
                                    draggable={false}
                                    className="hidden"
                                    onMouseEnter={() => {
                                      if (isTaskLocalDraft) return;
                                      setResizeHoverTaskId(task.id);
                                    }}
                                    onMouseLeave={() => {
                                      if (!resizeDraftRef.current) setResizeHoverTaskId(null);
                                    }}
                                    onMouseDown={(event) => {
                                      if (isTaskLocalDraft) {
                                        event.preventDefault();
                                        event.stopPropagation();
                                        onCalendarNotice?.('info', LOCAL_DRAFT_NOTICE);
                                        return;
                                      }
                                      handleStartWeekTaskResize(task.id, startMinute, durationMinutes, event, {
                                        mode: 'top',
                                        dayDate: displayItem.taskItem.dayDate,
                                      });
                                    }}
                                    onDragStart={(event) => {
                                      event.preventDefault();
                                      event.stopPropagation();
                                    }}
                                    onClick={(event) => {
                                      event.preventDefault();
                                      event.stopPropagation();
                                    }}
                                    title={isTaskLocalDraft ? LOCAL_DRAFT_NOTICE : '拖动调整开始时间'}
                                  >
                                    <span
                                      className={`pointer-events-none absolute inset-x-2 top-[2px] h-[2px] rounded-full transition-opacity ${
                                        isResizing ? 'opacity-100' : 'opacity-0 group-hover:opacity-60 group-hover/resizetop:opacity-100'
                                      }`}
                                      style={{ backgroundColor: chipStyle.color }}
                                    />
                                  </div>
                                  {/* 底部 resize 区:8px 高的透明 hover 区(够大易点),内嵌 2px 实色细线(只在 hover/resize 时可见)。
                                      mouseEnter 时把整张卡的 draggable 提前禁掉,这样 mousedown 不会被 HTML5 drag 抢走。 */}
                                  {!isCrossDayTask && <div
                                    draggable={false}
                                    className={`absolute inset-x-0 bottom-0 h-[8px] ${isTaskLocalDraft ? 'cursor-default' : 'cursor-ns-resize'} group/resize`}
                                    onMouseEnter={() => {
                                      if (isTaskLocalDraft) return;
                                      setResizeHoverTaskId(task.id);
                                    }}
                                    onMouseLeave={() => {
                                      if (!resizeDraftRef.current) {
                                        setResizeHoverTaskId(null);
                                      }
                                    }}
                                    onMouseDown={(event) => {
                                      if (isTaskLocalDraft) {
                                        event.preventDefault();
                                        event.stopPropagation();
                                        onCalendarNotice?.('info', LOCAL_DRAFT_NOTICE);
                                        return;
                                      }
                                      handleStartWeekTaskResize(task.id, startMinute, durationMinutes, event);
                                    }}
                                    onDragStart={(event) => {
                                      event.preventDefault();
                                      event.stopPropagation();
                                    }}
                                    onClick={(event) => {
                                      event.preventDefault();
                                      event.stopPropagation();
                                    }}
                                    title={isTaskLocalDraft ? LOCAL_DRAFT_NOTICE : '拖动调整时长'}
                                  >
                                    <span
                                      className={`pointer-events-none absolute inset-x-2 bottom-[2px] h-[2px] rounded-full transition-opacity ${
                                        isResizing ? 'opacity-100' : 'opacity-0 group-hover:opacity-60 group-hover/resize:opacity-100'
                                      }`}
                                      style={{ backgroundColor: chipStyle.color }}
                                    />
                                  </div>}
                                </div>
                              );
                            })}
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

    </div>
  );
}
