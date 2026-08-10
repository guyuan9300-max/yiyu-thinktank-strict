import test from 'node:test';
import assert from 'node:assert/strict';

import { getChinaCalendarMarkers } from './china-calendar.js';

test('returns festival and off-day markers for 2026 Qingming Festival', () => {
  const markers = getChinaCalendarMarkers(new Date(2026, 3, 4));

  assert.deepEqual(
    markers.map((item) => item.label),
    ['清明', '休'],
  );
});

test('returns make-up workday marker for 2026-10-10', () => {
  const markers = getChinaCalendarMarkers(new Date(2026, 9, 10));

  assert.deepEqual(
    markers.map((item) => item.label),
    ['班'],
  );
});

test('returns empty array for ordinary workday without official holiday markers', () => {
  const markers = getChinaCalendarMarkers(new Date(2026, 2, 23));

  assert.deepEqual(markers, []);
});

