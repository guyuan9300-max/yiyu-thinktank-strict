import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  BrainCircuit, Sparkles, FileText, CheckCircle,
  GitBranch, Award, Layers,
  AlertCircle, ClipboardList, Check, Folder, Target, FolderTree,
  Activity, Bot, PenLine, Calendar,
  ArrowLeft, AlertTriangle, ChevronRight, X, XCircle,
  Users, Flag, AlertOctagon, HelpCircle, CornerDownRight,
  RefreshCw, Star, Trash2, ChevronDown, ExternalLink
} from 'lucide-react';
import { FileTypeIcon } from '../FileTypeIcon';
import { StrategicClarificationView } from './StrategicClarificationView';
import {
  createAnalysisJob,
  getClientContradictions,
  getClientDigitalAssets,
  getClientDuplicateDocuments,
  getClientKnowledgeStatus,
  getClientStrategicPulse,
  regenerateClientNarrative,
  resolveDuplicateDocuments,
  getStrategicThoughts,
  refreshStrategicThoughts,
  reviewContradiction,
  reviewStrategicThought,
  updateStrategicThoughtState,
  type ClientKnowledgeStatus,
  type DigitalAssetClientDetail,
  type DuplicateDocumentGroup,
  type DuplicateDocumentItem,
  type FactContradictionRow,
  type DigitalAssetClientSummary,
  type DigitalAssetMaterialMaturityRow,
  type DigitalAssetMapNode,
  type DigitalAssetMetric,
  type DigitalAssetNarrative,
  type DigitalAssetPulse,
  type OrganizationDnaV2Item,
  type OrganizationDnaV2Kind,
  type OrganizationDnaV2Snapshot,
  type StrategicPulse,
  type StrategicPulseEvent,
  type StrategicPulseTodo,
  type StrategicPulseBlocker,
  type StrategicThought,
} from '../../lib/api';

// Stage 3：战略陪伴信息架构 —— Karpathy 知识页式 5 tab。
// 原本设计了 6 tab（含独立的「最近变化」），但发现客户档案 tab 里的 evolving_dna
// mini list 已经显示了最近变化；独立 tab 反而分散用户注意力。改回 5 tab，
// 客户档案 mini list 内部用滚动条让用户能看更多 evolving 条目即可。
const TABS = [
  // '客户档案' 是新的入口（底层 id 仍是 contradictions，复用 StrategicClarificationView 的整页 5 区块）
  // 原"客户档案/DigitalAssetsTab" 已下线；这里只是 label 改名 + reorder。
  { id: 'contradictions', label: '客户档案' },
  { id: 'thoughts', label: '判断 & 思考' },
];

export type ThoughtTaskPayload = {
  suggestion: string;
  ceoComment: string;
  thoughtLine: string;
  clientId: string;
  dueDate: string;
  thoughtId?: string;
  sources?: StrategicThought['sources'];
  evidenceCount?: number;
  confidence?: number | null;
  clientName?: string;
};

// --- Helpers ---

function isInternalSmokeClient(client: { id?: string; name?: string; alias?: string }) {
  return client.alias === 'workspace-smoke' || client.name === '安装态冒烟客户';
}

const getConfColor = (conf?: number) => {
  if (conf === undefined) return '#94a3b8';
  if (conf >= 70) return '#3b82f6';
  if (conf >= 50) return '#f59e0b';
  return '#ef4444';
};

const getConfBg = (conf?: number) => {
  if (conf === undefined) return 'bg-slate-100 text-slate-500';
  if (conf >= 70) return 'bg-blue-50 text-blue-600';
  if (conf >= 50) return 'bg-amber-50 text-amber-600';
  return 'bg-red-50 text-red-600';
};

const INTERNAL_KEY_SET = new Set([
  'client_overview',
  'org_overview',
  'project_overview',
  'main_contradiction',
  'core_breakthrough',
  'pending_material',
  'pending_decision',
]);

const INTERNAL_KEY_REGEX = /^[a-z]+(?:_[a-z0-9]+)+$/;

function _isInternalKeyText(value: string | null | undefined): boolean {
  const normalized = (value || '').trim().toLowerCase();
  if (!normalized) return false;
  return INTERNAL_KEY_SET.has(normalized) || INTERNAL_KEY_REGEX.test(normalized);
}

function _normalizeTextForUI(value: string | null | undefined): string {
  const text = (value || '').trim();
  if (!text) return '';
  const compact = text.replace(/\s+/g, ' ');
  if (_isInternalKeyText(compact)) return '系统发现一条待确认判断。';
  return compact;
}

// --- Detail View Components ---

function DetailHeader({
  clientName,
  stageLabel,
  readinessScore,
  assetStage,
  assetTrackTitle,
  onBack,
}: {
  clientName: string;
  stageLabel: string;
  readinessScore: number | null;
  assetStage?: string;
  assetTrackTitle?: string;
  onBack: () => void;
}) {
  const badgeText = assetStage ? `${assetStage} · ${assetTrackTitle || '组织资产型'}` : `数字资产进度 ${readinessScore ?? '--'}%`;
  return (
    <header className="sticky top-0 left-0 right-0 bg-white/90 backdrop-blur-xl border-b border-slate-200/60 z-50 px-6 sm:px-8 py-4 flex items-center justify-between shadow-sm">
      <div className="flex items-center gap-4">
        <button
          type="button"
          onClick={onBack}
          className="w-8 h-8 rounded-full border border-slate-200 flex items-center justify-center hover:bg-slate-50 transition-colors"
        >
          <ArrowLeft size={16} className="text-slate-600" />
        </button>
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-bold text-slate-800">{clientName}</h1>
          <span className="bg-slate-100 border border-slate-200 text-slate-600 text-[11px] font-bold px-2.5 py-1 rounded-lg">
            {stageLabel || '待判断'}
          </span>
        </div>
      </div>
      <div className="flex items-center gap-3">
        <div className={`px-3 py-1.5 rounded-full text-[12px] font-bold flex items-center gap-1.5 ${getConfBg(readinessScore ?? undefined)}`}>
          <Activity size={14} className="opacity-80" />
          {badgeText}
        </div>
      </div>
    </header>
  );
}

const clampPercent = (value: number | null | undefined) => Math.max(0, Math.min(100, Number(value || 0)));

const metricValue = (metrics: DigitalAssetMetric[], key: string) => metrics.find((item) => item.key === key)?.value ?? 0;

const STAGE_ORDER = ['资料整理期', '项目画像期', '结构计算期', '机制洞察期', '机会生成期'];
const NODE_STAGE_ORDER = ['整理', '画像', '计算', '洞察', '机会'];

const normalizedAssetStage = (stage: string | null | undefined) => String(stage || '').replace(/^L\d\s*/, '').trim();
const stageRank = (stage: string | null | undefined) => Math.max(0, STAGE_ORDER.indexOf(normalizedAssetStage(stage)));
const assetStageWithLevel = (stage: string | null | undefined) => {
  const normalized = normalizedAssetStage(stage) || '资料整理期';
  const rank = stageRank(normalized);
  return `L${rank + 1} ${normalized}`;
};

function AbilityLadder({ currentStage }: { currentStage: string }) {
  const current = stageRank(currentStage);
  return (
    <div className="flex flex-wrap items-center gap-2">
      {NODE_STAGE_ORDER.map((label, index) => (
        <div key={label} className="flex items-center gap-2">
          <span className={`rounded-full border px-2.5 py-1 text-[11px] font-bold ${
            index <= current
              ? 'border-blue-100 bg-blue-50 text-blue-600'
              : 'border-slate-100 bg-white text-slate-400'
          }`}>
            {label}
          </span>
          {index < NODE_STAGE_ORDER.length - 1 && <ChevronRight size={12} className="text-slate-300" />}
        </div>
      ))}
    </div>
  );
}

const nodeMaturityPercent = (node: DigitalAssetMapNode) => Math.max(0, Math.min(100, Math.round(node.maturityPercent ?? node.coverageScore ?? 0)));
const materialRowPercent = (row: DigitalAssetMaterialMaturityRow) => Math.max(0, Math.min(100, Math.round(row.percent ?? 0)));

const maturityBarClass = (value: number) => {
  if (value >= 75) return 'bg-emerald-500';
  if (value >= 50) return 'bg-blue-500';
  if (value >= 30) return 'bg-amber-500';
  return 'bg-rose-500';
};

function AssetMaturityRows({ rows }: { rows: DigitalAssetMaterialMaturityRow[] }) {
  const [expanded, setExpanded] = useState(false);
  const visibleRows = expanded ? rows : rows.slice(0, 8);
  return (
    <section className="mt-8">
      <div className="flex items-baseline justify-between mb-4 px-1">
        <div>
          <h2 className="text-[15px] font-bold text-slate-800">资料成熟度进度条</h2>
          <p className="mt-1 text-[12px] text-slate-400">百分比表示资料质量成熟度，不按文件数量直接打分。</p>
        </div>
        <span className="text-[12px] font-bold text-blue-600">资料类型 {rows.length}</span>
      </div>
      <div className="rounded-[24px] border border-slate-100 bg-white p-4 shadow-[0_2px_10px_rgba(0,0,0,0.02)]">
        <div className="space-y-3">
          {visibleRows.map((row) => {
            const maturity = materialRowPercent(row);
            return (
              <div key={row.key} className="rounded-[18px] border border-slate-100 bg-slate-50/50 px-4 py-4">
                <div className="grid grid-cols-1 xl:grid-cols-[200px_minmax(240px,1fr)] gap-4">
                  <div>
                    <div className="flex items-center gap-2">
                      {maturity >= 50 ? <CheckCircle size={14} className="text-emerald-500 shrink-0" /> : <AlertCircle size={14} className="text-amber-500 shrink-0" />}
                      <span className="text-[14px] font-bold text-slate-800">{row.label}</span>
                    </div>
                    <p className="mt-1 text-[11px] leading-[1.6] text-slate-400">{row.level}</p>
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-3">
                      <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-slate-200/70">
                        <div className={`h-full rounded-full ${maturityBarClass(maturity)}`} style={{ width: `${maturity}%` }} />
                      </div>
                      <span className="w-11 text-right text-[13px] font-bold tabular-nums text-slate-800">{maturity}%</span>
                    </div>
                    <div className="mt-3 grid grid-cols-1 lg:grid-cols-2 gap-3">
                      <div>
                        <div className="text-[10px] font-bold text-emerald-600">已看到</div>
                        <p className="mt-1 text-[12px] leading-[1.75] text-slate-600">{row.seenSummary || '已看到部分资料线索。'}</p>
                      </div>
                      <div>
                        <div className="text-[10px] font-bold text-amber-600">还缺</div>
                        <p className="mt-1 text-[12px] leading-[1.75] text-slate-600">{row.missingSummary || '还缺能持续复盘这类资料的记录。'}</p>
                      </div>
                    </div>
                    {row.unlockedValue && (
                      <div className="mt-3 rounded-[14px] bg-blue-50/70 px-3 py-2 text-[11px] leading-[1.65] text-blue-700">
                        {row.unlockedValue}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
        {rows.length > 8 && (
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            className="mt-4 w-full rounded-[14px] border border-slate-100 bg-white px-4 py-2 text-[12px] font-bold text-blue-600 hover:bg-blue-50 transition-colors"
          >
            {expanded ? '收起资料类型' : `查看更多资料类型（${rows.length - 8}）`}
          </button>
        )}
      </div>
    </section>
  );
}

function AssetScoreBreakdownPanel({ detail }: { detail: DigitalAssetClientDetail }) {
  // 5 项评分指标改用业务语言。原工程语言（结构完整度/可计算度/证据链强度/时间连续性/反馈结果关系）
  // 对非技术读者太陌生。这里翻译成「组织信息是否齐全 / AI 能否给出可计算判断 / 证据是否找得到 /
  // 时间线是否连贯 / 做完是否回灌系统」—— 用户能直接读懂该改进什么。
  const items = [
    { key: 'structuralCompleteness', label: '组织信息齐不齐', hint: '组织、项目、对象、过程、反馈 5 类资料的覆盖', value: detail.scoreBreakdown?.structuralCompleteness ?? 0 },
    { key: 'computability', label: 'AI 能不能给出准确判断', hint: '资料是否含有可被检索、可被引用的结构化片段', value: detail.scoreBreakdown?.computable ?? 0 },
    { key: 'evidenceChain', label: '证据找不找得到', hint: '判断和决策能否追溯到具体原文段落', value: detail.scoreBreakdown?.evidenceChain ?? 0 },
    { key: 'timeContinuity', label: '时间线连不连续', hint: '不同时期的资料是否能串成演进脉络', value: detail.scoreBreakdown?.timeContinuity ?? 0 },
    { key: 'resultFeedbackLoop', label: '做完有没有回灌系统', hint: '任务执行结果、复盘、反馈是否进入了知识库', value: detail.scoreBreakdown?.resultFeedbackLoop ?? 0 },
  ];
  return (
    <section className="mt-6 rounded-[24px] border border-slate-100 bg-white p-5 shadow-[0_2px_10px_rgba(0,0,0,0.02)]">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-[15px] font-bold text-slate-800">AI 学习能力体检</h2>
          <p className="mt-1 text-[12px] leading-6 text-slate-400">这 5 项决定了 AI 能不能针对你的组织给出靠谱判断。颜色越深，AI 在这一项越自信。</p>
        </div>
        <span className="rounded-full bg-slate-50 px-3 py-1 text-[11px] font-bold text-slate-500" title={detail.scoreMethodVersion || 'typed-profile-v2'}>体检维度</span>
      </div>
      <div className="mt-4 grid grid-cols-1 md:grid-cols-5 gap-3">
        {items.map((item) => (
          <div key={item.key} className="rounded-[16px] bg-slate-50 px-3.5 py-3" title={item.hint}>
            <div className="flex items-start justify-between gap-2">
              <span className="text-[11px] font-bold text-slate-600 leading-tight">{item.label}</span>
              <span className="text-[12px] font-bold text-slate-800 shrink-0">{item.value}%</span>
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-200">
              <div className={`h-full rounded-full ${maturityBarClass(item.value)}`} style={{ width: `${clampPercent(item.value)}%` }} />
            </div>
            <p className="mt-2 text-[10px] leading-[1.5] text-slate-400">{item.hint}</p>
          </div>
        ))}
      </div>
      {(detail.scoreRationale || []).length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {(detail.scoreRationale || []).slice(0, 4).map((item, index) => (
            <span key={`${item}-${index}`} className="rounded-full bg-blue-50 px-3 py-1 text-[11px] font-semibold text-blue-600">
              {item}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}

function NextBestDepositPanel({ suggestions }: { suggestions: DigitalAssetClientDetail['nextBestDeposits'] }) {
  const visible = (suggestions || []).slice(0, 4);
  if (!visible.length) return null;
  return (
    <section className="mt-8 rounded-[24px] border border-slate-100 bg-white p-5 shadow-[0_2px_10px_rgba(0,0,0,0.02)]">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-[15px] font-bold text-slate-800">下一步最值得沉淀</h2>
        <span className="text-[12px] font-bold text-slate-400">{visible.length} 项</span>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        {visible.map((item) => (
          <div key={`${item.dimensionKey}-${item.title}`} className="rounded-[18px] border border-slate-100 bg-slate-50/50 px-4 py-4">
            <div className="text-[13px] font-bold leading-6 text-slate-800">{item.title}</div>
            <p className="mt-2 text-[12px] leading-[1.75] text-slate-600">{item.reason}</p>
            {item.analysisValueUnlocked && (
              <p className="mt-3 rounded-[14px] bg-blue-50 px-3 py-2 text-[11px] leading-[1.65] text-blue-700">
                {item.analysisValueUnlocked}
              </p>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

function DigitalAssetMetricStrip({ metrics }: { metrics: DigitalAssetMetric[] }) {
  const visible = metrics.filter((metric) => ['documents', 'memoryFacts', 'eventLines', 'evidenceCards', 'themeClusters', 'openQuestions', 'judgments'].includes(metric.key));
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-2.5">
      {visible.map((metric) => (
        <div key={metric.key} className="rounded-[16px] border border-slate-100 bg-white/80 px-3.5 py-3">
          <div className="text-[10px] font-bold text-slate-400">{metric.label}</div>
          <div className="mt-1 flex items-baseline gap-1.5">
            <span className="text-[20px] font-bold text-slate-800 tabular-nums">{metric.value}</span>
            {metric.hint && <span className="text-[10px] font-semibold text-slate-400">{metric.hint}</span>}
          </div>
        </div>
      ))}
    </div>
  );
}

function formatNarrativeTime(value?: string | null) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function narrativeAuditCounts(narrative?: DigitalAssetNarrative | null) {
  const counts = narrative?.materialAudit?.counts;
  if (!counts || typeof counts !== 'object') return [];
  const source = counts as Record<string, unknown>;
  const items = [
    ['documents', '原始资料'],
    ['v2Documents', '结构化资料'],
    ['v2Ready', '已解析'],
    ['eventLines', '事件线'],
    ['tasks', '任务'],
    ['meetings', '会议'],
    ['judgmentVersions', '判断版本'],
  ] as const;
  return items
    .map(([key, label]) => ({ key, label, value: Number(source[key] || 0) }))
    .filter((item) => item.value > 0);
}

function DigitalAssetNarrativeMarkdown({ content }: { content: string }) {
  const lines = content.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (!lines.length) return null;
  return (
    <div className="space-y-3 text-[14px] leading-[1.9] text-slate-700">
      {lines.map((line, index) => {
        const heading = line.replace(/^#{1,3}\s*/, '');
        if (/^#{1,3}\s+/.test(line)) {
          return <h4 key={`${line}-${index}`} className="pt-2 text-[13px] font-bold text-slate-900">{heading}</h4>;
        }
        const bullet = line.replace(/^[-*]\s+/, '').replace(/^\d+[.、]\s*/, '');
        if (bullet !== line) {
          return (
            <p key={`${line}-${index}`} className="pl-3 border-l-2 border-blue-100 text-slate-700">
              {bullet}
            </p>
          );
        }
        return <p key={`${line}-${index}`}>{line}</p>;
      })}
    </div>
  );
}

function DigitalAssetNarrativePanel({
  narrative,
  loading,
  error,
  onRefresh,
}: {
  narrative?: DigitalAssetNarrative | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}) {
  const counts = narrativeAuditCounts(narrative);
  const hasContent = Boolean(narrative?.contentMarkdown?.trim());
  return (
    <section className="rounded-[24px] border border-blue-100 bg-white px-5 py-5 sm:px-6 shadow-[0_8px_28px_rgba(15,23,42,0.04)]">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Sparkles size={16} className="text-blue-600" />
            <h3 className="text-[15px] font-bold text-slate-900">系统读完资料后的判断</h3>
          </div>
          <p className="mt-1 text-[12px] text-slate-400">
            {hasContent ? `生成时间 ${formatNarrativeTime(narrative?.generatedAt)}` : '还没有生成资料体检内容'}
          </p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          disabled={loading}
          className="inline-flex h-9 items-center justify-center gap-2 rounded-full border border-blue-100 bg-blue-50 px-4 text-[12px] font-bold text-blue-600 transition-colors hover:bg-blue-100 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          {hasContent ? '重新生成' : '生成内容'}
        </button>
      </div>

      {error && (
        <div className="mt-4 rounded-[16px] border border-amber-100 bg-amber-50 px-4 py-3 text-[12px] font-medium leading-relaxed text-amber-700">
          {error}
        </div>
      )}

      {hasContent ? (
        <div className="mt-5 space-y-5">
          {counts.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {counts.map((item) => (
                <span key={item.key} className="rounded-full bg-slate-50 px-3 py-1 text-[11px] font-semibold text-slate-500">
                  {item.label} {item.value.toLocaleString()}
                </span>
              ))}
            </div>
          )}
          <DigitalAssetNarrativeMarkdown content={narrative?.contentMarkdown || ''} />
          {(narrative?.qualityWarnings || []).length > 0 && (
            <div className="border-t border-slate-100 pt-4">
              <div className="mb-2 text-[12px] font-bold text-slate-500">资料质量提示</div>
              <div className="space-y-1.5">
                {(narrative?.qualityWarnings || []).slice(0, 4).map((warning, index) => (
                  <p key={`${warning}-${index}`} className="text-[12px] leading-relaxed text-slate-500">
                    {warning}
                  </p>
                ))}
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="mt-5 rounded-[18px] bg-slate-50 px-4 py-4 text-[13px] leading-relaxed text-slate-500">
          点击生成内容后，系统会读取当前客户的数据中心资料，给出一段更直白的资料体检说明。
        </div>
      )}
    </section>
  );
}

// ─── 战略陪伴 · 客户脉搏 (Phase 1 克制版主页) ───
// 设计原则: 前台只放用户做事需要看的 (本周动态 / 待办 / 卡点),
// 不炫耀后台知道多少. 后端 LLM 推理留给 Phase 2.

interface PulseColumnProps {
  icon: React.ReactNode;
  label: string;
  count: number;
  children: React.ReactNode;
}

function PulseColumn({ icon, label, count, children }: PulseColumnProps) {
  return (
    <div className="min-w-0">
      <header className="flex items-center gap-2 mb-3">
        {icon}
        <h3 className="text-[13px] font-bold text-slate-700">{label}</h3>
        {count > 0 && <span className="text-[10px] font-bold text-slate-400">({count})</span>}
      </header>
      <div className="space-y-2">{children}</div>
    </div>
  );
}

function PulseEmptyHint({ text }: { text: string }) {
  return <div className="text-[11px] text-slate-400 py-1">{text}</div>;
}

function PulseEventCard({ event }: { event: StrategicPulseEvent }) {
  const tone =
    event.impact === 'advance'
      ? { border: 'border-l-emerald-500', text: 'text-emerald-600', label: '推进' }
      : event.impact === 'block'
        ? { border: 'border-l-rose-500', text: 'text-rose-600', label: '阻塞' }
        : { border: 'border-l-slate-300', text: 'text-slate-500', label: '中性' };
  const dateLabel = (event.occurredAt || '').slice(0, 10);
  return (
    <div className={`text-[12px] leading-relaxed border-l-2 ${tone.border} pl-3 py-1`}>
      <div className="font-medium text-slate-800 line-clamp-2">{event.title}</div>
      <div className="mt-0.5 flex items-center gap-1.5 text-[10px] font-bold text-slate-400">
        <span className={tone.text}>{tone.label}</span>
        {dateLabel && <span>· {dateLabel}</span>}
      </div>
    </div>
  );
}

function PulseTodoCard({ todo }: { todo: StrategicPulseTodo }) {
  const isOverdue = todo.urgency === 'overdue';
  const isToday = todo.urgency === 'today';
  const isThisWeek = todo.urgency === 'this_week';
  const borderClass = isOverdue
    ? 'border-l-rose-500 bg-rose-50/40'
    : isToday
      ? 'border-l-amber-500 bg-amber-50/40'
      : isThisWeek
        ? 'border-l-blue-400'
        : 'border-l-slate-300';
  const textColor = isOverdue
    ? 'text-rose-600'
    : isToday
      ? 'text-amber-600'
      : isThisWeek
        ? 'text-blue-600'
        : 'text-slate-400';
  const urgencyLabel =
    isOverdue && todo.daysUntilDue !== null
      ? `已逾期 ${-todo.daysUntilDue} 天`
      : isToday
        ? '今日到期'
        : isThisWeek && todo.daysUntilDue !== null
          ? `还剩 ${todo.daysUntilDue} 天`
          : todo.dueDate || '无期限';
  return (
    <div className={`text-[12px] leading-relaxed border-l-2 ${borderClass} pl-3 py-1`}>
      <div className="font-medium text-slate-800 line-clamp-2">{todo.title}</div>
      <div className="mt-0.5 flex items-center gap-1.5 text-[10px] font-bold">
        <span className={textColor}>{urgencyLabel}</span>
        {todo.eventLineName && (
          <span className="text-slate-400 line-clamp-1">· {todo.eventLineName}</span>
        )}
      </div>
    </div>
  );
}

function PulseBlockerCard({ blocker }: { blocker: StrategicPulseBlocker }) {
  return (
    <div className="text-[12px] leading-relaxed border-l-2 border-l-amber-400 bg-amber-50/40 pl-3 py-1">
      <div className="font-medium text-slate-800 line-clamp-2">{blocker.title}</div>
      <div className="mt-0.5 text-[10px] font-bold text-amber-700">停滞 {blocker.stuckDays} 天</div>
      {blocker.reason && <div className="mt-1 text-[11px] text-slate-600 line-clamp-2">{blocker.reason}</div>}
    </div>
  );
}

function dedupePulseTodos(todos: StrategicPulseTodo[]): StrategicPulseTodo[] {
  // 现状数据存在重复任务 (测试机构B测试数据); 前端按 title+dueDate 去重以减少噪音
  const seen = new Set<string>();
  const result: StrategicPulseTodo[] = [];
  for (const t of todos) {
    const key = `${t.title}|${t.dueDate || ''}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(t);
  }
  return result;
}

function ClientStrategicPulseSection({ clientId }: { clientId: string }) {
  const [pulse, setPulse] = useState<StrategicPulse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMsg, setRefreshMsg] = useState<string | null>(null);

  const loadPulse = useCallback((mountedRef?: { current: boolean }) => {
    setLoading(true);
    setError(null);
    return getClientStrategicPulse(clientId)
      .then((result) => {
        if (!mountedRef || mountedRef.current) setPulse(result);
      })
      .catch((err) => {
        if (!mountedRef || mountedRef.current) {
          setError(err instanceof Error ? err.message : '加载失败');
          setPulse(null);
        }
      })
      .finally(() => {
        if (!mountedRef || mountedRef.current) setLoading(false);
      });
  }, [clientId]);

  useEffect(() => {
    const mountedRef = { current: true };
    void loadPulse(mountedRef);
    return () => {
      mountedRef.current = false;
    };
  }, [loadPulse]);

  // 用户手动触发分析 → 入队 analysis_job → worker 异步跑 projection → evidence_cards
  const handleRefreshUnderstanding = useCallback(async () => {
    if (refreshing) return;
    setRefreshing(true);
    setRefreshMsg(null);
    try {
      await createAnalysisJob({
        jobType: 'strategy_pack',
        clientId,
        scopeType: 'client',
        scopeId: clientId,
        triggerType: 'manual',
        question: 'manual:refresh_understanding',
        intentProfile: 'client_overview',
      });
      setRefreshMsg('✓ 已请求 AI 重新理解,几分钟后再刷新页面查看新动态');
      // 5 秒后清掉提示, 用户可点"再刷新"重看
      window.setTimeout(() => setRefreshMsg(null), 8000);
    } catch (err) {
      setRefreshMsg(err instanceof Error ? `请求失败:${err.message}` : '请求失败');
    } finally {
      setRefreshing(false);
    }
  }, [clientId, refreshing]);

  if (loading) {
    return (
      <section className="rounded-[24px] border border-slate-200 bg-white px-6 py-5 shadow-sm">
        <div className="text-[12px] text-slate-400">正在读取客户脉搏...</div>
      </section>
    );
  }

  if (error || !pulse) {
    return (
      <section className="rounded-[24px] border border-amber-100 bg-amber-50 px-6 py-5">
        <div className="text-[12px] text-amber-700">客户脉搏暂不可用{error ? `（${error}）` : ''}</div>
      </section>
    );
  }

  const dedupedTodos = dedupePulseTodos(pulse.upcomingTodos);

  return (
    <section className="rounded-[24px] border border-slate-200 bg-white px-6 py-5 sm:px-8 sm:py-6 shadow-sm">
      {/* Header: 标题 + 刷新理解按钮(用户兜底入队 analysis_job) */}
      <div className="mb-4 flex items-center justify-between gap-3 border-b border-slate-100 pb-3">
        <div className="flex items-center gap-2">
          <Activity size={16} className="text-[#5B7BFE]" />
          <h2 className="text-[14px] font-bold text-slate-900">客户脉搏</h2>
          {refreshMsg && (
            <span className={`ml-2 text-[11px] ${refreshMsg.startsWith('✓') ? 'text-emerald-600' : 'text-rose-600'}`}>
              {refreshMsg}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={() => void handleRefreshUnderstanding()}
          disabled={refreshing}
          title="让 AI 重新读一遍这个客户的所有资料和讲述,刷新本周新动态/承诺/风险"
          className="inline-flex items-center gap-1.5 rounded-xl border border-[#D8E5FF] bg-white px-3 py-1.5 text-[11px] font-bold text-[#4A63CF] shadow-sm hover:border-[#5B7BFE] hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
          {refreshing ? '请求中...' : '让 AI 重新理解'}
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <PulseColumn
          icon={<Activity size={14} className="text-slate-500" />}
          label="本周新动态"
          count={pulse.weeklyEvents.length}
        >
          {pulse.weeklyEvents.length === 0 ? (
            <PulseEmptyHint text="本周无新动态记录" />
          ) : (
            pulse.weeklyEvents.map((event, i) => <PulseEventCard key={i} event={event} />)
          )}
        </PulseColumn>

        <PulseColumn
          icon={<Target size={14} className="text-slate-500" />}
          label="你接下来要做"
          count={dedupedTodos.length}
        >
          {dedupedTodos.length === 0 ? (
            <PulseEmptyHint text="无未完成任务" />
          ) : (
            dedupedTodos.map((todo, i) => <PulseTodoCard key={i} todo={todo} />)
          )}
        </PulseColumn>

        <PulseColumn
          icon={<AlertTriangle size={14} className="text-amber-500" />}
          label="当前卡点"
          count={pulse.currentBlockers.length}
        >
          {pulse.currentBlockers.length === 0 ? (
            <PulseEmptyHint text="暂无卡点" />
          ) : (
            pulse.currentBlockers.map((b, i) => <PulseBlockerCard key={i} blocker={b} />)
          )}
        </PulseColumn>
      </div>
    </section>
  );
}



// ================= TAB CONTENT =================

function DigitalAssetPulsePanel({
  pulse,
  onOpenDetail,
}: {
  pulse?: DigitalAssetPulse | null;
  onOpenDetail: (clientId: string) => void;
}) {
  const weeklyStats = [
    { label: '新记忆', value: pulse?.weeklyNewFacts ?? 0 },
    { label: '新资料', value: pulse?.weeklyNewDocuments ?? 0 },
    { label: '新证据卡', value: pulse?.weeklyNewEvidenceCards ?? 0 },
  ];
  const funnel = pulse?.digestionFunnel || [];
  const highlights = (pulse?.learningHighlights || []).slice(0, 3);
  const alerts = (pulse?.assetAlerts || []).slice(0, 3);
  const activeOrganizations = (pulse?.activeOrganizations || []).slice(0, 4);
  const signalClass = (severity?: string) => {
    if (severity === 'critical') return 'border-rose-100 bg-rose-50/70 text-rose-700';
    if (severity === 'warning') return 'border-amber-100 bg-amber-50/70 text-amber-700';
    return 'border-blue-100 bg-blue-50/70 text-blue-700';
  };
  return (
    <section
      className="mb-6 rounded-[28px] border border-blue-100 bg-white p-6 shadow-[0_12px_36px_rgba(15,23,42,0.05)]"
      style={{
        backgroundImage: 'linear-gradient(135deg, rgba(37,99,235,0.08), rgba(20,184,166,0.06) 42%, rgba(255,255,255,0) 78%), linear-gradient(180deg, #ffffff 0%, #f8fafc 100%)',
      }}
    >
      <div className="flex flex-col gap-5 xl:flex-row xl:items-start xl:justify-between">
        <div className="max-w-4xl">
          <div className="inline-flex items-center gap-2 rounded-full border border-blue-100 bg-blue-50 px-3 py-1 text-[12px] font-bold text-blue-600">
            <BrainCircuit size={14} />
            组织大脑脉搏
          </div>
          <h2 className="mt-4 text-[22px] font-bold tracking-tight text-slate-900">
            AI 最近学到了什么
          </h2>
          <p className="mt-2 text-[14px] font-medium leading-[1.85] text-slate-600">
            {pulse?.headline || '正在读取组织数字资产脉搏。'}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <span className="rounded-full border border-slate-100 bg-white/80 px-3 py-1 text-[11px] font-bold text-slate-500">
              已陪伴 {pulse?.daysAccompanied ?? 0} 天
            </span>
            {weeklyStats.map((item) => (
              <span key={item.label} className="rounded-full border border-emerald-100 bg-emerald-50 px-3 py-1 text-[11px] font-bold text-emerald-600">
                本周 +{item.value.toLocaleString()} {item.label}
              </span>
            ))}
          </div>
        </div>
        {activeOrganizations.length > 0 && (
          <div className="grid min-w-[320px] grid-cols-1 gap-2">
            {activeOrganizations.slice(0, 3).map((item) => (
              <button
                key={item.clientId}
                type="button"
                onClick={() => onOpenDetail(item.clientId)}
                className="rounded-[16px] border border-white/80 bg-white/80 px-4 py-3 text-left shadow-sm transition hover:border-blue-200 hover:bg-white"
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="truncate text-[13px] font-bold text-slate-800">{item.name}</span>
                  <span className="text-[11px] font-bold text-blue-600">{item.maturityScore}%</span>
                </div>
                <div className="mt-1 truncate text-[11px] font-semibold text-slate-400">{item.assetProfileType}</div>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="mt-6 rounded-[20px] border border-slate-100 bg-white/80 p-4">
        <div className="mb-3 flex items-center gap-2">
          <GitBranch size={15} className="text-blue-500" />
          <span className="text-[13px] font-bold text-slate-800">资料消化状态</span>
        </div>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
          {funnel.map((item, index) => (
            <div key={item.key} className="relative rounded-[16px] bg-slate-50 px-3 py-3">
              <div className="text-[10px] font-bold text-slate-400">{item.label}</div>
              <div className="mt-1 text-[20px] font-bold tabular-nums text-slate-800">{item.value.toLocaleString()}</div>
              {index < funnel.length - 1 && (
                <ChevronRight size={14} className="absolute right-2 top-1/2 hidden -translate-y-1/2 text-slate-300 md:block" />
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <div className="rounded-[20px] border border-slate-100 bg-white/80 p-4">
          <div className="mb-3 flex items-center gap-2">
            <Sparkles size={15} className="text-blue-500" />
            <span className="text-[13px] font-bold text-slate-800">本周变聪明的地方</span>
          </div>
          <div className="space-y-2">
            {highlights.length ? highlights.map((item) => (
              <button
                key={`${item.clientId}-${item.title}`}
                type="button"
                onClick={() => item.clientId && onOpenDetail(item.clientId)}
                className="w-full rounded-[16px] border border-blue-100 bg-blue-50/60 px-3 py-3 text-left transition hover:bg-blue-50"
              >
                <div className="text-[12px] font-bold text-blue-700">{item.title}</div>
                <p className="mt-1 line-clamp-2 text-[12px] leading-[1.65] text-slate-600">{item.summary}</p>
              </button>
            )) : (
              <div className="rounded-[16px] bg-slate-50 px-3 py-3 text-[12px] leading-6 text-slate-500">本周还没有明显的新理解，继续沉淀资料后这里会出现变化。</div>
            )}
          </div>
        </div>

        <div className="rounded-[20px] border border-slate-100 bg-white/80 p-4">
          <div className="mb-3 flex items-center gap-2">
            <AlertTriangle size={15} className="text-amber-500" />
            <span className="text-[13px] font-bold text-slate-800">需要处理的信号</span>
          </div>
          <div className="space-y-2">
            {alerts.length ? alerts.map((item) => (
              <button
                key={`${item.clientId}-${item.title}`}
                type="button"
                onClick={() => item.clientId && onOpenDetail(item.clientId)}
                className={`w-full rounded-[16px] border px-3 py-3 text-left transition hover:bg-white ${signalClass(item.severity)}`}
              >
                <div className="text-[12px] font-bold">{item.title}</div>
                <p className="mt-1 line-clamp-2 text-[12px] leading-[1.65] text-slate-600">{item.summary}</p>
              </button>
            )) : (
              <div className="rounded-[16px] bg-slate-50 px-3 py-3 text-[12px] leading-6 text-slate-500">暂时没有明显异常，继续观察资料是否能转成证据和判断。</div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

const ORGANIZATION_DNA_KIND_META: Record<OrganizationDnaV2Kind, { title: string; description: string; icon: React.ReactNode; tone: string }> = {
  stable_dna: {
    title: '稳定画像',
    description: '定位、服务对象、业务体系和表达口径',
    icon: <Layers size={15} />,
    tone: 'border-blue-100 bg-blue-50/70 text-blue-700',
  },
  evolving_dna: {
    title: '近期变化',
    description: '任务、事件线、复盘里的组织能力变化',
    icon: <Activity size={15} />,
    tone: 'border-emerald-100 bg-emerald-50/70 text-emerald-700',
  },
  gap_dna: {
    title: '资料缺口',
    description: '系统还能自动补什么、还缺哪些内部资料',
    icon: <HelpCircle size={15} />,
    tone: 'border-amber-100 bg-amber-50/70 text-amber-700',
  },
  risk_dna: {
    title: '风险边界',
    description: '公开/内部口径、弱证据和事实边界',
    icon: <AlertOctagon size={15} />,
    tone: 'border-rose-100 bg-rose-50/70 text-rose-700',
  },
};

function OrganizationDnaMiniList({ kind, items }: { kind: OrganizationDnaV2Kind; items: OrganizationDnaV2Item[] }) {
  const meta = ORGANIZATION_DNA_KIND_META[kind];
  // 之前 slice(0, 2) 把 evolving/stable/gap/risk 都强制只显示 2 条，
  // 用户看到列表里压着「N」但只见 2 条很委屈。改成内部滚动：
  // 前 2 条始终可见，超过后整个列表区可上下滚动，让用户能看到全部。
  // 这也回应了"最近变化用滑动条而不是独立 tab"的设计。
  return (
    <div className="rounded-[20px] border border-slate-100 bg-white/80 p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className={`inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-[11px] font-bold ${meta.tone}`}>
            {meta.icon}
            {meta.title}
          </div>
          <p className="mt-2 text-[12px] leading-5 text-slate-500">{meta.description}</p>
        </div>
        <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-bold text-slate-500">
          {items.length}
        </span>
      </div>
      <div
        className="mt-4 space-y-2 max-h-[220px] overflow-y-auto pr-1"
        style={{ scrollbarGutter: 'stable' }}
      >
        {items.length ? items.map((item) => (
          <div key={item.id} className="rounded-[16px] border border-slate-100 bg-slate-50/70 px-3 py-3">
            <div className="line-clamp-1 text-[12px] font-bold text-slate-800">{item.title}</div>
            <p className="mt-1 line-clamp-2 text-[12px] leading-[1.65] text-slate-600">
              {item.summary || '已进入组织 DNA 资料层，等待后续资料刷新。'}
            </p>
          </div>
        )) : (
          <div className="rounded-[16px] border border-dashed border-slate-200 bg-slate-50/70 px-3 py-3 text-[12px] leading-6 text-slate-400">
            暂无内容，生成或刷新后这里会出现新的组织资产。
          </div>
        )}
      </div>
      {items.length > 3 && (
        <p className="mt-2 text-[10px] text-slate-400 text-right">↕ 上下滑动查看全部 {items.length} 条</p>
      )}
    </div>
  );
}

function OrganizationDnaPanel({
  snapshot,
  loading,
  refreshing,
  error,
  onRefresh,
}: {
  snapshot: OrganizationDnaV2Snapshot | null;
  loading: boolean;
  refreshing: boolean;
  error: string | null;
  onRefresh: () => void;
}) {
  const itemCount =
    (snapshot?.stableItems.length ?? 0)
    + (snapshot?.evolvingItems.length ?? 0)
    + (snapshot?.gapItems.length ?? 0)
    + (snapshot?.riskItems.length ?? 0);
  const latestRun = snapshot?.latestRun;
  const actionText = itemCount > 0 ? '刷新组织 DNA' : '生成初版';
  return (
    <section className="mb-6 rounded-[28px] border border-slate-100 bg-white p-6 shadow-[0_12px_36px_rgba(15,23,42,0.05)]">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="max-w-3xl">
          <div className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-[12px] font-bold text-slate-600">
            <Bot size={14} />
            组织 DNA v2
          </div>
          <h2 className="mt-4 text-[22px] font-bold tracking-tight text-slate-900">组织级智能资产</h2>
          <p className="mt-2 text-[14px] font-medium leading-[1.85] text-slate-600">
            这里沉淀当前组织自己的定位、近期变化、资料缺口和风险边界。它由系统从组织模型、任务、事件线和复盘中持续刷新，不再作为设置页里的静态文本。
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <span className="rounded-full border border-blue-100 bg-blue-50 px-3 py-1 text-[11px] font-bold text-blue-600">
              已稳定 {snapshot?.confirmedCount ?? 0}
            </span>
            <span className="rounded-full border border-amber-100 bg-amber-50 px-3 py-1 text-[11px] font-bold text-amber-600">
              待积累 {snapshot?.candidateCount ?? 0}
            </span>
            <span className="rounded-full border border-slate-100 bg-slate-50 px-3 py-1 text-[11px] font-bold text-slate-500">
              需刷新 {snapshot?.staleCount ?? 0}
            </span>
            <span className="rounded-full border border-slate-100 bg-white px-3 py-1 text-[11px] font-bold text-slate-400">
              最近刷新 {snapshot?.updatedAt || latestRun?.updatedAt || '尚未生成'}
            </span>
          </div>
          {error && (
            <div className="mt-3 rounded-[14px] border border-rose-100 bg-rose-50 px-3 py-2 text-[12px] font-semibold text-rose-600">
              {error}
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={onRefresh}
          disabled={loading || refreshing}
          className="inline-flex items-center justify-center gap-2 rounded-2xl bg-slate-900 px-4 py-2.5 text-[13px] font-bold text-white shadow-sm transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
          {loading ? '读取中' : refreshing ? '刷新中' : actionText}
        </button>
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 xl:grid-cols-4">
        <OrganizationDnaMiniList kind="stable_dna" items={snapshot?.stableItems ?? []} />
        <OrganizationDnaMiniList kind="evolving_dna" items={snapshot?.evolvingItems ?? []} />
        <OrganizationDnaMiniList kind="gap_dna" items={snapshot?.gapItems ?? []} />
        <OrganizationDnaMiniList kind="risk_dna" items={snapshot?.riskItems ?? []} />
      </div>
    </section>
  );
}

const getThoughtStatusMeta = (thought: StrategicThought): { text: string; className: string } | null => {
  if (thought.status === 'confirmed') return { text: '已确认', className: 'bg-emerald-50 text-emerald-600 border border-emerald-100' };
  if (thought.status === 'task_created') return { text: '已转任务', className: 'bg-blue-50 text-blue-600 border border-blue-100' };
  if (thought.isSystem || thought.scope === 'system') return { text: '系统观察', className: 'bg-slate-100 text-slate-500 border border-slate-200' };
  return null;
};

const INSIGHT_TYPE_META: Record<string, { text: string; className: string }> = {
  strategic_shift: { text: '战略转型', className: 'bg-blue-50 text-blue-600 border border-blue-100' },
  risk_signal: { text: '风险研判', className: 'bg-rose-50 text-rose-600 border border-rose-100' },
  opportunity_window: { text: '机会窗口', className: 'bg-emerald-50 text-emerald-600 border border-emerald-100' },
  execution_bottleneck: { text: '执行瓶颈', className: 'bg-amber-50 text-amber-600 border border-amber-100' },
  narrative_upgrade: { text: '叙事升级', className: 'bg-violet-50 text-violet-600 border border-violet-100' },
  operating_model: { text: '运营模型', className: 'bg-cyan-50 text-cyan-600 border border-cyan-100' },
};

const getInsightTypeMeta = (thought: StrategicThought): { text: string; className: string } => {
  if (thought.insightType && INSIGHT_TYPE_META[thought.insightType]) return INSIGHT_TYPE_META[thought.insightType];
  return { text: '分析信号', className: 'bg-slate-50 text-slate-500 border border-slate-100' };
};

function ThoughtCard({
  thought,
  onCreateTask,
  onReview,
  onToggleFavorite,
  onDelete,
}: {
  thought: StrategicThought;
  onCreateTask?: (payload: ThoughtTaskPayload) => void;
  onReview: (thoughtId: string, action: 'confirm' | 'dismiss', note: string) => Promise<void>;
  onToggleFavorite: (thought: StrategicThought) => Promise<void>;
  onDelete: (thought: StrategicThought) => Promise<void>;
}) {
  const [isEditing, setIsEditing] = useState(false);
  const [reviewText, setReviewText] = useState(thought.review?.note || '');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const statusMeta = getThoughtStatusMeta(thought);
  const typeMeta = getInsightTypeMeta(thought);
  const normalizedLine = _normalizeTextForUI(thought.line) || '系统发现一条分析信号';
  const normalizedInsight = _normalizeTextForUI(thought.insightText || thought.observation) || '系统发现一条值得关注的客户洞察。';
  const normalizedFuture = _normalizeTextForUI(thought.futureJudgment || thought.whyItMatters || '');
  const normalizedAction = _normalizeTextForUI(thought.recommendedAction || thought.suggestion) || '建议将这条洞察转成下一步行动。';

  const handleConfirm = async () => {
    setIsSubmitting(true);
    try {
      await onReview(thought.id, 'confirm', reviewText.trim());
      setIsEditing(false);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDismiss = async () => {
    setIsSubmitting(true);
    try {
      await onReview(thought.id, 'dismiss', reviewText.trim());
      setIsEditing(false);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleCreateTask = () => {
    const today = new Date();
    const endOfWeek = new Date(today);
    endOfWeek.setDate(today.getDate() + (7 - today.getDay()));
    const dueDate = thought.dueDateHint === '本周' ? endOfWeek.toISOString().slice(0, 10) : '';
    onCreateTask?.({
      suggestion: normalizedAction,
      ceoComment: reviewText.trim(),
      thoughtLine: normalizedLine,
      clientId: thought.clientId || '',
      dueDate,
      thoughtId: thought.id,
      sources: thought.sources,
      evidenceCount: thought.evidenceCount,
      confidence: thought.confidence ?? null,
      clientName: thought.clientName,
    });
  };

  return (
    <div className="relative break-inside-avoid rounded-2xl border border-gray-100 bg-white p-5 transition-colors hover:border-gray-200 before:absolute before:left-0 before:top-5 before:bottom-5 before:w-[3px] before:rounded-r-full before:bg-[#5B7BFE]/55">
      <div className="mb-4 flex items-start justify-between gap-4 pl-2">
        <div className="min-w-0 flex-1">
          <div className="text-[9px] font-semibold uppercase tracking-[0.16em] text-gray-400">
            Strategic Signal
          </div>
          <div className={`mt-1 text-[15px] font-light leading-snug tracking-tight ${thought.isSystem ? 'text-gray-700' : 'text-gray-900'}`}>
            {normalizedLine}
          </div>
          {thought.clientName && thought.clientName !== '系统观察' && (
            <p className="mt-1 text-[10.5px] text-gray-400">{thought.clientName}</p>
          )}
        </div>
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
          <span className={`rounded-full px-2 py-[2px] text-[9.5px] font-semibold uppercase tracking-[0.12em] ring-1 ring-inset ${typeMeta.className}`}>
            {typeMeta.text}
          </span>
          {statusMeta && (
            <span className={`rounded-full px-2 py-[2px] text-[9.5px] font-semibold uppercase tracking-[0.12em] ring-1 ring-inset ${statusMeta.className}`}>
              {statusMeta.text}
            </span>
          )}
          <button
            type="button"
            onClick={() => void onToggleFavorite(thought)}
            className={`inline-flex h-7 w-7 items-center justify-center rounded-full transition-colors ring-1 ring-inset ${
              thought.isFavorite
                ? 'ring-amber-200 bg-amber-50/70 text-amber-500'
                : 'ring-gray-200 bg-white text-gray-400 hover:text-amber-500 hover:ring-amber-200'
            }`}
            title={thought.isFavorite ? '取消收藏' : '收藏'}
            aria-label={thought.isFavorite ? '取消收藏' : '收藏'}
          >
            <Star size={13} fill={thought.isFavorite ? 'currentColor' : 'none'} />
          </button>
          <button
            type="button"
            onClick={() => void onDelete(thought)}
            className="inline-flex h-7 w-7 items-center justify-center rounded-full bg-white text-gray-400 transition-colors ring-1 ring-inset ring-gray-200 hover:text-rose-500 hover:ring-rose-200"
            title="删除"
            aria-label="删除"
          >
            <Trash2 size={13} />
          </button>
        </div>
      </div>

      <div className="mb-4 pl-2">
        <p className="text-[13px] leading-[1.85] text-gray-700">{normalizedInsight}</p>
      </div>

      {(normalizedFuture || normalizedAction) && (
        <div className="mb-4 space-y-2 pl-2">
          {normalizedFuture && (
            <div className="rounded-xl bg-[#FAFAFA] px-4 py-2.5 ring-1 ring-inset ring-gray-100">
              <div className="text-[9px] font-semibold uppercase tracking-[0.16em] text-gray-400">未来判断</div>
              <p className="mt-1 text-[12px] leading-[1.7] text-gray-700">{normalizedFuture}</p>
            </div>
          )}
          {normalizedAction && (
            <div className="rounded-xl bg-[#5B7BFE]/4 px-4 py-2.5 ring-1 ring-inset ring-[#5B7BFE]/15">
              <div className="text-[9px] font-semibold uppercase tracking-[0.16em] text-[#5B7BFE]">建议动作</div>
              <p className="mt-1 text-[12px] leading-[1.7] text-gray-700">{normalizedAction}</p>
            </div>
          )}
        </div>
      )}

      <div className="mt-4 space-y-3 border-t border-gray-100 pt-4 pl-2">
        {(thought.review?.note || reviewText) && (
          <div className="rounded-xl bg-[#FAFAFA] px-4 py-2.5 ring-1 ring-inset ring-gray-100">
            <div className="text-[9px] font-semibold uppercase tracking-[0.16em] text-gray-500">
              {thought.review?.status === 'confirmed' ? '我的已确认判断' : '我的备注'}
            </div>
            <div className="mt-1 text-[12px] leading-[1.7] text-gray-700">{thought.review?.note || reviewText}</div>
          </div>
        )}

        {isEditing ? (
          <div className="rounded-xl bg-[#FAFAFA] p-3 ring-1 ring-inset ring-gray-100">
            <textarea
              className="w-full min-h-[72px] resize-y rounded-lg border border-gray-200 bg-white p-3 text-[13px] text-gray-700 outline-none focus:border-[#5B7BFE]"
              placeholder="补充你对这条洞察的判断..."
              value={reviewText}
              onChange={(e) => setReviewText(e.target.value)}
              autoFocus
            />
            <div className="mt-2.5 flex gap-2">
              <button
                type="button"
                onClick={handleDismiss}
                disabled={isSubmitting}
                className="rounded-md px-3 py-1.5 text-[11px] font-medium text-rose-600 ring-1 ring-inset ring-rose-200 hover:bg-rose-50/60 transition-colors disabled:opacity-60"
              >
                不准确
              </button>
              <button
                type="button"
                onClick={handleCreateTask}
                className="rounded-md px-3 py-1.5 text-[11px] font-medium text-[#3652c9] ring-1 ring-inset ring-[#5B7BFE]/30 hover:bg-[#5B7BFE]/8 transition-colors"
              >
                转为任务
              </button>
              <button
                type="button"
                onClick={handleConfirm}
                disabled={isSubmitting}
                className="ml-auto rounded-md bg-[#5B7BFE] px-4 py-1.5 text-[12px] font-medium text-white hover:bg-[#4A63CF] transition-colors disabled:opacity-60"
              >
                采纳为判断
              </button>
            </div>
          </div>
        ) : (
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setIsEditing(true)}
              className="flex flex-1 items-center gap-2 rounded-lg bg-white px-4 py-2 text-[12px] font-medium text-gray-500 ring-1 ring-inset ring-gray-200 hover:text-gray-700 hover:ring-[#5B7BFE]/35 transition-colors"
            >
              <PenLine size={13} className="text-gray-400" />
              采纳 / 备注…
            </button>
            <button
              type="button"
              onClick={handleCreateTask}
              className="shrink-0 inline-flex items-center gap-1.5 rounded-lg bg-[#5B7BFE]/8 px-4 py-2 text-[12px] font-medium text-[#3652c9] ring-1 ring-inset ring-[#5B7BFE]/30 hover:bg-[#5B7BFE]/12 transition-colors"
            >
              <ClipboardList size={13} />
              转为任务
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function ThoughtsTab({
  thoughts,
  loading,
  error,
  selectedClientId,
  selectedClientName,
  selectedProjectModuleId,
  selectedProjectModuleName,
  onReview,
  onCreateTask,
  onRetry,
  onToggleFavorite,
  onDelete,
}: {
  thoughts: StrategicThought[];
  loading: boolean;
  error: string | null;
  selectedClientId: string | null;
  selectedClientName?: string | null;
  selectedProjectModuleId?: string | null;
  selectedProjectModuleName?: string | null;
  onReview: (thoughtId: string, action: 'confirm' | 'dismiss', note: string) => Promise<void>;
  onCreateTask?: (payload: ThoughtTaskPayload) => void;
  onRetry: () => void;
  onToggleFavorite: (thought: StrategicThought) => Promise<void>;
  onDelete: (thought: StrategicThought) => Promise<void>;
}) {
  if (loading) {
    return (
      <div className="bg-white border border-slate-100 rounded-[20px] px-5 py-6 text-[13px] text-slate-500">
        正在加载思考与研判...
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-white border border-red-100 rounded-[20px] px-5 py-6">
        <p className="text-[13px] font-semibold text-red-500">研判加载失败</p>
        <p className="text-[12px] text-slate-500 mt-1">{error}</p>
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 text-[12px] font-bold px-3 py-1.5 rounded-full border border-red-200 bg-red-50 text-red-600 hover:bg-red-100"
        >
          重试
        </button>
      </div>
    );
  }

  if (!thoughts.length) {
    return (
      <div className="bg-white border border-slate-100 rounded-[20px] px-5 py-6">
        <p className="text-[13px] leading-7 text-slate-500">
          {selectedClientId
            ? `${selectedProjectModuleName || selectedClientName || '这个客户'}当前还没有足够材料形成高价值研判。点页面顶部"让 AI 重新理解"再生成。`
            : '当前还没有足够材料形成高价值研判。'}
        </p>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-4 flex items-center gap-3 px-1">
        <div className="text-[12px] text-slate-400">
          {selectedProjectModuleId
            ? `${selectedProjectModuleName || '当前项目'} · ${thoughts.length} 条洞察`
            : selectedClientId
              ? `${selectedClientName || '当前客户'} · ${thoughts.length} 条洞察`
              : `全部客户 · ${thoughts.length} 条洞察`}
        </div>
      </div>
      <div className="columns-1 md:columns-2 gap-5 space-y-5">
        {thoughts.map((thought) => (
          <ThoughtCard
            key={thought.id}
            thought={thought}
            onCreateTask={onCreateTask}
            onReview={onReview}
            onToggleFavorite={onToggleFavorite}
            onDelete={onDelete}
          />
        ))}
      </div>
    </div>
  );
}

function ThoughtScopeSelect({
  clients,
  selectedClientId,
  onChange,
  disabled,
}: {
  clients: Array<{ id: string; name: string }>;
  selectedClientId: string;
  onChange: (clientId: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const options = useMemo(() => [{ id: '', name: '全部客户' }, ...clients], [clients]);
  const selectedClient = options.find((client) => client.id === selectedClientId) || options[0];

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  const handleSelect = (clientId: string) => {
    onChange(clientId);
    setOpen(false);
  };

  return (
    <div ref={rootRef} className="relative flex items-center gap-2 rounded-2xl border border-slate-200 bg-white/90 px-3 py-2 shadow-sm">
      <Users size={15} className="text-blue-500 shrink-0" />
      <span className="text-[12px] font-semibold text-slate-500 whitespace-nowrap">客户/项目</span>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((value) => !value)}
        className="flex min-w-[130px] max-w-[220px] items-center justify-between gap-2 bg-transparent text-left text-[13px] font-semibold text-slate-700 outline-none disabled:cursor-not-allowed disabled:opacity-50"
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className="truncate">{selectedClient.name}</span>
        <ChevronDown size={14} className={`shrink-0 text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && !disabled && (
        <div
          role="listbox"
          className="absolute right-0 top-full z-50 mt-2 w-[240px] max-h-[360px] overflow-y-auto rounded-2xl border border-slate-200 bg-white p-1.5 shadow-[0_18px_60px_rgba(15,23,42,0.18)]"
        >
          {options.map((client) => {
            const selected = client.id === selectedClientId;
            return (
              <button
                key={client.id || 'all_clients'}
                type="button"
                role="option"
                aria-selected={selected}
                onClick={() => handleSelect(client.id)}
                className={`flex h-9 w-full items-center justify-between gap-2 rounded-xl px-3 text-left text-[13px] font-semibold transition-colors ${
                  selected ? 'bg-blue-500 text-white' : 'text-slate-700 hover:bg-blue-50 hover:text-blue-600'
                }`}
              >
                <span className="truncate">{client.name}</span>
                {selected && <Check size={14} className="shrink-0" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}


// ================= MAIN EXPORT =================

export type StrategicBrainViewProps = {
  clients?: Array<{ id: string; name: string }>;
  currentClientId?: string | null;
  onClientChange?: (clientId: string) => void;
  onCreateTaskFromThought?: (payload: ThoughtTaskPayload) => void;
  /** UnifiedTodoSection 里点 → 时触发, 由 App 接住打开原任务编辑器并预填. */
  onPromoteTodo?: (todo: import('../../lib/api').UnifiedTodo) => void;
  flash?: (level: 'success' | 'error' | 'info', message: string) => void;
};

// ───────────────────────────────────────────────────────────────────────────
// Tab 组件区（事实澄清 / 矛盾 / 最近变化等）
// 历史：原本还有「资料健康」「输出沉淀」两个 tab，业务上不再需要 → 2026-05-17 移除。
// ───────────────────────────────────────────────────────────────────────────

// 备注：原 RecentChangesTab 已删除。最近变化信息保留在客户档案 tab 内
// 的 OrganizationDnaMiniList「近期变化」mini list 里，那里改成可滚动容器
// 让用户能看到更多 evolving 条目。独立 tab 反而和客户档案 mini list 重复，
// 让信息架构臃肿。

function TabClientPicker({
  clientOptions,
  selectedClientId,
  onClientChange,
}: {
  clientOptions: Array<{ id: string; name: string }>;
  selectedClientId: string;
  onClientChange: (id: string) => void;
}) {
  return (
    <select
      value={selectedClientId}
      onChange={(e) => onClientChange(e.target.value)}
      className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-[12px] font-bold text-slate-700 focus:outline-none focus:border-blue-300"
    >
      <option value="">— 选择客户 —</option>
      {clientOptions.map((c) => (
        <option key={c.id} value={c.id}>{c.name}</option>
      ))}
    </select>
  );
}

// 仅显示用户可编辑的业务文件类型。
// 排除：
// - .md / .txt（系统生成的客户档案、事件线快照、元数据等）
// - 没有扩展名的（如 client_overview / 事件线快照名）
// 这些是数据中心内部使用的"系统标识"，用户不应在重复管理界面看到/删除它们。
const USER_EDITABLE_FILE_EXTENSIONS = new Set([
  'doc', 'docx',
  'xls', 'xlsx', 'csv',
  'ppt', 'pptx',
  'pdf',
  'png', 'jpg', 'jpeg', 'webp', 'gif',
]);

function isUserEditableFile(fileName: string): boolean {
  const lastDot = fileName.lastIndexOf('.');
  if (lastDot < 0 || lastDot === fileName.length - 1) return false;
  return USER_EDITABLE_FILE_EXTENSIONS.has(fileName.slice(lastDot + 1).toLowerCase());
}

function filterUserEditableGroups(groups: DuplicateDocumentGroup[]): DuplicateDocumentGroup[] {
  return groups
    .map((g) => {
      // 任一份不是用户文件 → 整组隐藏（混杂业务+系统的组先放过，避免误操作）
      const docs = g.documents.filter((d) => isUserEditableFile(d.fileName));
      return { ...g, documents: docs, count: docs.length };
    })
    .filter((g) => g.documents.length >= 2);
}

export function DuplicateDocumentsSection({
  clientId,
  flash,
  hideWhenEmpty = false,
}: {
  clientId: string;
  flash?: (level: 'success' | 'error' | 'info', message: string) => void;
  hideWhenEmpty?: boolean;
}) {
  const [groups, setGroups] = useState<DuplicateDocumentGroup[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busyGroupKey, setBusyGroupKey] = useState<string>('');
  const [busyDocId, setBusyDocId] = useState<string>('');
  const clientIdRef = useRef(clientId);
  useEffect(() => {
    clientIdRef.current = clientId;
  }, [clientId]);

  const reload = useCallback(() => {
    if (!clientId) { setGroups([]); return; }
    const capturedClientId = clientId;
    setLoading(true);
    setErr(null);
    getClientDuplicateDocuments(clientId)
      .then((raw) => {
        if (clientIdRef.current === capturedClientId) {
          setGroups(filterUserEditableGroups(raw));
        }
      })
      .catch((e) => {
        if (clientIdRef.current === capturedClientId) {
          setErr(e instanceof Error ? e.message : '加载重复文件失败');
        }
      })
      .finally(() => {
        if (clientIdRef.current === capturedClientId) setLoading(false);
      });
  }, [clientId]);

  useEffect(() => { reload(); }, [reload]);

  const totalRefsOf = (d: DuplicateDocumentItem) =>
    d.refTaskAttachmentCount + d.refEvidenceCardCount + d.refAtomicFactCount;

  // 单卡片上 🗑 删除一份：
  // - 不 reload（避免整个 list 重排，让用户找不到下一个想删的卡片）
  // - 直接在本地 state 把这份移除，其他卡片位置保持不变
  // - 引用自动迁到剩下的版本（migrateReferences=true）
  const handleDeleteOne = async (g: DuplicateDocumentGroup, doc: DuplicateDocumentItem) => {
    const remaining = g.documents.filter((d) => d.id !== doc.id);
    if (remaining.length === 0) {
      flash?.('error', '这是该组最后一份文件，无法单删 —— 请用整组「全部删除」');
      return;
    }
    if (!window.confirm(`删除「${doc.fileName.slice(0, 40)}」？\n\n文件进回收站，30 天内可恢复。\n关联到这份的任务附件/证据卡会自动迁移到剩下的版本。`)) return;
    setBusyDocId(doc.id);
    try {
      await resolveDuplicateDocuments(clientId, {
        groupKey: g.groupKey,
        action: 'delete_others',
        keepV2DocumentIds: remaining.map((d) => d.id),
        deleteV2DocumentIds: [doc.id],
        migrateReferences: true,
      });
      flash?.('success', '已删除，文件进回收站');
      // 不 reload —— 直接在本地 state 移除这份，保持其他卡片相对位置稳定
      setGroups((prev) =>
        prev
          .map((grp) => {
            if (grp.groupKey !== g.groupKey) return grp;
            const newDocs = grp.documents.filter((d) => d.id !== doc.id);
            return { ...grp, documents: newDocs, count: newDocs.length };
          })
          .filter((grp) => grp.documents.length >= 2),
      );
    } catch (e) {
      flash?.('error', e instanceof Error ? e.message : '删除失败');
    } finally {
      setBusyDocId('');
    }
  };

  // 整组「全部删除」：所有重复文件进回收站，引用自动 SET NULL（无目标可迁）
  const handleDeleteAll = async (g: DuplicateDocumentGroup) => {
    if (!window.confirm(
      `确认全部删除「${g.fileName.slice(0, 40)}」的 ${g.count} 份重复文件？\n\n所有文件进回收站，30 天内可恢复。\n关联到这些文件的任务附件 / 证据卡会变成「附件缺失」。`,
    )) return;
    setBusyGroupKey(g.groupKey);
    try {
      await resolveDuplicateDocuments(clientId, {
        groupKey: g.groupKey,
        action: 'delete_others',
        keepV2DocumentIds: [],
        deleteV2DocumentIds: g.documents.map((d) => d.id),
        migrateReferences: false,
      });
      flash?.('success', `已全部删除 ${g.count} 份（进回收站）`);
      // 整组消失了，直接从本地移除，避免 reload 重排其他 group
      setGroups((prev) => prev.filter((grp) => grp.groupKey !== g.groupKey));
    } catch (e) {
      flash?.('error', e instanceof Error ? e.message : '操作失败');
    } finally {
      setBusyGroupKey('');
    }
  };

  // 整组「保留最新」：自动按 importedAt 找最晚的那份，其他全删
  // 处理完后这组只剩 1 份不再是"重复"，直接从本地 state 移除该组，
  // 避免 reload 让其他 group 重新排序。
  const handleKeepLatest = async (g: DuplicateDocumentGroup) => {
    const sorted = [...g.documents].sort((a, b) => (b.importedAt || '').localeCompare(a.importedAt || ''));
    const latest = sorted[0];
    const toDelete = sorted.slice(1);
    if (toDelete.length === 0) return;
    if (!window.confirm(`保留最新版（${latest.importedAt.slice(0, 16).replace('T', ' ')}），其他 ${toDelete.length} 份进回收站？`)) return;
    setBusyGroupKey(g.groupKey);
    try {
      await resolveDuplicateDocuments(clientId, {
        groupKey: g.groupKey,
        action: 'delete_others',
        keepV2DocumentIds: [latest.id],
        deleteV2DocumentIds: toDelete.map((d) => d.id),
        migrateReferences: true,
      });
      flash?.('success', `已保留最新版，其他 ${toDelete.length} 份进回收站`);
      setGroups((prev) => prev.filter((grp) => grp.groupKey !== g.groupKey));
    } catch (e) {
      flash?.('error', e instanceof Error ? e.message : '操作失败');
    } finally {
      setBusyGroupKey('');
    }
  };

  // 「全部保留」：标记 group_key，下次扫描跳过
  const handleKeepAll = async (g: DuplicateDocumentGroup) => {
    setBusyGroupKey(g.groupKey);
    try {
      await resolveDuplicateDocuments(clientId, {
        groupKey: g.groupKey,
        action: 'keep_all',
        keepV2DocumentIds: g.documents.map((d) => d.id),
        deleteV2DocumentIds: [],
        migrateReferences: false,
      });
      flash?.('success', '已标记「全部保留」，下次不再提示这组');
      setGroups((prev) => prev.filter((grp) => grp.groupKey !== g.groupKey));
    } catch (e) {
      flash?.('error', e instanceof Error ? e.message : '操作失败');
    } finally {
      setBusyGroupKey('');
    }
  };

  const handleOpenFile = async (doc: DuplicateDocumentItem) => {
    const target = doc.originalPath || doc.managedPath;
    if (!target) { flash?.('error', '文件路径缺失'); return; }
    try { await window.yiyuWorkbench.openPath(target); }
    catch (e) { flash?.('error', e instanceof Error ? e.message : '打开失败'); }
  };

  if (!clientId) return null;
  if (loading) {
    if (hideWhenEmpty) return null;
    return (
      <div className="mb-6 rounded-2xl border border-dashed border-slate-200 bg-slate-50/60 px-4 py-3 text-[12px] text-slate-400">
        正在扫描重复文件…
      </div>
    );
  }
  if (err) {
    if (hideWhenEmpty) return null;
    return <div className="mb-6 rounded-xl border border-rose-100 bg-rose-50 px-3 py-2 text-[12px] text-rose-700">{err}</div>;
  }
  if (groups.length === 0) {
    if (hideWhenEmpty) return null;
    return (
      <div className="mb-6 rounded-2xl border border-emerald-100 bg-emerald-50/60 px-4 py-3 text-[12px] text-emerald-700">
        ✓ 未发现重复文件 —— 资料库里没有同内容或同文件名被上传多次的情况。
      </div>
    );
  }
  const totalCopies = groups.reduce((sum, g) => sum + g.count, 0);
  const wasted = totalCopies - groups.length;

  return (
    <section className="mb-6 rounded-[24px] border border-slate-100 bg-white p-5 sm:p-6 shadow-[0_2px_10px_rgba(0,0,0,0.02)]">
      {/* Header —— 跟 AssetScoreBreakdownPanel 风格一致 */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between mb-5">
        <div>
          <div className="inline-flex items-center gap-1.5 rounded-full border border-amber-100 bg-amber-50 px-2.5 py-1 text-[11px] font-bold text-amber-700 mb-2">
            <Layers size={12} />
            重复文件 {groups.length} 组
          </div>
          <h3 className="text-[15px] font-bold text-slate-800">同名 / 同内容文件待整理</h3>
          <p className="mt-1 text-[12px] leading-6 text-slate-400">
            AI 通过内容指纹发现同一份文件被上传多次。逐张卡片选择打开预览或移入回收站（30 天可恢复）。
          </p>
        </div>
        <div className="rounded-full bg-slate-50 px-3 py-1 text-[11px] font-bold text-slate-500 self-start tabular-nums">
          {totalCopies} 份 · 多余 {wasted}
        </div>
      </div>

      <div className="space-y-5">
        {groups.map((g) => {
          const isGroupBusy = busyGroupKey === g.groupKey;
          // 推荐策略简化：只标"最新"（importedAt 最晚的那份）。
          // 用户原话"只需要知道哪个是最新的就好了" —— 不再用引用计数做复杂的"推荐"。
          const latestSorted = [...g.documents].sort((a, b) => (b.importedAt || '').localeCompare(a.importedAt || ''));
          const latestId = latestSorted[0]?.id;
          return (
            <div key={g.groupKey} className="rounded-[18px] border border-slate-100 bg-slate-50/40 p-4">
              {/* Group header */}
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between mb-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 mb-1.5">
                    <span
                      className={`rounded-full px-2.5 py-1 text-[10px] font-bold ${
                        g.groupType === 'same_content_hash'
                          ? 'border border-rose-100 bg-rose-50 text-rose-700'
                          : 'border border-amber-100 bg-amber-50 text-amber-700'
                      }`}
                    >
                      {g.groupType === 'same_content_hash' ? `完全重复 × ${g.count}` : `同名 × ${g.count}`}
                    </span>
                  </div>
                  <div className="text-[14px] font-bold text-slate-800 truncate" title={g.fileName}>
                    {g.fileName}
                  </div>
                </div>
                <div className="flex gap-2 shrink-0 flex-wrap">
                  <button
                    type="button"
                    onClick={() => void handleKeepLatest(g)}
                    disabled={isGroupBusy}
                    className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-[11px] font-bold text-emerald-700 transition-colors hover:bg-emerald-100 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    保留最新
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleKeepAll(g)}
                    disabled={isGroupBusy}
                    className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-[11px] font-bold text-slate-600 transition-colors hover:border-slate-300 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    全部保留
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleDeleteAll(g)}
                    disabled={isGroupBusy}
                    className="inline-flex items-center gap-1.5 rounded-full border border-rose-200 bg-rose-50 px-3 py-1.5 text-[11px] font-bold text-rose-700 transition-colors hover:bg-rose-100 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    全部删除
                  </button>
                </div>
              </div>

              {/* Cards grid —— 参考客户工作台文件卡片：文件类型图标 + 文件名 + 大小 + 日期 */}
              <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
                {g.documents.map((d) => {
                  const isLatest = d.id === latestId;
                  const isDocBusy = busyDocId === d.id;
                  const iconPath = d.originalPath || d.managedPath || d.fileName;
                  const dateStr = d.importedAt
                    ? d.importedAt.slice(0, 10).replace(/-/g, '·')
                    : '—';
                  return (
                    <div
                      key={d.id}
                      className={`group flex flex-col rounded-[16px] bg-white p-4 transition-all hover:shadow-[0_4px_16px_rgba(15,23,42,0.06)] ${
                        isLatest
                          ? 'border border-emerald-200 ring-1 ring-emerald-100'
                          : 'border border-slate-100'
                      }`}
                    >
                      {/* Top: file icon + 「最新」chip */}
                      <div className="flex items-start justify-between mb-3">
                        <FileTypeIcon path={iconPath} size={44} />
                        {isLatest && (
                          <span className="inline-flex items-center gap-1 rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-700">
                            <Star size={9} className="fill-emerald-700" />
                            最新
                          </span>
                        )}
                      </div>

                      {/* File name */}
                      <div
                        className="text-[12.5px] font-bold text-slate-800 leading-[1.5] line-clamp-2 mb-2 min-h-[38px]"
                        title={d.fileName}
                      >
                        {d.fileName}
                      </div>

                      {/* Meta: size + date */}
                      <div className="text-[11px] text-slate-500 tabular-nums mb-3 flex-1">
                        <span className="font-semibold">{formatFileSizeBytes(d.fileSizeBytes)}</span>
                        <span className="mx-1.5 text-slate-300">·</span>
                        <span>{dateStr}</span>
                      </div>

                      {/* Actions */}
                      <div className="flex gap-1.5">
                        <button
                          type="button"
                          onClick={() => void handleOpenFile(d)}
                          className="flex-1 inline-flex items-center justify-center gap-1 rounded-lg border border-slate-200 bg-white py-1.5 text-[11px] font-bold text-slate-600 transition-colors hover:border-blue-200 hover:bg-blue-50 hover:text-blue-700"
                          title="用系统应用打开（Word / WPS / Pages）"
                        >
                          <ExternalLink size={11} />
                          打开
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleDeleteOne(g, d)}
                          disabled={isDocBusy || isGroupBusy}
                          className="inline-flex items-center justify-center rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-slate-400 transition-colors hover:border-rose-200 hover:bg-rose-50 hover:text-rose-600 disabled:opacity-40 disabled:cursor-not-allowed"
                          title="移到回收站（30 天可恢复）"
                          aria-label="移到回收站"
                        >
                          {isDocBusy ? <span className="text-[11px]">…</span> : <Trash2 size={12} />}
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

// 文件大小格式化：B / KB / MB / GB
function formatFileSizeBytes(bytes: number | null | undefined): string {
  if (!bytes || bytes <= 0) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function ContradictionsTab({
  clientOptions,
  selectedClientId,
  onClientChange,
  flash,
}: {
  clientOptions: Array<{ id: string; name: string }>;
  selectedClientId: string;
  onClientChange: (id: string) => void;
  flash?: (level: 'success' | 'error' | 'info', message: string) => void;
}) {
  const [contradictions, setContradictions] = useState<FactContradictionRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string>('');
  const selectedClientIdRef = useRef(selectedClientId);
  useEffect(() => {
    selectedClientIdRef.current = selectedClientId;
  }, [selectedClientId]);

  const reload = useCallback(async () => {
    if (!selectedClientId) {
      setContradictions([]);
      return;
    }
    const capturedClientId = selectedClientId;
    setLoading(true);
    setErr(null);
    try {
      const resp = await getClientContradictions(selectedClientId, { status: 'pending', limit: 50 });
      if (selectedClientIdRef.current !== capturedClientId) return;
      setContradictions(resp.contradictions);
    } catch (e) {
      if (selectedClientIdRef.current !== capturedClientId) return;
      setErr(e instanceof Error ? e.message : '加载矛盾失败');
      setContradictions([]);
    } finally {
      if (selectedClientIdRef.current === capturedClientId) setLoading(false);
    }
  }, [selectedClientId]);

  useEffect(() => { void reload(); }, [reload]);

  const handleReview = async (id: string, status: 'dismissed' | 'resolved') => {
    try {
      setBusy(id);
      if (!selectedClientId) return;
      await reviewContradiction(selectedClientId, id, { reviewStatus: status });
      flash?.('success', status === 'resolved' ? '矛盾已标为已解决' : '矛盾已忽略');
      await reload();
    } catch (e) {
      flash?.('error', e instanceof Error ? e.message : '处理矛盾失败');
    } finally {
      setBusy('');
    }
  };

  return (
    <section className="rounded-2xl border border-gray-100 bg-white p-6">
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-rose-500">
            Fact Contradictions
          </div>
          <div className="mt-1 text-[15px] font-light tracking-tight text-gray-900">矛盾 & 待确认</div>
          <p className="mt-1 text-[11.5px] text-gray-500">AI 在资料里发现的「同一件事说法不一」 — 矛盾是资产,不是错误。</p>
        </div>
        <TabClientPicker clientOptions={clientOptions} selectedClientId={selectedClientId} onClientChange={onClientChange} />
      </div>
      {!selectedClientId && (
        <div className="rounded-xl bg-[#FAFAFA] px-4 py-8 text-center text-[12px] text-gray-400 ring-1 ring-inset ring-gray-100">
          先选一个客户,查看 AI 检测到的重复文件和事实矛盾。
        </div>
      )}
      <DuplicateDocumentsSection clientId={selectedClientId} flash={flash} />
      {err && <div className="mb-4 rounded-lg bg-rose-50/60 px-3 py-2 text-[12px] text-rose-700 ring-1 ring-inset ring-rose-200">{err}</div>}
      {selectedClientId && !loading && contradictions.length === 0 && !err && (
        <div className="rounded-xl bg-emerald-50/30 px-4 py-8 text-center text-[12px] text-emerald-700 ring-1 ring-inset ring-emerald-100">
          暂无待确认的事实矛盾 · AI 觉得这位客户的资料内部一致。
        </div>
      )}
      {selectedClientId && loading && (
        <div className="rounded-xl bg-[#FAFAFA] px-4 py-8 text-center text-[12px] text-gray-400 ring-1 ring-inset ring-gray-100">扫描中…</div>
      )}
      {contradictions.length > 0 && (
        <div className="space-y-3">
          {contradictions.map((c) => {
            const sevAccent = c.severity === 'high' ? 'before:bg-rose-400' : c.severity === 'medium' ? 'before:bg-amber-400' : 'before:bg-gray-300';
            const sevTone = c.severity === 'high' ? 'ring-rose-200 text-rose-700' : c.severity === 'medium' ? 'ring-amber-200 text-amber-700' : 'ring-gray-200 text-gray-600';
            return (
              <div key={c.id} className={`relative rounded-xl bg-white px-5 py-3.5 ring-1 ring-inset ring-gray-100 before:absolute before:left-0 before:top-3.5 before:bottom-3.5 before:w-[3px] before:rounded-r-full ${sevAccent}`}>
                <div className="mb-3 flex items-baseline justify-between gap-3">
                  <div className="text-[13px] font-medium text-gray-800">
                    <span className="text-rose-700">{c.subjectText}</span> 的 <span className="text-rose-700">{c.attribute}</span>
                  </div>
                  <span className={`shrink-0 rounded-full px-2 py-[2px] text-[9.5px] font-semibold uppercase tracking-[0.12em] ring-1 ring-inset ${sevTone}`}>
                    {c.severity === 'high' ? '严重' : c.severity === 'medium' ? '中等' : '一般'}
                  </span>
                </div>
                <div className="mb-3 grid grid-cols-1 gap-2 md:grid-cols-2">
                  <div className="rounded-lg bg-[#FAFAFA] px-3 py-2 ring-1 ring-inset ring-gray-100">
                    <div className="text-[9px] font-semibold uppercase tracking-[0.16em] text-gray-400">说法 A</div>
                    <div className="mt-1 text-[12px] font-medium text-gray-800">{c.valueA}</div>
                    <div className="mt-1 line-clamp-2 text-[10.5px] leading-[1.6] text-gray-500">{c.evidenceA}</div>
                    {c.docAFileName && <div className="mt-1 truncate text-[10px] text-gray-400" title={c.docAFileName}>来源:{c.docAFileName}</div>}
                  </div>
                  <div className="rounded-lg bg-[#FAFAFA] px-3 py-2 ring-1 ring-inset ring-gray-100">
                    <div className="text-[9px] font-semibold uppercase tracking-[0.16em] text-gray-400">说法 B</div>
                    <div className="mt-1 text-[12px] font-medium text-gray-800">{c.valueB}</div>
                    <div className="mt-1 line-clamp-2 text-[10.5px] leading-[1.6] text-gray-500">{c.evidenceB}</div>
                    {c.docBFileName && <div className="mt-1 truncate text-[10px] text-gray-400" title={c.docBFileName}>来源:{c.docBFileName}</div>}
                  </div>
                </div>
                <div className="flex justify-end gap-2">
                  <button
                    type="button"
                    disabled={busy === c.id}
                    onClick={() => void handleReview(c.id, 'dismissed')}
                    className="rounded-md bg-white px-3 py-1 text-[11px] font-medium text-gray-600 ring-1 ring-inset ring-gray-200 hover:bg-gray-50 disabled:opacity-50 transition-colors"
                  >
                    假告警,忽略
                  </button>
                  <button
                    type="button"
                    disabled={busy === c.id}
                    onClick={() => void handleReview(c.id, 'resolved')}
                    className="rounded-md bg-emerald-50/70 px-3 py-1 text-[11px] font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200 hover:bg-emerald-100/70 disabled:opacity-50 transition-colors"
                  >
                    {busy === c.id ? '处理中…' : '已确认正解'}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

export function StrategicBrainView({
  clients = [],
  currentClientId,
  onClientChange,
  onCreateTaskFromThought,
  onPromoteTodo,
  flash,
}: StrategicBrainViewProps) {
  // 战略陪伴当前只剩 2 个 tab：客户档案（即 contradictions/事实澄清模块）/ 判断 & 思考
  // 原"客户档案/DigitalAssetsTab" 已下线；资料健康 / 输出沉淀 也已删除。
  const [activeTab, setActiveTab] = useState('contradictions');
  const [thoughts, setThoughts] = useState<StrategicThought[]>([]);
  const [thoughtsLoading, setThoughtsLoading] = useState(false);
  const [thoughtsError, setThoughtsError] = useState<string | null>(null);
  const [thoughtClientId, setThoughtClientId] = useState(currentClientId || '');
  const thoughtClientIdRef = useRef(thoughtClientId);
  useEffect(() => {
    thoughtClientIdRef.current = thoughtClientId;
  }, [thoughtClientId]);
  // 兜底:让 AI 全面重新理解这个客户(同时跑 analysis_job + refresh strategic_thoughts)
  const [globalRefreshing, setGlobalRefreshing] = useState(false);
  const [globalRefreshMsg, setGlobalRefreshMsg] = useState<string | null>(null);

  const thoughtClientOptions = useMemo(() => {
    const map = new Map<string, { id: string; name: string }>();
    for (const client of clients) {
      if (client.id && client.name && !isInternalSmokeClient(client) && !map.has(client.id)) {
        map.set(client.id, { id: client.id, name: client.name });
      }
    }
    return Array.from(map.values());
  }, [clients]);

  const selectedThoughtClient = useMemo(
    () => thoughtClientOptions.find((client) => client.id === thoughtClientId) || null,
    [thoughtClientId, thoughtClientOptions],
  );


  useEffect(() => {
    // 只在 currentClientId 真正变化时同步, 避免覆盖用户在 ThoughtScopeSelect 里的手动选择.
    // 例:用户上面在客户工作台是测试机构A, 下方 ThoughtScopeSelect 自己挑了"全公司", 父组件因
    // 其他原因 re-render 不会触发(currentClientId 没变); 但 currentClientId 真的从测试机构A
    // 切到士平时, 这条 effect 期望把 thought 视图也切过去.
    setThoughtClientId((prev) => (prev === currentClientId ? prev : (currentClientId || '')));
  }, [currentClientId]);

  const loadThoughts = useCallback(async () => {
    const capturedClientId = thoughtClientId;
    setThoughtsLoading(true);
    setThoughtsError(null);
    try {
      const response = await getStrategicThoughts({
        clientId: thoughtClientId || null,
        limit: thoughtClientId ? 12 : 24,
      });
      if (thoughtClientIdRef.current !== capturedClientId) return;
      setThoughts(response.items || []);
    } catch (error) {
      if (thoughtClientIdRef.current !== capturedClientId) return;
      setThoughtsError(error instanceof Error ? error.message : '未知错误');
      setThoughts([]);
    } finally {
      if (thoughtClientIdRef.current === capturedClientId) {
        setThoughtsLoading(false);
      }
    }
  }, [thoughtClientId]);

  useEffect(() => {
    void loadThoughts();
  }, [loadThoughts]);

  const handleThoughtReview = useCallback(
    async (thoughtId: string, action: 'confirm' | 'dismiss', note: string) => {
      try {
        await reviewStrategicThought(thoughtId, { action, note, createJudgment: action === 'confirm' });
        await loadThoughts();
      } catch (error) {
        const msg = error instanceof Error ? error.message : '研判评审失败';
        flash?.('error', msg);
      }
    },
    [flash, loadThoughts],
  );

  // 原 handleRefreshThoughts (单纯刷新研判, 不动客户档案) 已删. 思考 tab 内的两个
  // "刷新研判" 按钮 (空态/有数据态) 都收纳到顶部 "让 AI 重新理解" 全局按钮, 避免用户
  // 混淆 (顶部=全局 / tab内=仅研判) 的双入口困惑.

  // 全局兜底"让 AI 重新理解":同时跑 analysis_job(写 evidence_cards)+ 刷新研判
  const handleGlobalRefresh = useCallback(async () => {
    if (!thoughtClientId || globalRefreshing) return;
    const capturedClientId = thoughtClientId;
    setGlobalRefreshing(true);
    setGlobalRefreshMsg(null);
    try {
      // 入队 analysis_job(异步, worker 几秒内消费, 写入 evidence_cards)
      await createAnalysisJob({
        jobType: 'strategy_pack',
        clientId: thoughtClientId,
        scopeType: 'client',
        scopeId: thoughtClientId,
        triggerType: 'manual',
        question: 'manual:全局刷新理解',
        intentProfile: 'client_overview',
      });
      // 并行触发 narrative regenerate(客户档案/事实澄清 tab 看的)+ refresh thoughts(思考 tab)
      // 这两个都跑 LLM, 各自独立,不阻塞彼此
      const [thoughtsResult] = await Promise.allSettled([
        refreshStrategicThoughts({ clientId: capturedClientId, limit: 8 }),
        regenerateClientNarrative(capturedClientId, {
          trigger: 'manual_global_refresh',
          force: true,
        }),
      ]);
      if (thoughtClientIdRef.current !== capturedClientId) return;
      if (thoughtsResult.status === 'fulfilled') {
        const items = thoughtsResult.value.items || [];
        setThoughts(items);
        if (items.length > 0) {
          setActiveTab('thoughts');
        }
      }
      setGlobalRefreshMsg('✓ AI 理解已刷新,客户档案/思考 都已更新,刷新页面或切 tab 查看');
      window.setTimeout(() => setGlobalRefreshMsg(null), 12000);
    } catch (error) {
      if (thoughtClientIdRef.current !== capturedClientId) return;
      setGlobalRefreshMsg(error instanceof Error ? `请求失败:${error.message}` : '请求失败');
    } finally {
      if (thoughtClientIdRef.current === capturedClientId) {
        setGlobalRefreshing(false);
      }
    }
  }, [thoughtClientId, globalRefreshing]);

  const handleToggleFavoriteThought = useCallback(
    async (thought: StrategicThought) => {
      try {
        await updateStrategicThoughtState(thought.id, { action: thought.isFavorite ? 'unfavorite' : 'favorite' });
        await loadThoughts();
      } catch (error) {
        const msg = error instanceof Error ? error.message : '收藏状态更新失败';
        flash?.('error', msg);
      }
    },
    [flash, loadThoughts],
  );

  const handleDeleteThought = useCallback(
    async (thought: StrategicThought) => {
      try {
        await updateStrategicThoughtState(thought.id, { action: 'delete' });
        await loadThoughts();
      } catch (error) {
        const msg = error instanceof Error ? error.message : '删除研判失败';
        flash?.('error', msg);
      }
    },
    [flash, loadThoughts],
  );

  return (
    <div className="h-full flex flex-col bg-white overflow-hidden font-sans">
      {/* Header */}
      <div className="border-b border-gray-100 pt-6 pb-0 px-8 flex flex-col gap-5 shrink-0">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-gray-400">
              Strategic Accompaniment
            </div>
            <h1 className="mt-1 text-[22px] font-light tracking-tight text-gray-900">
              战略陪伴
            </h1>
            <p className="mt-0.5 text-[11.5px] text-gray-500">AI 陪伴组织成长 · 越用越懂你</p>
          </div>
          <div className="flex items-center gap-3">
            {/* 全局兜底:让 AI 重新理解这个客户(input → analysis_job + refresh thoughts) */}
            {thoughtClientId && (
              <div className="flex items-center gap-2">
                {globalRefreshMsg && (
                  <span className={`text-[11px] ${globalRefreshMsg.startsWith('✓') ? 'text-emerald-600' : 'text-rose-600'}`}>
                    {globalRefreshMsg}
                  </span>
                )}
                <button
                  type="button"
                  onClick={() => void handleGlobalRefresh()}
                  disabled={globalRefreshing}
                  title="让 AI 重新读一遍这个客户的所有资料,生成本周新动态/研判/承诺/风险"
                  className="inline-flex items-center gap-1.5 rounded-xl border border-[#D8E5FF] bg-white px-3 py-1.5 text-[11px] font-bold text-[#4A63CF] shadow-sm hover:border-[#5B7BFE] hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <RefreshCw size={12} className={globalRefreshing ? 'animate-spin' : ''} />
                  {globalRefreshing ? '请求中...' : '让 AI 重新理解'}
                </button>
              </div>
            )}
            {activeTab === 'thoughts' && (
              <ThoughtScopeSelect
                clients={thoughtClientOptions}
                selectedClientId={thoughtClientId}
                disabled={thoughtsLoading && !thoughtClientOptions.length}
                onChange={(clientId) => {
                  setThoughtClientId(clientId);
                  if (clientId) onClientChange?.(clientId);
                }}
              />
            )}
          </div>
        </div>
        <div className="flex items-center gap-6 -mb-px">
          {TABS.map(tab => {
            const isActive = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`relative pb-3 text-[12px] font-medium uppercase tracking-[0.14em] transition-colors ${
                  isActive
                    ? 'text-[#5B7BFE]'
                    : 'text-gray-500 hover:text-gray-800'
                }`}
              >
                {tab.label}
                <span
                  className={`absolute -bottom-px left-0 right-0 h-[2px] rounded-full transition-colors ${
                    isActive ? 'bg-[#5B7BFE]' : 'bg-transparent'
                  }`}
                />
              </button>
            );
          })}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-6 py-5">
        <div className="max-w-full mx-auto">
          {activeTab === 'thoughts' && (
            <ThoughtsTab
              thoughts={thoughts}
              loading={thoughtsLoading}
              error={thoughtsError}
              selectedClientId={thoughtClientId || null}
              selectedClientName={selectedThoughtClient?.name || null}
              selectedProjectModuleId={null}
              selectedProjectModuleName={null}
              onReview={handleThoughtReview}
              onCreateTask={onCreateTaskFromThought}
              onRetry={() => void loadThoughts()}
              onToggleFavorite={handleToggleFavoriteThought}
              onDelete={handleDeleteThought}
            />
          )}
          {activeTab === 'contradictions' && (
            <StrategicClarificationView
              clientOptions={thoughtClientOptions}
              selectedClientId={thoughtClientId}
              onClientChange={(id) => {
                setThoughtClientId(id);
                if (id) onClientChange?.(id);
              }}
              flash={flash}
              onPromoteTodo={onPromoteTodo}
            />
          )}
        </div>
      </div>
    </div>
  );
}

export default StrategicBrainView;
