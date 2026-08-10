/// <reference path="../../../../shared/types.ts" />

import type {
  GC13EvidenceCategory,
  GC13GrowthSnapshot,
} from './gc13GrowthContract';

const ROOT = '/api/v2/ui/gc13/growth';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers || {});
  headers.set('Accept', 'application/json');
  if (init?.body) headers.set('Content-Type', 'application/json');
  const token = window.yiyuWorkbench.desktopToken?.trim();
  if (token) headers.set('X-Yiyu-Desktop-Token', token);
  if (init?.method && init.method !== 'GET') {
    headers.set('Idempotency-Key', crypto.randomUUID());
  }
  const response = await fetch(`${window.yiyuWorkbench.backendBaseUrl}${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) {
    const text = await response.text();
    let message = text || `HTTP ${response.status}`;
    try {
      const payload = JSON.parse(text) as {
        detail?: string;
        error?: { message?: string };
      };
      message = payload.error?.message || payload.detail || message;
    } catch {
      // The plain response is already the most useful error.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export function loadGC13GrowthSnapshot() {
  return request<GC13GrowthSnapshot>(ROOT);
}

export function confirmGC13GrowthEvidence(payload: {
  summary: string;
  category: GC13EvidenceCategory;
}) {
  return request(`${ROOT}/evidence`, {
    method: 'POST',
    body: JSON.stringify({
      ...payload,
      sourceType: 'manual_reflection',
      contributionScore: 1,
    }),
  });
}

export function rebuildGC13GrowthModels() {
  return request(`${ROOT}/rebuild`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

export function updateGC13GrowthEvidence(
  evidenceId: string,
  action: 'revise' | 'exclude',
  payload: { expectedVersion: number; summary?: string; category?: GC13EvidenceCategory },
) {
  return request(`${ROOT}/evidence/${encodeURIComponent(evidenceId)}/${action}`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function decideGC13WeeklyReviewCandidate(
  candidateId: string,
  action: 'confirm' | 'ignore',
) {
  return request(`${ROOT}/weekly-review-candidates/${encodeURIComponent(candidateId)}/${action}`, {
    method: 'POST',
    body: JSON.stringify({}),
  });
}
