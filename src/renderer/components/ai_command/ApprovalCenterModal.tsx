/**
 * ApprovalCenterModal · AI 同事待审批中心 (顾源源 5/24 真用反馈)
 *
 * 触发: 用户提交 AI 工作指令后, task plan 进 pending_approval, 但 V2.1
 *       此前没任何前端 UI 显示 + 让用户拍板. 本组件填这个缺口.
 *
 * 范围 v0:
 *   - 列所有 active 机器人的 pending task plans (status=pending_approval)
 *   - 每条显示 plan_title / plan_text 摘要 / 机器人 / 创建时间
 *   - 按钮: ✓ 通过 / ✗ 拒绝 / 改后再审 (revise, 简化为弹文本输入)
 *   - 调 decideBotTaskPlan, 刷新列表
 *
 * 不做:
 *   - 不显示 R2/R4-P1 累积的旧 approval_queue 条目 (那是任务发布审批, 跟 AI task plan 不同表)
 *   - 不做完整审批历史 / 撤回 / 多人审批
 */
import React, { useCallback, useEffect, useState } from 'react';
import { X, Loader2, CheckCircle2, XCircle, Edit3, Bot, Clock } from 'lucide-react';
import {
  listBotMembers,
  listBotTaskPlans,
  decideBotTaskPlan,
  type AITaskPlanRecord,
  type BotMemberRecord,
} from '../../lib/api';
import { useBackdropClickClose } from '../../lib/useBackdropClickClose';

type ApprovalCenterModalProps = {
  open: boolean;
  onClose: () => void;
  currentUserId?: string;  // 当前登录用户 id (用于 decided_by + inline auth 校验)
};

type PendingPlanWithBot = AITaskPlanRecord & { _bot: BotMemberRecord };

export function ApprovalCenterModal({ open, onClose, currentUserId }: ApprovalCenterModalProps) {
  const [loading, setLoading] = useState(false);
  const [plans, setPlans] = useState<PendingPlanWithBot[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [deciding, setDeciding] = useState<string | null>(null);
  const [reviseDraft, setReviseDraft] = useState<{ planId: string; text: string } | null>(null);

  const backdropHandlers = useBackdropClickClose(onClose, !loading && deciding === null);

  // 拉所有 active 机器人的 pending task plans
  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const botsResp = await listBotMembers({ status: 'active' });
      const bots = botsResp.items || [];
      const allPending: PendingPlanWithBot[] = [];
      for (const bot of bots) {
        try {
          const data = await listBotTaskPlans(bot.id, { status: 'pending_approval', limit: 50 });
          for (const p of data.items || []) {
            allPending.push({ ...p, _bot: bot });
          }
        } catch {
          // 单个 bot 拉失败不阻塞其他
        }
      }
      // 按 created_at 倒序
      allPending.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
      setPlans(allPending);
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (open) void reload();
    else {
      setPlans([]);
      setError(null);
      setReviseDraft(null);
    }
  }, [open, reload]);

  if (!open) return null;

  const handleDecide = async (plan: PendingPlanWithBot, decision: 'approve' | 'reject' | 'revise', feedback?: string) => {
    setDeciding(plan.id);
    try {
      await decideBotTaskPlan(plan.id, decision, currentUserId || 'user_gu', { feedback });
      // 成功后刷新
      await reload();
      setReviseDraft(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : '审批失败');
    } finally {
      setDeciding(null);
    }
  };

  const formatTime = (iso?: string | null) => {
    if (!iso) return '?';
    try {
      const d = new Date(iso);
      return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
    } catch {
      return iso.slice(0, 16);
    }
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-gray-900/15 backdrop-blur-md transition-opacity"
      {...backdropHandlers}
    >
      <div className="w-[min(720px,92vw)] max-h-[88vh] overflow-y-auto rounded-2xl bg-white shadow-[0_24px_70px_rgba(15,23,42,0.18)] ring-1 ring-inset ring-gray-100">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 px-6 pt-5 pb-3 border-b border-gray-100 sticky top-0 bg-white z-10">
          <div>
            <div className="flex items-center gap-2 text-[9px] font-semibold uppercase tracking-[0.18em] text-gray-400">
              <CheckCircle2 size={11} className="text-[#5B7BFE]" />
              Approval Center
            </div>
            <div className="mt-1 text-[16px] font-light tracking-tight text-gray-900">
              AI 同事待审批
            </div>
            <p className="mt-0.5 text-[11.5px] text-gray-500">
              {loading ? '加载中...' : `${plans.length} 个待你拍板的 AI 工作计划`}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={loading || deciding !== null}
            className="shrink-0 inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-50 transition-colors disabled:opacity-40"
            aria-label="关闭"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-4 space-y-3">
          {loading && (
            <div className="flex flex-col items-center gap-2 py-8">
              <Loader2 size={24} className="text-[#5B7BFE] animate-spin" />
              <span className="text-[12px] text-gray-500">加载待审批 AI 工作计划...</span>
            </div>
          )}

          {error && (
            <div className="px-3 py-2 bg-red-50 border border-red-100 rounded-md text-[12px] text-red-700">
              {error}
            </div>
          )}

          {!loading && !error && plans.length === 0 && (
            <div className="text-center py-8 text-[12.5px] text-gray-400">
              <CheckCircle2 size={32} className="mx-auto mb-2 opacity-40" />
              当前没有 AI 同事待你审批的工作.
            </div>
          )}

          {!loading && plans.map((plan) => (
            <div key={plan.id} className="rounded-lg border border-gray-200 hover:border-[#5B7BFE]/30 transition-colors">
              {/* Header: bot + time + title */}
              <div className="px-4 py-3 border-b border-gray-100">
                <div className="flex items-center gap-2 text-[11px] text-gray-500">
                  <Bot size={12} className="text-[#5B7BFE]" />
                  <span className="font-medium text-gray-700">@{plan._bot.handle}</span>
                  <span>·</span>
                  <span>{plan._bot.department_name || '未分配'}</span>
                  <span className="ml-auto flex items-center gap-1">
                    <Clock size={10} /> {formatTime(plan.created_at)}
                  </span>
                </div>
                <div className="mt-1.5 text-[13.5px] font-medium text-gray-900 leading-snug">
                  {plan.plan_title || '(无标题)'}
                </div>
                {plan.plan_text && plan.plan_text !== plan.plan_title && (
                  <div className="mt-1 text-[11.5px] text-gray-600 leading-relaxed whitespace-pre-wrap line-clamp-4">
                    {plan.plan_text.slice(0, 400)}
                    {plan.plan_text.length > 400 ? '...' : ''}
                  </div>
                )}
              </div>

              {/* meta */}
              <div className="px-4 py-2 flex items-center gap-3 text-[10.5px] text-gray-500 bg-gray-50/50">
                {plan.client_id && (
                  <span>client: <code className="bg-white px-1 rounded">{plan.client_id.slice(0, 16)}</code></span>
                )}
                <span>approval: <span className="text-amber-600 font-medium">{plan.approval_source}</span></span>
                <span className="ml-auto">v{plan.plan_version}</span>
              </div>

              {/* revise 草稿输入框 */}
              {reviseDraft?.planId === plan.id && (
                <div className="px-4 py-2 bg-amber-50/50 border-t border-amber-100">
                  <textarea
                    value={reviseDraft.text}
                    onChange={(e) => setReviseDraft({ planId: plan.id, text: e.target.value })}
                    placeholder="给 AI 同事的修改意见..."
                    className="w-full h-16 px-2.5 py-1.5 text-[11.5px] bg-white border border-amber-200 rounded outline-none focus:border-amber-400 resize-none"
                  />
                  <div className="flex items-center justify-end gap-2 mt-1.5">
                    <button
                      type="button"
                      onClick={() => setReviseDraft(null)}
                      className="text-[10.5px] text-gray-500 hover:text-gray-700"
                    >
                      取消
                    </button>
                    <button
                      type="button"
                      disabled={deciding !== null || !reviseDraft.text.trim()}
                      onClick={() => handleDecide(plan, 'revise', reviseDraft.text)}
                      className="px-2.5 py-1 text-[10.5px] text-white bg-amber-500 hover:bg-amber-600 disabled:opacity-40 rounded transition-colors"
                    >
                      发送修改意见
                    </button>
                  </div>
                </div>
              )}

              {/* actions */}
              {reviseDraft?.planId !== plan.id && (
                <div className="px-4 py-2 flex items-center justify-end gap-2 border-t border-gray-100">
                  <button
                    type="button"
                    disabled={deciding !== null}
                    onClick={() => setReviseDraft({ planId: plan.id, text: '' })}
                    className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] text-gray-600 hover:text-gray-900 hover:bg-gray-50 rounded transition-colors disabled:opacity-40"
                  >
                    <Edit3 size={11} /> 改后再审
                  </button>
                  <button
                    type="button"
                    disabled={deciding !== null}
                    onClick={() => handleDecide(plan, 'reject', '')}
                    className="inline-flex items-center gap-1 px-2.5 py-1 text-[11px] text-red-600 hover:bg-red-50 rounded transition-colors disabled:opacity-40"
                  >
                    <XCircle size={11} /> 拒绝
                  </button>
                  <button
                    type="button"
                    disabled={deciding !== null}
                    onClick={() => handleDecide(plan, 'approve', '')}
                    className="inline-flex items-center gap-1 px-3 py-1 text-[11.5px] font-medium text-white bg-[#5B7BFE] hover:bg-[#4A63CF] rounded transition-colors disabled:opacity-40"
                  >
                    {deciding === plan.id ? <Loader2 size={11} className="animate-spin" /> : <CheckCircle2 size={11} />}
                    通过
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
