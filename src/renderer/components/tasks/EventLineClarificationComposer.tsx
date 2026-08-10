import React from 'react';
import { AlertCircle, MessageSquareText, Sparkles } from 'lucide-react';

type EventLineClarificationDraftValue = {
  summary: string;
  stage: string;
  intent: string;
  currentBlocker: string;
  nextStep: string;
  recentDecision: string;
  missingInfo: string[];
  confidence: 'low' | 'medium' | 'high';
};

type EventLineClarificationComposerProps = {
  transcript: string;
  onTranscriptChange: (value: string) => void;
  draft: EventLineClarificationDraftValue;
  onDraftChange: (patch: Partial<EventLineClarificationDraftValue>) => void;
  onGenerate: () => void;
  onCancel: () => void;
  onSave: () => void;
  isGenerating: boolean;
  isSaving: boolean;
  compact?: boolean;
};

export function EventLineClarificationComposer({
  transcript,
  onTranscriptChange,
  draft,
  onDraftChange,
  onGenerate,
  onCancel,
  onSave,
  isGenerating,
  isSaving,
  compact = false,
}: EventLineClarificationComposerProps) {
  const fieldClassName = compact
    ? 'rounded-2xl border border-gray-200 bg-white px-3 py-2.5 text-[12px] leading-5 text-gray-800 outline-none transition focus:border-[#B7C8FF] focus:ring-2 focus:ring-[#DCE5FF]'
    : 'rounded-2xl border border-gray-200 bg-white px-4 py-3 text-[13px] leading-6 text-gray-800 outline-none transition focus:border-[#B7C8FF] focus:ring-2 focus:ring-[#DCE5FF]';

  const labelClassName = compact ? 'text-[11px] font-bold text-slate-600' : 'text-[12px] font-bold text-gray-600';

  return (
    <div className={`mt-4 rounded-3xl border border-[#D7E0FF] bg-white/95 ${compact ? 'px-3 py-3' : 'px-4 py-4'}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <MessageSquareText size={compact ? 14 : 16} className="text-[#5B7BFE]" />
            <p className={`${compact ? 'text-[11px]' : 'text-[12px]'} font-semibold text-[#33449a]`}>粘贴聊天记录，AI 自动整理这条线</p>
          </div>
          <p className={`mt-1 ${compact ? 'text-[11px]' : 'text-[12px]'} leading-5 text-[#5c6ba1]`}>
            把和客户的聊天记录、会议纪要或沟通摘录贴进来，AI 会先提炼摘要、当前事项、阻塞、下一步和最近关键决策。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className={`rounded-2xl border border-[#D7E0FF] bg-[#F8FAFF] ${compact ? 'px-3 py-2 text-[11px]' : 'px-4 py-2 text-[12px]'} font-bold text-[#33449a] transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-60`}
            onClick={onGenerate}
            disabled={isGenerating || transcript.trim().length < 8}
          >
            <span className="inline-flex items-center gap-1.5">
              <Sparkles size={compact ? 12 : 14} />
              {isGenerating ? 'AI 整理中…' : 'AI 整理聊天记录'}
            </span>
          </button>
        </div>
      </div>

      <label className="mt-4 flex flex-col gap-2">
        <span className={labelClassName}>聊天记录 / 沟通摘录</span>
        <textarea
          value={transcript}
          onChange={(event) => onTranscriptChange(event.target.value)}
          rows={compact ? 6 : 8}
          placeholder="把和客户的聊天记录、语音转写、会议纪要、微信/飞书沟通摘录贴进来。AI 会先整理成这条事件线的当前态草稿。"
          className={`${fieldClassName} min-h-[160px] resize-y`}
        />
      </label>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <span
          className={`rounded-full px-3 py-1 text-[11px] font-bold ${
            draft.confidence === 'high'
              ? 'bg-emerald-50 text-emerald-700'
              : draft.confidence === 'medium'
                ? 'bg-amber-50 text-amber-700'
                : 'bg-slate-100 text-slate-600'
          }`}
        >
          AI 置信度：{draft.confidence === 'high' ? '高' : draft.confidence === 'medium' ? '中' : '低'}
        </span>
        {draft.missingInfo.map((item) => (
          <span key={item} className="rounded-full bg-[#FFF6EA] px-3 py-1 text-[11px] font-semibold text-[#E38B17]">
            缺：{item}
          </span>
        ))}
      </div>

      <div className={`mt-4 grid gap-3 ${compact ? 'md:grid-cols-2' : 'md:grid-cols-2'}`}>
        <label className="flex flex-col gap-2 md:col-span-2">
          <span className={labelClassName}>AI 整理摘要</span>
          <textarea
            value={draft.summary}
            onChange={(event) => onDraftChange({ summary: event.target.value })}
            rows={compact ? 3 : 4}
            placeholder="AI 会先总结这条线现在发生了什么。"
            className={fieldClassName}
          />
        </label>
        <label className="flex flex-col gap-2">
          <span className={labelClassName}>当前阶段</span>
          <input
            value={draft.stage}
            onChange={(event) => onDraftChange({ stage: event.target.value })}
            placeholder="例如：等待确认 / 资料补齐中"
            className={fieldClassName}
          />
        </label>
        <label className="flex flex-col gap-2">
          <span className={labelClassName}>当前事项</span>
          <textarea
            value={draft.intent}
            onChange={(event) => onDraftChange({ intent: event.target.value })}
            rows={compact ? 2 : 3}
            placeholder="这条线当前到底在推进什么。"
            className={fieldClassName}
          />
        </label>
        <label className="flex flex-col gap-2 md:col-span-2">
          <span className={labelClassName}>当前阻塞</span>
          <textarea
            value={draft.currentBlocker}
            onChange={(event) => onDraftChange({ currentBlocker: event.target.value })}
            rows={compact ? 2 : 3}
            placeholder="最卡的地方是什么，卡在谁、卡在哪个确认或资料上。"
            className={fieldClassName}
          />
        </label>
        <label className="flex flex-col gap-2">
          <span className={labelClassName}>下一步动作</span>
          <textarea
            value={draft.nextStep}
            onChange={(event) => onDraftChange({ nextStep: event.target.value })}
            rows={compact ? 2 : 3}
            placeholder="接下来最关键的一步是什么。"
            className={fieldClassName}
          />
        </label>
        <label className="flex flex-col gap-2">
          <span className={labelClassName}>最近关键决策</span>
          <textarea
            value={draft.recentDecision}
            onChange={(event) => onDraftChange({ recentDecision: event.target.value })}
            rows={compact ? 2 : 3}
            placeholder="最近哪次决定真正改变了这条线。"
            className={fieldClassName}
          />
        </label>
      </div>

      <div className="mt-4 rounded-2xl border border-[#F6E2B8] bg-[#FFF9ED] px-3 py-3 text-[11px] leading-5 text-[#8A6114]">
        <div className="flex items-start gap-2">
          <AlertCircle size={14} className="mt-0.5 shrink-0" />
          <p>AI 先帮你整理，再由你确认后保存；如果有缺口，优先继续补聊天摘录或手工修正，不要直接让系统猜。</p>
        </div>
      </div>

      <div className="mt-4 flex justify-end gap-2">
        <button
          type="button"
          className={`rounded-2xl border border-gray-200 bg-white ${compact ? 'px-3 py-2 text-[11px]' : 'px-4 py-2 text-[12px]'} font-bold text-gray-500 transition hover:text-gray-700`}
          onClick={onCancel}
        >
          取消
        </button>
        <button
          type="button"
          className={`rounded-2xl bg-[#5B7BFE] ${compact ? 'px-3 py-2 text-[11px]' : 'px-4 py-2 text-[12px]'} font-bold text-white transition hover:bg-[#4a68df] disabled:cursor-not-allowed disabled:bg-[#C9D4FF]`}
          onClick={onSave}
          disabled={isSaving}
        >
          {isSaving ? '保存中…' : '保存澄清'}
        </button>
      </div>
    </div>
  );
}
