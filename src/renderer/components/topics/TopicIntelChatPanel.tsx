import React, { useEffect, useRef } from 'react';
import { MessageCircle, SendHorizontal } from 'lucide-react';

import type { TopicCandidateChatMessage } from '../../../shared/types';

type TopicIntelChatPanelProps = {
  messages: TopicCandidateChatMessage[];
  draft: string;
  loading: boolean;
  onDraftChange: (value: string) => void;
  onSend: () => void;
};

function formatChatTime(value: string) {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  return parsed.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

export function TopicIntelChatPanel({ messages, draft, loading, onDraftChange, onSend }: TopicIntelChatPanelProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    element.scrollTo({ top: element.scrollHeight, behavior: 'smooth' });
  }, [loading, messages]);

  return (
    <section className="rounded-[24px] border border-gray-100 px-5 py-4">
      <div className="flex items-center gap-2">
        <MessageCircle size={16} className="text-[#5B7BFE]" />
        <p className="text-[12px] font-bold text-gray-900">围绕这篇情报继续问</p>
      </div>
      <p className="text-[12px] text-gray-500 mt-1">如果你对这篇新闻还有疑问，可以直接问情报助手。它会只围绕当前情报和已有解析继续回答。</p>

      <div
        ref={scrollRef}
        className="mt-3 rounded-[20px] border border-gray-100 bg-gray-50/70 px-4 py-4 space-y-3 min-h-[320px] max-h-[440px] overflow-y-auto"
      >
        {messages.length === 0 ? (
          <p className="text-[12px] text-gray-400 leading-6">上面那些“值得继续追问的问题”可以直接点，也可以在下面自己输入更具体的问题。</p>
        ) : (
          messages.map((message, index) => (
            <div key={`${message.role}-${message.createdAt}-${index}`} className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div
                className={`max-w-[88%] rounded-[18px] px-4 py-3 ${
                  message.role === 'user'
                    ? 'bg-[#5B7BFE] text-white shadow-[0_8px_20px_rgba(91,123,254,0.18)]'
                    : 'bg-white text-gray-700 border border-gray-100'
                }`}
              >
                <p className="text-[14px] leading-7 whitespace-pre-line">{message.content}</p>
                <p className={`text-[11px] mt-2 ${message.role === 'user' ? 'text-white/70' : 'text-gray-400'}`}>{formatChatTime(message.createdAt)}</p>
              </div>
            </div>
          ))
        )}

        {loading && (
          <div className="flex justify-start">
            <div className="max-w-[88%] rounded-[18px] px-4 py-3 bg-white text-gray-500 border border-gray-100">
              <p className="text-[14px] leading-7">情报助手正在结合这篇情报继续思考…</p>
            </div>
          </div>
        )}
      </div>

      <div className="mt-3 flex gap-3">
        <textarea
          value={draft}
          onChange={(event) => onDraftChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              onSend();
            }
          }}
          placeholder="例如：这篇新闻真正值得我们追问的，不是表面结论，而是哪层变化？"
          className="flex-1 min-h-[120px] rounded-[20px] border border-gray-200 bg-gray-50 px-4 py-3 text-[14px] leading-7 text-gray-700 outline-none resize-none focus:border-[#5B7BFE] focus:bg-white"
        />
        <button
          type="button"
          onClick={onSend}
          disabled={loading || !draft.trim()}
          className="shrink-0 self-end inline-flex items-center gap-2 rounded-[18px] bg-[#5B7BFE] px-4 py-3 text-[13px] font-semibold text-white shadow-[0_8px_20px_rgba(91,123,254,0.22)] transition-all hover:bg-[#4a6be6] disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <SendHorizontal size={14} />
          发送
        </button>
      </div>
    </section>
  );
}
