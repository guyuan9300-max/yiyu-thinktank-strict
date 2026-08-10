export type PlanningCycleType = 'month' | 'quarter' | 'year' | 'week' | 'custom';

const DAY_MS = 86_400_000;

function localDate(year: number, monthIndex: number, day: number) {
  return new Date(year, monthIndex, day, 12, 0, 0, 0);
}

function dateInputValue(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

export function planningCycleTypeForKey(value: string | null | undefined): PlanningCycleType {
  const key = String(value || '').trim();
  if (/^\d{4}-W\d{2}$/i.test(key)) return 'week';
  if (/^\d{4}-\d{2}$/.test(key)) return 'month';
  if (/^\d{4}-Q[1-4]$/i.test(key)) return 'quarter';
  if (/^\d{4}$/.test(key)) return 'year';
  return 'custom';
}

export function isoWeekKeyForDate(input: Date): string {
  const utcDate = new Date(Date.UTC(input.getFullYear(), input.getMonth(), input.getDate()));
  const day = utcDate.getUTCDay() || 7;
  utcDate.setUTCDate(utcDate.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(utcDate.getUTCFullYear(), 0, 1));
  const week = Math.ceil((((utcDate.getTime() - yearStart.getTime()) / DAY_MS) + 1) / 7);
  return `${utcDate.getUTCFullYear()}-W${String(week).padStart(2, '0')}`;
}

export function isoWeekDateRange(key: string): { start: Date; end: Date } | null {
  const match = key.trim().match(/^(\d{4})-W(\d{2})$/i);
  if (!match) return null;
  const year = Number(match[1]);
  const week = Number(match[2]);
  if (week < 1 || week > 53) return null;
  const januaryFourth = localDate(year, 0, 4);
  const januaryFourthDay = januaryFourth.getDay() || 7;
  const firstMonday = new Date(januaryFourth);
  firstMonday.setDate(januaryFourth.getDate() - januaryFourthDay + 1);
  const start = new Date(firstMonday);
  start.setDate(firstMonday.getDate() + (week - 1) * 7);
  const end = new Date(start);
  end.setDate(start.getDate() + 6);
  return { start, end };
}

export function defaultPlanningPeriodKey(type: PlanningCycleType, now = new Date()): string {
  const year = now.getFullYear();
  const month = now.getMonth() + 1;
  if (type === 'week') return isoWeekKeyForDate(now);
  if (type === 'month') return `${year}-${String(month).padStart(2, '0')}`;
  if (type === 'quarter') return `${year}-Q${Math.ceil(month / 3)}`;
  if (type === 'year') return String(year);
  return '';
}

export function weekStartInputValue(key: string): string {
  const range = isoWeekDateRange(key);
  return range ? dateInputValue(range.start) : '';
}

export function formatPlanningPeriodLabel(value: string | null | undefined): string {
  const key = String(value || '').trim();
  const week = isoWeekDateRange(key);
  if (week) {
    return `${week.start.getMonth() + 1}月${week.start.getDate()}日—${week.end.getMonth() + 1}月${week.end.getDate()}日`;
  }
  const month = key.match(/^(\d{4})-(\d{2})$/);
  if (month) return `${month[1]}年${Number(month[2])}月`;
  const quarter = key.match(/^(\d{4})-Q([1-4])$/i);
  if (quarter) return `${quarter[1]}年第${quarter[2]}季度`;
  if (/^\d{4}$/.test(key)) return `${key}年`;
  return key || '未选择周期';
}

export function planningPeriodSortKey(value: string | null | undefined): string {
  const key = String(value || '').trim();
  const week = isoWeekDateRange(key);
  if (week) return dateInputValue(week.start);
  const month = key.match(/^(\d{4})-(\d{2})$/);
  if (month) return `${month[1]}-${month[2]}-01`;
  const quarter = key.match(/^(\d{4})-Q([1-4])$/i);
  if (quarter) return `${quarter[1]}-${String((Number(quarter[2]) - 1) * 3 + 1).padStart(2, '0')}-01`;
  if (/^\d{4}$/.test(key)) return `${key}-01-01`;
  return key || '0000-00-00';
}
