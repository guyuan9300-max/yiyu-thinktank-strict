export type TaskVisibilityInput = {
  scopeMode?: string | null;
  tags?: Array<{ scope?: string | null }> | null;
};

export function isPersonalOnlyTask(task: TaskVisibilityInput | null | undefined): boolean {
  if (!task) return false;
  if (task.scopeMode === 'PERSONAL_ONLY') return true;
  return Boolean(task.tags?.some((tag) => tag.scope === 'self'));
}

export function isSharedTask(task: TaskVisibilityInput | null | undefined): boolean {
  return !isPersonalOnlyTask(task);
}

export function filterSharedTasks<T extends TaskVisibilityInput>(tasks: T[]): T[] {
  return tasks.filter((task) => isSharedTask(task));
}
