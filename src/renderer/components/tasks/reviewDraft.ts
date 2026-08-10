import type { ReviewDashboard, Task, WeeklyReviewAnalysis, WeeklyReviewTaskStructuredNote } from '../../../shared/types';

export type ReviewTaskRow = {
  task: Task;
  note: string;
  structuredNote: WeeklyReviewTaskStructuredNote;
  reviewedAt: string | null;
};

type ReviewDraftGroup = {
  id: string;
  title: string;
  eventLineId: string | null;
  rows: ReviewTaskRow[];
  sharedNote: string;
};

export function reviewStatusLabel(task: Task) {
  if (task.status === 'done') return '已完成';
  if (task.status === 'doing') return '进行中';
  if (task.status === 'rejected') return '已取消';
  if (task.status === 'inbox') return '收件箱';
  return '未完成';
}

export function reviewTaskDateLabel(task: Task) {
  return task.dueDate || task.createdAt.slice(0, 10);
}

export function reviewTaskBackground(task: Task) {
  const segments = [
    task.desc.trim() || '任务背景待补充。',
    `归属清单：${task.listName}`,
    `负责人：${task.ownerName || '未指定'}`,
  ];
  return segments.join('；');
}

function lensLabel(lens: string) {
  if (lens === 'organization') return '组织视角';
  if (lens === 'business') return '业务视角';
  if (lens === 'team') return '团队视角';
  if (lens === 'market') return '市场视角';
  if (lens === 'growth') return '成长视角';
  return '执行视角';
}

function confidenceLabel(confidence: string) {
  if (confidence === 'high') return '高';
  if (confidence === 'medium') return '中';
  return '低';
}

const SECTION_LABELS = ['一', '二', '三', '四', '五', '六', '七', '八', '九'];

function buildReviewDraftGroups(rows: ReviewTaskRow[]): ReviewDraftGroup[] {
  const groups = new Map<string, ReviewTaskRow[]>();
  rows.forEach((row) => {
    const eventLineId = row.task.eventLineId?.trim() || '';
    const key = eventLineId ? `event-line:${eventLineId}` : `task:${row.task.id}`;
    const bucket = groups.get(key);
    if (bucket) {
      bucket.push(row);
    } else {
      groups.set(key, [row]);
    }
  });

  return Array.from(groups.entries()).map(([id, groupRows]) => {
    const sharedNote = groupRows.find(({ note }) => note.trim())?.note.trim() || '';
    return {
      id,
      title: groupRows[0]?.task.eventLineName?.trim() || groupRows[0]?.task.title || '未命名事项',
      eventLineId: groupRows[0]?.task.eventLineId?.trim() || null,
      rows: [...groupRows],
      sharedNote,
    };
  });
}

export function buildWeeklyReviewDocumentDraft(
  scope: 'work' | 'personal',
  weekLabel: string,
  rows: ReviewTaskRow[],
  analysis?: WeeklyReviewAnalysis | null,
  dashboard?: Pick<ReviewDashboard, 'teamReport' | 'orgReport' | 'agentDepartmentDigests' | 'agentDepartmentPlans'> | null,
) {
  const scopeLabel = scope === 'work' ? '组织' : '成长';
  const generatedAt = new Date().toLocaleString('zh-CN', { hour12: false });
  const completedCount = rows.filter(({ task }) => task.status === 'done').length;
  const reviewedCount = rows.filter(({ note }) => Boolean(note.trim())).length;
  const unfinishedRows = rows.filter(({ task }) => task.status !== 'done');
  const reviewGroups = buildReviewDraftGroups(rows);
  const eventLineGroupCount = reviewGroups.filter((group) => group.eventLineId).length;
  const agentDepartmentDigests = dashboard?.agentDepartmentDigests || [];
  const agentDepartmentPlans = dashboard?.agentDepartmentPlans || [];

  const lines = [
    `${weekLabel} ${scopeLabel}复盘文档（草稿）`,
    `生成时间：${generatedAt}`,
    '',
  ];
  let sectionIndex = 0;
  const pushSection = (title: string) => {
    if (lines[lines.length - 1] !== '') {
      lines.push('');
    }
    const label = SECTION_LABELS[sectionIndex] || String(sectionIndex + 1);
    lines.push(`${label}、${title}`);
    sectionIndex += 1;
  };

  if (analysis) {
    lines.push('说明：以下内容分为“已确认事实”和“可能性分析”两层。可能性分析会明确标注权重与置信度，供人工继续判断，不应直接当成确定事实。', '');
  }

  pushSection('整体概览');
  lines.push(`本周共纳入 ${rows.length} 项${scope === 'work' ? '任务' : '成长事项'}，已完成 ${completedCount} 项，未完成 ${rows.length - completedCount} 项，已补充复盘说明 ${reviewedCount} 项。`);
  lines.push(`当前已按 ${reviewGroups.length} 个复盘模块整理，其中 ${eventLineGroupCount} 个模块来自事件线聚合。`);

  if (analysis?.headline) {
    lines.push(`整体判断：${analysis.headline}`);
  } else {
    lines.push(
      scope === 'work'
        ? `从整体执行情况看，当前周内任务推进呈现“已完成 ${completedCount} 项、仍待继续推进 ${rows.length - completedCount} 项”的节奏。以下文档会优先按事件线展开，避免把同一件事拆成多条重复记录。`
        : '从个人成长事项来看，本周已经补充的内容主要围绕状态、观察与感受展开；以下文档会优先按事件线展开，方便围绕同一件事集中复盘。',
    );
  }

  if (analysis?.metricCards?.length) {
    pushSection('核心指标');
    analysis.metricCards.forEach((metric, index) => {
      lines.push(`${index + 1}. ${metric.label}：${metric.valueText}（${metric.denominator > 0 ? `${metric.numerator}/${metric.denominator}` : '待补录'}）`);
      lines.push(metric.description);
    });
  }

  if (analysis?.confirmedFacts.length) {
    pushSection('已确认事实');
    analysis.confirmedFacts.forEach((item, index) => {
      lines.push(`${index + 1}. ${item}`);
    });
  }

  if (analysis?.evidenceWeights.length) {
    pushSection('证据权重说明');
    analysis.evidenceWeights.forEach((item) => {
      const weightLabel = item.weight === 'high' ? '高权重' : item.weight === 'medium' ? '中权重' : '低权重';
      lines.push(`- ${item.label}（${weightLabel}）：${item.rationale}`);
    });
  }

  if (analysis?.hypothesisHighlights.length) {
    pushSection('可能性分析');
    analysis.hypothesisHighlights.forEach((item, index) => {
      lines.push(`${index + 1}. ${item.title}｜${lensLabel(item.lens)}｜置信度 ${confidenceLabel(item.confidence)}`);
      lines.push(item.statement);
      lines.push(`依据：${item.reason}`);
      if (item.assumptionNote) {
        lines.push(`提示：${item.assumptionNote}`);
      }
      lines.push('');
    });
  } else {
    pushSection('可能性分析');
    lines.push(scope === 'work' ? '当前还没有足够多的过程说明，系统暂时只能给出保守判断；建议先补齐关键任务的一线说明。' : '当前成长复盘更偏事实记录，尚不足以形成更强的成长判断。');
  }

  if (scope === 'work' && dashboard?.teamReport?.summary) {
    lines.push('团队视角补充：' + dashboard.teamReport.summary, '');
  }
  if (scope === 'work' && dashboard?.orgReport?.summary) {
    lines.push('组织视角补充：' + dashboard.orgReport.summary, '');
  }
  if (scope === 'work' && agentDepartmentDigests.length > 0) {
    pushSection('部门周摘要补充');
    agentDepartmentDigests.forEach((digest, index) => {
      lines.push(`${index + 1}. ${digest.departmentName}（${digest.agentName}）`);
      lines.push(digest.summary);
      if (digest.focusItems.length > 0) {
        lines.push(`下周延续重点：${digest.focusItems.join('；')}`);
      }
      lines.push(`证据说明：本摘要基于 ${digest.evidenceCount} 条真实日志聚合，来源类型为 ${String(digest.sourcePolicy?.sourceType || 'real_log')}。`);
      lines.push('');
    });
  }
  if (scope === 'work' && agentDepartmentPlans.length > 0) {
    pushSection('部门下周计划补充');
    agentDepartmentPlans.forEach((plan, index) => {
      lines.push(`${index + 1}. ${plan.departmentName}（${plan.agentName}）`);
      lines.push(plan.summary);
      plan.planItems.forEach((item, itemIndex) => {
        lines.push(`   - 计划项 ${itemIndex + 1}：${item.title}`);
        if (item.rationale) lines.push(`   推演依据：${item.rationale}`);
        if (item.scheduleHint) lines.push(`   节奏提示：${item.scheduleHint}`);
      });
      lines.push('');
    });
  }

  pushSection('逐事件线推进情况');
  reviewGroups.forEach((group, index) => {
    lines.push(`${index + 1}. ${group.title}`);
    if (group.eventLineId) {
      lines.push(`事件线任务数：${group.rows.length}；这是本周围绕同一条事件线自动聚合出的复盘模块。`);
    } else {
      lines.push(`单项事项：当前尚未挂入事件线，仍按单条任务记录。`);
    }
    const backgroundLines = group.rows.map(({ task }) => `- ${task.title}｜${reviewStatusLabel(task)}｜${reviewTaskDateLabel(task)}｜${task.listName}`);
    lines.push('本周相关任务：');
    lines.push(...backgroundLines);
    const firstTask = group.rows[0]?.task;
    if (firstTask) {
      lines.push(`背景补充：${reviewTaskBackground(firstTask)}`);
    }
    lines.push(group.sharedNote || '尚未补充这条事件线的统一复盘说明。');
    lines.push('');
  });

  pushSection('下周关注重点');
  if (analysis?.nextWeekFocus.length) {
    analysis.nextWeekFocus.forEach((item) => {
      lines.push(`- ${item}`);
    });
  } else if (unfinishedRows.length) {
    reviewGroups
      .filter((group) => group.rows.some(({ task }) => task.status !== 'done'))
      .slice(0, 3)
      .forEach((group) => {
        lines.push(`- ${group.title}：优先继续推进；${group.sharedNote ? `可延续当前事件线判断：${group.sharedNote}` : '建议补全这一条事件线当前卡点和下一步动作。'}`);
      });
    if (reviewGroups.filter((group) => group.rows.some(({ task }) => task.status !== 'done')).length === 0) {
      unfinishedRows.slice(0, 3).forEach(({ task, note }) => {
        lines.push(`- ${task.title}：优先继续推进；${note.trim() ? `可延续当前说明中的重点：${note.trim()}` : '建议补全背景、卡点和下一步动作。'}`);
      });
    }
  } else {
    lines.push('- 当前纳入复盘的事项都已完成，可以把重点放在经验沉淀和下一轮任务准备。');
  }

  return lines.join('\n');
}
