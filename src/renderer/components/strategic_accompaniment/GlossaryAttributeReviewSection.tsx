/**
 * M1 · 事实澄清面板 (战略陪伴 → 客户档案 底部)
 *
 * 业务目标:
 *  - 官网逐字有据的事实默认生效
 *  - 用户只需在发现过时或错误时纠错/补充
 *  - 正式事实由 chat / narrative 自动消费
 *
 * 设计准则 (5/27 重设计):
 *  - 跟"重点主线 / 任务归属"统一: 细体大字标题 + 中性灰骨架 + 单一蓝紫强调色
 *  - 不用 emoji, 全 lucide-react icons
 *  - 按钮 ghost / outline 风格, 不用实色填充
 *  - chip / pill 统一 text-[10px] font-medium rounded-full
 */
import { useEffect, useRef, useState } from 'react';
import {
  CircleDollarSign,
  CalendarDays,
  User,
  MapPin,
  Hash,
  Star,
  Tag,
  FileText,
  Check,
  Loader2,
  ChevronDown,
  ChevronRight,
  Pencil,
  Clock,
  Link2,
  Inbox,
  type LucideIcon,
} from 'lucide-react';
import {
  listGlossaryAttributes,
  verifyGlossaryAttribute,
  rejectGlossaryAttribute,
  type GlossaryAttributeRecord,
  type GlossaryAttributeClarifyPayload,
} from '../../lib/api';

interface GlossaryAttributeReviewSectionProps {
  clientId: string;
  refreshKey?: number;
  onChanged?: () => void;
  flash?: (kind: 'success' | 'error', message: string) => void;
}

// 替换原 emoji 为 lucide icon component. 跟整个 codebase icon 风格统一.
// 用 lucide 官方导出的 LucideIcon 类型, 不手搓 —— 随 lucide 升级自动兼容, 不再因版本漂移崩。
type IconType = LucideIcon;
const CATEGORY_META: Record<string, { label: string; Icon: IconType }> = {
  amount: { label: '金额', Icon: CircleDollarSign },
  date: { label: '日期', Icon: CalendarDays },
  person: { label: '人物', Icon: User },
  location: { label: '地点', Icon: MapPin },
  count: { label: '数量', Icon: Hash },
  rating: { label: '评级', Icon: Star },
  organization_profile: { label: '机构定位', Icon: FileText },
  mission_vision: { label: '使命与愿景', Icon: FileText },
  service_offering: { label: '项目与服务', Icon: FileText },
  project_definition: { label: '项目与服务', Icon: FileText },
  methodology: { label: '方法与战略', Icon: Tag },
  governance: { label: '治理与合作', Icon: FileText },
  partnership: { label: '治理与合作', Icon: FileText },
  milestone: { label: '历史与里程碑', Icon: CalendarDays },
  impact_metric: { label: '规模与成效', Icon: Hash },
  business_term: { label: '业务术语', Icon: Tag },
  text: { label: '其他正式事实', Icon: FileText },
};

function categoryOf(attribute: GlossaryAttributeRecord): string {
  if (attribute.value_category !== 'text') return attribute.value_category || 'text';
  return attribute.fact_kind || 'text';
}

const SOURCE_META: Record<string, string> = {
  ai_inferred: '资料抽取',
  auto_resolved_clarification: '澄清回填',
  internet_ocr: '互联网整理',
  drift_alert: '冲突提示',
  user_input: '用户填写',
  official_website: '官网权威来源',
};

export function GlossaryAttributeReviewSection({
  clientId,
  refreshKey = 0,
  onChanged,
  flash,
}: GlossaryAttributeReviewSectionProps) {
  const [attrs, setAttrs] = useState<GlossaryAttributeRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [acting, setActing] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [showSection, setShowSection] = useState(true);
  const [editingAttr, setEditingAttr] = useState<GlossaryAttributeRecord | null>(null);
  const clientIdRef = useRef(clientId);
  useEffect(() => {
    clientIdRef.current = clientId;
    setActing(new Set());
  }, [clientId]);

  const load = async () => {
    const capturedClientId = clientId;
    setLoading(true);
    try {
      const [pending, verified] = await Promise.all([
        listGlossaryAttributes(clientId, 'pending'),
        listGlossaryAttributes(clientId, 'verified'),
      ]);
      if (clientIdRef.current !== capturedClientId) return;
      setAttrs([...(pending.attributes ?? []), ...(verified.attributes ?? [])]);
    } catch (err) {
      if (clientIdRef.current !== capturedClientId) return;
      flash?.('error', `事实澄清加载失败: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      if (clientIdRef.current === capturedClientId) setLoading(false);
    }
  };

  useEffect(() => {
    if (!clientId) return;
    void load();
  }, [clientId, refreshKey]);

  const mark = async (
    attrId: string,
    action: 'verify' | 'reject',
    clarifyPayload?: GlossaryAttributeClarifyPayload,
  ) => {
    const capturedClientId = clientId;
    setActing((s) => new Set(s).add(attrId));
    try {
      if (action === 'verify') {
        await verifyGlossaryAttribute(clientId, attrId, clarifyPayload ?? {});
        flash?.('success', clarifyPayload ? '已澄清并采纳，进入客户档案权威值' : '已采纳，进入客户档案权威值');
      } else {
        await rejectGlossaryAttribute(clientId, attrId);
        flash?.('success', '已拒绝');
      }
      if (clientIdRef.current !== capturedClientId) return;
      if (action === 'reject') {
        setAttrs((prev) => prev.filter((a) => a.id !== attrId));
      } else {
        await load();
      }
      onChanged?.();
    } catch (err) {
      if (clientIdRef.current !== capturedClientId) return;
      flash?.('error', `操作失败: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      if (clientIdRef.current !== capturedClientId) return;
      setActing((s) => {
        const ns = new Set(s);
        ns.delete(attrId);
        return ns;
      });
    }
  };

  const verifiedCount = attrs.filter((a) => a.verification_status === 'verified').length;

  if (!showSection) {
    return (
      <button
        type="button"
        onClick={() => setShowSection(true)}
        className="mt-6 inline-flex items-center gap-1.5 text-[12px] font-medium text-gray-500 hover:text-gray-900 transition-colors"
      >
        <ChevronRight size={13} strokeWidth={2} />
        展开事实澄清
        <span className="tabular-nums text-gray-400">· {attrs.length}</span>
      </button>
    );
  }

  // 按 category 分组, 组内按 confidence 倒序
  const byCategory = new Map<string, GlossaryAttributeRecord[]>();
  for (const a of attrs) {
    const cat = categoryOf(a);
    if (!byCategory.has(cat)) byCategory.set(cat, []);
    byCategory.get(cat)!.push(a);
  }
  for (const [, group] of byCategory) {
    group.sort((x, y) => (y.confidence ?? 0) - (x.confidence ?? 0));
  }
  const categoryOrder = [
    'person', 'organization_profile', 'mission_vision', 'service_offering',
    'project_definition', 'methodology', 'governance', 'partnership',
    'milestone', 'impact_metric', 'date', 'location', 'count', 'amount',
    'business_term', 'rating', 'text',
  ];

  return (
    <section className="mt-8">
      {/* Header: 大号细字标题 + 极简副 label + 右上角统计 */}
      <header className="mb-6 flex items-end justify-between gap-6">
        <div>
          <h3 className="text-[20px] font-light tracking-tight text-gray-900">事实澄清</h3>
          <p className="mt-1 text-[12px] text-gray-400 leading-relaxed">
            官网原句有据的事实默认生效 · 如发现过时或错误，可逐条纠错/补充
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-4 tabular-nums">
          <span className="inline-flex items-baseline gap-1 text-[11px] text-gray-400">
            <span className="text-[15px] font-semibold text-gray-900">{verifiedCount}</span>
            已生效
          </span>
          <button
            type="button"
            onClick={() => setShowSection(false)}
            className="ml-2 text-[11px] font-medium text-gray-400 hover:text-gray-700 transition-colors"
          >
            收起
          </button>
        </div>
      </header>

      {loading && (
        <div className="flex items-center gap-2 py-4 text-[12px] text-gray-400">
          <Loader2 size={13} className="animate-spin" />
          <span>正在加载待审事实…</span>
        </div>
      )}

      {!loading && attrs.length === 0 && (
        <div className="flex flex-col items-center gap-2 py-10 text-center">
          <Inbox size={22} className="text-gray-300" strokeWidth={1.5} />
          <p className="text-[13px] font-medium text-gray-500">暂无官网事实</p>
          <p className="text-[11px] text-gray-400 max-w-[280px] leading-relaxed">
            登记并抓取项目官网后，可核实事实会自动出现在这里
          </p>
        </div>
      )}

      {editingAttr && (
        <ClarifyEditModal
          attr={editingAttr}
          onClose={() => setEditingAttr(null)}
          onConfirm={async (payload) => {
            const id = editingAttr.id;
            setEditingAttr(null);
            await mark(id, 'verify', payload);
          }}
        />
      )}

      {!loading && attrs.length > 0 && (
        <div className="space-y-2">
          {categoryOrder.map((cat) => {
            const group = byCategory.get(cat);
            if (!group || group.length === 0) return null;
            const meta = CATEGORY_META[cat] || { label: cat, Icon: Tag };
            const Icon = meta.Icon;
            const isExpanded = expanded.has(cat);
            return (
              <div key={cat} className="rounded-lg border border-gray-100 bg-white overflow-hidden">
                {/* 分组 header: 细线 hover 灰底, 右侧"全部采纳"ghost 按钮 */}
                <div className="flex items-stretch justify-between">
                  <button
                    type="button"
                    onClick={() =>
                      setExpanded((s) => {
                        const ns = new Set(s);
                        if (ns.has(cat)) ns.delete(cat);
                        else ns.add(cat);
                        return ns;
                      })
                    }
                    className="flex flex-1 items-center gap-2.5 px-4 py-2.5 text-left transition-colors hover:bg-gray-50"
                  >
                    <span className="flex h-4 w-4 items-center justify-center text-gray-400">
                      {isExpanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                    </span>
                    <Icon size={14} className="text-gray-500" strokeWidth={1.8} />
                    <span className="text-[13px] font-medium text-gray-800">{meta.label}</span>
                    <span className="tabular-nums text-[11px] text-gray-400">{group.length}</span>
                  </button>
                </div>

                {isExpanded && (
                  <ul className="border-t border-gray-100 divide-y divide-gray-100">
                    {group.map((a) => {
                      const inAction = acting.has(a.id);
                      const alreadyVerified = a.verification_status === 'verified';
                      const sourceLabel = a.source_type ? SOURCE_META[a.source_type] : null;
                      const confidence = typeof a.confidence === 'number' ? a.confidence : null;
                      const confidenceTone = confidence === null
                        ? null
                        : confidence >= 0.9
                          ? 'text-emerald-700 border-emerald-200 bg-emerald-50/60'
                          : confidence >= 0.7
                            ? 'text-amber-700 border-amber-200 bg-amber-50/60'
                            : 'text-gray-500 border-gray-200 bg-gray-50';
                      return (
                        <li key={a.id} className="px-4 py-3">
                          <div className="flex items-start justify-between gap-4">
                            <div className="min-w-0 flex-1">
                              <div className="flex items-center gap-2 flex-wrap">
                                <span className="text-[13px] font-medium text-gray-900 truncate">
                                  {a.term}
                                  <span className="mx-1 text-gray-300">·</span>
                                  <span className="text-gray-600">{a.attribute_name}</span>
                                </span>
                                {sourceLabel && (
                                  <span className="rounded-full border border-gray-200 px-1.5 py-px text-[10px] font-medium text-gray-500">
                                    {sourceLabel}
                                  </span>
                                )}
                                {alreadyVerified && (
                                  <span className="rounded-full border border-emerald-200 bg-emerald-50/60 px-1.5 py-px text-[10px] font-medium text-emerald-700">
                                    官网已生效
                                  </span>
                                )}
                                {confidence !== null && (
                                  <span className={`rounded-full border px-1.5 py-px text-[10px] font-medium tabular-nums ${confidenceTone}`}>
                                    {Math.round(confidence * 100)}%
                                  </span>
                                )}
                              </div>
                              <div className="mt-1.5 flex items-baseline gap-1.5 text-[13px] text-gray-700">
                                <span className="text-gray-300">=</span>
                                <span>{a.value_text}</span>
                                {a.value_unit && (
                                  <span className="text-[12px] text-gray-400">{a.value_unit}</span>
                                )}
                              </div>
                              {(a.scope || a.as_of_date) && (
                                <div className="mt-1 flex items-center gap-2 text-[11px] text-gray-400">
                                  {a.scope && <span>口径：{a.scope}</span>}
                                  {a.scope && a.as_of_date && <span className="text-gray-200">·</span>}
                                  {a.as_of_date && <span>截至 {a.as_of_date}</span>}
                                </div>
                              )}
                              {a.source_evidence && (
                                <p className="mt-1.5 line-clamp-2 text-[11px] leading-relaxed text-gray-500">
                                  <span className="text-gray-400">证据：</span>
                                  {a.source_evidence}
                                </p>
                              )}
                              {a.source_doc_path && (
                                <button
                                  type="button"
                                  onClick={() => {
                                    if (a.source_doc_path) {
                                      if (/^https?:\/\//i.test(a.source_doc_path)) {
                                        void window.yiyuWorkbench.openExternalUrl(a.source_doc_path).catch(() => undefined);
                                      } else {
                                        void window.yiyuWorkbench.openPath(a.source_doc_path).catch(() => undefined);
                                      }
                                    }
                                  }}
                                  className="mt-1.5 inline-flex items-center gap-1 text-[11px] text-[#5B7BFE] hover:text-[#3a5cf0] transition-colors"
                                  title={`点击打开来源：${a.source_doc_title || a.source_doc_path}`}
                                >
                                  <Link2 size={11} strokeWidth={1.8} />
                                  {a.source_doc_title || '打开来源文档'}
                                </button>
                              )}
                              {!a.source_doc_path && a.source_reference_mode === 'evidence_snapshot' && (
                                <span
                                  className="mt-1.5 inline-flex items-center gap-1 text-[11px] text-gray-400"
                                  title="官网没有提供可确认的正常公开入口；上方原句来自已保存的抓取证据"
                                >
                                  <FileText size={11} strokeWidth={1.8} />
                                  官网抓取快照 · 原句已保留
                                </span>
                              )}
                            </div>
                            <div className="flex shrink-0 items-center gap-1">
                              <button
                                type="button"
                                onClick={() => setEditingAttr(a)}
                                disabled={inAction}
                                title="补充或更正这条官网事实"
                                className="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2.5 py-1 text-[11px] font-medium text-gray-600 transition-colors hover:border-[#5B7BFE]/40 hover:text-[#5B7BFE] disabled:opacity-50"
                              >
                                <Pencil size={11} strokeWidth={1.8} />
                                纠错/补充
                              </button>
                            </div>
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

interface ClarifyEditModalProps {
  attr: GlossaryAttributeRecord;
  onClose: () => void;
  onConfirm: (payload: GlossaryAttributeClarifyPayload) => void | Promise<void>;
}

function ClarifyEditModal({ attr, onClose, onConfirm }: ClarifyEditModalProps) {
  const [valueText, setValueText] = useState(attr.value_text || '');
  const [valueUnit, setValueUnit] = useState(attr.value_unit || '');
  const [scope, setScope] = useState(attr.scope || '');
  const [asOfDate, setAsOfDate] = useState(attr.as_of_date || '');
  const [attributeName, setAttributeName] = useState(attr.attribute_name || '');

  const isDate = attr.value_category === 'date';
  const isAmount = attr.value_category === 'amount';
  const isCount = attr.value_category === 'count';

  const handleSave = async () => {
    const payload: GlossaryAttributeClarifyPayload = {
      valueText: valueText.trim(),
      valueUnit: valueUnit.trim() || undefined,
      scope: scope.trim() || undefined,
      asOfDate: asOfDate.trim() || null,
      attributeName: attributeName.trim() || undefined,
    };
    await onConfirm(payload);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/40 backdrop-blur-sm">
      <div className="w-[500px] max-w-[92vw] rounded-2xl border border-gray-100 bg-white shadow-2xl">
        <header className="flex items-center gap-2 border-b border-gray-100 px-6 py-4">
          <Pencil size={14} className="text-[#5B7BFE]" strokeWidth={1.8} />
          <h3 className="text-[15px] font-medium text-gray-900">澄清</h3>
          <span className="text-gray-300">·</span>
          <span className="text-[13px] text-gray-600">{attr.term}</span>
        </header>

        <div className="space-y-4 px-6 py-5 text-[12px]">
          <div>
            <label className="mb-1.5 block text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">属性名</label>
            <input
              type="text"
              value={attributeName}
              onChange={(e) => setAttributeName(e.target.value)}
              className="w-full rounded-md border border-gray-200 px-3 py-2 text-[13px] text-gray-800 focus:border-[#5B7BFE] focus:outline-none focus:ring-1 focus:ring-[#5B7BFE]/20"
              placeholder="例：总部位置 / 2023年度支出"
            />
          </div>

          <div>
            <label className="mb-1.5 block text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">
              {isDate ? '日期' : isAmount ? '金额' : isCount ? '数量' : '权威值'}
            </label>
            {isDate ? (
              <div className="space-y-2.5">
                <div className="flex items-center gap-2">
                  <CalendarDays size={14} className="text-gray-400" strokeWidth={1.8} />
                  <input
                    type="date"
                    value={valueText.includes('-') ? valueText.slice(0, 10) : ''}
                    onChange={(e) => setValueText(e.target.value)}
                    className="rounded-md border border-gray-200 px-3 py-1.5 text-[13px] text-gray-800 focus:border-[#5B7BFE] focus:outline-none focus:ring-1 focus:ring-[#5B7BFE]/20"
                  />
                  <span className="text-[11px] text-gray-400">或自由输入：</span>
                  <input
                    type="text"
                    value={valueText}
                    onChange={(e) => setValueText(e.target.value)}
                    className="flex-1 rounded-md border border-gray-200 px-3 py-1.5 text-[13px] text-gray-800 focus:border-[#5B7BFE] focus:outline-none focus:ring-1 focus:ring-[#5B7BFE]/20"
                    placeholder="例：2014 年 / 2026-03-30"
                  />
                </div>
                <div className="flex items-center gap-2 text-[11px]">
                  <span className="text-gray-400">快捷标记：</span>
                  <button
                    type="button"
                    onClick={() => setValueText('已完成')}
                    className="inline-flex items-center gap-1 rounded-md border border-emerald-200 bg-emerald-50/60 px-2 py-0.5 font-medium text-emerald-700 hover:bg-emerald-100 transition-colors"
                  >
                    <Check size={11} strokeWidth={2} />已完成
                  </button>
                  <button
                    type="button"
                    onClick={() => setValueText('进行中')}
                    className="inline-flex items-center gap-1 rounded-md border border-amber-200 bg-amber-50/60 px-2 py-0.5 font-medium text-amber-700 hover:bg-amber-100 transition-colors"
                  >
                    <Clock size={11} strokeWidth={1.8} />进行中
                  </button>
                  <button
                    type="button"
                    onClick={() => setValueText('暂无 deadline')}
                    className="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2 py-0.5 font-medium text-gray-500 hover:bg-gray-50 transition-colors"
                  >
                    <FileText size={11} strokeWidth={1.8} />暂无
                  </button>
                </div>
              </div>
            ) : (
              <textarea
                value={valueText}
                onChange={(e) => setValueText(e.target.value)}
                rows={2}
                className="w-full rounded-md border border-gray-200 px-3 py-2 text-[13px] text-gray-800 focus:border-[#5B7BFE] focus:outline-none focus:ring-1 focus:ring-[#5B7BFE]/20"
                placeholder="填入权威值"
              />
            )}
          </div>

          {(isAmount || isCount) && (
            <div>
              <label className="mb-1.5 block text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">单位</label>
              <input
                type="text"
                value={valueUnit}
                onChange={(e) => setValueUnit(e.target.value)}
                className="w-full rounded-md border border-gray-200 px-3 py-2 text-[13px] text-gray-800 focus:border-[#5B7BFE] focus:outline-none focus:ring-1 focus:ring-[#5B7BFE]/20"
                placeholder="例：元 / 人 / 省"
              />
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1.5 block text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">口径</label>
              <input
                type="text"
                value={scope}
                onChange={(e) => setScope(e.target.value)}
                className="w-full rounded-md border border-gray-200 px-3 py-2 text-[13px] text-gray-800 focus:border-[#5B7BFE] focus:outline-none focus:ring-1 focus:ring-[#5B7BFE]/20"
                placeholder="机构当前 / 项目累计 / 现任"
              />
            </div>
            <div>
              <label className="mb-1.5 block text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">截至日期（可选）</label>
              <input
                type="date"
                value={asOfDate.slice(0, 10)}
                onChange={(e) => setAsOfDate(e.target.value)}
                className="w-full rounded-md border border-gray-200 px-3 py-2 text-[13px] text-gray-800 focus:border-[#5B7BFE] focus:outline-none focus:ring-1 focus:ring-[#5B7BFE]/20"
              />
            </div>
          </div>
        </div>

        <footer className="flex justify-end gap-2 border-t border-gray-100 px-6 py-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-gray-200 bg-white px-3 py-1.5 text-[12px] font-medium text-gray-600 transition-colors hover:bg-gray-50"
          >
            取消
          </button>
          <button
            type="button"
            onClick={handleSave}
            className="inline-flex items-center gap-1.5 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-[12px] font-medium text-emerald-700 transition-colors hover:bg-emerald-100"
          >
            <Check size={12} strokeWidth={2} />
            保存并采纳
          </button>
        </footer>
      </div>
    </div>
  );
}
