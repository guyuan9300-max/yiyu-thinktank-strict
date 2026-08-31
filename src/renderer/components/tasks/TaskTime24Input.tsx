import React, { useEffect, useRef, useState } from 'react';

import {
  resolveTaskEditorTimeCommit,
  resolveTaskEditorTimeDisplay,
} from '../../../shared/taskTime';

interface TaskTime24InputProps {
  value: string;
  previewValue?: string;
  onChange: (value: string) => void;
  label: string;
  autoFocus?: boolean;
  disabled?: boolean;
}

/**
 * 统一的 24 小时制任务时间输入。
 * previewValue 只参与显示；用户没有实际编辑时不会通过 onChange 写回。
 */
export function TaskTime24Input({
  value,
  previewValue = '',
  onChange,
  label,
  autoFocus = false,
  disabled = false,
}: TaskTime24InputProps) {
  const normalizedValue = resolveTaskEditorTimeDisplay(value, previewValue);
  const [hourInput, setHourInput] = useState(normalizedValue.slice(0, 2));
  const [minuteInput, setMinuteInput] = useState(normalizedValue ? normalizedValue.slice(3, 5) : '');
  const userHasEditedRef = useRef(false);

  useEffect(() => {
    setHourInput(normalizedValue.slice(0, 2));
    setMinuteInput(normalizedValue ? normalizedValue.slice(3, 5) : '');
    userHasEditedRef.current = false;
  }, [normalizedValue]);

  const commitTime = (nextHour: string, nextMinute: string) => {
    if (!nextHour && !nextMinute) {
      onChange('');
      return;
    }
    if (!/^\d{2}$/.test(nextHour) || !/^\d{2}$/.test(nextMinute)) return;
    const hour = Number(nextHour);
    const minute = Number(nextMinute);
    if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return;
    onChange(`${nextHour}:${nextMinute}`);
  };

  return (
    <div className="flex h-9 w-full items-center justify-center rounded-xl border border-slate-200 bg-slate-50 px-1 text-sm tabular-nums text-slate-700 transition focus-within:border-[#9FB2FF] focus-within:bg-white focus-within:ring-2 focus-within:ring-[#5B7BFE]/10 disabled:opacity-60">
      <div className="grid grid-cols-[1.75rem_0.75rem_1.75rem] items-center justify-center">
        <input
          type="text"
          inputMode="numeric"
          maxLength={2}
          aria-label={`${label}小时（24小时制）`}
          placeholder="--"
          value={hourInput}
          autoFocus={autoFocus}
          disabled={disabled}
          onChange={(event) => {
            const nextHour = event.target.value.replace(/\D/g, '').slice(0, 2);
            userHasEditedRef.current = true;
            setHourInput(nextHour);
            if (!nextHour) {
              setMinuteInput('');
              onChange('');
              return;
            }
            commitTime(nextHour, minuteInput);
          }}
          onBlur={() => {
            if (!/^\d{2}$/.test(hourInput) || Number(hourInput) > 23) {
              setHourInput(normalizedValue.slice(0, 2));
            } else {
              const nextTime = resolveTaskEditorTimeCommit(`${hourInput}:${minuteInput}`, userHasEditedRef.current);
              if (nextTime) onChange(nextTime);
            }
            userHasEditedRef.current = false;
          }}
          className="w-[1.75rem] min-w-0 appearance-none bg-transparent px-0 py-1 text-center text-sm font-semibold tabular-nums outline-none disabled:opacity-60"
        />
        <span className="w-[0.75rem] select-none text-center text-slate-400" aria-hidden>：</span>
        <input
          type="text"
          inputMode="numeric"
          maxLength={2}
          aria-label={`${label}分钟`}
          placeholder="--"
          value={minuteInput}
          disabled={disabled}
          onChange={(event) => {
            const nextMinute = event.target.value.replace(/\D/g, '').slice(0, 2);
            userHasEditedRef.current = true;
            setMinuteInput(nextMinute);
            if (!nextMinute) {
              setHourInput('');
              onChange('');
              return;
            }
            commitTime(hourInput, nextMinute);
          }}
          onBlur={() => {
            if (!/^\d{2}$/.test(minuteInput) || Number(minuteInput) > 59) {
              setMinuteInput(normalizedValue ? normalizedValue.slice(3, 5) : '');
            } else {
              const nextTime = resolveTaskEditorTimeCommit(`${hourInput}:${minuteInput}`, userHasEditedRef.current);
              if (nextTime) onChange(nextTime);
            }
            userHasEditedRef.current = false;
          }}
          className="w-[1.75rem] min-w-0 appearance-none bg-transparent px-0 py-1 text-center text-sm font-semibold tabular-nums outline-none disabled:opacity-60"
        />
      </div>
    </div>
  );
}
