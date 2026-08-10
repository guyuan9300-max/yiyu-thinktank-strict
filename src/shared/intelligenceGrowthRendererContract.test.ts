import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const intelligenceSource = readFileSync(
  new URL(
    '../renderer/components/intelligence/IntelligenceStationView.tsx',
    import.meta.url,
  ),
  'utf8',
);
const growthSource = readFileSync(
  new URL(
    '../renderer/components/handbook/GrowthCenterView.tsx',
    import.meta.url,
  ),
  'utf8',
);

test('brand strategy confirmation and editing use the strict update command', () => {
  assert.match(intelligenceSource, /updateBrandStrategyExtract/);
  assert.match(intelligenceSource, /saveConfirmedExtract/);
  assert.match(intelligenceSource, /['"]咨询师确认['"]/);
  assert.match(intelligenceSource, />\s*手动编辑\s*</);
  assert.doesNotMatch(intelligenceSource, /咨询师确认（blocked）/);
  assert.doesNotMatch(intelligenceSource, /手动编辑（blocked）/);
});

test('reachable growth badge failures remain distinct from an empty board', () => {
  assert.match(growthSource, /setLoadError\(/);
  assert.match(growthSource, /成长徽章加载失败/);
  assert.match(growthSource, /onClick=\{\(\) => void loadBadges\(\)\}/);
  assert.match(growthSource, />\s*重试\s*</);
});
