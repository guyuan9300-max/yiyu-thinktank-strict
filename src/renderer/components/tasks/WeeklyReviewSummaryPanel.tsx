import React from 'react';

type WeeklyReviewSummaryPanelProps = Record<string, unknown>;

export function WeeklyReviewSummaryPanel(_props: WeeklyReviewSummaryPanelProps) {
  return (
    <section className="rounded-2xl border border-gray-100 bg-white p-6 text-[13px] text-gray-500 shadow-sm">
      周复盘 AI 摘要面板正在使用兼容壳显示；当前构建重点是资讯情报站。
    </section>
  );
}
