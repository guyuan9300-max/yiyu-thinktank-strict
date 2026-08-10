import React, { useCallback, useEffect, useMemo, useState } from 'react';

import type {
  WorkspaceDataCenterReadiness,
  WorkspaceDataCenterReadinessActionType,
  WorkspaceDataCenterReadinessFix,
} from '../../../shared/types';
import {
  getWorkspaceDataCenterReadiness,
  runWorkspaceDataCenterReadinessAction,
} from '../../lib/api';
import { LocalAiHealthCard } from './LocalAiHealthCard';

type FlashFn = (type: 'success' | 'error' | 'info', text: string) => void;

type DataCenterOpsPanelProps = {
  clientId?: string | null;
  onRefreshWorkspace?: () => void | Promise<void>;
  flash?: FlashFn;
};

const ACTION_TONE: Partial<Record<WorkspaceDataCenterReadinessActionType, string>> = {
  auto_repair_documents: '自动修复可处理项',
  retry_parse: '重试解析',
  sync_master_index: '同步主索引',
  sync_vector_index: '同步向量',
  refresh_context_pack: '刷新上下文包',
  cleanup_invalid_documents: '清理无效资料',
  rebuild_client_knowledge: '重建知识库',
  regenerate_document_cards: '重建资料卡',
  internet_enrichment: '补全互联网资料',
  enqueue_local_model_optimization: '加入后台优化队列',
  retry_local_model_optimization: '重试本地优化',
};

function percent(numerator: number, denominator: number): number {
  if (!denominator) return 0;
  return Math.max(0, Math.min(100, Math.round((numerator / denominator) * 100)));
}

function severityClass(severity: WorkspaceDataCenterReadinessFix['severity']): string {
  if (severity === 'critical') return 'border-rose-100 bg-rose-50 text-rose-700';
  if (severity === 'warning') return 'border-amber-100 bg-amber-50 text-amber-700';
  return 'border-sky-100 bg-sky-50 text-sky-700';
}

function statusTone(value: number, total: number): string {
  if (!total) return 'text-slate-400';
  if (value <= 0) return 'text-emerald-600';
  return value >= Math.max(3, Math.ceil(total * 0.1)) ? 'text-rose-600' : 'text-amber-600';
}

function formatTime(value?: string | null): string {
  if (!value) return '暂无';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function MetricCard({
  label,
  value,
  detail,
  progress,
}: {
  label: string;
  value: string;
  detail: string;
  progress?: number;
}) {
  return (
    <div className="rounded-2xl border border-white bg-white/90 px-3 py-3 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[10px] font-black uppercase tracking-[0.12em] text-slate-400">{label}</p>
          <p className="mt-1 text-[16px] font-black text-slate-900">{value}</p>
        </div>
        {typeof progress === 'number' && (
          <span className="rounded-full bg-slate-50 px-2 py-1 text-[10px] font-black text-slate-500">{progress}%</span>
        )}
      </div>
      <p className="mt-2 text-[11px] leading-5 text-slate-500">{detail}</p>
    </div>
  );
}

export function DataCenterOpsPanel({ clientId, onRefreshWorkspace, flash }: DataCenterOpsPanelProps) {
  const [readiness, setReadiness] = useState<WorkspaceDataCenterReadiness | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runningAction, setRunningAction] = useState<string | null>(null);

  const loadReadiness = useCallback(async () => {
    if (!clientId) {
      setReadiness(null);
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      setReadiness(await getWorkspaceDataCenterReadiness(clientId));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '读取数据中心准备度失败');
    } finally {
      setIsLoading(false);
    }
  }, [clientId]);

  useEffect(() => {
    void loadReadiness();
  }, [loadReadiness]);

  const primaryFix = useMemo(() => {
    if (!readiness?.recommendedFixes.length) return null;
    return readiness.recommendedFixes.find((item) => item.actionType === 'auto_repair_documents')
      || readiness.recommendedFixes.find((item) => item.severity === 'critical')
      || readiness.recommendedFixes[0];
  }, [readiness]);

  const secondaryFixes = useMemo(() => {
    if (!readiness) return [];
    return readiness.recommendedFixes
      .filter((item) => item.id !== primaryFix?.id)
      .slice(0, 4);
  }, [primaryFix?.id, readiness]);

  const issueChips = useMemo(() => {
    const summary = readiness?.summary;
    if (!summary) return [];
    return [
      { label: '解析失败', count: summary.failedDocuments },
      { label: '无效资料', count: summary.invalidDocuments },
      { label: '原文件缺失', count: summary.sourceMissingDocuments },
      { label: '占位机读稿', count: summary.placeholderOnlyDocuments },
      { label: '历史归属失效', count: summary.orphanTaskCount + summary.orphanEventLineCount },
      { label: '已隔离历史线索', count: summary.skippedOrphanClientIngestCount },
      { label: '向量未就绪', count: Math.max(0, summary.totalDocuments - summary.vectorReadyDocuments) },
      { label: '上下文缺口', count: summary.missingContextCount },
      { label: '待自动整理', count: summary.autoRepairableDocuments },
    ].filter((item) => item.count > 0);
  }, [readiness]);

  const localOptimization = readiness?.jobs.localOptimization;
  const localQueued = localOptimization?.queuedTasks || 0;
  const localRunning = localOptimization?.runningTasks || 0;
  const localFailed = localOptimization?.failedTasks || 0;
  const localPendingCards = localOptimization?.pendingDocumentCards || 0;
  const localPendingPaths = localOptimization?.pendingPathOptimizations || 0;
  const canEnqueueLocalOptimization = Boolean(localOptimization?.enabled) && (localPendingCards > 0 || localPendingPaths > 0);
  const enqueueLocalOptimizationFix: WorkspaceDataCenterReadinessFix | null = canEnqueueLocalOptimization
    ? {
        id: 'enqueue_local_model_optimization',
        label: '生成文件名片与虚拟路径',
        actionType: 'enqueue_local_model_optimization',
        severity: 'info',
        reason: `还有 ${localPendingCards} 份资料缺少名片，${localPendingPaths} 份资料缺少虚拟路径建议。`,
        targetIds: [],
        estimatedImpact: '后台队列会在设定时间段内调用本地模型处理，不改变真实文件路径。',
      }
    : null;
  const retryLocalOptimizationFix: WorkspaceDataCenterReadinessFix | null = localFailed > 0
    ? {
        id: 'retry_local_model_optimization',
        label: '重试失败的本地优化任务',
        actionType: 'retry_local_model_optimization',
        severity: 'warning',
        reason: `${localFailed} 个后台优化任务失败，可重新放回队列。`,
        targetIds: [],
        estimatedImpact: '失败任务会重新进入待处理状态，下一次运行窗口继续执行。',
      }
    : null;

  const runAction = async (fix: WorkspaceDataCenterReadinessFix) => {
    if (!clientId) return;
    setRunningAction(fix.id);
    try {
      const result = await runWorkspaceDataCenterReadinessAction(clientId, {
        actionType: fix.actionType,
        targetIds: fix.targetIds,
        reason: `workspace_ops_panel:${fix.id}`,
      });
      flash?.('success', result.message || '数据中心修复动作已提交');
      await loadReadiness();
      await onRefreshWorkspace?.();
    } catch (actionError) {
      flash?.('error', actionError instanceof Error ? actionError.message : '数据中心修复动作失败');
    } finally {
      setRunningAction(null);
    }
  };

  if (!clientId) {
    return (
      <div className="mt-5 rounded-3xl border border-slate-100 bg-white p-4">
        <p className="text-[13px] font-black text-slate-800">资料可用性</p>
        <p className="mt-1 text-[12px] leading-5 text-slate-500">选择客户后可查看资料是否已解析、入索引并可被数据中心检索。</p>
      </div>
    );
  }

  const summary = readiness?.summary;
  const readyCount = summary ? summary.readyDocuments + summary.partialReadyDocuments : 0;
  const totalCount = summary?.totalDocuments || 0;
  const searchableCount = summary ? Math.min(summary.documentCards, summary.surrogates, summary.masterIndexEntries, summary.vectorReadyDocuments) : 0;
  const indexingProgress = summary ? percent(searchableCount, totalCount) : 0;
  const parsingProgress = summary ? percent(readyCount, totalCount) : 0;

  return (
    <div className="mt-5 rounded-3xl border border-slate-100 bg-slate-50/70 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[13px] font-black text-slate-900">资料可用性</p>
          <p className="mt-1 text-[11px] leading-5 text-slate-500">检查资料解析、索引、向量和上下文包是否能支撑回答。</p>
        </div>
        <button
          type="button"
          className="shrink-0 rounded-full bg-white px-3 py-1.5 text-[11px] font-black text-[#5B7BFE] shadow-sm transition hover:bg-blue-50 disabled:opacity-50"
          disabled={isLoading}
          onClick={() => void loadReadiness()}
        >
          {isLoading ? '刷新中…' : '刷新'}
        </button>
      </div>

      {error && (
        <div className="mt-3 rounded-2xl border border-rose-100 bg-rose-50 px-3 py-2 text-[11px] font-bold text-rose-700">
          {error}
        </div>
      )}

      {/* 本地 AI 加速卡片：与 readiness 无关，无论 summary 是否加载完都显示 */}
      <LocalAiHealthCard />

      {isLoading && !readiness ? (
        <div className="mt-3 rounded-2xl border border-white bg-white px-3 py-3 text-[12px] font-bold text-slate-500">
          正在读取数据中心准备度…
        </div>
      ) : summary ? (
        <>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <MetricCard
              label="资料解析"
              value={`${readyCount}/${totalCount}`}
              detail={`失败 ${summary.failedDocuments}，处理中 ${summary.parsingDocuments}`}
              progress={parsingProgress}
            />
            <MetricCard
              label="可检索索引"
              value={`${searchableCount}/${totalCount}`}
              detail={`资料卡 ${summary.documentCards}，主索引 ${summary.masterIndexEntries}，向量 ${summary.vectorReadyDocuments}`}
              progress={indexingProgress}
            />
            <MetricCard
              label="上下文包"
              value={summary.contextQuality || 'none'}
              detail={`缺口 ${summary.missingContextCount}，最近刷新 ${formatTime(summary.latestContextPackAt)}`}
            />
            <MetricCard
              label="自动整理"
              value={`${summary.autoRepairableDocuments}`}
              detail={`0 字节 ${summary.zeroByteDocuments}，重复候选 ${summary.dedupeCandidateDocuments}`}
            />
          </div>

          {issueChips.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              {issueChips.slice(0, 8).map((item) => (
                <span key={item.label} className={`rounded-full bg-white px-2 py-1 text-[10px] font-black ${statusTone(item.count, totalCount)}`}>
                  {item.label} {item.count}
                </span>
              ))}
            </div>
          )}

          {localOptimization && (
            <div className="mt-3 rounded-2xl border border-indigo-100 bg-indigo-50/70 px-3 py-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <p className="text-[11px] font-black text-indigo-900">后台数据优化</p>
                  <p className="mt-1 text-[11px] font-semibold leading-5 text-indigo-800">
                    {localOptimization.enabled
                      ? localOptimization.paused
                        ? '已暂停'
                        : localOptimization.inWindow
                          ? '运行窗口内'
                          : `等待 ${localOptimization.nextWindowLabel || '下次运行窗口'}`
                      : '未启用'}
                    {' · '}
                    队列 {localQueued} · 运行中 {localRunning} · 失败 {localFailed}
                  </p>
                  <p className="mt-1 text-[11px] font-medium leading-5 text-indigo-700">
                    待生成名片 {localPendingCards} · 待生成虚拟路径 {localPendingPaths} · 待确认路径 {localOptimization.pendingPathConfirmations}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {enqueueLocalOptimizationFix && (
                    <button
                      type="button"
                      className="rounded-full bg-indigo-600 px-3 py-1.5 text-[11px] font-black text-white shadow-sm disabled:opacity-60"
                      onClick={() => void runAction(enqueueLocalOptimizationFix)}
                      disabled={Boolean(runningAction)}
                    >
                      {runningAction === enqueueLocalOptimizationFix.id ? '提交中…' : '加入队列'}
                    </button>
                  )}
                  {retryLocalOptimizationFix && (
                    <button
                      type="button"
                      className="rounded-full border border-amber-300 bg-white px-3 py-1.5 text-[11px] font-black text-amber-700 disabled:opacity-60"
                      onClick={() => void runAction(retryLocalOptimizationFix)}
                      disabled={Boolean(runningAction)}
                    >
                      {runningAction === retryLocalOptimizationFix.id ? '提交中…' : '重试失败'}
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}

          {primaryFix && (
            <div className={`mt-3 rounded-2xl border px-3 py-3 ${severityClass(primaryFix.severity)}`}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-[12px] font-black">{ACTION_TONE[primaryFix.actionType] || primaryFix.label}</p>
                  <p className="mt-1 line-clamp-3 text-[11px] font-semibold leading-5 opacity-90">{primaryFix.reason}</p>
                </div>
                <button
                  type="button"
                  className="shrink-0 rounded-xl bg-white px-3 py-2 text-[11px] font-black text-slate-700 shadow-sm disabled:opacity-50"
                  disabled={Boolean(runningAction)}
                  onClick={() => void runAction(primaryFix)}
                >
                  {runningAction === primaryFix.id ? '提交中…' : '执行'}
                </button>
              </div>
            </div>
          )}

          {secondaryFixes.length > 0 && (
            <div className="mt-3 space-y-2">
              {secondaryFixes.map((fix) => (
                <div key={fix.id} className="flex items-center justify-between gap-3 rounded-2xl border border-white bg-white/80 px-3 py-2">
                  <div className="min-w-0">
                    <p className="truncate text-[11px] font-black text-slate-700">{ACTION_TONE[fix.actionType] || fix.label}</p>
                    <p className="mt-0.5 line-clamp-1 text-[10px] font-semibold text-slate-400">{fix.estimatedImpact || fix.reason}</p>
                  </div>
                  <button
                    type="button"
                    className="shrink-0 rounded-xl bg-slate-50 px-2.5 py-1.5 text-[10px] font-black text-[#5B7BFE] disabled:opacity-50"
                    disabled={Boolean(runningAction)}
                    onClick={() => void runAction(fix)}
                  >
                    {runningAction === fix.id ? '提交中' : '处理'}
                  </button>
                </div>
              ))}
            </div>
          )}

          {readiness.recentJobs.length > 0 && (
            <div className="mt-3 rounded-2xl border border-white bg-white/80 px-3 py-3">
              <p className="text-[11px] font-black text-slate-700">最近资料任务</p>
              <div className="mt-2 space-y-1.5">
                {readiness.recentJobs.slice(0, 3).map((job) => (
                  <div key={job.id} className="flex items-center justify-between gap-3 text-[10px] font-semibold text-slate-500">
                    <span className="truncate">{job.jobType}</span>
                    <span className="shrink-0">{job.status} · {job.processedItems}/{job.totalItems}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      ) : null}
    </div>
  );
}
