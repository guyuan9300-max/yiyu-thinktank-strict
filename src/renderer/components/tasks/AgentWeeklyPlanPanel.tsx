import type { AgentWeeklyPlan } from '../../../shared/types';

type AgentWeeklyPlanPanelProps = {
  plans: AgentWeeklyPlan[];
  title?: string;
  subtitle?: string;
};

function sourceLabel(sourceType: unknown) {
  if (sourceType === 'activity_log') return '战略动作';
  if (sourceType === 'topic_capture') return '情报处理';
  if (sourceType === 'workspace_sync') return '系统同步';
  return '真实日志';
}

function statusLabel(status: string) {
  if (status === 'doing') return '进行中';
  if (status === 'done') return '已完成';
  if (status === 'blocked') return '阻塞中';
  return '待推进';
}

function statusClass(status: string) {
  if (status === 'doing') return 'bg-blue-50 text-blue-700';
  if (status === 'done') return 'bg-emerald-50 text-emerald-700';
  if (status === 'blocked') return 'bg-rose-50 text-rose-700';
  return 'bg-amber-50 text-amber-700';
}

export function AgentWeeklyPlanPanel({
  plans,
  title = '三个部门本周计划',
  subtitle = '这些计划不是手工编造，而是基于真实工作痕迹和部门职责自动推演出的组织视角计划板。',
}: AgentWeeklyPlanPanelProps) {
  if (plans.length === 0) return null;

  return (
    <div className="bg-white border border-gray-200 rounded-3xl shadow-sm overflow-hidden">
      <div className="px-6 py-5 border-b border-gray-100 bg-[linear-gradient(135deg,rgba(255,247,237,0.95),rgba(255,255,255,1))]">
        <h2 className="text-[18px] font-bold text-gray-900">{title}</h2>
        <p className="mt-1 text-[12px] leading-6 text-gray-600">{subtitle}</p>
      </div>

      <div className="grid gap-5 p-6 xl:grid-cols-3">
        {plans.map((plan) => (
          <div key={`${plan.agentKey}:${plan.weekLabel}`} className="rounded-[28px] border border-gray-200 bg-gray-50/60 p-5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="h-3 w-3 rounded-full" style={{ backgroundColor: plan.color }} />
              <p className="text-[15px] font-bold text-gray-900">{plan.departmentName}</p>
              <span className="rounded-full bg-white px-2.5 py-1 text-[10px] font-bold text-gray-500 shadow-sm">{plan.agentName}</span>
            </div>

            <div className="mt-3 flex flex-wrap gap-2">
              <span className="rounded-full bg-white px-2.5 py-1 text-[10px] font-bold text-gray-500 shadow-sm">{plan.weekLabel}</span>
              <span className="rounded-full bg-white px-2.5 py-1 text-[10px] font-bold text-gray-500 shadow-sm">
                {sourceLabel(plan.sourcePolicy?.sourceType)}
              </span>
            </div>

            <p className="mt-4 text-[13px] leading-6 text-gray-700">{plan.summary}</p>

            <div className="mt-4 space-y-3">
              {plan.planItems.map((item) => (
                <div key={item.id} className="rounded-2xl bg-white px-4 py-3 shadow-sm">
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-[12px] font-bold text-gray-900">{item.title}</p>
                    <span className={`rounded-full px-2.5 py-1 text-[10px] font-bold ${statusClass(item.status)}`}>
                      {statusLabel(item.status)}
                    </span>
                  </div>
                  {item.rationale && <p className="mt-1 text-[12px] leading-6 text-gray-600">{item.rationale}</p>}
                  {item.scheduleHint && <p className="mt-2 text-[11px] font-bold text-amber-600">{item.scheduleHint}</p>}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
