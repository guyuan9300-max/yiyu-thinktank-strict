import type { TaskStatus, WeeklyReviewTaskStructuredNote } from '../../../shared/types';
import {
  CheckCircle2,
  Clock,
  XCircle,
  Save,
  MessageSquareText,
  Sparkles,
} from 'lucide-react';

const LIGHTWEIGHT_TAGS = ['资料不足', '等待他人', '方向不清', '资源不够', '工作过度饱和'] as const;

const GROWTH_HINT_RULES = [
  { key: 'exec', label: '推进执行', keywords: ['推进', '闭环', '行动项', '排期', '拆解', '跟进', '完成', '延期'] },
  { key: 'collab', label: '协作沟通', keywords: ['协作', '沟通', '对齐', '会议', '负责人', '跨组', '边界', '配合'] },
  { key: 'analyze', label: '分析判断', keywords: ['分析', '判断', '原因', '本质', '结论', '规律', '为什么'] },
  { key: 'insight', label: '客户洞察', keywords: ['客户', '用户', '访谈', '需求', '顾虑', '诉求', '反馈'] },
  { key: 'risk', label: '风险识别', keywords: ['风险', '阻碍', '卡点', '依赖', '预警', '退回', '失败'] },
  { key: 'write', label: '写作表达', keywords: ['模板', '方法', '清单', '沉淀', '复用', '记录', '总结'] },
] as const;

export function createEmptyReviewStructuredNote(): WeeklyReviewTaskStructuredNote {
  return {
    reflection: '',
    lightweightTag: '',
    planCommitment: '',
    progress: '',
    completionStatus: 'in_progress',
    departmentPlanId: null,
    departmentPlanAlignment: 'unknown',
    organizationPlanId: null,
    organizationPlanAlignment: 'unknown',
    successReason: '',
    successExperience: '',
    blockerReason: '',
    failureInsight: '',
    supportNeeded: '',
    nextAction: '',
  };
}

function reviewModeFromValue(value: WeeklyReviewTaskStructuredNote, taskStatus?: TaskStatus) {
  if (taskStatus === 'done') return 'done' as const;
  if (value.completionStatus === 'done_on_time' || value.completionStatus === 'done_late') return 'done' as const;
  return 'pending' as const;
}

function normalizeSimpleReviewText(text: string) {
  return text
    .replace(/^任务完成心得：\s*/, '')
    .replace(/^需要支持\s*\/\s*思考：\s*/, '')
    .replace(/^支持需求：\s*/, '')
    .replace(/^失败心得：\s*/, '')
    .replace(/^阻碍原因：\s*/, '')
    .replace(/^本周推进：\s*/, '')
    .trim();
}

function detectGrowthHints(text: string) {
  const normalized = normalizeSimpleReviewText(text);
  if (!normalized) return [];
  return GROWTH_HINT_RULES.filter((rule) => rule.keywords.some((keyword) => normalized.includes(keyword))).map((rule) => rule.label);
}

function detectContributionPremiumHint(text: string) {
  const normalized = normalizeSimpleReviewText(text);
  if (!normalized) return null;
  if (['模板', '复用', '流程', '规则', '机制', '统一'].some((keyword) => normalized.includes(keyword))) {
    return '这条复盘有机会拿到 30%-50% 的组织贡献溢价';
  }
  if (['协作', '会议', '负责人', '时间点', '边界', '支持', '帮助', '风险', '预警'].some((keyword) => normalized.includes(keyword))) {
    return '这条复盘有机会拿到 20%-40% 的组织贡献溢价';
  }
  return null;
}

export function getSimpleReviewText(value: WeeklyReviewTaskStructuredNote, taskStatus?: TaskStatus) {
  if (value.reflection.trim()) {
    return normalizeSimpleReviewText(value.reflection);
  }
  const mode = reviewModeFromValue(value, taskStatus);
  const candidates = mode === 'done'
    ? [
        value.successExperience.trim(),
        value.successReason.trim(),
        value.progress.trim(),
        value.nextAction.trim(),
      ]
    : [
        value.supportNeeded.trim(),
        value.failureInsight.trim(),
        value.blockerReason.trim(),
        value.progress.trim(),
        value.nextAction.trim(),
      ];
  return normalizeSimpleReviewText(candidates.find(Boolean) || '');
}

export function applySimpleReviewText(
  value: WeeklyReviewTaskStructuredNote,
  nextText: string,
  taskStatus?: TaskStatus,
): WeeklyReviewTaskStructuredNote {
  const trimmed = nextText.trim();
  const mode = reviewModeFromValue(value, taskStatus);
  if (mode === 'done') {
    return {
      ...value,
      completionStatus: value.completionStatus === 'done_late' ? 'done_late' : 'done_on_time',
      reflection: trimmed,
    };
  }
  return {
    ...value,
    completionStatus: value.completionStatus === 'not_done' ? 'not_done' : 'in_progress',
    reflection: trimmed,
  };
}

export function hasMeaningfulReviewStructuredNote(value: WeeklyReviewTaskStructuredNote) {
  return [
    value.reflection.trim(),
    value.lightweightTag,
    value.planCommitment.trim(),
    value.progress.trim(),
    value.departmentPlanId || '',
    value.organizationPlanId || '',
    value.successReason.trim(),
    value.successExperience.trim(),
    value.blockerReason.trim(),
    value.failureInsight.trim(),
    value.supportNeeded.trim(),
    value.nextAction.trim(),
    value.completionStatus !== 'in_progress' ? value.completionStatus : '',
    value.departmentPlanAlignment !== 'unknown' ? value.departmentPlanAlignment : '',
    value.organizationPlanAlignment !== 'unknown' ? value.organizationPlanAlignment : '',
  ].some(Boolean);
}

export function composeReviewNoteFromStructuredFields(value: WeeklyReviewTaskStructuredNote, taskStatus?: TaskStatus) {
  const text = getSimpleReviewText(value, taskStatus);
  const tagSuffix = reviewModeFromValue(value, taskStatus) === 'pending' && value.lightweightTag
    ? `（当前卡点：${value.lightweightTag}）`
    : '';
  if (!text && tagSuffix) {
    return `需要支持 / 思考：${value.lightweightTag}`;
  }
  if (!text) return '';
  return reviewModeFromValue(value, taskStatus) === 'done'
    ? `任务完成心得：${text}`
    : `需要支持 / 思考：${text}${tagSuffix}`;
}

type WeeklyReviewStructuredFieldsProps = {
  scope: 'work' | 'personal';
  value: WeeklyReviewTaskStructuredNote;
  taskStatus: TaskStatus;
  onChange: (nextValue: WeeklyReviewTaskStructuredNote) => void;
  onSave?: () => void;
  isSaving?: boolean;
  saveDisabled?: boolean;
  saveSucceeded?: boolean;
  onStatusChange?: (nextStatus: 'done' | 'delayed' | 'cancelled') => void;
  isStatusChanging?: boolean;
  statusScopeLabel?: string;
  textareaLabel?: string;
  reflectionPlaceholder?: string;
};

export function WeeklyReviewStructuredFields({
  scope,
  value,
  taskStatus,
  onChange,
  onSave,
  isSaving = false,
  saveDisabled = false,
  saveSucceeded = false,
  onStatusChange,
  isStatusChanging = false,
  statusScopeLabel = '本条任务状态',
  textareaLabel: textareaLabelOverride,
  reflectionPlaceholder,
}: WeeklyReviewStructuredFieldsProps) {
  const mode = reviewModeFromValue(value, taskStatus);
  const helperLabel = mode === 'done'
    ? '已完成'
    : taskStatus === 'rejected'
      ? '已取消'
      : value.completionStatus === 'not_done'
        ? '已延迟'
        : taskStatus === 'doing'
          ? '仍在推进'
          : '未完成 / 逾期';
  const textareaLabel = textareaLabelOverride || (mode === 'done' ? '完成心得' : '需要支持 / 这周思考');
  const placeholder = mode === 'done'
    ? '任务完成了，有什么心得？'
    : scope === 'work'
      ? '这项任务还没收口，需要什么支持，或者有什么思考？'
      : '这件事还没推进完，需要什么支持，或者有什么思考？';
  const resolvedPlaceholder = reflectionPlaceholder?.trim() || placeholder;
  const currentValue = getSimpleReviewText(value, taskStatus);
  const growthHints = detectGrowthHints(currentValue);
  const contributionPremiumHint = detectContributionPremiumHint(currentValue);
  const saveButtonState = isSaving ? 'saving' : saveSucceeded ? 'saved' : saveDisabled ? 'disabled' : 'ready';
  const selectedStatus =
    taskStatus === 'done'
      ? 'done'
      : taskStatus === 'rejected'
        ? 'cancelled'
        : value.completionStatus === 'not_done'
          ? 'delayed'
          : null;

  const statusDotColor =
    mode === 'done'
      ? 'bg-emerald-500'
      : taskStatus === 'rejected'
        ? 'bg-rose-500'
        : value.completionStatus === 'not_done'
          ? 'bg-amber-500'
          : 'bg-gray-300';

  return (
    <div className="space-y-4">
      {/* 状态切换：icon + 色彩 pill，北欧底色 + 中国用户的明确性 */}
      {onStatusChange && (
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-2 text-[11px]">
            <span className={`h-1.5 w-1.5 rounded-full ${statusDotColor}`} />
            <span className="text-gray-400">当前</span>
            <span className="font-medium text-gray-800">{helperLabel}</span>
          </div>
          <div className="inline-flex items-center bg-gray-100/70 rounded-lg p-0.5 ring-1 ring-gray-200/80">
            <button
              type="button"
              onClick={() => onStatusChange('done')}
              disabled={isStatusChanging}
              className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[12px] font-medium transition-all ${
                selectedStatus === 'done'
                  ? 'bg-white text-emerald-700 shadow-sm ring-1 ring-emerald-200'
                  : 'text-gray-600 hover:text-emerald-700 hover:bg-white/60'
              } ${isStatusChanging ? 'cursor-not-allowed opacity-50' : ''}`}
            >
              <CheckCircle2 size={13} strokeWidth={2} />
              <span>完成</span>
            </button>
            <button
              type="button"
              onClick={() => onStatusChange('delayed')}
              disabled={isStatusChanging}
              className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[12px] font-medium transition-all ${
                selectedStatus === 'delayed'
                  ? 'bg-white text-amber-700 shadow-sm ring-1 ring-amber-200'
                  : 'text-gray-600 hover:text-amber-700 hover:bg-white/60'
              } ${isStatusChanging ? 'cursor-not-allowed opacity-50' : ''}`}
            >
              <Clock size={13} strokeWidth={2} />
              <span>延迟</span>
            </button>
            <button
              type="button"
              onClick={() => onStatusChange('cancelled')}
              disabled={isStatusChanging}
              className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[12px] font-medium transition-all ${
                selectedStatus === 'cancelled'
                  ? 'bg-white text-rose-700 shadow-sm ring-1 ring-rose-200'
                  : 'text-gray-600 hover:text-rose-700 hover:bg-white/60'
              } ${isStatusChanging ? 'cursor-not-allowed opacity-50' : ''}`}
            >
              <XCircle size={13} strokeWidth={2} />
              <span>取消</span>
            </button>
          </div>
        </div>
      )}

      {/* 复盘输入：hairline border + 浅背景 + focus 强化，输入区可识别 */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-1.5">
            <MessageSquareText size={12} strokeWidth={2} className="text-gray-400" />
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">
              {textareaLabel}
            </p>
          </div>
          {onSave && (
            <button
              type="button"
              onClick={onSave}
              disabled={isSaving || saveDisabled}
              className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] font-medium tracking-wide transition-all ${
                saveButtonState === 'disabled'
                  ? 'cursor-not-allowed text-gray-300 bg-gray-50'
                  : saveButtonState === 'saved'
                    ? 'bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200/60'
                    : 'bg-gray-900 text-white shadow-sm hover:bg-gray-700'
              }`}
            >
              {saveButtonState === 'saved' ? (
                <CheckCircle2 size={12} strokeWidth={2.2} />
              ) : (
                <Save size={12} strokeWidth={2.2} />
              )}
              <span>{isSaving ? '保存中' : saveSucceeded ? '已保存' : '保存'}</span>
            </button>
          )}
        </div>
        <textarea
          value={currentValue}
          onChange={(event) => onChange(applySimpleReviewText(value, event.target.value, taskStatus))}
          placeholder={resolvedPlaceholder}
          className="w-full min-h-[80px] rounded-lg border border-gray-200 bg-gray-50/40 px-3.5 py-2.5 text-[13.5px] leading-7 text-gray-800 outline-none placeholder:text-gray-400 placeholder:font-light resize-y transition-all focus:border-gray-400 focus:bg-white focus:ring-1 focus:ring-gray-200"
        />
      </div>

      {/* 主要卡点：默认就有 bg + border，看起来就是按钮 */}
      {mode === 'pending' && (
        <div className="grid grid-cols-[80px_1fr] gap-x-6 items-start">
          <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400 pt-2">
            主要卡点
          </span>
          <div className="flex flex-wrap gap-2">
            {LIGHTWEIGHT_TAGS.map((tag) => {
              const active = value.lightweightTag === tag;
              return (
                <button
                  key={tag}
                  type="button"
                  onClick={() => onChange({ ...value, lightweightTag: active ? '' : tag })}
                  className={`inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[12px] font-medium transition-all ${
                    active
                      ? 'border-[#5B7BFE]/40 bg-[#EEF4FF] text-[#335CFF] shadow-sm'
                      : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300 hover:text-gray-900 hover:bg-gray-50'
                  }`}
                >
                  <span
                    className={`h-1.5 w-1.5 rounded-full ${
                      active ? 'bg-[#5B7BFE]' : 'bg-gray-300'
                    }`}
                  />
                  <span>{tag}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* 识别到的成长点：Sparkles icon + 极简彩色 chip */}
      {growthHints.length > 0 && (
        <div className="grid grid-cols-[80px_1fr] gap-x-6 items-start">
          <div className="flex items-center gap-1.5 pt-1">
            <Sparkles size={11} strokeWidth={2} className="text-[#5B7BFE]" />
            <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">
              成长识别
            </span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {growthHints.map((label) => (
              <span
                key={label}
                className="inline-flex items-center rounded-md bg-[#EEF4FF] px-2 py-0.5 text-[11.5px] font-medium text-[#335CFF]"
              >
                {label}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* 贡献溢价提示：左线引用 + accent 色 */}
      {contributionPremiumHint && (
        <div className="flex items-start gap-2.5 rounded-md bg-[#5B7BFE]/[0.04] border-l-[2px] border-[#5B7BFE] px-3.5 py-2.5">
          <Sparkles size={13} strokeWidth={2} className="text-[#5B7BFE] mt-0.5 shrink-0" />
          <p className="text-[12px] leading-relaxed text-[#335CFF]">
            {contributionPremiumHint}
          </p>
        </div>
      )}
    </div>
  );
}
