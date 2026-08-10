import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildCalendarCells,
  getTodayCalendarState,
  shiftCalendarMonth,
} from './calendar.js';

test('buildCalendarCells aligns Sunday-start months to the last column in a Monday-first calendar', () => {
  const cells = buildCalendarCells(new Date(2026, 2, 1));

  assert.equal(cells.length, 42);
  assert.equal(cells[6]?.day, 1);
});

test('buildCalendarCells aligns Wednesday-start months to the third column in a Monday-first calendar', () => {
  const cells = buildCalendarCells(new Date(2026, 3, 1));

  assert.equal(cells[2]?.day, 1);
});

test('shiftCalendarMonth clamps the selected day to the target month length', () => {
  const state = shiftCalendarMonth(new Date(2026, 4, 1), 31, -1);

  assert.equal(state.calendarDate.getFullYear(), 2026);
  assert.equal(state.calendarDate.getMonth(), 3);
  assert.equal(state.selectedDay, 30);
});

test('getTodayCalendarState resets month anchor and selected day together', () => {
  const today = new Date(2026, 2, 12);
  const state = getTodayCalendarState(today);

  assert.equal(state.calendarDate.getFullYear(), 2026);
  assert.equal(state.calendarDate.getMonth(), 2);
  assert.equal(state.calendarDate.getDate(), 1);
  assert.equal(state.selectedDay, 12);
});
