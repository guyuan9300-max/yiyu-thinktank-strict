export type MiniWindowWorkArea = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type MiniWindowBounds = MiniWindowWorkArea;

const MINI_WINDOW_WIDTH = 420;
const MINI_WINDOW_EDGE_GAP = 12;
const MINI_WINDOW_HEIGHT_RATIO = 2 / 3;

function normalizeDimension(value: number): number {
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error('屏幕工作区尺寸无效');
  }
  return Math.max(1, Math.round(value));
}

function normalizeCoordinate(value: number): number {
  if (!Number.isFinite(value)) {
    throw new Error('屏幕工作区坐标无效');
  }
  return Math.round(value);
}

/**
 * 把迷你任务看板放进当前显示器的可用工作区。
 *
 * 常规屏幕保留 12px 呼吸边距并贴左，纵向占缩小前主窗口高度的约三分之二；
 * 极窄屏幕则以当前显示器工作区为上限，
 * 优先保证窗口完整可见，避免固定宽度把关闭按钮或任务内容挤出屏幕。
 */
export function buildMiniWindowBounds(
  workArea: MiniWindowWorkArea,
  sourceWindowHeight: number,
): MiniWindowBounds {
  const areaX = normalizeCoordinate(workArea.x);
  const areaY = normalizeCoordinate(workArea.y);
  const areaWidth = normalizeDimension(workArea.width);
  const areaHeight = normalizeDimension(workArea.height);
  const sourceHeight = normalizeDimension(sourceWindowHeight);
  const horizontalGap = areaWidth >= MINI_WINDOW_WIDTH + MINI_WINDOW_EDGE_GAP * 2
    ? MINI_WINDOW_EDGE_GAP
    : 0;
  const verticalGap = areaHeight > MINI_WINDOW_EDGE_GAP * 2
    ? MINI_WINDOW_EDGE_GAP
    : 0;
  const availableHeight = areaHeight - verticalGap * 2;
  const preferredHeight = Math.round(sourceHeight * MINI_WINDOW_HEIGHT_RATIO);

  return {
    x: areaX + horizontalGap,
    y: areaY + verticalGap,
    width: Math.max(1, Math.min(MINI_WINDOW_WIDTH, areaWidth - horizontalGap * 2)),
    height: Math.max(1, Math.min(availableHeight, preferredHeight)),
  };
}
