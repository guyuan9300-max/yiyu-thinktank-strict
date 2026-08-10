import React, { useEffect, useMemo, useState } from 'react';
import { Archive, CalendarDays, CheckSquare2, LoaderCircle, RotateCcw } from 'lucide-react';

import { gc06Api } from './gc06Api';
import {
  assertEventLineClient,
  primaryActionTaskStatus,
  weeklyReviewStableKey,
  type GC06DecisionAction,
  type GC06EventLine,
  type GC06PlanKind,
  type GC06PlanningCycle,
  type GC06WeeklyReview,
} from './gc06Contract';

type Flash = (level: 'success' | 'error', message: string) => void;

export function GC06PlanningWorkspace({
  clientId,
  membershipId,
  departmentId = null,
  flash,
}: {
  clientId: string | null;
  membershipId: string;
  departmentId?: string | null;
  flash: Flash;
}) {
  const [eventLines, setEventLines] = useState<GC06EventLine[]>([]);
  const [plans, setPlans] = useState<GC06PlanningCycle[]>([]);
  const [reviews, setReviews] = useState<GC06WeeklyReview[]>([]);
  const [actions, setActions] = useState<GC06DecisionAction[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [lineName, setLineName] = useState('');
  const [reviewText, setReviewText] = useState('');
  const [selectedPlanId, setSelectedPlanId] = useState('');
  const [planTitle, setPlanTitle] = useState('');
  const [planKind, setPlanKind] = useState<GC06PlanKind>('organization_plan');
  const [periodStart, setPeriodStart] = useState('');
  const [periodEnd, setPeriodEnd] = useState('');
  const [actionTitle, setActionTitle] = useState('');

  const refresh = async () => {
    setLoading(true);
    try {
      const [nextLines, nextPlans, nextReviews, nextActions] = await Promise.all([
        gc06Api.listEventLines(clientId || undefined),
        gc06Api.listPlanningCycles(),
        gc06Api.listWeeklyReviews(),
        gc06Api.listDecisionActions(),
      ]);
      setEventLines(nextLines);
      setPlans(nextPlans);
      setReviews(nextReviews);
      setActions(nextActions);
      setSelectedPlanId((current) => current || nextPlans[0]?.id || '');
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '计划与复盘加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void refresh(); }, [clientId]);

  const currentReview = useMemo(
    () => selectedPlanId
      ? reviews.find((item) => (
          weeklyReviewStableKey(item.membershipId, item.planningCycleId)
          === weeklyReviewStableKey(membershipId, selectedPlanId)
        ))
      : undefined,
    [membershipId, reviews, selectedPlanId],
  );

  const createLine = async () => {
    const name = lineName.trim();
    if (!name) return;
    try {
      setBusy(true);
      const requiredClientId = assertEventLineClient(clientId);
      await gc06Api.createEventLine({ clientId: requiredClientId, name });
      setLineName('');
      flash('success', '事件线已创建');
      await refresh();
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '事件线创建失败');
    } finally {
      setBusy(false);
    }
  };

  const transitionLine = async (line: GC06EventLine) => {
    try {
      setBusy(true);
      await gc06Api.transitionEventLine(
        line,
        line.lifecycleState === 'archived' ? 'reopen' : 'archive',
      );
      await refresh();
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '事件线状态更新失败');
    } finally {
      setBusy(false);
    }
  };

  const saveReview = async () => {
    if (!selectedPlanId || !reviewText.trim()) return;
    try {
      setBusy(true);
      await gc06Api.saveWeeklyReviewDraft({
        planningCycleId: selectedPlanId,
        membershipId,
        content: { summary: reviewText.trim() },
        expectedVersion: currentReview?.version || 0,
      });
      setReviewText('');
      flash('success', '周复盘新版本已保存，稳定身份未变化');
      await refresh();
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '周复盘保存失败');
    } finally {
      setBusy(false);
    }
  };

  const submitReview = async () => {
    if (!currentReview || currentReview.status !== 'draft') return;
    try {
      setBusy(true);
      await gc06Api.transitionWeeklyReview(currentReview, 'submit');
      flash('success', '周复盘已正式提交，可作为计划行动的证据');
      await refresh();
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '周复盘提交失败');
    } finally {
      setBusy(false);
    }
  };

  const createPlan = async () => {
    if (!planTitle.trim() || !periodStart || !periodEnd) return;
    try {
      setBusy(true);
      await gc06Api.createPlanningCycle({
        recordKind: planKind,
        departmentId: planKind === 'department_plan' ? departmentId : null,
        clientId,
        title: planTitle.trim(),
        periodKind: 'custom',
        periodStart,
        periodEnd,
      });
      setPlanTitle('');
      flash('success', '计划周期已创建');
      await refresh();
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '计划周期创建失败');
    } finally {
      setBusy(false);
    }
  };

  const createAction = async () => {
    if (!selectedPlanId || !actionTitle.trim()) return;
    const submittedReviewVersionId = currentReview?.currentSubmittedVersionId || null;
    if (!submittedReviewVersionId) {
      flash('error', '请先保存并提交本周期复盘，再从正式证据形成行动');
      return;
    }
    try {
      setBusy(true);
      await gc06Api.createDecisionAction({
        planningCycleId: selectedPlanId,
        recordKind: 'plan_action',
        decisionState: 'confirmed',
        title: actionTitle.trim(),
        statement: '由本周期正式周复盘形成的计划行动',
        reviewVersionId: submittedReviewVersionId,
      });
      setActionTitle('');
      flash('success', '计划行动已保留正式复盘证据，可转为任务');
      await refresh();
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '计划行动创建失败');
    } finally {
      setBusy(false);
    }
  };

  const convertAction = async (action: GC06DecisionAction) => {
    try {
      setBusy(true);
      await gc06Api.convertActionToPrimaryTask(action);
      flash('success', '正式任务已承接该主要行动');
      await refresh();
    } catch (error) {
      flash('error', error instanceof Error ? error.message : '正式任务命令尚未接通');
    } finally {
      setBusy(false);
    }
  };

  if (loading && !plans.length && !eventLines.length) {
    return <div className="flex min-h-64 items-center justify-center text-sm text-slate-500"><LoaderCircle className="mr-2 h-4 w-4 animate-spin" />读取 GC-06 权威数据</div>;
  }

  return (
    <section className="space-y-5" data-gc06-planning-workspace>
      <header className="rounded-3xl border border-indigo-100 bg-indigo-50/60 p-5">
        <div className="flex items-center gap-2 text-xs font-semibold tracking-[0.16em] text-indigo-700"><CalendarDays className="h-4 w-4" />GC-06 · 计划与周复盘</div>
        <h2 className="mt-2 text-xl font-semibold text-slate-900">从事件线组织计划，用稳定版本完成周复盘</h2>
        <p className="mt-2 text-sm text-slate-600">日历只展示任务和会议派生项；计划行动先进入决策行动，再由正式任务命令承接。</p>
      </header>

      <div className="grid gap-5 xl:grid-cols-2">
        <article className="rounded-3xl border border-slate-100 bg-white p-5 shadow-sm">
          <h3 className="text-sm font-semibold text-slate-900">客户事件线</h3>
          <div className="mt-3 flex gap-2">
            <input value={lineName} onChange={(event) => setLineName(event.target.value)} placeholder={clientId ? '输入事件线名称' : '先选择客户'} disabled={!clientId || busy} className="min-w-0 flex-1 rounded-xl border border-slate-200 px-3 py-2 text-sm" />
            <button type="button" onClick={() => void createLine()} disabled={!clientId || !lineName.trim() || busy} className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">创建</button>
          </div>
          <div className="mt-4 space-y-2">
            {eventLines.map((line) => (
              <div key={line.id} className="flex items-center justify-between rounded-2xl bg-slate-50 p-3">
                <div><div className="text-sm font-medium text-slate-800">{line.name}</div><div className="mt-1 text-xs text-slate-500">任务 {line.taskCount} · 会议 {line.meetingCount} · 活动 {line.activityCount} · v{line.version}</div></div>
                <button type="button" disabled={busy} onClick={() => void transitionLine(line)} className="rounded-lg border border-slate-200 p-2 text-slate-600" aria-label={line.lifecycleState === 'archived' ? '重开事件线' : '归档事件线'}>{line.lifecycleState === 'archived' ? <RotateCcw className="h-4 w-4" /> : <Archive className="h-4 w-4" />}</button>
              </div>
            ))}
          </div>
        </article>

        <article className="rounded-3xl border border-slate-100 bg-white p-5 shadow-sm">
          <h3 className="text-sm font-semibold text-slate-900">我的周复盘</h3>
          <select value={selectedPlanId} onChange={(event) => setSelectedPlanId(event.target.value)} className="mt-3 w-full rounded-xl border border-slate-200 px-3 py-2 text-sm">
            <option value="">选择计划周期</option>
            {plans.map((plan) => <option key={plan.id} value={plan.id}>{plan.title} · {plan.periodStart.slice(0, 10)}</option>)}
          </select>
          <textarea value={reviewText} onChange={(event) => setReviewText(event.target.value)} rows={5} placeholder="记录本周期进展、阻碍和下一步" className="mt-3 w-full resize-none rounded-2xl border border-slate-200 p-3 text-sm" />
          <div className="mt-3 flex items-center justify-between gap-3 text-xs text-slate-500">
            <span>{currentReview ? `稳定复盘 ID · ${currentReview.id} · ${currentReview.versions.length} 个版本 · ${currentReview.status === 'submitted' ? '已提交' : '草稿'}` : '首次保存将创建稳定复盘身份'}</span>
            <div className="flex shrink-0 gap-2">
              {currentReview?.status === 'draft' ? <button type="button" disabled={busy} onClick={() => void submitReview()} className="rounded-xl border border-indigo-200 px-3 py-2 text-sm font-semibold text-indigo-700 disabled:opacity-40">提交复盘</button> : null}
              <button type="button" disabled={!selectedPlanId || !reviewText.trim() || busy} onClick={() => void saveReview()} className="rounded-xl bg-indigo-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">保存新版本</button>
            </div>
          </div>
        </article>
      </div>

      <article className="rounded-3xl border border-slate-100 bg-white p-5 shadow-sm">
        <h3 className="text-sm font-semibold text-slate-900">组织 / 部门计划周期</h3>
        <div className="mt-3 grid gap-2 md:grid-cols-[1fr_9rem_9rem_9rem_auto]">
          <input value={planTitle} onChange={(event) => setPlanTitle(event.target.value)} placeholder="计划标题" className="rounded-xl border border-slate-200 px-3 py-2 text-sm" />
          <select value={planKind} onChange={(event) => setPlanKind(event.target.value as GC06PlanKind)} className="rounded-xl border border-slate-200 px-3 py-2 text-sm">
            <option value="organization_plan">组织计划</option>
            <option value="department_plan" disabled={!departmentId}>部门计划</option>
          </select>
          <input type="date" value={periodStart} onChange={(event) => setPeriodStart(event.target.value)} className="rounded-xl border border-slate-200 px-3 py-2 text-sm" />
          <input type="date" value={periodEnd} onChange={(event) => setPeriodEnd(event.target.value)} className="rounded-xl border border-slate-200 px-3 py-2 text-sm" />
          <button type="button" disabled={busy || !planTitle.trim() || !periodStart || !periodEnd || (planKind === 'department_plan' && !departmentId)} onClick={() => void createPlan()} className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">新建周期</button>
        </div>
      </article>

      <article className="rounded-3xl border border-slate-100 bg-white p-5 shadow-sm">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-900"><CheckSquare2 className="h-4 w-4 text-indigo-600" />计划行动</div>
        <div className="mt-3 flex gap-2">
          <input value={actionTitle} onChange={(event) => setActionTitle(event.target.value)} placeholder={selectedPlanId ? '输入计划行动' : '先选择计划周期'} disabled={!selectedPlanId || busy} className="min-w-0 flex-1 rounded-xl border border-slate-200 px-3 py-2 text-sm" />
          <button type="button" onClick={() => void createAction()} disabled={!selectedPlanId || !actionTitle.trim() || !currentReview?.currentSubmittedVersionId || busy} className="rounded-xl border border-indigo-200 bg-indigo-50 px-4 py-2 text-sm font-semibold text-indigo-700 disabled:opacity-40">由复盘形成行动</button>
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          {actions.map((action) => {
            const taskStatus = primaryActionTaskStatus(action);
            return <div key={action.id} className="rounded-2xl border border-slate-100 p-4"><div className="text-sm font-medium text-slate-800">{action.title}</div><p className="mt-2 text-xs leading-5 text-slate-500">{action.statement}</p><div className="mt-3 flex items-center justify-between gap-2"><span className={`text-xs ${taskStatus.connected ? 'text-emerald-700' : 'text-amber-700'}`}>{taskStatus.label}</span>{!taskStatus.connected && action.decisionState === 'confirmed' ? <button type="button" disabled={busy} onClick={() => void convertAction(action)} className="rounded-lg border border-slate-200 px-2 py-1 text-xs text-slate-700">转正式任务</button> : null}</div></div>;
          })}
        </div>
      </article>
    </section>
  );
}
