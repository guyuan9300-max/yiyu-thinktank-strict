import test from 'node:test';
import assert from 'node:assert/strict';

import {
  cleanChatOutput,
  getWorkspaceMaterialNotice,
  stripFileCitations,
  stripGlossaryCitations,
} from './workspaceChatPresentation.js';

test('material notice recognizes strict local_original sources', () => {
  const notice = getWorkspaceMaterialNotice({
    materialAccessMode: 'local_original',
    selectedDocumentContentCount: 1,
    sourceCount: 1,
    memoryState: 'ready',
  });
  assert.equal(notice.hasMaterial, true);
  assert.equal(notice.tone, 'blue');
  assert.match(notice.sourceNotice, /已读取 1 份本机资料/);
  assert.equal(notice.memoryNotice, null);
});

test('material notice distinguishes organization-memory failure from no material', () => {
  const notice = getWorkspaceMaterialNotice({
    materialAccessMode: 'local_original',
    selectedDocumentContentCount: 2,
    memoryState: 'failed_retryable',
  });
  assert.equal(notice.hasMaterial, true);
  assert.match(notice.sourceNotice, /已读取 2 份本机资料/);
  assert.match(notice.memoryNotice || '', /记忆沉淀中/);
});

test('material notice reports recalled local project memory as a real source', () => {
  const notice = getWorkspaceMaterialNotice({
    materialAccessMode: 'memory_context',
    localMemoryCount: 2,
    sourceCount: 2,
    memoryState: 'ready',
  });
  assert.equal(notice.hasMaterial, true);
  assert.equal(notice.tone, 'blue');
  assert.match(notice.sourceNotice, /2 条当前项目的本机记忆/);
});

test('material notice only warns about general knowledge when no source exists', () => {
  const notice = getWorkspaceMaterialNotice({
    materialAccessMode: 'none',
    sourceCount: 0,
  });
  assert.equal(notice.hasMaterial, false);
  assert.equal(notice.tone, 'amber');
  assert.match(notice.sourceNotice, /没有命中可用的本机资料或组织共享知识/);
});

test('stripFileCitations returns empty for nullish', () => {
  assert.equal(stripFileCitations(null), '');
  assert.equal(stripFileCitations(undefined), '');
  assert.equal(stripFileCitations(''), '');
});

test('stripFileCitations removes English parenthetical (见 strategy.md)', () => {
  const input = '客户在赛道里走"心智素养"路线 (见 strategy.md), 这是核心定位。';
  const out = stripFileCitations(input);
  assert.equal(out, '客户在赛道里走"心智素养"路线, 这是核心定位。');
});

test('stripFileCitations removes Chinese full-width parenthetical （来源: strategy.md, methodology.md）', () => {
  const input = '基于四级飞轮的扩散通路（来源: strategy.md, methodology.md）展开。';
  const out = stripFileCitations(input);
  assert.equal(out, '基于四级飞轮的扩散通路展开。');
});

test('stripFileCitations removes square-bracket citations [strategy.md]', () => {
  const input = '客户当前阶段是规模化复制[strategy.md]。';
  const out = stripFileCitations(input);
  assert.equal(out, '客户当前阶段是规模化复制。');
});

test('stripFileCitations rewrites bare strategy.md → 战略文档', () => {
  const input = '根据 strategy.md 描述, 客户走县域路线。';
  const out = stripFileCitations(input);
  assert.equal(out, '根据 战略文档 描述, 客户走县域路线。');
});

test('stripFileCitations rewrites bare methodology.md → 方法论文档', () => {
  const input = '方法论按 methodology.md 的四级飞轮展开。';
  const out = stripFileCitations(input);
  assert.equal(out, '方法论按 方法论文档 的四级飞轮展开。');
});

test('stripFileCitations rewrites unknown filenames to 客户资料', () => {
  const input = '在 survey.docx 和 notes.pdf 提到过客户访谈结论。';
  const out = stripFileCitations(input);
  assert.equal(out, '在 客户资料 和 客户资料 提到过客户访谈结论。');
});

test('stripFileCitations preserves text without any citations', () => {
  const input = '客户战略很清楚, 走县域路线, 不走 KOL 路线。';
  const out = stripFileCitations(input);
  assert.equal(out, input);
});

test('stripFileCitations does not collapse legitimate parentheticals without filenames', () => {
  const input = '客户走县域路线 (而非 KOL 路线), 这是核心差异。';
  const out = stripFileCitations(input);
  assert.equal(out, '客户走县域路线 (而非 KOL 路线), 这是核心差异。');
});

test('stripFileCitations handles multi-citation paragraph end-to-end', () => {
  const input = '基于 strategy.md、methodology.md 推出 (见 strategy.md), 客户走"心智素养"路线。';
  const out = stripFileCitations(input);
  // strategy.md / methodology.md 替换 + (见 strategy.md) 整段删
  assert.equal(out, '基于 战略文档、方法论文档 推出, 客户走"心智素养"路线。');
});

test('stripFileCitations trims edges and caps blank lines at \\n\\n', () => {
  const input = '   客户走县域路线。   \n\n\n\n   不走 KOL 路线。   ';
  const out = stripFileCitations(input);
  // 头尾 trim, 内部连续空行折叠到 \n\n (行间空格保留无害)
  assert.match(out, /^客户走县域路线。/);
  assert.match(out, /不走 KOL 路线。$/);
  assert.ok(!/\n\n\n/.test(out), `expected ≤2 newlines in a row, got: ${JSON.stringify(out)}`);
});

// ────────────────────────────────────────────────────────────
// stripGlossaryCitations — 删字典 📚 / ⚠️ 引证标记
// ────────────────────────────────────────────────────────────

test('stripGlossaryCitations returns empty for nullish', () => {
  assert.equal(stripGlossaryCitations(null), '');
  assert.equal(stripGlossaryCitations(undefined), '');
  assert.equal(stripGlossaryCitations(''), '');
});

test('stripGlossaryCitations removes standard [📚 term.attribute] marker', () => {
  const input = '测试项目A累计服务近 17 万名大学生 [📚 测试项目A.累计服务大学生数]，覆盖高校同辈互助。';
  const out = stripGlossaryCitations(input);
  assert.equal(out, '测试项目A累计服务近 17 万名大学生，覆盖高校同辈互助。');
});

test('stripGlossaryCitations removes compact form without space', () => {
  const input = '测试机构A2013年12月成立[📚测试机构A.成立时间]，定位前置型心育。';
  const out = stripGlossaryCitations(input);
  assert.equal(out, '测试机构A2013年12月成立，定位前置型心育。');
});

test('stripGlossaryCitations removes mid-dot separator variant', () => {
  const input = '覆盖儿童 90 万名 [📚 测试项目C · 服务对象与规模]，2024 年扩展到 31 省。';
  const out = stripGlossaryCitations(input);
  assert.equal(out, '覆盖儿童 90 万名，2024 年扩展到 31 省。');
});

test('stripGlossaryCitations removes multiple consecutive citations', () => {
  const input = '关键数据 [📚 测试项目A.累计服务人数][📚 测试项目C.服务对象与规模] 来源可溯。';
  const out = stripGlossaryCitations(input);
  assert.equal(out, '关键数据 来源可溯。');
});

test('stripGlossaryCitations removes ⚠️ invalid-citation placeholder from validator', () => {
  const input = '该数字为 200 万 [⚠️ 引用失效：「鲁冰花舍.2023支出」不在字典 verified 列表，请在字典审核此项]，需复核。';
  const out = stripGlossaryCitations(input);
  assert.equal(out, '该数字为 200 万，需复核。');
});

test('stripGlossaryCitations preserves text without any markers', () => {
  const input = '测试机构A专注青少年心理健康教育，三大业务板块清晰。';
  const out = stripGlossaryCitations(input);
  assert.equal(out, input);
});

test('stripGlossaryCitations does not touch file citations', () => {
  // 不重叠职责:文件名引证由 stripFileCitations 管,这里只管 📚
  const input = '客户战略 [📚 测试机构A.战略主线] 跟 strategy.md 描述一致。';
  const out = stripGlossaryCitations(input);
  // 📚 没了,但 strategy.md 还在(应由 stripFileCitations 接力)
  assert.equal(out, '客户战略 跟 strategy.md 描述一致。');
});

// ────────────────────────────────────────────────────────────
// cleanChatOutput — 组合入口
// ────────────────────────────────────────────────────────────

test('cleanChatOutput strips both file and glossary citations end-to-end', () => {
  const input = '基于 strategy.md, 测试机构A 2013 年成立 [📚 测试机构A.成立时间], 三大业务板块 (见 methodology.md)。';
  const out = cleanChatOutput(input);
  // strategy.md → 战略文档, (见 methodology.md) 整段删, [📚...] 删
  assert.equal(out, '基于 战略文档, 测试机构A 2013 年成立, 三大业务板块。');
});

test('cleanChatOutput handles nullish', () => {
  assert.equal(cleanChatOutput(null), '');
  assert.equal(cleanChatOutput(undefined), '');
});
