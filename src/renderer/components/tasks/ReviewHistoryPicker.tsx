import React from 'react';

import type { ReviewHistoryEntry } from '../../../shared/types';

type ReviewHistoryPickerProps = {
  open: boolean;
  loading: boolean;
  items: ReviewHistoryEntry[];
  activeWeekLabel: string;
  onClose: () => void;
  onSelect: (weekLabel: string) => void;
};

function formatHistoryTimestamp(value: string) {
  if (!value) return '未记录时间';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return `${parsed.getMonth() + 1}月${parsed.getDate()}日 ${String(parsed.getHours()).padStart(2, '0')}:${String(parsed.getMinutes()).padStart(2, '0')}`;
}

export function ReviewHistoryPicker({
  open,
  loading,
  items,
  activeWeekLabel,
  onClose,
  onSelect,
}: ReviewHistoryPickerProps) {
  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="false"
      className="mt-4 rounded-[28px] border border-amber-100 bg-white p-5 shadow-[0_18px_50px_rgba(15,23,42,0.08)]"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <h3 className="text-[18px] font-bold text-gray-900">查看历史复盘</h3>
          <p className="mt-1 text-[12px] leading-6 text-gray-500">选择一个历史周次，直接切换到对应的复盘内容。</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="h-9 w-9 shrink-0 rounded-full border border-gray-200 text-gray-400 transition hover:border-gray-300 hover:text-gray-600"
          aria-label="关闭历史复盘"
        >
          ×
        </button>
      </div>

      <div className="mt-5 max-h-[420px] space-y-3 overflow-y-auto pr-1">
        {loading && <div className="rounded-2xl bg-gray-50 px-4 py-6 text-center text-[13px] text-gray-500">正在读取历史复盘…</div>}
        {!loading && items.length === 0 && <div className="rounded-2xl bg-gray-50 px-4 py-6 text-center text-[13px] text-gray-500">还没有可查看的历史复盘。</div>}
        {!loading &&
          items.map((item) => {
            const isActive = item.weekLabel === activeWeekLabel;
            return (
              <button
                key={item.weekLabel}
                type="button"
                onClick={() => onSelect(item.weekLabel)}
                className={`w-full rounded-[24px] border px-4 py-4 text-left transition ${
                  isActive
                    ? 'border-[#5B7BFE]/30 bg-[#5B7BFE]/6 shadow-sm'
                    : 'border-gray-200 bg-white hover:border-gray-300 hover:bg-gray-50'
                }`}
              >
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-[15px] font-bold text-gray-900">{item.weekLabel}</div>
                    <div className="mt-1 text-[12px] text-gray-500">最近提交：{formatHistoryTimestamp(item.submittedAt)}</div>
                  </div>
                  {isActive && (
                    <span className="rounded-full bg-[#5B7BFE] px-2.5 py-1 text-[10px] font-bold text-white">
                      当前查看
                    </span>
                  )}
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <span className="rounded-full bg-gray-100 px-2.5 py-1 text-[11px] font-bold text-gray-500">
                    工作复盘 {item.workItemCount} 条
                  </span>
                  <span className="rounded-full bg-rose-50 px-2.5 py-1 text-[11px] font-bold text-rose-500">
                    成长复盘 {item.personalItemCount} 条
                  </span>
                </div>
              </button>
            );
          })}
      </div>
    </div>
  );
}
