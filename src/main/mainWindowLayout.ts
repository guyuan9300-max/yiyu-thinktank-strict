export type MainWindowSize = {
  width: number;
  height: number;
};

export type MainWindowBounds = MainWindowSize & {
  x: number;
  y: number;
};

export const MAIN_WINDOW_ASPECT_RATIO = 16 / 10;
export const MAIN_WINDOW_DEFAULT_SIZE: MainWindowSize = Object.freeze({
  width: 1440,
  height: 900,
});
export const MAIN_WINDOW_MINIMUM_SIZE: MainWindowSize = Object.freeze({
  width: 1440,
  height: 900,
});

function normalizeDimension(value: number, label: string): number {
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error(`${label}无效`);
  }
  return Math.max(1, Math.round(value));
}

function normalizeCoordinate(value: number, label: string): number {
  if (!Number.isFinite(value)) {
    throw new Error(`${label}无效`);
  }
  return Math.round(value);
}

function fitAspectRatioInside(size: MainWindowSize): MainWindowSize {
  if (size.width / size.height > MAIN_WINDOW_ASPECT_RATIO) {
    return {
      width: Math.floor(size.height * MAIN_WINDOW_ASPECT_RATIO),
      height: size.height,
    };
  }
  return {
    width: size.width,
    height: Math.floor(size.width / MAIN_WINDOW_ASPECT_RATIO),
  };
}

/**
 * Full workspace layout contract.
 *
 * Normal displays keep a 1440×900 minimum. If the physical work area is
 * smaller, the largest visible 16:10 rectangle becomes the effective minimum
 * so macOS never places the title bar or primary actions outside the display.
 */
export function resolveMainWindowMinimumSize(
  workArea: MainWindowSize,
): MainWindowSize {
  const normalizedArea = {
    width: normalizeDimension(workArea.width, '屏幕工作区宽度'),
    height: normalizeDimension(workArea.height, '屏幕工作区高度'),
  };
  if (
    normalizedArea.width >= MAIN_WINDOW_MINIMUM_SIZE.width
    && normalizedArea.height >= MAIN_WINDOW_MINIMUM_SIZE.height
  ) {
    return MAIN_WINDOW_MINIMUM_SIZE;
  }
  return fitAspectRatioInside(normalizedArea);
}

/**
 * Restore a full workspace without carrying a narrow or flattened pre-mini
 * shape back into the renderer. The function only expands the requested size
 * to reach 16:10, then caps it to the current display work area.
 */
export function normalizeMainWindowBounds(
  requestedBounds: MainWindowBounds,
  workArea: MainWindowBounds,
): MainWindowBounds {
  const requested = {
    x: normalizeCoordinate(requestedBounds.x, '窗口横坐标'),
    y: normalizeCoordinate(requestedBounds.y, '窗口纵坐标'),
    width: normalizeDimension(requestedBounds.width, '窗口宽度'),
    height: normalizeDimension(requestedBounds.height, '窗口高度'),
  };
  const area = {
    x: normalizeCoordinate(workArea.x, '屏幕工作区横坐标'),
    y: normalizeCoordinate(workArea.y, '屏幕工作区纵坐标'),
    width: normalizeDimension(workArea.width, '屏幕工作区宽度'),
    height: normalizeDimension(workArea.height, '屏幕工作区高度'),
  };
  const maximum = fitAspectRatioInside(area);
  const minimum = resolveMainWindowMinimumSize(area);

  let width = Math.max(requested.width, minimum.width);
  let height = Math.max(requested.height, minimum.height);
  if (width / height > MAIN_WINDOW_ASPECT_RATIO) {
    height = Math.ceil(width / MAIN_WINDOW_ASPECT_RATIO);
  } else {
    width = Math.ceil(height * MAIN_WINDOW_ASPECT_RATIO);
  }

  if (width > maximum.width || height > maximum.height) {
    width = maximum.width;
    height = maximum.height;
  }

  const requestedCenterX = requested.x + requested.width / 2;
  const requestedCenterY = requested.y + requested.height / 2;
  const maxX = area.x + area.width - width;
  const maxY = area.y + area.height - height;

  return {
    x: Math.min(maxX, Math.max(area.x, Math.round(requestedCenterX - width / 2))),
    y: Math.min(maxY, Math.max(area.y, Math.round(requestedCenterY - height / 2))),
    width,
    height,
  };
}
