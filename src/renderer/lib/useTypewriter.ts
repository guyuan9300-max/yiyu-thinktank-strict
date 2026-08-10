/**
 * 打字机 hook：让 `actualText` 以"逐字蹦"的方式追加到 `displayed`，
 * 模拟 ChatGPT / 豆包那种"字一个个出来"的体感。
 *
 * 设计要点：
 * - 总长度不变（actualText 由后端轮询提供，hook 不改变它）；
 *   只是把"突然出现一大段"切成"30ms 推进 2 字符"的视觉节奏。
 * - 当 actualText 增长时（streaming），打字机继续追。
 * - 当 actualText 被替换（不再是当前 displayed 的延续），自动 reset。
 * - 当 enabled=false（消息完成态/历史消息），直接显示完整 actualText，不打字机。
 *
 * 同一时刻只有一个 setInterval timer 在跑（cleanup + 重启），不会泄漏。
 */
import { useEffect, useRef, useState } from 'react';

export interface UseTypewriterOptions {
  /** 是否启用打字机；false 时直接返回完整 actualText */
  enabled?: boolean;
  /** 每个 tick 推进多少字符 */
  charsPerTick?: number;
  /** tick 间隔毫秒 */
  tickMs?: number;
}

export function useTypewriter(
  actualText: string,
  options: UseTypewriterOptions = {},
): string {
  const { enabled = true, charsPerTick = 2, tickMs = 30 } = options;
  const [displayed, setDisplayed] = useState<string>('');
  const lastActualRef = useRef<string>('');

  useEffect(() => {
    lastActualRef.current = actualText;

    // 关键：未启用打字机（历史消息 / 已完成态）→ 立刻渲染完整内容，不启动 timer。
    // 这避免了重新进入对话时历史消息再次播一遍打字机动画。
    if (!enabled) {
      if (displayed !== actualText) {
        setDisplayed(actualText);
      }
      return;
    }

    // 内容被替换（不是延续）→ 重置 displayed 重新开始打字
    if (lastActualRef.current && !actualText.startsWith(lastActualRef.current)) {
      setDisplayed('');
    }

    if (displayed.length >= actualText.length) {
      if (displayed !== actualText) {
        setDisplayed(actualText);
      }
      return;
    }

    const interval = Math.max(8, tickMs);
    const step = Math.max(1, charsPerTick);
    const timer = window.setInterval(() => {
      setDisplayed((prev) => {
        if (prev.length >= actualText.length) {
          window.clearInterval(timer);
          return prev;
        }
        return actualText.slice(0, prev.length + step);
      });
    }, interval);

    return () => window.clearInterval(timer);
  }, [actualText, enabled, charsPerTick, tickMs, displayed]);

  return enabled ? displayed : actualText;
}
