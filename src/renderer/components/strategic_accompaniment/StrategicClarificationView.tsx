/**
 * 战略陪伴 · 事实澄清面板（Phase 1.5c — 6 维度故事网）
 *
 * 替换原"矛盾&待确认"Tab。主体改为 AI 写的 6 段叙事 (云端共享, 共同编织),
 * 旧 5 区块 db dump 降为"AI 引用源·字典层"折叠在底部。
 *
 * 设计原则 (顾源源 2026/5/16 原话):
 *   - "看到整个故事 (关键人/角色/这是一件什么事/承诺)" → 6 段叙事
 *   - "用户语音/打字澄清 → AI 更新故事网" → 每段含澄清入口
 *   - "凡是跟项目有关的人都关联到这个面板共同编织" → contributors 追溯
 *   - "数据中心要把关键信息串成一张网, 在功能界面发挥作用" → 加工层缺口诚实暴露
 *
 * 6 维度:
 *   1. essence       项目本质
 *   2. people        关键人物网
 *   3. history       来龙去脉
 *   4. commitments   承诺网
 *   5. risks         卡点与风险
 *   6. next          下一步
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Users, Target, Clock, AlertCircle, CheckCircle, HelpCircle,
  Mic, Type, ExternalLink, ChevronDown, Briefcase,
  Sparkles, MessageCircle, RefreshCw, Database, GitBranch,
  Compass, Network, History, Handshake, AlertTriangle, ArrowRight,
  UploadCloud, ClipboardCheck, X,
} from 'lucide-react';
import {
  getClientClarificationContext,
  getClientNarrative,
  getClientWorkspace,
  listClientNarrativeClarifications,
  submitClientNarrativeClarification,
  regenerateClientNarrative,
  getClientOfficialWebsite,
  refreshClientOfficialWebsite,
  getNarrativeStaleStatus,
  getMeetingActionItems,
  getNextSteps,
  getNextStepBackground,
  dismissUnifiedTodo,
  logSuggestionAction,
  getSuggestionLog,
  removeSuggestionLogEntry,
  getStrategicDocs,
  uploadStrategicDoc,
  // deleteStrategicDoc 不再使用 — 用户不能删战略文档 (它是下游品牌监控/情报站/chat 的关键基线)
  fetchBrandStrategyExtract,
  triggerBrandStrategyExtraction,
  updateBrandStrategyExtract,
  type MeetingActionItem,
  type NextStepItem,
  type SuggestionLogEntry,
  type SuggestionAction,
  type StrategicDocsResponse,
  type StrategicDocType,
  type BrandStrategyExtract,
  type ClarificationContext,
  type ClarificationEventLine,
  type ClarificationTimelineItem,
  type ClarificationPerson,
  type ClarificationCommitment,
  type ClarificationNeed,
  type ClarificationProfile,
  type ClientNarrative,
  type NarrativeProfileUpdate,
  type NarrativeDimensionRecord,
  type NarrativeDimensionKey,
  type NarrativeClarification,
  type OfficialWebsiteStatus,
} from '../../lib/api';
import { GlossaryAttributeReviewSection } from './GlossaryAttributeReviewSection';
import { UnifiedTodoSection } from './UnifiedTodoSection';
// [DEPRECATED 2026-05-22 · 新计划阶段 0] V2.1 8 段组件已废弃, 跟产品手册 §03 钦定
// 6 段 (essence/cooperation/business_intro/people/timeline/next_steps) 冲突.
// 主仓库现有 NarrativePanel (走 narrative_generator) 才是真渲染入口.
// import { FullNarrativeSection } from './FullNarrativeSection';

interface StrategicClarificationViewProps {
  clientOptions: Array<{ id: string; name: string }>;
  selectedClientId: string;
  onClientChange: (id: string) => void;
  flash?: (level: 'success' | 'error' | 'info', message: string) => void;
  /** UnifiedTodoSection 里点 → 时触发, 由 App 接住打开原任务编辑器并预填. */
  onPromoteTodo?: (todo: import('../../lib/api').UnifiedTodo) => void;
}

type Confidence = 'high' | 'medium' | 'low';

const CONFIDENCE_META: Record<Confidence, { label: string; bg: string; text: string; icon: typeof CheckCircle }> = {
  high: { label: '已更新', bg: 'bg-emerald-50', text: 'text-emerald-700', icon: CheckCircle },
  medium: { label: '部分确认', bg: 'bg-amber-50', text: 'text-amber-700', icon: AlertCircle },
  low: { label: '待补充', bg: 'bg-slate-100', text: 'text-slate-500', icon: HelpCircle },
};

function isPrimaryOfficialWebsitePage(rawUrl: string): boolean {
  try {
    const url = new URL(rawUrl);
    return !/^\/(?:seo|share)\//i.test(url.pathname);
  } catch {
    return false;
  }
}

export function StrategicClarificationView({
  clientOptions,
  selectedClientId,
  onClientChange,
  flash,
  onPromoteTodo,
}: StrategicClarificationViewProps) {
  const [narrative, setNarrative] = useState<ClientNarrative | null>(null);
  const [clarifications, setClarifications] = useState<NarrativeClarification[]>([]);
  const [ctx, setCtx] = useState<ClarificationContext | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [regenerating, setRegenerating] = useState(false);
  /** 单维度刷新进行中的维度集合 — 允许多个维度并发刷新但避免同一维度重复点击 */
  const [regeneratingDimensions, setRegeneratingDimensions] = useState<Set<NarrativeDimensionKey>>(new Set());
  const [dimensionRefreshErrors, setDimensionRefreshErrors] = useState<Partial<Record<NarrativeDimensionKey, string>>>({});
  const [refreshTodoKey, setRefreshTodoKey] = useState(0);
  const [officialWebsite, setOfficialWebsite] = useState<OfficialWebsiteStatus | null>(null);
  const [officialWebsiteUrl, setOfficialWebsiteUrl] = useState('');
  const [officialWebsiteRefreshing, setOfficialWebsiteRefreshing] = useState(false);
  const [officialWebsiteError, setOfficialWebsiteError] = useState<string | null>(null);
  const [officialWebsiteElapsed, setOfficialWebsiteElapsed] = useState(0);
  const [officialWebsiteRefreshKey, setOfficialWebsiteRefreshKey] = useState(0);

  // 根因修复: 切客户竞态 — async 操作(submit clarification / regenerate narrative /
  // regenerate single dim)期间用户可能切到另一客户. await 返回时如果直接 setX(新数据),
  // 旧客户的响应会污染新客户的 UI. 通用解法: ref 持有"当前真实 selectedClientId",
  // 每个 async handler 开头捕获 capturedId, await 后校验 ref.current === captured.
  //
  // 注: useEffect 内的 loadAll 已有 isMounted 闭包做同等校验; 事件 handler
  // (handleClarify / handleRegenerate / handleRegenerateDimension) 无法用 useEffect cleanup,
  // 必须靠 ref 这条独立路径.
  const selectedClientIdRef = useRef(selectedClientId);
  useEffect(() => {
    selectedClientIdRef.current = selectedClientId;
  }, [selectedClientId]);

  const loadAll = useCallback(async (clientId: string, isMounted: () => boolean) => {
    if (!isMounted()) return;
    setLoading(true);
    setError(null);
    setDimensionRefreshErrors({});
    try {
      // 本地优先(5/29): 叙事独立加载 — 后端 GET 本地优先读镜像, 断网也能返回上次版本。
      // 澄清/上下文是云端协同, 断网失败降级为空, 不再连累叙事整页变空。
      try {
        const n = await getClientNarrative(clientId);
        if (!isMounted()) return;  // 旧请求返回时新客户已切, 丢弃
        setNarrative(n);
      } catch (err) {
        if (!isMounted()) return;
        setError(err instanceof Error ? err.message : '加载失败');
        setNarrative(null);
      }
      const c = await listClientNarrativeClarifications(clientId).catch(() => null);
      const x = await getClientClarificationContext(clientId).catch(() => null);
      const official = await getClientOfficialWebsite(clientId).catch(() => null);
      if (!isMounted()) return;
      setClarifications(c?.clarifications ?? []);
      setCtx(x);
      setOfficialWebsite(official);
      setOfficialWebsiteUrl(String(official?.registeredUrl || ''));
    } finally {
      if (isMounted()) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!selectedClientId) {
      setNarrative(null);
      setClarifications([]);
      setCtx(null);
      return;
    }
    let mounted = true;
    const isMounted = () => mounted;
    void loadAll(selectedClientId, isMounted);
    return () => { mounted = false; };
  }, [selectedClientId, loadAll]);

  const handleOfficialWebsiteRefresh = async () => {
    const capturedClientId = selectedClientId;
    const url = officialWebsiteUrl.trim();
    if (!capturedClientId || !url || officialWebsiteRefreshing) return;
    setOfficialWebsiteRefreshing(true);
    setOfficialWebsiteElapsed(0);
    setOfficialWebsiteError(null);
    try {
      const refreshed = await refreshClientOfficialWebsite(capturedClientId, url);
      if (selectedClientIdRef.current !== capturedClientId) return;
      setOfficialWebsite(refreshed);
      setOfficialWebsiteRefreshKey((value) => value + 1);
      flash?.('success', `已读取 ${refreshed.pageCount} 个官网页面；权威事实已进入项目知识。`);
    } catch (err) {
      if (selectedClientIdRef.current !== capturedClientId) return;
      setOfficialWebsiteError(err instanceof Error ? err.message : '官网权威信息抓取失败，可重试');
    } finally {
      if (selectedClientIdRef.current === capturedClientId) setOfficialWebsiteRefreshing(false);
    }
  };

  useEffect(() => {
    if (!officialWebsiteRefreshing) return undefined;
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      setOfficialWebsiteElapsed(Math.max(1, Math.floor((Date.now() - startedAt) / 1000)));
    }, 1_000);
    return () => window.clearInterval(timer);
  }, [officialWebsiteRefreshing]);

  const primaryOfficialPages = (officialWebsite?.pages || [])
    .filter((page) => isPrimaryOfficialWebsitePage(page.url))
    .slice(0, 12);
  const supplementalOfficialPageCount = Math.max(
    0,
    Number(officialWebsite?.pageCount || 0) - primaryOfficialPages.length,
  );

  // stale 信号检测 (只通知,不自动 regen).
  // 历史版本会自动 regenerate, 但多人协作场景下任意同事打开页面都会触发 → 覆盖别人校准的内容.
  // 改成"检测到 stale 仅 flash 提示", 由用户决定按板块单独刷新还是全局重生.
  useEffect(() => {
    if (!selectedClientId || !narrative || regenerating) return;
    let cancelled = false;
    (async () => {
      try {
        const stale = await getNarrativeStaleStatus(selectedClientId);
        if (cancelled || !stale.isStale) return;
        flash?.(
          'info',
          `检测到新材料 (${stale.lastDocTitle || '新文档'}). 点对应板块右上角刷新按钮单独更新, 或点"下一步要做什么"卡片标题旁的 ↻ 全部重生.`,
        );
      } catch (err) {
        // eslint-disable-next-line no-console
        console.warn('[narrative stale-status] skip', err);
      }
    })();
    return () => { cancelled = true; };
  }, [selectedClientId, narrative?.id, regenerating, flash]);

  const handleClarify = async (dimension: NarrativeDimensionKey, answer: string, question?: string) => {
    if (!selectedClientId || !answer.trim()) return;
    const capturedClientId = selectedClientId;
    try {
      const submitted = await submitClientNarrativeClarification(capturedClientId, {
        dimension,
        answer: answer.trim(),
        question,
        basedOnRev: narrative?.rev,
      });
      // 切客户后, 旧客户的 success flash 不显示 (避免误导)
      if (selectedClientIdRef.current !== capturedClientId) return;
      if (submitted.narrative) setNarrative(submitted.narrative);
      flash?.('success', '补充已成为项目正式知识，客户档案已重新整理。');
      const c = await listClientNarrativeClarifications(capturedClientId);
      if (selectedClientIdRef.current !== capturedClientId) return;
      setClarifications(c.clarifications);
    } catch (err) {
      if (selectedClientIdRef.current !== capturedClientId) return;
      flash?.('error', err instanceof Error ? err.message : '提交失败');
      throw err;
    }
  };

  const handleRegenerateDimension = async (dim: NarrativeDimensionKey) => {
    if (!selectedClientId) return;
    if (regeneratingDimensions.has(dim)) return; // 已在刷新此维度,忽略重复点击
    const capturedClientId = selectedClientId;
    setRegeneratingDimensions((prev) => {
      const next = new Set(prev);
      next.add(dim);
      return next;
    });
    setDimensionRefreshErrors((prev) => ({ ...prev, [dim]: undefined }));
    try {
      const fresh = await regenerateClientNarrative(capturedClientId, {
        trigger: 'manual_single_dimension',
        force: true,
        dimensions: [dim],
      });
      // 切客户后, 旧客户的 narrative 不写入新客户 state
      if (selectedClientIdRef.current !== capturedClientId) return;
      setNarrative(fresh);
      const c = await listClientNarrativeClarifications(capturedClientId);
      if (selectedClientIdRef.current !== capturedClientId) return;
      setClarifications(c.clarifications);
      setDimensionRefreshErrors((prev) => ({ ...prev, [dim]: undefined }));
      const label = DIMENSION_META[dim]?.label || dim;
      flash?.('success', `已重新生成: ${label} (v${fresh.rev}, 其他板块未变)`);
    } catch (err) {
      if (selectedClientIdRef.current !== capturedClientId) return;
      // 模型生成失败不应升级成整桌面的“系统问题”。先重新读取云端已确认的
      // 最新档案（补充事实可能已经成功传播），再只在当前卡片显示可重试状态。
      try {
        const latest = await getClientNarrative(capturedClientId);
        if (selectedClientIdRef.current === capturedClientId) setNarrative(latest);
      } catch {
        // 保留当前可读版本；卡片内给出准确失败状态。
      }
      if (selectedClientIdRef.current === capturedClientId) {
        setDimensionRefreshErrors((prev) => ({
          ...prev,
          [dim]: err instanceof Error
            ? `本栏重新整理未完成：${err.message}。现有档案已保留，可重试。`
            : `${DIMENSION_META[dim]?.label || dim}重新整理未完成，现有档案已保留，可重试。`,
        }));
      }
    } finally {
      // 不论是否切客户都要清掉 regenerating flag (UI 不再 spin),
      // 切了客户也无所谓 — regeneratingDimensions 本身不影响新客户的 UI
      setRegeneratingDimensions((prev) => {
        const next = new Set(prev);
        next.delete(dim);
        return next;
      });
    }
  };

  const handleRegenerate = async () => {
    if (!selectedClientId) return;
    // 全部重生会覆盖所有 6 个维度,包括其他同事可能已经校准过的内容.
    // 加 confirm 防止误点; 想精细更新请用维度卡上的单刷按钮.
    const confirmed = window.confirm(
      '全部重生会覆盖 6 个维度全部内容,包括其他同事已校准的部分.\n如果只想更新某一个板块,请关闭此弹窗,点该板块右上角刷新按钮.\n\n确认要全部重生吗?',
    );
    if (!confirmed) return;
    const capturedClientId = selectedClientId;
    setRegenerating(true);
    try {
      const fresh = await regenerateClientNarrative(capturedClientId, { trigger: 'manual', force: true });
      // 切客户后, 旧客户的全部重生结果不写入新客户 state
      if (selectedClientIdRef.current !== capturedClientId) return;
      setNarrative(fresh);
      const c = await listClientNarrativeClarifications(capturedClientId);
      if (selectedClientIdRef.current !== capturedClientId) return;
      setClarifications(c.clarifications);
      flash?.('success', `已重新生成 v${fresh.rev} (生成方: ${fresh.generator})`);
    } catch (err) {
      if (selectedClientIdRef.current !== capturedClientId) return;
      flash?.('error', err instanceof Error ? err.message : '生成失败');
    } finally {
      // regenerating spin 始终要清, 否则 UI 永远转
      setRegenerating(false);
    }
  };

  return (
    <section className="rounded-[28px] border border-slate-100 bg-white p-6 shadow-[0_8px_28px_rgba(15,23,42,0.05)]">
      {/* 顶部: 客户名 + 极简精选 4 项 (左) + 客户选择器 (右), 同行平齐 */}
      <div className="flex items-baseline justify-between gap-4 mb-5 flex-wrap">
        <div className="flex items-baseline gap-3 min-w-0 flex-1 flex-wrap">
          <h2 className="text-[20px] font-bold text-slate-900 shrink-0">
            {clientOptions.find((c) => c.id === selectedClientId)?.name ?? ''}
          </h2>
          {(() => {
            const aiLine = narrative?.dataLayerGaps.find((g) => g.startsWith('✓ AI 本次看到'));
            if (!aiLine) return null;
            const pick = (re: RegExp): string | null => {
              const m = aiLine.match(re);
              return m ? m[1] : null;
            };
            const docs = pick(/(\d+)\s*份原始资料/);
            const persons = pick(/(\d+)\s*人物/);
            const dates = pick(/(\d+)\s*关键日期/);
            const moneys = pick(/(\d+)\s*金额/);
            const Item = ({ n, unit }: { n: string; unit: string }) => (
              <span>
                <span className="font-semibold text-slate-700">{n}</span>
                <span className="text-slate-400 ml-0.5">{unit}</span>
              </span>
            );
            return (
              <span className="text-[12px] text-slate-500 leading-relaxed">
                基于
                {docs && <> <Item n={docs} unit="份资料" /> · </>}
                {persons && <Item n={persons} unit="位人物" />}
                {dates && <> · <Item n={dates} unit="个时间节点" /></>}
                {moneys && <> · <Item n={moneys} unit="笔金额" /></>}
              </span>
            );
          })()}
        </div>
        <ClientPicker clientOptions={clientOptions} selectedClientId={selectedClientId} onClientChange={onClientChange} />
      </div>

      {!selectedClientId && <EmptyState text="先选一个客户, 看 AI 当前对它的理解。" />}
      {selectedClientId && loading && <EmptyState text="加载中..." />}
      {selectedClientId && error && (
        <div className="rounded-2xl border border-rose-100 bg-rose-50 px-4 py-3 text-[12px] text-rose-700">
          {error}
        </div>
      )}

      {selectedClientId && !loading && (
        <section className="mb-5 rounded-2xl border border-emerald-100 bg-emerald-50/40 px-4 py-3.5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <Database size={14} className="text-emerald-700" />
                <h3 className="text-[13px] font-bold text-slate-900">官网权威信息源</h3>
              </div>
              <p className="mt-1 text-[10.5px] leading-5 text-slate-500">
                同域页面会保存具体网址、版本和证据；无歧义事实进入项目知识，变化或冲突保留为待核实候选。
              </p>
            </div>
            {officialWebsite?.updatedAt && (
              <span className="text-[9.5px] text-slate-400">更新于 {officialWebsite.updatedAt.slice(0, 16).replace('T', ' ')}</span>
            )}
          </div>
          <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-emerald-100 bg-white px-3 py-2">
            <span className="min-w-0 flex-1 truncate text-[10.5px] text-slate-600">
              {officialWebsiteUrl || '尚未登记官网，请在“项目信息编辑”中填写并保存。'}
            </span>
            {officialWebsiteUrl && (
              <button
                type="button"
                disabled={officialWebsiteRefreshing}
                onClick={() => void handleOfficialWebsiteRefresh()}
                className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-1.5 text-[10.5px] font-bold text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <RefreshCw size={11} className={officialWebsiteRefreshing ? 'animate-spin' : ''} />
                {officialWebsiteRefreshing
                  ? officialWebsiteElapsed < 4
                    ? '快速浏览官网…'
                    : officialWebsiteElapsed < 10
                    ? '制定权威信息目标…'
                    : `逐项目抓取并核验 · ${officialWebsiteElapsed}s`
                  : '重新抓取并比对'}
              </button>
            )}
          </div>
          {officialWebsiteError && (
            <p className="mt-2 rounded-lg border border-rose-100 bg-rose-50 px-2.5 py-2 text-[10.5px] text-rose-700">{officialWebsiteError}</p>
          )}
          {officialWebsite?.researchProgress && (
            <p className="mt-2 text-[10px] text-emerald-700">
              本轮已结算 {officialWebsite.researchProgress.completedTargetCount}/{officialWebsite.researchProgress.targetCount} 个目标
              {' · '}提炼 {officialWebsite.researchProgress.factCount} 条可核实事实
              {officialWebsite.researchProgress.retryableFailureCount > 0
                ? ` · ${officialWebsite.researchProgress.retryableFailureCount} 个批次可重试`
                : ''}
            </p>
          )}
          {officialWebsite && officialWebsite.pages.length > 0 && (
            <div className="mt-3">
              <p className="text-[10px] font-bold text-slate-600">
                已读取 {officialWebsite.pageCount} 个页面 · 重点入口 {primaryOfficialPages.length} 个
                {officialWebsite.candidateCount > 0 ? ` · ${officialWebsite.candidateCount} 项变化待核实` : ' · 当前无待核实冲突'}
              </p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {primaryOfficialPages.map((page) => (
                  <a
                    key={`${page.url}:${page.version}`}
                    href={page.url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex max-w-full items-center gap-1 rounded-full border border-emerald-100 bg-white px-2.5 py-1 text-[9.5px] font-semibold text-emerald-700 hover:border-emerald-300"
                    title={page.url}
                  >
                    <ExternalLink size={9} />
                    <span className="max-w-[220px] truncate">{page.title}</span>
                  </a>
                ))}
              </div>
              {supplementalOfficialPageCount > 0 && (
                <p className="mt-2 text-[9.5px] text-slate-400">
                  另有 {supplementalOfficialPageCount} 个 SEO / 文章 / 报告证据页参与事实核验，不作为官网重点入口展示。
                </p>
              )}
            </div>
          )}
        </section>
      )}

      {/* [DEPRECATED 2026-05-22 · 新计划阶段 0] V2.1 8 段 FullNarrativeSection 已废弃,
          跟产品手册 §03 钦定 6 段 (essence/cooperation/business_intro/people/
          timeline/next_steps) 冲突. 下面的 NarrativePanel 走主仓库 6 段叙事,
          才是真渲染入口. */}
      {/* {selectedClientId && (
        <FullNarrativeSection
          clientId={selectedClientId}
          actorId="view_strategic_clarification"
        />
      )} */}

      {selectedClientId && narrative && !loading && (
        <NarrativePanel
          narrative={narrative}
          clarifications={clarifications}
          onClarify={handleClarify}
          onRegenerate={handleRegenerate}
          regenerating={regenerating}
          onRegenerateDimension={handleRegenerateDimension}
          regeneratingDimensions={regeneratingDimensions}
          dimensionRefreshErrors={dimensionRefreshErrors}
          clientName={clientOptions.find((c) => c.id === selectedClientId)?.name ?? ''}
          refreshTodoKey={refreshTodoKey}
          onPromoteTodo={onPromoteTodo}
          flash={flash}
        />
      )}

      {/* M1 · 字典待审 (学霸笔记本入口) — 在 6 段叙事下方, 引用源折叠之上 */}
      {selectedClientId && (
        <GlossaryAttributeReviewSection
          clientId={selectedClientId}
          refreshKey={officialWebsiteRefreshKey}
          flash={flash}
          onChanged={() => {
            // 字典 date 类点 [已完成] 后, 后端启发式联动 task done; 这里触发 UnifiedTodoSection 重新拉
            setRefreshTodoKey((k) => k + 1);
          }}
        />
      )}

      {/* 原"AI 引用源·字典层" (ReferenceLayerSection) 和"数据卫生" (DataHygieneSection)
          两个 section 已删 — ReferenceLayerSection 是 db raw row 调试视图, 核心流程不依赖;
          DataHygieneSection 的重复文件清理已经在客户工作台 → 资料区覆盖, 不需要在战略陪伴
          重复出现. */}
    </section>
  );
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 6 维度故事网主面板
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

const DIMENSION_META: Record<NarrativeDimensionKey, {
  label: string;
  icon: typeof Compass;
  hint: string;
}> = {
  // v1.0 6 层 (云端 narrative 真维度)
  // strategic_dna 不在这里 — 它是独立 UI (StrategicDnaCard), 数据从 /strategic-docs endpoint 拿
  essence:        { label: '组织介绍',           icon: Compass,       hint: '客户机构是谁 / 赛道 / 行业定位 / 影响力' },
  business_intro: { label: '业务介绍',           icon: Briefcase,     hint: '客户机构内含项目逐个详介' },
  cooperation:    { label: '合作关系',           icon: Handshake,     hint: '益语跟客户的服务关系 / 合作周期 / 核心交付' },
  people:         { label: '关键人物',           icon: Network,       hint: '益语方 + 客户方 + 每个项目对应角色' },
  timeline:       { label: '时间线',             icon: History,       hint: '合作里程碑 (起点→转折→现状)' },
  next_steps:     { label: '本阶段战略思路',     icon: ArrowRight,    hint: '战略层 / 关系层 / 风险对冲 — 给方向, 不列条目(看右侧"下一步要做什么")' },
  // 兼容旧 rev (废弃但仍可能从云端拿到)
  history:        { label: '来龙去脉 (旧)',     icon: History,       hint: '已废弃, 见时间线' },
  commitments:    { label: '承诺网 (旧)',       icon: Handshake,     hint: '已废弃, 见承诺与下一步' },
  risks:          { label: '卡点与风险 (旧)',   icon: AlertTriangle, hint: '已废弃, 隐含在下一步里' },
  next:           { label: '下一步 (旧)',       icon: ArrowRight,    hint: '已废弃, 见承诺与下一步' },
};

const DIMENSION_ORDER: NarrativeDimensionKey[] = [
  // 6 层 narrative 维度 (云端 storage 真维度)
  // 注: StrategicDnaCard (战略定位与发展路径, 用户上传的 .md) 是独立组件, 在循环外渲染,
  // 视觉上插在 essence 之后 / business_intro 之前
  'essence', 'business_intro', 'cooperation', 'people', 'timeline', 'next_steps',
  // 旧 dim 兼容显示在最后, 实际新 rev 不会有
  'history', 'commitments', 'risks', 'next',
];

// ────────────────────────────────────────────────────────────
// 业务介绍 — 启发式分段渲染
// 策略 (按优先级尝试): markdown headings → 数字列表 → bullet → 大段中按"项目名:" 切
// ────────────────────────────────────────────────────────────
interface Segment {
  title: string;
  body: string;
  source?: string;  // 从 body 末尾 [来自: X] 抽出
}

function parseBusinessIntro(text: string): Segment[] | null {
  const t = (text || '').trim();
  if (!t) return null;

  // 1. Markdown headings (## XX / ### XX)
  const headingRe = /^#{1,6}\s+(.+)$/gm;
  const headingMatches = Array.from(t.matchAll(headingRe));
  if (headingMatches.length >= 2) {
    const segs: Segment[] = [];
    for (let i = 0; i < headingMatches.length; i++) {
      const m = headingMatches[i];
      const start = (m.index ?? 0) + m[0].length;
      const end = i + 1 < headingMatches.length ? headingMatches[i + 1].index ?? t.length : t.length;
      segs.push({ title: m[1].trim(), body: t.slice(start, end).trim() });
    }
    return segs;
  }

  // 2. 数字编号列表 — 行内或跨行都能切
  //    "1. 测试项目C：xxx 2. 测试项目A：xxx 3. ..." (一段) 或 "1. xxx\n2. yyy" (多行)
  //    不再用 ^ 行首锚定, 用 lookbehind (开头/句号/换行/空格 后) + 数字
  const numberRe = /(?:^|[。\n]|\s)(\d+|[①②③④⑤⑥⑦⑧⑨⑩])[\.、)]\s*([^：:0-9]{2,30})[：:]([^]+?)(?=(?:[。\n\s]\d+[\.、)]|[①②③④⑤⑥⑦⑧⑨⑩][\.、)]?)|$)/g;
  const numberMatches = Array.from(t.matchAll(numberRe));
  if (numberMatches.length >= 2) {
    const segs: Segment[] = [];
    const sourceRe = /\s*\[来自[::]\s*([^\]]+)\]\s*$/;
    for (const m of numberMatches) {
      const title = m[2].trim();
      let body = m[3].trim().replace(/^[。,，;；]\s*/, '').replace(/[。,,;；]\s*\d+[\.、)].*$/, '');
      let source: string | undefined;
      const srcMatch = body.match(sourceRe);
      if (srcMatch) {
        source = srcMatch[1].trim();
        body = body.replace(sourceRe, '').trim();
      }
      if (title && (title.length <= 30)) {
        segs.push({ title, body, source });
      }
    }
    if (segs.length >= 2) return segs;
  }

  // 3. Bullet points (· - * • 开头)
  const bulletRe = /^[\s]*[·\-\*•]\s+([^\n]+)/gm;
  const bulletMatches = Array.from(t.matchAll(bulletRe));
  if (bulletMatches.length >= 3) {
    const segs: Segment[] = [];
    for (const m of bulletMatches) {
      const line = m[1].trim();
      const colonIdx = line.search(/[:：]/);
      if (colonIdx > 0 && colonIdx < 30) {
        segs.push({ title: line.slice(0, colonIdx).trim(), body: line.slice(colonIdx + 1).trim() });
      } else {
        segs.push({ title: line.slice(0, 30), body: '' });
      }
    }
    return segs;
  }

  // 4. 段落级 "项目名: 描述" — 中文流水文里常见的格式
  // 例: "测试项目A是测试机构A核心项目, 服务 18-24 青年. 教师赋能项目..."
  // 用句号+疑似项目名启发, 但太脆弱, 留作未来 LLM 二次抽取
  return null;
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 战略定位与发展路径 (用户上传的 .md, 不让 LLM 编造)
// 视觉上插在 essence 卡之后, 跟其他维度卡同构, 但数据源是独立 endpoint
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

/**
 * 战略定位与发展路径 — 改造后的卡片 (2026-05-21).
 *
 * 设计原则:
 *   1. 用户上传 strategy.md + methodology.md → backend LLM 抽取 → 显示**精炼后的 200 字以内文本** (不显示 .md 原文)
 *   2. 抽取出来的"战略主张 + 方法学"用户可以**直接编辑** (覆盖 LLM 输出, 保留下游 stakeholders 不动)
 *   3. 只保留**重传** (上传/替换 .md), **不允许删除** — 战略文档是下游品牌监控/情报站/chat 的关键基线
 *
 * 状态机:
 *   [未上传]    → 显示 2 个上传槽位 (.md 文件)
 *   [已上传]    → 显示 LLM 抽取的 200 字文本 + [编辑] / [重传 .md] 按钮
 *   [编辑中]    → 显示 2 个 textarea (战略主张 / 方法学), 字数实时计数, 可保存/取消
 *   [保存中]    → disabled
 */
function StrategicDnaCard({
  clientId,
  flash,
}: {
  clientId: string;
  flash?: (level: 'success' | 'error' | 'info', message: string) => void;
}) {
  const [docs, setDocs] = useState<StrategicDocsResponse | null>(null);
  const [extract, setExtract] = useState<BrandStrategyExtract | null>(null);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editingDraft, setEditingDraft] = useState<{ strategicObjective: string; methodology: string }>({
    strategicObjective: '',
    methodology: '',
  });
  const [extracting, setExtracting] = useState(false);
  const [saving, setSaving] = useState(false);

  // 根因修复: clientId 是 prop, 父组件切客户时 prop 变化, 但本组件的 async 操作
  // (reload / upload / LLM extract 最长 1-2 分钟) 期间不知道. clientIdRef 持有
  // "ref 视角的最新 clientId", 每个 await 后校验 ref.current === capturedClientId,
  // 不一致就丢弃响应 (旧客户的数据不污染新客户 UI).
  const clientIdRef = useRef(clientId);
  useEffect(() => {
    clientIdRef.current = clientId;
  }, [clientId]);

  const reload = useCallback(() => {
    if (!clientId) return;
    const capturedClientId = clientId;
    setLoading(true);
    void Promise.all([
      getStrategicDocs(capturedClientId),
      fetchBrandStrategyExtract(capturedClientId),
    ])
      .then(([d, e]) => {
        if (clientIdRef.current !== capturedClientId) return;  // 切客户了, 丢弃
        setDocs(d);
        setExtract(e.extract);
      })
      .catch(() => {
        if (clientIdRef.current !== capturedClientId) return;
        setDocs(null);
        setExtract(null);
      })
      .finally(() => {
        if (clientIdRef.current !== capturedClientId) return;
        setLoading(false);
      });
  }, [clientId]);

  useEffect(() => {
    reload();
  }, [reload]);

  const handleFilePick = (docType: StrategicDocType) => async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';  // 允许重选同一文件
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.md') && !file.name.toLowerCase().endsWith('.markdown')) {
      flash?.('error', '只接受 .md / .markdown 格式');
      return;
    }
    if (file.size > 200 * 1024) {
      flash?.('error', '文档过大 (>200KB), 请精简后再上传');
      return;
    }
    const capturedClientId = clientId;
    try {
      const mdContent = await file.text();
      await uploadStrategicDoc(capturedClientId, { docType, fileName: file.name, mdContent });
      // 切客户后, 旧客户的 success flash 不打 (避免误导新客户视角)
      if (clientIdRef.current !== capturedClientId) return;
      flash?.('success', `已上传 ${docType === 'strategy' ? '战略文档' : '方法论文档'}, 正在让 AI 抽取核心 200 字...`);
      const next = await getStrategicDocs(capturedClientId);
      if (clientIdRef.current !== capturedClientId) return;
      setDocs(next);
      if (next.hasStrategy && next.hasMethodology) {
        // LLM 抽取是最长操作 (60-120s), 用户最可能在这段时间切走
        setExtracting(true);
        try {
          await triggerBrandStrategyExtraction(capturedClientId);
          if (clientIdRef.current !== capturedClientId) return;
          flash?.('success', '✓ AI 抽取完成');
        } catch (err) {
          if (clientIdRef.current !== capturedClientId) return;
          flash?.('error', err instanceof Error ? `AI 抽取失败: ${err.message}` : 'AI 抽取失败');
        } finally {
          // setExtracting(false) 切了客户也无害, 因为新客户的 extracting state 是自己实例的
          // 但 reload() 必须只在还是同客户时调, 否则触发新客户的 fetch
          if (clientIdRef.current === capturedClientId) {
            setExtracting(false);
            reload();
          }
        }
      }
    } catch (err) {
      if (clientIdRef.current !== capturedClientId) return;
      flash?.('error', err instanceof Error ? err.message : '上传失败');
    }
  };

  const handleStartEdit = () => {
    if (!extract) return;
    setEditingDraft({
      strategicObjective: extract.strategicObjective || '',
      methodology: extract.methodology || '',
    });
    setEditing(true);
  };

  const handleSave = async () => {
    const total = editingDraft.strategicObjective.length + editingDraft.methodology.length;
    if (total > 200) {
      flash?.('error', `战略主张 + 方法学 共 ${total} 字, 超过 200 字上限, 请精简`);
      return;
    }
    if (!editingDraft.strategicObjective.trim() || !editingDraft.methodology.trim()) {
      flash?.('error', '战略主张和方法学都不能为空');
      return;
    }
    const capturedClientId = clientId;
    setSaving(true);
    try {
      const resp = await updateBrandStrategyExtract(capturedClientId, editingDraft);
      if (clientIdRef.current !== capturedClientId) return;
      setExtract(resp.extract);
      setEditing(false);
      flash?.('success', '已保存');
    } catch (err) {
      if (clientIdRef.current !== capturedClientId) return;
      flash?.('error', err instanceof Error ? err.message : '保存失败');
    } finally {
      // setSaving(false) 写到当前实例的 state, 切客户后是新组件实例, 旧实例无人看, 不影响
      if (clientIdRef.current === capturedClientId) setSaving(false);
    }
  };

  const handleCancel = () => {
    setEditing(false);
    setEditingDraft({ strategicObjective: '', methodology: '' });
  };

  if (loading && !docs && !extract) {
    return (
      <div className="rounded-2xl border border-slate-100 bg-white p-5">
        <div className="text-[11px] text-slate-400">加载战略文档...</div>
      </div>
    );
  }

  const hasAllDocs = Boolean(docs?.hasStrategy && docs?.hasMethodology);
  const hasExtract = Boolean(extract && (extract.strategicObjective || extract.methodology));

  return (
    <div className="rounded-2xl border border-slate-100 bg-gradient-to-br from-slate-50/40 to-white p-5">
      <div className="flex items-center justify-between gap-2 mb-3">
        <div className="flex items-center gap-2">
          <Target size={16} className="text-slate-600" />
          <h3 className="text-[15px] font-bold text-slate-900">战略定位与发展路径</h3>
          {extract?.isStale && (
            <span className="text-[10px] text-amber-700 bg-amber-100 rounded-full px-2 py-0.5 font-bold" title="上传的 .md 已变, 抽取结果可能过期">
              文档已更新, 待重抽
            </span>
          )}
        </div>
        <span className="text-[10px] text-slate-700 bg-slate-100 rounded-full px-2 py-0.5 font-bold">
          {hasExtract ? '已配置' : hasAllDocs ? '抽取中' : '未配置'}
        </span>
      </div>

      {/* 1. 未上传任何 .md → 显示 2 个上传槽位 */}
      {!hasAllDocs && !hasExtract && (
        <>
          <div className="text-[12px] leading-relaxed text-slate-700 bg-slate-50/60 rounded-xl px-4 py-3 border border-slate-100 mb-3">
            <p className="font-semibold text-slate-900 mb-1">AI 说:</p>
            <p>
              我没找到客户的战略文档和业务方法论. 上传这两份 <code className="bg-white px-1.5 py-0.5 rounded text-[11px]">.md</code> 后,
              AI 会抽出 200 字以内的"战略主张 + 方法学", 你可以再手动微调. 这份骨架是品牌监控/情报站/chat 等模块识别客户战略的关键基线.
            </p>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <StrategicDocUploadSlot
              docType="strategy"
              title="战略文档"
              hint="客户在赛道里走什么路线"
              uploaded={Boolean(docs?.hasStrategy)}
              onPick={handleFilePick('strategy')}
            />
            <StrategicDocUploadSlot
              docType="methodology"
              title="方法论文档"
              hint="客户的发展路径和工作方法"
              uploaded={Boolean(docs?.hasMethodology)}
              onPick={handleFilePick('methodology')}
            />
          </div>
        </>
      )}

      {/* 2. 已上传但还在抽取 / 抽取失败 */}
      {hasAllDocs && !hasExtract && (
        <div className="rounded-xl border border-slate-100 bg-white px-4 py-3 text-[12px] text-slate-600">
          {extracting ? '⏳ AI 正在抽取战略主张 + 方法学 (约 1-2 分钟)...' : '已上传两份文档, 但还没抽取出结果. 重传任意一份重新触发.'}
        </div>
      )}

      {/* 3. 已配置 — 显示 LLM 抽取的 200 字 + 编辑/重传 */}
      {hasExtract && !editing && extract && (
        <div className="space-y-3">
          <div className="rounded-xl border border-slate-200 bg-white px-4 py-3">
            <div className="flex items-center justify-between gap-2 mb-1.5">
              <div className="text-[11px] font-bold uppercase tracking-[0.12em] text-slate-700">战略主张</div>
              <span className="text-[10px] text-slate-400 tabular-nums">{extract.strategicObjective.length} 字</span>
            </div>
            <p className="text-[13px] leading-[1.75] text-slate-800 whitespace-pre-wrap">{extract.strategicObjective}</p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white px-4 py-3">
            <div className="flex items-center justify-between gap-2 mb-1.5">
              <div className="text-[11px] font-bold uppercase tracking-[0.12em] text-slate-700">组织方法学</div>
              <span className="text-[10px] text-slate-400 tabular-nums">{extract.methodology.length} 字</span>
            </div>
            <p className="text-[13px] leading-[1.75] text-slate-800 whitespace-pre-wrap">{extract.methodology}</p>
          </div>
          <div className="flex items-center justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={handleStartEdit}
              className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-[11px] font-bold text-slate-700 hover:bg-slate-50"
            >
              <Type size={12} />
              编辑
            </button>
            <label htmlFor="strategic-doc-replace-strategy" className="cursor-pointer inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-[11px] font-bold text-slate-700 hover:bg-slate-50">
              <UploadCloud size={12} />
              重传战略文档
              <input id="strategic-doc-replace-strategy" type="file" accept=".md,.markdown,text/markdown" className="hidden" onChange={handleFilePick('strategy')} />
            </label>
            <label htmlFor="strategic-doc-replace-methodology" className="cursor-pointer inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-[11px] font-bold text-slate-700 hover:bg-slate-50">
              <UploadCloud size={12} />
              重传方法论
              <input id="strategic-doc-replace-methodology" type="file" accept=".md,.markdown,text/markdown" className="hidden" onChange={handleFilePick('methodology')} />
            </label>
          </div>
        </div>
      )}

      {/* 4. 编辑中 — 2 个 textarea + 字数计数 + 保存/取消 */}
      {hasExtract && editing && (
        <div className="space-y-3">
          <StrategicEditField
            label="战略主张"
            value={editingDraft.strategicObjective}
            onChange={(v) => setEditingDraft((d) => ({ ...d, strategicObjective: v }))}
            placeholder="一句话讲清楚客户的战略主张 — 文档里最核心的 What & Why"
          />
          <StrategicEditField
            label="组织方法学"
            value={editingDraft.methodology}
            onChange={(v) => setEditingDraft((d) => ({ ...d, methodology: v }))}
            placeholder="一句话讲清楚客户实现战略的方法学骨架 — 关键路径/飞轮/抓手"
          />
          <div className="flex items-center justify-between gap-2 pt-1">
            <div className={`text-[11px] tabular-nums ${
              editingDraft.strategicObjective.length + editingDraft.methodology.length > 200
                ? 'text-rose-600 font-bold'
                : 'text-slate-500'
            }`}>
              总计 {editingDraft.strategicObjective.length + editingDraft.methodology.length} / 200 字
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleCancel}
                disabled={saving}
                className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-[11px] font-bold text-slate-600 hover:bg-slate-50 disabled:opacity-50"
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => void handleSave()}
                disabled={saving || editingDraft.strategicObjective.length + editingDraft.methodology.length > 200}
                className="inline-flex items-center gap-1.5 rounded-full bg-slate-700 text-white px-3 py-1.5 text-[11px] font-bold hover:bg-slate-800 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {saving ? '保存中...' : '保存'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** 未上传态的上传槽位 (dashed box, 点击触发 file picker). */
function StrategicDocUploadSlot({
  docType,
  title,
  hint,
  uploaded,
  onPick,
}: {
  docType: StrategicDocType;
  title: string;
  hint: string;
  uploaded: boolean;
  onPick: (event: React.ChangeEvent<HTMLInputElement>) => void;
}) {
  const inputId = `strategic-upload-${docType}`;
  return (
    <label htmlFor={inputId} className={`block cursor-pointer rounded-xl border-2 border-dashed px-4 py-4 text-center transition-colors ${
      uploaded
        ? 'border-slate-300 bg-slate-50/60 hover:border-slate-300'
        : 'border-slate-200 bg-white hover:border-slate-300'
    }`}>
      <UploadCloud size={18} className="mx-auto text-slate-500 mb-2" />
      <div className="text-[12px] font-bold text-slate-800">{title}{uploaded ? ' ✓' : ''}</div>
      <div className="text-[10px] text-slate-500 mt-0.5">{hint}</div>
      <div className="text-[10px] text-slate-600 font-bold mt-2">
        {uploaded ? '已上传, 点击替换' : '点击上传 .md 文件'}
      </div>
      <input id={inputId} type="file" accept=".md,.markdown,text/markdown" className="hidden" onChange={onPick} />
    </label>
  );
}

/** 编辑态的 textarea + label + 100 字红线提示. */
function StrategicEditField({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
}) {
  const over = value.length > 100;
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3">
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <div className="text-[11px] font-bold uppercase tracking-[0.12em] text-slate-700">{label}</div>
        <span className={`text-[10px] tabular-nums ${over ? 'text-rose-600 font-bold' : 'text-slate-400'}`}>
          {value.length} / 100 字
        </span>
      </div>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        rows={4}
        className="w-full resize-none rounded-lg border border-slate-200 bg-slate-50/40 px-3 py-2 text-[13px] leading-[1.75] text-slate-800 placeholder:text-slate-400 focus:outline-none focus:border-slate-300 focus:bg-white"
      />
    </div>
  );
}

function BusinessIntroSegmented({ text }: { text: string }) {
  const segs = parseBusinessIntro(text);
  if (!segs || segs.length === 0) {
    return <span className="whitespace-pre-wrap">{text}</span>;
  }
  return (
    <div className="space-y-3">
      {segs.map((s, idx) => (
        <div
          key={idx}
          className="rounded-lg bg-slate-50/70 border border-slate-100 px-3 py-2"
        >
          <div className="text-[13px] font-bold text-slate-800 mb-1">
            {s.title}
          </div>
          {s.body && (
            <div className="text-[12px] text-slate-600 leading-[1.7] whitespace-pre-wrap">
              {s.body}
            </div>
          )}
          {s.source && (
            <div className="mt-1.5 text-[10px] text-slate-400 italic">
              来自: {s.source}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function NarrativePanel({
  narrative,
  clarifications,
  onClarify,
  onRegenerate,
  regenerating,
  onRegenerateDimension,
  regeneratingDimensions,
  dimensionRefreshErrors,
  clientName,
  refreshTodoKey,
  onPromoteTodo,
  flash,
}: {
  narrative: ClientNarrative;
  clarifications: NarrativeClarification[];
  onClarify: (dim: NarrativeDimensionKey, answer: string, question?: string) => Promise<void>;
  onRegenerate: () => void;
  regenerating: boolean;
  onRegenerateDimension: (dim: NarrativeDimensionKey) => void;
  regeneratingDimensions: ReadonlySet<NarrativeDimensionKey>;
  dimensionRefreshErrors: Partial<Record<NarrativeDimensionKey, string>>;
  clientName: string;
  refreshTodoKey: number;
  onPromoteTodo?: (todo: import('../../lib/api').UnifiedTodo) => void;
  flash?: (level: 'success' | 'error' | 'info', message: string) => void;
}) {
  const dimsByKey = new Map<NarrativeDimensionKey, NarrativeDimensionRecord>(
    narrative.dimensions.map((d) => [d.dimension, d]),
  );
  const pending = clarifications.filter((c) => c.status === 'pending');
  const applied = clarifications.filter((c) => c.status === 'applied');
  // 本地刷新 key — 用户在主区块点 → / ✓ / ✗ 或在日志卡点"找回" 后, 主区块跟日志卡都重拉
  const [localRefreshKey, setLocalRefreshKey] = useState(0);
  const openLocalProfileSource = async (sourceId: string) => {
    try {
      const workspace = await getClientWorkspace(narrative.clientId);
      const document = (workspace.documents || []).find((item) => item.id === sourceId);
      const targetPath = document?.path || document?.originalSourcePath || '';
      if (!targetPath) {
        flash?.('error', '这条引用只有组织摘要，本机没有可打开的原文件');
        return;
      }
      const opened = await window.yiyuWorkbench.openPath(targetPath);
      if (!opened) flash?.('error', '原文件已移动或当前无法打开');
    } catch (error) {
      flash?.('error', error instanceof Error ? error.message : '原文件暂时无法打开');
    }
  };
  // 真生成: ai_doubao (云端) / backend_local_ai (本地, Plan A); 其它带 stub_ 前缀或空都视为降级
  const isStub = !narrative.generator
    || narrative.generator.startsWith('stub')
    || narrative.generator === 'stub_clarification_append';
  const generatorLabel = isStub
    ? `降级 (${narrative.generator})`
    : narrative.generator === 'strategy_companion_local_wiki_v1'
      ? '本机资料提炼'
    : narrative.generator === 'strategy_companion_verified_fact_router_v1'
      ? '旧版事实归档 · 待重新提炼'
    : narrative.generator === 'backend_local_ai'
      ? '本地 AI 真生成 (消费 atomic_facts+entities)'
      : 'AI 真生成';
  // overallPct (整体把握度) 之前用于右栏顶部的"整体把握度"section, 该 section 已删 —
  // 把握度信息分散在各维度卡片里, 不再聚合一个数字. 保留 narrative.overallConfidence 字段
  // 作为后端的诚实信号, 前端不再露出.

  // (客户名 + AI 本次看到 由父组件 StrategicClarificationView 顶部渲染, 不在这里重复)

  return (
    <div>
      {narrative.coverage && narrative.coverage.scannedDocumentCount > 0 && (
        <div className="mb-3 text-[11px] text-slate-500">
          本次已扫描 {narrative.coverage.scannedDocumentCount}/{narrative.coverage.eligibleDocumentCount} 份可解析资料；
          当前档案正文实际引用 {narrative.coverage.citedDocumentCount} 份。引用数少于扫描数表示其余资料未直接支撑当前文字，并非未读取。
        </div>
      )}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-5">
      <div className="space-y-4 min-w-0">
        {/* (状态条 + 加工层缺口 已按需求删除, 主体直接进入 6 段叙事) */}

        {/* 6 段叙事 + StrategicDnaCard 插在 essence 之后 / business_intro 之前
            (战略定位与发展路径 — 用户上传的 .md, 独立数据源, 不让 LLM 编造) */}
        {DIMENSION_ORDER.map((key) => {
          const dim = dimsByKey.get(key);
          if (!dim) return null;
          const dimClars = applied.filter((c) => c.dimension === key);
          const card = (
            <NarrativeDimensionCard
              key={key}
              dim={dim}
              appliedClarifications={dimClars}
              onClarify={(answer, question) => onClarify(key, answer, question)}
              onRefresh={() => onRegenerateDimension(key)}
              refreshing={regeneratingDimensions.has(key)}
              refreshError={dimensionRefreshErrors[key]}
              onOpenLocalSource={openLocalProfileSource}
            />
          );
          // essence 渲染完后, 紧跟着插入战略定位与发展路径卡
          if (key === 'essence') {
            return (
              <React.Fragment key="essence-with-dna">
                {card}
                <StrategicDnaCard clientId={narrative.clientId} flash={flash} />
              </React.Fragment>
            );
          }
          return card;
        })}
      </div>

      {/* 右侧 — 把握度卡片 + 澄清记录流 (共同编织追溯)
          (原"整体把握度 + 全部重生"section 已删 — 把握度信息在每个维度卡片里都有, "全部重生"
           入口收纳到下方"下一步要做什么"卡片标题旁的 icon, 视觉更紧凑.) */}
      <div className="space-y-3">
        <MeetingActionItemsCard
          clientId={narrative.clientId}
          onPromote={onPromoteTodo}
          refreshKey={refreshTodoKey}
          onLogChange={() => setLocalRefreshKey((k) => k + 1)}
          onRegenerateNarrative={onRegenerate}
          narrativeRegenerating={regenerating}
        />
        <SuggestionLogCard
          clientId={narrative.clientId}
          refreshKey={localRefreshKey}
          onChange={() => setLocalRefreshKey((k) => k + 1)}
        />
        <PendingClarificationsCard pending={pending} />
        <AppliedClarificationsCard applied={applied} />
      </div>
      </div>
    </div>
  );
}

function ProfileUpdatesCard({
  updates,
  narrativeNeedsRefresh,
}: {
  updates: NarrativeProfileUpdate[];
  narrativeNeedsRefresh: boolean;
}) {
  if (updates.length === 0) return null;
  const visibleUpdates = updates.slice(0, 8);
  return (
    <section
      data-strategic-profile-updates="true"
      className="mb-5 rounded-2xl border border-indigo-100 bg-indigo-50/55 px-5 py-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Database size={15} className="text-indigo-600" />
            <h3 className="text-[14px] font-bold text-slate-900">工作台记忆与事实更新</h3>
            <span className="rounded-full bg-white px-2 py-0.5 text-[10px] font-bold text-indigo-600 ring-1 ring-inset ring-indigo-100">
              {updates.length} 条正式项目知识
            </span>
          </div>
          <p className="mt-1 text-[11px] leading-5 text-slate-500">
            明确记住、纠错与补充无需组织审核；系统会保持事实原意并归入对应栏目，下方仅保留原材料核对视图。
          </p>
        </div>
        <span className={`rounded-full px-2.5 py-1 text-[10px] font-bold ${
          narrativeNeedsRefresh
            ? 'bg-amber-50 text-amber-700 ring-1 ring-inset ring-amber-200'
            : 'bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200'
        }`}>
          {narrativeNeedsRefresh ? '正式知识已保存 · 客户档案整理中' : '相关记忆已更新'}
        </span>
      </div>
      <div className="mt-3 grid grid-cols-1 gap-2 lg:grid-cols-2">
        {visibleUpdates.map((item) => {
          const organizationFact = item.visibility === 'organization';
          return (
            <article key={item.id} className="rounded-xl border border-white/90 bg-white px-3.5 py-3 shadow-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] font-bold text-slate-700">{item.title}</span>
                <span className={`shrink-0 rounded-full px-2 py-0.5 text-[9px] font-bold ${
                  organizationFact
                    ? 'bg-emerald-50 text-emerald-700'
                    : 'bg-blue-50 text-blue-700'
                }`}>
                  {organizationFact ? '成员已确认 · 即时生效' : '待同步'}
                </span>
              </div>
              <p className="mt-1.5 line-clamp-3 whitespace-pre-wrap text-[12px] leading-5 text-slate-600">
                {item.statement}
              </p>
              <p className="mt-1.5 text-[9px] text-slate-400">
                {item.updatedAt ? `更新于 ${item.updatedAt.slice(0, 16).replace('T', ' ')}` : `版本 ${item.version}`}
              </p>
            </article>
          );
        })}
      </div>
      {updates.length > visibleUpdates.length && (
        <p className="mt-2 text-[10px] text-slate-400">另有 {updates.length - visibleUpdates.length} 条历史更新，刷新叙事时仍会按权威顺序使用。</p>
      )}
    </section>
  );
}

function NarrativeDimensionCard({
  dim,
  appliedClarifications,
  onClarify,
  onRefresh,
  refreshing,
  refreshError,
  onOpenLocalSource,
}: {
  dim: NarrativeDimensionRecord;
  appliedClarifications: NarrativeClarification[];
  onClarify: (answer: string, question?: string) => Promise<void>;
  /** 单维度刷新: 只重生此维度,其他维度保留 cloud 现有内容 */
  onRefresh?: () => void;
  refreshing?: boolean;
  refreshError?: string;
  onOpenLocalSource?: (sourceId: string) => Promise<void>;
}) {
  const meta = DIMENSION_META[dim.dimension];
  const Icon = meta.icon;
  const confidenceMeta = CONFIDENCE_META[dim.confidence];
  const CIcon = confidenceMeta.icon;
  const [refsOpen, setRefsOpen] = useState(false);
  const [clarifyOpen, setClarifyOpen] = useState(false);
  const [clarifyText, setClarifyText] = useState('');
  const [clarifyQuestion, setClarifyQuestion] = useState('');
  const [clarifySaving, setClarifySaving] = useState(false);

  const handleSubmit = async () => {
    if (!clarifyText.trim() || clarifySaving) return;
    setClarifySaving(true);
    try {
      await onClarify(clarifyText, clarifyQuestion || undefined);
      setClarifyText('');
      setClarifyQuestion('');
      setClarifyOpen(false);
    } finally {
      setClarifySaving(false);
    }
  };

  const handleUploadClick = () => {
    // TODO(post-M1): 接 backend POST /clients/{id}/documents/upload-binary?dimension=${dim.dimension}
    alert(
      `📤 上传 ${meta.label} 相关资料\n\n` +
      `请先把文件 (例: 合作合同/项目说明书/财报) 拖入"工作台 → 客户文件夹".\n` +
      `系统会自动 OCR + ingest + 抽取字典 candidate, 替代你逐条澄清.\n\n` +
      `下一版会直接在这里上传, 一键到这个客户的"${meta.label}"专属目录.`
    );
  };

  return (
    <article className="rounded-2xl border border-slate-100 bg-white px-5 py-4 shadow-[0_2px_8px_rgba(15,23,42,0.03)]">
      <div className="flex items-center justify-between mb-2 gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <Icon size={15} className="text-slate-500" />
          <h3 className="text-[14px] font-bold text-slate-900">{meta.label}</h3>
          <span className="text-[10px] text-slate-400 hidden md:inline">{meta.hint}</span>
        </div>
        <div className="flex items-center gap-2">
          <div className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 ${confidenceMeta.bg} ${confidenceMeta.text}`}>
            <CIcon size={11} />
            <span className="text-[10px] font-bold">{confidenceMeta.label}</span>
          </div>
          {/* M2 取材来源标记: 让用户看到本段是语义检索还是关键词兜底 (无数据时不渲染) */}
          {dim.retrievalMode && (
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium ${
                dim.retrievalMode === 'semantic'
                  ? 'bg-blue-50 text-blue-600'
                  : dim.fallbackUsed || dim.retrievalMode === 'fallback_only' || dim.retrievalMode === 'legacy_like_only'
                    ? 'bg-amber-50 text-amber-600'
                    : 'bg-slate-100 text-slate-500'
              }`}
              title={dim.reindexRequired
                ? '本段主要靠关键词兜底召回，建议为该客户补跑语义索引以提升质量'
                : '本段资料的取材路径'}
            >
              {dim.retrievalMode === 'semantic'
                ? '语义检索'
                : dim.retrievalMode === 'semantic+fallback'
                  ? '语义+兜底'
                  : dim.retrievalMode === 'fallback_only'
                    ? '关键词兜底'
                    : dim.retrievalMode === 'legacy_like_only'
                      ? '旧路径'
                      : '取材'}
            </span>
          )}
          <button
            type="button"
            onClick={handleUploadClick}
            className="inline-flex items-center justify-center w-6 h-6 rounded-full text-slate-400 hover:text-blue-600 hover:bg-blue-50 transition"
            title={`上传 ${meta.label} 相关资料 (例: 合同/项目说明书), 自动替代逐条澄清`}
          >
            <UploadCloud size={14} />
          </button>
          {onRefresh && (
            <button
              type="button"
              onClick={onRefresh}
              disabled={refreshing}
              className="inline-flex items-center justify-center w-6 h-6 rounded-full text-slate-400 hover:text-blue-600 hover:bg-blue-50 transition disabled:opacity-50 disabled:cursor-not-allowed"
              title={`仅刷新此板块 (${meta.label}). 其他板块内容不受影响, 不会覆盖同事已校准的内容.`}
            >
              <RefreshCw size={13} className={refreshing ? 'animate-spin' : ''} />
            </button>
          )}
        </div>
      </div>

      <div className="text-[13px] text-slate-700 leading-[1.8] mb-2">
        {dim.narrative
          ? (dim.dimension === 'business_intro'
              ? <BusinessIntroSegmented text={dim.narrative} />
              : <span className="whitespace-pre-wrap">{
                  // 兜底: 部分 narrative LLM 偶尔输出 <br>/<br/>, 转成真换行
                  dim.narrative.replace(/<br\s*\/?>/gi, '\n')
                }</span>)
          : <span className="text-slate-400">⏳ AI 暂未生成此段</span>}
      </div>

      {refreshError && (
        <div className="mb-2 rounded-lg border border-amber-100 bg-amber-50 px-3 py-2 text-[11px] text-amber-700">
          {refreshError}
        </div>
      )}

      {/* "为什么把握度是 X" 已按需求隐藏 — confidenceReason 仅作后台调试用 */}

      {dim.dataLayerGap && (
        <div className="text-[11px] text-amber-700 bg-amber-50/60 rounded px-2 py-1 mb-2 inline-flex items-center gap-1">
          <Database size={11} />
          数据中心缺: {dim.dataLayerGap}
        </div>
      )}

      {dim.openClarifications.length > 0 && (
        <div className="mt-2 mb-2 space-y-1">
          {dim.openClarifications.map((q, i) => (
            <button
              key={i}
              type="button"
              onClick={() => {
                setClarifyQuestion(q);
                setClarifyOpen(true);
              }}
              className="block text-left w-full rounded-lg border border-blue-100 bg-blue-50/40 px-3 py-2 text-[12px] text-blue-700 hover:bg-blue-50"
            >
              <span className="inline-flex items-center gap-1.5">
                <MessageCircle size={11} />
                AI 想问: {q}
              </span>
            </button>
          ))}
        </div>
      )}

      {/* 引用源折叠 */}
      {dim.references.length > 0 && (
        <button
          type="button"
          onClick={() => setRefsOpen((v) => !v)}
          className="text-[11px] text-slate-500 hover:text-slate-700 inline-flex items-center gap-1"
        >
          <GitBranch size={11} />
          {dim.references.length} 条引用源
          <ChevronDown size={11} className={`transition-transform ${refsOpen ? 'rotate-180' : ''}`} />
        </button>
      )}
      {refsOpen && dim.references.length > 0 && (
        <ul className="mt-2 space-y-1 text-[11px] text-slate-600">
          {dim.references.map((r, i) => (
            <li key={i} className="flex items-start gap-1.5">
              <ExternalLink size={10} className="text-slate-400 mt-0.5" />
              <span className="min-w-0 flex-1">
                <span className="text-slate-800 font-mono">{r.sourceType}#{r.sourceId}</span>
                {r.label && <span className="text-slate-500"> · {r.label}</span>}
              </span>
              {r.sourceType === 'local_document' && onOpenLocalSource && (
                <button
                  type="button"
                  onClick={() => void onOpenLocalSource(r.sourceId)}
                  className="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold text-blue-700 hover:bg-blue-50"
                >
                  打开原文件
                </button>
              )}
              {r.sourceType === 'official_website' && r.sourceUrl && (
                <a
                  href={r.sourceUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700 hover:bg-emerald-50"
                >
                  打开网页
                </a>
              )}
            </li>
          ))}
        </ul>
      )}

      {/* 历史澄清贡献已迁移到右侧"共同编织"统一日志面板 (AppliedClarificationsCard),
          避免每个维度卡片都重复一份, 修改多了左侧就臃肿. */}

      {/* 澄清输入 */}
      <div className="mt-3 pt-3 border-t border-slate-100">
        {!clarifyOpen ? (
          <button
            type="button"
            onClick={() => setClarifyOpen(true)}
            className="inline-flex items-center gap-1.5 text-[11px] text-blue-700 hover:text-blue-900 font-semibold"
          >
            <Type size={11} />
            这里 AI 理解的对/不对, 我来补充
          </button>
        ) : (
          <div className="space-y-2">
            {clarifyQuestion && (
              <div className="text-[11px] text-slate-500 italic">
                回答: {clarifyQuestion}
              </div>
            )}
            <textarea
              value={clarifyText}
              onChange={(e) => setClarifyText(e.target.value)}
              rows={3}
              placeholder={`你想补充/纠正的内容 (会被 AI 在下次生成时吸纳进 ${meta.label})`}
              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-[12px] resize-none focus:outline-none focus:border-blue-400"
            />
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleSubmit}
                disabled={!clarifyText.trim() || clarifySaving}
                className="rounded-full bg-blue-600 text-white px-3 py-1 text-[11px] font-bold hover:bg-blue-700 disabled:opacity-50"
              >
                {clarifySaving ? '正在更新客户档案…' : '提交补充'}
              </button>
              <button
                type="button"
                onClick={() => {
                  setClarifyOpen(false);
                  setClarifyText('');
                  setClarifyQuestion('');
                }}
                className="text-[11px] text-slate-500 hover:text-slate-700"
              >
                取消
              </button>
            </div>
          </div>
        )}
      </div>
    </article>
  );
}

const KIND_META: Record<NextStepItem['kind'], { label: string; bg: string; text: string }> = {
  meeting:         { label: '会议',   bg: 'bg-slate-100',  text: 'text-slate-700' },
  commitment:      { label: '承诺',   bg: 'bg-blue-100',    text: 'text-blue-700' },
  task:            { label: '任务',   bg: 'bg-amber-100',   text: 'text-amber-700' },
  meeting_action:  { label: '会议待办', bg: 'bg-amber-50', text: 'text-amber-700' },
  event_line:      { label: '主线',   bg: 'bg-violet-100',  text: 'text-violet-700' },
};

function MeetingActionItemsCard({
  clientId,
  onPromote,
  refreshKey,
  onLogChange,
  onRegenerateNarrative,
  narrativeRegenerating,
}: {
  clientId: string;
  onPromote?: (todo: import('../../lib/api').UnifiedTodo) => void;
  refreshKey?: number;
  onLogChange?: () => void;
  /** 触发全部 6 段叙事 + 下一步列表重生成 — 原"全部重生"按钮收纳到这里 */
  onRegenerateNarrative?: () => void;
  /** narrative 全局重生进行中 — disable 按钮 + 旋转动画 */
  narrativeRegenerating?: boolean;
}) {
  const [items, setItems] = useState<NextStepItem[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let alive = true;
    if (!clientId) return;
    setLoading(true);
    getNextSteps(clientId)
      .then((d) => { if (alive) setItems(d.items || []); })
      .catch(() => { if (alive) setItems([]); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [clientId, refreshKey]);

  const writeLog = async (it: NextStepItem, action: SuggestionAction) => {
    try {
      await logSuggestionAction(clientId, {
        fingerprint: it.fingerprint,
        action,
        actor: it.actor,
        suggestionText: it.text,
        sourceDocTitle: '',
        sourceDocId: '',
      });
      // 老 dismiss endpoint 兼容: commitment/task/action 类同时落 status=cancelled/done
      // (这样 commitments 表里的旧建议不会通过 unified-todos 老路径再次回潮)
      if (it.rawId && (it.kind === 'commitment' || it.kind === 'task' || it.kind === 'meeting_action')) {
        try {
          const legacyAction = action === 'completed' ? 'complete' : 'cancel';
          await dismissUnifiedTodo(clientId, it.rawId, legacyAction);
        } catch { /* legacy endpoint 失败不阻塞 */ }
      }
    } catch { /* suggestion_log 写失败也不阻塞 UI */ }
    setItems((prev) => prev.filter((x) => x.fingerprint !== it.fingerprint));
    onLogChange?.();
  };

  const handlePromote = async (it: NextStepItem) => {
    if (!onPromote) return;
    const firstActor = (it.actor || '').split(',')[0]?.trim() || '';
    // 结构化兜底描述: 同步可得, 不依赖网络。即使背景超时也保证弹窗有富信息预填(承诺方向/负责人/截止/关联任务)。
    const dirLabel: Record<string, string> = {
      do: '我方做', follow_up: '催客户', wait_for: '等客户给', confirm: '双方确认',
    };
    const structuredLines: string[] = [];
    if (it.actionDirection && dirLabel[it.actionDirection]) structuredLines.push(`方向: ${dirLabel[it.actionDirection]}`);
    if (firstActor) structuredLines.push(`负责人: ${firstActor}`);
    if (it.dueDate) structuredLines.push(`截止: ${it.dueDate}`);
    if (it.matchedTaskTitle) structuredLines.push(`关联已有任务: ${it.matchedTaskTitle}`);
    const structuredDesc = structuredLines.join('\n');

    // 拿 LLM 背景说明 (cache 命中瞬时)。关键: 用有界竞速 2.5s, 杜绝无界 hang——
    // 否则后端 cache miss 同步走云端/LLM 慢或抖动时, await 永不返回 → 弹窗永远开不出来。
    let description = it.description || '';
    let sourceLabel = '';
    try {
      const bgPromise = getNextStepBackground(clientId, {
        fingerprint: it.fingerprint,
        kind: it.kind,
        actor: it.actor,
        text: it.text,
      });
      bgPromise.catch(() => undefined); // 落败方挂 catch, 防超时后真请求再 reject 造成 unhandledRejection
      const timeoutPromise = new Promise<null>((resolve) => { setTimeout(() => resolve(null), 2500); });
      const bg = await Promise.race([bgPromise, timeoutPromise]);
      if (bg) {
        description = bg.background || '';
        sourceLabel = bg.sourceLabel || '';
      }
    } catch { /* 失败也继续, 用结构化兜底 */ }
    if (description && sourceLabel) {
      description = `${description}\n\n— 来源: 《${sourceLabel}》`;
    }
    if (!description) description = structuredDesc; // 背景超时/为空 → 结构化兜底, 弹窗不会空
    const fakeTodo: import('../../lib/api').UnifiedTodo = {
      id: it.rawId || `meeting:${it.fingerprint}`,
      source: it.kind === 'meeting' ? 'meeting_action' : (it.kind as 'task' | 'commitment' | 'meeting_action'),
      title: it.text,
      owner: firstActor,
      due_date: it.dueDate || '',
      status: 'pending',
      direction: '下一步',
      related_to: it.matchedTaskTitle || '',
      raw_id: it.rawId,
      severity: it.severity,
      description,
    };
    onPromote(fakeTodo);
    await writeLog(it, 'promoted');
  };

  if (loading) {
    return (
      <section className="rounded-2xl border border-slate-100 bg-slate-50/40 px-4 py-3">
        <div className="text-[11px] text-slate-400">加载下一步...</div>
      </section>
    );
  }
  if (items.length === 0) {
    return (
      <section className="rounded-2xl border border-slate-100 bg-slate-50/60 px-4 py-3">
        <div className="flex items-center gap-2 mb-1">
          <ClipboardCheck size={13} className="text-slate-600" />
          <h3 className="text-[12px] font-bold text-slate-800">下一步要做什么</h3>
          <div className="flex-1" />
          {onRegenerateNarrative && (
            <button
              type="button"
              onClick={onRegenerateNarrative}
              disabled={narrativeRegenerating}
              title="重新生成全部 6 段叙事 (重生后下一步列表也会更新)"
              aria-label="重新生成全部 6 段叙事"
              className="inline-flex items-center justify-center w-6 h-6 rounded-full text-slate-600 hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <RefreshCw size={12} className={narrativeRegenerating ? 'animate-spin' : ''} />
            </button>
          )}
        </div>
        <div className="text-[11px] text-slate-500">
          暂无新的建议 — 上传新会议纪要或点下方"推荐历史"找回。
        </div>
      </section>
    );
  }

  return (
    <section className="rounded-2xl border border-slate-100 bg-slate-50/60 px-4 py-3">
      <div className="flex items-center gap-2 mb-2">
        <ClipboardCheck size={13} className="text-slate-600" />
        <h3 className="text-[12px] font-bold text-slate-800">下一步要做什么</h3>
        <span className="text-[10px] text-slate-700 bg-slate-100 rounded-full px-2 py-0.5 font-bold">
          {items.length}
        </span>
        <div className="flex-1" />
        {onRegenerateNarrative && (
          <button
            type="button"
            onClick={onRegenerateNarrative}
            disabled={narrativeRegenerating}
            title="重新生成全部 6 段叙事 (重生后下一步列表也会更新)"
            aria-label="重新生成全部 6 段叙事"
            className="inline-flex items-center justify-center w-6 h-6 rounded-full text-slate-600 hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            <RefreshCw size={12} className={narrativeRegenerating ? 'animate-spin' : ''} />
          </button>
        )}
      </div>
      <div className="space-y-1.5 max-h-[460px] overflow-y-auto pr-0.5">
        {items.map((it) => (
          <ActionRow
            key={it.fingerprint}
            item={it}
            onPromote={onPromote ? () => handlePromote(it) : undefined}
            onComplete={() => writeLog(it, 'completed')}
            onDismiss={() => writeLog(it, 'dismissed')}
          />
        ))}
      </div>
    </section>
  );
}

function ActionRow({
  item,
  onPromote,
  onComplete,
  onDismiss,
}: {
  item: NextStepItem;
  onPromote?: () => void;
  onComplete?: () => void;
  onDismiss?: () => void;
}) {
  const meta = KIND_META[item.kind] ?? KIND_META.meeting;
  return (
    <div className="rounded-xl border border-slate-100 bg-white px-3 py-2.5 hover:border-slate-200 transition-colors">
      {/* 第一行: 左 kind chip + 行动方向 + 右 三按钮 */}
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <div className="flex items-center gap-1 min-w-0">
          <span className={`text-[10px] font-bold ${meta.text} ${meta.bg} rounded px-1.5 py-0.5 shrink-0`}>
            {meta.label}
          </span>
          {item.confirmationState === 'pending_confirmation' && (
            <span className="text-[10px] font-bold text-amber-700 bg-amber-50 rounded px-1.5 py-0.5 shrink-0">待确认</span>
          )}
          {(() => {
            const dirMeta: Record<string, { label: string; cls: string }> = {
              do: { label: '我方做', cls: 'text-indigo-700 bg-indigo-50' },
              follow_up: { label: '催客户', cls: 'text-amber-700 bg-amber-50' },
              wait_for: { label: '等客户给', cls: 'text-amber-700 bg-amber-50' },
              confirm: { label: '双方确认', cls: 'text-violet-700 bg-violet-50' },
            };
            const d = item.actionDirection ? dirMeta[item.actionDirection] : undefined;
            return d ? <span className={`text-[10px] font-bold ${d.cls} rounded px-1.5 py-0.5 shrink-0`}>{d.label}</span> : null;
          })()}
          {item.mergedCount ? (
            <span className="text-[10px] text-slate-400 shrink-0" title={`合并了 ${item.mergedCount} 条改写重复`}>·合并{item.mergedCount}</span>
          ) : null}
        </div>
        <div className="flex items-center gap-1">
          {onPromote && (
            <button
              type="button"
              onClick={onPromote}
              title="制定任务"
              className="inline-flex items-center justify-center w-6 h-6 rounded-full text-slate-600 hover:bg-slate-100 hover:text-slate-800"
            >
              <ArrowRight size={13} />
            </button>
          )}
          {onComplete && (
            <button
              type="button"
              onClick={onComplete}
              title="已完成"
              className="inline-flex items-center justify-center w-6 h-6 rounded-full text-emerald-600 hover:bg-emerald-100"
            >
              <CheckCircle size={12} />
            </button>
          )}
          {onDismiss && (
            <button
              type="button"
              onClick={onDismiss}
              title="删除"
              className="inline-flex items-center justify-center w-6 h-6 rounded-full text-slate-400 hover:bg-rose-100 hover:text-rose-600"
            >
              <X size={13} />
            </button>
          )}
        </div>
      </div>

      {/* 第二行: 内容 */}
      <p className="text-[13px] text-slate-800 leading-snug mb-2">{item.text}</p>

      {/* 第三行: @人 · 截止日期 (强调) */}
      <div className="flex items-center gap-2 text-[11px]">
        <span className="font-bold text-slate-700">
          @{item.actor || '未指定'}
        </span>
        {item.dueDate && (
          <>
            <span className="text-slate-300">·</span>
            <span className="font-bold text-rose-600">
              截止 {item.dueDate}
            </span>
          </>
        )}
      </div>
    </div>
  );
}

function SuggestionLogCard({
  clientId,
  refreshKey,
  onChange,
}: {
  clientId: string;
  refreshKey: number;
  onChange: () => void;
}) {
  const [log, setLog] = useState<{ promoted: SuggestionLogEntry[]; completed: SuggestionLogEntry[]; dismissed: SuggestionLogEntry[] }>(
    { promoted: [], completed: [], dismissed: [] },
  );
  const [tab, setTab] = useState<SuggestionAction>('dismissed');
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!clientId) return;
    let alive = true;
    getSuggestionLog(clientId).then((d) => {
      if (!alive) return;
      setLog({ promoted: d.promoted || [], completed: d.completed || [], dismissed: d.dismissed || [] });
    }).catch(() => {});
    return () => { alive = false; };
  }, [clientId, refreshKey]);

  const total = log.promoted.length + log.completed.length + log.dismissed.length;
  if (total === 0) return null;

  const restore = async (entry: SuggestionLogEntry) => {
    try {
      await removeSuggestionLogEntry(clientId, entry.fingerprint);
      setLog((prev) => ({
        promoted: prev.promoted.filter((e) => e.fingerprint !== entry.fingerprint),
        completed: prev.completed.filter((e) => e.fingerprint !== entry.fingerprint),
        dismissed: prev.dismissed.filter((e) => e.fingerprint !== entry.fingerprint),
      }));
      onChange();
    } catch {}
  };

  const current = log[tab];

  return (
    <section className="rounded-2xl border border-slate-200 bg-white">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full px-4 py-2.5 flex items-center gap-2 text-left hover:bg-slate-50 rounded-2xl"
      >
        <History size={13} className="text-slate-500" />
        <h3 className="text-[12px] font-bold text-slate-700">推荐历史</h3>
        <span className="text-[10px] text-slate-500 ml-1">
          已分配 {log.promoted.length} · 已完成 {log.completed.length} · 已删除 {log.dismissed.length}
        </span>
        <ChevronDown size={13} className={`ml-auto text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="px-4 pb-3 border-t border-slate-100">
          <div className="flex items-center gap-1 my-2">
            {(['dismissed', 'promoted', 'completed'] as const).map((k) => {
              const label = k === 'dismissed' ? '已删除' : k === 'promoted' ? '已分配' : '已完成';
              const isActive = tab === k;
              return (
                <button
                  key={k}
                  type="button"
                  onClick={() => setTab(k)}
                  className={`text-[11px] px-2.5 py-1 rounded-full font-bold ${
                    isActive ? 'bg-slate-800 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                  }`}
                >
                  {label} {log[k].length}
                </button>
              );
            })}
          </div>
          {current.length === 0 ? (
            <div className="text-[11px] text-slate-400 py-2">这一类还没有记录</div>
          ) : (
            <div className="space-y-1 max-h-[260px] overflow-y-auto">
              {current.map((e) => (
                <div key={e.fingerprint} className="rounded-lg border border-slate-100 px-2.5 py-1.5 group">
                  <div className="flex items-start gap-1.5">
                    <span className="text-[9px] font-bold text-slate-600 bg-slate-100 rounded px-1 py-0.5 shrink-0">
                      @{e.actor || '?'}
                    </span>
                    <p className="text-[11px] text-slate-700 leading-snug flex-1 min-w-0">{e.suggestionText}</p>
                    <button
                      type="button"
                      onClick={() => restore(e)}
                      title="找回 — 让这条重新出现在'下一步要做什么'"
                      className="shrink-0 text-[10px] text-blue-600 hover:text-blue-800 opacity-0 group-hover:opacity-100"
                    >
                      找回
                    </button>
                  </div>
                  <div className="text-[9px] text-slate-400 mt-0.5 truncate">
                    {e.sourceDocTitle} · {(e.createdAt || '').slice(0, 16)}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function PendingClarificationsCard({ pending }: { pending: NarrativeClarification[] }) {
  return (
    <section className="rounded-2xl border border-amber-100 bg-amber-50/40 px-4 py-3">
      <div className="flex items-center gap-2 mb-2">
        <MessageCircle size={13} className="text-amber-600" />
        <h3 className="text-[12px] font-bold text-amber-800">待应用澄清</h3>
        <span className="text-[10px] text-amber-700 bg-amber-100 rounded-full px-2 py-0.5 font-bold">
          {pending.length}
        </span>
      </div>
      {pending.length === 0 ? (
        <div className="text-[11px] text-slate-500">还没有等待应用的澄清。</div>
      ) : (
        <div className="space-y-2">
          {pending.map((c) => (
            <div key={c.id} className="rounded-lg bg-white border border-amber-100 px-3 py-2">
              <div className="flex items-center justify-between gap-2 mb-1">
                <span className="text-[10px] font-bold text-slate-700">
                  {DIMENSION_META[c.dimension]?.label || c.dimension}
                </span>
                <span className="text-[10px] text-slate-400">{c.answeredAt.split('T')[0]}</span>
              </div>
              <div className="text-[11px] text-slate-700 leading-[1.6]">{c.answer}</div>
              <div className="text-[10px] text-slate-400 mt-1">— {c.answeredByDisplayName}</div>
            </div>
          ))}
          <div className="text-[10px] text-amber-700 mt-1">
            点上方"重新生成"让 AI 吸纳这些澄清, 更新故事网。
          </div>
        </div>
      )}
    </section>
  );
}

function AppliedClarificationsCard({ applied }: { applied: NarrativeClarification[] }) {
  if (applied.length === 0) return null;
  // 5 行高度的可滚动日志, 看谁填了什么, 像活动 log 一样. 全部内容可滚出来,
  // 不再硬截断到前 8 条 (避免遮蔽久远贡献).
  return (
    <section className="rounded-2xl border border-slate-100 bg-white px-4 py-3">
      <div className="flex items-center gap-2 mb-2">
        <Users size={13} className="text-slate-500" />
        <h3 className="text-[12px] font-bold text-slate-700">共同编织</h3>
        <span className="text-[10px] text-slate-500 ml-auto">{applied.length} 条</span>
      </div>
      {applied.length === 0 ? (
        <div className="text-[11px] text-slate-400 py-2">还没有人补充澄清</div>
      ) : (
        <ul className="space-y-1.5 max-h-[200px] overflow-y-auto pr-1">
          {applied.map((c) => (
            <li key={c.id} className="text-[11px] text-slate-600 leading-[1.55] border-b border-slate-50 last:border-b-0 pb-1.5 last:pb-0">
              <div className="flex items-center gap-1.5">
                <span className="font-semibold text-slate-700">{c.answeredByDisplayName}</span>
                <span className="text-slate-400">·</span>
                <span className="text-slate-400 text-[10px]">{c.answeredAt.split('T')[0]}</span>
                <span className="text-slate-400">·</span>
                <span className="text-slate-500 text-[10px]">{DIMENSION_META[c.dimension]?.label}</span>
              </div>
              <div className="text-slate-500 mt-0.5">{c.answer.slice(0, 120)}{c.answer.length > 120 ? '...' : ''}</div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}


// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 客户选择器
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

function ClientPicker({
  clientOptions,
  selectedClientId,
  onClientChange,
}: {
  clientOptions: Array<{ id: string; name: string }>;
  selectedClientId: string;
  onClientChange: (id: string) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[11px] font-bold text-slate-400">客户</span>
      <select
        value={selectedClientId}
        onChange={(e) => onClientChange(e.target.value)}
        className="rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-[13px] font-medium text-slate-700 focus:outline-none focus:border-blue-300"
      >
        <option value="">选择...</option>
        {clientOptions.map((c) => (
          <option key={c.id} value={c.id}>{c.name}</option>
        ))}
      </select>
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50/60 px-4 py-12 text-center text-[13px] text-slate-400">
      {text}
    </div>
  );
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 通用区块外壳
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

interface BlockShellProps {
  icon: typeof Briefcase;
  title: string;
  confidence: Confidence;
  hint?: string;
  children: React.ReactNode;
}

function BlockShell({ icon: Icon, title, confidence, hint, children }: BlockShellProps) {
  const meta = CONFIDENCE_META[confidence];
  const MetaIcon = meta.icon;
  return (
    <section className="rounded-[20px] border border-slate-100 bg-white px-5 py-4">
      <header className="flex items-center justify-between gap-3 mb-3">
        <div className="flex items-center gap-2">
          <Icon size={15} className="text-slate-500" />
          <h3 className="text-[14px] font-bold text-slate-800">{title}</h3>
        </div>
        <div className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold ${meta.bg} ${meta.text}`}>
          <MetaIcon size={10} />
          {meta.label}
        </div>
      </header>
      {hint && <p className="text-[11px] text-slate-400 mb-3 leading-[1.6]">{hint}</p>}
      <div>{children}</div>
    </section>
  );
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 区块 1 · 项目骨架
// 数据源: event_lines 表 (kind/stage/status/business_category/owner_name)
// 占位字段: 起止时间窗 / 交付物 / 验收标准 → 等 Phase 1 加 committed_at / expected_completion_at / deliverable_spec_json
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

function ProjectSkeletonBlock({
  eventLines,
  profile,
}: {
  eventLines: ClarificationEventLine[];
  profile: ClarificationProfile;
}) {
  const active = eventLines.filter((el) => !el.closedAt);
  const closed = eventLines.filter((el) => !!el.closedAt);
  const confidence: Confidence = active.length > 0 ? 'medium' : eventLines.length > 0 ? 'low' : 'low';

  return (
    <BlockShell
      icon={Briefcase}
      title="项目骨架"
      confidence={confidence}
      hint={`从 event_lines 表读取 ${eventLines.length} 条业务主线。`}
    >
      {eventLines.length === 0 ? (
        <PlaceholderRow text="这个客户还没建任何业务主线" />
      ) : (
        <div className="space-y-2">
          {active.map((el) => <EventLineRow key={el.id} el={el} />)}
          {closed.length > 0 && (
            <details className="mt-2">
              <summary className="text-[11px] font-bold text-slate-400 cursor-pointer hover:text-slate-600">
                已关闭 / 历史 ({closed.length})
              </summary>
              <div className="mt-2 space-y-2">
                {closed.map((el) => <EventLineRow key={el.id} el={el} grayed />)}
              </div>
            </details>
          )}
        </div>
      )}

      {/* 占位字段 - 等 Phase 1 升级 */}
      <div className="mt-4 pt-3 border-t border-dashed border-slate-200">
        <div className="text-[10px] font-bold text-slate-400 mb-2 uppercase tracking-wider">等 Phase 1 升级补充</div>
        <div className="grid grid-cols-2 gap-2 text-[11px] text-slate-400">
          <PlaceholderField label="起止时间窗" hint="字段 committed_at / expected_completion_at" />
          <PlaceholderField label="交付物 + 验收标准" hint="字段 deliverable_spec_json" />
          <PlaceholderField label="项目类型 (5 颗粒度)" hint="字段 thread_level" />
          <PlaceholderField label="父子嵌套关系" hint="字段 parent_thread_id" />
        </div>
      </div>
    </BlockShell>
  );
}

function EventLineRow({ el, grayed = false }: { el: ClarificationEventLine; grayed?: boolean }) {
  const statusMeta =
    el.closedAt ? { bg: 'bg-slate-100', text: 'text-slate-500', label: '已关闭' }
      : el.status === 'active' ? { bg: 'bg-emerald-50', text: 'text-emerald-700', label: '进行中' }
        : { bg: 'bg-amber-50', text: 'text-amber-700', label: el.status || '未知' };
  return (
    <div className={`rounded-xl border border-slate-100 px-3 py-2 ${grayed ? 'opacity-60' : ''}`}>
      <div className="flex items-start justify-between gap-2 mb-1">
        <div className="text-[13px] font-semibold text-slate-800 line-clamp-2 flex-1">
          {el.isDirtyName ? <span className="text-rose-500">(脏数据 - name=id)</span> : el.name}
        </div>
        <span className={`shrink-0 inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-bold ${statusMeta.bg} ${statusMeta.text}`}>
          {statusMeta.label}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] font-bold text-slate-400">
        {el.stage && <span>{el.stage}</span>}
        {el.ownerName && <span>· {el.ownerName}</span>}
        {el.businessCategory && <span>· {el.businessCategory}</span>}
        {el.evidenceCount > 0 && <span>· {el.evidenceCount} 证据</span>}
      </div>
    </div>
  );
}

function PlaceholderRow({ text }: { text: string }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50/40 px-3 py-3 text-[12px] text-slate-400 text-center">
      {text}
    </div>
  );
}

function PlaceholderField({ label, hint }: { label: string; hint: string }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50/30 px-2 py-1.5">
      <div className="font-semibold text-slate-500">{label}</div>
      <div className="text-[9px] text-slate-300 mt-0.5">{hint}</div>
    </div>
  );
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 区块 2 · 关键人物
// 数据源: event_lines.owner_name + activities.actor_name + tasks.owner_name + action_items.owner_name (粗糙归并)
// 占位: 别名归一 / 当前状态 / 在项目中的角色 / 健康信号 → 等 Phase 1 花名册 external_persons
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

function KeyPeopleBlock({ people }: { people: ClarificationPerson[] }) {
  const confidence: Confidence = people.length > 0 ? 'low' : 'low';
  return (
    <BlockShell
      icon={Users}
      title={`关键人物 (${people.length} 人候选)`}
      confidence={confidence}
      hint="从 task/event_line/activity 的 owner_name + actor_name 提取的人名候选。等 Phase 1 花名册建好后, 自动归一别名 + 显示角色 + 状态。"
    >
      {people.length === 0 ? (
        <PlaceholderRow text="数据库里没有提取到任何人名 (检查 owner_name / actor_name 字段是否有值)" />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {people.map((p) => <PersonCard key={p.name} person={p} />)}
        </div>
      )}

      <div className="mt-4 pt-3 border-t border-dashed border-slate-200">
        <div className="text-[10px] font-bold text-slate-400 mb-2 uppercase tracking-wider">等 Phase 1 花名册升级</div>
        <div className="grid grid-cols-2 gap-2 text-[11px] text-slate-400">
          <PlaceholderField label="别名归一" hint="例: 强哥=王强=王老师" />
          <PlaceholderField label="在职/离职/休假" hint="字段 employment_status + status_changed_at" />
          <PlaceholderField label="在这个项目里的角色" hint="字段 roles_json (含 since 时间戳)" />
          <PlaceholderField label="健康/工作风格信号" hint="字段 health_signal_json + work_style_traits_json" />
        </div>
      </div>
    </BlockShell>
  );
}

function PersonCard({ person }: { person: ClarificationPerson }) {
  return (
    <div className="rounded-xl border border-slate-100 px-3 py-2">
      <div className="flex items-baseline justify-between gap-2 mb-1">
        <div className="text-[13px] font-semibold text-slate-800">{person.name}</div>
        <span className="text-[10px] font-bold text-slate-400 tabular-nums">{person.mentionCount}×</span>
      </div>
      <div className="flex flex-wrap gap-1 text-[10px]">
        {person.sources.map((s) => (
          <span key={s} className="inline-block rounded bg-slate-100 px-1.5 py-0.5 text-slate-500 font-medium">
            {SOURCE_LABEL[s] || s}
          </span>
        ))}
      </div>
    </div>
  );
}

const SOURCE_LABEL: Record<string, string> = {
  event_line_owner: '主线负责人',
  activity_actor: '事件参与者',
  task_owner: '任务负责人',
  action_item_owner: '承诺负责人',
};

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 区块 3 · 承诺链
// 数据源: action_items + meetings (当前数据极少, 全库 4 条 action_items)
// 占位: 承诺人/被承诺人/承诺日 → 等 Phase 1 承诺级 thread 升级
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

function CommitmentChainBlock({ commitments }: { commitments: ClarificationCommitment[] }) {
  const confidence: Confidence = commitments.length === 0 ? 'low' : 'low';
  return (
    <BlockShell
      icon={Target}
      title={`承诺链 (${commitments.length} 条)`}
      confidence={confidence}
      hint="承诺表 (LLM 从对话/资料抽取) + 会议待办 action_items 合并。pending=未履约 / fulfilled=已交付 / cancelled=作废。"
    >
      {commitments.length === 0 ? (
        <PlaceholderRow text="还没有从会议里抽出来的承诺 (action_items 数据为空)" />
      ) : (
        <div className="space-y-1.5">
          {commitments.map((c) => <CommitmentRow key={c.id} commitment={c} />)}
        </div>
      )}

      <div className="mt-4 pt-3 border-t border-dashed border-slate-200">
        <div className="text-[10px] font-bold text-slate-400 mb-2 uppercase tracking-wider">等 Phase 1 承诺级 thread 升级</div>
        <div className="grid grid-cols-2 gap-2 text-[11px] text-slate-400">
          <PlaceholderField label="谁向谁承诺" hint="action_items 当前没有'被承诺人'字段" />
          <PlaceholderField label="承诺日期 ≠ 截止日期" hint="字段 committed_at" />
          <PlaceholderField label="是否有人承接" hint="承诺-承接对应链" />
          <PlaceholderField label="承诺履约状态" hint="advance/neutral/block 业务语义" />
        </div>
      </div>
    </BlockShell>
  );
}

function CommitmentRow({ commitment }: { commitment: ClarificationCommitment }) {
  return (
    <div className="rounded-xl border border-slate-100 px-3 py-2">
      <div className="text-[12px] font-semibold text-slate-800 mb-1 line-clamp-2">{commitment.title}</div>
      <div className="flex items-center gap-2 text-[10px] font-bold text-slate-400">
        {commitment.ownerName && <span>{commitment.ownerName}</span>}
        {commitment.dueDate && <span>· 截止 {commitment.dueDate}</span>}
        {commitment.meetingTitle && <span>· {commitment.meetingTitle}</span>}
      </div>
    </div>
  );
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 区块 4 · 来龙去脉 (时间线) — 当前可用度最高
// 数据源: event_line_activities (合并所有该客户主线的 activities)
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

function TimelineBlock({ items }: { items: ClarificationTimelineItem[] }) {
  const confidence: Confidence = items.length >= 5 ? 'medium' : items.length > 0 ? 'low' : 'low';
  return (
    <BlockShell
      icon={Clock}
      title={`来龙去脉 (${items.length} 条事件)`}
      confidence={confidence}
      hint="从 event_line_activities 表读取所有主线的事件 (已过滤测试附件)。等 Phase 2 加业务语义标签 (推进/中性/阻塞) + 影响传播。"
    >
      {items.length === 0 ? (
        <PlaceholderRow text="还没有事件记录" />
      ) : (
        <div className="relative pl-5 max-h-[400px] overflow-y-auto">
          <div className="absolute left-1.5 top-2 bottom-2 w-px bg-slate-200" />
          <div className="space-y-3">
            {items.map((item) => <TimelineItem key={item.id} item={item} />)}
          </div>
        </div>
      )}
    </BlockShell>
  );
}

function TimelineItem({ item }: { item: ClarificationTimelineItem }) {
  const dateLabel = (item.happenedAt || '').slice(0, 10);
  return (
    <div className="relative">
      <div className={`absolute -left-[18px] top-1 h-2.5 w-2.5 rounded-full ${item.isKey ? 'bg-blue-500' : 'bg-slate-300'} ring-2 ring-white`} />
      <div className="text-[10px] font-bold text-slate-400 mb-0.5">
        {dateLabel}
        {item.sourceType && <span className="ml-1.5 text-slate-300">· {item.sourceType}</span>}
        {item.actorName && <span className="ml-1.5">· {item.actorName}</span>}
      </div>
      <div className="text-[12px] text-slate-700 line-clamp-2">{item.title}</div>
      {item.eventLineName && (
        <div className="text-[10px] text-slate-400 mt-0.5">所属主线: {item.eventLineName}</div>
      )}
    </div>
  );
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 区块 5 · 业务意义
// 数据源: clients + client_strategic_profiles (后者大多空, 等 Phase 2 LLM 反推填充)
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

function BusinessMeaningBlock({
  profile,
  eventLines,
}: {
  profile: ClarificationProfile;
  eventLines: ClarificationEventLine[];
}) {
  const filledFields = [profile.industry, profile.scale, profile.currentNeeds, profile.strategicValueToYiyu]
    .filter((v) => v.trim().length > 0).length;
  const confidence: Confidence = filledFields >= 3 ? 'medium' : filledFields > 0 ? 'low' : 'low';
  return (
    <BlockShell
      icon={Sparkles}
      title="业务意义"
      confidence={confidence}
      hint={`从 clients + client_strategic_profiles 表读取。当前已填 ${filledFields}/7 字段, 等 Phase 2 加 LLM 从 memory_facts 反推填充。`}
    >
      <div className="space-y-2">
        <FieldRow label="行业 / 领域" value={profile.industry || profile.domain} />
        <FieldRow label="服务对象 / 当前需求" value={profile.currentNeeds} />
        <FieldRow label="痛点" value={profile.painPoints} />
        <FieldRow label="对益语的战略价值" value={profile.strategicValueToYiyu} />
        <FieldRow label="决策链" value={profile.decisionChain} />
        <FieldRow label="合作关系" value={profile.cooperationType ? `${profile.cooperationType} · 健康度: ${profile.relationshipHealth}` : ''} />
        <FieldRow label="阶段" value={profile.stage} />
      </div>

      <div className="mt-4 pt-3 border-t border-dashed border-slate-200">
        <div className="text-[10px] font-bold text-slate-400 mb-2 uppercase tracking-wider">等 Phase 1/2 字段升级</div>
        <div className="grid grid-cols-2 gap-2 text-[11px] text-slate-400">
          <PlaceholderField label="治理结构" hint="governance_mode + governance_structure_json" />
          <PlaceholderField label="业务线占比" hint="business_lines_json (含占比 + 手法)" />
          <PlaceholderField label="资助方 / 合作方关系" hint="funder_relations_json" />
          <PlaceholderField label="核心人员健康信号" hint="key_personnel_health_signals_json" />
        </div>
      </div>
    </BlockShell>
  );
}

function FieldRow({ label, value }: { label: string; value: string }) {
  if (!value.trim()) {
    return (
      <div className="flex items-baseline gap-3 py-1 border-b border-dashed border-slate-100 last:border-0">
        <span className="text-[11px] font-bold text-slate-400 w-24 shrink-0">{label}</span>
        <span className="text-[11px] text-slate-300 italic">(空)</span>
      </div>
    );
  }
  return (
    <div className="flex items-baseline gap-3 py-1 border-b border-dashed border-slate-100 last:border-0">
      <span className="text-[11px] font-bold text-slate-400 w-24 shrink-0">{label}</span>
      <span className="text-[12px] text-slate-700 leading-[1.6] flex-1">{value}</span>
    </div>
  );
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// 右侧 · 澄清队列
// 数据源: event_line_memory_snapshots.clarification_needs_json (当前是字段缺失提示, 不是业务问题)
// 占位: 真正的针对性业务问题需要 Phase 3 加'澄清问题生成引擎'
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

function ClarificationQueue({
  needs,
  eventLines,
  clientName,
}: {
  needs: ClarificationNeed[];
  eventLines: ClarificationEventLine[];
  clientName: string;
}) {
  // 把 missingFields 翻译成业务问题
  const questions = needs.flatMap((need) => {
    return need.missingFields.map((field) => ({
      id: `${need.eventLineId}_${field}`,
      eventLineId: need.eventLineId,
      eventLineName: need.eventLineName,
      field,
      label: FIELD_QUESTION_LABEL[field] || `请补充: ${field}`,
      readiness: need.predictionReadiness,
    }));
  });

  return (
    <section className="rounded-[20px] border border-blue-100 bg-blue-50/30 px-5 py-4 sticky top-4">
      <header className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <MessageCircle size={15} className="text-blue-600" />
          <h3 className="text-[14px] font-bold text-slate-800">AI 想问你</h3>
        </div>
        <span className="text-[11px] font-bold text-blue-700">{questions.length} 个问题</span>
      </header>

      <p className="text-[11px] text-slate-500 leading-[1.6] mb-3">
        以下是 AI 目前还没明白的地方。回答后, 故事网会立刻更新。
        <span className="block mt-1 text-slate-400">当前展示的是字段缺失类基础问题 (来自 event_line_memory_snapshots)。Phase 3 会接 AI 主动生成的针对性业务问题。</span>
      </p>

      {questions.length === 0 ? (
        <div className="rounded-xl border border-dashed border-blue-200 bg-white/60 px-3 py-6 text-center text-[11px] text-slate-400">
          {clientName} 的关系网当前没有明显缺口
        </div>
      ) : (
        <div className="space-y-3">
          {questions.slice(0, 8).map((q) => <QuestionCard key={q.id} question={q} />)}
        </div>
      )}

      <div className="mt-4 pt-3 border-t border-dashed border-blue-200">
        <div className="text-[10px] font-bold text-slate-400 mb-2 uppercase tracking-wider">等 Phase 3 升级</div>
        <ul className="text-[10px] text-slate-400 space-y-1 leading-[1.6]">
          <li>· 引文支撑 (列引用的 evidence/document)</li>
          <li>· 一键打开原文件 (📂 按钮)</li>
          <li>· 语音回答 (🎤 按钮)</li>
          <li>· 答完自动 patch 进关系网 + 显示更新</li>
          <li>· 部门同事共同编织 (多答者追溯)</li>
        </ul>
      </div>
    </section>
  );
}

const FIELD_QUESTION_LABEL: Record<string, string> = {
  current_stage: '这条主线现在到了哪个阶段?',
  current_blocker: '现在卡在什么地方?',
  recent_decision: '最近做出过什么决策?',
  next_step: '下一步要做什么?',
  current_work: '现在具体在做什么?',
};

function QuestionCard({ question }: {
  question: {
    id: string;
    eventLineId: string;
    eventLineName: string;
    field: string;
    label: string;
    readiness: number;
  };
}) {
  return (
    <div className="rounded-xl border border-blue-200/60 bg-white px-3 py-2.5">
      <div className="text-[12px] font-semibold text-slate-800 mb-1.5 leading-[1.5]">
        {question.label}
      </div>
      {question.eventLineName && (
        <div className="text-[10px] text-slate-400 mb-2">
          关于: {question.eventLineName}
        </div>
      )}
      <div className="flex gap-1.5">
        <button
          type="button"
          disabled
          className="flex-1 inline-flex items-center justify-center gap-1 rounded-lg border border-slate-200 bg-slate-50 py-1 text-[10px] font-bold text-slate-400 cursor-not-allowed"
          title="等 Phase 3 上线"
        >
          <Mic size={10} />
          语音
        </button>
        <button
          type="button"
          disabled
          className="flex-1 inline-flex items-center justify-center gap-1 rounded-lg border border-slate-200 bg-slate-50 py-1 text-[10px] font-bold text-slate-400 cursor-not-allowed"
          title="等 Phase 3 上线"
        >
          <Type size={10} />
          打字
        </button>
      </div>
    </div>
  );
}
