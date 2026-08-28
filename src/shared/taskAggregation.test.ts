import test from 'node:test';
import assert from 'node:assert/strict';

import {
  groupTasksByReference,
  isTaskOnPersonalSurface,
} from './taskAggregation.js';

type AggregationTask = Parameters<typeof groupTasksByReference>[0][number];

function task(id: string, patch: Partial<AggregationTask> = {}): AggregationTask {
  return {
    id,
    title: `任务 ${id}`,
    eventLineId: null,
    eventLineName: null,
    ownerDepartmentId: null,
    ownerDepartmentName: null,
    ownerDepartmentResolution: 'unassigned',
    viewerSurfaces: {
      personalList: true,
      personalCalendar: true,
      collaborationInbox: false,
      eventLineDetail: false,
    },
    ...patch,
  };
}

test('personal task surfaces are accepted from the server contract only', () => {
  assert.equal(isTaskOnPersonalSurface(task('personal'), 'list'), true);
  assert.equal(isTaskOnPersonalSurface(task('calendar'), 'calendar'), true);
  assert.equal(isTaskOnPersonalSurface(task('hidden', { viewerSurfaces: undefined }), 'list'), false);
  assert.equal(isTaskOnPersonalSurface(task('org-readable', {
    viewerSurfaces: {
      personalList: false,
      personalCalendar: false,
      collaborationInbox: false,
      eventLineDetail: false,
    },
  }), 'list'), false);
});

test('event-line aggregation uses stable ids and keeps unassigned tasks explicit', () => {
  const groups = groupTasksByReference([
    task('a', { eventLineId: 'line-1', eventLineName: '同名事件线' }),
    task('b', { eventLineId: 'line-2', eventLineName: '同名事件线' }),
    task('c'),
  ], 'eventLine');

  assert.deepEqual(groups.map((group) => group.key), [
    'event-line:line-1',
    'event-line:line-2',
    'event-line:unassigned',
  ]);
  assert.deepEqual(groups.map((group) => group.tasks.map((item) => item.id)), [
    ['a'],
    ['b'],
    ['c'],
  ]);
});

test('department aggregation never duplicates tasks and exposes unresolved ownership', () => {
  const source = [
    task('resolved', {
      ownerDepartmentId: 'department-product',
      ownerDepartmentName: '产品部',
      ownerDepartmentResolution: 'resolved',
    }),
    task('unassigned'),
    task('ambiguous', { ownerDepartmentResolution: 'ambiguous' }),
  ];

  const groups = groupTasksByReference(source, 'department', { organizationName: '星丛' });
  const ids = groups.flatMap((group) => group.tasks.map((item) => item.id));

  assert.deepEqual(groups.map((group) => group.key), [
    'department:department-product',
    'department:ambiguous',
    'department:unassigned',
  ]);
  assert.deepEqual(groups.map((group) => group.label), [
    '产品部',
    '部门归属异常',
    '星丛',
  ]);
  assert.deepEqual(ids.sort(), source.map((item) => item.id).sort());
  assert.equal(new Set(ids).size, source.length);
});
