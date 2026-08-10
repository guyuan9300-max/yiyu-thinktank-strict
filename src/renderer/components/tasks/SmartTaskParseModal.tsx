/**
 * 智能新建任务 — 毛玻璃浮窗,粘贴文字 → AI 解析 → 弹出现有 TaskEditorModal
 *
 * 用户视角:
 *   1. 在「任务与日程」点击 [+ 新建任务 | ✦ 智能] 右半
 *   2. 全屏 backdrop-blur 遮罩,中央漂浮对话框
 *   3. 粘贴文字 → 点解析 → loading 几秒
 *   4. 成功:浮窗关闭 → 现有 TaskEditorModal 弹出,所有字段已填好
 *   5. 失败:浮窗内 toast 报错,文本保留让用户改重试
 *
 * 设计原则(经与用户对齐):
 * - 不在 TaskEditorModal 上加 "AI 痕迹标记"(用户点保存即等于人审过,无须额外提示)
 * - 日期严格识别:文本明确说才填,语气/紧迫感一律不推断
 * - clientId 自动选最匹配,用户在 TaskEditorModal 里能改
 */
import React, { useEffect, useRef, useState } from 'react';
import { Sparkles, X, Loader2 } from 'lucide-react';

import { aiParseTask, type TaskAiParseResult } from '../../lib/api';
import { useBackdropClickClose } from '../../lib/useBackdropClickClose';

type SmartTaskParseModalProps = {
  open: boolean;
  onClose: () => void;
  onParsed: (result: TaskAiParseResult, originalText: string) => void;
};

export function SmartTaskParseModal({ open, onClose, onParsed }: SmartTaskParseModalProps) {
  const [text, setText] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // 打开时自动 focus + 清空上次错误
  useEffect(() => {
    if (open) {
      setErrorMessage(null);
      window.setTimeout(() => textareaRef.current?.focus(), 80);
    } else {
      setText('');
      setSubmitting(false);
      setErrorMessage(null);
    }
  }, [open]);

  // Esc 关闭
  useEffect(() => {
    if (!open) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !submitting) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [open, onClose, submitting]);

  // 所有 hook 必须在 early return 之前调用,否则 open 切换时 hook 数量不一致,
  // 触发 React "Rendered more hooks than during the previous render" 报错。
  const backdropHandlers = useBackdropClickClose(onClose, !submitting);

  if (!open) return null;

  const handleParse = async () => {
    const trimmed = text.trim();
    if (!trimmed) {
      setErrorMessage('请先粘贴一段文字');
      return;
    }
    setSubmitting(true);
    setErrorMessage(null);
    try {
      const today = new Date();
      const currentDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
      const result = await aiParseTask({ text: trimmed, currentDate });
      onParsed(result, trimmed);
      // onParsed 会触发 onClose(由父组件控制) — 这里不主动 close,避免父组件先关掉再消费 result 的竞态
    } catch (error) {
      const detail = error instanceof Error ? error.message : '解析失败';
      setErrorMessage(detail);
      setSubmitting(false);
    }
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Cmd/Ctrl + Enter 提交
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.preventDefault();
      void handleParse();
    }
  };

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-gray-900/15 backdrop-blur-md transition-opacity"
      {...backdropHandlers}
    >
      <div className="w-[min(640px,92vw)] rounded-2xl bg-white shadow-[0_24px_70px_rgba(15,23,42,0.18)] ring-1 ring-inset ring-gray-100">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 px-6 pt-5 pb-3 border-b border-gray-100">
          <div>
            <div className="flex items-center gap-2 text-[9px] font-semibold uppercase tracking-[0.18em] text-gray-400">
              <Sparkles size={11} className="text-[#5B7BFE]" strokeWidth={2.2} />
              Smart Capture
            </div>
            <div className="mt-1 text-[16px] font-light tracking-tight text-gray-900">
              智能新建任务
            </div>
            <p className="mt-0.5 text-[11.5px] text-gray-500">
              粘贴一段文字(会议讨论、想法、邮件片段)— AI 帮你拆成标题、背景、日期和负责的客户
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="shrink-0 inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-gray-700 hover:bg-gray-50 transition-colors disabled:opacity-40"
            aria-label="关闭"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-5">
          <textarea
            ref={textareaRef}
            value={text}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={handleKeyDown}
            disabled={submitting}
            placeholder="例如：&#10;今天下午两点和合作方开会，讨论当前项目的组织问题，以及我们能承载哪些工作。目标是明确下一步分工和完成时间。"
            className="w-full min-h-[180px] max-h-[320px] resize-y rounded-xl border border-gray-200 bg-white px-4 py-3 text-[13px] leading-[1.7] text-gray-800 placeholder-gray-300 outline-none focus:border-[#5B7BFE] focus:ring-2 focus:ring-[#5B7BFE]/15 transition-colors disabled:bg-gray-50"
          />
          {errorMessage && (
            <div className="mt-3 rounded-md bg-rose-50/60 px-3 py-2 text-[11.5px] text-rose-700 ring-1 ring-inset ring-rose-200">
              {errorMessage}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between gap-3 px-6 pb-5 pt-2">
          <span className="text-[10px] font-medium uppercase tracking-[0.14em] text-gray-400">
            Cmd/Ctrl + Enter 提交
          </span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="rounded-md px-4 py-2 text-[12px] font-medium text-gray-600 ring-1 ring-inset ring-gray-200 hover:bg-gray-50 transition-colors disabled:opacity-50"
            >
              取消
            </button>
            <button
              type="button"
              onClick={() => void handleParse()}
              disabled={submitting || !text.trim()}
              className="inline-flex items-center gap-1.5 rounded-md bg-[#5B7BFE] px-4 py-2 text-[12px] font-medium text-white hover:bg-[#4A63CF] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {submitting ? (
                <>
                  <Loader2 size={13} className="animate-spin" />
                  解析中…
                </>
              ) : (
                <>
                  <Sparkles size={13} strokeWidth={2.2} />
                  解析
                </>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
