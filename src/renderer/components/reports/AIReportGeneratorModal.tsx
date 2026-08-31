import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  Download,
  FilePenLine,
  FileText,
  History,
  Loader2,
  RefreshCw,
  Save,
  Sparkles,
  X,
} from 'lucide-react';
import type {
  ReportArtifactSummary,
  ReportArtifactVersionSummary,
  ReportBlueprint,
  ReportFileFormat,
  ReportRunSummary,
  SectionContent,
} from '../../../shared/types.js';
import {
  draftReportBlueprint,
  draftReportSections,
  getReportRun,
  listReportArtifactVersions,
  renderReportArtifact,
  restoreReportArtifactVersion,
  saveReport,
  updateReportBlueprint,
} from '../../lib/api.js';

type Phase =
  | 'intent'
  | 'reviewing-blueprint'
  | 'drafting-sections'
  | 'reviewing-body'
  | 'saved'
  | 'failed';

interface AIReportGeneratorModalProps {
  eventLineId?: string;
  clientId?: string;
  eventLineName?: string;
  clientName?: string;
  onClose?: () => void;
  onDownload?: (localPath: string, fileName: string) => Promise<void>;
  onOpenSmartEditor?: (artifact: ReportArtifactSummary, reportRunId: string) => void;
  onSaved?: (artifact: ReportArtifactSummary, reportRunId: string) => void;
  onRunChange?: (run: ReportRunSummary) => void;
  embedded?: boolean;
  blueprintOnly?: boolean;
  initialRun?: ReportRunSummary | null;
  defaultPeriodStart?: string;
  defaultPeriodEnd?: string;
  workingDocuments?: Array<{ documentId: string; title: string }>;
  activeAgentSkills?: Array<{ skillId: string; shortName: string }>;
}

interface IntentForm {
  periodStart: string;
  periodEnd: string;
  intentHint: string;
  audienceHint: string;
  toneHint: string;
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '未知错误，请重试';
}

function defaultPeriod(): { start: string; end: string } {
  const now = new Date();
  const quarterStartMonth = Math.floor(now.getMonth() / 3) * 3;
  const start = new Date(now.getFullYear(), quarterStartMonth, 1);
  const end = new Date(now.getFullYear(), quarterStartMonth + 3, 0);
  const fmt = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  return { start: fmt(start), end: fmt(end) };
}

export default function AIReportGeneratorModal({
  eventLineId,
  clientId,
  eventLineName,
  clientName,
  onClose,
  onDownload,
  onOpenSmartEditor,
  onSaved,
  onRunChange,
  embedded = false,
  blueprintOnly = false,
  initialRun = null,
  defaultPeriodStart,
  defaultPeriodEnd,
  workingDocuments = [],
  activeAgentSkills = [],
}: AIReportGeneratorModalProps): JSX.Element {
  const [phase, setPhase] = useState<Phase>('intent');
  const [intent, setIntent] = useState<IntentForm>(() => {
    const period = defaultPeriod();
    return {
      periodStart: defaultPeriodStart || period.start,
      periodEnd: defaultPeriodEnd || period.end,
      intentHint: '',
      audienceHint: '客户决策层',
      toneHint: '客观、克制、可执行',
    };
  });
  const [run, setRun] = useState<ReportRunSummary | null>(null);
  const [blueprintDraft, setBlueprintDraft] = useState<ReportBlueprint | null>(null);
  const [overallFeedback, setOverallFeedback] = useState('');
  const [sectionFeedback, setSectionFeedback] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [pollEnabled, setPollEnabled] = useState(false);
  const [versions, setVersions] = useState<ReportArtifactVersionSummary[]>([]);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [blueprintSaving, setBlueprintSaving] = useState(false);
  const [blueprintDirty, setBlueprintDirty] = useState(false);
  const pollRef = useRef<number | null>(null);
  const onRunChangeRef = useRef(onRunChange);

  useEffect(() => {
    onRunChangeRef.current = onRunChange;
  }, [onRunChange]);

  const handleStart = useCallback(async () => {
    setBusy(true);
    setErrorMsg(null);
    try {
      const result = await draftReportBlueprint({
        event_line_id: eventLineId || null,
        client_id: clientId || null,
        period_start: intent.periodStart || null,
        period_end: intent.periodEnd || null,
        intent_hint: intent.intentHint || null,
        audience_hint: intent.audienceHint || null,
        tone_hint: intent.toneHint || null,
        workingDocumentIds: workingDocuments.map((item) => item.documentId),
        activeSkillIds: activeAgentSkills.map((item) => item.skillId),
      });
      setRun(result);
      setBlueprintDraft(result.blueprint);
      setBlueprintDirty(false);
      onRunChangeRef.current?.(result);
      setPhase(result.status === 'failed' ? 'failed' : 'reviewing-blueprint');
      if (result.status === 'failed') setErrorMsg(result.error_message || '报告骨架生成失败');
    } catch (error) {
      setErrorMsg(getErrorMessage(error));
    } finally {
      setBusy(false);
    }
  }, [activeAgentSkills, clientId, eventLineId, intent, workingDocuments]);

  const handleGenerateBody = useCallback(async () => {
    if (!run || !blueprintDraft) return;
    setBusy(true);
    setErrorMsg(null);
    try {
      const updated = await updateReportBlueprint(run.id, blueprintDraft);
      const drafting = await draftReportSections(updated.id, {
        max_workers: 4,
        overall_feedback: overallFeedback || null,
        section_feedback: sectionFeedback,
      });
      setRun(drafting);
      onRunChangeRef.current?.(drafting);
      setPhase('drafting-sections');
      setPollEnabled(true);
    } catch (error) {
      setErrorMsg(getErrorMessage(error));
    } finally {
      setBusy(false);
    }
  }, [blueprintDraft, overallFeedback, run, sectionFeedback]);

  const rewriteSections = useCallback(async (indices?: number[]) => {
    if (!run) return;
    setBusy(true);
    setErrorMsg(null);
    try {
      const result = await draftReportSections(run.id, {
        section_indices: indices,
        max_workers: indices?.length === 1 ? 1 : 4,
        overall_feedback: overallFeedback || null,
        section_feedback: sectionFeedback,
      });
      setRun(result);
      onRunChangeRef.current?.(result);
      setPhase('drafting-sections');
      setPollEnabled(true);
    } catch (error) {
      setErrorMsg(getErrorMessage(error));
    } finally {
      setBusy(false);
    }
  }, [overallFeedback, run, sectionFeedback]);

  const handleSave = useCallback(async () => {
    if (!run) return;
    setBusy(true);
    setErrorMsg(null);
    try {
      const saved = await saveReport(run.id, {
        title: run.blueprint?.title || null,
        change_summary: '人工确认并首次保存',
      });
      setRun(saved);
      onRunChangeRef.current?.(saved);
      setPhase('saved');
      if (saved.artifact) onSaved?.(saved.artifact, saved.id);
    } catch (error) {
      setErrorMsg(getErrorMessage(error));
    } finally {
      setBusy(false);
    }
  }, [onSaved, run]);

  const handleDownload = useCallback(async (format: ReportFileFormat) => {
    if (!run?.artifact) return;
    setBusy(true);
    setErrorMsg(null);
    try {
      const rendered = await renderReportArtifact(run.artifact.id, format);
      if (onDownload) {
        await onDownload(rendered.file_path, rendered.file_name);
      } else {
        await window.yiyuWorkbench?.saveFileAs(rendered.file_path, rendered.file_name);
      }
    } catch (error) {
      setErrorMsg(getErrorMessage(error));
    } finally {
      setBusy(false);
    }
  }, [onDownload, run]);

  const loadVersions = useCallback(async () => {
    if (!run?.artifact) return;
    setVersionsOpen(true);
    setErrorMsg(null);
    try {
      setVersions(await listReportArtifactVersions(run.artifact.id));
    } catch (error) {
      setErrorMsg(getErrorMessage(error));
    }
  }, [run]);

  const handleRestore = useCallback(async (version: number) => {
    if (!run?.artifact) return;
    setBusy(true);
    setErrorMsg(null);
    try {
      const artifact = await restoreReportArtifactVersion(run.artifact.id, {
        expected_version: run.artifact.latest_version,
        restore_version: version,
        change_summary: `恢复第 ${version} 版`,
      });
      setRun({ ...run, artifact, output_files: {} });
      setVersions(await listReportArtifactVersions(artifact.id));
    } catch (error) {
      setErrorMsg(getErrorMessage(error));
    } finally {
      setBusy(false);
    }
  }, [run]);

  useEffect(() => {
    if (!pollEnabled || !run) return;
    const runId = run.id;
    const tick = async () => {
      try {
        const updated = await getReportRun(runId);
        setRun(updated);
        onRunChangeRef.current?.(updated);
        const running = updated.sections_status.some((status) => status === 'drafting' || status === 'pending');
        if (!running) {
          setPollEnabled(false);
          setPhase('reviewing-body');
          if (updated.sections_status.every((status) => status === 'failed')) {
            setErrorMsg(updated.error_message || '正文生成失败，可修改意见后重试');
          }
        }
      } catch {
        // 短暂断线时保留当前进度，下次继续查询。
      }
    };
    void tick();
    pollRef.current = window.setInterval(tick, 2500);
    return () => {
      if (pollRef.current !== null) window.clearInterval(pollRef.current);
      pollRef.current = null;
    };
  }, [pollEnabled, run?.id]);

  useEffect(() => {
    if (!blueprintOnly || !blueprintDirty || phase !== 'reviewing-blueprint' || !run || !blueprintDraft) return undefined;
    const timer = window.setTimeout(async () => {
      setBlueprintSaving(true);
      setErrorMsg(null);
      try {
        const updated = await updateReportBlueprint(run.id, blueprintDraft);
        setRun(updated);
        setBlueprintDirty(false);
        onRunChangeRef.current?.(updated);
      } catch (error) {
        setErrorMsg(getErrorMessage(error));
      } finally {
        setBlueprintSaving(false);
      }
    }, 700);
    return () => window.clearTimeout(timer);
  }, [blueprintDraft, blueprintDirty, blueprintOnly, phase, run?.id]);

  useEffect(() => {
    if (!initialRun) return;
    setRun(initialRun);
    setBlueprintDraft(initialRun.blueprint);
    setBlueprintDirty(false);
    setErrorMsg(initialRun.error_message || null);
    if (blueprintOnly && initialRun.blueprint) {
      setPhase('reviewing-blueprint');
      setPollEnabled(false);
    } else if (initialRun.artifact) {
      setPhase('saved');
      setPollEnabled(false);
    } else if (initialRun.status === 'drafting') {
      setPhase('drafting-sections');
      setPollEnabled(true);
    } else if (initialRun.status === 'body_ready') {
      setPhase('reviewing-body');
      setPollEnabled(false);
    } else if (initialRun.status === 'failed') {
      setPhase('failed');
      setPollEnabled(false);
    } else if (initialRun.blueprint) {
      setPhase('reviewing-blueprint');
      setPollEnabled(false);
    }
  }, [blueprintOnly, eventLineId, initialRun?.id, initialRun?.updated_at]);

  const allSectionsDone = useMemo(
    () => !!run?.sections_status.length && run.sections_status.every((status) => status === 'done'),
    [run],
  );

  const body = (
    <>
        <div className="flex items-start justify-between border-b border-gray-100 bg-blue-50/50 px-6 py-4">
          <div className="flex items-start gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-600 text-white"><Sparkles size={18} /></div>
            <div>
              <h2 className="text-[15px] font-bold text-gray-900">{blueprintOnly ? '报告骨架' : '生成项目报告'}</h2>
              <p className="mt-0.5 text-[12px] text-gray-500">
                {eventLineName || clientName || '当前项目'}{eventLineName && clientName ? ` · ${clientName}` : ''} · {phaseLabel(phase)}
              </p>
            </div>
          </div>
          {!embedded && onClose && <button type="button" onClick={onClose} className="rounded-md p-1.5 text-gray-400 hover:bg-white hover:text-gray-700" aria-label="关闭"><X size={18} /></button>}
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto px-6 py-5">
          {errorMsg && <ErrorBanner message={errorMsg} onDismiss={() => setErrorMsg(null)} />}
          {phase === 'intent' && (
            <>
              <IntentBlock intent={intent} onChange={setIntent} onSubmit={handleStart} busy={busy} />
            </>
          )}
          {phase === 'reviewing-blueprint' && blueprintDraft && (
              <BlueprintEditor
                blueprint={blueprintDraft}
                onChange={(value) => {
                  setBlueprintDraft(value);
                  setBlueprintDirty(true);
                }}
                overallFeedback={overallFeedback}
                onOverallFeedbackChange={setOverallFeedback}
                onGenerate={blueprintOnly ? undefined : () => void handleGenerateBody()}
                blueprintSaving={blueprintSaving}
                busy={busy}
            />
          )}
          {phase === 'drafting-sections' && run?.blueprint && <DraftingBlock run={run} />}
          {phase === 'reviewing-body' && run?.blueprint && (
            <BodyReview
              run={run}
              overallFeedback={overallFeedback}
              onOverallFeedbackChange={setOverallFeedback}
              sectionFeedback={sectionFeedback}
              onSectionFeedbackChange={setSectionFeedback}
              onRewriteAll={() => void rewriteSections()}
              onRewriteSection={(index) => void rewriteSections([index])}
              onSave={() => void handleSave()}
              allSectionsDone={allSectionsDone}
              busy={busy}
            />
          )}
          {phase === 'saved' && run?.artifact && (
            <SavedReport
              run={run}
              versions={versions}
              versionsOpen={versionsOpen}
              onOpenVersions={() => void loadVersions()}
              onRestore={(version) => void handleRestore(version)}
              onSmartEdit={() => onOpenSmartEditor?.(run.artifact!, run.id)}
              onDownload={(format) => void handleDownload(format)}
              busy={busy}
            />
          )}
          {phase === 'failed' && (
            <div className="space-y-4 py-8 text-center">
              <AlertCircle size={34} className="mx-auto text-red-500" />
              <p className="text-sm text-gray-700">{errorMsg || '报告生成失败'}</p>
              <button type="button" onClick={() => { setPhase('intent'); setRun(null); setErrorMsg(null); }} className="rounded-md border border-gray-200 px-4 py-2 text-xs text-gray-700">重新开始</button>
            </div>
          )}
        </div>
    </>
  );
  if (embedded) {
    return <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">{body}</div>;
  }
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="flex h-[88vh] w-full max-w-4xl flex-col overflow-hidden rounded-lg bg-white shadow-2xl">{body}</div>
    </div>
  );
}

function phaseLabel(phase: Phase): string {
  const labels: Record<Phase, string> = {
    intent: '设置报告意图',
    'reviewing-blueprint': '审阅报告骨架',
    'drafting-sections': '生成完整正文',
    'reviewing-body': '检查与修改报告',
    saved: '已保存共享报告',
    failed: '生成失败',
  };
  return labels[phase];
}

function ErrorBanner({ message, onDismiss }: { message: string; onDismiss: () => void }) {
  return (
    <div className="flex items-start gap-3 rounded-md border border-red-200 bg-red-50 px-4 py-3">
      <AlertCircle size={16} className="mt-0.5 shrink-0 text-red-500" />
      <p className="flex-1 text-[12px] text-red-700">{message}</p>
      <button type="button" onClick={onDismiss} className="text-red-400" aria-label="关闭"><X size={14} /></button>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block"><span className="mb-1.5 block text-[11.5px] font-medium text-gray-600">{label}</span>{children}</label>;
}

function IntentBlock({ intent, onChange, onSubmit, busy }: {
  intent: IntentForm;
  onChange: (value: IntentForm) => void;
  onSubmit: () => void;
  busy: boolean;
}) {
  const inputClass = 'w-full rounded-md border border-gray-200 px-3 py-2 text-[12px] outline-none focus:border-blue-500';
  return (
    <div className="space-y-4">
      <p className="text-[12.5px] leading-6 text-gray-600">Agent 会沿已确认的正式主线，结合报告意图、读者和基调直接生成可编辑骨架；项目知识只补充背景与证据。</p>
      <div className="grid grid-cols-2 gap-3">
        <Field label="报告期间起"><input type="date" value={intent.periodStart} onChange={(e) => onChange({ ...intent, periodStart: e.target.value })} className={inputClass} /></Field>
        <Field label="报告期间止"><input type="date" value={intent.periodEnd} onChange={(e) => onChange({ ...intent, periodEnd: e.target.value })} className={inputClass} /></Field>
      </div>
      <Field label="这份报告需要回答什么"><textarea rows={3} value={intent.intentHint} onChange={(e) => onChange({ ...intent, intentHint: e.target.value })} className={`${inputClass} resize-none`} placeholder="例如：向资方说明项目进展、关键成果与下一步安排" /></Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="目标读者"><input value={intent.audienceHint} onChange={(e) => onChange({ ...intent, audienceHint: e.target.value })} className={inputClass} /></Field>
        <Field label="期望基调"><input value={intent.toneHint} onChange={(e) => onChange({ ...intent, toneHint: e.target.value })} className={inputClass} /></Field>
      </div>
      <div className="flex justify-end border-t border-gray-100 pt-4"><button type="button" onClick={onSubmit} disabled={busy || !intent.periodStart || !intent.periodEnd} className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-xs font-medium text-white disabled:bg-gray-300">{busy && <Loader2 size={14} className="animate-spin" />}{busy ? 'Agent 正在梳理骨架' : '生成报告骨架'}</button></div>
    </div>
  );
}

function BlueprintEditor({ blueprint, onChange, overallFeedback, onOverallFeedbackChange, onGenerate, blueprintSaving, busy }: {
  blueprint: ReportBlueprint;
  onChange: (value: ReportBlueprint) => void;
  overallFeedback: string;
  onOverallFeedbackChange: (value: string) => void;
  onGenerate?: () => void;
  blueprintSaving?: boolean;
  busy: boolean;
}) {
  const updateSection = (index: number, patch: Partial<ReportBlueprint['sections'][number]>) => {
    const sections = blueprint.sections.map((section, i) => i === index ? { ...section, ...patch } : section);
    onChange({ ...blueprint, sections });
  };
  const move = (index: number, direction: -1 | 1) => {
    const next = index + direction;
    if (next < 0 || next >= blueprint.sections.length) return;
    const sections = [...blueprint.sections];
    [sections[index], sections[next]] = [sections[next], sections[index]];
    onChange({ ...blueprint, sections });
  };
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <Field label="报告标题"><input value={blueprint.title} onChange={(e) => onChange({ ...blueprint, title: e.target.value })} className="w-full rounded-md border border-gray-200 px-3 py-2 text-xs" /></Field>
        <Field label="时间范围"><input value={blueprint.subtitle || ''} onChange={(e) => onChange({ ...blueprint, subtitle: e.target.value || null })} className="w-full rounded-md border border-gray-200 px-3 py-2 text-xs" /></Field>
        <Field label="目标读者"><input value={blueprint.audience} onChange={(e) => onChange({ ...blueprint, audience: e.target.value })} className="w-full rounded-md border border-gray-200 px-3 py-2 text-xs" /></Field>
        <Field label="语气"><input value={blueprint.tone} onChange={(e) => onChange({ ...blueprint, tone: e.target.value })} className="w-full rounded-md border border-gray-200 px-3 py-2 text-xs" /></Field>
      </div>
      <div>
        <div className="mb-2 flex items-center justify-between"><h3 className="text-xs font-semibold text-gray-800">报告骨架</h3><span className="text-[11px] text-gray-400">可改标题、说明和顺序</span></div>
        <div className="space-y-2">
          {blueprint.sections.map((section, index) => (
            <div key={`${index}-${section.title}`} className="grid grid-cols-[32px_1fr] gap-2 rounded-md border border-gray-200 p-3">
              <div className="flex flex-col gap-1">
                <button type="button" onClick={() => move(index, -1)} disabled={index === 0} className="rounded p-1 text-gray-400 hover:bg-gray-100 disabled:opacity-20" aria-label="上移"><ArrowUp size={14} /></button>
                <button type="button" onClick={() => move(index, 1)} disabled={index === blueprint.sections.length - 1} className="rounded p-1 text-gray-400 hover:bg-gray-100 disabled:opacity-20" aria-label="下移"><ArrowDown size={14} /></button>
              </div>
              <div className="space-y-2">
                <input value={section.title} onChange={(e) => updateSection(index, { title: e.target.value })} className="w-full border-0 p-0 text-[12.5px] font-semibold text-gray-900 outline-none" />
                <textarea rows={2} value={section.goal} onChange={(e) => updateSection(index, { goal: e.target.value })} className="w-full resize-none rounded border border-gray-100 px-2 py-1.5 text-[11.5px] text-gray-600 outline-none focus:border-blue-300" />
              </div>
            </div>
          ))}
        </div>
      </div>
      {onGenerate ? (
        <>
          <Field label="整份报告的补充要求"><textarea rows={3} value={overallFeedback} onChange={(e) => onOverallFeedbackChange(e.target.value)} className="w-full resize-none rounded-md border border-gray-200 px-3 py-2 text-xs" placeholder="例如：重点说明对组织方法的影响，不夸大尚未验证的成果" /></Field>
          <div className="flex justify-end border-t border-gray-100 pt-4"><button type="button" onClick={onGenerate} disabled={busy || !blueprint.title.trim() || blueprint.sections.some((section) => !section.title.trim())} className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-xs font-medium text-white disabled:bg-gray-300">{busy && <Loader2 size={14} className="animate-spin" />}按当前骨架生成项目报告</button></div>
        </>
      ) : (
        <p className="border-t border-gray-100 pt-3 text-right text-[10.5px] text-gray-400">
          {blueprintSaving ? '正在保存修改…' : '骨架已保存；完整正文请在“项目报告”中生成'}
        </p>
      )}
    </div>
  );
}

function DraftingBlock({ run }: { run: ReportRunSummary }) {
  const done = run.sections_status.filter((status) => status === 'done').length;
  return (
    <div className="space-y-4">
      <div className="rounded-md border border-blue-100 bg-blue-50 px-4 py-3"><div className="flex items-center justify-between"><div><p className="text-[13px] font-semibold text-gray-900">{run.blueprint?.title}</p><p className="mt-1 text-[11.5px] text-gray-500">正在按已确认骨架撰写正文</p></div><span className="text-sm font-bold text-blue-600">{done}/{run.sections_status.length}</span></div></div>
      {run.blueprint?.sections.map((section, index) => {
        const status = run.sections_status[index] || 'pending';
        return <div key={index} className="flex items-center gap-3 rounded-md border border-gray-100 px-3 py-2.5">{status === 'done' ? <CheckCircle2 size={16} className="text-green-500" /> : status === 'failed' ? <AlertCircle size={16} className="text-red-500" /> : <Loader2 size={16} className="animate-spin text-blue-500" />}<div className="min-w-0 flex-1"><p className="truncate text-xs font-medium text-gray-800">{section.title}</p><p className="truncate text-[10.5px] text-gray-400">{section.goal}</p></div><span className="text-[10.5px] text-gray-400">{status === 'done' ? '已完成' : status === 'failed' ? '生成失败' : '生成中'}</span></div>;
      })}
    </div>
  );
}

function BodyReview({ run, overallFeedback, onOverallFeedbackChange, sectionFeedback, onSectionFeedbackChange, onRewriteAll, onRewriteSection, onSave, allSectionsDone, busy }: {
  run: ReportRunSummary;
  overallFeedback: string;
  onOverallFeedbackChange: (value: string) => void;
  sectionFeedback: Record<number, string>;
  onSectionFeedbackChange: (value: Record<number, string>) => void;
  onRewriteAll: () => void;
  onRewriteSection: (index: number) => void;
  onSave: () => void;
  allSectionsDone: boolean;
  busy: boolean;
}) {
  return (
    <div className="space-y-5">
      <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-[11.5px] leading-5 text-amber-800">当前是尚未保存的生成结果。检查完整后点击“保存报告”，才会成为组织共享版本。</div>
      <article className="space-y-5 rounded-md border border-gray-200 bg-white px-6 py-5">
        <header><h1 className="text-xl font-bold text-gray-950">{run.blueprint?.title}</h1>{run.blueprint?.subtitle && <p className="mt-1 text-sm text-gray-500">{run.blueprint.subtitle}</p>}</header>
        {run.sections.map((section, index) => (
          <SectionPreview key={index} index={index} section={section} status={run.sections_status[index]} feedback={sectionFeedback[index] || ''} onFeedback={(value) => onSectionFeedbackChange({ ...sectionFeedback, [index]: value })} onRewrite={() => onRewriteSection(index)} busy={busy} />
        ))}
      </article>
      {run.warnings.length > 0 && <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3"><p className="text-xs font-semibold text-amber-900">材料缺口与生成提示</p><ul className="mt-2 space-y-1 text-[11.5px] text-amber-800">{run.warnings.map((warning, index) => <li key={index}>• {warning}</li>)}</ul></div>}
      <Field label="整稿修改意见"><textarea rows={3} value={overallFeedback} onChange={(e) => onOverallFeedbackChange(e.target.value)} className="w-full resize-none rounded-md border border-gray-200 px-3 py-2 text-xs" placeholder="输入后可重写整稿；不能要求 AI 新增没有证据的事实" /></Field>
      <div className="flex items-center justify-between border-t border-gray-100 pt-4"><button type="button" onClick={onRewriteAll} disabled={busy} className="inline-flex items-center gap-2 rounded-md border border-gray-200 px-3 py-2 text-xs text-gray-700 disabled:opacity-50"><RefreshCw size={14} />按意见重写整稿</button><button type="button" onClick={onSave} disabled={busy || !allSectionsDone} className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-xs font-medium text-white disabled:bg-gray-300">{busy ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}保存报告</button></div>
    </div>
  );
}

function SectionPreview({ index, section, status, feedback, onFeedback, onRewrite, busy }: { index: number; section: SectionContent | null; status: string; feedback: string; onFeedback: (value: string) => void; onRewrite: () => void; busy: boolean }) {
  if (!section) return <section className="rounded-md border border-red-200 bg-red-50 p-4"><p className="text-xs font-semibold text-red-700">第 {index + 1} 章生成失败</p><div className="mt-2 flex gap-2"><input value={feedback} onChange={(e) => onFeedback(e.target.value)} className="flex-1 rounded border border-red-200 px-2 py-1.5 text-xs" placeholder="补充这一章的修改意见" /><button type="button" onClick={onRewrite} disabled={busy} className="rounded bg-white px-3 py-1.5 text-xs text-red-700">重试</button></div></section>;
  return (
    <section className="space-y-3 border-t border-gray-100 pt-5 first:border-0 first:pt-0">
      <h2 className="text-base font-bold text-gray-900">{section.plan.title}</h2>
      <div className="whitespace-pre-wrap text-[13px] leading-7 text-gray-700">{section.markdown}</div>
      {section.citations.length > 0 && <details className="text-[11px] text-gray-500"><summary className="cursor-pointer">查看引用来源（{section.citations.length}）</summary><ul className="mt-2 space-y-1 rounded bg-gray-50 p-3">{section.citations.map((citation) => <li key={`${citation.type}-${citation.id}`}><strong>{citation.label}</strong>{citation.excerpt ? `：${citation.excerpt}` : ''}</li>)}</ul></details>}
      {section.data_source_annotation && <p className="text-[10.5px] text-gray-400">数据源：{section.data_source_annotation}</p>}
      <div className="flex gap-2"><input value={feedback} onChange={(e) => onFeedback(e.target.value)} className="flex-1 rounded border border-gray-200 px-2 py-1.5 text-[11.5px]" placeholder="对本章提修改意见" /><button type="button" onClick={onRewrite} disabled={busy || status === 'drafting'} className="rounded border border-gray-200 px-3 py-1.5 text-[11.5px] text-gray-600 disabled:opacity-40">重写本章</button></div>
    </section>
  );
}

function SavedReport({ run, versions, versionsOpen, onOpenVersions, onRestore, onSmartEdit, onDownload, busy }: {
  run: ReportRunSummary;
  versions: ReportArtifactVersionSummary[];
  versionsOpen: boolean;
  onOpenVersions: () => void;
  onRestore: (version: number) => void;
  onSmartEdit: () => void;
  onDownload: (format: ReportFileFormat) => void;
  busy: boolean;
}) {
  const artifact = run.artifact!;
  const blocked = artifact.availability_status === 'blocked';
  return (
    <div className="space-y-5">
      <div className={`rounded-md border px-4 py-4 ${blocked ? 'border-red-200 bg-red-50' : 'border-green-200 bg-green-50'}`}><div className="flex gap-3"><CheckCircle2 size={22} className={blocked ? 'text-red-500' : 'text-green-600'} /><div><p className="text-sm font-semibold text-gray-900">{blocked ? '报告暂不可用' : '报告已保存'}</p><p className="mt-1 text-xs text-gray-600">{artifact.title} · 第 {artifact.latest_version} 版</p>{blocked ? <p className="mt-1 text-[11px] text-red-700">{artifact.availability_reason || '报告中的证据已撤销、删除或当前账号无权读取。'}</p> : artifact.is_stale && <p className="mt-1 text-[11px] text-amber-700">事件线或证据已变化，这份报告已标记为过期，历史版本仍可查看和下载。</p>}</div></div></div>
      <div className="flex flex-wrap justify-center gap-3"><button type="button" onClick={onSmartEdit} disabled={blocked} className="inline-flex items-center gap-2 rounded-md bg-blue-600 px-4 py-2 text-xs font-medium text-white disabled:bg-gray-300"><FilePenLine size={14} />智能编辑</button>{(['docx', 'pdf', 'md'] as ReportFileFormat[]).map((format) => <button key={format} type="button" onClick={() => onDownload(format)} disabled={busy || blocked} className="inline-flex items-center gap-2 rounded-md border border-gray-200 px-3 py-2 text-xs text-gray-700 disabled:opacity-50"><Download size={14} />下载 {format.toUpperCase()}</button>)}</div>
      <div className="border-t border-gray-100 pt-4"><button type="button" onClick={onOpenVersions} disabled={blocked} className="inline-flex items-center gap-2 text-xs text-gray-600 disabled:text-gray-300"><History size={14} />版本记录</button>{versionsOpen && <div className="mt-3 space-y-2">{versions.map((version) => <div key={version.version} className="flex items-center justify-between rounded-md border border-gray-100 px-3 py-2"><div><p className="text-xs font-medium text-gray-800">第 {version.version} 版{version.version === artifact.latest_version ? ' · 当前' : ''}</p><p className="mt-0.5 text-[10.5px] text-gray-400">{version.change_summary || '保存报告'} · {version.created_by_display_name || '组织成员'} · {version.created_at}</p></div>{version.version !== artifact.latest_version && <button type="button" onClick={() => onRestore(version.version)} disabled={busy} className="rounded border border-gray-200 px-2 py-1 text-[11px] text-gray-600">恢复此版</button>}</div>)}</div>}</div>
    </div>
  );
}
