/**
 * Modal backdrop 点击关闭 — 防止"从输入框拖选文字时鼠标滑出导致 modal 意外关闭"的标准修法.
 *
 * 问题:
 *   用 onClick + event.target === event.currentTarget 判断"点了 backdrop"是经典坑.
 *   用户在 modal 内 textarea 里拖选文字时, 鼠标松开如果落在 backdrop 上,
 *   浏览器以 mouseup 的 target 为 click target → 触发 onClose → modal 关闭丢内容.
 *
 * 修法:
 *   要求 mousedown 和 mouseup 都发生在 backdrop 上, 才算"真的点了 backdrop".
 *   从 textarea / input 拖出来时, mousedown 在 textarea 上 → ref 为 false → 不关闭.
 *
 * 用法:
 *   const backdropHandlers = useBackdropClickClose(onClose, !submitting);
 *   <div className="fixed inset-0 ..." {...backdropHandlers}>
 *     ...
 *   </div>
 */

import { useCallback, useRef } from 'react';

export interface BackdropHandlers {
  onMouseDown: (event: React.MouseEvent<HTMLElement>) => void;
  onClick: (event: React.MouseEvent<HTMLElement>) => void;
}

export function useBackdropClickClose(
  onClose: () => void,
  /** false 时禁用关闭 (e.g. submitting 中) */
  enabled: boolean = true,
): BackdropHandlers {
  const downOnBackdropRef = useRef(false);

  const onMouseDown = useCallback((event: React.MouseEvent<HTMLElement>) => {
    downOnBackdropRef.current = event.target === event.currentTarget;
  }, []);

  const onClick = useCallback(
    (event: React.MouseEvent<HTMLElement>) => {
      const downedHere = downOnBackdropRef.current;
      downOnBackdropRef.current = false;
      if (!enabled) return;
      if (!downedHere) return; // mousedown 不在 backdrop → 拖选场景, 跳过
      if (event.target !== event.currentTarget) return;
      onClose();
    },
    [enabled, onClose],
  );

  return { onMouseDown, onClick };
}
