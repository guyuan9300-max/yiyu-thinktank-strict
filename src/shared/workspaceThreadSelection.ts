import type { ChatThread } from './types.js';

export type WorkspaceThreadPreference = 'fresh' | 'latest';

export function parseWorkspaceThreadPreference(value: string | null | undefined): WorkspaceThreadPreference {
  return value === 'fresh' ? 'fresh' : 'latest';
}

export function pickWorkspaceCurrentThreadId(input: {
  activeAnalysisRunThreadId?: string | null;
  selectedThreadId?: string | null;
  threads?: ChatThread[] | null;
  preference?: WorkspaceThreadPreference;
  currentClientId?: string | null;
  workspaceClientId?: string | null;
}): string | null {
  if (input.activeAnalysisRunThreadId) {
    return input.activeAnalysisRunThreadId;
  }
  if (
    input.currentClientId &&
    input.workspaceClientId &&
    input.currentClientId !== input.workspaceClientId
  ) {
    return null;
  }
  const threads = Array.isArray(input.threads) ? input.threads : [];
  if (input.selectedThreadId) {
    if (threads.length === 0 || threads.some((thread) => thread.id === input.selectedThreadId)) {
      return input.selectedThreadId;
    }
  }
  if ((input.preference || 'latest') === 'fresh') {
    return null;
  }
  return [...threads]
    .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
    .find((thread) => Boolean(thread.id))
    ?.id || null;
}
