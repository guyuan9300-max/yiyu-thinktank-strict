import { createContext, createElement, useContext } from 'react';
import type { Dispatch, ReactNode } from 'react';

import type {
  ChatMessage,
  ClientAnalysisRun,
  EvidenceItem,
  KnowledgeSearchResult,
  LinkMaterialCookieBrowser,
  LinkMaterialImportRun,
  PublicAnalysisPlan,
} from '../../shared/types';

export type WorkspaceDisplayChatMessage = ChatMessage & {
  requestPrompt?: string;
  elapsedMs?: number;
  imageAttachments?: Array<{
    id: string;
    name: string;
    mimeType: string;
    dataUrl?: string;
  }>;
};

export type WorkspacePendingQuestionState = {
  question: string;
  startedAt: string;
  deepThinking?: boolean;
  creativityMode?: 'creative' | 'balanced' | 'strict';
  publicAnalysisPlan?: PublicAnalysisPlan | null;
  stage?: 'preparing' | 'reasoning' | 'answering';
  reasoningText?: string;
  answerText?: string;
  reasoningElapsedMs?: number;
};

export type WorkspaceRightPanelEvidenceSnapshot = {
  messageId: string | null;
  evidence: EvidenceItem[];
  evidenceKey: string;
};

export type WorkspaceRightTabKey = 'overview' | 'files' | 'memory' | 'proposals' | 'tools';
export type WorkspaceSurfaceMode = 'setup' | 'workspace';
export type WorkspaceImportDropZone = 'buffer' | 'composer' | 'favorites' | null;
export type WorkspaceAnswerActionName =
  | 'vectorize'
  | 'cancel-vectorize'
  | 'remember'
  | 'revoke-memory'
  | 'export'
  | 'create-task'
  | 'request-evidence'
  | 'create-proposal'
  | 'promote-judgment'
  | '';

export type WorkspaceComposerFocusSnapshot = {
  key: string;
  focused: boolean;
  selectionStart: number | null;
  selectionEnd: number | null;
  updatedAt: number;
};

export const CLIENT_CHAT_DRAFT_THREAD_ID = '__client_chat_draft__';
export const WORKSPACE_COMPOSER_NO_CLIENT_KEY = '__workspace_no_client__';

export interface WorkspaceFileSearchUiState {
  query: string;
  submittedQuery: string;
  result: KnowledgeSearchResult | null;
  isSearching: boolean;
}

export interface WorkspaceLinkMaterialUiState {
  run: LinkMaterialImportRun | null;
  latestRun: LinkMaterialImportRun | null;
  useBrowserCookies: boolean;
  cookieBrowser: LinkMaterialCookieBrowser;
  isStarting: boolean;
}

export interface WorkspaceClientUiState {
  composerDraftByClient: Record<string, string>;
  composerFocusByClient: Record<string, WorkspaceComposerFocusSnapshot>;
  selectedThreadIdByClient: Record<string, string>;
  activeMessageIdByClient: Record<string, string>;
  surfaceModeByClient: Record<string, WorkspaceSurfaceMode>;
  activeRunByClient: Record<string, ClientAnalysisRun>;
  pendingQuestionByClient: Record<string, WorkspacePendingQuestionState>;
  optimisticMessagesByThread: Record<string, WorkspaceDisplayChatMessage[]>;
  threadMessagesById: Record<string, WorkspaceDisplayChatMessage[]>;
  threadMessagesLoadingId: string | null;
  startingMessageByClient: Record<string, boolean>;
  fileSearchByClient: Record<string, WorkspaceFileSearchUiState>;
  linkMaterialByClient: Record<string, WorkspaceLinkMaterialUiState>;
  rightPanelEvidenceSnapshotByClient: Record<string, WorkspaceRightPanelEvidenceSnapshot>;
  rightTabByClient: Record<string, WorkspaceRightTabKey>;
  answerActionStateByClient: Record<string, Record<string, WorkspaceAnswerActionName>>;
  templateFillStateByClient: Record<string, unknown>;
  textDocumentDraftByClient: Record<string, unknown>;
  linkMaterialDraftByClient: Record<string, unknown>;
  folderRecommendationStateByClient: Record<string, unknown>;
  documentAutoRepairStateByClient: Record<string, unknown>;
  importDropZoneByClient: Record<string, WorkspaceImportDropZone>;
  meetingDraftsByClient: Record<string, unknown>;
}

export const defaultWorkspaceFileSearchUiState: WorkspaceFileSearchUiState = {
  query: '',
  submittedQuery: '',
  result: null,
  isSearching: false,
};

export const defaultWorkspaceLinkMaterialUiState: WorkspaceLinkMaterialUiState = {
  run: null,
  latestRun: null,
  useBrowserCookies: false,
  cookieBrowser: 'firefox',
  isStarting: false,
};

export const initialWorkspaceClientUiState: WorkspaceClientUiState = {
  composerDraftByClient: {},
  composerFocusByClient: {},
  selectedThreadIdByClient: {},
  activeMessageIdByClient: {},
  surfaceModeByClient: {},
  activeRunByClient: {},
  pendingQuestionByClient: {},
  optimisticMessagesByThread: {},
  threadMessagesById: {},
  threadMessagesLoadingId: null,
  startingMessageByClient: {},
  fileSearchByClient: {},
  linkMaterialByClient: {},
  rightPanelEvidenceSnapshotByClient: {},
  rightTabByClient: {},
  answerActionStateByClient: {},
  templateFillStateByClient: {},
  textDocumentDraftByClient: {},
  linkMaterialDraftByClient: {},
  folderRecommendationStateByClient: {},
  documentAutoRepairStateByClient: {},
  importDropZoneByClient: {},
  meetingDraftsByClient: {},
};

export type WorkspaceClientUiAction =
  | { type: 'resetAll' }
  | { type: 'setComposerDraft'; clientKey: string; value: string }
  | { type: 'setComposerFocusSnapshot'; clientId: string; snapshot: WorkspaceComposerFocusSnapshot | null }
  | { type: 'setSelectedThreadId'; clientId: string; threadId: string | null }
  | { type: 'setActiveMessageId'; clientId: string; messageId: string | null }
  | { type: 'setSurfaceMode'; clientId: string; mode: WorkspaceSurfaceMode | null }
  | { type: 'setActiveRun'; clientId: string; run: ClientAnalysisRun | null }
  | { type: 'setPendingQuestion'; clientId: string; pending: WorkspacePendingQuestionState | null }
  | { type: 'setOptimisticMessages'; threadId: string; messages: WorkspaceDisplayChatMessage[] }
  | { type: 'replaceThreadMessagesById'; value: Record<string, WorkspaceDisplayChatMessage[]> }
  | { type: 'upsertThreadMessages'; threadId: string; messages: WorkspaceDisplayChatMessage[] }
  | { type: 'removeThreadMessages'; threadId: string; messageIds: string[] }
  | { type: 'setThreadMessagesLoadingId'; threadId: string | null }
  | { type: 'setStartingMessage'; clientId: string; isStarting: boolean }
  | { type: 'setFileSearchQuery'; clientId: string; query: string }
  | { type: 'setFileSearchSubmittedQuery'; clientId: string; submittedQuery: string }
  | { type: 'setFileSearchResult'; clientId: string; result: KnowledgeSearchResult | null }
  | { type: 'setFileSearchLoading'; clientId: string; isSearching: boolean }
  | { type: 'resetFileSearch'; clientId: string }
  | { type: 'setLinkMaterialRun'; clientId: string; run: LinkMaterialImportRun | null }
  | { type: 'setLatestLinkMaterialRun'; clientId: string; run: LinkMaterialImportRun | null }
  | { type: 'setLinkMaterialUseBrowserCookies'; clientId: string; useBrowserCookies: boolean }
  | { type: 'setLinkMaterialCookieBrowser'; clientId: string; cookieBrowser: LinkMaterialCookieBrowser }
  | { type: 'setLinkMaterialStarting'; clientId: string; isStarting: boolean }
  | { type: 'setRightPanelEvidenceSnapshot'; clientId: string; snapshot: WorkspaceRightPanelEvidenceSnapshot | null }
  | { type: 'setRightTab'; clientId: string; tab: WorkspaceRightTabKey }
  | { type: 'setAnswerActionState'; clientId: string; value: Record<string, WorkspaceAnswerActionName> }
  | { type: 'setTemplateFillState'; clientId: string; value: unknown | null }
  | { type: 'setTextDocumentDraft'; clientId: string; value: unknown | null }
  | { type: 'setLinkMaterialDraft'; clientId: string; value: unknown | null }
  | { type: 'setFolderRecommendationState'; clientId: string; value: unknown | null }
  | { type: 'setDocumentAutoRepairState'; clientId: string; value: unknown | null }
  | { type: 'setImportDropZone'; clientId: string; zone: WorkspaceImportDropZone }
  | { type: 'setMeetingDrafts'; clientId: string; value: unknown | null }
  | { type: 'resetClientEphemeralState'; clientId: string };

export interface WorkspaceClientStoreContextValue {
  state: WorkspaceClientUiState;
  dispatch: Dispatch<WorkspaceClientUiAction>;
}

const WorkspaceClientStoreContext = createContext<WorkspaceClientStoreContextValue | null>(null);

export function WorkspaceClientStoreProvider({
  state,
  dispatch,
  children,
}: WorkspaceClientStoreContextValue & { children: ReactNode }) {
  return createElement(WorkspaceClientStoreContext.Provider, { value: { state, dispatch } }, children);
}

export function useWorkspaceClientStore(): WorkspaceClientStoreContextValue {
  const value = useContext(WorkspaceClientStoreContext);
  if (!value) {
    throw new Error('useWorkspaceClientStore must be used within WorkspaceClientStoreProvider');
  }
  return value;
}

function mergeMessages(
  existingMessages: WorkspaceDisplayChatMessage[],
  incomingMessages: WorkspaceDisplayChatMessage[],
): WorkspaceDisplayChatMessage[] {
  const messageMap = new Map<string, WorkspaceDisplayChatMessage>();
  for (const item of existingMessages) {
    messageMap.set(item.id, item);
  }
  for (const item of incomingMessages) {
    messageMap.set(item.id, {
      ...(messageMap.get(item.id) || {}),
      ...item,
    });
  }
  const mergedMessages = Array.from(messageMap.values()).sort((left, right) => left.createdAt.localeCompare(right.createdAt));
  return areValuesEqual(existingMessages, mergedMessages) ? existingMessages : mergedMessages;
}

function removeKey<T>(record: Record<string, T>, key: string): Record<string, T> {
  if (!(key in record)) return record;
  const next = { ...record };
  delete next[key];
  return next;
}

function areValuesEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (left == null || right == null) return left === right;
  try {
    return JSON.stringify(left) === JSON.stringify(right);
  } catch {
    return false;
  }
}

function updateClientRecord<T>(
  record: Record<string, T>,
  clientId: string,
  nextValue: T,
  options: { defaultValue?: T } = {},
): Record<string, T> {
  const currentValue = record[clientId];
  if (currentValue === undefined && options.defaultValue !== undefined && areValuesEqual(options.defaultValue, nextValue)) {
    return record;
  }
  if (areValuesEqual(currentValue, nextValue)) return record;
  return {
    ...record,
    [clientId]: nextValue,
  };
}

export function getWorkspaceFileSearchState(state: WorkspaceClientUiState, clientId: string): WorkspaceFileSearchUiState {
  return state.fileSearchByClient[clientId] || defaultWorkspaceFileSearchUiState;
}

export function getWorkspaceLinkMaterialState(state: WorkspaceClientUiState, clientId: string): WorkspaceLinkMaterialUiState {
  return state.linkMaterialByClient[clientId] || defaultWorkspaceLinkMaterialUiState;
}

export function getWorkspaceRightTab(state: WorkspaceClientUiState, clientId: string): WorkspaceRightTabKey {
  const raw = state.rightTabByClient[clientId];
  // 兼容已撤下的旧标签：evidence 已并入 files；proposal 只作为
  // 底层待确认机制；tools 已并入顶部快捷工具区。
  if (
    !raw
    || (raw as string) === 'evidence'
    || (raw as string) === 'proposals'
    || (raw as string) === 'tools'
  ) return 'files';
  return raw;
}

export function workspaceClientUiReducer(
  state: WorkspaceClientUiState,
  action: WorkspaceClientUiAction,
): WorkspaceClientUiState {
  switch (action.type) {
    case 'resetAll':
      return initialWorkspaceClientUiState;

    case 'setComposerDraft':
      if ((state.composerDraftByClient[action.clientKey] || '') === action.value) return state;
      return {
        ...state,
        composerDraftByClient: action.value
          ? { ...state.composerDraftByClient, [action.clientKey]: action.value }
          : removeKey(state.composerDraftByClient, action.clientKey),
      };

    case 'setComposerFocusSnapshot':
      return {
        ...state,
        composerFocusByClient: action.snapshot
          ? { ...state.composerFocusByClient, [action.clientId]: action.snapshot }
          : removeKey(state.composerFocusByClient, action.clientId),
      };

    case 'setSelectedThreadId':
      return {
        ...state,
        selectedThreadIdByClient: action.threadId
          ? { ...state.selectedThreadIdByClient, [action.clientId]: action.threadId }
          : removeKey(state.selectedThreadIdByClient, action.clientId),
      };

    case 'setActiveMessageId':
      return {
        ...state,
        activeMessageIdByClient: action.messageId
          ? { ...state.activeMessageIdByClient, [action.clientId]: action.messageId }
          : removeKey(state.activeMessageIdByClient, action.clientId),
      };

    case 'setSurfaceMode':
      return {
        ...state,
        surfaceModeByClient: action.mode
          ? { ...state.surfaceModeByClient, [action.clientId]: action.mode }
          : removeKey(state.surfaceModeByClient, action.clientId),
      };

    case 'setActiveRun':
      if (areValuesEqual(state.activeRunByClient[action.clientId] ?? null, action.run)) return state;
      return {
        ...state,
        activeRunByClient: action.run
          ? { ...state.activeRunByClient, [action.clientId]: action.run }
          : removeKey(state.activeRunByClient, action.clientId),
      };

    case 'setPendingQuestion':
      if (areValuesEqual(state.pendingQuestionByClient[action.clientId] ?? null, action.pending)) return state;
      return {
        ...state,
        pendingQuestionByClient: action.pending
          ? { ...state.pendingQuestionByClient, [action.clientId]: action.pending }
          : removeKey(state.pendingQuestionByClient, action.clientId),
      };

    case 'setOptimisticMessages':
      if (areValuesEqual(state.optimisticMessagesByThread[action.threadId] || [], action.messages)) return state;
      return {
        ...state,
        optimisticMessagesByThread: action.messages.length
          ? { ...state.optimisticMessagesByThread, [action.threadId]: action.messages }
          : removeKey(state.optimisticMessagesByThread, action.threadId),
      };

    case 'replaceThreadMessagesById':
      if (areValuesEqual(state.threadMessagesById, action.value)) return state;
      return {
        ...state,
        threadMessagesById: action.value,
      };

    case 'upsertThreadMessages': {
      const currentMessages = state.threadMessagesById[action.threadId] || [];
      const mergedMessages = mergeMessages(currentMessages, action.messages);
      if (mergedMessages === currentMessages) return state;
      return {
        ...state,
        threadMessagesById: {
          ...state.threadMessagesById,
          [action.threadId]: mergedMessages,
        },
      };
    }

    case 'removeThreadMessages': {
      const removalIds = new Set(action.messageIds);
      if (removalIds.size === 0) return state;
      // 删除时必须同时扫 threadMessagesById（来自 DB 的消息）和 optimisticMessagesByThread
      // （前端兜底/未确认消息）—— 之前只看 store 不看 optimistic 导致"失败提示删不掉"。
      const currentMessages = state.threadMessagesById[action.threadId] || [];
      const remaining = currentMessages.filter((message) => !removalIds.has(message.id));
      const storeChanged = remaining.length !== currentMessages.length;

      const currentOptimistic = state.optimisticMessagesByThread[action.threadId] || [];
      const remainingOptimistic = currentOptimistic.filter((message) => !removalIds.has(message.id));
      const optimisticChanged = remainingOptimistic.length !== currentOptimistic.length;

      if (!storeChanged && !optimisticChanged) return state;

      const nextThreadMessagesById = storeChanged
        ? (remaining.length
            ? { ...state.threadMessagesById, [action.threadId]: remaining }
            : removeKey(state.threadMessagesById, action.threadId))
        : state.threadMessagesById;
      const nextOptimistic = optimisticChanged
        ? (remainingOptimistic.length
            ? { ...state.optimisticMessagesByThread, [action.threadId]: remainingOptimistic }
            : removeKey(state.optimisticMessagesByThread, action.threadId))
        : state.optimisticMessagesByThread;
      return {
        ...state,
        threadMessagesById: nextThreadMessagesById,
        optimisticMessagesByThread: nextOptimistic,
      };
    }

    case 'setThreadMessagesLoadingId':
      if (state.threadMessagesLoadingId === action.threadId) return state;
      return {
        ...state,
        threadMessagesLoadingId: action.threadId,
      };

    case 'setStartingMessage':
      if (Boolean(state.startingMessageByClient[action.clientId]) === action.isStarting) return state;
      return {
        ...state,
        startingMessageByClient: action.isStarting
          ? { ...state.startingMessageByClient, [action.clientId]: true }
          : removeKey(state.startingMessageByClient, action.clientId),
      };

    case 'setFileSearchQuery': {
      const current = getWorkspaceFileSearchState(state, action.clientId);
      return {
        ...state,
        fileSearchByClient: updateClientRecord(
          state.fileSearchByClient,
          action.clientId,
          { ...current, query: action.query },
          { defaultValue: defaultWorkspaceFileSearchUiState },
        ),
      };
    }

    case 'setFileSearchSubmittedQuery': {
      const current = getWorkspaceFileSearchState(state, action.clientId);
      return {
        ...state,
        fileSearchByClient: updateClientRecord(
          state.fileSearchByClient,
          action.clientId,
          { ...current, submittedQuery: action.submittedQuery },
          { defaultValue: defaultWorkspaceFileSearchUiState },
        ),
      };
    }

    case 'setFileSearchResult': {
      const current = getWorkspaceFileSearchState(state, action.clientId);
      return {
        ...state,
        fileSearchByClient: updateClientRecord(
          state.fileSearchByClient,
          action.clientId,
          { ...current, result: action.result },
          { defaultValue: defaultWorkspaceFileSearchUiState },
        ),
      };
    }

    case 'setFileSearchLoading': {
      const current = getWorkspaceFileSearchState(state, action.clientId);
      return {
        ...state,
        fileSearchByClient: updateClientRecord(
          state.fileSearchByClient,
          action.clientId,
          { ...current, isSearching: action.isSearching },
          { defaultValue: defaultWorkspaceFileSearchUiState },
        ),
      };
    }

    case 'resetFileSearch':
      return {
        ...state,
        fileSearchByClient: removeKey(state.fileSearchByClient, action.clientId),
      };

    case 'setLinkMaterialRun': {
      const current = getWorkspaceLinkMaterialState(state, action.clientId);
      return {
        ...state,
        linkMaterialByClient: updateClientRecord(
          state.linkMaterialByClient,
          action.clientId,
          { ...current, run: action.run },
          { defaultValue: defaultWorkspaceLinkMaterialUiState },
        ),
      };
    }

    case 'setLatestLinkMaterialRun': {
      const current = getWorkspaceLinkMaterialState(state, action.clientId);
      return {
        ...state,
        linkMaterialByClient: updateClientRecord(
          state.linkMaterialByClient,
          action.clientId,
          { ...current, latestRun: action.run },
          { defaultValue: defaultWorkspaceLinkMaterialUiState },
        ),
      };
    }

    case 'setLinkMaterialUseBrowserCookies': {
      const current = getWorkspaceLinkMaterialState(state, action.clientId);
      return {
        ...state,
        linkMaterialByClient: updateClientRecord(
          state.linkMaterialByClient,
          action.clientId,
          { ...current, useBrowserCookies: action.useBrowserCookies },
          { defaultValue: defaultWorkspaceLinkMaterialUiState },
        ),
      };
    }

    case 'setLinkMaterialCookieBrowser': {
      const current = getWorkspaceLinkMaterialState(state, action.clientId);
      return {
        ...state,
        linkMaterialByClient: updateClientRecord(
          state.linkMaterialByClient,
          action.clientId,
          { ...current, cookieBrowser: action.cookieBrowser },
          { defaultValue: defaultWorkspaceLinkMaterialUiState },
        ),
      };
    }

    case 'setLinkMaterialStarting': {
      const current = getWorkspaceLinkMaterialState(state, action.clientId);
      return {
        ...state,
        linkMaterialByClient: updateClientRecord(
          state.linkMaterialByClient,
          action.clientId,
          { ...current, isStarting: action.isStarting },
          { defaultValue: defaultWorkspaceLinkMaterialUiState },
        ),
      };
    }

    case 'setRightPanelEvidenceSnapshot':
      if (areValuesEqual(state.rightPanelEvidenceSnapshotByClient[action.clientId] ?? null, action.snapshot)) return state;
      return {
        ...state,
        rightPanelEvidenceSnapshotByClient: action.snapshot
          ? { ...state.rightPanelEvidenceSnapshotByClient, [action.clientId]: action.snapshot }
          : removeKey(state.rightPanelEvidenceSnapshotByClient, action.clientId),
      };

    case 'setRightTab': {
      // 兼容老 state：以前持久化过 'evidence' 的客户在升级后自动迁移到 'files'，
      // 后者承担了原引证视图（作为过滤模式）。
      const nextTab: WorkspaceRightTabKey =
        ['evidence', 'tools', 'proposals'].includes(action.tab as string) ? 'files' : action.tab;
      return {
        ...state,
        rightTabByClient: { ...state.rightTabByClient, [action.clientId]: nextTab },
      };
    }

    case 'setAnswerActionState':
      if (areValuesEqual(state.answerActionStateByClient[action.clientId] || {}, action.value)) return state;
      return {
        ...state,
        answerActionStateByClient: Object.keys(action.value).length
          ? { ...state.answerActionStateByClient, [action.clientId]: action.value }
          : removeKey(state.answerActionStateByClient, action.clientId),
      };

    case 'setTemplateFillState':
      if (areValuesEqual(state.templateFillStateByClient[action.clientId] ?? null, action.value)) return state;
      return {
        ...state,
        templateFillStateByClient: action.value == null
          ? removeKey(state.templateFillStateByClient, action.clientId)
          : { ...state.templateFillStateByClient, [action.clientId]: action.value },
      };

    case 'setTextDocumentDraft':
      if (areValuesEqual(state.textDocumentDraftByClient[action.clientId] ?? null, action.value)) return state;
      return {
        ...state,
        textDocumentDraftByClient: action.value == null
          ? removeKey(state.textDocumentDraftByClient, action.clientId)
          : { ...state.textDocumentDraftByClient, [action.clientId]: action.value },
      };

    case 'setLinkMaterialDraft':
      if (areValuesEqual(state.linkMaterialDraftByClient[action.clientId] ?? null, action.value)) return state;
      return {
        ...state,
        linkMaterialDraftByClient: action.value == null
          ? removeKey(state.linkMaterialDraftByClient, action.clientId)
          : { ...state.linkMaterialDraftByClient, [action.clientId]: action.value },
      };

    case 'setFolderRecommendationState':
      if (areValuesEqual(state.folderRecommendationStateByClient[action.clientId] ?? null, action.value)) return state;
      return {
        ...state,
        folderRecommendationStateByClient: action.value == null
          ? removeKey(state.folderRecommendationStateByClient, action.clientId)
          : { ...state.folderRecommendationStateByClient, [action.clientId]: action.value },
      };

    case 'setDocumentAutoRepairState':
      if (areValuesEqual(state.documentAutoRepairStateByClient[action.clientId] ?? null, action.value)) return state;
      return {
        ...state,
        documentAutoRepairStateByClient: action.value == null
          ? removeKey(state.documentAutoRepairStateByClient, action.clientId)
          : { ...state.documentAutoRepairStateByClient, [action.clientId]: action.value },
      };

    case 'setImportDropZone':
      if ((state.importDropZoneByClient[action.clientId] || null) === action.zone) return state;
      return {
        ...state,
        importDropZoneByClient: action.zone
          ? { ...state.importDropZoneByClient, [action.clientId]: action.zone }
          : removeKey(state.importDropZoneByClient, action.clientId),
      };

    case 'setMeetingDrafts':
      if (areValuesEqual(state.meetingDraftsByClient[action.clientId] ?? null, action.value)) return state;
      return {
        ...state,
        meetingDraftsByClient: action.value == null
          ? removeKey(state.meetingDraftsByClient, action.clientId)
          : { ...state.meetingDraftsByClient, [action.clientId]: action.value },
      };

    case 'resetClientEphemeralState':
      return {
        ...state,
        selectedThreadIdByClient: removeKey(state.selectedThreadIdByClient, action.clientId),
        activeMessageIdByClient: removeKey(state.activeMessageIdByClient, action.clientId),
        activeRunByClient: removeKey(state.activeRunByClient, action.clientId),
        pendingQuestionByClient: removeKey(state.pendingQuestionByClient, action.clientId),
        startingMessageByClient: removeKey(state.startingMessageByClient, action.clientId),
        rightPanelEvidenceSnapshotByClient: removeKey(state.rightPanelEvidenceSnapshotByClient, action.clientId),
      };

    default:
      return state;
  }
}
