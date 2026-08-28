export type MiniAiQuickTaskStage = 'idle' | 'ai-input' | 'task-editor' | 'saving';

export type MiniAiQuickTaskEvent =
  | 'open'
  | 'parsed'
  | 'dismissed'
  | 'save-started'
  | 'save-succeeded'
  | 'save-failed'
  | 'abandoned';

export type MiniAiQuickTaskTransition = {
  stage: MiniAiQuickTaskStage;
  shouldReturnToMini: boolean;
};

/**
 * One-shot workflow for AI task creation launched from the mini panel.
 * A delayed or unrelated save may never collapse the window: only the
 * matching saving stage can consume save-succeeded and request mini mode.
 */
export function transitionMiniAiQuickTaskFlow(
  stage: MiniAiQuickTaskStage,
  event: MiniAiQuickTaskEvent,
): MiniAiQuickTaskTransition {
  if (event === 'open') return { stage: 'ai-input', shouldReturnToMini: false };
  if (event === 'dismissed' || event === 'abandoned') {
    return { stage: 'idle', shouldReturnToMini: false };
  }
  if (event === 'parsed' && stage === 'ai-input') {
    return { stage: 'task-editor', shouldReturnToMini: false };
  }
  if (event === 'save-started' && stage === 'task-editor') {
    return { stage: 'saving', shouldReturnToMini: false };
  }
  if (event === 'save-failed' && stage === 'saving') {
    return { stage: 'task-editor', shouldReturnToMini: false };
  }
  if (event === 'save-succeeded' && stage === 'saving') {
    return { stage: 'idle', shouldReturnToMini: true };
  }
  return { stage, shouldReturnToMini: false };
}
