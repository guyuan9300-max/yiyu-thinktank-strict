/**
 * AI 工作指令 · 解析 + 模块能力 Manifest + 类型
 *
 * 顾源源 5/24 任务书 §7-8: 智能指令解析 + 复杂任务类型分类
 *
 * B 角色: 前端解析层 (轻量规则 + LLM 辅助, 不在 prompt 写死流程)
 */

export type AICommandMode = 'quick_task' | 'ai_command';

export type AICommandIntent =
  | 'quick_task'
  | 'generate_client_background_document'
  | 'generate_report_outline'
  | 'review_today_tasks_for_ai'
  | 'summarize_client_state'
  | 'resolve_clarifications_plan'
  | 'unknown';

export type ParsedSmartCommand = {
  mode: AICommandMode;
  bot_handle: string | null;
  /**
   * 主客户 (任务真归属). 安全边界 — 每个任务以客户隔离, 不能为空 (UI 强制选).
   * 解析时取"出现次数最多 + 不在参考前缀后" 的候选.
   */
  client_name: string | null;
  /**
   * 参考客户 (合同/案例样本来源, 不是主客户).
   * 例: "参考测试机构A的合同结构" → references=['测试机构A'], 不是 client_name.
   */
  client_references: string[];
  intent: AICommandIntent;
  requested_outputs: string[];
  target_workspace: string | null;
  inline_authorization_detected: boolean;
  inline_authorization_text: string | null;
  requires_plan: boolean;
  original_text: string;
  /**
   * 顾源源 5/25 真用反馈: "我理解的任务" 不能是一坨, 要 step list,
   * 每 step 三段式 (做什么 / 基于什么 / 交付什么), 用户一眼看出 AI 理解对不对.
   * 解析失败时为空数组, UI 退回显示 original_text 摘要.
   */
  steps: UnderstoodStep[];
};

/**
 * 三段式步骤 — 让用户可视化"庆华是否理解了任务".
 *  · action:      庆华要做的动作 (动词短语, 比如"写一份背景档案")
 *  · basis:       庆华做这一步基于的输入 (虚构客户设定 / 参考 / 前置 step 产出)
 *  · deliverable: 交付物 + 落点 (篇幅/位置, 比如"~10000 字, 放进客户工作台")
 * 三段都允许"未识别"(空字符串), UI 显示灰字"(未识别, 庆华可能误解)".
 */
export type UnderstoodStep = {
  index: number;
  raw_text: string;
  action: string;
  basis: string;
  deliverable: string;
};

// 复杂任务特征关键词
const COMPLEX_TASK_KEYWORDS = [
  '帮我完成', '帮我生成', '生成报告', '写方案', '做分析', '扫描资料',
  '调用资讯', '放进工作台', '生成复盘', '帮我推进', '为某个客户',
  '集团介绍', '会谈准备', '品牌报告', '理事会说明',
];

// inline authorization 关键词
const INLINE_AUTH_PATTERNS: { re: RegExp; text: string }[] = [
  { re: /不用审批[,，]?\s*直接执行/, text: '不用审批, 直接执行' },
  { re: /直接开始/, text: '直接开始' },
  { re: /我授权你执行第一步/, text: '我授权你执行第一步' },
  { re: /不用再问我[,，]?\s*先做/, text: '不用再问我, 先做' },
  { re: /按我的授权先开始/, text: '按我的授权先开始' },
  { re: /按我的授权先执行/, text: '按我的授权先执行' },
];

// intent 识别规则 (轻量, 不写死单一场景)
function classifyIntent(text: string): AICommandIntent {
  if (/(集团介绍|背景资料|公司介绍|机构介绍)/.test(text)) {
    return 'generate_client_background_document';
  }
  if (/(报告.{0,5}提纲|提纲|outline)/.test(text)) {
    return 'generate_report_outline';
  }
  if (/(今天.{0,8}(任务|待办)|哪些.{0,5}(可以|能).{0,5}(做|接|交))/.test(text)) {
    return 'review_today_tasks_for_ai';
  }
  if (/(客户.{0,10}状态|总结.{0,10}客户|客户.{0,10}摘要)/.test(text)) {
    return 'summarize_client_state';
  }
  if (/(待澄清|待确认.{0,10}事项|澄清.{0,10}处理)/.test(text)) {
    return 'resolve_clarifications_plan';
  }
  return 'unknown';
}

// @ 机器人识别 (支持 @庆华 / @庆华 / @qinghua)
function extractBotHandle(text: string): string | null {
  const match = text.match(/@([一-龥a-zA-Z0-9_]+)/);
  return match ? match[1] : null;
}

// 顾源源 5/25 真用 bug: "参考测试机构A的合同结构" 把测试机构A识别成主客户.
// 真主客户是安然集团 (虚构, 但在 knownClientNames). 测试机构A只是参考样本.
// 安全边界: 每个任务以客户隔离, 误识别 → 跨客户污染.
const REFERENCE_PREFIX_RE = /(?:参考|对照|类似|像|按照|借鉴|仿照|比照|借用|套用)\s*$/;

function extractClientNames(
  text: string,
  candidates: string[],
): { primary: string | null; references: string[] } {
  type Hit = { name: string; positions: number[]; isAllReference: boolean };
  const hits: Hit[] = [];
  for (const name of candidates) {
    if (!name) continue;
    const positions: number[] = [];
    let pos = 0;
    while ((pos = text.indexOf(name, pos)) !== -1) {
      positions.push(pos);
      pos += name.length;
    }
    if (positions.length === 0) continue;
    // 所有命中位置前 15 字符都是 "参考/对照/类似/..." → 整体是参考客户
    const isAllReference = positions.every((p) => {
      const before = text.slice(Math.max(0, p - 15), p);
      return REFERENCE_PREFIX_RE.test(before);
    });
    hits.push({ name, positions, isAllReference });
  }
  // 主客户: 非全参考 + 出现次数最多 (相同次数取候选数组先后)
  const primaryCandidates = hits
    .filter((h) => !h.isAllReference)
    .sort((a, b) => b.positions.length - a.positions.length);
  const primary = primaryCandidates[0]?.name || null;
  const references = hits.filter((h) => h.isAllReference).map((h) => h.name);
  return { primary, references };
}

// 顾源源 5/25 真用 bug: extractOutputs 抓到了元话语 + 双冒号 + 重复.
// 真截图问题:
//   · "完整一套接触材料" / "下面这5份文档+1个会议任务" — 用户的总览句, 不是单个产出
//   · "任务::5月27日下午2点" — 双冒号 (m[2] 以 ":" 开头, 我又拼了 ": ")
//   · "一个任务:5月27日下午2点" — pattern 7 + 8 都命中同一片段, 重复
const META_OUTPUT_TOKENS = [
  '完整一套', '完整套', '下面这', '一系列', '若干份', '多份', '几份',
  '所有', '全套', '每一份', '每份', '一整套', '完整',
];

function extractOutputs(text: string): string[] {
  const outputs: string[] = [];
  // 顾源源 5/24 反馈: 真用指令里有"写一份/拟一份/建一个任务/给我建/做一份"等.
  const patterns: Array<{ re: RegExp; kind: 'simple' | 'build_task' | 'build_generic' }> = [
    { re: /生成一份(.{2,40}?)(?:[,，。.;；]|$)/g, kind: 'simple' },
    { re: /帮我写(.{2,40}?)(?:[,，。.;；]|$)/g, kind: 'simple' },
    { re: /做一份(.{2,40}?)(?:[,，。.;；]|$)/g, kind: 'simple' },
    { re: /写一份(.{2,40}?)(?:[,，。.;；]|$)/g, kind: 'simple' },
    { re: /拟一份(.{2,40}?)(?:[,，。.;；]|$)/g, kind: 'simple' },
    { re: /起草一份(.{2,40}?)(?:[,，。.;；]|$)/g, kind: 'simple' },
    { re: /(?:给我|帮我)?建一个(任务|会议|事项|提醒|日程)(.{0,40}?)(?:[,，。.;；]|$)/g, kind: 'build_task' },
    // pattern 8 加 negative lookahead: 跳过"建一个任务/会议/..." (pattern 7 已覆盖)
    { re: /(?:给我|帮我)建立?(?!一个(?:任务|会议|事项|提醒|日程))(.{2,40}?)(?:[,，。.;；]|$)/g, kind: 'build_generic' },
  ];

  patterns.forEach(({ re, kind }) => {
    let m;
    while ((m = re.exec(text)) !== null) {
      let o: string;
      if (kind === 'build_task') {
        // m[1]=任务|会议|... , m[2]=后续描述. m[2] 可能以 ":,;" 开头 → strip 防双冒号.
        const cleanM2 = (m[2] || '').replace(/^[\s::,，。.;；、]+/, '').trim();
        o = cleanM2 ? `${m[1]}: ${cleanM2}` : m[1];
      } else {
        o = (m[1] || '').trim();
      }
      // META 排除 (元话语 / 总览句)
      if (META_OUTPUT_TOKENS.some((t) => o.includes(t))) continue;
      if (o.length < 2) continue;
      if (outputs.includes(o)) continue;
      outputs.push(o);
    }
  });
  return outputs;
}

function extractTargetWorkspace(text: string): string | null {
  if (/客户工作台/.test(text)) return '客户工作台';
  if (/战略陪伴/.test(text)) return '战略陪伴';
  if (/资讯情报站/.test(text)) return '资讯情报站';
  if (/任务与日程/.test(text)) return '任务与日程';
  return null;
}

// ── 顾源源 5/25 真用反馈: step 分段 + 三段式提取 ─────────────
// "我理解的任务" 不能整段堆, 用户要看到 AI 把任务拆成几步, 每步:
//   做什么 / 基于什么 / 交付什么 — 三项缺一项就标"未识别".

const STEP_HEAD_RE =
  /(?:^|\n)\s*(?:第([一二三四五六七八九十]+)[,，、\.]?|([0-9]+)[、\.])\s*/g;

const CN_NUMERALS: Record<string, number> = {
  一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10,
};

function splitTextIntoSteps(text: string): { index: number; raw: string }[] {
  // 收集所有 "第一/第二/.../1./2." 头的位置
  const heads: { index: number; pos: number }[] = [];
  let m: RegExpExecArray | null;
  STEP_HEAD_RE.lastIndex = 0;
  while ((m = STEP_HEAD_RE.exec(text)) !== null) {
    const cn = m[1];
    const arabic = m[2];
    const idx = cn ? CN_NUMERALS[cn] || heads.length + 1 : parseInt(arabic, 10);
    if (!Number.isFinite(idx) || idx < 1 || idx > 20) continue;
    heads.push({ index: idx, pos: m.index + (m[0].match(/^\s*\n?\s*/)?.[0].length ?? 0) });
  }
  if (heads.length === 0) return [];
  // 按 pos 切片
  const out: { index: number; raw: string }[] = [];
  for (let i = 0; i < heads.length; i++) {
    const start = heads[i].pos;
    const end = i + 1 < heads.length ? heads[i + 1].pos : text.length;
    const raw = text.slice(start, end).trim();
    if (raw.length >= 4) out.push({ index: heads[i].index, raw });
  }
  return out;
}

function extractAction(raw: string): string {
  // 去掉序号头 "第X," / "X." → 取第一句 (到第一个 "," "." ",." 或 30 字)
  const cleaned = raw.replace(/^\s*(?:第[一二三四五六七八九十]+[,，、\.]?|[0-9]+[、\.])\s*/, '');
  // 找第一个动词短语 (写一份/拟一份/起草一份/帮我写/做一份/建一个/给我建/放进/参考)
  const verbMatch = cleaned.match(
    /^[^,，。.;；]{0,60}?(?:写一份|拟一份|起草一份|帮我写|做一份|建一个|给我建|生成一份)[^,，。.;；]{0,40}/,
  );
  if (verbMatch) return verbMatch[0].trim();
  // 退而求其次, 取第一句 (到第一个标点)
  const first = cleaned.match(/^[^,，。.;；\n]{2,80}/);
  return first ? first[0].trim() : cleaned.slice(0, 60).trim();
}

function extractBasis(raw: string): string {
  // 找 "参考/基于/根据/按...的口径" 后的描述
  const patterns = [
    /(?:参考|基于|根据|按照)([^,，。.;；\n]{2,50})/,
    /(?:覆盖|包含|包括)[:：]?\s*([^。\n]{4,80})/,
    /(预算先按[^,，。.;；\n]{2,40})/,
  ];
  for (const p of patterns) {
    const m = raw.match(p);
    if (m) return m[1].trim();
  }
  return '';
}

function extractDeliverable(raw: string): string {
  // 找篇幅 + 落点
  // 篇幅: "大概 X 字" / "约 X 字" / "X-Y 字" / "X 字"
  const sizeMatch = raw.match(/(?:大概|约|大约)?\s*([0-9]{3,5})(?:[-–至]([0-9]{3,5}))?\s*字/);
  const size = sizeMatch ? (sizeMatch[2] ? `~${sizeMatch[1]}-${sizeMatch[2]} 字` : `~${sizeMatch[1]} 字`) : '';
  // 落点: "放进 X" / "进我审批" / "建到我日程" / "起草完进我审批"
  const placeMatch = raw.match(/(?:放进|建到|写回)([^,，。.;；\n]{2,30})/);
  const reviewMatch = /进我审批|做完进我审批|完成后进我审批|起草完进我审批/.test(raw)
    ? '完成进我审批'
    : '';
  const directMatch = /(?:直接建|直接执行|不(?:需要)?审批)/.test(raw) ? '直接执行' : '';
  const parts = [size, placeMatch ? `落点: ${placeMatch[1].trim()}` : '', reviewMatch, directMatch]
    .filter(Boolean);
  return parts.join(' · ');
}

export function decomposeSteps(text: string): UnderstoodStep[] {
  const raws = splitTextIntoSteps(text);
  if (raws.length === 0) return [];
  return raws.map((r) => ({
    index: r.index,
    raw_text: r.raw,
    action: extractAction(r.raw),
    basis: extractBasis(r.raw),
    deliverable: extractDeliverable(r.raw),
  }));
}

/**
 * 解析智能指令文本
 *
 * @param text 用户输入原文
 * @param knownClientNames 已知客户名列表 (从 V2.1 lab db 拉, 用于匹配)
 */
export function parseSmartCommand(
  text: string,
  knownClientNames: string[] = [],
): ParsedSmartCommand {
  const botHandle = extractBotHandle(text);
  const hasComplexKeyword = COMPLEX_TASK_KEYWORDS.some((kw) => text.includes(kw));
  const intent = classifyIntent(text);

  // mode 判断: 有 @机器人 或 complex keyword 或 intent != unknown → ai_command
  const mode: AICommandMode =
    botHandle || hasComplexKeyword || intent !== 'unknown' ? 'ai_command' : 'quick_task';

  const inlineAuth = INLINE_AUTH_PATTERNS.find((p) => p.re.test(text));
  const clientMatch = extractClientNames(text, knownClientNames);

  return {
    mode,
    bot_handle: botHandle,
    client_name: clientMatch.primary,
    client_references: clientMatch.references,
    intent: mode === 'quick_task' ? 'quick_task' : intent,
    requested_outputs: extractOutputs(text),
    target_workspace: extractTargetWorkspace(text),
    inline_authorization_detected: inlineAuth != null,
    inline_authorization_text: inlineAuth?.text ?? null,
    requires_plan: mode === 'ai_command',
    original_text: text,
    steps: mode === 'ai_command' ? decomposeSteps(text) : [],
  };
}

// ── 模块能力 Manifest (顾源源 §6, 不硬编码业务流程) ────

export type ModuleCapabilityEntry = {
  capabilityKey: string;
  label: string;
  riskLevel: 'low' | 'medium' | 'high';
  approvalRequired: boolean;
  toolNames: string[];
  requiredBotCapability?: string;
};

export type ModuleCapability = {
  moduleKey: string;
  moduleName: string;
  enabled: boolean;
  description: string;
  capabilities: ModuleCapabilityEntry[];
};

export const MODULE_CAPABILITY_MANIFEST_V1: ModuleCapability[] = [
  {
    moduleKey: 'tasks',
    moduleName: '任务与日程',
    enabled: true,
    description: '创建 AI 自己的任务 / 执行计划 / 复盘 / 标记状态',
    capabilities: [
      { capabilityKey: 'task.create', label: '创建任务', riskLevel: 'low', approvalRequired: false, toolNames: ['tasks.create'] },
      { capabilityKey: 'task.review', label: '写复盘', riskLevel: 'low', approvalRequired: false, toolNames: [] },
    ],
  },
  {
    moduleKey: 'workspace',
    moduleName: '客户工作台',
    enabled: true,
    description: '读取客户状态 / 工作台问答 / 写入客户工作台正式文件 (需审批)',
    capabilities: [
      { capabilityKey: 'workspace.read_state', label: '读客户状态', riskLevel: 'low', approvalRequired: false, toolNames: ['clients.agent_state'] },
      { capabilityKey: 'workspace.chat', label: '工作台问答', riskLevel: 'low', approvalRequired: false, toolNames: ['workspace.chat'] },
      { capabilityKey: 'workspace.file_write_request', label: '申请写入工作台正式文件', riskLevel: 'high', approvalRequired: true, toolNames: [], requiredBotCapability: 'workspace_file_write.request' },
    ],
  },
  {
    moduleKey: 'data_center',
    moduleName: '数据中心 / 公司大脑',
    enabled: true,
    description: '读事实/承诺/风险/待澄清/数据缺口 / 判断证据 / 触发解析',
    capabilities: [
      { capabilityKey: 'data.read_gaps', label: '查数据缺口', riskLevel: 'low', approvalRequired: false, toolNames: ['data_gaps.list'] },
      { capabilityKey: 'data.check_evidence', label: '检查证据', riskLevel: 'low', approvalRequired: false, toolNames: ['evidence.check'] },
      { capabilityKey: 'data.quality_context', label: '质量评估', riskLevel: 'low', approvalRequired: false, toolNames: ['quality.context'] },
      { capabilityKey: 'data.authority_resolve', label: '权威值判断', riskLevel: 'low', approvalRequired: false, toolNames: ['authority.resolve'] },
      { capabilityKey: 'data.parse_request', label: '申请触发数据中心解析', riskLevel: 'high', approvalRequired: true, toolNames: [], requiredBotCapability: 'data_center_parse.request' },
    ],
  },
  {
    moduleKey: 'intelligence',
    moduleName: '资讯情报站',
    enabled: false,
    description: '读已有外部证据 / 查缺口 / 申请补外部证据 (部分未暴露)',
    capabilities: [
      { capabilityKey: 'intel.read_gaps', label: '查外部证据缺口', riskLevel: 'low', approvalRequired: false, toolNames: ['data_gaps.list'] },
      { capabilityKey: 'intel.compensate', label: '申请补外部证据', riskLevel: 'medium', approvalRequired: true, toolNames: ['data_gaps.compensate'] },
    ],
  },
  {
    moduleKey: 'documents',
    moduleName: '文档生成 / 战略陪伴',
    enabled: true,
    description: '生成报告提纲 / 草稿 / 品牌建议 / 合同草稿 / 会谈包 / 模板填充',
    capabilities: [
      { capabilityKey: 'doc.fill_template', label: '模板填充 (R4-P1)', riskLevel: 'medium', approvalRequired: true, toolNames: ['documents.fill_template'], requiredBotCapability: 'external_material_draft.create' },
      { capabilityKey: 'doc.contracts_draft', label: '合同草稿 (V3.0 P0-1, 未暴露)', riskLevel: 'high', approvalRequired: true, toolNames: ['contracts.draft'], requiredBotCapability: 'external_material_draft.create' },
      { capabilityKey: 'doc.templates_generate', label: '模板生成 (V3.0 P0-2, 未暴露)', riskLevel: 'medium', approvalRequired: true, toolNames: ['templates.generate'], requiredBotCapability: 'external_material_draft.create' },
    ],
  },
  {
    moduleKey: 'growth',
    moduleName: '成长中心 / 复盘',
    enabled: true,
    description: '写 AI 任务复盘 (第一版只写任务复盘, 不沉淀到成长中心)',
    capabilities: [
      { capabilityKey: 'growth.write_review', label: '写任务复盘', riskLevel: 'low', approvalRequired: false, toolNames: [] },
    ],
  },
];

// 根据 intent 推荐需要的模块
export function recommendModulesForIntent(intent: AICommandIntent): string[] {
  switch (intent) {
    case 'generate_client_background_document':
      return ['workspace', 'data_center', 'documents', 'tasks', 'growth'];
    case 'generate_report_outline':
      return ['workspace', 'data_center', 'documents', 'tasks'];
    case 'review_today_tasks_for_ai':
      return ['tasks', 'data_center'];
    case 'summarize_client_state':
      return ['workspace', 'data_center'];
    case 'resolve_clarifications_plan':
      return ['workspace', 'data_center', 'tasks'];
    default:
      return ['workspace', 'tasks'];
  }
}

// ── 执行计划数据结构 (顾源源 §9 AIExecutionPlan) ────

export type AIExecutionPlanStep = {
  order: number;
  moduleKey: string;
  action: string;
  toolName?: string;
  expectedResult: string;
  writesTo?: string[];
  readsFrom?: string[];
  approvalRequired: boolean;
  riskLevel: 'low' | 'medium' | 'high';
};

export type AIExecutionPlanApprovalItem = {
  action: string;
  reason: string;
  approverType: 'department_lead' | 'ceo' | 'any_reporting_approver';
};

export type AIExecutionPlanRequiredModule = {
  moduleKey: string;
  moduleName: string;
  enabled: boolean;
  purpose: string;
};

export type AIExecutionPlanInlineAuth = {
  detected: boolean;
  text?: string;
  humanInitiatorId?: string;
};

export type AIExecutionPlan = {
  title: string;
  botMemberId: string;
  actorId: string;
  clientId?: string;
  eventLineId?: string;
  intent: AICommandIntent;
  taskGoal: string;
  requiredModules: AIExecutionPlanRequiredModule[];
  steps: AIExecutionPlanStep[];
  expectedOutputs: string[];
  approvalItems: AIExecutionPlanApprovalItem[];
  limitations: string[];
  inlineAuthorization?: AIExecutionPlanInlineAuth;
};
