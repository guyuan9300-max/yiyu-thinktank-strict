export type KnowledgeJobStatus = string | null | undefined;

export function isActiveKnowledgeJobStatus(status: KnowledgeJobStatus): boolean {
  return status === 'queued' || status === 'running';
}

export function shouldPollKnowledgeProgress(input: {
  isSubmitting: boolean;
  pendingJobs: number;
  runningJobs: number;
}): boolean {
  return input.isSubmitting || input.pendingJobs + input.runningJobs > 0;
}
