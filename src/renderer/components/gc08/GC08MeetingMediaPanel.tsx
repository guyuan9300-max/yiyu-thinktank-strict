import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { FileAudio, FileText, FolderOpen, Settings, Upload } from 'lucide-react';

import { gc08Api, type GC08ApiClient } from './gc08Api';
import {
  gc08CanCreateMinutes,
  gc08CanPublish,
  gc08CanRetryTranscription,
  gc08IsRecordingPath,
  gc08StatePresentation,
  type GC08MeetingMaterial,
  type GC08RecordingDetail,
  type GC08StatePresentation,
} from './gc08Contract';

export interface GC08MeetingMediaPanelProps {
  clientId: string;
  meetingId: string;
  apiClient?: GC08ApiClient;
  compact?: boolean;
  asrInstalled?: boolean | null;
  onOpenAsrSettings?: () => void;
  showImportRecording?: boolean;
  headerActions?: ReactNode;
}

type BusyAction = 'loading' | 'registering' | 'transcribing' | 'drafting' | 'publishing' | null;

const TONE_CLASSES: Record<GC08StatePresentation['tone'], string> = {
  neutral: 'border-slate-200 bg-slate-50 text-slate-700',
  working: 'border-blue-200 bg-blue-50 text-blue-800',
  warning: 'border-amber-200 bg-amber-50 text-amber-900',
  error: 'border-rose-200 bg-rose-50 text-rose-800',
  success: 'border-emerald-200 bg-emerald-50 text-emerald-800',
};

function storageKey(clientId: string, meetingId: string): string {
  return `yiyu.gc08.last-recording.${clientId}.${meetingId}`;
}

export function saveLastRecording(clientId: string, meetingId: string, recordingId: string): void {
  try {
    window.localStorage.setItem(storageKey(clientId, meetingId), recordingId);
  } catch {
    // The database remains authoritative when renderer storage is unavailable.
  }
}

function readLastRecording(clientId: string, meetingId: string): string {
  try {
    return window.localStorage.getItem(storageKey(clientId, meetingId))?.trim() || '';
  } catch {
    return '';
  }
}

function clearLastRecording(clientId: string, meetingId: string): void {
  try {
    window.localStorage.removeItem(storageKey(clientId, meetingId));
  } catch {
    // No renderer pointer to clear.
  }
}

function statusCard(
  title: string,
  presentation: GC08StatePresentation,
  code?: string | null,
  message?: string | null,
) {
  return (
    <div className={`rounded-2xl border px-4 py-3 ${TONE_CLASSES[presentation.tone]}`}>
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-bold">{title}</p>
        <span className="rounded-full bg-white/70 px-2 py-1 text-[11px] font-bold">
          {presentation.label}
        </span>
      </div>
      <p className="mt-2 text-xs leading-5 opacity-90">{message || presentation.description}</p>
      {code && <p className="mt-1 break-all font-mono text-[10px] opacity-70">{code}</p>}
    </div>
  );
}

export function GC08MeetingMediaPanel({
  clientId,
  meetingId,
  apiClient = gc08Api,
  compact = false,
  asrInstalled = null,
  onOpenAsrSettings,
  showImportRecording = true,
  headerActions,
}: GC08MeetingMediaPanelProps) {
  const [audioPath, setAudioPath] = useState('');
  const [detail, setDetail] = useState<GC08RecordingDetail | null>(null);
  const [meetingMaterials, setMeetingMaterials] = useState<GC08MeetingMaterial[]>([]);
  const [minutesTitle, setMinutesTitle] = useState('');
  const [minutesMarkdown, setMinutesMarkdown] = useState('');
  const [publishConfirmed, setPublishConfirmed] = useState(false);
  const [busy, setBusy] = useState<BusyAction>(null);
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; text: string } | null>(null);
  const publishKeyRef = useRef('');

  const fetchStoredRecording = useCallback(async (): Promise<GC08RecordingDetail | null> => {
    if (!clientId.trim() || !meetingId.trim()) return null;
    const latest = await apiClient.getLatestRecording(clientId, meetingId).catch(() => null);
    if (latest) {
      saveLastRecording(clientId, meetingId, latest.recordingId);
      return latest;
    }
    const recordingId = readLastRecording(clientId, meetingId);
    if (recordingId) {
      const stored = await apiClient.getRecording(clientId, meetingId, recordingId).catch(() => null);
      if (stored) return stored;
    }
    return null;
  }, [apiClient, clientId, meetingId]);

  const fetchMeetingMaterials = useCallback(async (): Promise<GC08MeetingMaterial[]> => {
    if (!clientId.trim() || !meetingId.trim()) return [];
    return apiClient.getMeetingMaterials(clientId, meetingId);
  }, [apiClient, clientId, meetingId]);

  const transcriptionPresentation = useMemo(
    () => gc08StatePresentation(detail?.transcription.status || (busy === 'transcribing' ? 'processing' : 'not_requested')),
    [busy, detail?.transcription.status],
  );
  const minutesPresentation = useMemo(
    () => gc08StatePresentation(
      detail?.minutes?.publicationState === 'published'
        ? 'ready'
        : detail?.minutes
          ? 'ready'
          : busy === 'drafting'
            ? 'processing'
            : detail?.minutesProcessing.status || 'not_requested',
    ),
    [busy, detail?.minutes, detail?.minutesProcessing.status],
  );

  useEffect(() => {
    let cancelled = false;
    setDetail(null);
    setMeetingMaterials([]);
    setMinutesTitle('');
    setMinutesMarkdown('');
    setPublishConfirmed(false);
    setNotice(null);
    publishKeyRef.current = '';
    if (!clientId.trim() || !meetingId.trim()) return () => { cancelled = true; };
    setBusy('loading');
    void Promise.all([
      fetchStoredRecording().catch(() => null),
      fetchMeetingMaterials().catch(() => []),
    ])
      .then(([next, materials]) => {
        if (cancelled) return;
        setMeetingMaterials(materials);
        if (!next) return;
        setDetail(next);
        setMinutesTitle(next.minutes?.title || '会议纪要');
        setMinutesMarkdown(next.minutes?.minutesMarkdown || '');
      })
      .catch(() => {
        if (!cancelled) clearLastRecording(clientId, meetingId);
      })
      .finally(() => {
        if (!cancelled) setBusy(null);
      });
    return () => { cancelled = true; };
  }, [clientId, meetingId, fetchMeetingMaterials, fetchStoredRecording]);

  useEffect(() => {
    const refreshAfterFinder = () => {
      void fetchStoredRecording()
        .then((next) => {
          if (next) setDetail(next);
        })
        .catch(() => undefined);
    };
    window.addEventListener('focus', refreshAfterFinder);
    return () => window.removeEventListener('focus', refreshAfterFinder);
  }, [fetchStoredRecording]);

  const transcriptionJobStatus = String(detail?.transcriptionProgress?.status || '');
  useEffect(() => {
    if (!['queued', 'processing'].includes(transcriptionJobStatus)) return;
    let cancelled = false;
    let timer = 0;
    const poll = async () => {
      try {
        const next = await fetchStoredRecording();
        if (cancelled || !next) return;
        setDetail(next);
        if (['queued', 'processing'].includes(String(next.transcriptionProgress?.status || ''))) {
          timer = window.setTimeout(poll, 700);
        }
      } catch {
        if (!cancelled) timer = window.setTimeout(poll, 1200);
      }
    };
    timer = window.setTimeout(poll, 500);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [fetchStoredRecording, transcriptionJobStatus]);

  const run = async (action: Exclude<BusyAction, 'loading' | null>, operation: () => Promise<GC08RecordingDetail>) => {
    setBusy(action);
    setNotice(null);
    try {
      const next = await operation();
      setDetail(next);
      saveLastRecording(clientId, meetingId, next.recordingId);
      setMinutesTitle(next.minutes?.title || minutesTitle || '会议纪要');
      setMinutesMarkdown(next.minutes?.minutesMarkdown || minutesMarkdown);
      setNotice({ tone: 'success', text: action === 'registering' ? '本机录音已登记。' : action === 'transcribing' ? '转写状态已更新。' : '纪要草稿已保存。' });
      return next;
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : 'GC-08 操作失败' });
      return null;
    } finally {
      setBusy(null);
    }
  };

  const chooseRecording = async () => {
    setNotice(null);
    try {
      const selected = await window.yiyuWorkbench.selectFiles();
      const candidate = selected.find(gc08IsRecordingPath) || '';
      if (!candidate) {
        setNotice({ tone: 'error', text: selected.length ? '请选择受支持的音频或视频录音文件。' : '没有选择录音文件。' });
        return;
      }
      setAudioPath(candidate);
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '无法打开文件选择器' });
    }
  };

  const registerRecording = async () => {
    const normalizedPath = audioPath.trim();
    if (!gc08IsRecordingPath(normalizedPath)) {
      setNotice({ tone: 'error', text: '请填写或选择有效的本机录音路径。' });
      return;
    }
    await run('registering', () => apiClient.registerRecording(clientId, meetingId, { audioPath: normalizedPath }));
  };

  const importRecording = async () => {
    setNotice(null);
    try {
      const selected = await window.yiyuWorkbench.selectFiles();
      const candidate = selected.find(gc08IsRecordingPath) || '';
      if (!candidate) {
        setNotice({ tone: 'error', text: selected.length ? '请选择受支持的音频或视频录音文件。' : '没有选择录音文件。' });
        return;
      }
      await run('registering', () => apiClient.registerRecording(
        clientId,
        meetingId,
        { audioPath: candidate },
      ));
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '导入录音失败' });
    }
  };

  const transcribe = async () => {
    if (!detail) return;
    const force = gc08CanRetryTranscription(detail.transcription.status);
    await run('transcribing', () => apiClient.transcribe(
      clientId,
      meetingId,
      detail.recordingId,
      { language: 'auto', force },
    ));
  };

  const createDraft = async () => {
    if (!detail) return;
    await run('drafting', () => apiClient.createMinutesDraft(
      clientId,
      meetingId,
      detail.recordingId,
      {
        title: minutesTitle.trim() || undefined,
        minutesMarkdown: minutesMarkdown.trim() || undefined,
        force: Boolean(detail.minutes),
      },
    ));
  };

  const publish = async () => {
    if (!detail || !gc08CanPublish(detail, publishConfirmed, busy !== null)) return;
    setBusy('publishing');
    setNotice(null);
    if (!publishKeyRef.current) {
      publishKeyRef.current = globalThis.crypto?.randomUUID?.() || `gc08-publish-${Date.now()}`;
    }
    try {
      const result = await apiClient.publishMinutes(
        clientId,
        meetingId,
        detail.recordingId,
        { expectedVersion: 0 },
        publishKeyRef.current,
      );
      setDetail(result.local);
      setPublishConfirmed(false);
      setNotice({ tone: 'success', text: result.cloud.idempotentReplay ? '正式纪要已确认，无重复发布。' : '正式纪要已安全发布到组织云。' });
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof Error ? error.message : '正式纪要发布失败，可使用同一回执重试。' });
    } finally {
      setBusy(null);
    }
  };

  const transcriptionButtonLabel = busy === 'transcribing'
    ? '转写处理中…'
    : gc08CanRetryTranscription(detail?.transcription.status)
      ? '重试转写'
      : '开始本机转写';
  const published = detail?.minutes?.publicationState === 'published';
  const canPublish = gc08CanPublish(detail, publishConfirmed, busy !== null);

  const revealLocalFile = async (
    pathKey: 'recordingPath' | 'transcriptionPath',
    label: string,
  ) => {
    const freshDetail = await fetchStoredRecording().catch(() => null);
    if (freshDetail) setDetail(freshDetail);
    const path = (freshDetail || detail)?.localFiles?.[pathKey];
    if (!path) {
      setNotice({ tone: 'error', text: `${label}尚未生成或当前设备不可用。` });
      return;
    }
    const revealed = await window.yiyuWorkbench.revealInFinder(path).catch(() => false);
    if (!revealed) setNotice({ tone: 'error', text: `${label}不存在或当前无法在 Finder 中定位。` });
  };

  const revealMaterial = async (material: GC08MeetingMaterial) => {
    if (!material.localPath) {
      setNotice({ tone: 'error', text: `${material.fileName}在当前设备上不存在。` });
      return;
    }
    const revealed = await window.yiyuWorkbench.revealInFinder(material.localPath).catch(() => false);
    if (!revealed) setNotice({ tone: 'error', text: `${material.fileName}不存在或当前无法在 Finder 中定位。` });
  };

  if (compact) {
    const recordingPath = detail?.localFiles?.recordingPath || null;
    const transcriptionPath = detail?.localFiles?.transcriptionPath || null;
    const transcriptionMissing = Boolean(
      detail?.transcription.transcriptionId && !transcriptionPath,
    );
    const transcriptionState = String(detail?.transcription.status || 'not_requested');
    const transcriptionProgress = Math.max(
      0,
      Math.min(100, Number(detail?.transcriptionProgress?.percent || 0)),
    );
    const transcriptionRunning = ['queued', 'processing'].includes(transcriptionJobStatus);
    const staleAsrBlock = transcriptionState === 'blocked'
      && detail?.transcription.errorCode === 'local_asr_not_connected';
    const asrNeedsSetup = staleAsrBlock && asrInstalled !== true;
    const canStartOrRetry = Boolean(detail)
      && Boolean(recordingPath)
      && (
        transcriptionState === 'not_requested'
        || transcriptionState === 'failed_retryable'
        || (staleAsrBlock && asrInstalled === true)
        || transcriptionMissing
      );
    const fileName = (path: string | null, fallback: string) => (
      path?.split(/[/\\]/).pop() || fallback
    );

    return (
      <section className="rounded-xl border border-slate-200 bg-slate-50/70 px-3 py-2.5" aria-label="会议录音与转写文件">
        <div className="flex items-center justify-between gap-3">
          <p className="text-xs font-bold text-slate-700">录音与转写</p>
          <div className="flex flex-wrap items-center justify-end gap-1.5">
            {busy === 'loading' && <span className="text-[10px] text-slate-400">正在读取…</span>}
            {headerActions}
            {showImportRecording && (
              <button
                type="button"
                onClick={() => void importRecording()}
                disabled={busy !== null}
                className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[10px] font-bold text-blue-600 hover:bg-blue-50 disabled:text-slate-300"
              >
                <Upload size={11} /> 导入录音
              </button>
            )}
          </div>
        </div>
        <div className="mt-2 grid gap-1.5">
          <button
            type="button"
            onClick={() => void revealLocalFile('recordingPath', '录音原件')}
            disabled={!detail}
            title={recordingPath || '尚无录音原件'}
            className="flex min-w-0 items-center gap-2 rounded-lg bg-white px-2.5 py-2 text-left text-[11px] text-slate-600 disabled:text-slate-400"
          >
            <FileAudio size={13} className="shrink-0" />
            <span className="shrink-0 font-bold">录音原件</span>
            <span className="min-w-0 flex-1 truncate">
              {fileName(recordingPath, '文件不存在')}
            </span>
            {recordingPath && <FolderOpen size={12} className="shrink-0 text-slate-400" />}
          </button>
          <div className="flex min-w-0 items-center gap-2 rounded-lg bg-white px-2.5 py-2 text-[11px] text-slate-600">
            <FileText size={13} className="shrink-0" />
            <span className="shrink-0 font-bold">转写文件</span>
            <button
              type="button"
              onClick={() => void revealLocalFile('transcriptionPath', '转写文件')}
              disabled={!detail}
              title={transcriptionPath || detail?.transcription.message || '尚未生成转写文件'}
              className="min-w-0 flex-1 truncate text-left disabled:text-slate-400"
            >
              {fileName(
                transcriptionPath,
                transcriptionPath ? transcriptionPresentation.label : '文件不存在',
              )}
            </button>
            {transcriptionPath && <FolderOpen size={12} className="shrink-0 text-slate-400" />}
            {transcriptionRunning && (
              <>
                <span className="shrink-0 font-bold text-blue-600">转写中</span>
                <div
                  className="relative h-8 w-8 shrink-0 rounded-full"
                  style={{
                    background: `conic-gradient(#5B7BFE ${transcriptionProgress * 3.6}deg, #dbeafe 0deg)`,
                  }}
                  title={detail?.transcriptionProgress?.stage || '正在转写'}
                  aria-label={`转写进度 ${transcriptionProgress}%`}
                >
                  <div className="absolute inset-[3px] flex items-center justify-center rounded-full bg-white text-[8px] font-bold text-blue-600">
                    {transcriptionProgress}%
                  </div>
                </div>
              </>
            )}
            {asrNeedsSetup && onOpenAsrSettings && (
              <button
                type="button"
                onClick={onOpenAsrSettings}
                className="inline-flex shrink-0 items-center gap-1 font-bold text-amber-700 hover:text-amber-800"
              >
                <Settings size={12} /> 安装转写组件
              </button>
            )}
            {canStartOrRetry && !asrNeedsSetup && (
              <button
                type="button"
                onClick={() => void transcribe()}
                disabled={busy !== null}
                className="shrink-0 font-bold text-blue-600 disabled:text-slate-300"
              >
                {transcriptionMissing || transcriptionState === 'not_requested'
                  ? '转写'
                  : '重试转写'}
              </button>
            )}
          </div>
          {meetingMaterials.length > 0 && (
            <div className="rounded-lg bg-white px-2.5 py-2 text-[11px] text-slate-600">
              <p className="mb-1.5 font-bold text-slate-700">已导入附件</p>
              <div className="flex flex-wrap gap-1.5">
                {meetingMaterials.map((material) => (
                  <button
                    key={material.id}
                    type="button"
                    onClick={() => void revealMaterial(material)}
                    title={material.localPath || `${material.fileName}在当前设备上不存在`}
                    className="inline-flex max-w-full items-center gap-1 rounded-md border border-slate-200 px-2 py-1 text-left hover:border-blue-200 hover:bg-blue-50 hover:text-blue-700"
                  >
                    <FileText size={11} className="shrink-0" />
                    <span className="max-w-[220px] truncate">{material.fileName}</span>
                    {material.localPath ? (
                      <FolderOpen size={11} className="shrink-0 text-slate-400" />
                    ) : (
                      <span className="shrink-0 text-rose-500">不存在</span>
                    )}
                  </button>
                ))}
              </div>
            </div>
          )}
          {notice?.tone === 'error' && (
            <p className="px-0.5 text-[10px] leading-4 text-rose-700">{notice.text}</p>
          )}
          <p className="px-0.5 text-[10px] leading-4 text-slate-400">
            如关联了项目，转写文件和附件均会进入项目工作台
          </p>
        </div>
      </section>
    );
  }

  return (
    <section className="rounded-[24px] border border-indigo-100 bg-white p-5 shadow-sm" aria-label="GC-08 会议录音与纪要">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-bold text-slate-900">本机录音与正式纪要</p>
          <p className="mt-1 text-xs leading-5 text-slate-500">
            原录音、完整转写和本机路径只保存在当前设备；仅明确发布后的安全纪要进入组织云。
          </p>
        </div>
        {detail && <span className="rounded-full bg-indigo-50 px-3 py-1 text-[11px] font-bold text-indigo-700">录音 #{detail.recordingId.slice(-8)}</span>}
      </div>

      <div className="mt-5 rounded-2xl border border-slate-200 bg-slate-50 p-4">
        <label className="text-xs font-bold text-slate-700" htmlFor="gc08-audio-path">本机录音路径</label>
        <div className="mt-2 flex flex-col gap-2 md:flex-row">
          <input
            id="gc08-audio-path"
            value={audioPath}
            onChange={(event) => setAudioPath(event.target.value)}
            placeholder="请选择应用受管录音目录中的音频文件"
            className="min-w-0 flex-1 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700 outline-none focus:border-indigo-400"
          />
          <button type="button" onClick={() => void chooseRecording()} disabled={busy !== null} className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-xs font-bold text-slate-700 disabled:opacity-50">
            选择本机录音
          </button>
          <button type="button" onClick={() => void registerRecording()} disabled={busy !== null || !clientId.trim() || !meetingId.trim()} className="rounded-xl bg-indigo-600 px-4 py-2 text-xs font-bold text-white disabled:bg-slate-300">
            {busy === 'registering' ? '登记中…' : '登记到当前会议'}
          </button>
        </div>
        <p className="mt-2 text-[11px] text-slate-500">路径仅发送给本机后端，不会进入组织云。受管目录外文件会被后端拒绝。</p>
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        {statusCard('转写状态', transcriptionPresentation, detail?.transcription.errorCode, detail?.transcription.message)}
        {statusCard(
          '纪要状态',
          minutesPresentation,
          detail?.minutesProcessing.errorCode,
          published ? '正式纪要已发布；本机仍保留原录音和完整转写。' : detail?.minutesProcessing.message,
        )}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button type="button" onClick={() => void transcribe()} disabled={!detail || busy !== null} className="rounded-xl border border-indigo-200 px-4 py-2 text-xs font-bold text-indigo-700 disabled:border-slate-200 disabled:text-slate-400">
          {transcriptionButtonLabel}
        </button>
        <span className="text-[11px] text-slate-500">`blocked` 与 `failed_retryable` 会保留真实错误码，不会显示为空文本成功。</span>
      </div>

      <div className="mt-5 space-y-3 border-t border-slate-100 pt-5">
        <div className="grid gap-3 md:grid-cols-[220px_1fr]">
          <input value={minutesTitle} onChange={(event) => setMinutesTitle(event.target.value)} disabled={!gc08CanCreateMinutes(detail) || published} placeholder="纪要标题" className="rounded-xl border border-slate-200 px-3 py-2 text-xs disabled:bg-slate-50" />
          <p className="self-center text-[11px] text-slate-500">可留空让本机能力生成草稿；正式发布前请人工校阅正文和引用。</p>
        </div>
        <textarea
          value={minutesMarkdown}
          onChange={(event) => setMinutesMarkdown(event.target.value)}
          disabled={!gc08CanCreateMinutes(detail) || published}
          placeholder="纪要草稿（Markdown）"
          className="min-h-[180px] w-full rounded-2xl border border-slate-200 bg-slate-50 p-4 text-xs leading-6 text-slate-800 outline-none focus:border-indigo-400 focus:bg-white disabled:opacity-70"
        />
        <button type="button" onClick={() => void createDraft()} disabled={!gc08CanCreateMinutes(detail) || busy !== null || published} className="rounded-xl border border-slate-300 bg-white px-4 py-2 text-xs font-bold text-slate-700 disabled:opacity-50">
          {busy === 'drafting' ? '保存中…' : detail?.minutes ? '更新纪要草稿' : '生成并保存纪要草稿'}
        </button>
      </div>

      <div className="mt-5 rounded-2xl border border-amber-200 bg-amber-50 p-4">
        <label className="flex items-start gap-3 text-xs font-medium leading-5 text-amber-950">
          <input type="checkbox" checked={publishConfirmed} onChange={(event) => setPublishConfirmed(event.target.checked)} disabled={!detail?.minutes || published || busy !== null} className="mt-1" />
          <span>我已人工核对纪要正文与证据，并明确同意把安全业务版本发布到当前客户的组织云。</span>
        </label>
        <button type="button" onClick={() => void publish()} disabled={!canPublish || published} className="mt-3 rounded-xl bg-amber-700 px-4 py-2 text-xs font-bold text-white disabled:bg-amber-200 disabled:text-amber-500">
          {busy === 'publishing' ? '正式发布中…' : published ? '正式纪要已发布' : '明确发布正式纪要'}
        </button>
        {detail?.downstreamAdapters && (
          <p className="mt-3 text-[11px] leading-5 text-amber-900">
            转任务和挂事件线仍在等待 A/B 正式命令；本面板不会直接创建任务或事件线。
          </p>
        )}
      </div>

      {notice && (
        <div role="status" className={`mt-4 rounded-xl px-4 py-3 text-xs ${notice.tone === 'success' ? 'bg-emerald-50 text-emerald-800' : 'bg-rose-50 text-rose-800'}`}>
          {notice.text}
        </div>
      )}
    </section>
  );
}

export default GC08MeetingMediaPanel;
