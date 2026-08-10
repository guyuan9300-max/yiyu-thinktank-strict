import assert from 'node:assert/strict';
import test from 'node:test';

import { renderToStaticMarkup } from 'react-dom/server';

import { gc08Api } from './gc08Api';
import {
  gc08CanPublish,
  gc08CanRetryTranscription,
  gc08ProcessingState,
  gc08StatePresentation,
  type GC08RecordingDetail,
} from './gc08Contract';
import { GC08MeetingMediaPanel } from './GC08MeetingMediaPanel';

function detail(status: string, publicationState: 'draft' | 'published' = 'draft'): GC08RecordingDetail {
  return {
    clientId: 'client-1',
    meetingId: 'meeting-1',
    recordingId: 'recording-1',
    recordingState: 'transcribed',
    durationMs: 42000,
    capturedAt: '2026-08-07T09:00:00Z',
    transcription: {
      transcriptionId: status === 'ready' ? 'transcription-1' : null,
      version: status === 'ready' ? 1 : null,
      status,
      language: 'zh',
      integrityHash: status === 'ready' ? 'a'.repeat(64) : null,
      errorCode: status === 'ready' ? null : 'local_audio_asr_not_connected',
      message: null,
      retryable: status === 'failed_retryable',
    },
    minutes: status === 'ready' ? {
      documentId: 'minutes-1',
      documentVersionId: 'minutes-version-1',
      version: 1,
      title: '正式纪要',
      publicationState,
      contentHash: 'b'.repeat(64),
      minutesMarkdown: '# 正式纪要\n\n已人工核对。',
      receipt: {},
    } : null,
    minutesProcessing: {
      status: status === 'ready' ? 'ready' : 'not_requested',
      errorCode: null,
      message: null,
      retryable: false,
    },
  };
}

test('GC-08 never presents blocked, retryable failure or unknown state as ready', () => {
  assert.equal(gc08StatePresentation('blocked').tone, 'warning');
  assert.equal(gc08StatePresentation('failed_retryable').tone, 'error');
  assert.equal(gc08StatePresentation('ready').tone, 'success');
  assert.equal(gc08ProcessingState('unexpected_success'), 'unknown');
  assert.equal(gc08CanRetryTranscription('blocked'), true);
  assert.equal(gc08CanRetryTranscription('failed_retryable'), true);
});

test('GC-08 formal publication requires a real ready transcript, draft body and explicit confirmation', () => {
  assert.equal(gc08CanPublish(detail('blocked'), true), false);
  assert.equal(gc08CanPublish(detail('ready'), false), false);
  assert.equal(gc08CanPublish(detail('ready'), true), true);
  assert.equal(gc08CanPublish(detail('ready', 'published'), true), false);
  assert.equal(gc08CanPublish(detail('ready'), true, true), false);
});

test('GC-08 API uses detached local routes, desktop token and stable publish receipt', async () => {
  const originalWindow = globalThis.window;
  const originalFetch = globalThis.fetch;
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const responseDetail = detail('ready');
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: {
      yiyuWorkbench: {
        backendBaseUrl: 'http://127.0.0.1:47829',
        desktopToken: 'desktop-token',
      },
    },
  });
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(input), init });
    const payload = calls.length === 4
      ? { state: 'published', cloud: { publicationState: 'published' }, local: { ...responseDetail, minutes: { ...responseDetail.minutes!, publicationState: 'published' } } }
      : responseDetail;
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  }) as typeof fetch;
  try {
    await gc08Api.registerRecording('client/一', 'meeting-1', { audioPath: '/local/meeting.m4a' });
    await gc08Api.transcribe('client/一', 'meeting-1', 'recording-1', { force: true });
    await gc08Api.createMinutesDraft('client/一', 'meeting-1', 'recording-1', { minutesMarkdown: '# 纪要' });
    await gc08Api.publishMinutes('client/一', 'meeting-1', 'recording-1', { expectedVersion: 0 }, 'stable-publish-key');
  } finally {
    globalThis.fetch = originalFetch;
    Object.defineProperty(globalThis, 'window', { configurable: true, value: originalWindow });
  }
  assert.equal(calls.length, 4);
  assert.match(calls[0].url, /\/api\/v2\/ui\/clients\/client%2F%E4%B8%80\/meetings\/meeting-1\/recordings$/);
  assert.match(calls[1].url, /\/recording-1\/transcriptions$/);
  assert.match(calls[2].url, /\/recording-1\/minutes\/draft$/);
  assert.match(calls[3].url, /\/recording-1\/minutes\/publish$/);
  assert.equal(new Headers(calls[0].init?.headers).get('X-Yiyu-Desktop-Token'), 'desktop-token');
  assert.equal(new Headers(calls[3].init?.headers).get('Idempotency-Key'), 'stable-publish-key');
  assert.deepEqual(JSON.parse(String(calls[1].init?.body)), { force: true });
  assert.deepEqual(JSON.parse(String(calls[3].init?.body)), { expectedVersion: 0 });
});

test('GC-08 panel exposes clickable registration, retry, draft and explicit publish controls', () => {
  const html = renderToStaticMarkup(
    <GC08MeetingMediaPanel clientId="client-1" meetingId="meeting-1" />,
  );
  assert.match(html, /选择本机录音/);
  assert.match(html, /登记到当前会议/);
  assert.match(html, /开始本机转写/);
  assert.match(html, /生成并保存纪要草稿/);
  assert.match(html, /我已人工核对纪要正文与证据/);
  assert.match(html, /明确发布正式纪要/);
  assert.match(html, /原录音、完整转写和本机路径只保存在当前设备/);
});

test('GC-08 compact editor panel only exposes local files and ASR setup guidance', () => {
  const html = renderToStaticMarkup(
    <GC08MeetingMediaPanel
      clientId="client-1"
      meetingId="meeting-1"
      compact
      onOpenAsrSettings={() => undefined}
    />,
  );
  assert.match(html, /录音原件/);
  assert.match(html, /转写文件/);
  assert.match(html, /导入录音/);
  assert.doesNotMatch(html, /生成并保存纪要草稿/);
  assert.doesNotMatch(html, /明确发布正式纪要/);
});
