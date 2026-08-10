import type { Task } from '../../../shared/types';
import type { MiniDay, MiniEvent, MiniTask } from './MiniPanel';

// 把主软件现有的 tasks(getTaskBoard 加载、存在 App state)纯客户端派生成迷你面板数据。
// 不发任何新请求、不接新端点——迷你面板只是现有任务轨道的一个视图。

const isoDate = (s?: string | null): string => {
  if (!s || s.length < 10) return '';
  const d = s.slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(d) ? d : '';
};

const hhmm = (s?: string | null): string => {
  if (!s) return '';
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? '' : `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};

const isDone = (t: Task): boolean => t.status === 'done';
// 任务归属日期:有定时用定时,否则用截止日
const taskDate = (t: Task): string => isoDate(t.scheduledStartAt) || isoDate(t.dueDate);

function toMiniTask(t: Task, todayIso: string): MiniTask {
  const due = isoDate(t.dueDate);
  const done = isDone(t);
  let dueLabel = '';
  let overdue = false;
  if (due && due === todayIso) {
    dueLabel = '今天';
  } else if (due && due < todayIso && !done) {
    dueLabel = '逾期';
    overdue = true;
  } else if (due) {
    const parts = due.split('-');
    dueLabel = `${Number(parts[1])}/${Number(parts[2])}`;
  }
  return {
    id: t.id,
    title: t.title,
    done,
    dueLabel: dueLabel || undefined,
    clientName: t.clientName || undefined,
    overdue,
  };
}

export interface MiniData {
  today: MiniDay;
  markedDates: string[];
  getDay: (iso: string) => MiniDay;
}

export function buildMiniData(tasks: Task[], todayIso: string): MiniData {
  const byDate = new Map<string, Task[]>();
  const marked = new Set<string>();
  for (const t of tasks) {
    const d = taskDate(t);
    if (!d) continue;
    marked.add(d);
    const arr = byDate.get(d);
    if (arr) arr.push(t);
    else byDate.set(d, [t]);
  }

  const buildDay = (iso: string): MiniDay => {
    const dayTasks = byDate.get(iso) ?? [];
    const events: MiniEvent[] = dayTasks
      .filter((t) => isoDate(t.scheduledStartAt) === iso && hhmm(t.scheduledStartAt))
      .map((t) => ({ id: t.id, time: hhmm(t.scheduledStartAt), title: t.title, clientName: t.clientName || undefined }))
      .sort((a, b) => a.time.localeCompare(b.time));
    const eventIds = new Set(events.map((e) => e.id));
    const todos: MiniTask[] = dayTasks.filter((t) => !eventIds.has(t.id)).map((t) => toMiniTask(t, todayIso));
    return { date: iso, events, tasks: todos };
  };

  const todayDay = buildDay(todayIso);
  // 今天视图并入"逾期未完成"(去重)
  const seen = new Set(todayDay.tasks.map((x) => x.id));
  const overdue = tasks
    .filter((t) => !isDone(t) && taskDate(t) && taskDate(t) < todayIso && !seen.has(t.id))
    .map((t) => toMiniTask(t, todayIso));
  const today: MiniDay = { date: todayIso, events: todayDay.events, tasks: [...overdue, ...todayDay.tasks] };

  return {
    today,
    markedDates: [...marked],
    getDay: (iso) => (iso === todayIso ? today : buildDay(iso)),
  };
}
