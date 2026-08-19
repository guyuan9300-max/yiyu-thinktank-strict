import type {
  GC06CalendarEntry,
  GC06DecisionAction,
  GC06EventLine,
  GC06Meeting,
  GC06PlanningCycle,
  GC06WeeklyReview,
} from './gc06Contract';
import { requestStrictUi } from '../../lib/api';

const ROOT = '/api/v2/ui/gc06';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return requestStrictUi<T>(path, init);
}

const encodeQuery = (query: Record<string, string | undefined>) => {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  const suffix = params.toString();
  return suffix ? `?${suffix}` : '';
};

export const gc06Api = {
  listEventLines(clientId?: string) {
    return request<GC06EventLine[]>(
      `${ROOT}/event-lines${encodeQuery({ clientId, includeArchived: 'true' })}`,
    );
  },
  createEventLine(payload: { clientId: string; name: string; goal?: string }) {
    return request<{ eventLine: GC06EventLine }>(`${ROOT}/event-lines`, {
      method: 'POST', body: JSON.stringify(payload),
    });
  },
  transitionEventLine(eventLine: GC06EventLine, transition: 'archive' | 'reopen') {
    return request<{ eventLine: GC06EventLine }>(
      `${ROOT}/event-lines/${eventLine.id}/${transition}`,
      { method: 'POST', body: JSON.stringify({ expectedVersion: eventLine.version }) },
    );
  },
  listPlanningCycles(includeArchived = false) {
    return request<GC06PlanningCycle[]>(
      `${ROOT}/planning-cycles${encodeQuery({ includeArchived: includeArchived ? 'true' : undefined })}`,
    );
  },
  createPlanningCycle(payload: Record<string, unknown>) {
    return request<{ planningCycle: GC06PlanningCycle }>(`${ROOT}/planning-cycles`, {
      method: 'POST', body: JSON.stringify(payload),
    });
  },
  updatePlanningCycle(cycle: GC06PlanningCycle, payload: Record<string, unknown>) {
    return request<{ planningCycle: GC06PlanningCycle }>(
      `${ROOT}/planning-cycles/${cycle.id}`,
      {
        method: 'PATCH',
        body: JSON.stringify({ ...payload, expectedVersion: cycle.version }),
      },
    );
  },
  deletePlanningCycle(cycle: GC06PlanningCycle) {
    return request<{ planningCycle: GC06PlanningCycle }>(
      `${ROOT}/planning-cycles/${cycle.id}`,
      { method: 'DELETE', body: JSON.stringify({ expectedVersion: cycle.version }) },
    );
  },
  listWeeklyReviews(planningCycleId?: string) {
    return request<GC06WeeklyReview[]>(
      `${ROOT}/weekly-reviews${encodeQuery({ planningCycleId })}`,
    );
  },
  saveWeeklyReviewDraft(payload: Record<string, unknown>) {
    return request<{ weeklyReview: GC06WeeklyReview }>(`${ROOT}/weekly-reviews/draft`, {
      method: 'POST', body: JSON.stringify(payload),
    });
  },
  transitionWeeklyReview(
    review: GC06WeeklyReview,
    transition: 'submit' | 'return' | 'reopen',
  ) {
    return request<{ weeklyReview: GC06WeeklyReview }>(
      `${ROOT}/weekly-reviews/${review.id}/${transition}`,
      {
        method: 'POST',
        body: JSON.stringify({ expectedVersion: review.version }),
      },
    );
  },
  listDecisionActions(planningCycleId?: string) {
    return request<GC06DecisionAction[]>(
      `${ROOT}/decision-actions${encodeQuery({ planningCycleId })}`,
    );
  },
  createDecisionAction(payload: Record<string, unknown>) {
    return request<{ decisionAction: GC06DecisionAction }>(`${ROOT}/decision-actions`, {
      method: 'POST', body: JSON.stringify(payload),
    });
  },
  updateDecisionAction(action: GC06DecisionAction, payload: Record<string, unknown>) {
    return request<{ decisionAction: GC06DecisionAction }>(
      `${ROOT}/decision-actions/${action.id}`,
      {
        method: 'PATCH',
        body: JSON.stringify({ ...payload, expectedVersion: action.version }),
      },
    );
  },
  convertActionToPrimaryTask(action: GC06DecisionAction) {
    return request<{ decisionAction: GC06DecisionAction }>(
      `${ROOT}/decision-actions/${action.id}/primary-task`,
      { method: 'POST', body: JSON.stringify({ expectedVersion: action.version }) },
    );
  },
  listMeetings(clientId?: string) {
    return request<GC06Meeting[]>(`${ROOT}/meetings${encodeQuery({ clientId })}`);
  },
  createMeeting(payload: Record<string, unknown>) {
    return request<{ meeting: GC06Meeting }>(`${ROOT}/meetings`, {
      method: 'POST', body: JSON.stringify(payload),
    });
  },
  updateMeeting(meeting: GC06Meeting, payload: Record<string, unknown>) {
    return request<{ meeting: GC06Meeting }>(`${ROOT}/meetings/${meeting.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ ...payload, expectedVersion: meeting.version }),
    });
  },
  migrateMeetingToTask(meeting: GC06Meeting) {
    return request<{ task: import('../../../shared/types').Task; meetingId: string; migrated: boolean }>(
      `${ROOT}/meetings/${meeting.id}/migrate-to-task`,
      { method: 'POST', body: JSON.stringify({ expectedVersion: meeting.version }) },
    );
  },
  transitionMeetingCollaboration(
    meeting: GC06Meeting,
    action: 'accept' | 'reject',
    expectedGrantVersion: number,
  ) {
    return request<{ meeting: GC06Meeting }>(
      `${ROOT}/meetings/${meeting.id}/collaboration/${action}`,
      {
        method: 'POST',
        body: JSON.stringify({ expectedGrantVersion }),
      },
    );
  },
  listCalendar(startsFrom?: string, startsTo?: string) {
    return request<GC06CalendarEntry[]>(
      `${ROOT}/calendar${encodeQuery({ startsFrom, startsTo })}`,
    );
  },
};
