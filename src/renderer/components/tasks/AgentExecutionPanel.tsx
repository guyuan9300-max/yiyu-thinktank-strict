import React, { useEffect, useMemo, useState } from 'react';

import type { Task } from '../../../shared/types';
import { getAgentExecutionTasks } from '../../lib/api';

type AgentExecutionPanelProps = {
  weekLabel: string;
  title: string;
  subtitle: string;
  departmentName?: string | null;
};

type DisplayStatus = 'todo' | 'doing' | 'done' | 'blocked';

const displayStatusClassMap: Record<DisplayStatus, string> = {
  todo: 'bg-slate-100 text-slate-700',
  doing: 'bg-blue-50 text-blue-700',
  done: 'bg-emerald-50 text-emerald-700',
  blocked: 'bg-rose-50 text-rose-700',
};

const displayStatusLabelMap: Record<DisplayStatus, string> = {
  todo: '待推进',
  doing: '进行中',
  done: '已完成',
  blocked: '阻塞中',
};

function resolveDepartmentName(task: Task) {
  const departmentTag = task.tags.find((tag) => tag.name.trim().endsWith('部'));
  if (departmentTag) return departmentTag.name;
  if (task.sourceId?.includes('strategy_design') || task.ownerName === '庆华') return '咨询策略部';
  if (task.sourceId?.includes('tech_development') || task.ownerName === '佳乐') return '科技发展部';
  if (task.sourceId?.includes('info_data') || task.ownerName === '大周') return '信息数据部';
  return '机器人部门';
}

function resolveDisplayStatus(task: Task): DisplayStatus {
  const matched = task.note?.match(/状态：([^\n]+)/);
  const planStatus = matched?.[1]?.trim().toLowerCase() || '';
  if (planStatus === 'blocked') return 'blocked';
  if (planStatus === 'done') return 'done';
  if (planStatus === 'doing') return 'doing';
  if (planStatus === 'planned') return 'todo';
  if (task.status === 'done') return 'done';
  if (task.status === 'doing') return 'doing';
  return 'todo';
}

function formatDateLabel(value?: string | null) {
  if (!value) return '未设置';
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return value.slice(5);
  }
  const iso = value.slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(iso) ? iso.slice(5) : value;
}

function buildNoteHighlights(task: Task) {
  return (task.note || '')
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith('状态：') && !line.startsWith('部门：'))
    .slice(0, 3);
}

export function AgentExecutionPanel({ weekLabel, title, subtitle, departmentName }: AgentExecutionPanelProps) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let disposed = false;
    setLoading(true);
    setError(null);
    void getAgentExecutionTasks(weekLabel, departmentName || undefined)
      .then((response) => {
        if (disposed) return;
        setTasks(response);
      })
      .catch((err) => {
        if (disposed) return;
        setError(err instanceof Error ? err.message : '机器人执行层加载失败');
        setTasks([]);
      })
      .finally(() => {
        if (!disposed) {
          setLoading(false);
        }
      });
    return () => {
      disposed = true;
    };
  }, [departmentName, reloadToken, weekLabel]);

  const groupedTasks = useMemo(() => {
    const grouped = new Map<string, Task[]>();
    tasks.forEach((task) => {
      const key = resolveDepartmentName(task);
      const bucket = grouped.get(key);
      if (bucket) {
        bucket.push(task);
      } else {
        grouped.set(key, [task]);
      }
    });
    return Array.from(grouped.entries()).map(([name, items]) => ({
      name,
      items: items.slice().sort((left, right) => right.updatedAt.localeCompare(left.updatedAt)),
    }));
  }, [tasks]);

  const statusCounts = useMemo(() => {
    const counts: Record<DisplayStatus, number> = { todo: 0, doing: 0, done: 0, blocked: 0 };
    tasks.forEach((task) => {
      counts[resolveDisplayStatus(task)] += 1;
    });
    return counts;
  }, [tasks]);

  return (
    <div className="rounded-3xl border border-gray-200 bg-white p-5 shadow-sm">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h3 className="text-[16px] font-bold text-gray-900">{title}</h3>
          <p className="mt-1 text-[12px] leading-6 text-gray-500">{subtitle}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <span className="rounded-full bg-slate-100 px-3 py-1.5 text-[11px] font-bold text-slate-700">{tasks.length} 条正式任务</span>
          {(['done', 'doing', 'blocked', 'todo'] as DisplayStatus[]).map((status) => (
            <span key={status} className={`rounded-full px-3 py-1.5 text-[11px] font-bold ${displayStatusClassMap[status]}`}>
              {displayStatusLabelMap[status]} {statusCounts[status]}
            </span>
          ))}
        </div>
      </div>

      {loading && <div className="mt-4 rounded-2xl bg-slate-50 px-4 py-3 text-[13px] text-slate-500">正在同步机器人本周正式任务...</div>}

      {!loading && error && (
        <div className="mt-4 rounded-2xl border border-rose-100 bg-rose-50 px-4 py-3 text-[13px] text-rose-700">
          <div>{error}</div>
          <button
            type="button"
            className="mt-3 rounded-xl border border-rose-200 bg-white px-3 py-1.5 text-[12px] font-bold text-rose-700"
            onClick={() => setReloadToken((value) => value + 1)}
          >
            重试加载
          </button>
        </div>
      )}

      {!loading && !error && tasks.length === 0 && (
        <div className="mt-4 rounded-2xl bg-slate-50 px-4 py-3 text-[13px] leading-6 text-slate-600">
          当前周没有可展示的机器人正式任务。这通常表示该部门本周还没有形成可同步的执行项，或者该部门目前没有机器人执行单元。
        </div>
      )}

      {!loading && !error && tasks.length > 0 && (
        <div className="mt-4 space-y-4">
          {groupedTasks.map((group) => (
            <div key={group.name} className="space-y-3">
              {!departmentName && (
                <div className="flex items-center justify-between">
                  <h4 className="text-[13px] font-bold text-gray-900">{group.name}</h4>
                  <span className="rounded-full bg-gray-100 px-3 py-1 text-[11px] font-bold text-gray-600">{group.items.length} 条</span>
                </div>
              )}
              <div className="space-y-3">
                {group.items.map((task) => {
                  const displayStatus = resolveDisplayStatus(task);
                  const highlights = buildNoteHighlights(task);
                  return (
                    <div key={task.id} className="rounded-2xl border border-gray-100 bg-slate-50/60 p-4">
                      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="text-[14px] font-bold text-gray-900">{task.title}</p>
                            <span className={`rounded-full px-2.5 py-1 text-[11px] font-bold ${displayStatusClassMap[displayStatus]}`}>
                              {displayStatusLabelMap[displayStatus]}
                            </span>
                          </div>
                          <p className="mt-1 text-[12px] text-gray-500">
                            {group.name} · {task.ownerName} · 截止 {formatDateLabel(task.ddl)} · 更新于 {formatDateLabel(task.updatedAt)}
                          </p>
                          <p className="mt-3 text-[13px] leading-6 text-gray-700">{task.desc}</p>
                        </div>
                      </div>

                      {highlights.length > 0 && (
                        <div className="mt-3 grid gap-2">
                          {highlights.map((line) => (
                            <div key={line} className="rounded-2xl bg-white px-3 py-2 text-[12px] leading-5 text-gray-600">
                              {line}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
