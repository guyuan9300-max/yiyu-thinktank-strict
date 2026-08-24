/**
 * 录音会话 hook：把“录音 → 本机落地 → 归档到任务”串成一条主线。
 *
 * 这里只负责把原音频归档，不回填任务标题或正文。
 * onTranscribed 仅表示归档成功；用户随后从任务附件启动本机转写。
 *
 * 设计约束：
 * - 一次只允许一个录音 session（同应用全局）
 * - 录音不随某个模态卸载销毁：调用 hook 的组件必须是 App 顶层
 * - 录满 4 小时自动停止
 * - 开始录音前必须依次通过：本机 ASR、麦克风权限、真实声音信号
 * - 录音文件按任务稳定目录落地，并使用任务标题生成可读文件名
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { archiveMeetingRecording, archiveTaskRecording } from './api';

export interface RecordingBinding {
  taskId?: string;
  meetingId?: string;
  taskTitle: string;
  clientId?: string | null;
  eventLineId?: string | null;
}

export type RecordingStatus =
  | 'idle'
  | 'requesting_mic'
  | 'recording'
  | 'stopping'
  | 'transcribing'
  | 'error';

export interface RecordingSessionApi {
  status: RecordingStatus;
  elapsedSeconds: number;
  binding: RecordingBinding | null;
  errorMessage: string | null;
  /** 录音真正开始前的检查进度；检查期间不会计时或生成文件。 */
  preflightMessage: string | null;
  /** 实时输入音量（0-1），由麦克风 RMS 计算；非录音中始终为 0。 */
  audioLevel: number;
  start: (binding: RecordingBinding) => Promise<{ started: boolean; reason?: string }>;
  stop: () => Promise<void>;
  cancel: () => Promise<void>;
  isActive: boolean;
}

export interface TranscribedPayload {
  binding: RecordingBinding;
  sessionId: string;
  absolutePath: string;
  sizeBytes: number;
}

interface UseRecordingSessionOptions {
  onTranscribed: (payload: TranscribedPayload) => void | Promise<void>;
  onError: (message: string) => void;
  /** 每次点击录音时重新确认本机 ASR，而不是依赖页面加载时的旧状态。 */
  ensureAsrReady: () => Promise<boolean>;
  /** 最长录音时长（秒），默认 4h。到点自动 stop。 */
  maxDurationSeconds?: number;
}

const DEFAULT_MAX_DURATION_SECONDS = 4 * 60 * 60; // 4h
const MICROPHONE_PROBE_DURATION_MS = 10_000;
const MICROPHONE_MIN_RMS = 0.0005;
const MICROPHONE_MIN_PEAK = 0.004;

function pickRecorderMimeType(): { mimeType: string; extension: string } {
  const candidates: Array<{ mimeType: string; extension: string }> = [
    { mimeType: 'audio/webm;codecs=opus', extension: 'webm' },
    { mimeType: 'audio/webm', extension: 'webm' },
    { mimeType: 'audio/ogg;codecs=opus', extension: 'ogg' },
    { mimeType: 'audio/mp4', extension: 'mp4' },
  ];
  for (const candidate of candidates) {
    if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(candidate.mimeType)) {
      return candidate;
    }
  }
  return { mimeType: '', extension: 'webm' };
}

export function useRecordingSession(options: UseRecordingSessionOptions): RecordingSessionApi {
  const {
    onTranscribed,
    onError,
    ensureAsrReady,
    maxDurationSeconds = DEFAULT_MAX_DURATION_SECONDS,
  } = options;

  const [status, setStatus] = useState<RecordingStatus>('idle');
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [binding, setBinding] = useState<RecordingBinding | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [preflightMessage, setPreflightMessage] = useState<string | null>(null);
  const [audioLevel, setAudioLevel] = useState(0);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const mimeTypeRef = useRef<string>('');
  const extensionRef = useRef<string>('webm');
  const sessionIdRef = useRef<string>('');
  const bindingRef = useRef<RecordingBinding | null>(null);
  const startedAtRef = useRef<number>(0);
  const tickerRef = useRef<number | null>(null);
  const autoStopTimerRef = useRef<number | null>(null);
  const cancelFlagRef = useRef<boolean>(false);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const levelRafRef = useRef<number | null>(null);
  const levelLastUpdateRef = useRef<number>(0);

  // 保存最新的 onTranscribed / onError —— MediaRecorder.onstop 回调里要拿到最新的
  const onTranscribedRef = useRef(onTranscribed);
  const onErrorRef = useRef(onError);
  const ensureAsrReadyRef = useRef(ensureAsrReady);
  useEffect(() => { onTranscribedRef.current = onTranscribed; }, [onTranscribed]);
  useEffect(() => { onErrorRef.current = onError; }, [onError]);
  useEffect(() => { ensureAsrReadyRef.current = ensureAsrReady; }, [ensureAsrReady]);

  const clearTimers = useCallback(() => {
    if (tickerRef.current !== null) {
      window.clearInterval(tickerRef.current);
      tickerRef.current = null;
    }
    if (autoStopTimerRef.current !== null) {
      window.clearTimeout(autoStopTimerRef.current);
      autoStopTimerRef.current = null;
    }
  }, []);

  const stopLevelMeter = useCallback(() => {
    if (levelRafRef.current !== null) {
      window.cancelAnimationFrame(levelRafRef.current);
      levelRafRef.current = null;
    }
    analyserRef.current = null;
    if (audioContextRef.current) {
      try {
        void audioContextRef.current.close();
      } catch {
        /* swallow */
      }
      audioContextRef.current = null;
    }
    levelLastUpdateRef.current = 0;
    setAudioLevel(0);
  }, []);

  const releaseStream = useCallback(() => {
    stopLevelMeter();
    if (streamRef.current) {
      try {
        for (const track of streamRef.current.getTracks()) {
          track.stop();
        }
      } catch {
        /* swallow */
      }
      streamRef.current = null;
    }
  }, [stopLevelMeter]);

  const startLevelMeter = useCallback((stream: MediaStream) => {
    try {
      const AudioCtor = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      if (!AudioCtor) return;
      const ctx = new AudioCtor();
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      analyser.smoothingTimeConstant = 0.4;
      source.connect(analyser);
      audioContextRef.current = ctx;
      analyserRef.current = analyser;

      const buffer = new Uint8Array(analyser.fftSize);
      const tick = (timestamp: number) => {
        const current = analyserRef.current;
        if (!current) return;
        current.getByteTimeDomainData(buffer);
        let sumSquares = 0;
        for (let i = 0; i < buffer.length; i += 1) {
          const normalized = (buffer[i] - 128) / 128;
          sumSquares += normalized * normalized;
        }
        const rms = Math.sqrt(sumSquares / buffer.length);
        // 30fps 节流，避免每帧 setState
        if (timestamp - levelLastUpdateRef.current >= 33) {
          // 把 RMS 拉到 0-1 的"视觉敏感"区间（×3 + clamp），让小音量也能看见波动
          const visualLevel = Math.min(1, Math.max(0, rms * 3));
          setAudioLevel(visualLevel);
          levelLastUpdateRef.current = timestamp;
        }
        levelRafRef.current = window.requestAnimationFrame(tick);
      };
      levelRafRef.current = window.requestAnimationFrame(tick);
    } catch (err) {
      console.warn('[recording] audio level meter setup failed', err);
    }
  }, []);

  const resetAll = useCallback(() => {
    clearTimers();
    releaseStream();
    recorderRef.current = null;
    chunksRef.current = [];
    bindingRef.current = null;
    sessionIdRef.current = '';
    startedAtRef.current = 0;
    cancelFlagRef.current = false;
    mimeTypeRef.current = '';
    extensionRef.current = 'webm';
    setBinding(null);
    setElapsedSeconds(0);
    setPreflightMessage(null);
    setStatus('idle');
  }, [clearTimers, releaseStream]);

  const probeMicrophoneSignal = useCallback(async (stream: MediaStream): Promise<boolean> => {
    const AudioCtor = window.AudioContext
      ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioCtor) return false;

    let context: AudioContext | null = null;
    try {
      context = new AudioCtor();
      if (context.state === 'suspended') {
        await context.resume();
      }
      const source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 2048;
      analyser.smoothingTimeConstant = 0;
      source.connect(analyser);
      const samples = new Float32Array(analyser.fftSize);
      const startedAt = performance.now();
      let voicedFrames = 0;

      while (performance.now() - startedAt < MICROPHONE_PROBE_DURATION_MS) {
        analyser.getFloatTimeDomainData(samples);
        let sumSquares = 0;
        let framePeak = 0;
        for (let i = 0; i < samples.length; i += 1) {
          const value = Math.abs(samples[i]);
          sumSquares += value * value;
          if (value > framePeak) framePeak = value;
        }
        const rms = Math.sqrt(sumSquares / samples.length);
        if (rms >= MICROPHONE_MIN_RMS || framePeak >= MICROPHONE_MIN_PEAK) {
          voicedFrames += 1;
          if (voicedFrames >= 2) return true;
        }
        await new Promise<void>((resolve) => window.setTimeout(resolve, 45));
      }
      return false;
    } catch (error) {
      console.warn('[recording] microphone signal probe failed', error);
      return false;
    } finally {
      if (context) {
        try { await context.close(); } catch { /* swallow */ }
      }
    }
  }, []);

  // 卸载兜底（虽然 hook 在 App 顶层不会卸载，但 dev 热重载会触发）
  useEffect(() => {
    return () => {
      clearTimers();
      releaseStream();
    };
  }, [clearTimers, releaseStream]);

  const start = useCallback<RecordingSessionApi['start']>(async (nextBinding) => {
    if (status !== 'idle' && status !== 'error') {
      return { started: false, reason: '已有一个录音正在进行' };
    }
    const taskId = nextBinding.taskId?.trim() || '';
    const meetingId = nextBinding.meetingId?.trim() || '';
    if (!taskId && !meetingId) {
      return { started: false, reason: '请先保存任务或会议' };
    }
    if (meetingId && !nextBinding.clientId?.trim()) {
      return { started: false, reason: '客户会议录音缺少关联项目' };
    }
    setErrorMessage(null);
    setBinding(nextBinding);
    setElapsedSeconds(0);
    setStatus('requesting_mic');

    const failPreflight = (message: string, stream?: MediaStream) => {
      if (stream) stream.getTracks().forEach((track) => track.stop());
      setPreflightMessage(null);
      setErrorMessage(message);
      setStatus('error');
      onErrorRef.current(message);
      return { started: false, reason: message };
    };

    setPreflightMessage('正在检查本机 ASR');
    try {
      if (!(await ensureAsrReadyRef.current())) {
        return failPreflight('本机 ASR 尚未安装。请先前往系统设置配置 ASR，再开始录音。');
      }
    } catch {
      return failPreflight('无法确认本机 ASR 状态，请稍后重试。');
    }

    if (typeof navigator === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      return failPreflight('当前环境不支持麦克风录音');
    }

    setPreflightMessage('正在检查麦克风权限');
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return failPreflight(`麦克风获取失败：${msg}`);
    }

    const audioTracks = stream.getAudioTracks();
    if (audioTracks.length === 0 || audioTracks.every((track) => track.readyState !== 'live' || !track.enabled)) {
      return failPreflight('没有可用的麦克风输入，请检查系统输入设备和麦克风权限。', stream);
    }

    setPreflightMessage('请说一句话，正在检测麦克风声音');
    if (!(await probeMicrophoneSignal(stream))) {
      return failPreflight(
        '持续 10 秒没有检测到麦克风声音。请检查系统输入设备、静音状态或麦克风权限后重试。',
        stream,
      );
    }

    const { mimeType, extension } = pickRecorderMimeType();
    let recorder: MediaRecorder;
    try {
      recorder = mimeType
        ? new MediaRecorder(stream, { mimeType, audioBitsPerSecond: 64000 })
        : new MediaRecorder(stream, { audioBitsPerSecond: 64000 });
    } catch (err) {
      stream.getTracks().forEach((t) => t.stop());
      const msg = err instanceof Error ? err.message : String(err);
      const friendly = `MediaRecorder 初始化失败：${msg}`;
      setErrorMessage(friendly);
      setStatus('error');
      onErrorRef.current(friendly);
      return { started: false, reason: friendly };
    }

    const sessionId = (typeof crypto !== 'undefined' && 'randomUUID' in crypto)
      ? crypto.randomUUID()
      : `rec-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    streamRef.current = stream;
    recorderRef.current = recorder;
    chunksRef.current = [];
    mimeTypeRef.current = mimeType || 'audio/webm';
    extensionRef.current = extension;
    sessionIdRef.current = sessionId;
    bindingRef.current = nextBinding;
    startedAtRef.current = Date.now();
    cancelFlagRef.current = false;

    recorder.ondataavailable = (event) => {
      if (event.data && event.data.size > 0) {
        chunksRef.current.push(event.data);
      }
    };
    recorder.onstop = () => {
      void handleRecorderStopped();
    };
    recorder.onerror = (event) => {
      const evt = event as unknown as { error?: Error };
      const msg = evt?.error?.message || 'MediaRecorder 内部错误';
      setErrorMessage(msg);
      setStatus('error');
      onErrorRef.current(msg);
      // 强行停止 & 释放
      try { recorder.stop(); } catch { /* swallow */ }
      releaseStream();
    };

    // dataavailable 每 5 秒一次（避免一次性把 4h 内存全压在一个 Blob 上）
    try {
      recorder.start(5000);
    } catch (err) {
      stream.getTracks().forEach((t) => t.stop());
      const msg = err instanceof Error ? err.message : String(err);
      const friendly = `录音启动失败：${msg}`;
      setErrorMessage(friendly);
      setStatus('error');
      onErrorRef.current(friendly);
      return { started: false, reason: friendly };
    }

    startLevelMeter(stream);

    setBinding(nextBinding);
    setPreflightMessage(null);
    setStatus('recording');
    setElapsedSeconds(0);

    // 每秒更新 UI 计时
    tickerRef.current = window.setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - startedAtRef.current) / 1000));
    }, 1000);

    // 4 小时上限：到点自动 stop
    autoStopTimerRef.current = window.setTimeout(() => {
      if (recorderRef.current && recorderRef.current.state === 'recording') {
        try { recorderRef.current.stop(); } catch { /* swallow */ }
      }
    }, Math.max(maxDurationSeconds, 60) * 1000);

    return { started: true };
  }, [status, maxDurationSeconds, probeMicrophoneSignal, releaseStream, startLevelMeter]);

  const stop = useCallback<RecordingSessionApi['stop']>(async () => {
    const recorder = recorderRef.current;
    if (!recorder || (recorder.state !== 'recording' && recorder.state !== 'paused')) {
      return;
    }
    setStatus('stopping');
    clearTimers();
    stopLevelMeter();
    try {
      recorder.stop();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setErrorMessage(`录音停止失败：${msg}`);
      setStatus('error');
      onErrorRef.current(msg);
      releaseStream();
    }
  }, [clearTimers, releaseStream, stopLevelMeter]);

  const cancel = useCallback<RecordingSessionApi['cancel']>(async () => {
    cancelFlagRef.current = true;
    const recorder = recorderRef.current;
    if (recorder && (recorder.state === 'recording' || recorder.state === 'paused')) {
      try { recorder.stop(); } catch { /* swallow */ }
    } else {
      resetAll();
    }
  }, [resetAll]);

  /**
   * MediaRecorder.onstop 回调：把 chunks 拼成 Blob → IPC 落地 → 归档并入队 → 通知 App。
   */
  const handleRecorderStopped = useCallback(async () => {
    const currentBinding = bindingRef.current;
    const sessionId = sessionIdRef.current;
    const extension = extensionRef.current;
    const mimeType = mimeTypeRef.current || 'audio/webm';
    const chunks = chunksRef.current.slice();
    const wasCancelled = cancelFlagRef.current;

    releaseStream();
    chunksRef.current = [];

    if (wasCancelled) {
      resetAll();
      return;
    }
    if (!currentBinding || !sessionId) {
      resetAll();
      return;
    }
    if (chunks.length === 0) {
      resetAll();
      onErrorRef.current('录音文件为空，没有可转写的内容');
      return;
    }

    setStatus('transcribing');

    const blob = new Blob(chunks, { type: mimeType });
    let absolutePath = '';
    let sizeBytes = 0;
    try {
      const buffer = await blob.arrayBuffer();
      const saved = await window.yiyuWorkbench.saveRecordingBlob({
        buffer,
        extension,
        sessionId,
        scopeId: currentBinding.taskId || currentBinding.meetingId || sessionId,
        suggestedBaseName: currentBinding.taskTitle,
      });
      absolutePath = saved.absolutePath;
      sizeBytes = saved.sizeBytes;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setErrorMessage(`保存录音文件失败：${msg}`);
      setStatus('error');
      onErrorRef.current(msg);
      // 暂不 resetAll，保留 binding 供 UI 显示错误；用户清除后 resetAll
      return;
    }

    try {
      if (currentBinding.meetingId) {
        const archived = await archiveMeetingRecording({
          clientId: currentBinding.clientId || '',
          meetingId: currentBinding.meetingId,
          audioPath: absolutePath,
          durationMs: Math.max(0, Date.now() - startedAtRef.current),
          capturedAt: new Date(startedAtRef.current || Date.now()).toISOString(),
        });
        // 与 GC08MeetingMediaPanel 共用同一个本机指针，重新打开会议后
        // 可继续转写和纪要，不会因为关闭编辑器丢失录音状态。
        window.localStorage.setItem(
          `yiyu.gc08.last-recording.${currentBinding.clientId}.${currentBinding.meetingId}`,
          archived.recordingId,
        );
      } else {
        await archiveTaskRecording({
          taskId: currentBinding.taskId || '',
          audioPath: absolutePath,
          sessionId,
          clientId: currentBinding.clientId,
          eventLineId: currentBinding.eventLineId,
          taskTitle: currentBinding.taskTitle,
        });
      }
      await onTranscribedRef.current({
        binding: currentBinding,
        sessionId,
        absolutePath,
        sizeBytes,
      });
      resetAll();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setErrorMessage(`录音归档失败：${msg}`);
      setStatus('error');
      onErrorRef.current(`录音归档失败：${msg}。录音原件仍保留在本机，可重试。`);
      // 不调 resetAll —— 让 UI 显示错误，用户手动清除
    }
  }, [releaseStream, resetAll]);

  // 这里返回的 isActive 表示录音/收尾流程中（含 transcribing）
  const isActive = status === 'recording' || status === 'stopping' || status === 'transcribing' || status === 'requesting_mic';

  return {
    status,
    elapsedSeconds,
    binding,
    errorMessage,
    preflightMessage,
    audioLevel,
    start,
    stop,
    cancel,
    isActive,
  };
}

export function formatRecordingClock(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds || 0));
  const hh = Math.floor(safe / 3600);
  const mm = Math.floor((safe % 3600) / 60);
  const ss = safe % 60;
  const pad = (n: number) => String(n).padStart(2, '0');
  if (hh > 0) return `${pad(hh)}:${pad(mm)}:${pad(ss)}`;
  return `${pad(mm)}:${pad(ss)}`;
}
