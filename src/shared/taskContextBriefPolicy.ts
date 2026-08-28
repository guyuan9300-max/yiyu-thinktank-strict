export type TaskContextBriefCandidate = {
  isLocalDraft: boolean;
  scopeMode?: string | null;
  eventLineId?: string | null;
  clientId?: string | null;
};

export function shouldLoadTaskContextBrief(
  task: TaskContextBriefCandidate,
  cachedQualityFlags?: readonly string[] | null,
): boolean {
  if (task.isLocalDraft || task.scopeMode === 'PERSONAL_ONLY') return false;
  if (!task.eventLineId && !task.clientId) return false;
  return !cachedQualityFlags || cachedQualityFlags.includes('preview_only');
}
