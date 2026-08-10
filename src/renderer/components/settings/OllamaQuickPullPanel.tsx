import { useCallback, useEffect, useRef, useState } from 'react';
import { Download, CheckCircle2, AlertCircle, Loader2, ExternalLink, X } from 'lucide-react';

import type {
  OllamaHealthResponse,
  OllamaPullStatusResponse,
  OllamaRecommendedModel,
} from '../../../shared/types';
import {
  getOllamaHealth,
  getOllamaRecommendedModels,
  startOllamaPull,
  getOllamaPullStatus,
  cancelOllamaPull,
} from '../../lib/api';

interface OllamaQuickPullPanelProps {
  capability: string;
  canEdit: boolean;
  /** 下载完成后回调，给上层（profile 表单）自动写入 baseUrl + model */
  onModelReady: (modelName: string) => void;
  /** 当前 profile 已配置的模型名（如果有）—— dropdown 默认选这个，标题显示"当前在用" */
  currentModelName?: string;
}

function formatBytes(bytes: number): string {
  if (bytes <= 0) return '0 B';
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(0)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

export function OllamaQuickPullPanel({ capability, canEdit, onModelReady, currentModelName }: OllamaQuickPullPanelProps) {
  const [health, setHealth] = useState<OllamaHealthResponse | null>(null);
  const [recommended, setRecommended] = useState<OllamaRecommendedModel[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>('');
  const [pullStatus, setPullStatus] = useState<OllamaPullStatusResponse | null>(null);
  const [actionError, setActionError] = useState<string>('');
  const [actionMessage, setActionMessage] = useState<string>('');
  const completedRef = useRef<string>('');
  const pollTimerRef = useRef<number | null>(null);
  // 当前 panel 触发的下载模型名 — 用来区分"这个 panel 自己启动的下载"vs"别的 panel 启动的下载"
  // 因为后端 OllamaPullManager 是单例，所有 panel 共享同一个 pullStatus，必须靠这个本地状态区分。
  const [thisPanelDownloadingModel, setThisPanelDownloadingModel] = useState<string>('');

  const refreshHealth = useCallback(async () => {
    try {
      const h = await getOllamaHealth();
      setHealth(h);
    } catch (error) {
      console.warn('[ollama] health failed', error);
    }
  }, []);

  const refreshRecommended = useCallback(async () => {
    try {
      const r = await getOllamaRecommendedModels(capability);
      setRecommended(r.models);
      // dropdown 默认选中：优先用 profile 当前已配置的，否则用 recommended default
      const fallback = currentModelName || r.models.find((m) => m.default)?.name || '';
      if (fallback) setSelectedModel((prev) => prev || fallback);
    } catch (error) {
      console.warn('[ollama] recommended failed', error);
    }
  }, [capability, currentModelName]);

  const refreshPullStatus = useCallback(async () => {
    try {
      const s = await getOllamaPullStatus();
      setPullStatus(s);
      // 只在"这个 panel 自己触发的"下载完成时回调上层填配置
      // 否则会出现 panel A 下载完，panel B/C 也跟着把 baseUrl/model 填错的情况
      if (
        s.completed
        && s.modelName
        && s.modelName === thisPanelDownloadingModel
        && completedRef.current !== s.modelName
      ) {
        completedRef.current = s.modelName;
        onModelReady(s.modelName);
        setActionMessage(`✅ ${s.modelName} 已下载完成并自动填入下方配置`);
        setThisPanelDownloadingModel('');
        await refreshHealth();
      }
    } catch (error) {
      console.warn('[ollama] pull status failed', error);
    }
  }, [onModelReady, refreshHealth, thisPanelDownloadingModel]);

  useEffect(() => {
    void refreshHealth();
    void refreshRecommended();
    void refreshPullStatus();
  }, [refreshHealth, refreshRecommended, refreshPullStatus]);

  useEffect(() => {
    const inProgress = (
      pullStatus?.pollingEnabled === true
      && pullStatus.inProgress
    );
    if (!inProgress) {
      if (pollTimerRef.current !== null) {
        window.clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
      return;
    }
    pollTimerRef.current = window.setInterval(() => {
      void refreshPullStatus();
    }, 1000);
    return () => {
      if (pollTimerRef.current !== null) {
        window.clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [pullStatus?.inProgress, pullStatus?.pollingEnabled, refreshPullStatus]);

  const installedNames = new Set((health?.installedModels || []).map((m) => m.name));

  const handlePull = async () => {
    if (!selectedModel) return;
    setActionError('');
    setActionMessage('');
    completedRef.current = '';
    try {
      const r = await startOllamaPull(selectedModel);
      if (!r.started) {
        setActionError(r.message || '启动拉取失败');
      } else {
        // 记录这个 panel 触发的模型,后续 pullStatus 看到 modelName 匹配时才认为是"自己的下载"
        setThisPanelDownloadingModel(selectedModel);
        setActionMessage(r.message || `已开始拉取 ${selectedModel}`);
        void refreshPullStatus();
      }
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '请求失败');
    }
  };

  const handleCancel = async () => {
    try {
      await cancelOllamaPull();
      setActionMessage('已请求取消，下载即将停止');
      await refreshPullStatus();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '取消失败');
    }
  };

  const handleUseInstalled = (name: string) => {
    onModelReady(name);
    setActionMessage(`✅ 已选择已安装的 ${name}，并自动填入下方配置`);
  };

  if (!health) {
    return (
      <div className="rounded-2xl border border-gray-200 bg-gray-50 px-3 py-2 text-[11px] text-gray-500">
        正在读取 Ollama 状态…
      </div>
    );
  }

  if (!health.running) {
    return (
      <div className="space-y-2 rounded-2xl border border-amber-100 bg-amber-50 px-3 py-2.5 text-amber-800">
        <div className="flex items-start gap-2 text-[11px]">
          <AlertCircle size={13} className="mt-0.5 shrink-0" />
          <div className="min-w-0">
            <p className="font-bold break-words">需要先安装 Ollama</p>
            <p className="opacity-80 mt-0.5 break-words">{health.error || '无法连接 127.0.0.1:11434'}</p>
          </div>
        </div>
        <div className="flex gap-2">
          <a
            href="https://ollama.com/download"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 rounded-xl bg-amber-700 text-white px-3 py-1.5 text-[11px] font-bold hover:bg-amber-800"
          >
            <ExternalLink size={11} /> 去 ollama.com 下载安装
          </a>
          <button
            type="button"
            onClick={() => void refreshHealth()}
            className="inline-flex items-center gap-1 rounded-xl border border-amber-200 bg-white px-3 py-1.5 text-[11px] font-bold text-amber-700 hover:border-amber-300"
          >
            重新检测
          </button>
        </div>
      </div>
    );
  }

  const pullPercent = pullStatus && pullStatus.bytesTotal > 0
    ? Math.min(100, (pullStatus.bytesDownloaded / pullStatus.bytesTotal) * 100)
    : 0;

  return (
    <div className="space-y-2 rounded-2xl border border-blue-100 bg-blue-50/60 px-3 py-2.5">
      {currentModelName && (
        <p className="text-[11px] text-gray-600 break-all">
          当前在用：<span className="font-mono font-bold text-gray-900">{currentModelName}</span>
        </p>
      )}
      {/* 区分"这个 panel 自己在下载" vs "别的 panel 在下载" */}
      {(() => {
        const someoneDownloading = pullStatus?.inProgress;
        const itsMyDownload = someoneDownloading && pullStatus.modelName === thisPanelDownloadingModel;
        const itsOtherDownload = someoneDownloading && !itsMyDownload;
        return (
          <>
            {itsOtherDownload && (
              <div className="rounded-xl border border-amber-100 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
                <span className="font-bold">⏳ 另一个本地模型正在下载：</span>
                <span className="ml-1">{pullStatus.modelName} · {pullStatus.status}</span>
                <span className="ml-1 opacity-70">— 请等它完成后再下载</span>
              </div>
            )}

            {/* 推荐模型 dropdown + 按钮 — 永远竖排，避免在窄卡片里按钮被挤出视野 */}
            <div className="space-y-2">
              <select
                value={selectedModel}
                onChange={(e) => setSelectedModel(e.target.value)}
                disabled={!canEdit || Boolean(someoneDownloading)}
                className="block w-full rounded-xl border border-gray-200 bg-white px-2.5 py-2 text-[12px] font-bold text-gray-900 outline-none focus:border-[#5B7BFE] disabled:opacity-50"
              >
                <option value="">— 选择推荐模型 —</option>
                {recommended.map((m) => {
                  const isInstalled = installedNames.has(m.name);
                  return (
                    <option key={m.name} value={m.name}>
                      {m.name} ({m.sizeGb.toFixed(1)}GB){isInstalled ? ' · 已安装 ✓' : ''} — {m.description}
                    </option>
                  );
                })}
              </select>
              {!someoneDownloading && (
                selectedModel && installedNames.has(selectedModel) ? (
                  <button
                    type="button"
                    onClick={() => handleUseInstalled(selectedModel)}
                    disabled={!canEdit}
                    className="inline-flex w-full items-center justify-center gap-1.5 rounded-xl bg-emerald-600 text-white px-3 py-2 text-[12px] font-bold hover:bg-emerald-700 disabled:opacity-50"
                  >
                    <CheckCircle2 size={13} /> 使用此已安装模型
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => void handlePull()}
                    disabled={!canEdit || !selectedModel}
                    className="inline-flex w-full items-center justify-center gap-1.5 rounded-xl bg-[#5B7BFE] text-white px-3 py-2 text-[12px] font-bold shadow-[0_4px_12px_rgba(91,123,254,0.18)] disabled:opacity-50"
                  >
                    <Download size={11} /> 下载模型
                  </button>
                )
              )}
              {itsMyDownload && (
                <button
                  type="button"
                  onClick={() => void handleCancel()}
                  className="inline-flex w-full items-center justify-center gap-1.5 rounded-xl border border-rose-200 bg-white px-3 py-2 text-[12px] font-bold text-rose-600 hover:border-rose-300"
                >
                  <X size={13} /> 取消下载
                </button>
              )}
            </div>

            {/* 进度条 — 只在自己 panel 触发的下载时显示 */}
            {itsMyDownload && (
              <div className="space-y-1.5">
                <div className="h-2 rounded-full bg-blue-100 overflow-hidden">
                  <div className="h-full bg-[#5B7BFE] transition-all" style={{ width: `${pullPercent}%` }} />
                </div>
                <div className="flex flex-wrap items-center justify-between gap-1.5 text-[10px] text-blue-700">
                  <span className="inline-flex items-center gap-1 min-w-0 truncate">
                    <Loader2 size={10} className="shrink-0 animate-spin" />
                    <span className="truncate">{pullStatus.modelName} · {pullStatus.status}</span>
                  </span>
                  <span className="shrink-0">
                    {formatBytes(pullStatus.bytesDownloaded)} / {formatBytes(pullStatus.bytesTotal)}（{pullPercent.toFixed(1)}%）
                  </span>
                </div>
              </div>
            )}
          </>
        );
      })()}

      {pullStatus?.error && !pullStatus.inProgress && (
        <p className="text-[11px] text-rose-600 break-words">{pullStatus.error}</p>
      )}
      {actionMessage && <p className="text-[11px] text-emerald-700 break-words">{actionMessage}</p>}
      {actionError && <p className="text-[11px] text-rose-600 break-words">{actionError}</p>}
    </div>
  );
}
