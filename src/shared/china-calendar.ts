export type ChinaCalendarMarkerKind = 'festival' | 'offday' | 'workday';

export interface ChinaCalendarMarker {
  label: string;
  kind: ChinaCalendarMarkerKind;
}

type MarkerDraft = {
  date: string;
  label: string;
  kind: ChinaCalendarMarkerKind;
};

type RangeMarkerDraft = {
  start: string;
  end: string;
  label: string;
  kind: ChinaCalendarMarkerKind;
};

function addDays(base: Date, days: number) {
  return new Date(base.getFullYear(), base.getMonth(), base.getDate() + days);
}

function toDateKey(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function expandRanges(ranges: RangeMarkerDraft[]) {
  const expanded: MarkerDraft[] = [];
  ranges.forEach((range) => {
    const start = new Date(`${range.start}T00:00:00`);
    const end = new Date(`${range.end}T00:00:00`);
    for (let cursor = start; cursor.getTime() <= end.getTime(); cursor = addDays(cursor, 1)) {
      expanded.push({
        date: toDateKey(cursor),
        label: range.label,
        kind: range.kind,
      });
    }
  });
  return expanded;
}

function buildMarkerMap() {
  const ranges: RangeMarkerDraft[] = [
    { start: '2025-01-01', end: '2025-01-01', label: '休', kind: 'offday' },
    { start: '2025-01-28', end: '2025-02-04', label: '休', kind: 'offday' },
    { start: '2025-04-04', end: '2025-04-06', label: '休', kind: 'offday' },
    { start: '2025-05-01', end: '2025-05-05', label: '休', kind: 'offday' },
    { start: '2025-05-31', end: '2025-06-02', label: '休', kind: 'offday' },
    { start: '2025-10-01', end: '2025-10-08', label: '休', kind: 'offday' },
    { start: '2026-01-01', end: '2026-01-03', label: '休', kind: 'offday' },
    { start: '2026-02-15', end: '2026-02-23', label: '休', kind: 'offday' },
    { start: '2026-04-04', end: '2026-04-06', label: '休', kind: 'offday' },
    { start: '2026-05-01', end: '2026-05-05', label: '休', kind: 'offday' },
    { start: '2026-06-19', end: '2026-06-21', label: '休', kind: 'offday' },
    { start: '2026-09-25', end: '2026-09-27', label: '休', kind: 'offday' },
    { start: '2026-10-01', end: '2026-10-07', label: '休', kind: 'offday' },
  ];

  const singles: MarkerDraft[] = [
    { date: '2025-01-01', label: '元旦', kind: 'festival' },
    { date: '2025-01-28', label: '除夕', kind: 'festival' },
    { date: '2025-01-29', label: '春节', kind: 'festival' },
    { date: '2025-04-04', label: '清明', kind: 'festival' },
    { date: '2025-05-01', label: '劳动节', kind: 'festival' },
    { date: '2025-05-31', label: '端午', kind: 'festival' },
    { date: '2025-10-01', label: '国庆', kind: 'festival' },
    { date: '2025-10-06', label: '中秋', kind: 'festival' },
    { date: '2025-01-26', label: '班', kind: 'workday' },
    { date: '2025-02-08', label: '班', kind: 'workday' },
    { date: '2025-04-27', label: '班', kind: 'workday' },
    { date: '2025-09-28', label: '班', kind: 'workday' },
    { date: '2025-10-11', label: '班', kind: 'workday' },
    { date: '2026-01-01', label: '元旦', kind: 'festival' },
    { date: '2026-02-16', label: '除夕', kind: 'festival' },
    { date: '2026-02-17', label: '春节', kind: 'festival' },
    { date: '2026-04-04', label: '清明', kind: 'festival' },
    { date: '2026-05-01', label: '劳动节', kind: 'festival' },
    { date: '2026-06-19', label: '端午', kind: 'festival' },
    { date: '2026-09-25', label: '中秋', kind: 'festival' },
    { date: '2026-10-01', label: '国庆', kind: 'festival' },
    { date: '2026-01-04', label: '班', kind: 'workday' },
    { date: '2026-02-14', label: '班', kind: 'workday' },
    { date: '2026-02-28', label: '班', kind: 'workday' },
    { date: '2026-05-09', label: '班', kind: 'workday' },
    { date: '2026-09-20', label: '班', kind: 'workday' },
    { date: '2026-10-10', label: '班', kind: 'workday' },
  ];

  const priority: Record<ChinaCalendarMarkerKind, number> = {
    festival: 0,
    offday: 1,
    workday: 2,
  };

  const map = new Map<string, ChinaCalendarMarker[]>();
  [...expandRanges(ranges), ...singles].forEach((marker) => {
    const existing = map.get(marker.date) || [];
    if (!existing.some((item) => item.label === marker.label && item.kind === marker.kind)) {
      existing.push({ label: marker.label, kind: marker.kind });
      existing.sort((left, right) => priority[left.kind] - priority[right.kind]);
      map.set(marker.date, existing);
    }
  });

  return map;
}

const CHINA_CALENDAR_MARKERS = buildMarkerMap();

export function getChinaCalendarMarkers(date: Date): ChinaCalendarMarker[] {
  return CHINA_CALENDAR_MARKERS.get(toDateKey(date)) || [];
}

