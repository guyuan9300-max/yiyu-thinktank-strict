import React, { useMemo } from 'react';
import { CalendarClock, ChevronLeft, ChevronRight, Sparkles } from 'lucide-react';

import type { AgentWeeklyDigest, AgentWeeklyPlan, AgentWeeklyPlanPayload, AgentWorklog } from '../../../shared/types';
import { buildCalendarCells, formatMonthTitle } from '../../../shared/calendar';
import { AgentWeeklyPlanEditor } from './AgentWeeklyPlanEditor';

type AgentSimulationCalendarViewProps = {
  agentWorklogs: AgentWorklog[];
  weeklyDigests: AgentWeeklyDigest[];
  weeklyPlans: AgentWeeklyPlan[];
  onSavePlan: (payload: AgentWeeklyPlanPayload) => Promise<void>;
  calendarDate: Date;
  selectedDay: number;
  onSelectDay: (day: number) => void;
  onSelectDate: (date: Date) => void;
  onShiftMonth: (delta: number) => void;
  onGoToToday: () => void;
};

const AGENT_ORDER: Record<AgentWorklog['agentKey'], number> = {
  strategy_design: 0,
  info_data: 1,
  tech_development: 2,
};

function sourceLabel(sourceType: AgentWorklog['sourceType']) {
  if (sourceType === 'activity_log') return '战略动作';
  if (sourceType === 'topic_capture') return '情报处理';
  return '系统同步';
}

function formatDateLabel(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

export function AgentSimulationCalendarView({
  agentWorklogs,
  weeklyDigests,
  weeklyPlans,
  onSavePlan,
  calendarDate,
  selectedDay,
  onSelectDay,
  onSelectDate,
  onShiftMonth,
  onGoToToday,
}: AgentSimulationCalendarViewProps) {
  const calendarCells = useMemo(() => buildCalendarCells(calendarDate), [calendarDate]);
  const selectedDate = useMemo(
    () => new Date(calendarDate.getFullYear(), calendarDate.getMonth(), selectedDay),
    [calendarDate, selectedDay],
  );

  const logsByDay = useMemo(() => {
    const mapping = new Map<number, AgentWorklog[]>();
    agentWorklogs.forEach((log) => {
      const date = new Date(log.date);
      if (Number.isNaN(date.getTime())) return;
      if (date.getFullYear() !== calendarDate.getFullYear() || date.getMonth() !== calendarDate.getMonth()) return;
      const current = mapping.get(date.getDate()) || [];
      current.push(log);
      mapping.set(date.getDate(), current);
    });
    mapping.forEach((items, day) => {
      mapping.set(
        day,
        [...items].sort((left, right) => AGENT_ORDER[left.agentKey] - AGENT_ORDER[right.agentKey]),
      );
    });
    return mapping;
  }, [agentWorklogs, calendarDate]);

  const selectedDayLogs = useMemo(
    () => logsByDay.get(selectedDay) || [],
    [logsByDay, selectedDay],
  );

  const monthStats = useMemo(() => ({
    activeDays: logsByDay.size,
    totalLogs: agentWorklogs.length,
    activeDepartments: new Set(agentWorklogs.map((item) => item.departmentName)).size,
  }), [agentWorklogs, logsByDay.size]);

  return (
    <div className="w-full min-w-0 grid grid-cols-1 xl:grid-cols-[minmax(0,1.35fr)_minmax(360px,0.95fr)] gap-6 items-start">
      <div className="min-w-0 w-full bg-white border border-gray-200 rounded-[32px] shadow-sm overflow-hidden">
        <div className="px-6 lg:px-8 py-6 border-b border-gray-100 space-y-4">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div className="space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                <h2 className="text-[20px] lg:text-[24px] font-bold text-gray-900">{formatMonthTitle(calendarDate)}</h2>
                <span className="text-[11px] font-bold text-[#5B7BFE] bg-blue-50 px-3 py-1 rounded-full">仅 CEO 可见</span>
              </div>
              <div className="flex flex-wrap gap-2 text-[11px] font-semibold">
                <span className="rounded-full bg-slate-100 px-3 py-1 text-slate-600">{monthStats.activeDays} 天有机器人工作痕迹</span>
                <span className="rounded-full bg-emerald-50 px-3 py-1 text-emerald-700">{monthStats.activeDepartments} 个单人部门</span>
                <span className="rounded-full bg-amber-50 px-3 py-1 text-amber-700">{monthStats.totalLogs} 条模拟日程</span>
              </div>
            </div>

            <div className="flex items-center gap-2 self-start lg:self-auto">
              <button
                type="button"
                className="h-11 w-11 rounded-2xl border border-gray-200 text-gray-500 hover:text-[#5B7BFE] hover:border-blue-100 transition-colors"
                onClick={() => onShiftMonth(-1)}
              >
                <ChevronLeft size={18} className="mx-auto" />
              </button>
              <button
                type="button"
                className="h-11 px-4 rounded-2xl border border-gray-200 bg-white text-[13px] font-bold text-gray-700 hover:text-[#5B7BFE] hover:border-blue-100 transition-colors"
                onClick={onGoToToday}
              >
                今天
              </button>
              <button
                type="button"
                className="h-11 w-11 rounded-2xl border border-gray-200 text-gray-500 hover:text-[#5B7BFE] hover:border-blue-100 transition-colors"
                onClick={() => onShiftMonth(1)}
              >
                <ChevronRight size={18} className="mx-auto" />
              </button>
            </div>
          </div>

          <div className="rounded-[28px] border border-blue-100 bg-[linear-gradient(135deg,rgba(239,246,255,0.9),rgba(255,255,255,1))] px-5 py-4">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 h-9 w-9 shrink-0 rounded-2xl bg-white text-[#5B7BFE] shadow-sm flex items-center justify-center">
                <Sparkles size={16} />
              </div>
              <div>
                <p className="text-[14px] font-bold text-gray-900">机器人模拟日程</p>
                <p className="mt-1 text-[12px] leading-6 text-gray-600">
                  这里不把庆华、大周、佳乐当成真实员工，而是把他们每天的真实工作痕迹折算成单人部门的模拟日程，方便 CEO 看组织日常运转。
                </p>
              </div>
            </div>
          </div>
        </div>

        <div className="p-6 lg:p-8">
          <div className="grid grid-cols-7 gap-3 text-[12px] font-bold text-gray-400 mb-3">
            {['周一', '周二', '周三', '周四', '周五', '周六', '周日'].map((label) => (
              <div key={label} className="px-2">{label}</div>
            ))}
          </div>
          <div className="grid grid-cols-7 gap-3">
            {calendarCells.map((cell, index) => {
              const cellLogs = cell.day ? logsByDay.get(cell.day) || [] : [];
              const isSelected = cell.day === selectedDay;
              return (
                <button
                  key={`${cell.day ?? 'blank'}-${index}`}
                  type="button"
                  disabled={!cell.day}
                  onClick={() => {
                    if (!cell.date || !cell.day) return;
                    onSelectDate(cell.date);
                    onSelectDay(cell.day);
                  }}
                  className={`min-h-[126px] rounded-[24px] border p-3 text-left transition-all ${
                    cell.day
                      ? isSelected
                        ? 'border-[#5B7BFE] bg-blue-50/70 shadow-[0_10px_26px_rgba(91,123,254,0.12)]'
                        : 'border-gray-200 bg-white hover:border-blue-100 hover:bg-blue-50/30'
                      : 'border-transparent bg-gray-50/50'
                  }`}
                >
                  {cell.day ? (
                    <div className="h-full flex flex-col">
                      <div className="flex items-center justify-between">
                        <span className={`text-[14px] font-bold ${isSelected ? 'text-[#5B7BFE]' : 'text-gray-900'}`}>{cell.day}</span>
                        {cellLogs.length > 0 && (
                          <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-bold text-gray-500">
                            {cellLogs.length} 条
                          </span>
                        )}
                      </div>
                      <div className="mt-3 space-y-2">
                        {cellLogs.slice(0, 3).map((log) => (
                          <div key={log.id} className="rounded-2xl px-2.5 py-2 text-[10px] font-bold leading-5" style={{ backgroundColor: `${log.color}14`, color: log.color }}>
                            {log.departmentName}
                          </div>
                        ))}
                        {cellLogs.length === 0 && (
                          <div className="rounded-2xl border border-dashed border-gray-200 px-2.5 py-3 text-[10px] text-gray-300">
                            暂无排程
                          </div>
                        )}
                      </div>
                    </div>
                  ) : null}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <div className="space-y-5">
        <div className="bg-white border border-gray-200 rounded-[32px] shadow-sm overflow-hidden">
          <div className="px-6 py-5 border-b border-gray-100">
            <div className="flex items-center gap-2">
              <CalendarClock size={17} className="text-[#5B7BFE]" />
              <h3 className="text-[17px] font-bold text-gray-900">{formatDateLabel(selectedDate)} 模拟日程</h3>
            </div>
            <p className="mt-1 text-[12px] leading-6 text-gray-500">按当天真实工作痕迹折算出的部门日程块，不进入正式员工任务考核。</p>
          </div>
          <div className="p-5 space-y-4">
            {selectedDayLogs.length > 0 ? (
              selectedDayLogs.map((log) => (
                <div key={log.id} className="rounded-[28px] border border-gray-200 bg-gray-50/60 p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="h-3 w-3 rounded-full" style={{ backgroundColor: log.color }} />
                    <p className="text-[14px] font-bold text-gray-900">{log.departmentName}</p>
                    <span className="rounded-full bg-white px-2.5 py-1 text-[10px] font-bold text-gray-500 shadow-sm">{log.agentName}</span>
                    <span className="rounded-full bg-white px-2.5 py-1 text-[10px] font-bold text-gray-500 shadow-sm">{sourceLabel(log.sourceType)}</span>
                  </div>
                  <p className="mt-3 text-[13px] font-bold text-gray-900">{log.title}</p>
                  <p className="mt-2 text-[12px] leading-6 text-gray-600">{log.summary}</p>
                  {log.detailLines.length > 0 && (
                    <div className="mt-3 space-y-2">
                      {log.detailLines.map((item) => (
                        <div key={item} className="rounded-2xl bg-white px-4 py-3 text-[12px] leading-6 text-gray-700 shadow-sm">
                          {item}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))
            ) : (
              <div className="rounded-[28px] border border-dashed border-gray-200 bg-gray-50/60 px-6 py-10 text-center text-[12px] leading-6 text-gray-400">
                这一天还没有采集到机器人部门的工作痕迹，所以不会生成模拟日程块。
              </div>
            )}
          </div>
        </div>

        {weeklyPlans.length > 0 ? (
          <AgentWeeklyPlanEditor plans={weeklyPlans} onSavePlan={onSavePlan} />
        ) : weeklyDigests.length > 0 ? (
          <div className="bg-white border border-gray-200 rounded-[32px] shadow-sm overflow-hidden">
            <div className="px-6 py-5 border-b border-gray-100">
              <h3 className="text-[17px] font-bold text-gray-900">本周部门摘要</h3>
              <p className="mt-1 text-[12px] leading-6 text-gray-500">当前计划层还没生成时，先展示这周三个部门的真实摘要。</p>
            </div>
            <div className="p-5 space-y-4">
              {weeklyDigests.map((digest) => (
                <div key={`${digest.agentKey}:${digest.weekLabel}`} className="rounded-[28px] border border-gray-200 bg-gray-50/60 p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="h-3 w-3 rounded-full" style={{ backgroundColor: digest.color }} />
                    <p className="text-[14px] font-bold text-gray-900">{digest.departmentName}</p>
                    <span className="rounded-full bg-white px-2.5 py-1 text-[10px] font-bold text-gray-500 shadow-sm">{digest.agentName}</span>
                  </div>
                  <p className="mt-3 text-[12px] leading-6 text-gray-600">{digest.summary}</p>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div className="bg-white border border-gray-200 rounded-[32px] shadow-sm overflow-hidden">
            <div className="px-6 py-5 border-b border-gray-100">
              <h3 className="text-[17px] font-bold text-gray-900">本周部门计划</h3>
              <p className="mt-1 text-[12px] leading-6 text-gray-500">当前这一周还没有足够的真实痕迹来推演部门周计划。</p>
            </div>
            <div className="p-5">
              <div className="rounded-[28px] border border-dashed border-gray-200 bg-gray-50/60 px-6 py-10 text-center text-[12px] leading-6 text-gray-400">
                先补更多真实工作痕迹，再生成三个单人部门的周计划。
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
