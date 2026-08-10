/**
 * AICommandModal · AI 工作指令入口 (顾源源 5/24 §5)
 *
 * 替代 SmartTaskParseModal 入口. 内含 2 mode:
 *   - quick_task: 走原 ai-parse 链路 (保留原能力, 顾源源原则一)
 *   - ai_command: 新链路 (@机器人 → 解析 → 计划 → 创建 AI 任务 → 审批)
 *
 * 路径选择:
 *   - 默认: 自动识别 (parseSmartCommand)
 *   - 用户可手动切换
 *
 * 安全边界 (顾源源 §12):
 *   - 不直接写 db
 *   - 不绕过 approval_queue
 *   - 不让机器人自审批
 *   - 全程带 actor_id 留痕
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Sparkles, X, Loader2, Bot, AlertCircle, CheckCircle2, ChevronRight } from 'lucide-react';

import {
  aiParseTask,
  resolveBotByHandle,
  getBotPermissions,
  createBotTaskPlan,
  listBotMembers,
  listBotTaskPlans,
  getBotTaskPlanProgress,
  aiCommandParseSteps,
  type TaskAiParseResult,
  type BotPermissionsResponse,
  type BotMemberRecord,
  type PlanProgressRecord,
} from '../../lib/api';

// A 写的 resolveBotByHandle 返回类型 (inline, 避免 import 错)
type BotResolveResult = Awaited<ReturnType<typeof resolveBotByHandle>>;
import { useBackdropClickClose } from '../../lib/useBackdropClickClose';
import {
  parseSmartCommand,
  recommendModulesForIntent,
  MODULE_CAPABILITY_MANIFEST_V1,
  type AICommandMode,
  type ParsedSmartCommand,
} from '../../lib/aiCommand';

type AICommandModalProps = {
  open: boolean;
  onClose: () => void;
  onQuickTaskParsed: (result: TaskAiParseResult, originalText: string) => void;
  /** 已知客户名列表 (用于 parseSmartCommand 命中匹配). */
  knownClientNames?: string[];
  /** 上下文明确提供的客户 id（例如从项目详情触发）。 */
  defaultClientId?: string;
  /** 供用户用稳定 id 明确选择的客户列表；名称只用于显示。 */
  clientsForResolve?: Array<{ id: string; name: string }>;
  /** 当前登录用户 id, 用于 inline_authorization 时作为 human_initiator (审批人本人). */
  currentUserId?: string;
  /**
   * 顾源源 5/25 真用反馈: plan inline approved 进 submitted 时, 立刻把 planId 抬到 App level,
   * 由 sidebar 系统状态那块的 PlanProgressMini 接管轮询, modal 自动关闭, 用户能继续做别的事.
   */
  onPlanStarted?: (info: { planId: string; botName: string }) => void;
};

type Stage = 'input' | 'parsing' | 'bot_resolved' | 'plan_preview' | 'submitting' | 'submitted' | 'error';

export function AICommandModal({
  open,
  onClose,
  onQuickTaskParsed,
  knownClientNames = [],
  defaultClientId,
  clientsForResolve = [],
  currentUserId,
  onPlanStarted,
}: AICommandModalProps) {
  const [text, setText] = useState('');
  const [stage, setStage] = useState<Stage>('input');
  const [mode, setMode] = useState<AICommandMode>('ai_command');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [parsed, setParsed] = useState<ParsedSmartCommand | null>(null);
  const [bot, setBot] = useState<BotResolveResult | null>(null);
  const [permissions, setPermissions] = useState<BotPermissionsResponse | null>(null);
  const [submitResult, setSubmitResult] = useState<{ task_id: string; ai_task_plan_id: string; status: string } | null>(null);
  // 顾源源 5/25 安全边界: 每个任务以客户隔离, 必须强制有客户.
  // 自动识别 → 可改 → 没识别就强制选 → 没选不让提交.
  const [chosenClientId, setChosenClientId] = useState<string | null>(null);
  const [availableBots, setAvailableBots] = useState<BotMemberRecord[]>([]);
  // M10 (A, 2026-05-25) · plan 执行进度轮询 (2s)
  const [planProgress, setPlanProgress] = useState<PlanProgressRecord | null>(null);
  const [progressError, setProgressError] = useState<string | null>(null);
  const progressTimerRef = useRef<number | null>(null);
  // ── M7: inline @mention picker 状态 (顾源源 5/25: 微信群那种弹窗) ──
  // mentionState: 仅当 textarea 当前 cursor 紧跟在 @<query> 后才 open.
  //   - atPos: '@' 在 text 里的下标
  //   - query: '@' 后到 cursor 之间的字符串 (filter 关键字)
  const [mentionState, setMentionState] = useState<{ open: boolean; query: string; atPos: number } | null>(null);
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  // 中文 IME composition 中不弹 picker, end 后再检测一次
  const isComposingRef = useRef(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // ★ open 时拉 active 机器人列表 (顾源源 5/24: @ 下拉支持)
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void listBotMembers({ status: 'active' }).then((data) => {
      if (cancelled) return;
      setAvailableBots(data.items || []);
    }).catch(() => {
      // 拉失败不阻塞 modal, 用户仍能手动输 @庆华
    });
    return () => { cancelled = true; };
  }, [open]);

  useEffect(() => {
    if (open) {
      setErrorMessage(null);
      window.setTimeout(() => textareaRef.current?.focus(), 80);
    } else {
      // 关闭时重置全部状态
      setText('');
      setStage('input');
      setMode('ai_command');
      setErrorMessage(null);
      setParsed(null);
      setBot(null);
      setPermissions(null);
      setSubmitResult(null);
      setChosenClientId(null);
      setPlanProgress(null);
      setProgressError(null);
    }
  }, [open]);

  // M10 · 进度轮询: submitted + approved 时, 每 2s GET 一次, 终态停止
  useEffect(() => {
    const planId = submitResult?.ai_task_plan_id;
    const isApproved = submitResult?.status === 'approved';
    if (stage !== 'submitted' || !planId || !isApproved) {
      return;
    }
    let cancelled = false;
    setPlanProgress(null);
    setProgressError(null);
    const tick = async () => {
      if (cancelled) return;
      try {
        const result = await getBotTaskPlanProgress(planId);
        if (cancelled) return;
        setPlanProgress(result);
        // 终态停轮询
        if (
          result.execution_status === 'success' ||
          result.execution_status === 'failed' ||
          result.execution_status === 'partial'
        ) {
          if (progressTimerRef.current !== null) {
            window.clearInterval(progressTimerRef.current);
            progressTimerRef.current = null;
          }
        }
      } catch (err: unknown) {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : '进度查询失败';
        setProgressError(msg);
      }
    };
    void tick(); // 立即拉一次, 不等 2s
    progressTimerRef.current = window.setInterval(() => {
      void tick();
    }, 2000);
    return () => {
      cancelled = true;
      if (progressTimerRef.current !== null) {
        window.clearInterval(progressTimerRef.current);
        progressTimerRef.current = null;
      }
    };
  }, [stage, submitResult?.ai_task_plan_id, submitResult?.status]);

  useEffect(() => {
    if (!open) return;
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && stage !== 'parsing' && stage !== 'submitting') {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [open, onClose, stage]);

  const backdropHandlers = useBackdropClickClose(onClose, stage === 'input');

  // ★ useMemo 必须在 if (!open) return null 之前 — React Hooks Rule:
  // 所有 hooks 每次 render 必须按相同顺序调用. 早期 return 后的 hook
  // 会导致 "Rendered more hooks than during the previous render".
  const recommendedModules = useMemo(() => {
    if (!parsed) return [];
    return recommendModulesForIntent(parsed.intent).map((k) =>
      MODULE_CAPABILITY_MANIFEST_V1.find((m) => m.moduleKey === k),
    ).filter(Boolean);
  }, [parsed]);

  // 项目归属只能来自调用方明确传入的稳定 client_id 或用户在下拉框中的明确选择。
  // parsed.client_name 只是自然语言提示，绝不能反查并写入业务归属。
  useEffect(() => {
    if (!parsed) return;
    setChosenClientId(defaultClientId || null);
  }, [parsed, defaultClientId]);

  if (!open) return null;

  // ── 选 AI 同事: 自动 prepend "@<handle> " 到 textarea start (顾源源 5/24, 旧入口) ──
  // 注: M7 把 UI 入口换成 inline @mention picker 后, picker 走 insertMentionFromPicker.
  // 本方法保留, 供未来其它代码路径 (如 quick fill) 调用, 不在 render 树中触发.
  const handleSelectBot = (bot: BotMemberRecord) => {
    const mention = `@${bot.handle} `;
    setText((prev) => {
      // 如果开头已经是 @xxx (任何 @), 替换它; 否则 prepend
      const reMention = /^@[^\s]+\s*/;
      if (reMention.test(prev)) {
        return prev.replace(reMention, mention);
      }
      return mention + prev;
    });
    setMode('ai_command');
    // focus 回输入框, 光标到末尾
    window.setTimeout(() => {
      const ta = textareaRef.current;
      if (ta) {
        ta.focus();
        ta.selectionStart = ta.selectionEnd = ta.value.length;
      }
    }, 30);
  };

  // ── M7: inline mention 检测 (顾源源 5/25 微信群弹窗体验) ──
  // 从 cursor 往回找最近的 '@'; 若 '@' 后到 cursor 之间是合法 mention 字符 (中文/英文/数字/下划线, 无空白),
  // 且 '@' 前是 空白/换行/字符串开头 → 触发 picker.
  const MENTION_TOKEN_RE = /^[一-龥A-Za-z0-9_]*$/;

  const detectMention = (value: string, cursor: number) => {
    if (isComposingRef.current) return; // IME 中不弹
    // 从 cursor-1 往回扫
    let atPos = -1;
    for (let i = cursor - 1; i >= 0; i--) {
      const ch = value[i];
      if (ch === '@') {
        atPos = i;
        break;
      }
      // 命中空白/换行 → 视为离开 mention 范围
      if (ch === ' ' || ch === '\n' || ch === '\t' || ch === '\r') {
        break;
      }
    }
    if (atPos < 0) {
      setMentionState(null);
      return;
    }
    // '@' 前必须是 空白/换行/字符串开头
    const prevCh = atPos === 0 ? '' : value[atPos - 1];
    if (prevCh && prevCh !== ' ' && prevCh !== '\n' && prevCh !== '\t' && prevCh !== '\r') {
      setMentionState(null);
      return;
    }
    const query = value.slice(atPos + 1, cursor);
    if (!MENTION_TOKEN_RE.test(query)) {
      setMentionState(null);
      return;
    }
    setMentionState({ open: true, query, atPos });
    setHighlightedIndex(0);
  };

  const handleTextareaChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newText = e.target.value;
    const cursor = e.target.selectionStart || 0;
    setText(newText);
    detectMention(newText, cursor);
  };

  // 给 picker 用的过滤后列表 (大小写不敏感; 中文直接 includes)
  const filteredBots: BotMemberRecord[] = (() => {
    if (!mentionState?.open) return [];
    const q = mentionState.query.toLowerCase();
    if (!q) return availableBots;
    return availableBots.filter((b) => {
      const handle = (b.handle || '').toLowerCase();
      const name = (b.display_name || '').toLowerCase();
      return handle.includes(q) || name.includes(q) || b.display_name.includes(mentionState.query);
    });
  })();

  const insertMentionFromPicker = (bot: BotMemberRecord) => {
    if (!mentionState) return;
    const ta = textareaRef.current;
    if (!ta) return;
    const cursor = ta.selectionStart || 0;
    const before = text.slice(0, mentionState.atPos);
    const after = text.slice(cursor);
    const insert = `@${bot.handle} `;
    const newText = before + insert + after;
    setText(newText);
    setMentionState(null);
    setMode('ai_command');
    const newCursor = mentionState.atPos + insert.length;
    window.setTimeout(() => {
      const t = textareaRef.current;
      if (t) {
        t.focus();
        t.setSelectionRange(newCursor, newCursor);
      }
    }, 0);
  };

  const handleTextareaKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // picker 打开时拦截方向键 / Enter / Tab / Esc
    if (mentionState?.open && filteredBots.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setHighlightedIndex((i) => (i + 1) % filteredBots.length);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setHighlightedIndex((i) => (i - 1 + filteredBots.length) % filteredBots.length);
        return;
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        const target = filteredBots[highlightedIndex] || filteredBots[0];
        if (target) insertMentionFromPicker(target);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setMentionState(null);
        return;
      }
    } else if (mentionState?.open && filteredBots.length === 0 && e.key === 'Escape') {
      e.preventDefault();
      setMentionState(null);
      return;
    }
    // 默认: Cmd/Ctrl+Enter 提交
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      void handleParse();
    }
  };

  // ── 主动作 1: 解析 + 走对应链路 ──────────────

  const handleParse = async () => {
    const trimmed = text.trim();
    if (!trimmed) {
      setErrorMessage('请先输入文字');
      return;
    }
    setErrorMessage(null);
    const parsed = parseSmartCommand(trimmed, knownClientNames);
    setParsed(parsed);

    // 顾源源 5/25 真用 bug: toggle=quick_task 但用户输入带 @机器人时, 强制 quick_task,
    // 导致 5 份文档复杂指令被当成 quick_task → 打开普通新建任务弹窗. 反直觉.
    // 修法: 输入带 @机器人 = 用户意图明确, 任何 toggle 都强制 ai_command.
    // 只有"完全没 @ 谁" 时, toggle 才生效.
    const finalMode: AICommandMode =
      parsed.bot_handle
        ? 'ai_command'
        : mode === 'quick_task'
          ? 'quick_task'
          : parsed.mode;
    // toggle 跟实际路由不一致时, 同步回 UI (让用户知道走了哪条)
    if (finalMode !== mode) setMode(finalMode);

    if (finalMode === 'quick_task') {
      // Quick Task 链路: 直接调原 ai-parse, 跟旧 SmartTaskParseModal 一致
      setStage('parsing');
      try {
        const today = new Date();
        const currentDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
        const result = await aiParseTask({ text: trimmed, currentDate });
        onQuickTaskParsed(result, trimmed);
      } catch (e) {
        const detail = e instanceof Error ? e.message : 'AI 解析失败';
        setErrorMessage(detail);
        setStage('input');
      }
      return;
    }

    // AI Command 链路: 必须有 @机器人
    if (!parsed.bot_handle) {
      setErrorMessage('AI 工作指令需要 @机器人同事. 例如: @庆华 帮我...');
      setStage('input');
      return;
    }

    setStage('parsing');
    try {
      // 顾源源 5/25 PM 真用反馈: regex 解析跟不上自然口语, 必须 LLM 真解析.
      // 并行调 3 个: resolveBotByHandle / getBotPermissions / aiCommandParseSteps (本地 qwen2.5:7b).
      // LLM 解析 ~5s, bot resolve <1s, 总耗时 ≈ 5s (并行).
      const [botData, llmStepsResult] = await Promise.all([
        resolveBotByHandle(parsed.bot_handle),
        aiCommandParseSteps(trimmed).catch((err) => {
          // LLM 解析失败不阻塞 — 退回 regex steps (parsed.steps 已经有)
          // eslint-disable-next-line no-console
          console.warn('aiCommandParseSteps failed, fallback to regex steps:', err);
          return { steps: [] as ParsedSmartCommand['steps'], fallback_reason: 'request_failed' };
        }),
      ]);
      setBot(botData);
      const permData = await getBotPermissions(botData.bot_member_id);
      setPermissions(permData);

      // LLM 真解析的 steps 覆盖 regex 拆的 (LLM 准得多). 失败时保留 regex steps.
      if (llmStepsResult.steps && llmStepsResult.steps.length > 0) {
        const llmSteps = llmStepsResult.steps.map((s) => ({
          index: s.index,
          raw_text: '',  // LLM 路径不保留原片段, 后续如要原文可加
          action: s.action || '',
          basis: s.basis || '',
          deliverable: s.deliverable || '',
        }));
        setParsed({ ...parsed, steps: llmSteps });
      }
      setStage('bot_resolved');
    } catch (e) {
      const detail = e instanceof Error ? e.message : '解析失败';
      setErrorMessage(detail.includes('404') ? `没找到 "@${parsed.bot_handle}" 这个 AI 同事. 请先在组织搭建中心添加.` : detail);
      setStage('error');
    }
  };

  // ── 主动作 2: 进入计划预览 ──────────────

  const handleEnterPlan = () => {
    setStage('plan_preview');
  };

  // ── 主动作 3: 提交 AI 任务计划 ──────────────

  const handleSubmitPlan = async () => {
    if (!bot || !parsed) return;

    // 顾源源 5/25 P0-6 真用 bug: 同一指令跑了 3 轮 = 15 份文档. 提交前查 in-flight,
    // 防重复触发. backend approved + running 状态的 plan 也会被检测.
    try {
      const inflightCheck = await listBotTaskPlans(bot.bot_member_id, { status: 'approved', limit: 5 });
      const running = (inflightCheck.items || []).filter((p) => {
        const exec = (p as { execution_status?: string }).execution_status;
        return exec === 'running' || exec === 'pending_execute' || exec === 'not_started';
      });
      if (running.length > 0) {
        const r = running[0];
        const confirmMsg =
          `${bot.display_name} 已经有 ${running.length} 个任务正在执行 (最新: "${r.plan_title?.slice(0, 30) || r.id}").\n\n` +
          `如果你确认要再触发一次新计划, 会同时产生新文档/任务. 通常应该等当前任务跑完再说.\n\n` +
          `继续提交吗?`;
        if (typeof window !== 'undefined' && !window.confirm(confirmMsg)) {
          setErrorMessage('已取消 — 等当前任务跑完再发 (sidebar 左下角进度条可以看到状态)');
          return;
        }
      }
    } catch {
      // 查 in-flight 失败不阻塞 — 让用户继续提交 (避免误伤)
    }

    setStage('submitting');
    try {
      // 推荐模块 (基于 intent)
      const recommendedModules = recommendModulesForIntent(parsed.intent);
      const moduleNames = recommendedModules
        .map((k) => MODULE_CAPABILITY_MANIFEST_V1.find((m) => m.moduleKey === k)?.moduleName)
        .filter(Boolean) as string[];

      // 任务标题: 取原文前 30 字
      const planTitle = text.length > 30 ? `${text.slice(0, 30)}...` : text;

      // 顾源源 5/24 真用反馈洞察: "确认这个流程 = 审批本身"
      // 用户点"我审批通过"那一刻, 就是他作为审批人在 inline auth.
      // 所以总是传 inline_authorization=true + human_initiator_id (= 当前 user).
      // backend 会校验 user 是否是 bot 审批人, 通过则直接 approved, 不通过仍 pending.
      // 顾源源 5/25 安全边界: 每个任务以客户隔离, 没客户禁止提交 (防跨客户污染).
      // chosenClientId 只来自明确上下文或用户选择，不能由名称猜测。
      const resolvedClientId = chosenClientId || defaultClientId || null;
      if (!resolvedClientId) {
        setErrorMessage('必须选关联客户 — 任务以客户隔离, 不选会导致跨客户污染. 在下方"关联客户"下拉选一个.');
        setStage('plan_preview');
        return;
      }

      // 顾源源 5/25 真用 bug fix: plan_executor 拿 steps:[] → total=0 → failed.
      // 把 parsed.steps (前端三段式) 转成 backend steps_json 格式, A 的 _step_to_subtask
      // 会从 module/action 推 tool (documents.generate / tasks.create / smart_import).
      // A 的推法用英文关键词 (document/draft/generate/task/import) — 中文 action 不命中,
      // 所以这里前端先把 module 推成英文关键词.
      const backendSteps = parsed.steps.map((s) => {
        const a = s.action || '';
        let module = 'noop';
        // 写一份/拟一份/起草/草案/档案/报告/分析/提案/协议 → documents.generate
        if (/(?:写一份|拟一份|起草|草案|档案|报告|分析|提案|协议|介绍|背景|画像|盘点)/.test(a)) {
          module = 'documents.generate';
        }
        // 建一个任务/会议/事项/提醒/日程 → tasks.create
        else if (/(?:建一个|给我建|建立?)(?:任务|会议|事项|提醒|日程)/.test(a)) {
          module = 'tasks.create';
        }
        // 导入/接入/同步资料 → smart_import
        else if (/(?:导入|接入|同步|抓取).{0,10}(?:资料|文件|材料)/.test(a)) {
          module = 'smart_import';
        }
        return {
          module,
          action: s.action,
          expected_result: s.deliverable || s.raw_text.slice(0, 100),
        };
      });

      const result = await createBotTaskPlan(bot.bot_member_id, {
        plan_title: planTitle,
        plan_text: text,
        client_id: resolvedClientId,
        required_modules: moduleNames,
        steps: backendSteps,
        expected_outputs: parsed.requested_outputs,
        approval_required: true,
        inline_authorization: true,
        inline_authorization_text:
          parsed.inline_authorization_text || `${bot.display_name} 由我本人 (审批人) 当场确认执行`,
        human_initiator_id: currentUserId || 'user_guyuan',
      });
      const submitStatus = result.approval_status || result.status || 'pending_approval';
      setSubmitResult({
        task_id: result.task_id || '',
        ai_task_plan_id: result.ai_task_plan_id,
        status: submitStatus,
      });
      setStage('submitted');

      // 顾源源 5/25 真用反馈: approved 路径下, 立刻把 planId 抬到 App,
      // sidebar 的 PlanProgressMini 接管轮询. modal 自动 close, 用户能继续做事.
      // pending 路径 (你不在审批人列表) modal 留着让你看, 不自动关.
      if (submitStatus === 'approved' && onPlanStarted) {
        onPlanStarted({
          planId: result.ai_task_plan_id,
          botName: bot.display_name,
        });
        // 给个 600ms 让用户看到"已确认" 视觉反馈再关
        window.setTimeout(() => onClose(), 600);
      }
    } catch (e) {
      const detail = e instanceof Error ? e.message : '提交失败';
      setErrorMessage(detail);
      setStage('error');
    }
  };

  // ── 主动作 4: 切回 Quick Task ──────────────

  const handleSwitchToQuickTask = async () => {
    setMode('quick_task');
    setStage('input');
    setErrorMessage(null);
    setParsed(null);
    setBot(null);
    setPermissions(null);
  };

  // ── 渲染 ──────────────
  // (recommendedModules useMemo 已上移到 if (!open) 之前, 避免 Hooks Rule 违反)

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-gray-900/15 backdrop-blur-md transition-opacity"
      {...backdropHandlers}
    >
      <div className="w-[min(720px,92vw)] max-h-[88vh] overflow-y-auto rounded-2xl bg-white shadow-[0_24px_70px_rgba(15,23,42,0.18)] ring-1 ring-inset ring-gray-100">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 px-6 pt-5 pb-3 border-b border-gray-100">
          <div>
            <div className="flex items-center gap-2 text-[9px] font-semibold uppercase tracking-[0.18em] text-gray-400">
              <Sparkles size={11} className="text-[#5B7BFE]" strokeWidth={2.2} />
              AI Command Center
            </div>
            <div className="mt-1 text-[16px] font-light tracking-tight text-gray-900">
              AI 工作指令
            </div>
            <p className="mt-0.5 text-[11.5px] text-gray-500">
              可以快速建任务, 也可以 @AI 同事让它帮你推进复杂工作.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={stage === 'parsing' || stage === 'submitting'}
            className="shrink-0 inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-50 transition-colors disabled:opacity-40"
            aria-label="关闭"
          >
            <X size={16} />
          </button>
        </div>

        {/* Stage: input — 用户输入 */}
        {stage === 'input' && (
          <div className="px-6 py-4 space-y-3">
            {/* mode toggle */}
            <div className="flex items-center gap-2 text-[11px]">
              <span className="text-gray-400">模式:</span>
              <button
                type="button"
                onClick={() => setMode('ai_command')}
                className={`px-2.5 py-1 rounded-md transition-colors ${
                  mode === 'ai_command'
                    ? 'bg-[#5B7BFE] text-white'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                <Bot size={11} className="inline mr-1" /> AI 同事推进
              </button>
              <button
                type="button"
                onClick={() => setMode('quick_task')}
                className={`px-2.5 py-1 rounded-md transition-colors ${
                  mode === 'quick_task'
                    ? 'bg-[#5B7BFE] text-white'
                    : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                快速建任务
              </button>
              <span className="ml-auto text-[10px] text-gray-400">
                {mode === 'ai_command' ? '复杂任务 → AI 拆解+审批' : '一句话 → 直接建任务'}
              </span>
            </div>

            {/* M7: textarea + inline @mention picker — 顾源源 5/25 微信群体验 */}
            <div className="relative">
              <textarea
                ref={textareaRef}
                value={text}
                onChange={handleTextareaChange}
                onKeyDown={handleTextareaKeyDown}
                onCompositionStart={() => { isComposingRef.current = true; }}
                onCompositionEnd={(e) => {
                  isComposingRef.current = false;
                  // composition 结束后再跑一次检测 (例如刚输完 "@庆华")
                  detectMention(e.currentTarget.value, e.currentTarget.selectionStart || 0);
                }}
                placeholder={
                  mode === 'ai_command'
                    ? '例如: @庆华 帮我为安然集团生成一份集团介绍, 并申请放入客户工作台.\n如果我说不用审批, 直接执行第一步, 就按我的授权先开始.'
                    : '例如：明天下午三点提醒我联系某合作机构发补充协议。'
                }
                className="w-full h-32 px-3 py-2.5 text-[13px] text-gray-900 placeholder:text-gray-400 bg-gray-50 rounded-lg border border-gray-200 focus:bg-white focus:border-[#5B7BFE] focus:ring-2 focus:ring-[#5B7BFE]/15 outline-none resize-none"
              />
              {mode === 'ai_command' && mentionState?.open && (
                <MentionPicker
                  bots={availableBots}
                  query={mentionState.query}
                  highlightedIndex={highlightedIndex}
                  onHover={setHighlightedIndex}
                  onSelect={insertMentionFromPicker}
                />
              )}
            </div>

            {errorMessage && (
              <div className="flex items-start gap-2 px-3 py-2 bg-red-50 border border-red-100 rounded-md text-[12px] text-red-700">
                <AlertCircle size={13} className="mt-0.5 shrink-0" />
                <span>{errorMessage}</span>
              </div>
            )}

            <div className="flex items-center justify-between pt-1">
              <span className="text-[10px] text-gray-400">Cmd/Ctrl + Enter 提交</span>
              <button
                type="button"
                onClick={handleParse}
                disabled={!text.trim()}
                className="inline-flex items-center gap-1.5 px-4 py-1.5 text-[12.5px] font-medium text-white bg-[#5B7BFE] hover:bg-[#4A63CF] disabled:opacity-40 disabled:cursor-not-allowed rounded-lg transition-colors"
              >
                <Sparkles size={13} strokeWidth={2.2} />
                {mode === 'ai_command' ? '让 AI 同事来推进' : '解析任务字段'}
              </button>
            </div>
          </div>
        )}

        {/* Stage: parsing — LLM 真解析中 (顾源源 5/25 PM: 本地 qwen2.5:7b ~5s) */}
        {stage === 'parsing' && (
          <div className="px-6 py-12 flex flex-col items-center gap-3">
            <Loader2 size={28} className="text-[#5B7BFE] animate-spin" />
            <div className="text-[13px] text-gray-700 font-medium">
              {parsed?.bot_handle
                ? `${parsed.bot_handle} 正在理解你的指令...`
                : '正在解析指令...'}
            </div>
            <div className="text-[11px] text-gray-400">
              本地模型 (qwen2.5:7b) 真解析, 4-6 秒
              {parsed?.bot_handle && ` · @${parsed.bot_handle}`}
            </div>
          </div>
        )}

        {/* Stage: bot_resolved — 顾源源 5/25 真用反馈重排:
              · 庆华信息卡 → 右上角小卡 (collapsed)
              · 主区显示 step list, 每步三段式 (做什么/基于什么/交付什么)
              · 用户一眼看出 AI 是否理解任务, 而不是再读一遍自己的原话 */}
        {stage === 'bot_resolved' && bot && parsed && (
          <div className="px-6 py-4 space-y-3">
            {/* Header row: 左 "我理解的任务" 标题 + 右上角庆华小卡 */}
            <div className="flex items-start justify-between gap-3">
              <div className="flex-1 min-w-0">
                <div className="text-[13px] font-medium text-gray-900">我理解的任务</div>
                <div className="mt-0.5 text-[10.5px] text-gray-500">
                  {parsed.steps.length > 0
                    ? `共 ${parsed.steps.length} 步, 请逐步核对庆华是否理解准确`
                    : '没识别出明确步骤, 退回显示原文 (庆华可能误解任务结构)'}
                </div>
              </div>
              <div className="shrink-0 rounded-lg border border-[#5B7BFE]/30 bg-gradient-to-br from-[#5B7BFE]/5 to-transparent px-3 py-2 text-right max-w-[200px]">
                <div className="flex items-center justify-end gap-1.5">
                  <Bot size={12} className="text-[#5B7BFE]" />
                  <span className="text-[12px] font-medium text-gray-900">{bot.display_name}</span>
                </div>
                <div className="mt-0.5 text-[10px] text-gray-600">
                  AI 同事 · {bot.department_name || '未分配'}
                </div>
                <div className="mt-0.5 text-[9.5px] text-gray-500">
                  汇报: {bot.reporting_approvers.map((a) => a.role).join(' / ') || '无'}
                </div>
                <div className="mt-1 text-[9px] text-gray-400 truncate">
                  {bot.enabled_capabilities.length} 项能力
                </div>
              </div>
            </div>

            {/* Step list — 三段式 */}
            {parsed.steps.length > 0 ? (
              <div className="space-y-2">
                {parsed.steps.map((step) => (
                  <div
                    key={step.index}
                    className="rounded-lg border border-gray-200 hover:border-[#5B7BFE]/40 transition-colors overflow-hidden"
                  >
                    {/* Step header */}
                    <div className="px-3 py-1.5 bg-gray-50 border-b border-gray-100 flex items-center gap-2">
                      <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-[#5B7BFE] text-white text-[10.5px] font-medium">
                        {step.index}
                      </span>
                      <span className="text-[10.5px] text-gray-500 uppercase tracking-wider">Step</span>
                    </div>
                    {/* 三段式 */}
                    <div className="px-3 py-2 space-y-1.5">
                      <div className="flex gap-2">
                        <span className="shrink-0 inline-flex items-center justify-center w-14 text-[10px] font-medium text-emerald-700 bg-emerald-50 rounded px-1.5 py-0.5">
                          做什么
                        </span>
                        <span className="text-[12px] text-gray-800 leading-relaxed">
                          {step.action || <span className="text-gray-400 italic">(未识别 — 庆华可能误解)</span>}
                        </span>
                      </div>
                      <div className="flex gap-2">
                        <span className="shrink-0 inline-flex items-center justify-center w-14 text-[10px] font-medium text-indigo-700 bg-indigo-50 rounded px-1.5 py-0.5">
                          基于
                        </span>
                        <span className="text-[11.5px] text-gray-700 leading-relaxed">
                          {step.basis || <span className="text-gray-400 italic">(未识别 — 没有显式输入/参考)</span>}
                        </span>
                      </div>
                      <div className="flex gap-2">
                        <span className="shrink-0 inline-flex items-center justify-center w-14 text-[10px] font-medium text-amber-700 bg-amber-50 rounded px-1.5 py-0.5">
                          交付
                        </span>
                        <span className="text-[11.5px] text-gray-700 leading-relaxed">
                          {step.deliverable || <span className="text-gray-400 italic">(未识别 — 没说篇幅/落点)</span>}
                        </span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="rounded-md bg-gray-50 px-3 py-2 text-[11.5px] text-gray-600 leading-relaxed">
                {parsed.original_text.slice(0, 280)}
                {parsed.original_text.length > 280 ? '...' : ''}
              </div>
            )}

            {/* meta row: intent + 参考客户 + inline auth */}
            <div className="flex items-center gap-3 text-[10.5px] text-gray-500 pt-1 flex-wrap">
              <span>
                Intent: <code className="bg-gray-100 px-1 rounded text-[10px]">{parsed.intent}</code>
              </span>
              {parsed.client_references.length > 0 && (
                <span title="这些客户出现在你指令里, 但被识别为参考样本 (合同/方法论来源), 不是主客户">
                  参考样本: <span className="text-gray-600">{parsed.client_references.join(', ')}</span>
                </span>
              )}
              {parsed.inline_authorization_detected && (
                <span className="text-amber-600">★ 指令内授权: "{parsed.inline_authorization_text}"</span>
              )}
            </div>

            {/* 顾源源 5/25 安全边界: 关联客户必选 (任务以客户隔离, 没客户=跨污染). */}
            <div className="rounded-lg border border-amber-200 bg-amber-50/40 px-3 py-2.5">
              <div className="flex items-center justify-between gap-2 mb-1.5">
                <div className="flex items-center gap-1.5">
                  <span className="text-[11.5px] font-medium text-gray-800">关联客户</span>
                  <span className="text-[9.5px] text-red-600 font-medium">* 必选</span>
                </div>
                <span className="text-[9.5px] text-gray-500">
                  {parsed.client_name
                    ? `指令提到：${parsed.client_name}（请核对后选择）`
                    : '未带明确项目上下文，必须手动选'}
                </span>
              </div>
              <select
                value={chosenClientId || ''}
                onChange={(e) => setChosenClientId(e.target.value || null)}
                className="w-full px-2.5 py-1.5 text-[12px] text-gray-900 bg-white border border-gray-300 rounded-md outline-none focus:border-[#5B7BFE] focus:ring-2 focus:ring-[#5B7BFE]/15"
              >
                <option value="">— 选客户 (任务将归属到这个客户的工作台) —</option>
                {clientsForResolve.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
              <div className="mt-1 text-[10px] text-gray-600 leading-snug">
                每个任务以客户隔离：文档写入这个客户的工作台，跨客户引用需要单独授权。
              </div>
            </div>

            {errorMessage && (
              <div className="flex items-start gap-2 px-3 py-2 bg-red-50 border border-red-100 rounded-md text-[12px] text-red-700">
                <AlertCircle size={13} className="mt-0.5 shrink-0" />
                <span>{errorMessage}</span>
              </div>
            )}

            <div className="flex items-center justify-between pt-1 gap-2">
              <button type="button" onClick={handleSwitchToQuickTask} className="text-[11px] text-gray-500 hover:text-gray-700">
                作为普通任务创建
              </button>
              <div className="flex items-center gap-3">
                <button
                  type="button"
                  onClick={handleEnterPlan}
                  className="text-[10.5px] text-gray-500 hover:text-gray-700 underline-offset-2 hover:underline"
                  title="查看推荐模块和能力边界 (可选)"
                >
                  查看模块/能力详情
                </button>
                {/* 顾源源 5/25 真用反馈: 这页已经清楚, 直接确认执行, 不再多一步 plan_preview */}
                <button
                  type="button"
                  onClick={handleSubmitPlan}
                  className="inline-flex items-center gap-1.5 px-4 py-1.5 text-[12.5px] font-medium text-white bg-[#5B7BFE] hover:bg-[#4A63CF] rounded-lg transition-colors"
                  title="点击 = 我作为审批人当场确认这个流程, 庆华立刻开始执行"
                >
                  <CheckCircle2 size={13} /> 确认执行 — 我审批通过
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Stage: plan_preview — 执行计划卡 */}
        {stage === 'plan_preview' && bot && parsed && (
          <div className="px-6 py-4 space-y-3">
            <div className="text-[14px] font-medium text-gray-900">{bot.display_name} 的执行计划</div>

            {/* 模块调用计划 */}
            <div>
              <div className="text-[11px] font-medium text-gray-700 mb-1.5">需要调用的模块</div>
              <div className="space-y-1.5">
                {recommendedModules.map((m) => m && (
                  <div key={m.moduleKey} className="flex items-center gap-2 px-2.5 py-1.5 bg-gray-50 rounded-md text-[11.5px]">
                    <span className={`w-1.5 h-1.5 rounded-full ${m.enabled ? 'bg-emerald-500' : 'bg-gray-300'}`} />
                    <span className="font-medium text-gray-800">{m.moduleName}</span>
                    <span className="text-gray-500">— {m.description.slice(0, 35)}...</span>
                    {!m.enabled && <span className="ml-auto text-[10px] text-amber-600">未启用</span>}
                  </div>
                ))}
              </div>
            </div>

            {/* 预期产出 */}
            {parsed.requested_outputs.length > 0 && (
              <div>
                <div className="text-[11px] font-medium text-gray-700 mb-1.5">预期产出</div>
                <ul className="space-y-0.5 text-[11.5px] text-gray-600">
                  {parsed.requested_outputs.map((o, i) => (
                    <li key={i} className="flex items-center gap-1.5">
                      <CheckCircle2 size={11} className="text-emerald-500" /> {o}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* 当前能力/限制 — A 5/25 完 M9 plan_executor 后真实状态 (不再硬编码 blocked_by_A). */}
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-md bg-emerald-50 border border-emerald-100 px-3 py-2 text-[11px] text-emerald-800">
                <div className="font-medium mb-0.5">已接好</div>
                <ul className="list-disc list-inside space-y-0.5 text-emerald-700">
                  <li>tasks.create (建任务/日程)</li>
                  <li>documents.generate (准备文档草稿上下文 — 真查数据中心)</li>
                  <li>inline 审批 (你本人提交即通过)</li>
                  <li>AI 调用全程留痕</li>
                </ul>
              </div>
              <div className="rounded-md bg-amber-50 border border-amber-100 px-3 py-2 text-[11px] text-amber-800">
                <div className="font-medium mb-0.5">未接 / 有限制</div>
                <ul className="list-disc list-inside space-y-0.5 text-amber-700">
                  <li>documents.generate 只组上下文, 不真出 markdown (A P2)</li>
                  <li>合同草稿 endpoint 单独走 contract_drafts 链路</li>
                  <li>历史合同 / 方法论 注入 (待 P1)</li>
                  <li>AI 输出不自动标"客户官方资料"</li>
                </ul>
              </div>
            </div>

            {/* inline authorization 提示 */}
            {parsed.inline_authorization_detected && (
              <div className="rounded-md bg-emerald-50 border border-emerald-100 px-3 py-2 text-[11px] text-emerald-800">
                <div className="font-medium">✓ 检测到指令内授权</div>
                <div className="text-emerald-700 mt-0.5">
                  你的原话: "{parsed.inline_authorization_text}". 后端将校验你是否为 {bot.display_name} 的审批人, 通过后直接 inline approve.
                </div>
              </div>
            )}

            {errorMessage && (
              <div className="flex items-start gap-2 px-3 py-2 bg-red-50 border border-red-100 rounded-md text-[12px] text-red-700">
                <AlertCircle size={13} className="mt-0.5 shrink-0" />
                <span>{errorMessage}</span>
              </div>
            )}

            <div className="flex items-center justify-between pt-1 gap-2 flex-wrap">
              <button type="button" onClick={() => setStage('bot_resolved')} className="text-[11px] text-gray-500 hover:text-gray-700">
                返回
              </button>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setStage('input')}
                  className="text-[11px] text-gray-600 hover:text-gray-900 px-2 py-1"
                >
                  修改计划
                </button>
                <button
                  type="button"
                  onClick={handleSubmitPlan}
                  className="inline-flex items-center gap-1.5 px-4 py-1.5 text-[12.5px] font-medium text-white bg-[#5B7BFE] hover:bg-[#4A63CF] rounded-lg transition-colors"
                  title="点击=我作为审批人当场确认这个流程 (你本人就是审批人, 不会再去审批中心走二次流程)"
                >
                  ✓ 确认执行 — 我审批通过
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Stage: submitting */}
        {stage === 'submitting' && (
          <div className="px-6 py-12 flex flex-col items-center gap-3">
            <Loader2 size={28} className="text-[#5B7BFE] animate-spin" />
            <div className="text-[13px] text-gray-600">正在提交 AI 任务计划...</div>
          </div>
        )}

        {/* Stage: submitted - 顾源源 5/24 真用反馈: "确认这个流程 = 审批本身; 任务结束要类似邮件通知" */}
        {/* M10 (A, 2026-05-25) · approved 路径加进度轮询展示 */}
        {stage === 'submitted' && submitResult && (
          <div className="px-6 py-6 space-y-3">
            {submitResult.status === 'approved' ? (
              <>
                <div className="flex items-center gap-2 text-[14px] font-medium text-emerald-700">
                  <CheckCircle2 size={18} /> 你已确认 — {bot?.display_name} 开始执行
                </div>
                <PlanProgressView
                  progress={planProgress}
                  error={progressError}
                  botName={bot?.display_name || '机器人'}
                />
              </>
            ) : (
              <>
                <div className="flex items-center gap-2 text-[14px] font-medium text-amber-700">
                  <AlertCircle size={18} /> 还差一步审批
                </div>
                <div className="rounded-md bg-amber-50 border border-amber-100 px-3 py-2.5 text-[11.5px] text-amber-800 leading-relaxed">
                  你不在 {bot?.display_name} 的审批人列表 ({(bot?.reporting_approvers || []).map((a) => a.role).join(' / ') || '未配置'}),
                  系统已转交真正的审批人. 通过后 {bot?.display_name} 才会执行.
                </div>
              </>
            )}
            <div className="rounded-md bg-gray-50 border border-gray-100 px-3 py-2 text-[10.5px] text-gray-500 space-y-0.5 font-mono">
              <div>task_id: {submitResult.task_id || '(无关联任务)'}</div>
              <div>plan_id: {submitResult.ai_task_plan_id}</div>
              <div>actor: {bot?.actor_id}</div>
            </div>
            <button type="button" onClick={onClose} className="w-full mt-2 px-4 py-2 text-[12.5px] font-medium text-white bg-[#5B7BFE] hover:bg-[#4A63CF] rounded-lg transition-colors">
              完成
            </button>
          </div>
        )}

        {/* Stage: error */}
        {stage === 'error' && (
          <div className="px-6 py-6 space-y-3">
            <div className="flex items-center gap-2 text-[14px] font-medium text-red-700">
              <AlertCircle size={18} /> 出错了
            </div>
            <div className="rounded-md bg-red-50 border border-red-100 px-3 py-2 text-[12px] text-red-700">
              {errorMessage || '未知错误'}
            </div>
            <div className="flex items-center justify-between gap-2">
              <button type="button" onClick={handleSwitchToQuickTask} className="text-[11px] text-gray-500 hover:text-gray-700">
                作为普通任务创建
              </button>
              <button type="button" onClick={() => setStage('input')} className="px-4 py-1.5 text-[12.5px] font-medium text-white bg-[#5B7BFE] hover:bg-[#4A63CF] rounded-lg transition-colors">
                重新尝试
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── M10 (A, 2026-05-25) · plan 执行进度展示 (顾源源 5/24: "复杂任务 5-30 min 必须看得到进度") ──
type PlanProgressViewProps = {
  progress: PlanProgressRecord | null;
  error: string | null;
  botName: string;
};

function PlanProgressView({ progress, error, botName }: PlanProgressViewProps) {
  if (error) {
    return (
      <div className="rounded-md bg-red-50 border border-red-100 px-3 py-2 text-[11.5px] text-red-700 leading-relaxed">
        进度查询失败: {error}
      </div>
    );
  }
  if (!progress) {
    return (
      <div className="rounded-md bg-emerald-50 border border-emerald-100 px-3 py-2.5 text-[11.5px] text-emerald-800 leading-relaxed flex items-center gap-2">
        <Loader2 size={12} className="animate-spin" />
        <span>正在准备执行... {botName} 已收到指令</span>
      </div>
    );
  }

  const exec = progress.execution_status;
  const pct = Math.max(0, Math.min(100, progress.progress?.percent ?? 0));
  const total = progress.progress?.total ?? 0;
  const completed = progress.progress?.completed ?? 0;
  const current = progress.progress?.current || '';
  const subtasks = progress.subtasks || [];

  let headerEl: React.ReactNode = null;
  if (exec === 'not_started' || exec === 'pending_execute') {
    headerEl = (
      <div className="text-[11.5px] text-emerald-800 flex items-center gap-2">
        <Loader2 size={12} className="animate-spin" />
        <span>等待执行... {botName} 已接收, 即将开跑</span>
      </div>
    );
  } else if (exec === 'running') {
    headerEl = (
      <div className="text-[11.5px] text-emerald-800 flex items-center gap-2">
        <Loader2 size={12} className="animate-spin" />
        <span>{current || '正在执行'} ({completed}/{total})</span>
      </div>
    );
  } else if (exec === 'success') {
    headerEl = (
      <div className="text-[11.5px] text-emerald-800 flex items-center gap-2">
        <CheckCircle2 size={12} />
        <span>已完成 ({completed}/{total} 个子任务)</span>
      </div>
    );
  } else if (exec === 'partial') {
    headerEl = (
      <div className="text-[11.5px] text-amber-700 flex items-center gap-2">
        <AlertCircle size={12} />
        <span>部分完成 ({completed}/{total} 成功, {(progress.errors || []).length} 失败)</span>
      </div>
    );
  } else if (exec === 'failed') {
    headerEl = (
      <div className="text-[11.5px] text-red-700 flex items-center gap-2">
        <AlertCircle size={12} />
        <span>执行失败 (0/{total} 成功)</span>
      </div>
    );
  }

  const barColor =
    exec === 'success' ? 'bg-emerald-500'
    : exec === 'failed' ? 'bg-red-400'
    : exec === 'partial' ? 'bg-amber-400'
    : 'bg-[#5B7BFE]';

  return (
    <div className="space-y-2">
      <div className="rounded-md bg-emerald-50 border border-emerald-100 px-3 py-2.5 space-y-2">
        {headerEl}
        {total > 0 && (
          <div className="w-full h-1 bg-emerald-100 rounded-full overflow-hidden ring-1 ring-inset ring-emerald-200/50">
            <div
              className={`h-full ${barColor} transition-all duration-300`}
              style={{ width: `${pct}%` }}
            />
          </div>
        )}
        <div className="text-[10px] text-emerald-700/80">{pct}%</div>
      </div>

      {subtasks.length > 0 && (
        <div className="rounded-md bg-white border border-gray-200 ring-1 ring-inset ring-gray-100 divide-y divide-gray-100">
          {subtasks.map((st) => {
            const iconColor =
              st.status === 'success' ? 'text-emerald-600'
              : st.status === 'failed' ? 'text-red-500'
              : st.status === 'running' ? 'text-[#5B7BFE]'
              : 'text-gray-400';
            const iconEl =
              st.status === 'success' ? <CheckCircle2 size={11} className={iconColor} />
              : st.status === 'failed' ? <AlertCircle size={11} className={iconColor} />
              : st.status === 'running' ? <Loader2 size={11} className={`${iconColor} animate-spin`} />
              : <ChevronRight size={11} className={iconColor} />;
            return (
              <div key={st.index} className="px-3 py-1.5 flex items-start gap-2 text-[11px]">
                <div className="mt-0.5">{iconEl}</div>
                <div className="flex-1 min-w-0">
                  <div className="text-gray-700 font-medium truncate">
                    <span className="text-gray-400 mr-1">#{st.index + 1}</span>{st.tool}
                  </div>
                  {st.output_summary && (
                    <div className="text-[10.5px] text-gray-500 mt-0.5 leading-relaxed break-words">
                      {st.output_summary}
                    </div>
                  )}
                  {st.error && (
                    <div className="text-[10.5px] text-red-600 mt-0.5 leading-relaxed break-words">
                      错误: {st.error}
                    </div>
                  )}
                </div>
                {typeof st.duration_ms === 'number' && (
                  <div className="text-[9.5px] text-gray-400 shrink-0 mt-0.5 tabular-nums">
                    {st.duration_ms}ms
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── M7: inline @mention picker (顾源源 5/25 微信群体验) ──
// 注: inline 在本文件, 不单独建文件 (顾源源 hard rule).
// 风格对齐 LeaderPicker: ring-1 inset, rounded-lg, shadow-lg, 主色 #5B7BFE.
type MentionPickerProps = {
  bots: BotMemberRecord[];
  query: string;
  highlightedIndex: number;
  onHover: (i: number) => void;
  onSelect: (bot: BotMemberRecord) => void;
};

function MentionPicker({ bots, query, highlightedIndex, onHover, onSelect }: MentionPickerProps) {
  // 过滤 — 与父组件 filteredBots 同口径 (大小写不敏感, 中文 includes 原 query)
  const q = query.toLowerCase();
  const filtered = !q
    ? bots
    : bots.filter((b) => {
        const handle = (b.handle || '').toLowerCase();
        const name = (b.display_name || '').toLowerCase();
        return handle.includes(q) || name.includes(q) || b.display_name.includes(query);
      });

  return (
    <div
      className="absolute left-0 top-full mt-1 z-[70] w-[320px] max-h-[280px] overflow-y-auto bg-white rounded-lg shadow-lg ring-1 ring-inset ring-gray-200"
      // 阻止 mousedown 抢 textarea focus (否则 onClick 之前 textarea blur, cursor 丢)
      onMouseDown={(e) => e.preventDefault()}
    >
      {filtered.length === 0 ? (
        <div className="px-3 py-3 text-[11.5px] text-gray-400 leading-relaxed">
          无匹配的机器人同事 — 你可以手动输 @庆华
        </div>
      ) : (
        filtered.map((b, i) => {
          const highlighted = highlightedIndex === i;
          return (
            <button
              key={b.id}
              type="button"
              onMouseEnter={() => onHover(i)}
              onClick={() => onSelect(b)}
              className={`w-full text-left px-3 py-2 border-b border-gray-100 last:border-b-0 transition-colors ${
                highlighted ? 'bg-[#5B7BFE]/10' : 'hover:bg-gray-50'
              }`}
            >
              <div className={`flex items-center gap-2 text-[12.5px] font-medium ${highlighted ? 'text-gray-900' : 'text-gray-800'}`}>
                <Bot size={11} className="text-[#5B7BFE]" />
                <span>{b.display_name}</span>
                <span className={`text-[10.5px] ${highlighted ? 'text-gray-500' : 'text-gray-400'}`}>@{b.handle}</span>
                <span className="ml-auto text-[9.5px] px-1.5 py-0.5 rounded bg-[#5B7BFE]/10 text-[#3B5BCF]">{b.actor_type}</span>
              </div>
              <div className={`mt-0.5 text-[10.5px] ${highlighted ? 'text-gray-500' : 'text-gray-400'}`}>
                {b.department_name || '未分配部门'}
              </div>
            </button>
          );
        })
      )}
    </div>
  );
}
