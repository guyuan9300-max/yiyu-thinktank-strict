import type { ReactNode } from 'react';
import { FileAudio, FileText, FolderOpen, Settings, X } from 'lucide-react';

export interface TaskMediaItem {
  id: string;
  title: string;
  path?: string | null;
  isAudio?: boolean;
  processingStatus?: 'not_requested' | 'queued' | 'processing' | 'ready' | 'failed' | 'failed_retryable' | 'blocked' | null;
  processingError?: string | null;
  processingProgress?: number | null;
  processingStage?: string | null;
  transcriptAttachmentId?: string | null;
  transcriptPath?: string | null;
  isTranscriptProjection?: boolean;
  cloudMetadataState?: string | null;
  pending?: boolean;
  pendingIndex?: number;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface TaskMediaPanelProps {
  attachments: TaskMediaItem[];
  disabled?: boolean;
  asrInstalled?: boolean | null;
  uploadProgress?: { currentFileName: string; percent: number } | null;
  notice?: string | null;
  headerActions?: ReactNode;
  onOpenAttachment: (attachment: TaskMediaItem) => void;
  onRevealAttachment: (attachment: TaskMediaItem) => void;
  onRevealTranscript: (attachment: TaskMediaItem) => void;
  onTranscribe: (attachment: TaskMediaItem) => void;
  onOpenTranscript: (attachment: TaskMediaItem) => void;
  onDelete: (attachment: TaskMediaItem) => void;
  onOpenAsrSettings?: () => void;
}

function fileName(path: string | null | undefined, fallback: string): string {
  return path?.split(/[/\\]/).pop() || fallback;
}

function normalizedStem(value: string | null | undefined): string {
  return fileName(value, value || '')
    .replace(/\.[^.]+$/, '')
    .replace(/\s+/g, '')
    .toLocaleLowerCase();
}

function isRunning(status: string | null | undefined): boolean {
  return status === 'queued' || status === 'processing';
}

function isRetryable(status: string | null | undefined): boolean {
  return status === 'failed' || status === 'failed_retryable';
}

export function TaskMediaPanel({
  attachments,
  disabled = false,
  asrInstalled = null,
  uploadProgress = null,
  notice = null,
  headerActions,
  onOpenAttachment,
  onRevealAttachment,
  onRevealTranscript,
  onTranscribe,
  onOpenTranscript,
  onDelete,
  onOpenAsrSettings,
}: TaskMediaPanelProps) {
  const recordings = attachments.filter((item) => item.isAudio);
  const activeRecording = [...recordings]
    .sort((left, right) => {
      const pendingOrder = Number(Boolean(left.pending)) - Number(Boolean(right.pending));
      if (pendingOrder !== 0) return pendingOrder;
      const leftTime = Date.parse(left.updatedAt || left.createdAt || '') || 0;
      const rightTime = Date.parse(right.updatedAt || right.createdAt || '') || 0;
      return leftTime - rightTime || left.id.localeCompare(right.id);
    })
    .at(-1) || null;
  const transcriptAttachmentIds = new Set(
    recordings.map((item) => item.transcriptAttachmentId).filter((id): id is string => Boolean(id)),
  );
  const recordingStems = new Set(
    recordings.flatMap((item) => [normalizedStem(item.title), normalizedStem(item.path)]).filter(Boolean),
  );
  const materials = attachments.filter(
    (item) => {
      if (item.isAudio || item.isTranscriptProjection || transcriptAttachmentIds.has(item.id)) return false;
      const stem = normalizedStem(item.title || item.path);
      return ![...recordingStems].some((recordingStem) => stem === `${recordingStem}-录音转写`);
    },
  );

  return (
    <section className="rounded-xl border border-slate-200 bg-slate-50/70 px-3 py-2.5" aria-label="任务录音、转写与附件">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-bold text-slate-700">录音与转写</p>
        <div className="flex flex-wrap items-center justify-end gap-1.5">
          {uploadProgress && (
            <span className="max-w-[180px] truncate text-[10px] font-bold text-blue-600">
              上传中 {uploadProgress.percent}% · {uploadProgress.currentFileName}
            </span>
          )}
          {headerActions}
        </div>
      </div>

      <div className="mt-2 grid gap-1.5">
        {activeRecording === null ? (
          <>
            <div className="flex min-w-0 items-center gap-2 rounded-lg bg-white px-2.5 py-2 text-[11px] text-slate-400">
              <FileAudio size={13} className="shrink-0" />
              <span className="shrink-0 font-bold">录音原件</span>
              <span className="min-w-0 flex-1 truncate">文件不存在</span>
            </div>
            <div className="flex min-w-0 items-center gap-2 rounded-lg bg-white px-2.5 py-2 text-[11px] text-slate-400">
              <FileText size={13} className="shrink-0" />
              <span className="shrink-0 font-bold">转写文件</span>
              <span className="min-w-0 flex-1 truncate">文件不存在</span>
            </div>
          </>
        ) : (() => {
          const recording = activeRecording;
          const status = recording.processingStatus || 'not_requested';
          const progress = Math.max(0, Math.min(100, Number(recording.processingProgress || 0)));
          const needsSetup = asrInstalled === false && !recording.transcriptPath && !isRunning(status);
          return (
            <div key={recording.id} className="grid gap-1.5">
              <div className="flex min-w-0 items-center gap-2 rounded-lg bg-white px-2.5 py-2 text-[11px] text-slate-600">
                <FileAudio size={13} className="shrink-0" />
                <span className="shrink-0 font-bold">录音原件</span>
                <button
                  type="button"
                  onClick={() => onOpenAttachment(recording)}
                  disabled={disabled || recording.pending || !recording.path}
                  title="播放录音"
                  className="min-w-0 flex-1 truncate text-left hover:text-blue-700 disabled:text-slate-400"
                >
                  {recording.title || fileName(recording.path, '文件不存在')}
                </button>
                {recording.path && !recording.pending && (
                  <button
                    type="button"
                    onClick={() => onRevealAttachment(recording)}
                    title="查看录音原件位置"
                    className="shrink-0 rounded p-0.5 text-slate-400 hover:bg-blue-50 hover:text-blue-700"
                  >
                    <FolderOpen size={12} />
                  </button>
                )}
                {!recording.pending && recording.cloudMetadataState && recording.cloudMetadataState !== 'ready' && (
                  <span className="shrink-0 text-amber-600">工作台待同步</span>
                )}
                <button type="button" onClick={() => onDelete(recording)} className="shrink-0 rounded p-0.5 text-slate-300 hover:bg-rose-50 hover:text-rose-500" title="删除录音">
                  <X size={11} />
                </button>
              </div>
              <div className="flex min-w-0 items-center gap-2 rounded-lg bg-white px-2.5 py-2 text-[11px] text-slate-600">
                <FileText size={13} className="shrink-0" />
                <span className="shrink-0 font-bold">转写文件</span>
                <button
                  type="button"
                  onClick={() => onOpenTranscript(recording)}
                  disabled={!recording.transcriptPath}
                  title={recording.transcriptPath ? '查看转写文字' : recording.processingError || '尚未生成转写文件'}
                  className="min-w-0 flex-1 truncate text-left hover:text-blue-700 disabled:text-slate-400"
                >
                  {fileName(recording.transcriptPath, '文件不存在')}
                </button>
                {recording.transcriptPath && (
                  <button
                    type="button"
                    onClick={() => onRevealTranscript(recording)}
                    title="查看转写文件位置"
                    className="shrink-0 rounded p-0.5 text-slate-400 hover:bg-blue-50 hover:text-blue-700"
                  >
                    <FolderOpen size={12} />
                  </button>
                )}
                {isRunning(status) && (
                  <>
                    <span className="shrink-0 font-bold text-blue-600">转写中</span>
                    <div
                      className="relative h-8 w-8 shrink-0 rounded-full"
                      style={{ background: `conic-gradient(#5B7BFE ${progress * 3.6}deg, #dbeafe 0deg)` }}
                      title={recording.processingStage || '正在转写'}
                      aria-label={`转写进度 ${progress}%`}
                    >
                      <div className="absolute inset-[3px] flex items-center justify-center rounded-full bg-white text-[8px] font-bold text-blue-600">{progress}%</div>
                    </div>
                  </>
                )}
                {needsSetup && onOpenAsrSettings && (
                  <button type="button" onClick={onOpenAsrSettings} className="inline-flex shrink-0 items-center gap-1 font-bold text-amber-700">
                    <Settings size={12} /> 去配置 ASR
                  </button>
                )}
                {!recording.pending && !isRunning(status) && !needsSetup && status !== 'ready' && (
                  <button type="button" onClick={() => onTranscribe(recording)} disabled={disabled} className="shrink-0 font-bold text-blue-600 disabled:text-slate-300">
                    {isRetryable(status) ? '重试转写' : '转写'}
                  </button>
                )}
                {!recording.pending && !isRunning(status) && !needsSetup && status === 'ready' && (
                  <button type="button" onClick={() => onTranscribe(recording)} disabled={disabled} className="shrink-0 font-bold text-blue-600 disabled:text-slate-300">
                    重新转写
                  </button>
                )}
              </div>
              {recording.processingError && !isRunning(status) && status !== 'ready' && (
                <p className="px-0.5 text-[10px] leading-4 text-rose-700">{recording.processingError}</p>
              )}
            </div>
          );
        })()}

        {materials.length > 0 && (
          <div className="rounded-lg bg-white px-2.5 py-2 text-[11px] text-slate-600">
            <p className="mb-1.5 font-bold text-slate-700">已导入附件</p>
            <div className="flex flex-wrap gap-1.5">
              {materials.map((material) => (
                <span key={material.id} className="inline-flex max-w-full items-center gap-1 rounded-md border border-slate-200 px-2 py-1">
                  <button
                    type="button"
                    onClick={() => onOpenAttachment(material)}
                    disabled={material.pending || !material.path}
                    title="打开附件"
                    className="inline-flex min-w-0 items-center gap-1 text-left hover:text-blue-700 disabled:text-slate-400"
                  >
                    <FileText size={11} className="shrink-0" />
                    <span className="max-w-[220px] truncate">{material.title}</span>
                    {!material.pending && material.cloudMetadataState && material.cloudMetadataState !== 'ready' && (
                      <span className="shrink-0 text-amber-600">工作台待同步</span>
                    )}
                  </button>
                  {material.path && !material.pending && (
                    <button
                      type="button"
                      onClick={() => onRevealAttachment(material)}
                      title="查看附件位置"
                      className="shrink-0 rounded p-0.5 text-slate-400 hover:bg-blue-50 hover:text-blue-700"
                    >
                      <FolderOpen size={11} />
                    </button>
                  )}
                  <button type="button" onClick={() => onDelete(material)} className="rounded p-0.5 text-slate-300 hover:bg-rose-50 hover:text-rose-500" title="删除附件">
                    <X size={11} />
                  </button>
                </span>
              ))}
            </div>
          </div>
        )}
        {notice && (
          <div className="flex items-center justify-between gap-2 px-0.5 text-[10px] leading-4 text-rose-700">
            <p>{notice}</p>
            {asrInstalled === false && onOpenAsrSettings && (
              <button
                type="button"
                onClick={onOpenAsrSettings}
                className="inline-flex shrink-0 items-center gap-1 rounded-md bg-amber-50 px-2 py-1 font-bold text-amber-700 hover:bg-amber-100"
              >
                <Settings size={11} /> 去配置 ASR
              </button>
            )}
          </div>
        )}
        <p className="px-0.5 text-[10px] leading-4 text-slate-400">
          如关联了项目，转写文件和附件均会进入项目工作台
        </p>
      </div>
    </section>
  );
}

export default TaskMediaPanel;
