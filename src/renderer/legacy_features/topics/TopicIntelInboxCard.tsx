import React from 'react';
import { ChevronDown, ChevronUp, ExternalLink, FilePlus2, Gauge, Newspaper, ShieldCheck, Signal } from 'lucide-react';

import type { TopicCandidate } from '../../../shared/types';

type TopicIntelInboxCardProps = {
  candidate: TopicCandidate;
  radarTitle: string;
  relatedTaskCount: number;
  mainBadge?: string | null;
  sourceStatusText?: string;
  scopeLabel?: string;
  dataCenterStatusText?: string;
  evidenceStatusText?: string;
  evidenceStatusTone?: 'neutral' | 'warning' | 'success' | 'danger';
  relevanceReason: string;
  suggestedAction: string;
  isDeepAnalysisOpen: boolean;
  canReviewEvidence?: boolean;
  isEvidenceReviewPending?: boolean;
  onOpenTask: () => void;
  onToggleDeepAnalysis: () => void;
  onOpenSource: () => void;
  onAcceptEvidence?: () => void;
  onRejectEvidence?: () => void;
  children?: React.ReactNode;
};

function formatPublishedAt(value?: string | null) {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString('zh-CN', { year: 'numeric', month: 'numeric', day: 'numeric' });
}

function insightStatusLabel(candidate: TopicCandidate) {
  if (candidate.insightStatus !== 'failed') return null;
  return { label: '深度分析生成失败，可重试', className: 'border-rose-100 bg-rose-50 text-rose-700' };
}

function scoreValue(value: unknown, fallback: number) {
  const numeric = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) return fallback;
  return Math.max(0, Math.min(100, numeric));
}

function scoreTone(score: number) {
  if (score >= 78) return 'bg-emerald-500 text-emerald-700';
  if (score >= 58) return 'bg-[#5B7BFE] text-[#4a67f5]';
  return 'bg-amber-500 text-amber-700';
}

function scoreLabel(score: number) {
  if (score >= 78) return '高';
  if (score >= 58) return '中';
  return '待核验';
}

function sourceQualityReason(candidate: TopicCandidate) {
  const quality = candidate.sourceQuality || {};
  const reason = typeof quality.credibilityReason === 'string' ? quality.credibilityReason.trim() : '';
  if (reason) return reason;
  if (!candidate.sourceUrl) return '当前没有原文链接，需要人工复核。';
  return '建议打开原文核对关键事实、时间和适用条件。';
}

function AdvisorMetric({
  icon,
  label,
  score,
}: {
  icon: React.ReactNode;
  label: string;
  score: number;
}) {
  const tone = scoreTone(score);
  const barClass = tone.split(' ')[0];
  const textClass = tone.split(' ')[1];
  return (
    <div className="rounded-lg border border-gray-100 bg-gray-50/80 px-3 py-2">
      <div className="flex items-center justify-between gap-2 text-[11px] font-bold text-gray-500">
        <span className="inline-flex items-center gap-1.5">
          {icon}
          {label}
        </span>
        <span className={textClass}>{Math.round(score)}% · {scoreLabel(score)}</span>
      </div>
      <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-white">
        <div className={`h-full rounded-full ${barClass}`} style={{ width: `${Math.max(6, Math.round(score))}%` }} />
      </div>
    </div>
  );
}

export function TopicIntelInboxCard({
  candidate,
  radarTitle,
  relatedTaskCount,
  mainBadge,
  sourceStatusText,
  scopeLabel,
  dataCenterStatusText,
  evidenceStatusText,
  evidenceStatusTone = 'neutral',
  relevanceReason,
  suggestedAction,
  isDeepAnalysisOpen,
  canReviewEvidence = false,
  isEvidenceReviewPending = false,
  onOpenTask,
  onToggleDeepAnalysis,
  onOpenSource,
  onAcceptEvidence,
  onRejectEvidence,
  children,
}: TopicIntelInboxCardProps) {
  const analysisBadge = insightStatusLabel(candidate);
  const hasProcessingStatus = Boolean(analysisBadge || sourceStatusText);
  const publishedAtText = formatPublishedAt(candidate.publishedAt) || '未标注';
  const relatedTaskText = relatedTaskCount > 0 ? `已转任务 ${relatedTaskCount}` : '';
  const evidenceToneClass = {
    neutral: 'border-gray-200 bg-gray-50 text-gray-600',
    warning: 'border-amber-200 bg-amber-50 text-amber-800',
    success: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    danger: 'border-rose-200 bg-rose-50 text-rose-700',
  }[evidenceStatusTone];
  const matchScore = scoreValue(candidate.matchStrength, candidate.primaryBadge ? 72 : 58);
  const credibilityScore = scoreValue(candidate.credibilityScore, candidate.sourceUrl ? 62 : 42);
  const confidenceScore = scoreValue(candidate.confidenceScore, Math.round(matchScore * 0.55 + credibilityScore * 0.35));
  const advisorReason = candidate.whyRecommended?.trim() || relevanceReason;
  const sourceReason = sourceQualityReason(candidate);

  return (
    <article className="h-full rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
      <div className="flex flex-col gap-3">
        {mainBadge && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center rounded-md bg-[#5B7BFE] px-2.5 py-1 text-[11px] font-bold text-white">
              {mainBadge}
            </span>
          </div>
        )}

        <h3 className="text-[17px] font-bold leading-7 text-gray-950">{candidate.title}</h3>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-[12px] text-gray-500">
          <span className="inline-flex items-center gap-1.5">
            <Newspaper size={13} />
            来源：{candidate.source || '未知来源'}
          </span>
          <span>发布时间：{publishedAtText}</span>
          <span>来源画像：{radarTitle}</span>
        </div>

        {(scopeLabel || dataCenterStatusText || evidenceStatusText) && (
          <div className="flex flex-wrap items-center gap-2 text-[12px]">
            {scopeLabel ? (
              <span className="inline-flex items-center rounded-md border border-indigo-100 bg-indigo-50 px-2.5 py-1 font-semibold text-indigo-700">
                {scopeLabel}
              </span>
            ) : null}
            {dataCenterStatusText ? (
              <span className="inline-flex items-center rounded-md border border-slate-200 bg-slate-50 px-2.5 py-1 font-semibold text-slate-600">
                {dataCenterStatusText}
              </span>
            ) : null}
            {evidenceStatusText ? (
              <span className={`inline-flex items-center rounded-md border px-2.5 py-1 font-semibold ${evidenceToneClass}`}>
                {evidenceStatusText}
              </span>
            ) : null}
          </div>
        )}

        {hasProcessingStatus && (
          <div className="flex flex-wrap items-center gap-2 text-[12px]">
            <span className="font-semibold text-gray-500">处理状态</span>
            {sourceStatusText && (
              <span className="inline-flex items-center rounded-md border border-orange-100 bg-orange-50 px-2.5 py-1 font-semibold text-orange-700">
                {sourceStatusText}
              </span>
            )}
            {analysisBadge && (
              <span className={`inline-flex items-center rounded-md border px-2.5 py-1 font-semibold ${analysisBadge.className}`}>
                {analysisBadge.label}
              </span>
            )}
          </div>
        )}

        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          <AdvisorMetric icon={<Signal size={12} />} label="匹配强度" score={matchScore} />
          <AdvisorMetric icon={<ShieldCheck size={12} />} label="来源可信度" score={credibilityScore} />
          <AdvisorMetric icon={<Gauge size={12} />} label="推荐把握" score={confidenceScore} />
        </div>

        <p className="text-[12px] leading-5 text-gray-500">来源核验：{sourceReason}</p>

        <div>
          <p className="text-[11px] font-bold text-gray-500">一句话摘要</p>
          <p className="mt-1 text-[13px] leading-6 text-gray-700">{candidate.summary || '当前暂无摘要。'}</p>
        </div>

        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          <section className="rounded-lg border border-blue-100 bg-blue-50/70 px-4 py-3">
            <p className="text-[11px] font-bold text-[#5B7BFE]">为什么推给你</p>
            <p className="mt-2 text-[13px] leading-6 text-slate-700">{advisorReason}</p>
          </section>
          <section className="rounded-lg border border-emerald-100 bg-emerald-50/70 px-4 py-3">
            <p className="text-[11px] font-bold text-emerald-700">建议动作</p>
            <p className="mt-2 text-[13px] leading-6 text-slate-700">{suggestedAction}</p>
          </section>
        </div>

        {relatedTaskText && (
          <span className="w-fit rounded-md border border-violet-100 bg-violet-50 px-2.5 py-1 text-[11px] font-bold text-violet-700">
            {relatedTaskText}
          </span>
        )}

        <div className="flex flex-wrap items-center gap-2 pt-1">
          {canReviewEvidence && (
            <>
              <button
                type="button"
                onClick={onAcceptEvidence}
                disabled={isEvidenceReviewPending}
                className="inline-flex items-center gap-1.5 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-[12px] font-semibold text-emerald-700 transition-colors hover:bg-emerald-100 disabled:cursor-wait disabled:opacity-60"
              >
                通过
              </button>
              <button
                type="button"
                onClick={onRejectEvidence}
                disabled={isEvidenceReviewPending}
                className="inline-flex items-center gap-1.5 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-[12px] font-semibold text-rose-700 transition-colors hover:bg-rose-100 disabled:cursor-wait disabled:opacity-60"
              >
                不采用
              </button>
            </>
          )}
          <button
            type="button"
            onClick={onOpenTask}
            className="inline-flex items-center gap-1.5 rounded-md border border-gray-200 bg-white px-3 py-2 text-[12px] font-semibold text-gray-600 transition-colors hover:bg-gray-50"
          >
            <FilePlus2 size={14} />
            转任务
          </button>
          <button
            type="button"
            onClick={onToggleDeepAnalysis}
            className="inline-flex items-center gap-1.5 rounded-md border border-gray-200 bg-white px-3 py-2 text-[12px] font-semibold text-gray-600 transition-colors hover:bg-gray-50"
          >
            {isDeepAnalysisOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            {isDeepAnalysisOpen ? '收起分析' : '深度分析'}
          </button>
          <button
            type="button"
            onClick={onOpenSource}
            disabled={!candidate.sourceUrl}
            className={`inline-flex items-center gap-1.5 rounded-md border px-3 py-2 text-[12px] font-semibold transition-colors ${
              candidate.sourceUrl
                ? 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
                : 'cursor-not-allowed border-gray-100 bg-gray-50 text-gray-300'
            }`}
          >
            <ExternalLink size={14} />
            打开信息源
          </button>
        </div>
      </div>

      {isDeepAnalysisOpen && children ? (
        <div className="mt-5 border-t border-gray-100 pt-5">
          {children}
        </div>
      ) : null}
    </article>
  );
}
