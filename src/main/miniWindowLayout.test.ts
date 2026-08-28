import assert from 'node:assert/strict';
import test from 'node:test';
import { buildMiniWindowBounds } from './miniWindowLayout.js';

test('迷你任务看板贴在当前屏幕左侧并占约三分之二原窗高', () => {
  assert.deepEqual(
    buildMiniWindowBounds({ x: 0, y: 25, width: 1440, height: 875 }, 900),
    { x: 12, y: 37, width: 420, height: 600 },
  );
});

test('迷你任务看板遵守副屏坐标和系统工作区边界', () => {
  assert.deepEqual(
    buildMiniWindowBounds({ x: -1920, y: 0, width: 1920, height: 1055 }, 900),
    { x: -1908, y: 12, width: 420, height: 600 },
  );
});

test('窄小屏幕会收缩边距和尺寸，窗口不会跑出可用区域', () => {
  assert.deepEqual(
    buildMiniWindowBounds({ x: 80, y: 24, width: 370, height: 500 }, 900),
    { x: 80, y: 36, width: 370, height: 476 },
  );
});

test('无效的小数工作区会被整理为可用的整数窗口边界', () => {
  assert.deepEqual(
    buildMiniWindowBounds({ x: 10.6, y: 20.2, width: 1024.8, height: 768.9 }, 900),
    { x: 23, y: 32, width: 420, height: 600 },
  );
});
