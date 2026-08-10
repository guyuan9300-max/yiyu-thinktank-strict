/**
 * 统一待办 — 跨 tasks/action_items/commitments union
 *
 * 设计: 参考周复盘风格 (rounded-3xl + 渐变 header + 单行布局), 无 emoji, 用 icon + chip.
 * → 点 ArrowRight icon 把 todo 字段 (title/owner/dueDate/priority) **映射给原任务编辑器**
 *   (App.tsx 里的 editingTask + isTaskModalOpen + openTaskEditor), 不再有独立 PromoteModal —
 *   保证用户看到的是已经熟悉的"新建任务"界面, 只是预填了字段.
 * ✓/🗑 dismiss: 标记 commitment.status=fulfilled/cancelled, 下次 narrative 不复活.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2, ArrowRight, Trash2, Check } from 'lucide-react';
import {
  getUnifiedTodos,
  dismissUnifiedTodo,
  type UnifiedTodo,
  type UnifiedTodosResponse,
} from '../../lib/api';

interface UnifiedTodoSectionProps {
  clientId: string;
  flash?: (kind: 'success' | 'error', message: string) => void;
  /**
   * 点 → 按钮时调用. 由 App.tsx 接住, 切到 tasks tab + 用 todo 字段
   * 预填原任务编辑器 (editingTask). 不传则按钮无效.
   */
  onPromote?: (todo: UnifiedTodo) => void;
}

const SOURCE_LABEL: Record<UnifiedTodo['source'], { label: string; cls: string }> = {
  task:           { label: '任务',     cls: 'bg-blue-50 text-blue-700' },
  meeting_action: { label: '会议待办', cls: 'bg-purple-50 text-purple-700' },
  commitment:     { label: '承诺',     cls: 'bg-emerald-50 text-emerald-700' },
};

const SEVERITY_LABEL: Record<UnifiedTodo['severity'], { label: string; cls: string }> = {
  high:   { label: '紧急', cls: 'bg-rose-50 text-rose-700' },
  medium: { label: '关注', cls: 'bg-amber-50 text-amber-700' },
  low:    { label: '常规', cls: 'bg-gray-50 text-gray-600' },
};

export function UnifiedTodoSection({ clientId, flash, onPromote }: UnifiedTodoSectionProps) {
  const [data, setData] = useState<UnifiedTodosResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [acting, setActing] = useState<Set<string>>(new Set());
  // mounted guard: 用户点完"完成/取消"立刻切客户或 tab,
  // dismissUnifiedTodo / getUnifiedTodos 的 promise 仍在飞 — resolve 时如果还在 setState,
  // 会触发 React "state update on unmounted component" 警告且写到错的客户上.
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const load = useCallback(() => {
    if (!clientId) return;
    setLoading(true);
    void getUnifiedTodos(clientId)
      .then((d) => {
        if (mountedRef.current) setData(d);
      })
      .catch((err) => {
        if (mountedRef.current) {
          flash?.('error', `待办加载失败: ${err instanceof Error ? err.message : String(err)}`);
        }
      })
      .finally(() => {
        if (mountedRef.current) setLoading(false);
      });
  }, [clientId, flash]);

  useEffect(() => {
    load();
  }, [load]);

  const setActingFor = (id: string, on: boolean) => {
    setActing((s) => {
      const ns = new Set(s);
      if (on) ns.add(id); else ns.delete(id);
      return ns;
    });
  };

  const handlePromote = (todo: UnifiedTodo) => {
    if (todo.source === 'task') {
      flash?.('success', '已经是任务, 可在任务页查看');
      return;
    }
    if (!onPromote) {
      flash?.('error', '当前界面不支持转任务, 请到任务页手动新建');
      return;
    }
    // 把 todo 字段交给 App 层的 onPromote, 由它打开原任务编辑器并预填.
    onPromote(todo);
  };

  const handleDismiss = async (todo: UnifiedTodo, action: 'complete' | 'cancel') => {
    setActingFor(todo.id, true);
    try {
      await dismissUnifiedTodo(clientId, todo.id, action);
      if (!mountedRef.current) return;
      flash?.('success', action === 'complete' ? '已标记完成' : '已从列表移除, 下次不再生成');
      load();
    } catch (err) {
      if (mountedRef.current) {
        flash?.('error', `操作失败: ${err instanceof Error ? err.message : String(err)}`);
      }
    } finally {
      if (mountedRef.current) setActingFor(todo.id, false);
    }
  };

  return (
    <section className="bg-white border border-gray-200 rounded-3xl shadow-sm overflow-hidden">
      <div className="px-5 py-4 border-b border-gray-100 bg-[linear-gradient(135deg,rgba(248,250,252,0.95),rgba(255,255,255,1))]">
        <div className="flex items-baseline justify-between gap-3 flex-wrap">
          <div>
            <h3 className="text-[15px] font-bold text-gray-900">下一步要做什么</h3>
            <p className="mt-0.5 text-[11px] text-gray-500">
              跨任务、会议、承诺 union, 按紧急度排
            </p>
          </div>
          {data && (
            <div className="flex items-center gap-1.5 text-[10px]">
              <span className="rounded-full bg-gray-100 text-gray-600 px-2 py-0.5">
                共 {data.total}
              </span>
              {data.by_severity.high > 0 && (
                <span className="rounded-full bg-rose-50 text-rose-700 px-2 py-0.5 font-bold">
                  紧急 {data.by_severity.high}
                </span>
              )}
            </div>
          )}
        </div>
      </div>

      {loading && (
        <div className="px-5 py-6 text-[12px] text-gray-500 flex items-center gap-2">
          <Loader2 size={13} className="animate-spin" />
          加载中
        </div>
      )}

      {!loading && (!data || data.total === 0) && (
        <div className="px-5 py-6 text-[12px] text-gray-400">当前没有待办</div>
      )}

      {!loading && data && data.total > 0 && (
        <ul className="divide-y divide-gray-100">
          {data.todos.slice(0, 12).map((t) => {
            const src = SOURCE_LABEL[t.source];
            const sev = SEVERITY_LABEL[t.severity];
            const inAction = acting.has(t.id);
            const showDirection = t.direction && t.direction !== '内部';
            const metaParts = [
              t.owner,
              t.due_date,
              showDirection ? t.direction : null,
            ].filter(Boolean);
            return (
              <li
                key={t.id}
                className="px-5 py-3 hover:bg-gray-50/60"
              >
                {/* 顶部一行: 左 = chip+紧急 / 右 = 3 个操作 icon */}
                <div className="flex items-center justify-between gap-2 mb-1.5">
                  <div className="flex items-center gap-1.5">
                    <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold ${src.cls}`}>
                      {src.label}
                    </span>
                    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-bold ${sev.cls}`}>
                      {sev.label}
                    </span>
                  </div>
                  <div className="flex items-center gap-0.5 shrink-0">
                    <button
                      type="button"
                      onClick={() => handleDismiss(t, 'complete')}
                      disabled={inAction}
                      className="w-7 h-7 rounded-full text-gray-400 hover:text-emerald-600 hover:bg-emerald-50 inline-flex items-center justify-center transition disabled:opacity-50"
                      title="标记已完成 (从列表移除, 下次不再生成)"
                    >
                      {inAction ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDismiss(t, 'cancel')}
                      disabled={inAction}
                      className="w-7 h-7 rounded-full text-gray-400 hover:text-rose-600 hover:bg-rose-50 inline-flex items-center justify-center transition disabled:opacity-50"
                      title="删除 (不再追踪, 下次也不会再生成)"
                    >
                      <Trash2 size={13} />
                    </button>
                    <button
                      type="button"
                      onClick={() => handlePromote(t)}
                      disabled={inAction}
                      className="w-7 h-7 rounded-full text-gray-400 hover:text-blue-600 hover:bg-blue-50 inline-flex items-center justify-center transition disabled:opacity-50"
                      title={t.source === 'task' ? '已是任务, 查看详情' : '打开任务编辑器 (已预填标题/负责人/截止日期)'}
                    >
                      <ArrowRight size={13} />
                    </button>
                  </div>
                </div>

                {/* 主体: 标题 (允许 2 行, 占最大空间) */}
                <div
                  className="text-[13px] text-gray-800 leading-relaxed line-clamp-2"
                  title={t.title}
                >
                  {t.title}
                </div>

                {/* 底部: meta (owner · 日期 · 方向) */}
                {metaParts.length > 0 && (
                  <div className="mt-1.5 flex items-center gap-2 text-[11px] text-gray-500 flex-wrap">
                    {metaParts.map((m, i) => (
                      <span key={i} className="inline-flex items-center gap-2">
                        {i > 0 && <span className="text-gray-300">·</span>}
                        <span>{m}</span>
                      </span>
                    ))}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {!loading && data && data.total > 12 && (
        <div className="px-5 py-2 border-t border-gray-100 text-center text-[10px] text-gray-400">
          还有 {data.total - 12} 条 (已按紧急度排序, 显示前 12)
        </div>
      )}
    </section>
  );
}
