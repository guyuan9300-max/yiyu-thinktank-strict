import type {
  GC08MeetingMaterial,
  GC08PublishResult,
  GC08RecordingDetail,
} from './gc08Contract';

const ROOT = '/api/v2/ui';

export class GC08ApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(status: number, message: string, code: string | null = null) {
    super(message);
    this.name = 'GC08ApiError';
    this.status = status;
    this.code = code;
  }
}

function segment(value: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error('gc08_route_segment_required');
  return encodeURIComponent(normalized);
}

function randomIdempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.() || `gc08-${Date.now()}-${Math.random()}`;
}

function errorDetail(value: unknown): { message: string | null; code: string | null } {
  if (typeof value === 'string') return { message: value, code: null };
  if (!value || typeof value !== 'object') return { message: null, code: null };
  const payload = value as Record<string, unknown>;
  const nested = payload.error && typeof payload.error === 'object'
    ? payload.error as Record<string, unknown>
    : null;
  const detail = payload.detail && typeof payload.detail === 'object'
    ? payload.detail as Record<string, unknown>
    : null;
  return {
    message: String(
      nested?.message
      || detail?.message
      || (typeof payload.detail === 'string' ? payload.detail : '')
      || '',
    ).trim() || null,
    code: String(nested?.code || detail?.code || payload.code || '').trim() || null,
  };
}

async function request<T>(
  path: string,
  init?: RequestInit,
  idempotencyKey?: string,
): Promise<T> {
  const headers = new Headers(init?.headers || {});
  headers.set('Accept', 'application/json');
  if (init?.body) headers.set('Content-Type', 'application/json');
  const token = window.yiyuWorkbench.desktopToken?.trim();
  if (token) headers.set('X-Yiyu-Desktop-Token', token);
  if (init?.method && init.method !== 'GET') {
    headers.set('Idempotency-Key', idempotencyKey || randomIdempotencyKey());
  }
  const response = await fetch(`${window.yiyuWorkbench.backendBaseUrl}${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) {
    const text = await response.text();
    let decoded: unknown = text;
    try {
      decoded = JSON.parse(text);
    } catch {
      // Safe plain-text errors are shown as-is.
    }
    const parsed = errorDetail(decoded);
    throw new GC08ApiError(
      response.status,
      parsed.message || text || `HTTP ${response.status}`,
      parsed.code,
    );
  }
  return response.json() as Promise<T>;
}

function recordingRoot(clientId: string, meetingId: string): string {
  return `${ROOT}/clients/${segment(clientId)}/meetings/${segment(meetingId)}/recordings`;
}

export interface GC08ApiClient {
  getMeetingMaterials(
    clientId: string,
    meetingId: string,
  ): Promise<GC08MeetingMaterial[]>;
  getLatestRecording(
    clientId: string,
    meetingId: string,
  ): Promise<GC08RecordingDetail | null>;
  registerRecording(
    clientId: string,
    meetingId: string,
    payload: { audioPath: string; durationMs?: number; capturedAt?: string },
  ): Promise<GC08RecordingDetail>;
  getRecording(
    clientId: string,
    meetingId: string,
    recordingId: string,
  ): Promise<GC08RecordingDetail>;
  transcribe(
    clientId: string,
    meetingId: string,
    recordingId: string,
    payload?: { language?: string; force?: boolean },
  ): Promise<GC08RecordingDetail>;
  createMinutesDraft(
    clientId: string,
    meetingId: string,
    recordingId: string,
    payload: { title?: string; minutesMarkdown?: string; force?: boolean },
  ): Promise<GC08RecordingDetail>;
  publishMinutes(
    clientId: string,
    meetingId: string,
    recordingId: string,
    payload?: { expectedVersion?: number },
    idempotencyKey?: string,
  ): Promise<GC08PublishResult>;
}

export const gc08Api: GC08ApiClient = {
  getMeetingMaterials(clientId, meetingId) {
    return request<GC08MeetingMaterial[]>(
      `${ROOT}/clients/${segment(clientId)}/meetings/${segment(meetingId)}/materials`,
    );
  },
  getLatestRecording(clientId, meetingId) {
    return request<GC08RecordingDetail | null>(recordingRoot(clientId, meetingId));
  },
  registerRecording(clientId, meetingId, payload) {
    return request<GC08RecordingDetail>(recordingRoot(clientId, meetingId), {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  },
  getRecording(clientId, meetingId, recordingId) {
    return request<GC08RecordingDetail>(
      `${recordingRoot(clientId, meetingId)}/${segment(recordingId)}`,
    );
  },
  transcribe(clientId, meetingId, recordingId, payload = {}) {
    return request<GC08RecordingDetail>(
      `${recordingRoot(clientId, meetingId)}/${segment(recordingId)}/transcriptions`,
      { method: 'POST', body: JSON.stringify(payload) },
    );
  },
  createMinutesDraft(clientId, meetingId, recordingId, payload) {
    return request<GC08RecordingDetail>(
      `${recordingRoot(clientId, meetingId)}/${segment(recordingId)}/minutes/draft`,
      { method: 'POST', body: JSON.stringify(payload) },
    );
  },
  publishMinutes(clientId, meetingId, recordingId, payload = {}, idempotencyKey) {
    return request<GC08PublishResult>(
      `${recordingRoot(clientId, meetingId)}/${segment(recordingId)}/minutes/publish`,
      { method: 'POST', body: JSON.stringify(payload) },
      idempotencyKey,
    );
  },
};
