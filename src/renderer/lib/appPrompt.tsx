// Electron renderer 默认禁用 window.prompt(contextIsolation+nodeIntegration=false 默认行为),
// 返回 null 静默失败。本文件提供等价但能用的全局 modal:
//
//   const result = await appPrompt({
//     title: '插入图片',
//     fields: [
//       { name: 'url', label: '图片 URL（http/https 或 data:）', required: true },
//       { name: 'alt', label: '图片说明（可选）' },
//     ],
//   });
//   // result: { url: string, alt: string } 或 null（用户取消）
//
// 简化形式 - 单字段:
//   const value = await appPromptText('输入流程名称');
//
// 用法:
//   1. 在 main.tsx 把 <AppPromptHost /> 挂到 root 根节点旁边一次
//   2. 业务代码任何地方 await appPrompt(...) 即可,host 自动渲染 modal
//
// 不要再用 window.prompt,Electron 上根本走不通。

import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

export type AppPromptField = {
  name: string;
  label: string;
  placeholder?: string;
  defaultValue?: string;
  required?: boolean;
  multiline?: boolean;
};

export type AppPromptOptions = {
  title?: string;
  fields: AppPromptField[];
  confirmLabel?: string;
  cancelLabel?: string;
};

type ActivePrompt = AppPromptOptions & {
  resolve: (value: Record<string, string> | null) => void;
};

let activePromptSetter: ((next: ActivePrompt | null) => void) | null = null;

export function appPrompt(options: AppPromptOptions): Promise<Record<string, string> | null> {
  if (!activePromptSetter) {
    // eslint-disable-next-line no-console
    console.warn('[appPrompt] AppPromptHost 没挂上,返回 null。请在 main.tsx 添加 <AppPromptHost />。');
    return Promise.resolve(null);
  }
  return new Promise((resolve) => {
    activePromptSetter!({ ...options, resolve });
  });
}

export async function appPromptText(label: string, defaultValue = '', placeholder = ''): Promise<string | null> {
  const result = await appPrompt({
    fields: [{ name: 'value', label, defaultValue, placeholder, required: false }],
  });
  if (!result) return null;
  return result.value ?? null;
}

export function AppPromptHost() {
  const [active, setActive] = useState<ActivePrompt | null>(null);
  const valuesRef = useRef<Record<string, string>>({});
  const firstInputRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);

  useEffect(() => {
    activePromptSetter = setActive;
    return () => {
      activePromptSetter = null;
    };
  }, []);

  useEffect(() => {
    if (active) {
      valuesRef.current = Object.fromEntries(active.fields.map((f) => [f.name, f.defaultValue ?? '']));
      // 等 DOM 渲染完成后聚焦第一个输入框
      setTimeout(() => firstInputRef.current?.focus(), 0);
    }
  }, [active]);

  if (!active) return null;

  const close = (result: Record<string, string> | null) => {
    active.resolve(result);
    setActive(null);
  };

  const handleConfirm = () => {
    // 校验必填
    for (const field of active.fields) {
      if (field.required && !(valuesRef.current[field.name] ?? '').trim()) {
        // 不关闭,焦点回到该必填项
        return;
      }
    }
    close({ ...valuesRef.current });
  };

  const handleKeyDown = (event: React.KeyboardEvent, isMultiline: boolean) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      close(null);
    } else if (event.key === 'Enter' && !isMultiline && !event.shiftKey) {
      event.preventDefault();
      handleConfirm();
    }
  };

  return createPortal(
    <div
      className="fixed inset-0 z-[300] flex items-center justify-center bg-slate-950/40 px-4 backdrop-blur-sm"
      onMouseDown={() => close(null)}
    >
      <div
        className="w-full max-w-md rounded-2xl border border-gray-200 bg-white p-5 shadow-xl"
        onMouseDown={(event) => event.stopPropagation()}
      >
        {active.title && <h3 className="mb-3 text-base font-bold text-gray-800">{active.title}</h3>}
        {active.fields.map((field, idx) => (
          <div key={field.name} className="mb-3">
            <label className="mb-1 block text-[12px] font-semibold text-gray-600">
              {field.label}
              {field.required && <span className="ml-1 text-rose-500">*</span>}
            </label>
            {field.multiline ? (
              <textarea
                ref={(el) => {
                  if (idx === 0) firstInputRef.current = el;
                }}
                defaultValue={field.defaultValue ?? ''}
                placeholder={field.placeholder}
                onChange={(event) => {
                  valuesRef.current[field.name] = event.target.value;
                }}
                onKeyDown={(event) => handleKeyDown(event, true)}
                className="min-h-[80px] w-full resize-y rounded-lg border border-gray-300 bg-white px-3 py-2 text-[13px] outline-none focus:border-blue-400"
              />
            ) : (
              <input
                ref={(el) => {
                  if (idx === 0) firstInputRef.current = el;
                }}
                type="text"
                defaultValue={field.defaultValue ?? ''}
                placeholder={field.placeholder}
                onChange={(event) => {
                  valuesRef.current[field.name] = event.target.value;
                }}
                onKeyDown={(event) => handleKeyDown(event, false)}
                className="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-[13px] outline-none focus:border-blue-400"
              />
            )}
          </div>
        ))}
        <div className="mt-2 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => close(null)}
            className="rounded-lg border border-gray-200 bg-white px-4 py-1.5 text-[12px] font-semibold text-gray-600 hover:bg-gray-50"
          >
            {active.cancelLabel ?? '取消'}
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            className="rounded-lg bg-[#5B7BFE] px-4 py-1.5 text-[12px] font-semibold text-white hover:bg-[#4A6AEF]"
          >
            {active.confirmLabel ?? '确定'}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
