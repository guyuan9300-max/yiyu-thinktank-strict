import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { MarkdownAnswerDocument } from '../src/renderer/components/workbench/MarkdownAnswerDocument';

function render(markdown: string) {
  return renderToStaticMarkup(<MarkdownAnswerDocument text={markdown} />);
}

test('keeps one ordered list around nested bullets and never promotes the last item to a heading', () => {
  const html = render(`1. **会员关系系统**：第一项
   - 关键矛盾：甲
   - 设计要点：乙
2. **供给与合作网络**：第二项
   - 关键矛盾：丙
3. **AI经营系统**：第三项`);

  assert.equal((html.match(/<ol/g) || []).length, 1);
  assert.equal((html.match(/<ul/g) || []).length, 2);
  assert.ok(html.includes('会员关系系统'));
  assert.ok(html.includes('供给与合作网络'));
  assert.ok(html.includes('AI经营系统'));
  assert.ok(!/<h[1-6][^>]*>[^<]*AI经营系统/.test(html));
});

test('only explicit markdown headings become headings', () => {
  const html = render(`普通短句

3. “合作方向+合作案例式”招商版

## 明确标题`);

  assert.ok(html.includes('<p'));
  assert.equal((html.match(/<h2/g) || []).length, 1);
  assert.ok(html.includes('明确标题'));
});

test('renders GFM tables, links, task lists and inline code without leaking markdown markers', () => {
  const html = render(`| 项目 | 状态 |
| --- | --- |
| [官网](https://example.com) | \`ready\` |

- [x] 已完成`);

  assert.ok(html.includes('<table'));
  assert.ok(html.includes('href="https://example.com"'));
  assert.ok(html.includes('<code'));
  assert.ok(html.includes('type="checkbox"'));
});

test('renders incomplete streaming markdown without throwing or inventing a heading', () => {
  const html = render('1. 正在生成\n   - **尚未闭合');
  assert.ok(html.includes('<ol'));
  assert.ok(!html.includes('<h2'));
});
