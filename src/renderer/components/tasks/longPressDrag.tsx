/**
 * 任务长按拖拽（"收集箱→月历" 交互）
 *
 * 因为长按 + 拖拽组合不能用 HTML5 native drag（mousedown 后才能变 draggable，
 * 此时已经丢失原始 PointerEvent），整套用 pointer 事件自己模拟。
 * 阈值见 LONG_PRESS_MS（当前 400ms）。
 *
 *   1. pointerdown          → 启动计时器
 *   2. pointermove >5px     → 取消（视为滚动/误触）
 *   3. LONG_PRESS_MS 满     → 进入 dragging 态 + 切到月历 + ghost 浮起
 *   4. pointermove          → 更新 mousePos
 *   5. pointerup            → hit-test cell，命中则赋日期
 */
import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import type { Task } from '../../../shared/types';

const LONG_PRESS_MS = 400;
const MOVE_TOLERANCE_PX = 5;

type DropTarget = {
  id: string;
  rect: DOMRect;
  onDrop: (task: Task) => void;
};

type DraggingState = {
  task: Task;
  startedAt: number;
  mouseX: number;
  mouseY: number;
};

type DraggingTaskContextValue = {
  dragging: DraggingState | null;
  registerDropTarget: (id: string, el: HTMLElement, onDrop: (task: Task) => void) => () => void;
  /** 长按触发时本地组件用：把 Task 推上来 */
  startDragging: (task: Task, ev: PointerEvent | React.PointerEvent) => void;
  /** 切换视图时让父组件订阅。pointerdown 之后 800ms 触发 */
  onDragTriggered?: (task: Task) => void;
};

const DraggingTaskContext = createContext<DraggingTaskContextValue | null>(null);

export function useDraggingTask() {
  const ctx = useContext(DraggingTaskContext);
  if (!ctx) throw new Error('useDraggingTask must be used within DraggingTaskProvider');
  return ctx;
}

export function DraggingTaskProvider({
  children,
  onDragTriggered,
  onDateDrop,
}: {
  children: React.ReactNode;
  onDragTriggered?: (task: Task) => void;
  /** 释放鼠标在 [data-day-drop="YYYY-MM-DD"] 元素上时触发。 */
  onDateDrop?: (task: Task, dateStr: string) => void;
}) {
  const [dragging, setDragging] = useState<DraggingState | null>(null);
  const dropTargets = useRef<Map<string, DropTarget>>(new Map());

  const registerDropTarget = useCallback(
    (id: string, el: HTMLElement, onDrop: (task: Task) => void) => {
      const update = () => {
        dropTargets.current.set(id, { id, rect: el.getBoundingClientRect(), onDrop });
      };
      update();
      // 滚动 / resize 时刷新 rect
      const handler = () => update();
      window.addEventListener('scroll', handler, true);
      window.addEventListener('resize', handler);
      return () => {
        dropTargets.current.delete(id);
        window.removeEventListener('scroll', handler, true);
        window.removeEventListener('resize', handler);
      };
    },
    [],
  );

  const startDragging = useCallback(
    (task: Task, ev: PointerEvent | React.PointerEvent) => {
      setDragging({
        task,
        startedAt: Date.now(),
        mouseX: ev.clientX,
        mouseY: ev.clientY,
      });
      onDragTriggered?.(task);
    },
    [onDragTriggered],
  );

  // 全局监听 pointermove + pointerup，只在 dragging 时生效
  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: PointerEvent) => {
      setDragging((prev) => (prev ? { ...prev, mouseX: e.clientX, mouseY: e.clientY } : prev));
    };
    const onUp = (e: PointerEvent) => {
      // 优先按 data-day-drop 命中月历格子
      const el = document.elementFromPoint(e.clientX, e.clientY);
      const dayDrop = el?.closest('[data-day-drop]') as HTMLElement | null;
      if (dayDrop && dragging) {
        const dateStr = dayDrop.getAttribute('data-day-drop') || '';
        if (dateStr) {
          onDateDrop?.(dragging.task, dateStr);
          setDragging(null);
          return;
        }
      }
      // 其次按已注册的 drop targets 命中
      for (const t of dropTargets.current.values()) {
        const targetEl = document.querySelector(`[data-drop-id="${t.id}"]`) as HTMLElement | null;
        const r = targetEl?.getBoundingClientRect() || t.rect;
        if (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom) {
          if (dragging) t.onDrop(dragging.task);
          setDragging(null);
          return;
        }
      }
      setDragging(null);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
  }, [dragging, onDateDrop]);

  return (
    <DraggingTaskContext.Provider value={{ dragging, registerDropTarget, startDragging, onDragTriggered }}>
      {children}
      {dragging && <DraggingGhostOverlay dragging={dragging} />}
    </DraggingTaskContext.Provider>
  );
}

function DraggingGhostOverlay({ dragging }: { dragging: DraggingState }) {
  return (
    <div
      style={{
        position: 'fixed',
        left: dragging.mouseX + 12,
        top: dragging.mouseY + 12,
        pointerEvents: 'none',
        zIndex: 9999,
        maxWidth: 280,
        background: '#fff',
        boxShadow: '0 8px 24px rgba(15, 23, 42, 0.18)',
        border: '1px solid #c9d8fb',
        borderRadius: 12,
        padding: '8px 12px',
        fontSize: 12,
        fontWeight: 600,
        color: '#1e293b',
        opacity: 0.95,
        transform: 'rotate(-1deg)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#5B7BFE', flexShrink: 0 }} />
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {dragging.task.title}
        </span>
      </div>
      <div style={{ fontSize: 10, color: '#94a3b8', marginTop: 4 }}>
        拖到月历的某一天即可设置日期
      </div>
    </div>
  );
}

/**
 * 在任务行上挂的 hook。返回需要绑定的 props（pointer 事件 + 倒计时圆圈状态）。
 */
export function useLongPressDrag(
  task: Task | null,
  options?: { onCommit?: (task: Task) => void; disabled?: boolean },
): {
  longPressProps: React.HTMLAttributes<HTMLElement>;
  isPressing: boolean;
  progress: number; // 0-1，给倒计时圆圈用
} {
  const ctx = useContext(DraggingTaskContext);
  const [isPressing, setIsPressing] = useState(false);
  const [progress, setProgress] = useState(0);
  const timerRef = useRef<number | null>(null);
  const rafRef = useRef<number | null>(null);
  const startRef = useRef<{ x: number; y: number; t: number } | null>(null);
  const triggeredRef = useRef(false);

  const cleanup = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    startRef.current = null;
    triggeredRef.current = false;
    setIsPressing(false);
    setProgress(0);
  }, []);

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLElement>) => {
      if (options?.disabled || !task || !ctx) return;
      // 只响应左键
      if (e.button !== 0) return;
      // 避免在按钮 / input 等元素上触发
      const target = e.target as HTMLElement;
      if (target.closest('button, input, textarea, select, a[href], [data-no-long-press]')) return;

      startRef.current = { x: e.clientX, y: e.clientY, t: Date.now() };
      triggeredRef.current = false;
      setIsPressing(true);
      setProgress(0);

      // 倒计时圆圈动画
      const tick = () => {
        if (!startRef.current) return;
        const elapsed = Date.now() - startRef.current.t;
        const p = Math.min(1, elapsed / LONG_PRESS_MS);
        setProgress(p);
        if (p < 1 && !triggeredRef.current) {
          rafRef.current = requestAnimationFrame(tick);
        }
      };
      rafRef.current = requestAnimationFrame(tick);

      // LONG_PRESS_MS 后触发
      timerRef.current = window.setTimeout(() => {
        if (!startRef.current || triggeredRef.current) return;
        triggeredRef.current = true;
        ctx.startDragging(task, e.nativeEvent);
        options?.onCommit?.(task);
        setIsPressing(false);
        setProgress(0);
      }, LONG_PRESS_MS);
    },
    [ctx, options, task],
  );

  const onPointerMove = useCallback((e: React.PointerEvent<HTMLElement>) => {
    if (!startRef.current || triggeredRef.current) return;
    const dx = e.clientX - startRef.current.x;
    const dy = e.clientY - startRef.current.y;
    if (Math.hypot(dx, dy) > MOVE_TOLERANCE_PX) {
      cleanup();
    }
  }, [cleanup]);

  const onPointerUp = useCallback(() => {
    if (triggeredRef.current) {
      // 已经进入拖拽 mode，由 Context 全局处理 pointerup
      return;
    }
    cleanup();
  }, [cleanup]);

  const onPointerCancel = useCallback(() => cleanup(), [cleanup]);

  return {
    longPressProps: {
      onPointerDown,
      onPointerMove,
      onPointerUp,
      onPointerCancel,
    },
    isPressing,
    progress,
  };
}

/**
 * Wrapper 组件 · 给任务行包一层，在内部调 useLongPressDrag。
 * 用法：
 *   <TaskRowLongPress task={task}>
 *     <div className="...原任务卡...">...</div>
 *   </TaskRowLongPress>
 * 进入拖拽态后会自动显示倒计时圆圈。
 */
export function TaskRowLongPress({
  task,
  children,
  disabled,
}: {
  task: Task;
  children: React.ReactNode;
  disabled?: boolean;
}) {
  const { longPressProps, progress, isPressing } = useLongPressDrag(task, { disabled });
  return (
    <div
      {...longPressProps}
      style={{
        position: 'relative',
        touchAction: 'manipulation',
        // 长按时给整张卡微微缩放反馈
        transform: progress > 0 && progress < 1 ? `scale(${1 - progress * 0.015})` : 'scale(1)',
        transition: 'transform 80ms ease',
        userSelect: isPressing ? 'none' : 'auto',
      }}
    >
      <LongPressProgressRing progress={progress} />
      {children}
    </div>
  );
}

/**
 * 倒计时进度圆圈（叠在任务卡左上角，长按时显示）
 */
export function LongPressProgressRing({ progress, size = 18 }: { progress: number; size?: number }) {
  if (progress <= 0) return null;
  const stroke = 2;
  const r = (size - stroke) / 2;
  const cx = size / 2;
  const cy = size / 2;
  const circumference = 2 * Math.PI * r;
  const offset = circumference * (1 - progress);
  return (
    <svg
      width={size}
      height={size}
      style={{
        position: 'absolute',
        top: 4,
        left: 4,
        pointerEvents: 'none',
        zIndex: 10,
      }}
    >
      <circle cx={cx} cy={cy} r={r} fill="none" stroke="rgba(91,123,254,0.15)" strokeWidth={stroke} />
      <circle
        cx={cx}
        cy={cy}
        r={r}
        fill="none"
        stroke="#5B7BFE"
        strokeWidth={stroke}
        strokeDasharray={circumference}
        strokeDashoffset={offset}
        strokeLinecap="round"
        transform={`rotate(-90 ${cx} ${cy})`}
        style={{ transition: 'stroke-dashoffset 16ms linear' }}
      />
    </svg>
  );
}
