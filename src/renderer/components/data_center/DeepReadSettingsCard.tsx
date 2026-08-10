import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, Cloud, HardDrive, Loader2, Play, Square } from 'lucide-react';

import {
  backfillLocalAi,
  getClients,
  getLocalAiCoverage,
  getLocalAiQueue,
  getLocalAiSettings,
  getOllamaHealth,
  runLocalAiNow,
  updateLocalAiSettings,
  type LocalAiClientCoverage,
  type LocalAiOptimizationSettings,
} from '../../lib/api';
import type { OllamaHealthResponse } from '../../../shared/types';
import { LocalAiHealthCard } from './LocalAiHealthCard';

// 深读用的本地模型 tag 前缀（兼容量化变种如 qwen3-vl:32b-q4_K_M）。
// 单一来源（SSOT）：所有"本地深读是否可选"判断都基于这个前缀匹配 installedModels。
const LOCAL_PARSE_MODEL_PREFIX = 'qwen3-vl:32b';

interface DeepReadSettingsCardProps {
  /** 当前工作台客户。null = 没选客户（按钮按全库范围）。 */
  clientId: string | null;
  /** 是否有权改敏感设置（云端只读账号为 false）。 */
  canEdit: boolean;
  onFlash?: (tone: 'info' | 'success' | 'error', message: string) => void;
}

// 自动解析打开时写入的一套"温柔"默认：夜间窗口 + 插电 + 需空闲，确保不影响白天使用。
const AUTO_ON_PATCH: Partial<LocalAiOptimizationSettings> = {
  enabled: true,
  paused: false,
  dailyWindows: [{ start: '22:00', end: '08:00' }],
  requireACPower: true,
  minIdleSeconds: 60,
};

export function DeepReadSettingsCard({ clientId, canEdit, onFlash }: DeepReadSettingsCardProps) {
  const [settings, setSettings] = useState<LocalAiOptimizationSettings | null>(null);
  const [coverage, setCoverage] = useState<LocalAiClientCoverage | null>(null);
  const [clients, setClients] = useState<{ id: string; name: string }[]>([]);
  const [queued, setQueued] = useState(0);
  const [running, setRunning] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [ollamaHealth, setOllamaHealth] = useState<OllamaHealthResponse | null>(null);
  const [capabilityBlockedReason, setCapabilityBlockedReason] = useState('');
  const [localAiPollingEnabled, setLocalAiPollingEnabled] = useState(false);
  const mounted = useRef(true);
  const refreshSequenceRef = useRef(0);

  const flash = useCallback(
    (tone: 'info' | 'success' | 'error', message: string) => onFlash?.(tone, message),
    [onFlash],
  );

  const refresh = useCallback(async () => {
    const requestId = refreshSequenceRef.current + 1;
    refreshSequenceRef.current = requestId;
    const capturedClientId = clientId || null;
    const canApply = () => (
      mounted.current
      && requestId === refreshSequenceRef.current
      && capturedClientId === (clientId || null)
    );
    try {
      const [s, cov, queue] = await Promise.all([
        getLocalAiSettings(),
        getLocalAiCoverage(clientId || undefined),
        getLocalAiQueue({ taskType: 'document_card_generation', limit: 1 }),
      ]);
      if (!canApply()) return;
      setSettings(s);
      const blockedState = [s.state, cov.state, queue.state].find(
        (state) => state === 'not_connected' || state === 'blocked' || state === 'failed_retryable',
      );
      setCapabilityBlockedReason(
        blockedState
          ? String(cov.message || queue.message || s.message || '严格新版本地 AI 队列尚未连接')
          : '',
      );
      setLocalAiPollingEnabled(
        s.pollingEnabled === true || queue.pollingEnabled === true,
      );
      // 优先客户(下拉菜单选某客户)→ 显示该客户; 全量(无)→ 聚合所有客户(而非取第一个 测试论坛A 的 98%)。
      const sel = ((s.priorityClientId as string | null) ?? null) || clientId || null;
      if (sel) {
        setCoverage(
          cov.perClient.find((p) => p.clientId === sel) ?? ({ clientId: sel, documents: 0, deepRead: 0, coverage: 0 } as LocalAiClientCoverage),
        );
      } else {
        const agg = cov.perClient.reduce(
          (a, p) => ({ clientId: null, documents: a.documents + p.documents, deepRead: a.deepRead + p.deepRead }),
          { clientId: null as string | null, documents: 0, deepRead: 0 },
        );
        setCoverage(cov.perClient.length ? (agg as unknown as LocalAiClientCoverage) : null);
      }
      setQueued(Number(queue.totalByStatus?.queued ?? 0));
      setRunning(Number(queue.totalByStatus?.running ?? 0));
    } catch (error) {
      if (canApply()) {
        setCapabilityBlockedReason(
          error instanceof Error
            ? error.message
            : '本地 AI 状态读取失败，请稍后重试',
        );
        setLocalAiPollingEnabled(false);
      }
    } finally {
      if (canApply()) setLoading(false);
    }
  }, [clientId]);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    void getClients()
      .then((cs) => {
        if (mounted.current) setClients(cs.map((c) => ({ id: c.id, name: c.name })));
      })
      .catch(() => undefined);
    return () => {
      mounted.current = false;
      refreshSequenceRef.current += 1;
    };
  }, [refresh]);

  // 解析进行中时轮询，让进度条/状态自己往上爬。
  const active = (
    localAiPollingEnabled
    && (Boolean(settings?.manualActive) || running > 0 || queued > 0)
  );
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => void refresh(), 4000);
    return () => clearInterval(timer);
  }, [active, refresh]);

  // 30s 轮询 ollama health：用于判断"本地"按钮是否可选。
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setInterval> | null = null;
    const fetchHealth = async () => {
      try {
        const h = await getOllamaHealth();
        if (alive) setOllamaHealth(h);
        return h.pollingEnabled === true;
      } catch {
        // 静默：本地未装 Ollama 时端点会报错,等价于不可选,UI 已会处理。
        if (alive) setOllamaHealth({ running: false, baseUrl: '', installedModels: [] });
        return false;
      }
    };
    void fetchHealth().then((pollingEnabled) => {
      if (alive && pollingEnabled) {
        timer = setInterval(() => void fetchHealth(), 30000);
      }
    });
    return () => {
      alive = false;
      if (timer !== null) clearInterval(timer);
    };
  }, []);

  const apply = useCallback(
    async (fn: () => Promise<unknown>, okMsg?: string) => {
      if (!canEdit || busy) return;
      setBusy(true);
      try {
        await fn();
        await refresh();
        if (okMsg) flash('success', okMsg);
      } catch (error: unknown) {
        flash('error', error instanceof Error ? error.message : '操作失败');
      } finally {
        if (mounted.current) setBusy(false);
      }
    },
    [canEdit, busy, refresh, flash],
  );

  const autoOn = settings?.enabled === true;
  const manualOn = settings?.manualActive === true;
  const parseLocal = settings?.parseModelMode === 'local';
  const pct = coverage && coverage.documents > 0 ? Math.round((coverage.deepRead / coverage.documents) * 100) : 0;

  // "本地"按钮的前置检测：Ollama 在跑 + 目标模型已 pull。
  // 任一不满足 → 按钮置灰 + tooltip 说明原因；如果用户当前已选"本地" → 顶上加红条警示。
  const ollamaRunning = ollamaHealth?.running === true;
  const hasLocalParseModel = !!ollamaHealth?.installedModels?.some((m) =>
    m.name.startsWith(LOCAL_PARSE_MODEL_PREFIX),
  );
  const localReady = ollamaRunning && hasLocalParseModel;
  const localBlockedReason =
    ollamaHealth === null
      ? '正在检测本地模型环境…'
      : !ollamaRunning
        ? '本机未运行 Ollama，请先安装并启动 Ollama'
        : !hasLocalParseModel
          ? `本机未检测到 ${LOCAL_PARSE_MODEL_PREFIX} 模型（约 19GB，需执行 ollama pull ${LOCAL_PARSE_MODEL_PREFIX}）`
          : null;

  const toggleAuto = () =>
    apply(
      () => updateLocalAiSettings(autoOn ? { enabled: false } : AUTO_ON_PATCH),
      autoOn ? '已关闭自动解析' : '已开启自动解析（空闲/夜间自动进行）',
    );

  const setMode = (mode: 'online' | 'local') => {
    if (settings?.parseModelMode === mode) return;
    void apply(() => updateLocalAiSettings({ parseModelMode: mode }));
  };

  const toggleManual = () => {
    if (manualOn) {
      void apply(() => updateLocalAiSettings({ manualActive: false }), '已停止解析');
      return;
    }
    void apply(async () => {
      await backfillLocalAi(clientId || undefined); // 把存量入队（幂等）
      await updateLocalAiSettings({ manualActive: true, paused: false }); // 开手动直跑
      await runLocalAiNow(true); // 立刻踢一条，不等下一轮轮询
    }, '已开始解析');
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 px-1 py-3 text-[12px] text-gray-400">
        <Loader2 size={13} className="animate-spin" /> 正在读取解析状态…
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-[12px] leading-5 text-gray-500">
        深度解析会占用内存和算力，建议<b className="text-gray-700">空闲/夜间自动进行</b>。也可立刻手动解析（会占用资源）。
      </p>

      {capabilityBlockedReason && (
        <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-5 text-amber-800">
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          <div>
            <p className="font-bold">本地 AI 当前不可用</p>
            <p>{capabilityBlockedReason}</p>
          </div>
        </div>
      )}

      {/* 解析进度 */}
      <div className="rounded-2xl border border-gray-100 bg-gray-50/70 px-4 py-3">
        <div className="mb-1.5 flex items-center justify-between gap-2 text-[12px]">
          {/* 下拉:默认"全量解析"; 选某客户=该客户优先(每次一个), 进度条/数字也切到该客户。 */}
          <select
            value={(settings?.priorityClientId as string | null) ?? ''}
            disabled={!canEdit || busy || Boolean(capabilityBlockedReason)}
            onChange={(e) => {
              const v = e.target.value;
              void apply(
                () => updateLocalAiSettings({ priorityClientId: v || null }),
                v ? '已设为优先解析该客户' : '已切回全量解析',
              );
            }}
            title="选一个客户优先解析(每次只一个),或全量"
            className="min-w-0 flex-1 truncate rounded border border-gray-200 bg-white px-1.5 py-1 font-semibold text-gray-700 outline-none focus:border-[#5B7BFE] disabled:opacity-60"
          >
            <option value="">全部客户(全量解析)</option>
            {clients.map((c) => (
              <option key={c.id} value={c.id}>{c.name} · 优先</option>
            ))}
          </select>
          <span className="shrink-0 font-bold text-[#5B7BFE]">{coverage ? `${coverage.deepRead}/${coverage.documents}` : '—'} · {pct}%</span>
        </div>
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-gray-200">
          <div className="h-full rounded-full bg-[#5B7BFE] transition-all" style={{ width: `${pct}%` }} />
        </div>
        <p className="mt-1.5 text-[11px] text-gray-400">
          {manualOn || running > 0 ? (
            <span className="inline-flex items-center gap-1 text-[#5B7BFE]">
              <Loader2 size={11} className="animate-spin" /> 解析中…{queued > 0 ? ` 待跑 ${queued}` : ''}
            </span>
          ) : autoOn ? '已开启自动解析（空闲/夜间）' : '未解析的文件不会进入公司大脑的深度理解'}
        </p>
      </div>

      {/* 自动解析开关 */}
      <label className="flex items-center justify-between">
        <span className="text-[13px] font-medium text-gray-800">自动解析<span className="ml-1 text-[11px] text-gray-400">空闲/夜间/插电时</span></span>
        <button
          type="button"
          role="switch"
          aria-checked={autoOn}
          disabled={!canEdit || busy || Boolean(capabilityBlockedReason)}
          onClick={toggleAuto}
          className={`relative h-6 w-11 shrink-0 rounded-full transition-colors disabled:opacity-40 ${autoOn ? 'bg-[#5B7BFE]' : 'bg-gray-300'}`}
        >
          <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${autoOn ? 'left-[22px]' : 'left-0.5'}`} />
        </button>
      </label>

      {/* 解析用模型 */}
      <div className="flex items-center justify-between">
        <span className="text-[13px] font-medium text-gray-800">解析用模型</span>
        <div className="inline-flex rounded-xl border border-gray-200 bg-gray-50 p-0.5 text-[12px] font-semibold">
          <button
            type="button"
            disabled={!canEdit || busy || Boolean(capabilityBlockedReason)}
            onClick={() => setMode('online')}
            className={`inline-flex items-center gap-1 rounded-lg px-3 py-1.5 transition-colors disabled:opacity-40 ${!parseLocal ? 'bg-white text-[#5B7BFE] shadow-sm' : 'text-gray-500'}`}
            title="跟主模型走（快，按量计费）"
          >
            <Cloud size={13} /> 线上
          </button>
          <button
            type="button"
            disabled={!canEdit || busy || !localReady || Boolean(capabilityBlockedReason)}
            onClick={() => setMode('local')}
            className={`inline-flex items-center gap-1 rounded-lg px-3 py-1.5 transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${parseLocal ? 'bg-white text-[#5B7BFE] shadow-sm' : 'text-gray-500'}`}
            title={localBlockedReason ?? `本地 ${LOCAL_PARSE_MODEL_PREFIX}（免费，占本机算力）`}
          >
            <HardDrive size={13} /> 本地
          </button>
        </div>
      </div>

      {/* 本地环境未就绪警示：仅在用户当前已选"本地"但环境不可用时显示（红色,促使切回线上） */}
      {parseLocal && !localReady && ollamaHealth !== null && (
        <div className="flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[11px] leading-5 text-rose-800">
          <AlertTriangle size={12} className="mt-0.5 shrink-0" />
          <div className="flex-1">
            <p className="font-bold">本地环境未就绪，深读不会执行</p>
            <p className="mt-0.5 font-medium">{localBlockedReason}</p>
            <button
              type="button"
              disabled={!canEdit || busy}
              onClick={() => setMode('online')}
              className="mt-1 text-[11px] font-bold text-rose-900 underline underline-offset-2 hover:text-rose-700 disabled:opacity-50"
            >
              切回线上模型 →
            </button>
          </div>
        </div>
      )}

      {/* 现在开始解析 / 停止 */}
      <button
        type="button"
        disabled={!canEdit || busy || Boolean(capabilityBlockedReason)}
        onClick={toggleManual}
        className={`flex w-full items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-[13px] font-bold text-white transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${manualOn ? 'bg-emerald-500 hover:bg-emerald-600' : 'bg-[#5B7BFE] hover:bg-[#4a6af0]'}`}
      >
        {busy ? <Loader2 size={15} className="animate-spin" /> : manualOn ? <Square size={15} /> : <Play size={15} />}
        {manualOn ? '停止解析' : '现在开始解析'}
      </button>

      {/* 本地推理硬件健康面板：给用户"为什么没在跑"的可解释信号
          （温度/内存/电池/空闲/Ollama），原嵌在死组件 DataCenterOpsPanel 中,已挂回这里 */}
      <LocalAiHealthCard />

      {!canEdit && <p className="text-[11px] text-amber-700">当前账号只能查看，不能修改解析设置。</p>}
    </div>
  );
}

export default DeepReadSettingsCard;
