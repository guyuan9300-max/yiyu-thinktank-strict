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
  pending?: boolean;
  pendingIndex?: number;
}

export interface TaskMediaPanelProps {
  attachments: TaskMediaItem[];
  disabled?: boolean;
  asrInstalled?: boolean | null;
  uploadProgress?: { currentFileName: string; percent: number } | null;
  notice?: string | null;
  headerActions?: ReactNode;
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
  onRevealAttachment,
  onRevealTranscript,
  onTranscribe,
  onOpenTranscript,
  onDelete,
  onOpenAsrSettings,
}: TaskMediaPanelProps) {
  const recordings = attachments.filter((item) => item.isAudio);
  const materials = attachments.filter((item) => !item.isAudio);

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
        {recordings.length === 0 ? (
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
        ) : recordings.map((recording) => {
          const status = recording.processingStatus || 'not_requested';
          const progress = Math.max(0, Math.min(100, Number(recording.processingProgress || 0)));
          const needsSetup = status === 'blocked' && asrInstalled !== true;
          return (
            <div key={recording.id} className="grid gap-1.5">
              <div className="flex min-w-0 items-center gap-2 rounded-lg bg-white px-2.5 py-2 text-[11px] text-slate-600">
                <FileAudio size={13} className="shrink-0" />
                <span className="shrink-0 font-bold">录音原件</span>
                <button
                  type="button"
                  onClick={() => onRevealAttachment(recording)}
                  disabled={disabled || recording.pending || !recording.path}
                  title={recording.path || (recording.pending ? '保存后即可定位' : '文件不存在')}
                  className="min-w-0 flex-1 truncate text-left disabled:text-slate-400"
                >
                  {recording.title || fileName(recording.path, '文件不存在')}
                </button>
                {recording.path && !recording.pending && <FolderOpen size={12} className="shrink-0 text-slate-400" />}
                {recording.pending && <span className="shrink-0 text-amber-600">待保存</span>}
                <button type="button" onClick={() => onDelete(recording)} className="shrink-0 rounded p-0.5 text-slate-300 hover:bg-rose-50 hover:text-rose-500" title="删除录音">
                  <X size={11} />
                </button>
              </div>
              <div className="flex min-w-0 items-center gap-2 rounded-lg bg-white px-2.5 py-2 text-[11px] text-slate-600">
                <FileText size={13} className="shrink-0" />
                <span className="shrink-0 font-bold">转写文件</span>
                <button
                  type="button"
                  onClick={() => onRevealTranscript(recording)}
                  disabled={!recording.transcriptPath}
                  title={recording.transcriptPath || recording.processingError || '尚未生成转写文件'}
                  className="min-w-0 flex-1 truncate text-left disabled:text-slate-400"
                >
                  {fileName(recording.transcriptPath, '文件不存在')}
                </button>
                {recording.transcriptPath && <FolderOpen size={12} className="shrink-0 text-slate-400" />}
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
                    <Settings size={12} /> 安装转写组件
                  </button>
                )}
                {!recording.pending && !isRunning(status) && !needsSetup && status !== 'ready' && (
                  <button type="button" onClick={() => onTranscribe(recording)} disabled={disabled} className="shrink-0 font-bold text-blue-600 disabled:text-slate-300">
                    {isRetryable(status) ? '重试转写' : '转写'}
                  </button>
                )}
                {status === 'ready' && recording.transcriptAttachmentId && (
                  <button type="button" onClick={() => onOpenTranscript(recording)} className="shrink-0 font-bold text-blue-600">查看文字</button>
                )}
              </div>
              {recording.processingError && !isRunning(status) && status !== 'ready' && (
                <p className="px-0.5 text-[10px] leading-4 text-rose-700">{recording.processingError}</p>
              )}
            </div>
          );
        })}

        {materials.length > 0 && (
          <div className="rounded-lg bg-white px-2.5 py-2 text-[11px] text-slate-600">
            <p className="mb-1.5 font-bold text-slate-700">已导入附件</p>
            <div className="flex flex-wrap gap-1.5">
              {materials.map((material) => (
                <span key={material.id} className="inline-flex max-w-full items-center gap-1 rounded-md border border-slate-200 px-2 py-1">
                  <button
                    type="button"
                    onClick={() => onRevealAttachment(material)}
                    disabled={material.pending || !material.path}
                    title={material.path || (material.pending ? '保存后即可定位' : '文件不存在')}
                    className="inline-flex min-w-0 items-center gap-1 text-left hover:text-blue-700 disabled:text-slate-400"
                  >
                    <FileText size={11} className="shrink-0" />
                    <span className="max-w-[220px] truncate">{material.title}</span>
                    {material.path && !material.pending ? <FolderOpen size={11} className="shrink-0 text-slate-400" /> : <span className="shrink-0 text-amber-600">待保存</span>}
                  </button>
                  <button type="button" onClick={() => onDelete(material)} className="rounded p-0.5 text-slate-300 hover:bg-rose-50 hover:text-rose-500" title="删除附件">
                    <X size={11} />
                  </button>
                </span>
              ))}
            </div>
          </div>
        )}
        {notice && <p className="px-0.5 text-[10px] leading-4 text-rose-700">{notice}</p>}
        <p className="px-0.5 text-[10px] leading-4 text-slate-400">
          如关联了项目，转写文件和附件均会进入项目工作台
        </p>
      </div>
    </section>
  );
}

export default TaskMediaPanel;
