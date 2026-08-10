import { useEffect, useMemo, useState } from 'react';

import type { AgentPlanStatus, AgentWeeklyPlan, AgentWeeklyPlanPayload } from '../../../shared/types';

type AgentWeeklyPlanEditorProps = {
  plans: AgentWeeklyPlan[];
  onSavePlan: (payload: AgentWeeklyPlanPayload) => Promise<void>;
};

type DraftPlan = {
  summary: string;
  planItems: Array<{
    title: string;
    rationale: string;
    scheduleHint: string;
    status: AgentPlanStatus;
  }>;
};

const STATUS_OPTIONS: Array<{ value: AgentPlanStatus; label: string }> = [
  { value: 'planned', label: '待推进' },
  { value: 'doing', label: '进行中' },
  { value: 'done', label: '已完成' },
  { value: 'blocked', label: '阻塞中' },
];

function createDraftMap(plans: AgentWeeklyPlan[]) {
  return Object.fromEntries(
    plans.map((plan) => [
      plan.agentKey,
      {
        summary: plan.summary,
        planItems: plan.planItems.map((item) => ({
          title: item.title,
          rationale: item.rationale,
          scheduleHint: item.scheduleHint,
          status: item.status,
        })),
      } satisfies DraftPlan,
    ]),
  ) as Record<string, DraftPlan>;
}

function statusClass(status: AgentPlanStatus) {
  if (status === 'doing') return 'bg-blue-50 text-blue-700';
  if (status === 'done') return 'bg-emerald-50 text-emerald-700';
  if (status === 'blocked') return 'bg-rose-50 text-rose-700';
  return 'bg-amber-50 text-amber-700';
}

export function AgentWeeklyPlanEditor({ plans, onSavePlan }: AgentWeeklyPlanEditorProps) {
  const [drafts, setDrafts] = useState<Record<string, DraftPlan>>(() => createDraftMap(plans));
  const [savingKey, setSavingKey] = useState<string | null>(null);

  useEffect(() => {
    setDrafts(createDraftMap(plans));
  }, [plans]);

  const orderedPlans = useMemo(
    () => [...plans].sort((left, right) => left.departmentName.localeCompare(right.departmentName, 'zh-CN')),
    [plans],
  );

  const updateDraft = (agentKey: string, updater: (draft: DraftPlan) => DraftPlan) => {
    setDrafts((prev) => ({
      ...prev,
      [agentKey]: updater(prev[agentKey] || { summary: '', planItems: [] }),
    }));
  };

  if (plans.length === 0) {
    return (
      <div className="bg-white border border-gray-200 rounded-[32px] shadow-sm overflow-hidden">
        <div className="px-6 py-5 border-b border-gray-100">
          <h3 className="text-[17px] font-bold text-gray-900">本周正式计划</h3>
          <p className="mt-1 text-[12px] leading-6 text-gray-500">当前这一周还没有足够的真实痕迹，暂时无法生成可调整的正式计划。</p>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-white border border-gray-200 rounded-[32px] shadow-sm overflow-hidden">
      <div className="px-6 py-5 border-b border-gray-100 bg-[linear-gradient(135deg,rgba(255,247,237,0.95),rgba(255,255,255,1))]">
        <h3 className="text-[18px] font-bold text-gray-900">三个部门本周正式计划</h3>
        <p className="mt-1 text-[12px] leading-6 text-gray-600">
          这里是 CEO 可调整的正式计划层。默认值来自真实日志推演，但你一旦保存，后续模拟日程和周复盘都会优先读这里。
        </p>
      </div>

      <div className="grid gap-5 p-6 xl:grid-cols-3">
        {orderedPlans.map((plan) => {
          const draft = drafts[plan.agentKey] || { summary: '', planItems: [] };
          return (
            <div key={`${plan.agentKey}:${plan.weekLabel}`} className="rounded-[28px] border border-gray-200 bg-gray-50/60 p-5">
              <div className="flex flex-wrap items-center gap-2">
                <span className="h-3 w-3 rounded-full" style={{ backgroundColor: plan.color }} />
                <p className="text-[15px] font-bold text-gray-900">{plan.departmentName}</p>
                <span className="rounded-full bg-white px-2.5 py-1 text-[10px] font-bold text-gray-500 shadow-sm">{plan.agentName}</span>
                {plan.sourcePolicy?.manualOverride === true && (
                  <span className="rounded-full bg-slate-900 px-2.5 py-1 text-[10px] font-bold text-white">已人工修订</span>
                )}
              </div>

              <textarea
                value={draft.summary}
                onChange={(event) =>
                  updateDraft(plan.agentKey, (current) => ({ ...current, summary: event.target.value }))
                }
                className="mt-4 min-h-[112px] w-full rounded-2xl border border-gray-200 bg-white px-4 py-3 text-[12px] leading-6 text-gray-700 outline-none focus:border-amber-200"
              />

              <div className="mt-4 space-y-3">
                {draft.planItems.map((item, index) => (
                  <div key={`${plan.agentKey}-${index}`} className="rounded-2xl bg-white px-4 py-4 shadow-sm space-y-3">
                    <div className="flex items-center justify-between gap-3">
                      <span className={`rounded-full px-2.5 py-1 text-[10px] font-bold ${statusClass(item.status)}`}>
                        {STATUS_OPTIONS.find((option) => option.value === item.status)?.label || '待推进'}
                      </span>
                      <button
                        type="button"
                        className="text-[11px] font-bold text-gray-400 hover:text-rose-500"
                        onClick={() =>
                          updateDraft(plan.agentKey, (current) => ({
                            ...current,
                            planItems: current.planItems.filter((_, itemIndex) => itemIndex !== index),
                          }))
                        }
                      >
                        删除
                      </button>
                    </div>
                    <input
                      value={item.title}
                      onChange={(event) =>
                        updateDraft(plan.agentKey, (current) => ({
                          ...current,
                          planItems: current.planItems.map((currentItem, itemIndex) =>
                            itemIndex === index ? { ...currentItem, title: event.target.value } : currentItem,
                          ),
                        }))
                      }
                      className="w-full rounded-2xl border border-gray-200 bg-gray-50 px-3 py-2 text-[12px] font-bold text-gray-800 outline-none focus:border-amber-200 focus:bg-white"
                    />
                    <textarea
                      value={item.rationale}
                      onChange={(event) =>
                        updateDraft(plan.agentKey, (current) => ({
                          ...current,
                          planItems: current.planItems.map((currentItem, itemIndex) =>
                            itemIndex === index ? { ...currentItem, rationale: event.target.value } : currentItem,
                          ),
                        }))
                      }
                      className="min-h-[88px] w-full rounded-2xl border border-gray-200 bg-gray-50 px-3 py-3 text-[12px] leading-6 text-gray-600 outline-none focus:border-amber-200 focus:bg-white"
                    />
                    <input
                      value={item.scheduleHint}
                      onChange={(event) =>
                        updateDraft(plan.agentKey, (current) => ({
                          ...current,
                          planItems: current.planItems.map((currentItem, itemIndex) =>
                            itemIndex === index ? { ...currentItem, scheduleHint: event.target.value } : currentItem,
                          ),
                        }))
                      }
                      className="w-full rounded-2xl border border-gray-200 bg-gray-50 px-3 py-2 text-[12px] text-gray-700 outline-none focus:border-amber-200 focus:bg-white"
                    />
                    <select
                      value={item.status}
                      onChange={(event) =>
                        updateDraft(plan.agentKey, (current) => ({
                          ...current,
                          planItems: current.planItems.map((currentItem, itemIndex) =>
                            itemIndex === index ? { ...currentItem, status: event.target.value as AgentPlanStatus } : currentItem,
                          ),
                        }))
                      }
                      className="w-full rounded-2xl border border-gray-200 bg-gray-50 px-3 py-2 text-[12px] font-bold text-gray-700 outline-none focus:border-amber-200 focus:bg-white"
                    >
                      {STATUS_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </div>
                ))}
              </div>

              <div className="mt-4 flex items-center justify-between gap-3">
                <button
                  type="button"
                  className="rounded-2xl border border-dashed border-gray-300 px-3 py-2 text-[12px] font-bold text-gray-500 hover:border-amber-200 hover:text-amber-700"
                  onClick={() =>
                    updateDraft(plan.agentKey, (current) => ({
                      ...current,
                      planItems: [
                        ...current.planItems,
                        { title: '', rationale: '', scheduleHint: '', status: 'planned' },
                      ],
                    }))
                  }
                >
                  新增计划项
                </button>
                <button
                  type="button"
                  disabled={savingKey === plan.agentKey}
                  className="rounded-2xl bg-[#5B7BFE] px-4 py-2 text-[12px] font-bold text-white shadow-sm disabled:opacity-60"
                  onClick={async () => {
                    setSavingKey(plan.agentKey);
                    try {
                      await onSavePlan({
                        weekLabel: plan.weekLabel,
                        agentKey: plan.agentKey,
                        summary: draft.summary,
                        planItems: draft.planItems.filter((item) => item.title.trim()),
                      });
                    } finally {
                      setSavingKey(null);
                    }
                  }}
                >
                  {savingKey === plan.agentKey ? '保存中...' : '保存正式计划'}
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
