/* eslint-disable @typescript-eslint/no-explicit-any */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  FileText, Upload, X, Plus, Trash2, Loader2, CheckCircle2,
  AlertCircle, Eye, Sparkles, Link as LinkIcon, UserCog, Clock, Save, Pencil,
} from 'lucide-react';
import {
  addSmartImportChunk,
  assignSmartImportFile,
  commitSmartImportSession,
  createSmartImportSession,
  deleteSmartImportChunk,
  deleteSmartImportFile,
  discardSmartImportSession,
  getSmartImportPreview,
  getSmartImportSession,
  parseSmartImportChunk,
  patchSmartImportChunkParsed,
  updateSmartImportChunk,
  uploadSmartImportFile,
} from '../../lib/api';
import type {
  SmartImportChunk,
  SmartImportCommitStats,
  SmartImportParsedChunkOutput,
  SmartImportPreviewPlan,
  SmartImportSessionState,
  SmartImportStagedFile,
} from '../../lib/api';

const MAX_FILES_PER_CHUNK = 20;

interface SmartFileImportModalProps {
  open: boolean;
  clientId?: string;
  projectEventLineId?: string;
  onClose: () => void;
  onImported?: (stats: SmartImportCommitStats) => void;
  resumeSessionId?: string;
}

export function SmartFileImportModal({
  open, clientId, projectEventLineId, onClose, onImported, resumeSessionId,
}: SmartFileImportModalProps): JSX.Element | null {
  const [sessionState, setSessionState] = useState<SmartImportSessionState | null>(null);
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewPlan, setPreviewPlan] = useState<SmartImportPreviewPlan | null>(null);
  const [committing, setCommitting] = useState(false);
  // 每段是否在解析中(乐观显示)
  const [parsingChunkIds, setParsingChunkIds] = useState<Set<string>>(new Set());
  // 每段本地未保存的文字(dirty 检测)
  const [draftTextByChunk, setDraftTextByChunk] = useState<Record<string, string>>({});

  // 初始化:resume 或新建,自动确保至少有 1 个空段
  useEffect(() => {
    if (!open) return;
    let aborted = false;
    (async () => {
      setLoading(true); setErrorMsg(null);
      try {
        let state: SmartImportSessionState;
        if (resumeSessionId) {
          state = await getSmartImportSession(resumeSessionId);
        } else {
          state = await createSmartImportSession({
            clientId, projectEventLineId,
            title: `智能文件导入 · ${new Date().toLocaleString('zh-CN')}`,
          });
        }
        if (state.chunks.length === 0) {
          state = await addSmartImportChunk(state.session.id, {
            rawText: '', fileIds: [], autoParse: false,
          });
        }
        if (!aborted) {
          setSessionState(state);
          // 初始化 drafts
          const drafts: Record<string, string> = {};
          state.chunks.forEach(c => { drafts[c.id] = c.raw_text; });
          setDraftTextByChunk(drafts);
        }
      } catch (e) {
        if (!aborted) setErrorMsg(e instanceof Error ? e.message : '初始化失败');
      } finally {
        if (!aborted) setLoading(false);
      }
    })();
    return () => { aborted = true; };
  }, [open, resumeSessionId, clientId, projectEventLineId]);

  const handleClose = useCallback(() => {
    setSessionState(null); setPreviewOpen(false); setPreviewPlan(null);
    setErrorMsg(null);
    setDraftTextByChunk({});
    setParsingChunkIds(new Set());
    onClose();
  }, [onClose]);

  const refresh = useCallback(async (): Promise<SmartImportSessionState | null> => {
    if (!sessionState) return null;
    try {
      const state = await getSmartImportSession(sessionState.session.id);
      setSessionState(state);
      return state;
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : '刷新失败');
      return null;
    }
  }, [sessionState]);

  // 上传文件到某段
  const handleUploadFilesToChunk = useCallback(async (chunkId: string, files: FileList | File[]) => {
    if (!sessionState) return;
    const fileArr = Array.from(files);
    if (fileArr.length === 0) return;
    const currentAttached = sessionState.staged_files.filter(f => f.assigned_chunk_id === chunkId).length;
    if (currentAttached + fileArr.length > MAX_FILES_PER_CHUNK) {
      setErrorMsg(`每段最多 ${MAX_FILES_PER_CHUNK} 个文件,这段已有 ${currentAttached} 个`);
      return;
    }
    setErrorMsg(null);
    try {
      for (const f of fileArr) {
        const sf = await uploadSmartImportFile(sessionState.session.id, f);
        await assignSmartImportFile(sf.id, chunkId);
      }
      await refresh();
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : '文件上传失败');
    }
  }, [sessionState, refresh]);

  const handleRemoveFileFromChunk = useCallback(async (fileId: string) => {
    try {
      await deleteSmartImportFile(fileId);
      await refresh();
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : '删除文件失败');
    }
  }, [refresh]);

  // 用户编辑文本(本地 draft, 不调后端)
  const handleDraftTextChange = useCallback((chunkId: string, text: string) => {
    setDraftTextByChunk(prev => ({ ...prev, [chunkId]: text }));
  }, []);

  // 点「保存并解析」 — 把 draft 写后端 + 触发 LLM
  const handleSaveAndParse = useCallback(async (chunkId: string) => {
    if (!sessionState) return;
    const draft = draftTextByChunk[chunkId] ?? '';
    const chunk = sessionState.chunks.find(c => c.id === chunkId);
    if (!chunk) return;
    setErrorMsg(null);
    setParsingChunkIds(prev => new Set(prev).add(chunkId));
    try {
      // 文本变了就先保存
      if (draft !== chunk.raw_text) {
        await updateSmartImportChunk(chunkId, { rawText: draft, autoParse: false });
      }
      // 空文本 + 没文件 → 没东西解析
      const hasFiles = sessionState.staged_files.some(f => f.assigned_chunk_id === chunkId);
      if (!draft.trim() && !hasFiles) {
        setErrorMsg('这一段还没有内容,先写讲述或拖文件再保存');
        return;
      }
      // 触发解析
      await parseSmartImportChunk(chunkId);
      await refresh();
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : '保存/解析失败');
    } finally {
      setParsingChunkIds(prev => {
        const next = new Set(prev); next.delete(chunkId); return next;
      });
    }
  }, [sessionState, draftTextByChunk, refresh]);

  // 补充说明给 AI 重听:把 supplement append 到 raw_text + 重新解析
  const handleClarifyChunk = useCallback(async (chunkId: string, supplement: string) => {
    if (!sessionState) return;
    const chunk = sessionState.chunks.find(c => c.id === chunkId);
    if (!chunk) return;
    const trimmed = supplement.trim();
    if (!trimmed) return;
    const base = (draftTextByChunk[chunkId] ?? chunk.raw_text).trim();
    const newRawText = base
      ? `${base}\n\n[补充说明] ${trimmed}`
      : `[补充说明] ${trimmed}`;
    setErrorMsg(null);
    setParsingChunkIds(prev => new Set(prev).add(chunkId));
    try {
      await updateSmartImportChunk(chunkId, { rawText: newRawText, autoParse: false });
      setDraftTextByChunk(prev => ({ ...prev, [chunkId]: newRawText }));
      await parseSmartImportChunk(chunkId);
      await refresh();
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : '补充失败');
    } finally {
      setParsingChunkIds(prev => {
        const next = new Set(prev); next.delete(chunkId); return next;
      });
    }
  }, [sessionState, draftTextByChunk, refresh]);

  // ➕ 加段(不再自动触发上一段解析)
  const handleAddChunk = useCallback(async () => {
    if (!sessionState) return;
    try {
      const newState = await addSmartImportChunk(sessionState.session.id, {
        rawText: '', fileIds: [], autoParse: false,
      });
      setSessionState(newState);
      // 给新段填空 draft
      const newest = newState.chunks[newState.chunks.length - 1];
      if (newest) setDraftTextByChunk(prev => ({ ...prev, [newest.id]: '' }));
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : '添加新段失败');
    }
  }, [sessionState]);

  const handleDeleteChunk = useCallback(async (chunkId: string) => {
    if (!sessionState) return;
    if (sessionState.chunks.length <= 1) {
      setErrorMsg('至少保留一段');
      return;
    }
    if (!window.confirm('删除这段?这段的文字和挂载文件都会丢弃。')) return;
    try {
      await deleteSmartImportChunk(chunkId);
      await refresh();
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : '删段失败');
    }
  }, [sessionState, refresh]);

  // 用户 inline 编辑某字段后,把整个 parsed 对象写回
  const handlePatchParsed = useCallback(async (
    chunkId: string,
    nextParsed: SmartImportParsedChunkOutput,
  ) => {
    try {
      const state = await patchSmartImportChunkParsed(chunkId, nextParsed);
      setSessionState(state);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : '保存修改失败');
    }
  }, []);

  // 判断每段是否 dirty(文字变了)
  const isChunkDirty = useCallback((chunk: SmartImportChunk) => {
    const draft = draftTextByChunk[chunk.id];
    if (draft === undefined) return false;
    return draft !== chunk.raw_text;
  }, [draftTextByChunk]);

  // 判断每段是否需要保存+解析(dirty 或 没解析过但有内容)
  const chunkNeedsAttention = useCallback((chunk: SmartImportChunk) => {
    if (isChunkDirty(chunk)) return true;
    if (chunk.parse_status !== 'parsed' && chunk.raw_text.trim()) return true;
    return false;
  }, [isChunkDirty]);

  // 预览
  const handlePreview = useCallback(async () => {
    if (!sessionState) return;
    // 提示有未处理的段
    const pendingChunks = sessionState.chunks.filter(chunkNeedsAttention);
    if (pendingChunks.length > 0) {
      setErrorMsg(`还有 ${pendingChunks.length} 段需要保存或解析,点击每段的「保存并解析」按钮`);
      return;
    }
    try {
      const plan = await getSmartImportPreview(sessionState.session.id);
      setPreviewPlan(plan);
      setPreviewOpen(true);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : '加载预览失败');
    }
  }, [sessionState, chunkNeedsAttention]);

  const handleCommit = useCallback(async () => {
    if (!sessionState) return;
    if (!sessionState.session.client_id) {
      setErrorMsg('请先选择一个客户(本次导入要归到哪个客户档案)');
      return;
    }
    // commit 前再次检查
    const pendingChunks = sessionState.chunks.filter(chunkNeedsAttention);
    if (pendingChunks.length > 0) {
      setErrorMsg(`还有 ${pendingChunks.length} 段需要保存或解析,不能直接导入`);
      return;
    }
    setCommitting(true); setErrorMsg(null);
    try {
      const result = await commitSmartImportSession(sessionState.session.id);
      if (onImported) onImported(result.stats);
      handleClose();
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : '导入失败');
    } finally {
      setCommitting(false);
    }
  }, [sessionState, onImported, handleClose, chunkNeedsAttention]);

  const handleDiscard = useCallback(async () => {
    if (!sessionState) return;
    if (!window.confirm('放弃这次导入?本次讲述的内容和文件都会丢弃,不可恢复。')) return;
    try {
      await discardSmartImportSession(sessionState.session.id);
      handleClose();
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : '取消失败');
    }
  }, [sessionState, handleClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4">
      <div className="relative flex h-[92vh] w-full max-w-[1100px] flex-col rounded-2xl border border-gray-200 bg-white shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
          <div className="flex items-center gap-3">
            <Sparkles size={20} className="text-[#5B7BFE]" />
            <h2 className="text-[16px] font-bold text-gray-900">智能文件导入</h2>
            <span className="text-[12px] text-gray-400">
              · 一段一段讲, 点「保存并解析」 AI 整理 角色/关系/时间
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-gray-400">
              {sessionState?.session.total_chunks ?? 0} 段 · {sessionState?.session.total_files ?? 0} 文件
            </span>
            <button
              type="button"
              onClick={handleClose}
              className="rounded-full p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
              aria-label="关闭"
            >
              <X size={18} />
            </button>
          </div>
        </div>

        {errorMsg && (
          <div className="border-b border-red-200 bg-red-50 px-6 py-2 text-[12px] text-red-700">
            <AlertCircle size={14} className="mr-1 inline" />
            {errorMsg}
            <button onClick={() => setErrorMsg(null)} className="ml-3 text-red-500 hover:underline">×</button>
          </div>
        )}

        {loading && !sessionState && (
          <div className="flex flex-1 items-center justify-center text-gray-400">
            <Loader2 size={20} className="mr-2 animate-spin" />初始化会话中...
          </div>
        )}

        {sessionState && (
          <>
            <div className="flex flex-1 flex-col gap-2 overflow-y-auto bg-gray-50/40 px-6 py-5">
              {sessionState.chunks.map((chunk, idx) => {
                const attachedFiles = sessionState.staged_files.filter(f => f.assigned_chunk_id === chunk.id);
                const isLast = idx === sessionState.chunks.length - 1;
                const draftText = draftTextByChunk[chunk.id] ?? chunk.raw_text;
                const isParsing = parsingChunkIds.has(chunk.id);
                const dirty = isChunkDirty(chunk);
                return (
                  <div key={chunk.id}>
                    <ChunkCard
                      chunk={chunk}
                      index={idx}
                      attachedFiles={attachedFiles}
                      draftText={draftText}
                      isParsing={isParsing}
                      dirty={dirty}
                      onUploadFiles={(files) => handleUploadFilesToChunk(chunk.id, files)}
                      onRemoveFile={handleRemoveFileFromChunk}
                      onTextChange={(text) => handleDraftTextChange(chunk.id, text)}
                      onSaveAndParse={() => handleSaveAndParse(chunk.id)}
                      onDelete={() => handleDeleteChunk(chunk.id)}
                      onPatchParsed={(p) => handlePatchParsed(chunk.id, p)}
                      onClarify={(text) => handleClarifyChunk(chunk.id, text)}
                    />
                    {isLast && (
                      <div className="my-3 flex items-center justify-center">
                        <button
                          type="button"
                          onClick={() => void handleAddChunk()}
                          className="group flex h-9 items-center gap-1.5 rounded-full border-2 border-dashed border-[#C7D5FF] bg-white px-4 text-[#5B7BFE] shadow-sm transition hover:bg-[#5B7BFE] hover:text-white"
                          title="添加下一段"
                          aria-label="添加下一段"
                        >
                          <Plus size={16} strokeWidth={2.5} />
                          <span className="text-[11px] font-bold">加一段</span>
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>

            <div className="flex items-center justify-between border-t border-gray-100 bg-gray-50 px-6 py-3">
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={handleDiscard}
                  className="rounded-2xl border border-gray-300 bg-white px-4 py-2 text-[12px] text-gray-600 hover:border-red-300 hover:text-red-600"
                >
                  <Trash2 size={13} className="mr-1 inline" />放弃本次导入
                </button>
                <span className="text-[11px] text-gray-400">
                  💾 关闭窗口会保留进度,下次可继续
                </span>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={handlePreview}
                  className="rounded-2xl border border-[#D8E5FF] bg-white px-4 py-2 text-[12px] font-bold text-[#4A63CF] hover:border-[#5B7BFE]"
                >
                  <Eye size={13} className="mr-1 inline" />预览全部提取
                </button>
                <button
                  type="button"
                  onClick={handleCommit}
                  disabled={committing || (sessionState.chunks.length === 0)}
                  className="rounded-2xl bg-[#5B7BFE] px-5 py-2 text-[12px] font-bold text-white shadow-md hover:bg-[#4A6BE6] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {committing ? (<><Loader2 size={13} className="mr-1 inline animate-spin" />导入中...</>) :
                                (<><CheckCircle2 size={13} className="mr-1 inline" />确认导入数据中心</>)}
                </button>
              </div>
            </div>
          </>
        )}

        {previewOpen && previewPlan && (
          <PreviewPlanModal
            plan={previewPlan}
            onClose={() => setPreviewOpen(false)}
            onConfirm={handleCommit}
            committing={committing}
          />
        )}
      </div>
    </div>
  );
}

// ====================== 子组件:单段卡片 ======================
interface ChunkCardProps {
  chunk: SmartImportChunk;
  index: number;
  attachedFiles: SmartImportStagedFile[];
  draftText: string;
  isParsing: boolean;
  dirty: boolean;
  onUploadFiles: (files: FileList | File[]) => void;
  onRemoveFile: (fileId: string) => void;
  onTextChange: (text: string) => void;
  onSaveAndParse: () => void;
  onDelete: () => void;
  onPatchParsed: (parsed: SmartImportParsedChunkOutput) => void;
  onClarify: (supplement: string) => Promise<void> | void;
}

function ChunkCard({
  chunk, index, attachedFiles, draftText, isParsing, dirty,
  onUploadFiles, onRemoveFile, onTextChange, onSaveAndParse, onDelete, onPatchParsed, onClarify,
}: ChunkCardProps): JSX.Element {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  // 保存按钮状态:可点 = dirty 或 (未解析过 + 有内容)
  const hasContent = draftText.trim().length > 0 || attachedFiles.length > 0;
  const needsParse = chunk.parse_status !== 'parsed' && hasContent;
  const canSave = (dirty || needsParse) && !isParsing;

  return (
    <div className="rounded-2xl border border-gray-200 bg-white shadow-sm">
      {/* 段标记 + 删除 */}
      <div className="flex items-center justify-between border-b border-gray-100 px-4 py-2">
        <span className="text-[11px] font-bold text-gray-500">第 {index + 1} 段</span>
        <div className="flex items-center gap-2 text-[11px]">
          {isParsing && (
            <span className="text-yellow-600"><Loader2 size={11} className="mr-1 inline animate-spin" />AI 正在听...</span>
          )}
          {!isParsing && chunk.parse_status === 'parsed' && !dirty && (
            <span className="text-green-600">● AI 已理解</span>
          )}
          {!isParsing && chunk.parse_status === 'parsed' && dirty && (
            <span className="text-amber-600">● 有修改, 点保存重新解析</span>
          )}
          {!isParsing && chunk.parse_status === 'failed' && (
            <span className="text-red-600" title={chunk.parse_error}>● 解析失败</span>
          )}
          <button onClick={onDelete} className="text-gray-400 hover:text-red-500" title="删除此段">
            <Trash2 size={12} />
          </button>
        </div>
      </div>

      {/* 文件池 */}
      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault(); setDragOver(false);
          if (e.dataTransfer.files.length > 0) onUploadFiles(e.dataTransfer.files);
        }}
        className={`m-3 rounded-xl border-2 border-dashed px-3 py-2.5 transition-colors ${
          dragOver ? 'border-[#5B7BFE] bg-blue-50' : 'border-gray-200 bg-gray-50/60'
        }`}
      >
        <div className="mb-2 flex items-center justify-between">
          <span className="text-[11px] font-bold text-gray-600">
            📎 文件 ({attachedFiles.length} / {MAX_FILES_PER_CHUNK})
          </span>
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={attachedFiles.length >= MAX_FILES_PER_CHUNK}
            className="rounded-lg bg-white px-2 py-0.5 text-[10px] font-bold text-[#5B7BFE] shadow-sm hover:bg-blue-50 disabled:opacity-40"
          >
            <Upload size={10} className="mr-1 inline" />添加
          </button>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            hidden
            onChange={(e) => { if (e.target.files) { onUploadFiles(e.target.files); e.target.value = ''; } }}
          />
        </div>
        {attachedFiles.length === 0 ? (
          <p className="text-[11px] text-gray-400">
            把这段相关的文件拖到这里(最多 {MAX_FILES_PER_CHUNK} 个);也可以只讲不挂文件
          </p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {attachedFiles.map((f) => (
              <div
                key={f.id}
                className="group flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-2 py-1 text-[11px] shadow-sm"
                title={f.original_filename}
              >
                <FileText size={11} className="text-gray-400" />
                <span className="max-w-[180px] truncate text-gray-700">{f.original_filename}</span>
                <button
                  onClick={() => onRemoveFile(f.id)}
                  className="text-gray-300 hover:text-red-500 opacity-0 group-hover:opacity-100"
                  aria-label="移除"
                >
                  <X size={10} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 讲述框 */}
      <div className="px-3 pb-2">
        <textarea
          value={draftText}
          onChange={(e) => onTextChange(e.target.value)}
          rows={Math.max(3, Math.min(8, Math.ceil((draftText.length || 1) / 50)))}
          placeholder={index === 0
            ? '点击此处开始讲述故事吧... 比如:谁产出的?为什么找他?谁的反应/评价?发生在什么时候?'
            : '继续讲讲这一段...'}
          className="w-full resize-none rounded-xl border border-gray-200 bg-white p-3 text-[13px] text-gray-700 focus:border-[#5B7BFE] focus:outline-none focus:ring-2 focus:ring-[#5B7BFE]/30"
        />
      </div>

      {/* 保存按钮(段内底部) */}
      <div className="flex items-center justify-end gap-2 px-3 pb-3">
        {dirty && <span className="text-[11px] text-amber-600">有未保存改动</span>}
        <button
          type="button"
          onClick={onSaveAndParse}
          disabled={!canSave}
          className={`flex items-center gap-1 rounded-xl px-3 py-1.5 text-[11px] font-bold shadow-sm transition ${
            canSave
              ? 'bg-[#5B7BFE] text-white hover:bg-[#4A6BE6]'
              : 'bg-gray-100 text-gray-400 cursor-not-allowed'
          }`}
        >
          {isParsing
            ? <><Loader2 size={11} className="animate-spin" />解析中</>
            : <><Save size={11} />保存并解析</>
          }
        </button>
      </div>

      {/* 系统理解 — 角色 / 关系 / 时间 三卡同行 */}
      <UnderstandingPanel chunk={chunk} onPatchParsed={onPatchParsed} />

      {/* 补充说明给 AI 重听(已解析后才显示) */}
      {chunk.parse_status === 'parsed' && (
        <ClarifyComposer onSubmit={onClarify} disabled={isParsing} />
      )}
    </div>
  );
}

// ====================== 子组件:补充说明 composer ======================
function ClarifyComposer({
  onSubmit, disabled,
}: {
  onSubmit: (text: string) => Promise<void> | void;
  disabled: boolean;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const [submitting, setSubmitting] = useState(false);

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        disabled={disabled}
        className="flex w-full items-center justify-center gap-1 border-t border-gray-100 bg-amber-50/30 px-4 py-2 text-[11px] text-amber-700 hover:bg-amber-50 disabled:opacity-50"
      >
        💬 想补充说明给 AI 吗? 点这里再讲几句让 AI 重听
      </button>
    );
  }

  const submit = async () => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setSubmitting(true);
    try {
      await onSubmit(trimmed);
      setText('');
      setOpen(false);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="border-t border-gray-100 bg-amber-50/30 px-4 py-3">
      <div className="mb-1 text-[11px] font-bold text-amber-800">💬 补充说明给 AI</div>
      <textarea
        autoFocus
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={2}
        placeholder="比如:红霞总还兼任决策审批,这一点 AI 漏了"
        className="w-full resize-none rounded-xl border border-amber-200 bg-white p-2 text-[12px] text-gray-700 focus:border-amber-400 focus:outline-none focus:ring-2 focus:ring-amber-200"
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
            e.preventDefault();
            void submit();
          }
        }}
      />
      <div className="mt-2 flex items-center justify-between">
        <span className="text-[10px] text-gray-400">
          补充内容会附到原讲述末尾,AI 重新整理整段
        </span>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => { setOpen(false); setText(''); }}
            disabled={submitting}
            className="rounded-xl border border-gray-300 bg-white px-3 py-1 text-[11px] text-gray-600"
          >
            取消
          </button>
          <button
            type="button"
            onClick={() => void submit()}
            disabled={submitting || !text.trim()}
            className="rounded-xl bg-amber-500 px-3 py-1 text-[11px] font-bold text-white shadow-sm hover:bg-amber-600 disabled:opacity-50"
          >
            {submitting
              ? <><Loader2 size={11} className="mr-1 inline animate-spin" />重听中...</>
              : '让 AI 重听这段'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ====================== 子组件:系统理解面板(2 卡:关系 / 时间)======================
function UnderstandingPanel({
  chunk, onPatchParsed,
}: {
  chunk: SmartImportChunk;
  onPatchParsed: (parsed: SmartImportParsedChunkOutput) => void;
}): JSX.Element | null {
  if (chunk.parse_status !== 'parsed') return null;
  const parsed = chunk.parsed || {};

  const relationships = parsed.relationships || [];
  const events = parsed.events || [];

  const hasAny = relationships.length || events.length;
  if (!hasAny) {
    return (
      <div className="border-t border-gray-100 bg-gradient-to-br from-amber-50/30 to-gray-50/30 px-4 py-2 text-[11px] text-gray-400">
        AI 没有从这段听出明确的关系/时间信息,可以补充几句细节或点下方"补充给 AI 重听"
      </div>
    );
  }

  // 整行更新一条 relationship
  const updateRelRow = (idx: number, next: { from: string; type: string; to: string }) => {
    const nextParsed: SmartImportParsedChunkOutput = {
      ...parsed,
      relationships: relationships.map((r, i) =>
        i === idx ? { ...r, from: next.from, type: next.type, to: next.to } : r
      ),
    };
    onPatchParsed(nextParsed);
  };
  const deleteRelRow = (idx: number) => {
    const nextParsed: SmartImportParsedChunkOutput = {
      ...parsed,
      relationships: relationships.filter((_, i) => i !== idx),
    };
    onPatchParsed(nextParsed);
  };

  // 整行更新一条 event
  const updateEventRow = (idx: number, next: { when: string; what: string }) => {
    const nextParsed: SmartImportParsedChunkOutput = {
      ...parsed,
      events: events.map((ev, i) =>
        i === idx ? { ...ev, happened_at: next.when, summary: next.what } : ev
      ),
    };
    onPatchParsed(nextParsed);
  };
  const deleteEventRow = (idx: number) => {
    const nextParsed: SmartImportParsedChunkOutput = {
      ...parsed,
      events: events.filter((_, i) => i !== idx),
    };
    onPatchParsed(nextParsed);
  };

  return (
    <div className="border-t border-gray-100 bg-gradient-to-br from-blue-50/40 to-emerald-50/30 px-4 py-3">
      <div className="mb-2 flex items-center gap-1 text-[11px] font-bold text-gray-600">
        <Sparkles size={11} className="text-[#5B7BFE]" />AI 听到的内容
      </div>
      <div className="grid grid-cols-[1fr_2fr] gap-3 text-[12px]">
        {/* 关系(含角色) */}
        <DimensionBlock icon={<LinkIcon size={12} />} title="关系" count={relationships.length}>
          {relationships.length === 0
            ? <span className="text-gray-400">未提到</span>
            : <div className="space-y-1 divide-y divide-gray-100">
                {relationships.map((r, i) => (
                  <RelationshipRow
                    key={i}
                    from={(r?.from || '').trim()}
                    type_={(r?.type || '').trim()}
                    to={(r?.to || '').trim()}
                    onSave={(next) => updateRelRow(i, next)}
                    onDelete={() => deleteRelRow(i)}
                  />
                ))}
              </div>
          }
        </DimensionBlock>

        {/* 时间 */}
        <DimensionBlock icon={<Clock size={12} />} title="时间" count={events.length}>
          {events.length === 0
            ? <span className="text-gray-400">未提到</span>
            : <div className="space-y-2 divide-y divide-gray-100">
                {events.map((ev, i) => (
                  <EventRow
                    key={i}
                    when={(ev?.happened_at || '').trim()}
                    what={(ev?.summary || ev?.action || '').trim()}
                    onSave={(next) => updateEventRow(i, next)}
                    onDelete={() => deleteEventRow(i)}
                  />
                ))}
              </div>
          }
        </DimensionBlock>
      </div>
    </div>
  );
}

// ====================== 关系行(整行可编辑)======================
function RelationshipRow({
  from, type_, to, onSave, onDelete,
}: {
  from: string;
  type_: string;
  to: string;
  onSave: (next: { from: string; type: string; to: string }) => void;
  onDelete: () => void;
}): JSX.Element {
  const [editing, setEditing] = useState(false);
  const [dFrom, setDFrom] = useState(from);
  const [dType, setDType] = useState(type_);
  const [dTo, setDTo] = useState(to);
  useEffect(() => { setDFrom(from); setDType(type_); setDTo(to); }, [from, type_, to]);

  const save = () => {
    setEditing(false);
    const f = dFrom.trim(), t = dType.trim(), to_ = dTo.trim();
    if (f !== from.trim() || t !== type_.trim() || to_ !== to.trim()) {
      onSave({ from: f, type: t, to: to_ });
    }
  };
  const cancel = () => {
    setEditing(false);
    setDFrom(from); setDType(type_); setDTo(to);
  };

  if (editing) {
    return (
      <div className="space-y-1 py-1.5 first:pt-0">
        <div className="flex flex-wrap items-center gap-1">
          <input
            value={dFrom}
            onChange={(e) => setDFrom(e.target.value.slice(0, 30))}
            placeholder="主体"
            autoFocus
            className="rounded border border-[#5B7BFE] bg-white px-1.5 py-0.5 text-[11px] focus:outline-none"
            style={{ width: '32%' }}
          />
          <input
            value={dType}
            onChange={(e) => setDType(e.target.value.slice(0, 10))}
            placeholder="关系"
            className="rounded border border-[#5B7BFE] bg-white px-1.5 py-0.5 text-[11px] focus:outline-none"
            style={{ width: '28%' }}
          />
          <input
            value={dTo}
            onChange={(e) => setDTo(e.target.value.slice(0, 30))}
            placeholder="对象"
            className="rounded border border-[#5B7BFE] bg-white px-1.5 py-0.5 text-[11px] focus:outline-none"
            style={{ width: '32%' }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); save(); }
              if (e.key === 'Escape') cancel();
            }}
          />
        </div>
        <div className="flex justify-end gap-1.5">
          <button onClick={onDelete} className="text-[10px] text-red-500 hover:underline">删除</button>
          <button onClick={cancel} className="text-[10px] text-gray-500 hover:underline">取消</button>
          <button onClick={save} className="text-[10px] font-bold text-[#5B7BFE] hover:underline">保存</button>
        </div>
      </div>
    );
  }

  return (
    <div className="group flex items-center gap-1.5 py-1 first:pt-0 text-[11px] text-gray-700">
      <span className="font-bold">{from}</span>
      <span className="text-gray-400">—</span>
      <span className={type_ ? 'text-gray-600' : 'text-amber-600 italic'}>{type_ || '(未明确)'}</span>
      <span className="text-gray-400">—</span>
      <span className="font-bold">{to}</span>
      <button
        type="button"
        onClick={() => setEditing(true)}
        className="ml-auto rounded p-0.5 text-gray-400 hover:bg-white hover:text-[#5B7BFE]"
        title="编辑这一行"
      >
        <Pencil size={11} />
      </button>
    </div>
  );
}

// ====================== 时间行(整行可编辑)======================
function EventRow({
  when, what, onSave, onDelete,
}: {
  when: string;
  what: string;
  onSave: (next: { when: string; what: string }) => void;
  onDelete: () => void;
}): JSX.Element {
  const [editing, setEditing] = useState(false);
  const [dWhen, setDWhen] = useState(when);
  const [dWhat, setDWhat] = useState(what);
  useEffect(() => { setDWhen(when); setDWhat(what); }, [when, what]);

  const save = () => {
    setEditing(false);
    const w = dWhen.trim(), s = dWhat.trim();
    if (w !== when.trim() || s !== what.trim()) {
      onSave({ when: w, what: s });
    }
  };
  const cancel = () => {
    setEditing(false);
    setDWhen(when); setDWhat(what);
  };

  if (editing) {
    return (
      <div className="space-y-1 pt-1.5 first:pt-0">
        <input
          value={dWhen}
          onChange={(e) => setDWhen(e.target.value.slice(0, 30))}
          placeholder="时间(比如:2025-03 / 今年 / 项目前期)"
          autoFocus
          className="w-full rounded border border-[#5B7BFE] bg-white px-1.5 py-0.5 text-[11px] focus:outline-none"
        />
        <textarea
          value={dWhat}
          onChange={(e) => setDWhat(e.target.value)}
          placeholder="这个时间发生了什么"
          rows={2}
          className="w-full resize-none rounded border border-[#5B7BFE] bg-white px-1.5 py-1 text-[11px] focus:outline-none"
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); save(); }
            if (e.key === 'Escape') cancel();
          }}
        />
        <div className="flex justify-end gap-1.5">
          <button onClick={onDelete} className="text-[10px] text-red-500 hover:underline">删除</button>
          <button onClick={cancel} className="text-[10px] text-gray-500 hover:underline">取消</button>
          <button onClick={save} className="text-[10px] font-bold text-[#5B7BFE] hover:underline">保存</button>
        </div>
      </div>
    );
  }

  return (
    <div className="group relative pt-1 first:pt-0 text-[11px] text-gray-700">
      <div className="flex items-center justify-between">
        <span className={`font-bold ${when ? 'text-blue-600' : 'text-amber-600 italic'}`}>
          {when || '(未明确时间)'}
        </span>
        <button
          type="button"
          onClick={() => setEditing(true)}
          className="rounded p-0.5 text-gray-400 hover:bg-white hover:text-[#5B7BFE]"
          title="编辑这一行"
        >
          <Pencil size={11} />
        </button>
      </div>
      {what && (
        <div className="mt-0.5 whitespace-pre-wrap break-words text-gray-500 leading-snug">
          {what}
        </div>
      )}
    </div>
  );
}

function DimensionBlock({
  icon, title, count, children,
}: { icon: React.ReactNode; title: string; count: number; children: React.ReactNode }): JSX.Element {
  return (
    <div className="rounded-xl border border-gray-100 bg-white/70 p-2">
      <div className="mb-1 flex items-center gap-1 text-[10px] font-bold text-gray-500">
        {icon}{title} {count > 0 && <span className="text-[#5B7BFE]">({count})</span>}
      </div>
      <div>{children}</div>
    </div>
  );
}

// ====================== 预览全部模态 ======================
interface PreviewPlanModalProps {
  plan: SmartImportPreviewPlan;
  onClose: () => void;
  onConfirm: () => Promise<void>;
  committing: boolean;
}

const ROLE_LABELS: Record<string, { label: string; color: string }> = {
  client_owned: { label: '客户自有', color: 'bg-blue-100 text-blue-700' },
  partner_submission: { label: '合作方提交', color: 'bg-purple-100 text-purple-700' },
  yiyu_advisory: { label: '益语顾问产出', color: 'bg-emerald-100 text-emerald-700' },
  external_reference: { label: '外部对标', color: 'bg-amber-100 text-amber-700' },
  policy_industry: { label: '行业政策', color: 'bg-rose-100 text-rose-700' },
  unknown: { label: '待分类', color: 'bg-gray-100 text-gray-500' },
};

function PreviewPlanModal({ plan, onClose, onConfirm, committing }: PreviewPlanModalProps): JSX.Element {
  return (
    <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="max-h-[80vh] w-full max-w-[760px] overflow-y-auto rounded-2xl bg-white shadow-2xl">
        <div className="sticky top-0 flex items-center justify-between border-b border-gray-100 bg-white px-5 py-3">
          <h3 className="text-[15px] font-bold text-gray-900">预览将写入数据中心</h3>
          <button onClick={onClose} className="rounded-full p-1 text-gray-400 hover:bg-gray-100">
            <X size={16} />
          </button>
        </div>
        <div className="space-y-4 p-5 text-[12px]">
          <div>
            <div className="font-bold text-gray-700">📊 解析进度</div>
            <div className="text-gray-500">{plan.chunks_parsed} / {plan.chunks_total} 段已成功解析</div>
            {plan.chunks_failed.length > 0 && (
              <div className="text-red-600">⚠️ {plan.chunks_failed.length} 段解析失败</div>
            )}
          </div>

          <Section title={`👥 人物/机构 (${plan.entities.length})`}>
            {plan.entities.map((e, i) => (
              <li key={i}>
                <span className="font-bold">{e.name}</span>
                {e.role_in_project && <span className="text-gray-500"> · {e.role_in_project}</span>}
              </li>
            ))}
          </Section>

          <Section title={`🔗 关系 (${(plan.relationships || []).length})`}>
            {(plan.relationships || []).map((r, i) => (
              <li key={i}>
                <span className="font-bold">{r.from}</span>
                <span className="mx-1 text-gray-400">→ {r.type || '关联'} →</span>
                <span className="font-bold">{r.to}</span>
              </li>
            ))}
          </Section>

          <Section title={`📄 文件分类 (${plan.files_classified.length})`}>
            {plan.files_classified.map((f, i) => (
              <li key={i} className="flex items-center justify-between gap-2">
                <span className="flex-1 truncate">{f.original_filename}</span>
                <span className={`shrink-0 rounded px-2 py-0.5 text-[10px] ${ROLE_LABELS[f.role || 'unknown']?.color || ROLE_LABELS.unknown.color}`}>
                  {ROLE_LABELS[f.role || 'unknown']?.label || '?'}
                  {f.subject_entity_name ? ` · ${f.subject_entity_name}` : ''}
                </span>
              </li>
            ))}
          </Section>

          <Section title={`⏰ 时间线 (${(plan.events || []).length})`}>
            {(plan.events || []).map((ev, i) => (
              <li key={i}>
                {ev?.happened_at && <span className="font-bold text-blue-600">{ev.happened_at}</span>}
                {ev?.happened_at && ev?.summary && <span className="mx-1 text-gray-400">·</span>}
                {ev?.summary && <span>{ev.summary}</span>}
              </li>
            ))}
          </Section>

          <Section title={`🤝 承诺 (${(plan.commitments || []).length})`}>
            {(plan.commitments || []).map((c, i) => (
              <li key={i}>{c?.committer} → {c?.recipient}: {c?.content}</li>
            ))}
          </Section>

          <Section title={`⚠️ 风险 (${(plan.risk_signals || []).length})`}>
            {(plan.risk_signals || []).map((r, i) => (
              <li key={i}><span className="text-red-700">[{r?.severity}]</span> {r?.title}</li>
            ))}
          </Section>
        </div>
        <div className="sticky bottom-0 flex justify-end gap-2 border-t border-gray-100 bg-white px-5 py-3">
          <button onClick={onClose} className="rounded-xl border border-gray-300 bg-white px-4 py-2 text-[12px]">
            回去修改
          </button>
          <button
            onClick={onConfirm}
            disabled={committing}
            className="inline-flex items-center gap-1.5 rounded-xl bg-[#5B7BFE] px-5 py-2 text-[12px] font-bold text-white hover:bg-[#4A6BE6] disabled:opacity-50 transition-colors"
          >
            {committing ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />}
            {committing ? '导入中...' : '确认 · 全部导入'}
          </button>
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }): JSX.Element {
  return (
    <div>
      <div className="mb-1 font-bold text-gray-700">{title}</div>
      <ul className="space-y-1 pl-3 text-gray-600">{children}</ul>
    </div>
  );
}
