export type GC08ProcessingState =
  | 'not_requested'
  | 'processing'
  | 'blocked'
  | 'failed_retryable'
  | 'ready'
  | 'unknown';

export interface GC08TranscriptionState {
  transcriptionId: string | null;
  version: number | null;
  status: GC08ProcessingState | string;
  language: string | null;
  integrityHash: string | null;
  errorCode: string | null;
  message: string | null;
  retryable: boolean;
}

export interface GC08MinutesState {
  documentId: string;
  documentVersionId: string;
  version: number;
  title: string;
  publicationState: 'draft' | 'published' | string;
  contentHash: string;
  minutesMarkdown: string | null;
  receipt: Record<string, unknown>;
}

export interface GC08MinutesProcessingState {
  status: GC08ProcessingState | string;
  errorCode: string | null;
  message: string | null;
  retryable: boolean;
}

export interface GC08DownstreamAdapter {
  interface: string;
  state: 'waiting_for_formal_command' | string;
  payloadBasis: {
    clientId: string;
    meetingId: string;
    sourceDocumentId: string;
    sourceVersion: number;
  };
}

export interface GC08RecordingDetail {
  clientId: string;
  meetingId: string;
  recordingId: string;
  recordingState: string;
  durationMs: number;
  capturedAt: string | null;
  localFiles?: {
    recordingPath: string | null;
    transcriptionPath: string | null;
  };
  transcriptionProgress?: {
    percent: number;
    stage: string;
    status: 'queued' | 'processing' | 'completed' | 'blocked' | 'failed_retryable' | string;
    retryable: boolean;
    updatedAt?: string | null;
  };
  transcription: GC08TranscriptionState;
  minutes: GC08MinutesState | null;
  minutesProcessing: GC08MinutesProcessingState;
  downstreamAdapters?: {
    taskCommand: GC08DownstreamAdapter;
    eventLineReferenceCommand: GC08DownstreamAdapter;
  };
}

export interface GC08MeetingMaterial {
  id: string;
  fileName: string;
  mediaType: string;
  byteSize: number;
  localPath: string | null;
  availabilityState: 'ready' | 'missing' | string;
}

export interface GC08PublishResult {
  state: 'published' | string;
  cloud: {
    publicationState?: string;
    version?: number;
    idempotentReplay?: boolean;
    [key: string]: unknown;
  };
  local: GC08RecordingDetail;
}

export interface GC08StatePresentation {
  label: string;
  description: string;
  tone: 'neutral' | 'working' | 'warning' | 'error' | 'success';
}

const KNOWN_STATES = new Set<GC08ProcessingState>([
  'not_requested',
  'processing',
  'blocked',
  'failed_retryable',
  'ready',
]);

export function gc08ProcessingState(value: unknown): GC08ProcessingState {
  const normalized = String(value || '').trim() as GC08ProcessingState;
  return KNOWN_STATES.has(normalized) ? normalized : 'unknown';
}

export function gc08StatePresentation(value: unknown): GC08StatePresentation {
  switch (gc08ProcessingState(value)) {
    case 'not_requested':
      return {
        label: '尚未开始',
        description: '登记本机录音后才能开始转写。',
        tone: 'neutral',
      };
    case 'processing':
      return {
        label: '处理中',
        description: '本机正在处理，请勿关闭应用。',
        tone: 'working',
      };
    case 'blocked':
      return {
        label: '能力未就绪',
        description: '原文件已保留，外部能力就绪后可以重试。',
        tone: 'warning',
      };
    case 'failed_retryable':
      return {
        label: '失败，可重试',
        description: '本次没有生成有效结果，原文件和失败回执均已保留。',
        tone: 'error',
      };
    case 'ready':
      return {
        label: '已就绪',
        description: '已生成非空结果，并保留本机版本和证据链。',
        tone: 'success',
      };
    default:
      return {
        label: '状态待核对',
        description: '没有把未知状态视为成功，请刷新后重试。',
        tone: 'warning',
      };
  }
}

export function gc08CanRetryTranscription(value: unknown): boolean {
  const state = gc08ProcessingState(value);
  return state === 'blocked' || state === 'failed_retryable' || state === 'ready';
}

export function gc08CanCreateMinutes(detail: GC08RecordingDetail | null): boolean {
  return gc08ProcessingState(detail?.transcription.status) === 'ready';
}

export function gc08CanPublish(
  detail: GC08RecordingDetail | null,
  confirmed: boolean,
  busy = false,
): boolean {
  if (!detail || busy || !confirmed) return false;
  if (gc08ProcessingState(detail.transcription.status) !== 'ready') return false;
  const minutes = detail.minutes;
  return Boolean(
    minutes
      && minutes.publicationState === 'draft'
      && minutes.minutesMarkdown?.trim(),
  );
}

const RECORDING_SUFFIXES = new Set([
  '.aac',
  '.flac',
  '.m4a',
  '.mp3',
  '.mp4',
  '.ogg',
  '.wav',
  '.webm',
]);

export function gc08IsRecordingPath(value: string): boolean {
  const normalized = value.trim().toLowerCase();
  const dot = normalized.lastIndexOf('.');
  return Boolean(normalized && dot >= 0 && RECORDING_SUFFIXES.has(normalized.slice(dot)));
}
