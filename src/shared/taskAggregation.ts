export type TaskAggregationMode = 'eventLine' | 'department';

export type TaskPersonalSurface = 'list' | 'calendar';

export interface TaskAggregationViewerSurfaces {
  personalList: boolean;
  personalCalendar: boolean;
  collaborationInbox: boolean;
  eventLineDetail: boolean;
}

export type TaskOwnerDepartmentResolution = 'resolved' | 'unassigned' | 'ambiguous';

export interface TaskAggregationInput {
  id: string;
  title: string;
  eventLineId?: string | null;
  eventLineName?: string | null;
  ownerDepartmentId?: string | null;
  ownerDepartmentName?: string | null;
  ownerDepartmentResolution?: TaskOwnerDepartmentResolution;
  viewerSurfaces?: TaskAggregationViewerSurfaces;
}

export interface TaskAggregationGroup<T extends TaskAggregationInput> {
  key: string;
  label: string;
  hint: string;
  sourceId: string | null;
  tasks: T[];
}

export function isTaskOnPersonalSurface(
  task: Pick<TaskAggregationInput, 'viewerSurfaces'>,
  surface: TaskPersonalSurface,
) {
  if (!task.viewerSurfaces) return false;
  return surface === 'list'
    ? task.viewerSurfaces.personalList === true
    : task.viewerSurfaces.personalCalendar === true;
}

export function groupTasksByReference<T extends TaskAggregationInput>(
  tasks: T[],
  mode: TaskAggregationMode,
  options: { organizationName?: string | null } = {},
): Array<TaskAggregationGroup<T>> {
  const groups = new Map<string, TaskAggregationGroup<T>>();

  tasks.forEach((task) => {
    let key: string;
    let label: string;
    let hint: string;
    let sourceId: string | null = null;

    if (mode === 'eventLine') {
      sourceId = task.eventLineId?.trim() || null;
      key = sourceId ? `event-line:${sourceId}` : 'event-line:unassigned';
      label = sourceId
        ? task.eventLineName?.trim() || '未命名事件线'
        : '未归入事件线';
      hint = sourceId ? '事件线内的个人任务' : '尚未归入事件线的个人任务';
    } else {
      const resolution = task.ownerDepartmentResolution || 'unassigned';
      sourceId = resolution === 'resolved'
        ? task.ownerDepartmentId?.trim() || null
        : null;
      if (resolution === 'ambiguous') {
        key = 'department:ambiguous';
        label = '部门归属异常';
        hint = '检测到多个有效部门归属，当前规则不会自动选择其中一个';
      } else if (sourceId) {
        key = `department:${sourceId}`;
        label = task.ownerDepartmentName?.trim() || '未命名部门';
        hint = '按负责人当前归属部门聚合';
      } else {
        key = 'department:unassigned';
        label = options.organizationName?.trim() || '组织任务';
        hint = '负责人没有部门，按当前组织归类';
      }
    }

    const existing = groups.get(key);
    if (existing) {
      existing.tasks.push(task);
    } else {
      groups.set(key, { key, label, hint, sourceId, tasks: [task] });
    }
  });

  const specialRank = (key: string) => {
    if (key.endsWith(':ambiguous')) return 1;
    if (key.endsWith(':unassigned')) return 2;
    return 0;
  };
  return [...groups.values()].sort((left, right) => {
    const rankDifference = specialRank(left.key) - specialRank(right.key);
    if (rankDifference !== 0) return rankDifference;
    const labelDifference = left.label.localeCompare(right.label, 'zh-CN');
    return labelDifference !== 0 ? labelDifference : left.key.localeCompare(right.key);
  });
}
