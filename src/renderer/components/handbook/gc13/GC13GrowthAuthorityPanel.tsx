import React, { useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  Award,
  BrainCircuit,
  CheckCircle2,
  Database,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  type LucideIcon,
} from 'lucide-react';

import {
  confirmGC13GrowthEvidence,
  decideGC13WeeklyReviewCandidate,
  loadGC13GrowthSnapshot,
  rebuildGC13GrowthModels,
  updateGC13GrowthEvidence,
} from './gc13GrowthApi';
import {
  GC13_CATEGORY_OPTIONS,
  gc13BoundarySummary,
  gc13StateCopy,
  type GC13EvidenceCategory,
  type GC13GrowthSnapshot,
} from './gc13GrowthContract';

type Flash = (level: 'success' | 'error', message: string) => void;

export function GC13GrowthAuthorityPanel({ flash }: { flash: Flash }) {
  const [snapshot, setSnapshot] = useState<GC13GrowthSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [decidingCandidateId, setDecidingCandidateId] = useState<string | null>(null);
  const [summary, setSummary] = useState('');
  const [category, setCategory] = useState<GC13EvidenceCategory>('reflection');
  const [editingEvidenceId, setEditingEvidenceId] = useState<string | null>(null);
  const [editingEvidenceSummary, setEditingEvidenceSummary] = useState('');
  const [editingEvidenceCategory, setEditingEvidenceCategory] = useState<GC13EvidenceCategory>('reflection');

  const refresh = async () => {
    setLoading(true);
    try {
      setSnapshot(await loadGC13GrowthSnapshot());
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '成长中心加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const summaryView = useMemo(
    () => (snapshot ? gc13BoundarySummary(snapshot) : null),
    [snapshot],
  );
  const stateCopy = snapshot ? gc13StateCopy(snapshot.rebuild.state) : null;
  const statCards: Array<{ label: string; value: number; Icon: LucideIcon }> = snapshot && summaryView
    ? [
        { label: '成长证据', value: summaryView.evidenceCount, Icon: Database },
        { label: '能力指标', value: summaryView.metricCount, Icon: BrainCircuit },
        { label: '已获徽章', value: summaryView.earnedBadgeCount, Icon: Award },
        { label: '能力维度', value: summaryView.abilityCount, Icon: Sparkles },
      ]
    : [];

  const confirmEvidence = async () => {
    const text = summary.trim();
    if (!text) return;
    setSubmitting(true);
    try {
      await confirmGC13GrowthEvidence({ summary: text, category });
      setSummary('');
      flash('success', '成长证据已记录，能力指标进入待更新状态');
      await refresh();
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '成长证据记录失败');
    } finally {
      setSubmitting(false);
    }
  };

  const rebuild = async () => {
    setRebuilding(true);
    try {
      await rebuildGC13GrowthModels();
      flash('success', '成长指标、徽章与能力已按当前规则重算');
      await refresh();
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '成长能力重算失败');
      await refresh();
    } finally {
      setRebuilding(false);
    }
  };

  const decideCandidate = async (candidateId: string, action: 'confirm' | 'ignore') => {
    setDecidingCandidateId(candidateId);
    try {
      await decideGC13WeeklyReviewCandidate(candidateId, action);
      flash('success', action === 'confirm' ? '周复盘成长证据已确认' : '该成长候选已忽略');
      await refresh();
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '成长候选处理失败');
    } finally {
      setDecidingCandidateId(null);
    }
  };

  const updateEvidence = async (action: 'revise' | 'exclude', evidenceId: string, version: number) => {
    setSubmitting(true);
    try {
      await updateGC13GrowthEvidence(evidenceId, action, {
        expectedVersion: version,
        summary: action === 'revise' ? editingEvidenceSummary.trim() : undefined,
        category: action === 'revise' ? editingEvidenceCategory : undefined,
      });
      setEditingEvidenceId(null);
      flash('success', action === 'revise' ? '成长证据已纠正，等待重新计算' : '该证据已排除，等待重新计算');
      await refresh();
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '成长证据更新失败');
    } finally {
      setSubmitting(false);
    }
  };

  if (loading && !snapshot) {
    return (
      <div className="flex min-h-64 items-center justify-center rounded-3xl border border-slate-100 bg-white text-sm text-slate-500">
        <LoaderCircle className="mr-2 h-4 w-4 animate-spin" />
        正在读取个人成长权威记录
      </div>
    );
  }

  if (!snapshot || !summaryView || !stateCopy) return null;

  const statusTone = {
    ready: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    pending: 'border-blue-200 bg-blue-50 text-blue-700',
    error: 'border-rose-200 bg-rose-50 text-rose-700',
    muted: 'border-slate-200 bg-slate-50 text-slate-600',
  }[stateCopy.tone];

  return (
    <section className="space-y-5" data-gc13-growth-authority>
      <div className="rounded-3xl border border-blue-100 bg-gradient-to-br from-white to-blue-50/60 p-6 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-xs font-semibold tracking-[0.16em] text-blue-600">
              <ShieldCheck className="h-4 w-4" />
              GC-13 · 个人成长权威
            </div>
            <h2 className="mt-2 text-xl font-semibold text-slate-900">成长证据决定能力，读模型随时可重建</h2>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-600">
              徽章、指标和能力只来自本人确认的成长证据与版本化规则；重算失败不会丢失证据，也不会自动生成 Skill。
            </p>
          </div>
          <span className={`rounded-full border px-3 py-1.5 text-xs font-semibold ${statusTone}`}>
            {stateCopy.label}
          </span>
        </div>

        <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {statCards.map(({ label, value, Icon }) => (
            <div key={label} className="rounded-2xl border border-white bg-white/90 p-4 shadow-sm">
              <div className="flex items-center justify-between text-xs text-slate-500">
                {label}
                <Icon className="h-4 w-4 text-blue-500" />
              </div>
              <div className="mt-2 text-2xl font-semibold text-slate-900">{value}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="grid gap-5 xl:grid-cols-[1.08fr_0.92fr]">
        <div className="rounded-3xl border border-slate-100 bg-white p-5 shadow-sm">
          {(snapshot.weeklyReviewCandidates || []).length ? (
            <div className="mb-5 rounded-2xl border border-blue-100 bg-blue-50/60 p-4">
              <h3 className="text-sm font-semibold text-slate-900">来自周复盘的成长候选</h3>
              <p className="mt-1 text-xs leading-5 text-slate-500">复盘提交不会自动变成个人成长事实，请本人确认或忽略。</p>
              <div className="mt-3 space-y-3">
                {(snapshot.weeklyReviewCandidates || []).map((candidate) => (
                  <div key={candidate.candidateId} className="rounded-xl border border-white bg-white p-3">
                    <p className="text-sm leading-6 text-slate-800">{candidate.summary}</p>
                    <div className="mt-3 flex justify-end gap-2">
                      <button
                        type="button"
                        disabled={decidingCandidateId === candidate.candidateId}
                        onClick={() => void decideCandidate(candidate.candidateId, 'ignore')}
                        className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-600 disabled:opacity-50"
                      >
                        忽略
                      </button>
                      <button
                        type="button"
                        disabled={decidingCandidateId === candidate.candidateId}
                        onClick={() => void decideCandidate(candidate.candidateId, 'confirm')}
                        className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
                      >
                        {decidingCandidateId === candidate.candidateId ? '处理中…' : '确认为成长证据'}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          <h3 className="text-sm font-semibold text-slate-900">确认一条成长证据</h3>
          <p className="mt-1 text-xs leading-5 text-slate-500">这里只写新的个人成长事实，不复制项目协作记忆，也不把周复盘原文自行搬入。</p>
          <textarea
            value={summary}
            onChange={(event) => setSummary(event.target.value)}
            rows={4}
            maxLength={2000}
            placeholder="这次实践让我确认了什么能力变化？"
            className="mt-4 w-full resize-none rounded-2xl border border-slate-200 px-4 py-3 text-sm leading-6 text-slate-800 outline-none transition focus:border-blue-400 focus:ring-2 focus:ring-blue-100"
          />
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <select
              value={category}
              onChange={(event) => setCategory(event.target.value as GC13EvidenceCategory)}
              className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700 outline-none"
            >
              {GC13_CATEGORY_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
            <button
              type="button"
              disabled={submitting || !summary.trim()}
              onClick={() => void confirmEvidence()}
              className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
              确认证据
            </button>
          </div>

          <div className="mt-5 space-y-3">
            {snapshot.evidence.slice(0, 8).map((item) => (
              <div key={item.evidenceId} className="rounded-2xl border border-slate-100 bg-slate-50/70 p-4">
                <div className="flex items-center justify-between gap-3 text-xs text-slate-500">
                  <span>{GC13_CATEGORY_OPTIONS.find((option) => option.value === item.category)?.label || item.category}</span>
                  <span>证据 v{item.version}</span>
                </div>
                <p className="mt-2 text-sm leading-6 text-slate-800">{item.summary}</p>
                {editingEvidenceId === item.evidenceId ? (
                  <div className="mt-3 space-y-2">
                    <textarea
                      value={editingEvidenceSummary}
                      onChange={(event) => setEditingEvidenceSummary(event.target.value)}
                      rows={3}
                      className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm"
                    />
                    <div className="flex items-center justify-between gap-2">
                      <select
                        value={editingEvidenceCategory}
                        onChange={(event) => setEditingEvidenceCategory(event.target.value as GC13EvidenceCategory)}
                        className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs"
                      >
                        {GC13_CATEGORY_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                      </select>
                      <div className="flex gap-2">
                        <button type="button" onClick={() => setEditingEvidenceId(null)} className="text-xs text-slate-500">取消</button>
                        <button type="button" disabled={submitting || !editingEvidenceSummary.trim()} onClick={() => void updateEvidence('revise', item.evidenceId, item.version)} className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50">保存纠正</button>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="mt-3 flex justify-end gap-3 text-xs">
                    <button
                      type="button"
                      onClick={() => {
                        setEditingEvidenceId(item.evidenceId);
                        setEditingEvidenceSummary(item.summary);
                        setEditingEvidenceCategory(item.category);
                      }}
                      className="text-blue-600"
                    >纠正</button>
                    <button type="button" disabled={submitting} onClick={() => void updateEvidence('exclude', item.evidenceId, item.version)} className="text-slate-500 disabled:opacity-50">排除</button>
                  </div>
                )}
              </div>
            ))}
            {!snapshot.evidence.length ? (
              <div className="rounded-2xl border border-dashed border-slate-200 p-6 text-center text-sm text-slate-500">还没有已确认成长证据</div>
            ) : null}
          </div>
        </div>

        <div className="space-y-5">
          <div className="rounded-3xl border border-slate-100 bg-white p-5 shadow-sm">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-slate-900">失败与重算</h3>
                <p className="mt-1 text-xs leading-5 text-slate-500">{snapshot.rebuild.message}</p>
              </div>
              {snapshot.rules.length ? (
                <button
                  type="button"
                  disabled={rebuilding}
                  onClick={() => void rebuild()}
                  className="inline-flex shrink-0 items-center gap-2 rounded-xl border border-blue-200 bg-blue-50 px-3 py-2 text-xs font-semibold text-blue-700 hover:bg-blue-100 disabled:opacity-50"
                >
                  <RefreshCw className={`h-4 w-4 ${rebuilding ? 'animate-spin' : ''}`} />
                  手动刷新
                </button>
              ) : null}
            </div>
            {snapshot.rebuild.state === 'failed_retryable' ? (
              <div className="mt-4 flex gap-2 rounded-2xl border border-rose-100 bg-rose-50 p-3 text-xs leading-5 text-rose-700">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                重算失败只影响徽章与能力展示，成长证据没有被回滚或覆盖。
              </div>
            ) : null}
            <div className="mt-4 space-y-2">
              {snapshot.rules.map((rule) => (
                <div key={rule.ruleVersionId} className="flex items-center justify-between rounded-xl bg-slate-50 px-3 py-2 text-xs">
                  <span className="font-medium text-slate-700">{rule.spec.label || rule.metricKey}</span>
                  <span className="text-slate-500">规则 v{rule.ruleVersion}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-3xl border border-slate-100 bg-white p-5 shadow-sm">
            <h3 className="text-sm font-semibold text-slate-900">成长分析</h3>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              {snapshot.companion.mode === 'base_mode'
                ? snapshot.companion.baseMode
                : `已按 ${snapshot.companion.sourceLabels.join('、')} 提供成长解释。`}
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              {snapshot.companion.boundaries.map((boundary) => (
                <span key={boundary} className="rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-[11px] text-slate-600">{boundary}</span>
              ))}
            </div>
            {snapshot.companion.allowedPreferences.length ? (
              <div className="mt-4 rounded-2xl bg-blue-50 p-3 text-xs text-blue-700">
                本次只应用本人允许的通用偏好：{snapshot.companion.allowedPreferences.map((item) => item.label).join('、')}
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  );
}
