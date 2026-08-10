export interface CalendarCell {
  day: number | null;
  date: Date | null;
}

export function getStartOfMonth(date: Date) {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

export function getDaysInMonth(baseDate: Date) {
  return new Date(baseDate.getFullYear(), baseDate.getMonth() + 1, 0).getDate();
}

export function clampCalendarDay(baseDate: Date, day: number) {
  return Math.min(Math.max(day, 1), getDaysInMonth(baseDate));
}

export function buildCalendarCells(baseDate: Date): CalendarCell[] {
  const year = baseDate.getFullYear();
  const month = baseDate.getMonth();
  const firstDay = new Date(year, month, 1).getDay();
  const mondayFirstOffset = (firstDay + 6) % 7;
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const cells: CalendarCell[] = [];

  for (let i = 0; i < mondayFirstOffset; i += 1) {
    cells.push({ day: null, date: null });
  }

  for (let day = 1; day <= daysInMonth; day += 1) {
    cells.push({ day, date: new Date(year, month, day) });
  }

  // Keep the month grid at 6 rows so the calendar card height stays stable
  // and different months do not visually collapse to content width/height.
  while (cells.length % 7 !== 0 || cells.length < 42) {
    cells.push({ day: null, date: null });
  }

  return cells;
}

export function formatMonthTitle(date: Date) {
  return `${date.getFullYear()}年 ${date.getMonth() + 1}月`;
}

export function shiftCalendarMonth(baseDate: Date, selectedDay: number, monthDelta: number) {
  const calendarDate = new Date(baseDate.getFullYear(), baseDate.getMonth() + monthDelta, 1);

  return {
    calendarDate,
    selectedDay: clampCalendarDay(calendarDate, selectedDay),
  };
}

export function getTodayCalendarState(today = new Date()) {
  return {
    calendarDate: getStartOfMonth(today),
    selectedDay: today.getDate(),
  };
}
