import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import type { ClientAnalysisRun, KnowledgeSearchResult, LinkMaterialImportRun } from '../../shared/types';
import {
  initialWorkspaceClientUiState,
  workspaceClientUiReducer,
} from './workspaceClientUiStore';
import type { WorkspaceClientUiAction } from './workspaceClientUiStore';

const appSourcePath = fileURLToPath(new URL('../App.tsx', import.meta.url));

function run(overrides: Partial<ClientAnalysisRun>): ClientAnalysisRun {
  return {
    id: 'run-1',
    clientId: 'client-a',
    threadId: 'thread-a',
    userMessageId: 'user-a',
    assistantMessageId: 'assistant-a',
    question: '介绍客户',
    status: 'running',
    phase: 'generating_long_answer',
    progress: 40,
    progressFloor: 20,
    progressCeiling: 90,
    stageLabel: '正在生成',
    elapsedMs: 1000,
    evidenceSummary: {
      summaryText: '',
      masterHitCount: 0,
      surrogateHitCount: 0,
      rawChunkHitCount: 0,
      drillthroughUsed: false,
      coveredCategories: [],
      missingCategories: [],
      evidenceList: [],
    },
    longAnswerStatus: 'pending',
    summaryStatus: 'pending',
    answerMode: null,
    llmInvoked: false,
    timing: {},
    failureReason: null,
    assistantMessage: null,
    createdAt: '2026-05-03T00:00:00.000Z',
    updatedAt: '2026-05-03T00:00:01.000Z',
    ...overrides,
  };
}

test('workspace UI store isolates composer drafts by client', () => {
  const state = workspaceClientUiReducer(
    workspaceClientUiReducer(initialWorkspaceClientUiState, {
      type: 'setComposerDraft',
      clientKey: 'client-a',
      value: '测试机构A问题',
    }),
    {
      type: 'setComposerDraft',
      clientKey: 'client-b',
      value: '测试论坛A 问题',
    },
  );

  assert.equal(state.composerDraftByClient['client-a'], '测试机构A问题');
  assert.equal(state.composerDraftByClient['client-b'], '测试论坛A 问题');
});

test('workspace UI store keeps active runs and pending questions client scoped', () => {
  const state = [
    { type: 'setActiveRun' as const, clientId: 'client-a', run: run({ id: 'run-a', clientId: 'client-a' }) },
    { type: 'setActiveRun' as const, clientId: 'client-b', run: run({ id: 'run-b', clientId: 'client-b' }) },
    { type: 'setPendingQuestion' as const, clientId: 'client-a', pending: { question: 'A', startedAt: 't1' } },
  ].reduce(workspaceClientUiReducer, initialWorkspaceClientUiState);

  assert.equal(state.activeRunByClient['client-a'].id, 'run-a');
  assert.equal(state.activeRunByClient['client-b'].id, 'run-b');
  assert.equal(state.pendingQuestionByClient['client-a'].question, 'A');
  assert.equal(state.pendingQuestionByClient['client-b'], undefined);
});

test('workspace UI store merges thread messages without dropping existing messages', () => {
  const state = [
    {
      type: 'upsertThreadMessages' as const,
      threadId: 'thread-a',
      messages: [{ id: 'm1', threadId: 'thread-a', role: 'user' as const, content: 'Q', createdAt: '2026-05-03T00:00:00.000Z', status: 'success' as const, evidence: [] }],
    },
    {
      type: 'upsertThreadMessages' as const,
      threadId: 'thread-a',
      messages: [{ id: 'm2', threadId: 'thread-a', role: 'assistant' as const, content: 'A', createdAt: '2026-05-03T00:00:01.000Z', status: 'success' as const, evidence: [] }],
    },
  ].reduce(workspaceClientUiReducer, initialWorkspaceClientUiState);

  assert.deepEqual(state.threadMessagesById['thread-a'].map((item) => item.id), ['m1', 'm2']);
});

test('workspace UI store ignores unchanged thread message payloads', () => {
  const message = {
    id: 'm1',
    threadId: 'thread-a',
    role: 'user' as const,
    content: 'Q',
    createdAt: '2026-05-03T00:00:00.000Z',
    status: 'success' as const,
    evidence: [],
  };
  const state = workspaceClientUiReducer(initialWorkspaceClientUiState, {
    type: 'upsertThreadMessages',
    threadId: 'thread-a',
    messages: [message],
  });
  const nextState = workspaceClientUiReducer(state, {
    type: 'upsertThreadMessages',
    threadId: 'thread-a',
    messages: [{ ...message }],
  });

  assert.equal(nextState, state);
  assert.equal(nextState.threadMessagesById['thread-a'], state.threadMessagesById['thread-a']);
});

test('workspace UI store persists file search and link import state per client', () => {
  const searchResult = {
    searchId: 'search-1',
    clientId: 'client-a',
    query: '合同',
    coverage: 1,
    matchedTerms: ['合同'],
    masterHitCount: 1,
    surrogateHitCount: 0,
    rawChunkHitCount: 0,
    drillthroughUsed: false,
    hits: [],
  } satisfies KnowledgeSearchResult;
  const linkRun = {
    runId: 'link-run-1',
    clientId: 'client-a',
    sourcePlatform: 'bilibili',
    sourceUrl: 'https://www.bilibili.com/video/BV1',
    title: '视频',
    status: 'running',
    stage: '下载临时媒体中',
    progress: 20,
    documentId: null,
    documentPath: null,
    mediaCacheStatus: 'not_downloaded',
    error: null,
    metadata: {},
    createdAt: '2026-05-03T00:00:00.000Z',
    updatedAt: '2026-05-03T00:00:01.000Z',
  } satisfies LinkMaterialImportRun;

  const state = [
    { type: 'setFileSearchQuery' as const, clientId: 'client-a', query: '合同' },
    { type: 'setFileSearchResult' as const, clientId: 'client-a', result: searchResult },
    { type: 'setLinkMaterialRun' as const, clientId: 'client-a', run: linkRun },
  ].reduce(workspaceClientUiReducer, initialWorkspaceClientUiState);

  assert.equal(state.fileSearchByClient['client-a'].query, '合同');
  assert.equal(state.fileSearchByClient['client-a'].result?.searchId, 'search-1');
  assert.equal(state.linkMaterialByClient['client-a'].run?.runId, 'link-run-1');
  assert.equal(state.fileSearchByClient['client-b'], undefined);
});

test('workspace UI store ignores unchanged default link material latest run', () => {
  const nextState = workspaceClientUiReducer(initialWorkspaceClientUiState, {
    type: 'setLatestLinkMaterialRun',
    clientId: 'client-a',
    run: null,
  });

  assert.equal(nextState, initialWorkspaceClientUiState);
  assert.equal(nextState.linkMaterialByClient['client-a'], undefined);
});

test('workspace UI store ignores unchanged link material latest run payloads', () => {
  const linkRun = {
    runId: 'link-run-1',
    clientId: 'client-a',
    sourcePlatform: 'bilibili',
    sourceUrl: 'https://www.bilibili.com/video/BV1',
    title: '视频',
    status: 'completed',
    stage: '完成',
    progress: 100,
    documentId: 'doc-1',
    documentPath: '/tmp/doc.md',
    mediaCacheStatus: 'cleaned',
    error: null,
    metadata: { pipelineMode: 'media_first' },
    createdAt: '2026-05-03T00:00:00.000Z',
    updatedAt: '2026-05-03T00:00:01.000Z',
  } satisfies LinkMaterialImportRun;
  const state = workspaceClientUiReducer(initialWorkspaceClientUiState, {
    type: 'setLatestLinkMaterialRun',
    clientId: 'client-a',
    run: linkRun,
  });
  const nextState = workspaceClientUiReducer(state, {
    type: 'setLatestLinkMaterialRun',
    clientId: 'client-a',
    run: { ...linkRun, metadata: { pipelineMode: 'media_first' } },
  });

  assert.equal(nextState, state);
  assert.equal(nextState.linkMaterialByClient['client-a'], state.linkMaterialByClient['client-a']);
});

test('workspace UI store persists selected thread, active message and surface mode per client', () => {
  const state = [
    { type: 'setSelectedThreadId' as const, clientId: 'client-a', threadId: 'thread-a' },
    { type: 'setSelectedThreadId' as const, clientId: 'client-b', threadId: 'thread-b' },
    { type: 'setActiveMessageId' as const, clientId: 'client-a', messageId: 'message-a' },
    { type: 'setSurfaceMode' as const, clientId: 'client-a', mode: 'setup' as const },
  ].reduce(workspaceClientUiReducer, initialWorkspaceClientUiState);

  assert.equal(state.selectedThreadIdByClient['client-a'], 'thread-a');
  assert.equal(state.selectedThreadIdByClient['client-b'], 'thread-b');
  assert.equal(state.activeMessageIdByClient['client-a'], 'message-a');
  assert.equal(state.surfaceModeByClient['client-a'], 'setup');
  assert.equal(state.activeMessageIdByClient['client-b'], undefined);
});

test('workspace UI store persists dialog and tool drafts per client', () => {
  const actions: WorkspaceClientUiAction[] = [
    { type: 'setAnswerActionState' as const, clientId: 'client-a', value: { message_1: 'vectorize' as const } },
    { type: 'setTextDocumentDraft' as const, clientId: 'client-a', value: { title: '文档', content: '内容' } },
    { type: 'setLinkMaterialDraft' as const, clientId: 'client-a', value: { url: 'https://example.com' } },
    { type: 'setFolderRecommendationState' as const, clientId: 'client-a', value: { open: true } },
    { type: 'setDocumentAutoRepairState' as const, clientId: 'client-a', value: { previewId: 'preview-1' } },
    { type: 'setImportDropZone' as const, clientId: 'client-a', zone: 'composer' as const },
    { type: 'setMeetingDrafts' as const, clientId: 'client-a', value: { meetingTitle: '周会' } },
  ];
  const state = actions.reduce(workspaceClientUiReducer, initialWorkspaceClientUiState);

  assert.equal(state.answerActionStateByClient['client-a'].message_1, 'vectorize');
  assert.deepEqual(state.textDocumentDraftByClient['client-a'], { title: '文档', content: '内容' });
  assert.deepEqual(state.linkMaterialDraftByClient['client-a'], { url: 'https://example.com' });
  assert.deepEqual(state.folderRecommendationStateByClient['client-a'], { open: true });
  assert.deepEqual(state.documentAutoRepairStateByClient['client-a'], { previewId: 'preview-1' });
  assert.equal(state.importDropZoneByClient['client-a'], 'composer');
  assert.deepEqual(state.meetingDraftsByClient['client-a'], { meetingTitle: '周会' });
  assert.equal(state.importDropZoneByClient['client-b'], undefined);
});

test('client workspace route no longer forces remount by current client key', () => {
  const appSource = readFileSync(appSourcePath, 'utf8');

  assert.equal(appSource.includes("key={currentClientId || 'no-client'}"), false);
  assert.equal(appSource.includes('useReducer(\n    workspaceClientUiReducer'), true);
  assert.equal(appSource.includes('const ClientWorkspaceView ='), false);
  assert.equal(appSource.includes('<ClientWorkspaceView>{renderClientWorkspaceView}</ClientWorkspaceView>'), true);
});
