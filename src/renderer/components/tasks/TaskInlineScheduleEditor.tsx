import React, { useEffect, useRef, useState } from 'react';
import { Calendar as CalendarIcon, Check, Clock, Loader2 } from 'lucide-react';

import { TaskTime24Input } from './TaskTime24Input';

interface TaskInlineScheduleEditorProps {
  taskId: string;
  taskTitle: string;
  label: string;
  toneClassName: string;
  initialDate: string;
  initialTime: string;
  isOpen: boolean;
  disabled?: boolean;
  onOpen: () => void;
  onClose: () => void;
  onSave: (value: { date: string; time: string }) => Promise<void>;
}

export function TaskInlineScheduleEditor({
  taskId,
  taskTitle,
  label,
  toneClassName,
  initialDate,
  initialTime,
  isOpen,
  disabled = false,
  onOpen,
  onClose,
  onSave,
}: TaskInlineScheduleEditorProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [draftDate, setDraftDate] = useState(initialDate);
  const [draftTime, setDraftTime] = useState(initialTime);
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState('');

  useEffect(() => {
    if (!isOpen) return;
    setDraftDate(initialDate);
    setDraftTime(initialTime);
    setSaveError('');
  }, [initialDate, initialTime, isOpen]);

  useEffect(() => {
    if (!isOpen) return undefined;
    const handlePointerDown = (event: PointerEvent) => {
      if (isSaving || rootRef.current?.contains(event.target as Node)) return;
      onClose();
    };
    document.addEventListener('pointerdown', handlePointerDown);
    return () => document.removeEventListener('pointerdown', handlePointerDown);
  }, [isOpen, isSaving, onClose]);

  const normalizedDate = draftDate.trim();
  const normalizedTime = draftTime.trim();
  const hasChanges = normalizedDate !== initialDate || normalizedTime !== initialTime;
  const canSave = Boolean(normalizedDate && hasChanges && !isSaving);

  const handleSave = async () => {
    if (!canSave) return;
    setIsSaving(true);
    setSaveError('');
    try {
      await onSave({ date: normalizedDate, time: normalizedTime });
      onClose();
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : '任务时间保存失败，请重试。');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div
      ref={rootRef}
      className="relative shrink-0"
      onClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => {
        event.stopPropagation();
        if (event.key === 'Escape' && !isSaving) onClose();
      }}
    >
      <button
        type="button"
        onClick={onOpen}
        disabled={disabled}
        aria-expanded={isOpen}
        aria-haspopup="dialog"
        aria-controls={`task-inline-schedule-${taskId}`}
        className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-bold tabular-nums transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[#5B7BFE]/30 disabled:cursor-not-allowed disabled:opacity-50 ${toneClassName} ${
          isOpen ? 'border-[#B9C8FF] bg-[#F2F5FF] text-[#4E6EF2]' : 'hover:border-[#C9D6FF] hover:bg-white hover:text-[#4E6EF2]'
        }`}
        title={`${label}，点击直接修改任务时间`}
      >
        <CalendarIcon size={10} />
        {label}
      </button>

      {isOpen && (
        <div
          id={`task-inline-schedule-${taskId}`}
          role="dialog"
          aria-label={`修改任务“${taskTitle}”的时间`}
          className="absolute right-0 top-full z-40 mt-2 w-[292px] max-w-[calc(100vw-48px)] rounded-2xl border border-slate-200 bg-white p-3.5 text-left shadow-[0_14px_34px_rgba(15,23,42,0.14)]"
        >
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-[12px] font-bold text-slate-800">调整任务时间</p>
              <p className="mt-0.5 max-w-[220px] truncate text-[10px] text-slate-400" title={taskTitle}>{taskTitle}</p>
            </div>
            <span className="rounded-full bg-[#F2F5FF] px-2 py-1 text-[9px] font-bold text-[#5B7BFE]">直接编辑</span>
          </div>

          <div className="mt-3 grid grid-cols-[1.35fr_1fr] gap-2">
            <label className="block">
              <span className="mb-1 flex items-center gap-1 text-[10px] font-semibold text-slate-500">
                <CalendarIcon size={10} /> 日期
              </span>
              <input
                type="date"
                value={draftDate}
                onChange={(event) => { setDraftDate(event.target.value); setSaveError(''); }}
                disabled={isSaving}
                className="h-9 w-full rounded-xl border border-slate-200 bg-slate-50 px-2.5 text-[11px] font-semibold tabular-nums text-slate-700 outline-none transition focus:border-[#9FB2FF] focus:bg-white focus:ring-2 focus:ring-[#5B7BFE]/10 disabled:opacity-60"
              />
            </label>
            <div className="block">
              <span className="mb-1 flex items-center gap-1 text-[10px] font-semibold text-slate-500">
                <Clock size={10} /> 时间
              </span>
              <TaskTime24Input
                label="任务时间"
                value={draftTime}
                previewValue="09:00"
                onChange={(value) => { setDraftTime(value); setSaveError(''); }}
                autoFocus
                disabled={isSaving}
              />
            </div>
          </div>

          {saveError && (
            <p role="alert" className="mt-2 rounded-lg bg-rose-50 px-2.5 py-2 text-[10px] leading-4 text-rose-600">
              {saveError}
            </p>
          )}

          <div className="mt-3 flex items-center justify-between gap-3 border-t border-slate-100 pt-3">
            <p className="text-[9px] leading-4 text-slate-400">保存后同步任务列表和日历</p>
            <div className="flex shrink-0 items-center gap-1.5">
              <button
                type="button"
                onClick={onClose}
                disabled={isSaving}
                className="h-8 rounded-lg px-3 text-[11px] font-semibold text-slate-500 transition hover:bg-slate-100 disabled:opacity-50"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void handleSave()}
                disabled={!canSave}
                className="inline-flex h-8 min-w-[64px] items-center justify-center gap-1 rounded-lg bg-[#5B7BFE] px-3 text-[11px] font-bold text-white shadow-[0_4px_10px_rgba(91,123,254,0.22)] transition hover:bg-[#4E6EF2] disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400 disabled:shadow-none"
              >
                {isSaving ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                {isSaving ? '保存中' : '保存'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
