export type GC06LifecycleState = 'active' | 'archived' | 'deleted';
export type GC06PlanKind = 'organization_plan' | 'department_plan';
export type GC06ReviewState = 'draft' | 'submitted' | 'returned';

export interface GC06EventLine {
  id: string;
  clientId: string;
  name: string;
  kind: string;
  goal: string;
  background: string;
  visibilityScope: string;
  lifecycleState: GC06LifecycleState;
  version: number;
  taskCount: number;
  meetingCount: number;
  activityCount: number;
}

export interface GC06PlanningCycle {
  id: string;
  recordKind: GC06PlanKind;
  clientId: string | null;
  eventLineId: string | null;
  departmentId: string | null;
  ownerMembershipId: string | null;
  period?: string | null;
  periodKind?: 'week' | 'month' | 'quarter' | 'year' | 'custom' | string | null;
  periodStart: string;
  periodEnd: string;
  title: string;
  summary: string;
  status: string;
  version: number;
  lifecycleState: GC06LifecycleState;
}

export interface GC06WeeklyReviewVersion {
  id: string;
  reviewId: string;
  version: number;
  businessState: GC06ReviewState;
  content: Record<string, unknown>;
  reviewNote: string;
  submittedAt: string | null;
}

export interface GC06WeeklyReview {
  id: string;
  membershipId: string;
  planningCycleId: string;
  status: GC06ReviewState;
  version: number;
  currentDraftVersionId: string | null;
  currentSubmittedVersionId: string | null;
  versions: GC06WeeklyReviewVersion[];
}

export interface GC06DecisionAction {
  id: string;
  recordKind: 'decision' | 'plan_action';
  planningCycleId: string;
  clientId: string | null;
  taskId: string | null;
  decisionState: string;
  title: string;
  statement: string;
  expectedOutput: string;
  ownerMembershipId: string | null;
  version: number;
}

export interface GC06Meeting {
  id: string;
  clientId: string;
  eventLineId: string | null;
  title: string;
  agenda: string;
  startsAt: string;
  endsAt: string;
  status: string;
  version: number;
  organizerMembershipId?: string | null;
  createdByMembershipId?: string | null;
  collaborators?: Array<{
    grantId: string;
    membershipId: string;
    displayName: string;
    roleKey: 'creator' | 'owner' | 'collaborator';
    inboxStatus: 'pending' | 'accepted' | 'rejected';
    version: number;
  }>;
  planLink?: {
    sourceSetId?: string;
    planningCycleId: string | null;
    decisionActionId: string | null;
  } | null;
}

export interface GC06CalendarEntry {
  id: string;
  target_kind: 'task' | 'meeting';
  task_id: string | null;
  meeting_id: string | null;
  starts_at: string;
  ends_at: string | null;
  source_version: number;
  display_state: string;
}

export function weeklyReviewStableKey(
  membershipId: string,
  planningCycleId: string,
): string {
  const membership = membershipId.trim();
  const cycle = planningCycleId.trim();
  if (!membership || !cycle) throw new Error('membership_and_planning_cycle_required');
  return `${membership}::${cycle}`;
}

export function canAttachTaskToEventLine(
  taskClientId: string | null,
  eventLineClientId: string,
): boolean {
  return Boolean(taskClientId && taskClientId === eventLineClientId);
}

export function assertEventLineClient(clientId: string | null | undefined): string {
  const normalized = clientId?.trim() || '';
  if (!normalized) throw new Error('event_line_client_required');
  return normalized;
}

export function primaryActionTaskStatus(action: GC06DecisionAction) {
  return action.taskId
    ? { connected: true as const, label: '已由正式任务承接' }
    : { connected: false as const, label: '等待正式任务命令' };
}
