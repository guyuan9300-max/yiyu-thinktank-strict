import React, { useCallback, useEffect, useState } from 'react';
import { AlertCircle, Download, Loader2, Save, Sparkles } from 'lucide-react';
import type { ReportRunSummary } from '../../../shared/types.js';
import type { DocumentAiAction } from '../../lib/api.js';
import {
  documentAiAction,
  draftReportSections,
  renderReportRun,
  saveReport,
} from '../../lib/api.js';
import {
  RichTextDocumentEditor,
  type RichTextDocumentEditorAiOpts,
  type RichTextDocumentEditorAiResult,
} from '../client_workspace/RichTextDocumentEditor.js';

interface ProjectReportEditorProps {
  run: ReportRunSummary;
  clientId: string;
  eventLineName?: string;
  readOnly?: boolean;
  onRunChange?: (run: ReportRunSummary) => void;
  onDownload?: (localPath: string, fileName: string) => Promise<void>;
}

const REPORT_AI_ACTIONS: DocumentAiAction[] = [
  'expand',
  'rewrite_pro',
  'rewrite_short',
  'summarize',
  'extract',
  'translate',
  'style_distilled',
  'insert_from_materials',
  'rewrite_by_strategy',
  'insert_data_table',
];

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

export default function ProjectReportEditor({
  run,
  clientId,
  eventLineName,
  readOnly = false,
  onRunChange,
  onDownload,
}: ProjectReportEditorProps): JSX.Element {
  const [title, setTitle] = useState(run.blueprint?.title || '项目报告');
  const [content, setContent] = useState(run.body_markdown || '');
  const [editorRevision, setEditorRevision] = useState(0);
  const [busyAction, setBusyAction] = useState<'generate' | 'save' | 'download' | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setTitle(run.blueprint?.title || '项目报告');
    setContent(run.body_markdown || '');
    setEditorRevision((value) => value + 1);
  }, [run.id]);

  const generateReport = useCallback(async () => {
    if (busyAction || readOnly) return;
    if (content.trim() && !window.confirm('将依据当前报告骨架重新撰写整篇报告，编辑框里的现有内容会被替换。是否继续？')) return;
    setBusyAction('generate');
    setMessage(null);
    setError(null);
    try {
      const generated = await draftReportSections(run.id, { max_workers: 4 });
      setContent(generated.body_markdown || '');
      setTitle(generated.blueprint?.title || title);
      setEditorRevision((value) => value + 1);
      onRunChange?.(generated);
      setMessage('Agent 已依据当前骨架完成整稿，可继续修改或使用 AI 优化。');
    } catch (cause) {
      setError(errorText(cause, '报告生成失败'));
    } finally {
      setBusyAction(null);
    }
  }, [busyAction, content, onRunChange, readOnly, run.id, title]);

  const saveChanges = useCallback(async (): Promise<ReportRunSummary | null> => {
    if (busyAction || readOnly) return null;
    if (!content.trim()) {
      setError('请先生成或填写报告正文');
      return null;
    }
    setBusyAction('save');
    setMessage(null);
    setError(null);
    try {
      const saved = await saveReport(run.id, {
        title: title.trim() || '项目报告',
        content_markdown: content,
        change_summary: '保存事件线报告修改',
      });
      onRunChange?.(saved);
      setMessage('修改已保存在本机当前事件线中。');
      return saved;
    } catch (cause) {
      setError(errorText(cause, '保存报告失败'));
      return null;
    } finally {
      setBusyAction(null);
    }
  }, [busyAction, content, onRunChange, readOnly, run.id, title]);

  const downloadReport = useCallback(async () => {
    if (busyAction || readOnly) return;
    if (!content.trim()) {
      setError('请先生成或填写报告正文');
      return;
    }
    setBusyAction('download');
    setMessage(null);
    setError(null);
    try {
      const saved = await saveReport(run.id, {
        title: title.trim() || '项目报告',
        content_markdown: content,
        change_summary: '下载前保存事件线报告修改',
      });
      onRunChange?.(saved);
      const rendered = await renderReportRun(saved.id, 'docx');
      if (onDownload) await onDownload(rendered.file_path, rendered.file_name);
      else await window.yiyuWorkbench?.saveFileAs(rendered.file_path, rendered.file_name);
      setMessage('报告已生成 Word 文件。');
    } catch (cause) {
      setError(errorText(cause, '下载报告失败'));
    } finally {
      setBusyAction(null);
    }
  }, [busyAction, content, onDownload, onRunChange, readOnly, run.id, title]);

  const runAiAction = useCallback(async (
    action: string,
    opts?: RichTextDocumentEditorAiOpts,
  ): Promise<RichTextDocumentEditorAiResult | void> => {
    if (!REPORT_AI_ACTIONS.includes(action as DocumentAiAction)) return;
    const selectionText = (opts?.selectionText || '').trim();
    if (!content.trim() && !selectionText && !(opts?.userRequest || '').trim()) {
      throw new Error('请先写一点内容，或在 AI 输入框说明要生成什么');
    }
    const result = await documentAiAction(clientId, {
      content,
      action: action as DocumentAiAction,
      userRequest: opts?.userRequest || '',
      creativityMode: opts?.creativityMode || 'balanced',
      activeSkillId: opts?.activeSkillId || null,
      activeSkillIds: opts?.activeAgentSkillIds || [],
      selectionText,
      workingDocumentIds: [],
      reportSourceSetId: run.source_set_id,
    });
    return {
      content: result.content,
      targetScope: result.targetScope || (selectionText ? 'selection' : 'cursor_insert'),
    };
  }, [clientId, content, run.source_set_id]);

  return (
    <section className="overflow-hidden rounded-xl border border-gray-200 bg-white">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 bg-blue-50/40 px-5 py-4">
        <div>
          <div className="flex items-center gap-2 text-[14px] font-semibold text-gray-900">
            <Sparkles size={16} className="text-blue-600" />项目报告
          </div>
          <p className="mt-1 text-[11px] text-gray-500">{eventLineName || '当前事件线'} · 依据已确认的报告骨架撰写</p>
        </div>
        <button
          type="button"
          onClick={() => void generateReport()}
          disabled={Boolean(busyAction) || readOnly}
          className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-[11.5px] font-medium text-white disabled:bg-gray-300"
        >
          {busyAction === 'generate' && <Loader2 size={14} className="animate-spin" />}
          {busyAction === 'generate' ? 'Agent 正在撰写' : '生成报告'}
        </button>
      </header>

      <div className="border-b border-gray-100 px-5 py-4">
        <label className="block text-[10.5px] font-medium text-gray-500">报告标题</label>
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          readOnly={readOnly}
          className="mt-1 w-full border-0 p-0 text-[18px] font-semibold text-gray-950 outline-none"
        />
        {run.blueprint?.subtitle && <p className="mt-1 text-[11px] text-gray-400">时间范围：{run.blueprint.subtitle}</p>}
      </div>

      {error && (
        <div className="mx-5 mt-4 flex items-start gap-2 rounded-md border border-rose-200 bg-rose-50 px-3 py-2.5 text-[11.5px] text-rose-700">
          <AlertCircle size={14} className="mt-0.5 shrink-0" />{error}
        </div>
      )}
      {message && <p className="mx-5 mt-4 rounded-md bg-emerald-50 px-3 py-2.5 text-[11.5px] text-emerald-700">{message}</p>}

      <RichTextDocumentEditor
        key={`${run.id}:${editorRevision}`}
        value={content}
        onChange={setContent}
        minHeight={560}
        readOnly={readOnly || busyAction === 'generate'}
        placeholder="点击顶部“生成报告”，Agent 会依据当前骨架撰写整稿；也可以直接开始写。"
        onAiAction={runAiAction}
        showDocumentTab={false}
      />

      <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-100 bg-gray-50/60 px-5 py-4">
        <p className="text-[10.5px] text-gray-400">仅保存在本机当前事件线，不会自动同步到工作台项目。</p>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void saveChanges()}
            disabled={Boolean(busyAction) || readOnly || !content.trim()}
            className="inline-flex items-center gap-2 rounded-md border border-gray-200 bg-white px-3.5 py-2 text-[11.5px] font-medium text-gray-700 disabled:opacity-40"
          >
            {busyAction === 'save' ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
            保存修改
          </button>
          <button
            type="button"
            onClick={() => void downloadReport()}
            disabled={Boolean(busyAction) || readOnly || !content.trim()}
            className="inline-flex items-center gap-2 rounded-md bg-gray-900 px-3.5 py-2 text-[11.5px] font-medium text-white disabled:bg-gray-300"
          >
            {busyAction === 'download' ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
            下载报告
          </button>
        </div>
      </footer>
    </section>
  );
}
