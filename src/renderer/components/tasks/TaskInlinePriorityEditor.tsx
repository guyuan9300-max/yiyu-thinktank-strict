import React, { useEffect, useRef, useState } from 'react';
import { Check, Flag, Loader2 } from 'lucide-react';

import type { Priority } from '../../../shared/types';

const PRIORITY_OPTIONS: Array<{
  value: Priority;
  label: string;
  description: string;
  dotClassName: string;
}> = [
  { value: 'high', label: '高优先级', description: '需要优先推进或已临近风险节点', dotClassName: 'bg-rose-500' },
  { value: 'normal', label: '普通优先级', description: '按当前计划正常推进', dotClassName: 'bg-blue-500' },
  { value: 'low', label: '低优先级', description: '可在核心事项之后安排', dotClassName: 'bg-slate-400' },
];

interface TaskInlinePriorityEditorProps {
  taskId: string;
  taskTitle: string;
  priority: Priority;
  label: string;
  marker?: string | null;
  toneClassName: string;
  isOpen: boolean;
  disabled?: boolean;
  onOpen: () => void;
  onClose: () => void;
  onSave: (priority: Priority) => Promise<void>;
}

export function TaskInlinePriorityEditor({
  taskId,
  taskTitle,
  priority,
  label,
  marker,
  toneClassName,
  isOpen,
  disabled = false,
  onOpen,
  onClose,
  onSave,
}: TaskInlinePriorityEditorProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const [savingPriority, setSavingPriority] = useState<Priority | null>(null);
  const [saveError, setSaveError] = useState('');

  useEffect(() => {
    if (!isOpen) return;
    setSaveError('');
    const focusTimer = window.setTimeout(() => dialogRef.current?.focus(), 0);
    return () => window.clearTimeout(focusTimer);
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return undefined;
    const handlePointerDown = (event: PointerEvent) => {
      if (savingPriority || rootRef.current?.contains(event.target as Node)) return;
      onClose();
    };
    document.addEventListener('pointerdown', handlePointerDown);
    return () => document.removeEventListener('pointerdown', handlePointerDown);
  }, [isOpen, onClose, savingPriority]);

  const selectPriority = async (nextPriority: Priority) => {
    if (nextPriority === priority || savingPriority) return;
    setSavingPriority(nextPriority);
    setSaveError('');
    try {
      await onSave(nextPriority);
      onClose();
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : '任务优先级保存失败，请重试。');
    } finally {
      setSavingPriority(null);
    }
  };

  const displayLabel = marker ? `${marker} · ${label}` : label;

  return (
    <div
      ref={rootRef}
      className="relative shrink-0"
      onClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => {
        event.stopPropagation();
        if (event.key === 'Escape' && !savingPriority) onClose();
      }}
    >
      <button
        type="button"
        onClick={onOpen}
        disabled={disabled}
        aria-expanded={isOpen}
        aria-haspopup="dialog"
        aria-controls={`task-inline-priority-${taskId}`}
        className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-bold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[#5B7BFE]/30 disabled:cursor-not-allowed disabled:opacity-50 ${toneClassName} ${
          isOpen ? 'border-[#B9C8FF] bg-[#F2F5FF] text-[#4E6EF2]' : 'hover:border-[#C9D6FF] hover:bg-white hover:text-[#4E6EF2]'
        }`}
        title={`${displayLabel}，点击直接修改任务优先级`}
      >
        <Flag size={10} />
        {marker && <span>{marker} ·</span>}
        <span>{label}</span>
      </button>

      {isOpen && (
        <div
          ref={dialogRef}
          id={`task-inline-priority-${taskId}`}
          role="dialog"
          tabIndex={-1}
          aria-label={`修改任务“${taskTitle}”的优先级`}
          className="absolute left-0 top-full z-40 mt-2 w-[258px] rounded-2xl border border-slate-200 bg-white p-2.5 text-left shadow-[0_14px_34px_rgba(15,23,42,0.14)]"
        >
          <div className="px-1 pb-2">
            <p className="text-[12px] font-bold text-slate-800">调整优先级</p>
            <p className="mt-0.5 truncate text-[10px] text-slate-400" title={taskTitle}>{taskTitle}</p>
          </div>

          <div className="space-y-1">
            {PRIORITY_OPTIONS.map((option) => {
              const selected = option.value === priority;
              const saving = option.value === savingPriority;
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => void selectPriority(option.value)}
                  disabled={Boolean(savingPriority) || selected}
                  aria-pressed={selected}
                  className={`flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left transition focus:outline-none focus-visible:ring-2 focus-visible:ring-[#5B7BFE]/20 ${
                    selected ? 'bg-[#F2F5FF]' : 'hover:bg-slate-50'
                  } disabled:cursor-default`}
                >
                  <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${option.dotClassName}`} />
                  <span className="min-w-0 flex-1">
                    <span className={`block text-[11px] font-bold ${selected ? 'text-[#4E6EF2]' : 'text-slate-700'}`}>{option.label}</span>
                    <span className="mt-0.5 block text-[9px] leading-4 text-slate-400">{option.description}</span>
                  </span>
                  {saving ? (
                    <Loader2 size={13} className="shrink-0 animate-spin text-[#5B7BFE]" />
                  ) : selected ? (
                    <Check size={13} className="shrink-0 text-[#5B7BFE]" />
                  ) : null}
                </button>
              );
            })}
          </div>

          {saveError && (
            <p role="alert" className="mt-2 rounded-lg bg-rose-50 px-2.5 py-2 text-[10px] leading-4 text-rose-600">
              {saveError}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
