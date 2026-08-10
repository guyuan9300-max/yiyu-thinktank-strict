import React from 'react';
import { ArrowUpRight, Bookmark, BookmarkCheck, ExternalLink, FilePlus2, Newspaper, Sparkles } from 'lucide-react';

import type { Task, TopicCandidate, TopicCandidateChatMessage, TopicCandidateInsight } from '../../../shared/types';
import { TopicIntelChatPanel } from './TopicIntelChatPanel';

type TopicIntelDetailPanelProps = {
  candidate: TopicCandidate | null;
  radarTitle?: string;
  insight?: TopicCandidateInsight | null;
  isLoadingInsight: boolean;
  saved: boolean;
  relatedTasks: Task[];
  chatMessages: TopicCandidateChatMessage[];
  chatDraft: string;
  isChatting: boolean;
  onToggleSaved: () => void;
  onAskDiscussionPrompt: (question: string) => void;
  onChatDraftChange: (value: string) => void;
  onSendChat: () => void;
  onOpenTask: () => void;
  onOpenSource: () => void;
};

function formatPublishedAt(value?: string | null) {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function normalizeEditorialNote(value?: string | null) {
  return (value || '')
    .trim()
    .replace(/^(?:大周|情报助手)(?:的)?(?:前哨判断|情报判断|判断)[：:]\s*/, '');
}

export function TopicIntelDetailPanel({
  candidate,
  radarTitle,
  insight,
  isLoadingInsight,
  saved,
  relatedTasks,
  chatMessages,
  chatDraft,
  isChatting,
  onToggleSaved,
  onAskDiscussionPrompt,
  onChatDraftChange,
  onSendChat,
  onOpenTask,
  onOpenSource,
}: TopicIntelDetailPanelProps) {
  if (!candidate) {
    return (
      <div className="h-full bg-white border border-gray-100 rounded-[32px] shadow-sm p-6 flex flex-col justify-center items-center text-center">
        <div className="w-14 h-14 rounded-2xl bg-blue-50 text-[#5B7BFE] flex items-center justify-center">
          <Newspaper size={24} />
        </div>
        <h2 className="text-[18px] font-bold text-gray-900 mt-5">选择一篇情报</h2>
        <p className="text-[13px] text-gray-500 mt-2 max-w-[320px] leading-6">
          左侧会显示雷达抓回的情报。点开任意一篇，就能看到它和哪个雷达相关、主要观点是什么，以及能不能收进资料夹或转成任务。
        </p>
      </div>
    );
  }

  const canCreateTask = candidate.insightStatus === 'ready';
  const keyPoints = insight?.keyPoints?.length ? insight.keyPoints : ['当前还没有稳定的核心观点，建议先看原文。'];
  const writingAngles = insight?.practicalUses?.length ? insight.practicalUses : ['后续可围绕这篇内容继续追问：哪些判断值得转成文章、哪些事实值得交给同事跟进。'];
  const discussionPrompts = insight?.discussionPrompts?.length ? insight.discussionPrompts : ['如果继续深挖，这篇内容最值得追问的，是它背后到底反映了怎样的变化。'];
  const editorialNote = normalizeEditorialNote(insight?.editorialNote) || '情报助手还在把这篇文章里的显性观点转成更值得继续思考的情报判断。';

  return (
    <div className="h-full bg-white border border-gray-100 rounded-[32px] shadow-sm p-6 overflow-y-auto">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="px-2.5 py-1 rounded-full text-[11px] font-bold bg-blue-50 text-[#4a67f5] border border-blue-100">
              {radarTitle || '未命名雷达'}
            </span>
            {saved && (
              <span className="px-2.5 py-1 rounded-full text-[11px] font-bold bg-amber-50 text-amber-700 border border-amber-100">
                资料夹
              </span>
            )}
            {relatedTasks.length > 0 && (
              <span className="px-2.5 py-1 rounded-full text-[11px] font-bold bg-violet-50 text-violet-700 border border-violet-100">
                已转任务 {relatedTasks.length}
              </span>
            )}
          </div>
          <h2 className="text-[24px] font-bold text-gray-900 mt-3 leading-9">{candidate.title}</h2>
          <div className="flex flex-wrap items-center gap-3 mt-3 text-[12px] text-gray-500">
            <span>{candidate.source}</span>
            {candidate.publishedAt && <span>发布于 {formatPublishedAt(candidate.publishedAt)}</span>}
            <span>收录于 {formatPublishedAt(candidate.createdAt)}</span>
          </div>
        </div>

        <div className="flex flex-col gap-2 shrink-0">
          <button
            type="button"
            onClick={onToggleSaved}
            className={`px-4 py-2.5 rounded-xl text-[13px] font-semibold transition-all inline-flex items-center justify-center gap-2 ${
              saved ? 'bg-amber-50 text-amber-700 border border-amber-200 hover:bg-amber-100' : 'bg-white border border-gray-200 text-gray-700 shadow-sm hover:bg-gray-50 hover:border-gray-300'
            }`}
          >
            {saved ? <BookmarkCheck size={14} /> : <Bookmark size={14} />}
            {saved ? '移出资料夹' : '收进资料夹'}
          </button>
          <button
            type="button"
            disabled={!canCreateTask}
            onClick={onOpenTask}
            className={`px-4 py-2.5 rounded-xl text-[13px] font-semibold transition-all inline-flex items-center justify-center gap-2 ${
              canCreateTask
                ? 'bg-white border border-gray-200 text-gray-700 shadow-sm hover:bg-gray-50 hover:border-gray-300'
                : 'bg-gray-100 border border-gray-200 text-gray-400 cursor-not-allowed'
            }`}
          >
            <FilePlus2 size={14} />
            转任务
          </button>
          <button
            type="button"
            onClick={onOpenSource}
            className="px-4 py-2.5 rounded-xl text-[13px] font-semibold bg-white border border-gray-200 text-gray-700 shadow-sm hover:bg-gray-50 hover:border-gray-300 transition-all inline-flex items-center justify-center gap-2"
          >
            <ExternalLink size={14} />
            查看原文
          </button>
        </div>
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4">
        <section className="rounded-[24px] border border-gray-100 px-5 py-4">
          <p className="text-[12px] font-bold text-gray-900">核心观点</p>
          <div className="mt-3 space-y-3">
            {keyPoints.map((item, index) => (
              <div key={`${candidate.id}-point-${index}`} className="flex items-start gap-3 text-[13px] text-gray-600">
                <span className="w-6 h-6 rounded-full bg-emerald-50 text-emerald-700 flex items-center justify-center text-[11px] font-bold shrink-0">
                  {index + 1}
                </span>
                <p className="leading-6">{item}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-[24px] border border-gray-100 px-5 py-4">
          <div className="flex items-center gap-2">
            <Sparkles size={16} className="text-violet-600" />
            <p className="text-[12px] font-bold text-gray-900">情报判断</p>
          </div>
          {isLoadingInsight ? (
            <p className="text-[13px] text-gray-500 mt-3">情报助手正在补全这篇情报的判断…</p>
          ) : (
            <p className="text-[13px] text-gray-600 mt-3 leading-7 whitespace-pre-line">{editorialNote}</p>
          )}
        </section>

        <section className="rounded-[24px] border border-gray-100 px-5 py-4">
          <p className="text-[12px] font-bold text-gray-900">可直接展开成文</p>
          <div className="mt-3 space-y-3">
            {writingAngles.map((item, index) => (
              <div key={`${candidate.id}-use-${index}`} className="flex items-start gap-3 text-[13px] text-gray-600">
                <span className="w-6 h-6 rounded-full bg-amber-50 text-amber-700 flex items-center justify-center text-[11px] font-bold shrink-0">
                  {index + 1}
                </span>
                <p className="leading-6">{item}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-[24px] border border-gray-100 px-5 py-4">
          <p className="text-[12px] font-bold text-gray-900">值得继续追问的问题</p>
          <p className="text-[12px] text-gray-500 mt-1">点任何一条问题，都可以直接让情报助手基于这篇情报继续回答。</p>
          <div className="mt-3 space-y-3">
            {discussionPrompts.map((item, index) => (
              <button
                key={`${candidate.id}-discussion-${index}`}
                type="button"
                onClick={() => onAskDiscussionPrompt(item)}
                className="w-full flex items-start gap-3 rounded-[18px] border border-sky-100 bg-sky-50/60 px-3 py-3 text-left transition-all hover:border-sky-200 hover:bg-sky-50"
              >
                <span className="w-6 h-6 rounded-full bg-white text-sky-700 flex items-center justify-center text-[11px] font-bold shrink-0">
                  {index + 1}
                </span>
                <span className="flex-1 text-[13px] leading-6 text-slate-700">{item}</span>
                <ArrowUpRight size={14} className="shrink-0 mt-1 text-sky-600" />
              </button>
            ))}
          </div>
        </section>

        <TopicIntelChatPanel
          messages={chatMessages}
          draft={chatDraft}
          loading={isChatting}
          onDraftChange={onChatDraftChange}
          onSend={onSendChat}
        />
      </div>
    </div>
  );
}
