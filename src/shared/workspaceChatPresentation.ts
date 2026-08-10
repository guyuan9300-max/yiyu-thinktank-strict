export type WorkspaceFallbackNotice = {
  tone: 'amber' | 'rose' | 'blue';
  title: string;
  detail: string;
};

export type WorkspaceMaterialNotice = {
  hasMaterial: boolean;
  tone: 'blue' | 'amber';
  sourceNotice: string;
  memoryNotice: string | null;
};

function countValue(value: unknown): number {
  const parsed = Number(value || 0);
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : 0;
}

export function getWorkspaceMaterialNotice(
  summary?: Record<string, unknown> | null,
): WorkspaceMaterialNotice {
  const value = summary || {};
  const mode = String(value.materialAccessMode || 'none').trim();
  const localCount = countValue(value.selectedDocumentContentCount);
  const organizationCount = countValue(value.projectKnowledgeSummaryCount);
  const localMemoryCount = countValue(value.localMemoryCount);
  const sourceCount = Math.max(
    countValue(value.sourceCount),
    countValue(value.linkedEvidenceCount),
    localCount + organizationCount + localMemoryCount,
  );
  const hasLocal = localCount > 0 || mode === 'local_original' || mode === 'lightweight_direct' || mode === 'mixed';
  const hasOrganization = organizationCount > 0 || mode === 'organization_knowledge' || mode === 'knowledge_summary' || mode === 'mixed';
  const hasMemory = localMemoryCount > 0 || mode === 'memory_context';
  let sourceNotice: string;
  if (hasLocal && hasOrganization) {
    sourceNotice = `已读取 ${localCount || sourceCount} 份本机资料，并使用 ${organizationCount || Math.max(1, sourceCount - localCount - localMemoryCount)} 条组织共享知识${hasMemory ? `、${localMemoryCount} 条已存记忆` : ''}。`;
  } else if (hasLocal) {
    sourceNotice = `已读取 ${localCount || sourceCount || 1} 份本机资料${hasMemory ? `，并使用 ${localMemoryCount} 条已存记忆` : ''}，本轮回答基于这些材料。`;
  } else if (hasOrganization) {
    sourceNotice = `已使用 ${organizationCount || sourceCount || 1} 条组织共享知识${hasMemory ? `和 ${localMemoryCount} 条已存记忆` : ''}；本轮未读取本机原件。`;
  } else if (hasMemory) {
    sourceNotice = `已使用 ${localMemoryCount || sourceCount || 1} 条当前项目的本机记忆；本轮未读取本机原件或组织共享知识。`;
  } else {
    sourceNotice = '当前没有命中可用的本机资料或组织共享知识；以下仅为通用背景判断，不是项目资料的正式结论。';
  }

  const memoryState = String(value.memoryState || 'ready').trim();
  let memoryNotice: string | null = null;
  if (memoryState === 'not_connected') {
    memoryNotice = '记忆沉淀尚未接通 · 本次仍基于当前材料和已核实知识回答。';
  } else if (memoryState === 'blocked') {
    memoryNotice = '记忆读取受限 · 本次仍基于当前材料和已核实知识回答。';
  } else if (memoryState === 'failed_retryable') {
    memoryNotice = '记忆沉淀中 · 本次仍基于当前材料和已核实知识回答。';
  }

  return {
    hasMaterial: hasLocal || hasOrganization || hasMemory,
    tone: hasLocal || hasOrganization || hasMemory ? 'blue' : 'amber',
    sourceNotice,
    memoryNotice,
  };
}

export function getWorkspaceFallbackNotice(input: {
  answerMode?: string | null;
  failureReason?: string | null;
  fallbackTemplateUsed?: boolean;
  finalFailureStage?: string | null;
  partialGenerationPreserved?: boolean;
  generationPolicy?: Record<string, unknown> | null;
}): WorkspaceFallbackNotice | null {
  const answerMode = String(input.answerMode || '').trim();
  if (answerMode !== 'grounded_fallback' && answerMode !== 'system_failure') {
    return null;
  }
  const failureReason = String(input.failureReason || '').trim();
  const finalFailureStage = String(input.finalFailureStage || '').trim();
  const generationPolicyReason = String(input.generationPolicy?.reason || '').trim();
  const generationPolicy = input.generationPolicy || null;
  const cooldownLike = Boolean(generationPolicy?.cooldownActive)
    || generationPolicyReason.includes('cooldown')
    || Boolean(generationPolicy?.shouldUseLocalOnly);

  if (input.fallbackTemplateUsed) {
    return {
      tone: 'amber',
      title: '当前启用了 legacy fallback',
      detail: '以下内容来自旧模板兜底结果，不代表新的数据中心主链回答。',
    };
  }

  if (input.partialGenerationPreserved || failureReason === 'llm_partial_preserved_after_retry') {
    return {
      tone: 'blue',
      title: '本轮模型生成中断，但已保留有效内容',
      detail: '系统保留了部分可用回答和来源诊断；如需完整回答，请稍后重试。',
    };
  }

  if (cooldownLike) {
    return {
      tone: 'amber',
      title: '模型近期连续超时，当前处于冷却保护',
      detail: '建议先重置 runtime 状态或检查模型连通性，再重新发起回答。',
    };
  }

  if (answerMode === 'system_failure' || finalFailureStage === 'compact_retry_failed' || failureReason === 'llm_generation_failed') {
    return {
      tone: 'rose',
      title: '本轮模型没有成功完成回答',
      detail: '系统已命中相关资料，但没有生成可交付文本。你可以重试，或先处理资料缺口和候选动作。',
    };
  }

  return {
    tone: 'amber',
    title: '本轮回答未完整完成',
    detail: '系统已保留可用内容、来源诊断和修复建议。你可以重试，或先处理右侧资料缺口和候选动作。',
  };
}

/**
 * 客户工作台问答 chat 输出抑制 — 去掉 LLM 在正文里裸露的文件名引用 (例如
 * "（见 strategy.md）" / "[methodology.md]" / "在 survey.docx 中提到的...")。
 *
 * Why: 战略文档 prompt 注入后, LLM 会在正文里直接引用文件名, 看着像 prompt
 * 模板泄漏, 客户看着不专业. 我们在前端 chat 渲染前清掉这些 token,
 * 同时把常见的 strategy.md / methodology.md 替换为友好中文标签.
 *
 * How to apply: 在 chat 消息渲染前调用; 不要应用到 strategic narrative
 * 维度卡片渲染 (那边明确告诉用户"溯源到 strategy.md / methodology.md" 是 feature).
 */
export function stripFileCitations(text: string | null | undefined): string {
  if (!text) return '';
  let result = String(text);

  // 1. 整段括号引用 (中英文括号 + 包含文件后缀的句子级片段) — 整段删
  //    例: (见 strategy.md) / （来源: strategy.md, methodology.md）
  //    .md/.markdown/.docx/.pdf/.txt 都覆盖
  //    注: （ = （  ） = )  用 unicode 转义避免源码编码问题
  result = result.replace(/\s*\(([^()]*?\.(?:md|markdown|docx|pdf|txt))[^()]*\)/gi, '');
  result = result.replace(/\s*（([^（）]*?\.(?:md|markdown|docx|pdf|txt))[^（）]*）/gi, '');

  // 2. 方括号引用: [xxx.md]
  result = result.replace(/\s*\[[^[\]]*?\.(?:md|markdown|docx|pdf|txt)[^[\]]*\]/gi, '');

  // 3. 已知核心文件名 → 友好中文标签 (保留语义)
  result = result.replace(/\bstrategy\.md\b/gi, '战略文档');
  result = result.replace(/\bmethodology\.md\b/gi, '方法论文档');

  // 4. 其他未知裸文件名 token (例: survey.docx) → 通用标签 "客户资料"
  //    避免漏过的文件名继续暴露在正文里
  result = result.replace(/\b[\w\-]+\.(?:md|markdown|docx|pdf|txt)\b/gi, '客户资料');

  // 5. 清理 step 1-2 删括号留下的相邻标点 + 多余空白
  result = result.replace(/[，,、]\s*([。！？\n])/g, '$1');
  result = result.replace(/[，,]\s*[，,]/g, '，');
  result = result.replace(/\(\s*\)/g, '');
  result = result.replace(/（\s*）/g, '');
  result = result.replace(/[ \t]{2,}/g, ' ');
  result = result.replace(/\n{3,}/g, '\n\n');

  return result.trim();
}

/**
 * 删除 LLM 输出里的字典引证标记(例 `[📚 测试项目A.累计服务大学生数]`)。
 *
 * Why: 后端 system 指令引导 LLM 用 `[📚 term.attribute]` 标记关键事实的字典溯源,
 * `citation_validator.py` 校验有效性后**保留**有效标记给前端做漂亮渲染。但前端
 * 一直没接渲染层,标记直接显示在 chat 正文 → 用户看见"[📚 测试项目A.累计服务人数][📚 ...]"
 * 很碎,不连贯。
 *
 * 用户决定:LLM **应该**继续基于字典 verified 值生成事实(grounding 不变),但
 * **不在正文里显示字典溯源标记**(以及上游 validator 替换出的 ⚠️ 引用失效标记)。
 *
 * 格式覆盖(对应 services/citation_validator.py 的 _CITE_PATTERN):
 * - `[📚 term.attribute_name]` 标准格式
 * - `[📚 term · attribute_name]` 中点分隔
 * - `[📚term.attr]` / `[📚 term . attr]` LLM 偶尔产出的紧凑/松散变体
 * - `[⚠️ 引用失效：「X.Y」不在字典 verified 列表，请在字典审核此项]` validator 替换的失效标记
 */
export function stripGlossaryCitations(text: string | null | undefined): string {
  if (!text) return '';
  let result = String(text);

  // 1. 📚 字典引证 — 覆盖标准 / 紧凑 / 中点分隔三种变体
  result = result.replace(/\s*\[\s*📚[^\]]*\]/g, '');

  // 2. validator 替换的 ⚠️ 失效标记 — 用户也不该看见
  //    格式:`[⚠️ 引用失效：...]`(中文方括号内含 引用失效 字样)
  result = result.replace(/\s*\[\s*⚠️\s*引用失效[^\]]*\]/g, '');

  // 3. 清理删除后留下的多余标点 / 空白(跟 stripFileCitations 同一套规则)
  result = result.replace(/[，,、]\s*([。！？\n])/g, '$1');
  result = result.replace(/[，,]\s*[，,]/g, '，');
  result = result.replace(/[ \t]{2,}/g, ' ');
  result = result.replace(/\n{3,}/g, '\n\n');

  return result.trim();
}

/**
 * chat 输出的统一清理入口 — 文件名引证 + 字典引证 + ⚠️ 失效标记 都过一遍。
 *
 * App.tsx 的 chat 渲染点应调这个,而不是单独调 stripFileCitations。
 */
export function cleanChatOutput(text: string | null | undefined): string {
  return stripGlossaryCitations(stripFileCitations(text));
}

export function getWorkspaceRuntimeMismatchNotice(input: {
  sourceIntegrityMatch?: boolean | null;
  sourceIntegrityWarning?: string | null;
  backendBuildVersion?: string | null;
  backendGitCommit?: string | null;
  frontendBuildVersion?: string | null;
  frontendGitCommit?: string | null;
}): string | null {
  if (input.sourceIntegrityMatch === false) {
    return input.sourceIntegrityWarning || '当前运行的是旧打包版，源码修改可能尚未生效。请重新构建或切换到最新运行后端。';
  }
  if (
    input.backendGitCommit
    && input.frontendGitCommit
    && input.backendGitCommit !== input.frontendGitCommit
  ) {
    return '当前前后端 Git 指纹不一致，客户工作台可能仍在运行旧包。';
  }
  if (
    input.backendBuildVersion
    && input.frontendBuildVersion
    && input.backendBuildVersion !== input.frontendBuildVersion
  ) {
    return '当前前后端 buildVersion 不一致，源码修改可能只在一端生效。';
  }
  return null;
}
