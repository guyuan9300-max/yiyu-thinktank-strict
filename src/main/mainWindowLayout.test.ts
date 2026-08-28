import assert from 'node:assert/strict';
import test from 'node:test';

import {
  MAIN_WINDOW_ASPECT_RATIO,
  MAIN_WINDOW_DEFAULT_SIZE,
  MAIN_WINDOW_MINIMUM_SIZE,
  normalizeMainWindowBounds,
} from './mainWindowLayout.js';

test('完整主界面使用 16:10，并以 1440×900 作为默认尺寸与安全下限', () => {
  assert.equal(MAIN_WINDOW_ASPECT_RATIO, 16 / 10);
  assert.deepEqual(MAIN_WINDOW_DEFAULT_SIZE, { width: 1440, height: 900 });
  assert.deepEqual(MAIN_WINDOW_MINIMUM_SIZE, { width: 1440, height: 900 });
});

test('从旧的过窄主窗口恢复时提升到安全下限并保持原中心位置', () => {
  assert.deepEqual(
    normalizeMainWindowBounds(
      { x: 180, y: 100, width: 1080, height: 700 },
      { x: 0, y: 25, width: 1728, height: 1080 },
    ),
    { x: 0, y: 25, width: 1440, height: 900 },
  );
});

test('高瘦窗口恢复时扩展宽度，避免主页面文字被压缩', () => {
  assert.deepEqual(
    normalizeMainWindowBounds(
      { x: 100, y: 50, width: 1200, height: 800 },
      { x: 0, y: 25, width: 1728, height: 1080 },
    ),
    { x: 0, y: 25, width: 1440, height: 900 },
  );
});

test('宽扁窗口恢复时扩展高度，避免正文行高和工具区比例失衡', () => {
  assert.deepEqual(
    normalizeMainWindowBounds(
      { x: 100, y: 50, width: 1440, height: 760 },
      { x: 0, y: 25, width: 1728, height: 1080 },
    ),
    { x: 100, y: 25, width: 1440, height: 900 },
  );
});

test('小屏幕以可用工作区为上限，仍保持 16:10 且不把窗口推出屏幕', () => {
  assert.deepEqual(
    normalizeMainWindowBounds(
      { x: 50, y: 40, width: 1080, height: 700 },
      { x: 0, y: 25, width: 1152, height: 720 },
    ),
    { x: 0, y: 25, width: 1152, height: 720 },
  );
});
