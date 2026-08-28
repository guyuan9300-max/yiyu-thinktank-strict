import React, { useEffect, useState, useMemo, useCallback } from 'react';
import {
  readRuntimeUiSessionValue,
  useRuntimeUiSessionState,
  writeRuntimeUiSessionValue,
} from '../../lib/runtimeUiSessionStore';
import {
  ArrowRight,
  BookOpen,
  BrainCircuit,
  Eye,
  Heart,
  Layers3,
  Lightbulb,
  Lock,
  PenTool,
  Rocket,
  Search,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Swords,
  Target,
  Trophy,
  Users,
  X,
  type LucideIcon,
  Flag,
  Briefcase,
  CalendarClock,
  Gauge,
  FileStack,
  CircleDashed,
  Handshake,
  HandHelping,
  Radar,
  Wrench,
  Star,
} from 'lucide-react';

import {
  getGrowthOverview,
  getGrowthExperienceWall,
  getGrowthBadges,
  likeGrowthExperienceWallQuote,
  markHandbookEntryReused,
} from '../../lib/api';
import { useGrowthOverviewState } from '../growth/GrowthContext';
import { ActivityCalendar } from 'react-activity-calendar';
import {
  LineChart,
  Line,
  ResponsiveContainer,
  Tooltip as RTooltip,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
} from 'recharts';
import type {
  GrowthAbilityKey,
  GrowthAbilityScore,
  GrowthAbilityGap,
  GrowthAbilityTrendPoint,
  GrowthDailyActivity,
  GrowthCommitmentSummary,
  GrowthBusinessCoverage,
  GrowthReviewStreak,
  GrowthWorkType,
  GrowthImpact,
  GrowthLearning,
  GrowthLearningPick,
  GrowthPeerComparison,
  GrowthOverview,
  XpLedgerEntry,
  GrowthExperienceWallItem,
  BadgeBoard,
  BadgeProgress,
  BadgeState,
  BadgeCategory,
  BadgeBoardOverview,
} from '../../../shared/types';

const GROWTH_RUNTIME_CACHE_TTL_MS = 5 * 60 * 1000;

/* ══════════════════════════════════════════════════════════════════════
   CSS — injected once, matches growth-center-preview.html exactly
   ──────────────────────────────────────────────────────────────────── */
const GROWTH_CSS = `
.gc-root { height: 100%; display: flex; flex-direction: column; overflow: hidden; background: #fff; font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'PingFang SC', 'Hiragino Sans GB', sans-serif; color: #374151; -webkit-font-smoothing: antialiased; }

/* Header */
.gc-header { background: #fff; border-bottom: 1px solid #F3F4F6; padding: 20px clamp(20px, 2.2vw, 48px) 0; flex-shrink: 0; }
.gc-header-inner { width: 100%; margin: 0 auto; box-sizing: border-box; }
.gc-header-top { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 20px; gap: 16px; }
.gc-page-eyebrow { font-size: 10px; font-weight: 600; color: #9ca3af; letter-spacing: 0.18em; text-transform: uppercase; }
.gc-page-title { font-size: 22px; font-weight: 300; color: #111827; letter-spacing: -0.4px; margin-top: 4px; }
.gc-page-subtitle { font-size: 11.5px; color: #6b7280; font-weight: 400; margin-top: 2px; }
.gc-xp-area { display: flex; align-items: center; gap: 12px; }
.gc-rank-chip { display: inline-flex; align-items: center; gap: 6px; background: transparent; box-shadow: inset 0 0 0 1px rgba(91,123,254,0.30); border-radius: 999px; padding: 4px 12px; font-size: 11px; font-weight: 500; color: #3652c9; letter-spacing: 0.04em; }
.gc-xp-num { font-size: 16px; font-weight: 300; color: #111827; letter-spacing: -0.3px; text-align: right; }
.gc-xp-label { font-size: 10px; color: #9ca3af; font-weight: 500; letter-spacing: 0.12em; text-transform: uppercase; }
.gc-xp-week { font-size: 11px; font-weight: 500; color: #059669; text-align: right; }

/* Tabs — underline style */
.gc-tab-bar { display: inline-flex; gap: 24px; margin-bottom: -1px; }
.gc-tab-btn { background: none; border: none; cursor: pointer; padding: 0 0 12px; font-size: 12px; font-weight: 500; color: #6b7280; transition: color 0.2s; letter-spacing: 0.14em; text-transform: uppercase; position: relative; border-bottom: 2px solid transparent; }
.gc-tab-btn:hover { color: #111827; }
.gc-tab-btn.active { color: #5B7BFE; border-bottom-color: #5B7BFE; }

/* Content */
.gc-content { flex: 1; overflow-y: auto; padding: 20px clamp(20px, 2.2vw, 48px) 40px; }
.gc-content-inner { width: 100%; margin: 0 auto; box-sizing: border-box; }

/* Cards */
.gc-card { background: #fff; border: 1px solid #f3f4f6; border-radius: 16px; }
.gc-card-inner { background: #fff; border: 1px solid #f3f4f6; border-radius: 14px; }
.gc-section-header { display: flex; align-items: flex-end; justify-content: space-between; padding: 0 4px; margin-bottom: 14px; }
.gc-section-title { font-size: 15px; font-weight: 400; color: #111827; letter-spacing: -0.2px; }
.gc-section-hint { font-size: 10px; font-weight: 500; letter-spacing: 0.16em; text-transform: uppercase; color: #9ca3af; }

/* Icon token */
.gc-icon-token { display: flex; align-items: center; justify-content: center; border-radius: 999px; flex-shrink: 0; }
.gc-icon-token.sm { width: 20px; height: 20px; }
.gc-icon-token.md { width: 28px; height: 28px; }
.gc-icon-token.lg { width: 40px; height: 40px; }
.gc-icon-token.xl { width: 56px; height: 56px; }
.gc-icon-token.brand { background: #EEF3FF; }
.gc-icon-token.brand-border { background: #EEF3FF; border: 1px solid #D6E1FF; }
.gc-icon-token.gray { background: #f1f5f9; }

/* Insight cards */
.gc-insight-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }
.gc-insight-card { padding: 20px; }
.gc-insight-quote { font-size: 13px; color: #1e293b; line-height: 1.9; }
.gc-insight-meta { display: flex; align-items: center; justify-content: space-between; margin-top: 12px; }
.gc-insight-author { display: flex; align-items: center; gap: 8px; }
.gc-author-name { font-size: 11px; font-weight: 500; color: #64748b; }
.gc-source-tag { background: #f8fafc; border: 1px solid #f1f5f9; border-radius: 999px; padding: 2px 8px; font-size: 10px; color: #94a3b8; font-weight: 500; letter-spacing: 0.2px; }
.gc-like-btn { display: flex; align-items: center; gap: 4px; background: none; border: none; cursor: pointer; font-size: 11px; color: #D1D5DB; transition: color 0.2s; }
.gc-like-btn:hover { color: #5B7BFE; }
.gc-like-btn.liked { color: #5B7BFE; }

/* AI section */
.gc-ai-section { border-radius: 24px; background: radial-gradient(circle at top left, rgba(51,92,254,0.06), transparent 40%), linear-gradient(180deg, #fff 0%, #fafbff 100%); border: 1px solid #DDE6FF; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.04); margin-top: 24px; }
.gc-ai-chip { display: inline-flex; align-items: center; gap: 6px; border-radius: 999px; border: 1px solid #D6E1FF; background: #fff; padding: 4px 12px; font-size: 12px; font-weight: 500; color: #335CFE; box-shadow: 0 1px 2px rgba(0,0,0,0.04); margin-bottom: 16px; }
.gc-pending-actions { display: flex; gap: 8px; margin-top: 12px; }
.gc-btn-brand { display: inline-flex; align-items: center; gap: 6px; background: #335CFE; color: #fff; border: none; border-radius: 999px; padding: 8px 16px; font-size: 12px; font-weight: 500; cursor: pointer; transition: background 0.2s; }
.gc-btn-brand:hover:not(:disabled) { background: #2C50E0; }
.gc-btn-brand:disabled { opacity: 0.6; cursor: not-allowed; }
@keyframes gc-toast-in { from { opacity: 0; transform: translateX(-50%) translateY(-8px); } to { opacity: 1; transform: translateX(-50%) translateY(0); } }
.gc-btn-ghost { background: none; border: 1px solid #e2e8f0; color: #64748b; border-radius: 999px; padding: 8px 16px; font-size: 12px; font-weight: 500; cursor: pointer; transition: background 0.2s; }
.gc-btn-ghost:hover { background: #f8fafc; }

.gc-progress-track { flex: 1; height: 3px; background: #f1f5f9; border-radius: 999px; overflow: hidden; }
.gc-progress-fill { height: 100%; border-radius: 999px; }

/* XP Overview hero */
.gc-xp-hero { border-radius: 28px; border: 1px solid #DDE6FF; background: radial-gradient(circle at top left, rgba(51,92,254,0.08), transparent 34%), linear-gradient(180deg, #fff 0%, #fafbff 100%); padding: 24px; box-shadow: 0 24px 70px rgba(15,23,42,0.04); }
.gc-xp-hero-top { display: flex; align-items: center; gap: 16px; margin-bottom: 20px; }
.gc-xp-hero-rank { font-size: 16px; font-weight: 600; color: #0f172a; letter-spacing: -0.3px; }
.gc-xp-hero-num { font-size: 24px; font-weight: 600; color: #0f172a; letter-spacing: -0.5px; }
.gc-xp-hero-badge { display: inline-flex; align-items: center; gap: 4px; background: #ecfdf5; border-radius: 999px; padding: 4px 10px; font-size: 11px; font-weight: 600; color: #059669; }
.gc-xp-breakdown { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-top: 20px; }
.gc-xp-break-card { border-radius: 22px; border: 1px solid rgba(255,255,255,0.8); background: rgba(255,255,255,0.88); padding: 16px; box-shadow: 0 18px 40px rgba(148,163,184,0.08); backdrop-filter: blur(8px); }
.gc-xp-break-top { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.gc-xp-break-label { font-size: 10px; font-weight: 500; color: #94a3b8; }
.gc-xp-break-val { font-size: 20px; font-weight: 600; color: #0f172a; letter-spacing: -0.3px; }

/* Badge milestones */
.gc-badge-section { border-radius: 24px; border: 1px solid #eef2ff; background: #fff; padding: 20px; box-shadow: 0 16px 48px rgba(15,23,42,0.035); }
.gc-badge-section + .gc-badge-section { margin-top: 16px; }
.gc-badge-section-head { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
.gc-badge-section-title { font-size: 14px; font-weight: 600; color: #0f172a; letter-spacing: -0.2px; }
.gc-badge-section-hint { font-size: 11px; font-weight: 500; color: #94a3b8; text-align: right; }
.gc-badge-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(118px, 1fr)); gap: 10px; }
.gc-badge-cell { min-width: 0; display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 12px 8px; cursor: pointer; transition: transform 0.2s, background 0.2s; background: none; border: none; border-radius: 18px; }
.gc-badge-cell:hover { transform: translateY(-2px); background: rgba(248,250,252,0.86); }
.gc-badge-name { font-size: 11px; font-weight: 500; text-align: center; line-height: 1.35; max-width: 96px; min-height: 30px; display: flex; align-items: center; justify-content: center; }
.gc-badge-name.lit { color: #334155; }
.gc-badge-name.prog { color: #64748b; }
.gc-badge-name.lock { color: #94a3b8; }
.gc-badge-sub { font-size: 10px; font-weight: 600; }
.gc-badge-status { display: inline-flex; align-items: center; border-radius: 999px; padding: 2px 8px; font-size: 9px; font-weight: 600; }
.gc-badge-status.lit { background: #ecfdf5; color: #059669; }
.gc-badge-status.ready { background: rgba(91,123,254,0.1); color: #335CFE; }
.gc-badge-status.progress { background: rgba(91,123,254,0.08); color: #5B7BFE; }
.gc-badge-status.locked { background: #f1f5f9; color: #94a3b8; }
.gc-badge-category-list { display: flex; flex-direction: column; gap: 14px; }
.gc-badge-category-card { border: 1px solid #edf2f7; border-radius: 22px; background: #fff; padding: 18px; }
.gc-badge-category-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 12px; }
.gc-badge-category-title { display: flex; align-items: center; gap: 8px; min-width: 0; }
.gc-badge-category-name { font-size: 13px; font-weight: 600; color: #0f172a; letter-spacing: -0.2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.gc-badge-category-meta { font-size: 11px; font-weight: 500; color: #94a3b8; flex-shrink: 0; }
.gc-badge-category-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(108px, 1fr)); gap: 8px; }

/* Personal rank */
.gc-rank-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; padding: 0 4px; }
.gc-rank-header-left { display: flex; align-items: center; gap: 8px; }
.gc-rank-list { padding: 18px; display: flex; flex-direction: column; gap: 14px; }
.gc-rank-row { display: flex; align-items: center; gap: 14px; padding: 16px; border-radius: 18px; background: rgba(248,250,252,0.72); }
.gc-rank-name-text { font-size: 14px; font-weight: 600; color: #0f172a; letter-spacing: -0.2px; }
.gc-rank-meta { font-size: 11px; font-weight: 500; color: #94a3b8; margin-top: 3px; }
.gc-rank-xp { font-size: 13px; font-weight: 600; color: #335CFE; text-align: right; white-space: nowrap; flex-shrink: 0; }
.gc-rank-bar { height: 4px; background: #f1f5f9; border-radius: 999px; overflow: hidden; }
.gc-rank-bar-fill { height: 100%; border-radius: 999px; background: #5B7BFE; }
.gc-rank-week { display: flex; align-items: center; justify-content: space-between; gap: 12px; border-radius: 18px; background: rgba(236,253,245,0.72); border: 1px solid #d1fae5; padding: 14px 16px; font-size: 12px; font-weight: 500; color: #047857; }
.gc-modal-subtitle { font-size: 11px; font-weight: 600; letter-spacing: 1.2px; color: #94a3b8; margin-bottom: 8px; }
.gc-modal-link-list { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.gc-modal-link-pill { border-radius: 999px; border: 1px solid #dbeafe; background: #eff6ff; padding: 4px 10px; font-size: 10px; font-weight: 600; color: #335CFE; }

.gc-space-y > * + * { margin-top: 24px; }

/* Ability growth tab */
.gc-radar-card { display: flex; flex-direction: column; align-items: stretch; gap: 28px; padding: 32px 36px; }
@media (min-width: 900px) { .gc-radar-card { flex-direction: row; align-items: center; gap: 56px; } }
.gc-radar-visual { display: flex; flex-direction: column; align-items: center; flex-shrink: 0; }
.gc-radar-visual svg { max-width: 100%; height: auto; }
@media (min-width: 900px) { .gc-radar-visual { width: 380px; } }
.gc-radar-legend { display: flex; align-items: center; gap: 16px; font-size: 11px; font-weight: 500; color: #94a3b8; margin-top: 4px; }
.gc-radar-legend-dot { width: 8px; height: 8px; border-radius: 999px; margin-right: 6px; display: inline-block; }
.gc-ability-list { flex: 1; display: flex; flex-direction: column; gap: 16px; min-width: 0; }
.gc-ability-row-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.gc-ability-row-left { display: flex; align-items: center; gap: 8px; }
.gc-ability-icon-box { display: flex; align-items: center; justify-content: center; width: 28px; height: 28px; border-radius: 8px; }
.gc-ability-name { font-size: 13px; font-weight: 600; color: #1e293b; }
.gc-ability-stage { border-radius: 6px; border: 1px solid #f1f5f9; background: #f8fafc; padding: 1px 6px; font-size: 9px; font-weight: 500; letter-spacing: 1px; color: #94a3b8; text-transform: uppercase; }
.gc-ability-xp { font-size: 12px; font-weight: 600; color: #335CFE; }
.gc-ability-delta { font-size: 10px; font-weight: 500; color: #10b981; margin-left: 6px; }
.gc-ability-bar { height: 6px; background: #f8fafc; border-radius: 999px; overflow: hidden; }
.gc-ability-bar-fill { height: 100%; background: #5B7BFE; border-radius: 999px; }
.gc-ability-evidence { font-size: 10px; font-weight: 500; color: #94a3b8; margin-top: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* Timeline */
.gc-timeline-entry { display: flex; gap: 16px; }
.gc-timeline-line { display: flex; flex-direction: column; align-items: center; flex-shrink: 0; }
.gc-timeline-dot { display: flex; align-items: center; justify-content: center; width: 32px; height: 32px; border-radius: 999px; flex-shrink: 0; }
.gc-timeline-tail { width: 1px; flex: 1; background: #f1f5f9; margin: 4px 0; }
.gc-timeline-content { flex: 1; padding-bottom: 20px; }
.gc-timeline-top { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.gc-timeline-title { font-size: 13px; font-weight: 500; color: #1e293b; line-height: 1.4; }
.gc-timeline-tags { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; }
.gc-timeline-tag { border-radius: 999px; padding: 2px 8px; font-size: 10px; font-weight: 500; letter-spacing: 0.2px; }
.gc-timeline-tag.special { background: #fff7ed; color: #ea580c; }
.gc-timeline-tag.normal { background: #f1f5f9; color: #64748b; }
.gc-timeline-xp { font-size: 13px; font-weight: 600; letter-spacing: -0.3px; flex-shrink: 0; }
.gc-timeline-time { font-size: 10px; font-weight: 500; color: #94a3b8; margin-top: 2px; text-align: right; }

/* Gap card */
.gc-gap-card { padding: 20px; }
.gc-gap-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 12px; }
.gc-no-gap { border-radius: 24px; background: rgba(236,253,245,0.4); border: 1px solid #a7f3d0; padding: 16px 20px; }

@media (max-width: 640px) {
  .gc-header { padding: 16px 16px 0; }
  .gc-content { padding: 16px; }
  .gc-header-top { gap: 12px; }
  .gc-radar-card { padding: 24px 16px; }
  .gc-xp-breakdown { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .gc-badge-section { padding: 16px; }
  .gc-badge-section-head, .gc-badge-category-header { align-items: flex-start; flex-direction: column; gap: 4px; }
  .gc-badge-section-hint { text-align: left; }
  .gc-badge-grid, .gc-badge-category-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .gc-rank-row { align-items: flex-start; }
}

/* Loading */
.gc-loading { display: flex; align-items: center; justify-content: center; padding: 80px 0; }
.gc-loading-icon { width: 40px; height: 40px; border-radius: 999px; background: #EEF3FF; display: flex; align-items: center; justify-content: center; animation: gc-pulse 1.5s ease-in-out infinite; margin-bottom: 12px; }
@keyframes gc-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
.gc-loading-text { font-size: 13px; font-weight: 500; color: #94a3b8; }

/* Empty state */
.gc-empty { border-radius: 24px; border: 1px dashed #e2e8f0; background: #fff; padding: 40px 20px; text-align: center; }
.gc-empty-title { font-size: 14px; font-weight: 600; color: #475569; margin-bottom: 4px; }
.gc-empty-desc { font-size: 13px; color: #94a3b8; max-width: 400px; margin: 0 auto; line-height: 1.6; }

/* Badge modal */
.gc-modal-overlay { position: fixed; inset: 0; z-index: 50; display: flex; align-items: center; justify-content: center; background: rgba(15,23,42,0.2); backdrop-filter: blur(4px); }
.gc-modal { border-radius: 28px; border: 1px solid rgba(255,255,255,0.7); background: #fff; box-shadow: 0 24px 80px rgba(15,23,42,0.18); padding: 24px; width: 420px; max-width: 90vw; max-height: 80vh; overflow-y: auto; }
`;

/* Inject CSS once */
let cssInjected = false;
function injectGrowthCSS() {
  if (cssInjected) return;
  const style = document.createElement('style');
  style.setAttribute('data-growth-center', 'true');
  style.textContent = GROWTH_CSS;
  document.head.appendChild(style);
  cssInjected = true;
}

/* ══════════════════════════════════════════════════════════════════════
   Icon motif map — for badge rendering from API data
   ──────────────────────────────────────────────────────────────────── */
const MOTIF_ICON_MAP: Record<string, LucideIcon> = {
  meeting_ring: Users,
  report_arrow: ArrowRight,
  chat_bolt: Sparkles,
  linked_rings: Users,
  handoff: HandHelping,
  radar_ping: Radar,
  search_chat: Search,
  stack_docs: Layers3,
  path_nodes: Target,
  handshake_seal: Handshake,
  blueprint_flag: Flag,
  grid_blocks: Layers3,
  summit_flag: Flag,
  shield_ping: ShieldCheck,
  seal_box: Briefcase,
  calendar_lines: CalendarClock,
  dashboard_gauge: Gauge,
  manual_stack: BookOpen,
  stamp_flow: CircleDashed,
  loop_note: ArrowRight,
  invoice_shield: ShieldCheck,
  wallet_gate: Briefcase,
  scroll_seal: FileStack,
  bill_return: ArrowRight,
  cart_checklist: Briefcase,
  mentor_orbit: Users,
  idea_burst: Lightbulb,
  cards_spark: Sparkles,
  path_flag: Flag,
  wrench_up: Wrench,
  spark_card: Sparkles,
  converge_box: CircleDashed,
  check_list: ShieldCheck,
  steps_forward: ArrowRight,
  split_arrows: ArrowRight,
  split_blocks: Layers3,
  roadblock_light: ShieldAlert,
  calendar_zero: CalendarClock,
  bullseye_arrow: Target,
  shield_card: ShieldCheck,
  loop_flag: Flag,
  time_blocks: CalendarClock,
  door_focus: Lock,
  bell_early: CalendarClock,
  bubble_basket: HandHelping,
  dial_clock: Gauge,
  timeline_split: ArrowRight,
  sunrise_card: Sparkles,
  lamp_notebook: BookOpen,
  parallel_lines: Layers3,
  week_baton: CalendarClock,
  hand_card: HandHelping,
  highlight_lines: Lightbulb,
  bubble_to_list: ArrowRight,
  flag_margin: Flag,
  anchor_bubbles: Target,
  stamp_card: ShieldCheck,
  path_branch: Target,
  card_stack: Layers3,
  question_bridge: Search,
  sync_loop: CircleDashed,
  focus_lens: Eye,
  decision_gate: ShieldAlert,
  orbit_users: Users,
  pen_signal: PenTool,
  file_stack: FileStack,
  star_step: Star,
  arrow_db_return: ArrowRight,
  boundary_blocks: Layers3,
  bridge_bubble_gear: Handshake,
  bridge_depts: Handshake,
  bridge_platforms: Handshake,
  bulb_question: Lightbulb,
  card_to_box: Briefcase,
  card_to_people: Users,
  card_up_arrow: ArrowRight,
  center_copy: FileStack,
  chairs_lamp: Users,
  cloud_to_tag: BookOpen,
  compass_correct: Target,
  connector_dual: Handshake,
  dialog_reverse: ArrowRight,
  doc_shelf_light: BookOpen,
  door_source: Lock,
  dual_pens: PenTool,
  dual_rings: Users,
  eye_dials: Eye,
  filter_clean: Search,
  flag_people: Flag,
  flow_to_ui: ArrowRight,
  folder_stack: FileStack,
  footprint_box: Target,
  gauge_red: Gauge,
  gears_people: Users,
  hand_light_path: HandHelping,
  helix_card: BrainCircuit,
  ice_warm: Sparkles,
  knot_hand: Handshake,
  lens_seed: Search,
  lever_rock: Wrench,
  map_pieces: Layers3,
  map_to_cards: Layers3,
  milestone_steps: Target,
  mirror_outline: Eye,
  modules_chain: Layers3,
  mold_cards: Layers3,
  net_sparkle: Sparkles,
  news_to_task: ArrowRight,
  page_sections: FileStack,
  page_structure: FileStack,
  pan_gold: ShieldCheck,
  part_slot: Layers3,
  pen_screen_correct: PenTool,
  person_highlight: Users,
  person_lens: Users,
  pin_evidence: ShieldCheck,
  puzzle_outline: Layers3,
  question_frame: Search,
  radar_waves: Radar,
  reply_bubble: Users,
  scene_frame: Eye,
  scissors_vine: Wrench,
  spotlight_crack: ShieldAlert,
  sprout_flow: Sparkles,
  stacked_stones: Layers3,
  stamp_smooth: ShieldCheck,
  stone_stairs: Target,
  table_path: Layers3,
  thermometer_hand: Gauge,
  thick_line: PenTool,
  thread_nodes: Target,
  toolbox_docs: Wrench,
  version_up: ArrowRight,
  warning_crack: ShieldAlert,
  wave_to_text: PenTool,
  web_nodes: Target,
  whiteboard_spot: BookOpen,
  window_path_stars: Sparkles,
  wrench_module: Wrench,
  zip_folder: Briefcase,
};

const ABILITY_FALLBACK_ICONS: Record<GrowthAbilityKey, LucideIcon> = {
  exec: Rocket,
  collab: Users,
  analyze: BrainCircuit,
  insight: Eye,
  risk: ShieldAlert,
  write: PenTool,
};

function badgeIconFor(badge: BadgeProgress): LucideIcon {
  return MOTIF_ICON_MAP[badge.iconMotif] || ABILITY_FALLBACK_ICONS[badge.abilityKey] || Sparkles;
}

/* ══════════════════════════════════════════════════════════════════════
   Ability visual config
   ──────────────────────────────────────────────────────────────────── */
const ABILITY_VISUALS: Record<string, { icon: LucideIcon; color: string; bg: string }> = {
  exec:    { icon: Rocket,      color: '#5B7BFE', bg: 'rgba(91,123,254,0.1)' },
  collab:  { icon: Users,       color: '#5B7BFE', bg: 'rgba(91,123,254,0.1)' },
  analyze: { icon: BrainCircuit, color: '#64748b', bg: '#f1f5f9' },
  insight: { icon: Eye,         color: '#10b981', bg: 'rgba(16,185,129,0.08)' },
  risk:    { icon: ShieldAlert,  color: '#f97316', bg: 'rgba(249,115,22,0.08)' },
  write:   { icon: PenTool,     color: '#5B7BFE', bg: 'rgba(91,123,254,0.1)' },
};

/* ══════════════════════════════════════════════════════════════════════
   Utility functions
   ──────────────────────────────────────────────────────────────────── */
function formatRelativeMoment(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const diffMs = Date.now() - date.getTime();
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
  if (diffHours < 1) return '刚刚';
  if (diffHours < 24) return `${diffHours}小时前`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays === 1) return '昨天';
  if (diffDays < 7) return `${diffDays}天前`;
  if (diffDays < 14) return '1周前';
  return date.toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
}

function weekLabelFromDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const oneJan = new Date(date.getFullYear(), 0, 1);
  const weekNum = Math.ceil(((date.getTime() - oneJan.getTime()) / 86400000 + oneJan.getDay() + 1) / 7);
  return `第${weekNum}周`;
}

const SOURCE_TYPE_CN: Record<string, string> = {
  weekly_review_task_entry: '任务复盘',
  task_note: '任务备注',
  review_insight: '复盘提炼',
  meeting: '会议沉淀',
  meeting_source: '会议资料',
  event_line_activity: '事件线活动',
  v2_document: '数据中心资料',
  document: '资料文档',
  task_context_candidate: '任务经验',
  task_attachment_candidate: '附件提炼',
  review_insight_pending: '复盘提炼',
  manual: '手动录入',
};

function sourceTypeCN(raw: string): string {
  return SOURCE_TYPE_CN[raw] || raw.replace(/_/g, ' ');
}

function pickExperienceWallText(item: GrowthExperienceWallItem): string {
  const text = item.text || '';
  const summary = item.summary || '';
  if (text.length > 0 && text.length <= 120) return text;
  if (text.length > 120) return text.slice(0, 117) + '…';
  return summary.slice(0, 117) + (summary.length > 117 ? '…' : '');
}

function xpTypeLabel(xpType: string): string {
  if (xpType === 'codification') return '经验沉淀';
  if (xpType === 'reuse') return '方法复用';
  if (xpType === 'improvement') return '成长改进';
  return '复盘反思';
}

/* ══════════════════════════════════════════════════════════════════════
   Badge palette + BadgeToken — SVG ring with icon center
   ──────────────────────────────────────────────────────────────────── */
const STATE_LABELS: Record<BadgeState, string> = {
  locked: '未解锁',
  progress: '进行中',
  ready: '待点亮',
  lit: '已点亮',
  mastered: '已精通',
};

function badgePalette(state: BadgeState) {
  if (state === 'lit' || state === 'mastered') {
    return {
      ring: '#335CFE', glow: 'drop-shadow(0 16px 40px rgba(51,92,254,0.18))',
      outer: 'linear-gradient(180deg,rgba(246,249,255,0.98),rgba(225,233,255,0.98))',
      center: 'linear-gradient(180deg,rgba(83,121,255,0.98),rgba(44,78,233,0.98))',
      iconColor: '#fff', border: 'rgba(113,144,255,0.26)',
    };
  }
  if (state === 'ready') {
    return {
      ring: '#5B7BFE', glow: 'drop-shadow(0 12px 30px rgba(91,123,254,0.16))',
      outer: 'linear-gradient(180deg,#fff,rgba(240,244,255,1))',
      center: 'linear-gradient(180deg,rgba(239,244,255,1),rgba(221,231,255,1))',
      iconColor: '#335CFE', border: 'rgba(113,144,255,0.22)',
    };
  }
  if (state === 'progress') {
    return {
      ring: '#8FA4FF', glow: 'drop-shadow(0 8px 20px rgba(143,164,255,0.08))',
      outer: 'linear-gradient(180deg,#fff,rgba(243,246,253,1))',
      center: 'linear-gradient(180deg,#fff,rgba(243,246,253,1))',
      iconColor: '#5B7BFE', border: 'rgba(203,213,225,0.8)',
    };
  }
  return {
    ring: '#D5DCE8', glow: 'none',
    outer: 'linear-gradient(180deg,#fff,rgba(245,247,250,1))',
    center: 'linear-gradient(180deg,#fff,rgba(245,247,250,1))',
    iconColor: '#94a3b8', border: 'rgba(226,232,240,0.9)',
  };
}

function badgeIsLit(badge: BadgeProgress): boolean {
  return badge.state === 'lit' || badge.state === 'mastered';
}

function normalizeProgressPercent(value?: number | null): number {
  const raw = Number(value ?? 0);
  const pct = raw <= 1 ? raw * 100 : raw;
  if (!Number.isFinite(pct)) return 0;
  return Math.max(0, Math.min(100, Math.round(pct)));
}

function BadgeToken({ badge, size = 'md' }: { badge: BadgeProgress; size?: 'md' | 'lg' }) {
  const pal = badgePalette(badge.state);
  const Icon = badgeIconFor(badge);
  const d = size === 'lg' ? 116 : 84;
  const r = size === 'lg' ? 44 : 31;
  const sw = size === 'lg' ? 5 : 4;
  const circ = 2 * Math.PI * r;
  const off = circ - (circ * badge.progressPercent) / 100;
  const innerSize = size === 'lg' ? 76 : 56;
  const iconSize = size === 'lg' ? 32 : 24;

  return (
    <div style={{ position: 'relative', width: d, height: d, filter: pal.glow }}>
      <div style={{ position: 'absolute', inset: 0, borderRadius: 999, border: `1px solid ${pal.border}`, background: pal.outer }} />
      <svg style={{ position: 'absolute', inset: 0, transform: 'rotate(-90deg)' }} viewBox={`0 0 ${d} ${d}`}>
        <circle cx={d / 2} cy={d / 2} r={r} fill="none" stroke="rgba(226,232,240,0.66)" strokeWidth={sw} />
        <circle cx={d / 2} cy={d / 2} r={r} fill="none" stroke={pal.ring} strokeWidth={sw}
          strokeLinecap="round" strokeDasharray={circ} strokeDashoffset={off}
          style={{ transition: 'stroke-dashoffset 0.7s ease' }} />
      </svg>
      <div style={{
        position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%,-50%)',
        width: innerSize, height: innerSize, borderRadius: 999,
        border: `1px solid ${pal.border}`, background: pal.center,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <Icon width={iconSize} height={iconSize} color={pal.iconColor} strokeWidth={1.9} />
      </div>
    </div>
  );
}

function BadgeCell({ badge, onSelect }: { badge: BadgeProgress; onSelect: (badge: BadgeProgress) => void }) {
  const isLit = badgeIsLit(badge);
  const isReady = badge.state === 'ready';
  const inProgress = badge.state === 'progress';
  const nameClass = isLit ? 'lit' : inProgress || isReady ? 'prog' : 'lock';
  const statusClass = isLit ? 'lit' : isReady ? 'ready' : inProgress ? 'progress' : 'locked';
  const statusText = isLit ? STATE_LABELS[badge.state] : isReady ? '即将点亮' : inProgress ? `${badge.progressPercent}%` : '未解锁';

  return (
    <button className="gc-badge-cell" onClick={() => onSelect(badge)}>
      <BadgeToken badge={badge} size="md" />
      <span className={`gc-badge-name ${nameClass}`}>{badge.name}</span>
      <span className={`gc-badge-status ${statusClass}`}>{statusText}</span>
    </button>
  );
}

/* ══════════════════════════════════════════════════════════════════════
   Badge Modal
   ──────────────────────────────────────────────────────────────────── */
function BadgeModal({ badge, onClose }: { badge: BadgeProgress; onClose: () => void }) {
  const pal = badgePalette(badge.state);
  return (
    <div className="gc-modal-overlay" onClick={onClose}>
      <div className="gc-modal" onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 20 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            <BadgeToken badge={badge} size="lg" />
            <div>
              <div style={{ fontSize: 16, fontWeight: 600, color: '#0f172a', letterSpacing: -0.3 }}>{badge.name}</div>
              <div style={{ fontSize: 12, fontWeight: 500, color: '#94a3b8', marginTop: 2 }}>{badge.categoryLabel} · +{badge.xp} XP</div>
              <span style={{
                display: 'inline-block', marginTop: 4, borderRadius: 999, padding: '2px 8px',
                fontSize: 10, fontWeight: 600,
                background: pal.iconColor === '#fff' ? 'rgba(51,92,254,0.1)' : '#f1f5f9',
                color: pal.iconColor === '#fff' ? '#335CFE' : '#475569',
              }}>{STATE_LABELS[badge.state]}</span>
            </div>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 6, borderRadius: 999, color: '#94a3b8' }}>
            <X size={16} />
          </button>
        </div>

        <div>
          <div className="gc-modal-subtitle">为什么重要</div>
          <p style={{ fontSize: 13, lineHeight: 1.8, color: '#475569' }}>{badge.whyItMatters || badge.description}</p>
          {badge.description && badge.description !== badge.whyItMatters && (
            <p style={{ marginTop: 8, fontSize: 12, lineHeight: 1.7, color: '#64748b' }}>{badge.description}</p>
          )}
        </div>

        {/* Progress bar */}
        <div style={{ marginTop: 16, borderRadius: 18, background: '#f8fafc', padding: 16 }}>
          <div className="gc-modal-subtitle">当前进度</div>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
            <span style={{ fontSize: 12, fontWeight: 600, color: '#334155' }}>{badge.progressText}</span>
            <span style={{ fontSize: 14, fontWeight: 600, color: '#335CFE' }}>{badge.progressPercent}%</span>
          </div>
          <div style={{ height: 8, borderRadius: 999, background: '#fff', overflow: 'hidden' }}>
            <div style={{ height: '100%', borderRadius: 999, background: '#335CFE', width: `${badge.progressPercent}%`, transition: 'width 0.5s' }} />
          </div>
          {badge.nextActionText && (
            <p style={{ marginTop: 12, fontSize: 11, lineHeight: 1.6, color: '#64748b' }}>{badge.nextActionText}</p>
          )}
        </div>

        {badge.unlockedAt && (
          <p style={{ marginTop: 12, fontSize: 11, fontWeight: 500, color: '#94a3b8' }}>获得时间：{formatRelativeMoment(badge.unlockedAt)}</p>
        )}

        {badge.missingSignals.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <p style={{ fontSize: 11, fontWeight: 600, letterSpacing: 1.2, color: '#94a3b8', marginBottom: 8 }}>还差什么</p>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {badge.missingSignals.map((signal) => (
                <span key={signal} style={{ borderRadius: 999, border: '1px solid #e2e8f0', background: '#f8fafc', padding: '4px 10px', fontSize: 10, fontWeight: 500, color: '#64748b' }}>
                  {signal}
                </span>
              ))}
            </div>
          </div>
        )}

        {badge.evidence.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <p style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 1.5, color: '#94a3b8', marginBottom: 8 }}>达成证据</p>
            {badge.evidence.slice(0, 3).map((ev, i) => (
              <div key={i} style={{ borderRadius: 14, background: '#f8fafc', padding: '8px 12px', marginBottom: 8 }}>
                <p style={{ fontSize: 12, fontWeight: 500, color: '#334155' }}>{ev.title}</p>
                <p style={{ fontSize: 10, color: '#94a3b8', marginTop: 2 }}>{ev.subtitle} · {formatRelativeMoment(ev.occurredAt)}</p>
              </div>
            ))}
          </div>
        )}

        {badge.actionLinks.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <div className="gc-modal-subtitle">相关入口</div>
            <div className="gc-modal-link-list">
              {badge.actionLinks.map((link) => (
                <span key={`${link.tab}-${link.label}`} className="gc-modal-link-pill">{link.label}</span>
              ))}
            </div>
          </div>
        )}

        <button onClick={onClose} style={{
          marginTop: 20, width: '100%', borderRadius: 999, border: '1px solid #e2e8f0',
          background: '#fff', fontSize: 13, fontWeight: 500, color: '#475569', padding: '10px 0',
          cursor: 'pointer', transition: 'background 0.2s',
        }}>关闭</button>
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════
   Hexagonal Radar Chart — matches preview exactly
   ──────────────────────────────────────────────────────────────────── */
function AbilitySparkline({ points, color }: { points: GrowthAbilityTrendPoint[]; color: string }) {
  // L1 升级：用 recharts mini line，比手写 SVG 美观且自带 hover tooltip
  if (points.length < 2) return null;
  const data = points.map(p => ({ week: p.weekLabel, score: p.score }));
  return (
    <div style={{ width: 56, height: 18 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 2, right: 1, left: 1, bottom: 2 }}>
          <Line
            type="monotone"
            dataKey="score"
            stroke={color}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
          <RTooltip
            cursor={false}
            contentStyle={{ fontSize: 10, padding: '2px 6px', borderRadius: 4 }}
            labelFormatter={(label) => `${label}`}
            formatter={(value) => [`${value} 分`, ''] as [string, string]}
            separator=""
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ────────── M2: 承诺履约 · 正向 streak + 环比双线 ──────────
function CommitmentCard({ summary }: { summary: GrowthCommitmentSummary }) {
  const currentStreak = summary.currentStreakDays ?? 0;
  const longestStreak = summary.longestStreakDays ?? 0;
  const monthly = summary.monthlyFulfilledCount ?? 0;
  const lastMonthly = summary.lastMonthFulfilledCount ?? 0;
  const growth = summary.growthPercent ?? 0;
  const curve = summary.cumulativeCurve ?? [];

  // 没任何数据时不显示
  if (monthly === 0 && longestStreak === 0 && curve.length === 0) return null;

  // 距离破纪录还差多少
  const breakRecordIn = longestStreak > currentStreak ? longestStreak - currentStreak + 1 : 0;
  const isBeatingRecord = currentStreak >= longestStreak && currentStreak > 0;

  return (
    <div className="gc-card" style={{ padding: 20 }}>
      <div className="gc-section-header" style={{ marginBottom: 16 }}>
        <div className="gc-section-title">准时兑现</div>
        <span className="gc-section-hint">
          {isBeatingRecord ? '正在创造新纪录' : `最长 ${longestStreak} 天`}
        </span>
      </div>

      {/* 顶部 2 个大数字 */}
      <div style={{ display: 'flex', gap: 32, alignItems: 'flex-end', marginBottom: 16, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 4 }}>连续准时</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
            <span style={{ fontSize: 36, fontWeight: 700, color: '#5B7BFE', lineHeight: 1 }}>{currentStreak}</span>
            <span style={{ fontSize: 14, color: '#94a3b8' }}>天</span>
            {isBeatingRecord && <span style={{ fontSize: 11, color: '#f59e0b', fontWeight: 600, marginLeft: 4 }}>🔥 破纪录</span>}
          </div>
          {breakRecordIn > 0 && (
            <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 4 }}>
              再坚持 {breakRecordIn} 天可破纪录
            </div>
          )}
        </div>
        <div>
          <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 4 }}>本月已兑现</div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
            <span style={{ fontSize: 28, fontWeight: 600, color: '#1e293b', lineHeight: 1 }}>{monthly}</span>
            <span style={{ fontSize: 14, color: '#94a3b8' }}>件</span>
            {growth !== 0 && (
              <span style={{
                fontSize: 11,
                fontWeight: 600,
                marginLeft: 4,
                padding: '2px 6px',
                borderRadius: 4,
                background: growth > 0 ? 'rgba(5,150,105,0.1)' : 'rgba(220,38,38,0.08)',
                color: growth > 0 ? '#059669' : '#dc2626',
              }}>
                {growth > 0 ? '↗' : '↘'} {Math.abs(growth)}%
              </span>
            )}
          </div>
          <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 4 }}>
            上月 {lastMonthly} 件
          </div>
        </div>
      </div>

      {/* 双线累计图：本月 vs 上月 */}
      {curve.length > 0 && (curve.some(c => c.currentCumulative > 0) || curve.some(c => c.previousCumulative > 0)) && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 11, color: '#64748b', marginBottom: 6 }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <span style={{ width: 16, height: 2, background: '#5B7BFE', borderRadius: 1 }} /> 本月累计
            </span>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <span style={{ width: 16, height: 0, borderTop: '2px dashed #94a3b8' }} /> 上月同位置
            </span>
          </div>
          <div style={{ width: '100%', height: 80 }}>
            <ResponsiveContainer>
              <LineChart data={curve} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <XAxis dataKey="weekLabel" axisLine={false} tickLine={false} tick={{ fontSize: 10, fill: '#94a3b8' }} />
                <YAxis hide />
                <Line type="monotone" dataKey="currentCumulative" stroke="#5B7BFE" strokeWidth={2} dot={{ r: 3 }} />
                <Line type="monotone" dataKey="previousCumulative" stroke="#94a3b8" strokeWidth={1.5} strokeDasharray="4 3" dot={false} />
                <RTooltip
                  contentStyle={{ fontSize: 11 }}
                  formatter={(v, n) => [`${v} 件`, n === 'currentCumulative' ? '本月' : '上月'] as [string, string]}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </div>
  );
}

// ────────── M1: 业务覆盖热力 (用 BarChart 横向) ──────────
function BusinessCoverageCard({ coverage }: { coverage: GrowthBusinessCoverage }) {
  if (coverage.items.length === 0) return null;
  const data = coverage.items.slice(0, 8);
  return (
    <div className="gc-card" style={{ padding: 20 }}>
      <div className="gc-section-header" style={{ marginBottom: 12 }}>
        <div className="gc-section-title">精力投入</div>
        <span className="gc-section-hint">{coverage.coveredClients} 个客户 · {coverage.coveredProjects} 个关联项目</span>
      </div>
      <div style={{ width: '100%', height: data.length * 32 + 20 }}>
        <ResponsiveContainer>
          <BarChart data={data} layout="vertical" margin={{ left: 0, right: 20 }}>
            <XAxis type="number" hide />
            <YAxis type="category" dataKey="label" width={110} tick={{ fontSize: 11 }} />
            <RTooltip
              contentStyle={{ fontSize: 11 }}
              formatter={(_v, _n, item: { payload?: { taskCount: number; documentCount: number; glossaryTermCount: number } }) => {
                const it = item?.payload;
                if (!it) return ['', ''] as [string, string];
                return [`任务${it.taskCount} 文档${it.documentCount} 字典${it.glossaryTermCount}`, ''] as [string, string];
              }}
              separator=""
            />
            <Bar dataKey="score" fill="#5B7BFE" radius={[0, 4, 4, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ────────── M3 复盘沉淀（独立大卡片）──────────
function ReviewStreakCard({ streak }: { streak: GrowthReviewStreak }) {
  const cur = streak.currentStreakWeeks;
  const longest = streak.maxStreakWeeks;
  const monthly = streak.monthlyEntryCount ?? 0;
  const lastMonthly = streak.lastMonthEntryCount ?? 0;
  const entryGrowth = streak.entryGrowthPercent ?? 0;
  const monthlyChars = streak.monthlyCharCount ?? 0;
  const lastMonthlyChars = streak.lastMonthCharCount ?? 0;
  const charGrowth = streak.charGrowthPercent ?? 0;
  const dailyTrend = streak.dailyTrend ?? [];

  if (cur === 0 && longest === 0 && monthly === 0) return null;

  const breakIn = longest > cur ? longest - cur + 1 : 0;
  const isBeating = cur >= longest && cur > 0;

  function formatChars(n: number): string {
    if (n >= 10000) return `${(n / 10000).toFixed(1)}万`;
    if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
    return String(n);
  }

  // 单元格组件：统一处理 label + value+chip + sub
  type Cell = {
    label: string;
    value: string | number;
    unit?: string;
    chip?: { text: string; positive: boolean };
    sub?: string;
    valueColor?: string;
    valueSize?: number;
  };
  const cells: Cell[] = [
    {
      label: '连续',
      value: cur,
      unit: '周',
      sub: isBeating ? '🔥 创造新纪录' : breakIn > 0 ? `再 ${breakIn} 周破纪录` : `最长 ${longest} 周`,
      valueColor: '#5B7BFE',
      valueSize: 30,
    },
    {
      label: '本月条目',
      value: monthly,
      unit: '条',
      chip: entryGrowth !== 0 ? { text: `${entryGrowth > 0 ? '↗' : '↘'}${Math.abs(entryGrowth)}%`, positive: entryGrowth > 0 } : undefined,
      sub: `上月 ${lastMonthly} 条`,
      valueColor: '#1e293b',
      valueSize: 26,
    },
    {
      label: '本月字数',
      value: formatChars(monthlyChars),
      unit: '字',
      chip: charGrowth !== 0 ? { text: `${charGrowth > 0 ? '↗' : '↘'}${Math.abs(charGrowth)}%`, positive: charGrowth > 0 } : undefined,
      sub: `上月 ${formatChars(lastMonthlyChars)} 字`,
      valueColor: '#1e293b',
      valueSize: 26,
    },
    {
      label: '累计',
      value: streak.totalReviewWeeks,
      unit: '周',
      sub: '沉淀进组织',
      valueColor: '#1e293b',
      valueSize: 26,
    },
  ];

  // 30 天日趋势：补一个 7 天 SMA 给曲线更平滑
  const trendData = dailyTrend.map((p, i) => {
    const window = dailyTrend.slice(Math.max(0, i - 6), i + 1);
    const smaEntry = window.reduce((s, x) => s + x.entryCount, 0) / window.length;
    return {
      date: p.date,
      shortDate: p.date.slice(5),  // MM-DD
      entryCount: p.entryCount,
      charCount: p.charCount,
      sma7: Number(smaEntry.toFixed(2)),
    };
  });

  return (
    <div className="gc-card" style={{ padding: 20 }}>
      <div className="gc-section-header" style={{ marginBottom: 16 }}>
        <div className="gc-section-title">复盘沉淀</div>
        <span className="gc-section-hint">
          {isBeating ? '正在创造新纪录' : `最长 ${longest} 周连续`}
        </span>
      </div>

      {/* 上半部分：4 列数字（每个 cell nowrap）*/}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(4, 1fr)',
        gap: 12,
        marginBottom: 18,
      }}>
        {cells.map((c) => (
          <div key={c.label} style={{ minWidth: 0 }}>
            <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 6, whiteSpace: 'nowrap' }}>
              {c.label}
            </div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 3, flexWrap: 'nowrap', whiteSpace: 'nowrap' }}>
              <span style={{ fontSize: c.valueSize, fontWeight: 700, color: c.valueColor, lineHeight: 1 }}>
                {c.value}
              </span>
              {c.unit && <span style={{ fontSize: 11, color: '#94a3b8' }}>{c.unit}</span>}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6, whiteSpace: 'nowrap', flexWrap: 'wrap' }}>
              {c.chip && (
                <span style={{
                  fontSize: 10, fontWeight: 600, padding: '1px 5px', borderRadius: 3,
                  background: c.chip.positive ? 'rgba(5,150,105,0.1)' : 'rgba(220,38,38,0.08)',
                  color: c.chip.positive ? '#059669' : '#dc2626',
                  whiteSpace: 'nowrap',
                }}>{c.chip.text}</span>
              )}
              {c.sub && (
                <span style={{ fontSize: 10, color: '#94a3b8', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {c.sub}
                </span>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* 下半部分：30 天复盘曲线（条目数日趋势 + 7 日均线）*/}
      {trendData.length > 0 && (
        <div>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
            <span style={{ fontSize: 11, color: '#64748b' }}>近 30 天复盘节奏</span>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 12, fontSize: 10, color: '#94a3b8' }}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                <span style={{ width: 14, height: 2, background: '#5B7BFE' }} />单日条目
              </span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                <span style={{ width: 14, borderTop: '2px dashed #94a3b8' }} />7 日均线
              </span>
            </span>
          </div>
          <div style={{ width: '100%', height: 100 }}>
            <ResponsiveContainer>
              <LineChart data={trendData} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
                <XAxis
                  dataKey="shortDate"
                  axisLine={false}
                  tickLine={false}
                  tick={{ fontSize: 9, fill: '#94a3b8' }}
                  interval={Math.max(0, Math.floor(trendData.length / 6) - 1)}
                />
                <YAxis hide />
                <Line type="monotone" dataKey="entryCount" stroke="#5B7BFE" strokeWidth={2} dot={false} isAnimationActive={false} />
                <Line type="monotone" dataKey="sma7" stroke="#94a3b8" strokeWidth={1.5} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
                <RTooltip
                  contentStyle={{ fontSize: 11 }}
                  labelFormatter={(label) => `${label}`}
                  formatter={(value, name, item: { payload?: { entryCount: number; charCount: number } }) => {
                    if (name === 'sma7') return [`${value} 条/天`, '7 日均'] as [string, string];
                    const p = item?.payload;
                    if (p && p.entryCount > 0) {
                      return [`${p.entryCount} 条 · ${formatChars(p.charCount)}字`, ''] as [string, string];
                    }
                    return [`${value} 条`, ''] as [string, string];
                  }}
                  separator=""
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}
    </div>
  );
}

// ────────── M4 工作类型分布（独立卡片）──────────
function WorkTypeCard({ workType }: { workType: GrowthWorkType }) {
  if (workType.totalTasks === 0) return null;
  const slices = workType.slices.slice(0, 6);
  const palette = ['#5B7BFE', '#7494ec', '#92aef0', '#a4b8f5', '#c9d8fb', '#dbe7ff'];

  return (
    <div className="gc-card" style={{ padding: 20 }}>
      <div className="gc-section-header" style={{ marginBottom: 12 }}>
        <div className="gc-section-title">工作类型分布</div>
        <span className="gc-section-hint">{workType.totalTasks} 件 · {workType.slices.length} 类</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <div style={{ width: 140, height: 140, flexShrink: 0 }}>
          <ResponsiveContainer>
            <PieChart>
              <Pie data={slices} dataKey="count" innerRadius={32} outerRadius={62} paddingAngle={2}>
                {slices.map((_, i) => (
                  <Cell key={i} fill={palette[i % palette.length]} />
                ))}
              </Pie>
              <RTooltip
                contentStyle={{ fontSize: 11 }}
                formatter={(v, n) => [`${v} 件`, String(n)] as [string, string]}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 6 }}>
          {slices.map((s, i) => {
            const pct = Math.round((s.count / workType.totalTasks) * 100);
            return (
              <div key={s.label} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
                <span style={{
                  width: 10, height: 10, borderRadius: 2,
                  background: palette[i % palette.length],
                  flexShrink: 0,
                }} />
                <span style={{ flex: 1, color: '#475569', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {s.label}
                </span>
                <span style={{ color: '#94a3b8', fontSize: 11 }}>{s.count} · {pct}%</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ────────── L3/L4/L5: 学习推荐 ──────────
function LearningCard({ learning }: { learning: GrowthLearning }) {
  const total = learning.internalPicks.length + learning.githubPicks.length + learning.frontierPicks.length;
  if (total === 0 && !learning.externalConfigHint) return null;

  function renderPick(p: GrowthLearningPick, idx: number) {
    const sourceLabel = p.source === 'handbook' ? '📘 同事手册' :
                       p.source === 'exp_wall' ? '💬 组织墙' :
                       p.source === 'github' ? '⚙ GitHub' :
                       p.source === 'exa' ? '🌐 前沿' : p.source;
    return (
      <div key={`${p.source}-${idx}`} style={{ padding: '10px 12px', borderRadius: 6, background: 'rgba(241,245,249,0.5)', display: 'flex', flexDirection: 'column', gap: 4 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 11 }}>
          <span style={{ fontWeight: 600, color: '#5B7BFE' }}>{sourceLabel}</span>
          {p.matchedAbilityLabel && <span style={{ color: '#94a3b8' }}>对应「{p.matchedAbilityLabel}」</span>}
        </div>
        <div style={{ fontSize: 13, fontWeight: 500, color: '#1e293b' }}>{p.title}</div>
        {p.detail && <div style={{ fontSize: 11, color: '#64748b', lineHeight: 1.5 }}>{p.detail}</div>}
        <div style={{ fontSize: 10, color: '#94a3b8', display: 'flex', gap: 12 }}>
          {p.authorName && <span>作者 {p.authorName}</span>}
          {p.reusedCount > 0 && <span>被复用 {p.reusedCount} 次</span>}
          {p.likedCount > 0 && <span>★ {p.likedCount}</span>}
        </div>
      </div>
    );
  }

  return (
    <div className="gc-card" style={{ padding: 20 }}>
      <div className="gc-section-header" style={{ marginBottom: 12 }}>
        <div className="gc-section-title">本周推荐学</div>
        <span className="gc-section-hint">基于你最弱的方向自动匹配</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {learning.internalPicks.map(renderPick)}
        {learning.githubPicks.map((p, i) => renderPick(p, i + 1000))}
        {learning.frontierPicks.map((p, i) => renderPick(p, i + 2000))}
      </div>
      {total === 0 && (
        <div style={{ fontSize: 12, color: '#94a3b8', padding: 12, textAlign: 'center' }}>
          暂无内部同事手册可推荐
          {learning.externalConfigHint && <div style={{ fontSize: 11, marginTop: 4 }}>{learning.externalConfigHint}</div>}
        </div>
      )}
      {total > 0 && !learning.externalEnabled && learning.externalConfigHint && (
        <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 8, padding: '6px 10px', borderRadius: 4, background: 'rgba(241,245,249,0.5)' }}>
          外部前瞻：{learning.externalConfigHint}
        </div>
      )}
    </div>
  );
}

// ────────── H5: 同岗位匿名对标 ──────────
function PeerComparisonCard({ peer }: { peer: GrowthPeerComparison }) {
  if (peer.peerCount === 0) return null;
  const aloneInRole = peer.peerCount === 1;
  return (
    <div className="gc-card" style={{ padding: 20 }}>
      <div className="gc-section-header" style={{ marginBottom: 12 }}>
        <div className="gc-section-title">同岗位对标</div>
        <span className="gc-section-hint">岗位「{peer.roleLabel}」 · 共 {peer.peerCount} 人</span>
      </div>
      {aloneInRole ? (
        <div style={{ fontSize: 12, color: '#64748b', padding: 12, background: 'rgba(241,245,249,0.4)', borderRadius: 4 }}>
          目前组织中你是「{peer.roleLabel}」岗位的唯一一人，等同岗位有 ≥ 2 人之后会出现对标信号。
        </div>
      ) : (
        <div style={{ display: 'flex', alignItems: 'center', gap: 24, flexWrap: 'wrap' }}>
          <div>
            <div style={{ fontSize: 12, color: '#94a3b8' }}>你的排名</div>
            <div style={{ fontSize: 28, fontWeight: 700, color: '#5B7BFE' }}>第 {peer.rank}</div>
            <div style={{ fontSize: 11, color: '#94a3b8' }}>共 {peer.peerCount} 人</div>
          </div>
          <div style={{ flex: 1, minWidth: 200 }}>
            <div style={{ fontSize: 11, color: '#94a3b8', marginBottom: 6 }}>总 XP 对比（匿名）</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
              <div><span style={{ fontSize: 11, color: '#94a3b8' }}>你</span><div style={{ fontSize: 14, fontWeight: 600 }}>{peer.yourTotalXp}</div></div>
              <div><span style={{ fontSize: 11, color: '#94a3b8' }}>中位</span><div style={{ fontSize: 14, fontWeight: 600 }}>{peer.peerMedianXp}</div></div>
              <div><span style={{ fontSize: 11, color: '#94a3b8' }}>第一</span><div style={{ fontSize: 14, fontWeight: 600 }}>{peer.peerTopXp}</div></div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function ActivityRhythm({ days, activeDays, maxStreak }: { days: GrowthDailyActivity[]; activeDays: number; maxStreak: number }) {
  // L2 节奏日历 — react-activity-calendar
  // 数据已经是 react-activity-calendar 期望的 {date, count, level} 格式
  const totalDays = days.length;
  const activeRate = totalDays > 0 ? Math.round((activeDays / totalDays) * 100) : 0;
  // 颜色规则：清闲（level 0）= 浅 / 工作多（level 4）= 深
  // light + dark 两套配色都保持「浅 → 深」一致方向，避免系统色模式翻转
  const theme = {
    light: ['#f1f5f9', '#c9d8fb', '#92aef0', '#5B7BFE', '#3a5ce0'],
    dark:  ['#1e293b', '#2d3a5e', '#3f5288', '#5B7BFE', '#3a5ce0'],
  };
  return (
    <div className="gc-card" style={{ padding: 20 }}>
      <div className="gc-section-header" style={{ marginBottom: 12 }}>
        <div className="gc-section-title">工作节奏</div>
        <span className="gc-section-hint">
          近 {totalDays} 天 · 活跃 {activeDays} 天 ({activeRate}%) · 最长连续 {maxStreak} 天
        </span>
      </div>
      <div style={{ overflowX: 'auto', paddingBottom: 4 }}>
        <ActivityCalendar
          data={days}
          theme={theme}
          colorScheme="light"
          blockSize={11}
          blockMargin={3}
          fontSize={11}
          weekStart={1}
          labels={{
            months: ['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月'],
            weekdays: ['周日','周一','周二','周三','周四','周五','周六'],
            legend: { less: '清闲', more: '工作多' },
            totalCount: '{{count}} 分产出 / {{year}} 年',
          }}
        />
      </div>
    </div>
  );
}


function AbilityRadar({ abilities, gaps }: { abilities: GrowthAbilityScore[]; gaps?: GrowthAbilityGap[] }) {
  const size = 320;
  const cx = 160, cy = 160, R = 110;
  const n = abilities.length;
  if (n < 3) return null;

  const angles = abilities.map((_, i) => (Math.PI * 2 * i / n) - Math.PI / 2);
  const hexPt = (angle: number, r: number): [number, number] => [cx + r * Math.cos(angle), cy + r * Math.sin(angle)];
  const hexPoly = (r: number) => angles.map(a => hexPt(a, r).join(',')).join(' ');

  const gapMap = new Map((gaps || []).map(g => [g.abilityKey, g.requiredScore]));
  const hasRequired = gapMap.size > 0;

  const prevPts = abilities.map((ab, i) => hexPt(angles[i], R * ab.previousScore / 100).join(',')).join(' ');
  const curPts = abilities.map((ab, i) => hexPt(angles[i], R * ab.currentScore / 100).join(',')).join(' ');
  const reqPts = hasRequired
    ? abilities.map((ab, i) => hexPt(angles[i], R * (gapMap.get(ab.abilityKey) ?? ab.currentScore) / 100).join(',')).join(' ')
    : '';

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ overflow: 'visible' }}>
      {/* Grid */}
      {[0.2, 0.4, 0.6, 0.8, 1.0].map(s => (
        <polygon key={s} points={hexPoly(R * s)} fill="none" stroke="#f1f5f9" strokeWidth="1" />
      ))}
      {/* Axis lines */}
      {angles.map((a, i) => {
        const [ex, ey] = hexPt(a, R);
        return <line key={i} x1={cx} y1={cy} x2={ex} y2={ey} stroke="#f1f5f9" strokeWidth="1" />;
      })}
      {/* Previous (gray dashed) */}
      <polygon points={prevPts} fill="rgba(203,213,225,0.12)" stroke="#cbd5e1" strokeWidth="1.5" strokeDasharray="4 3" />
      {/* Required (amber dashed) */}
      {hasRequired && (
        <polygon points={reqPts} fill="none" stroke="#f59e0b" strokeWidth="1.2" strokeDasharray="3 3" />
      )}
      {/* Current (blue filled) */}
      <polygon points={curPts} fill="rgba(91,123,254,0.15)" stroke="#5B7BFE" strokeWidth="2" />
      {/* Current dots */}
      {abilities.map((ab, i) => {
        const [dx, dy] = hexPt(angles[i], R * ab.currentScore / 100);
        return <circle key={ab.abilityKey} cx={dx} cy={dy} r={4} fill="#5B7BFE" stroke="#fff" strokeWidth={2} />;
      })}
      {/* Labels */}
      {abilities.map((ab, i) => {
        const [lx, ly] = hexPt(angles[i], R + 32);
        const anchor = lx < cx - 10 ? 'end' : lx > cx + 10 ? 'start' : 'middle';
        return (
          <g key={`lbl-${ab.abilityKey}`}>
            <text x={lx} y={ly - 2} textAnchor={anchor} dominantBaseline="central" fontSize="12" fontWeight="500" fill="#64748b">{ab.label}</text>
            <text x={lx} y={ly + 14} textAnchor={anchor} dominantBaseline="central" fontSize="11" fontWeight="600" fill="#5B7BFE">{ab.currentScore}</text>
          </g>
        );
      })}
    </svg>
  );
}

/* ══════════════════════════════════════════════════════════════════════
   Tab 1: Quote Wall
   ══════════════════════════════════════════════════════════════════ */
function ExperienceWallTab({ uiSessionScopeKey }: { uiSessionScopeKey: string }) {
  const growthState = useGrowthOverviewState();
  const [items, setItems] = useRuntimeUiSessionState<GrowthExperienceWallItem[]>(`${uiSessionScopeKey}:items`, []);
  const [loadedAt, setLoadedAt] = useRuntimeUiSessionState<number>(`${uiSessionScopeKey}:loaded-at`, 0);
  const [isLoading, setIsLoading] = useState(loadedAt <= 0);
  const [cloudSyncError, setCloudSyncError] = useRuntimeUiSessionState<string | null>(`${uiSessionScopeKey}:cloud-error`, null);
  const [filter, setFilter] = useRuntimeUiSessionState<'all' | 'month' | 'quarter'>(`${uiSessionScopeKey}:filter`, 'all');
  const [likingIds, setLikingIds] = useState<Set<string>>(() => new Set());

  const reloadExperienceWall = useCallback(async () => {
    try {
      const result = await getGrowthExperienceWall();
      setItems(result.items || []);
      setCloudSyncError(result.cloudSyncError || null);
      setLoadedAt(Date.now());
    } catch {
      setItems([]);
      setCloudSyncError('成长金句暂时无法加载。');
    }
  }, []);

  const handleLikeQuote = useCallback((entry: GrowthExperienceWallItem) => {
    if (entry.source !== 'exp_wall' || entry.currentUserLiked || likingIds.has(entry.id)) return;
    setLikingIds((current) => new Set(current).add(entry.id));
    likeGrowthExperienceWallQuote(entry.id)
      .then((updated) => {
        setItems((current) => current.map((item) => (item.id === updated.id ? { ...item, ...updated } : item)));
      })
      .catch(() => {
        reloadExperienceWall().catch(() => undefined);
      })
      .finally(() => {
        setLikingIds((current) => {
          const next = new Set(current);
          next.delete(entry.id);
          return next;
        });
      });
  }, [likingIds, reloadExperienceWall]);

  useEffect(() => {
    if (loadedAt > 0 && Date.now() - loadedAt < GROWTH_RUNTIME_CACHE_TTL_MS) {
      setIsLoading(false);
      return;
    }
    setIsLoading(items.length === 0);
    reloadExperienceWall()
      .finally(() => setIsLoading(false));
  }, [items.length, loadedAt, reloadExperienceWall]);

  const companionGeneratedAt = growthState?.growthOverview?.companionSummary?.generatedAt || '';
  useEffect(() => {
    if (!companionGeneratedAt) return;
    void reloadExperienceWall();
  }, [companionGeneratedAt, reloadExperienceWall]);

  const sortedEntries = useMemo(() => {
    let filtered = [...items];
    const now = new Date();
    if (filter === 'month') {
      const cutoff = new Date(now.getFullYear(), now.getMonth(), 1);
      filtered = filtered.filter((e) => new Date(e.createdAt) >= cutoff);
    } else if (filter === 'quarter') {
      const quarterStart = new Date(now.getFullYear(), Math.floor(now.getMonth() / 3) * 3, 1);
      filtered = filtered.filter((e) => new Date(e.createdAt) >= quarterStart);
    }
    return filtered.sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());
  }, [items, filter]);

  if (isLoading) {
    return (
      <div className="gc-loading">
        <div style={{ textAlign: 'center' }}>
          <div className="gc-loading-icon"><BookOpen size={20} color="#335CFE" /></div>
          <div className="gc-loading-text">加载经验金句...</div>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="gc-section-header">
        <div>
          <div className="gc-section-title">组织经验金句</div>
          <span className="gc-section-hint">已沉淀 {sortedEntries.length} 条</span>
          {cloudSyncError && (
            <span className="gc-section-hint" style={{ marginLeft: 8, color: '#f59e0b' }}>
              当前显示本地缓存
            </span>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {(['all', 'month', 'quarter'] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              style={{
                padding: '4px 10px', borderRadius: 8, fontSize: 11, fontWeight: 600, border: 'none', cursor: 'pointer',
                background: filter === f ? '#EEF3FF' : 'transparent',
                color: filter === f ? '#335CFE' : '#94a3b8',
              }}
            >
              {{ all: '全部', month: '本月', quarter: '本季度' }[f]}
            </button>
          ))}
        </div>
      </div>

      {sortedEntries.length > 0 ? (
        <div className="gc-insight-grid">
          {sortedEntries.map((entry) => {
            const isAI = entry.authorUserName?.includes('大周') || entry.authorUserName?.includes('庆华') || entry.authorUserName?.includes('花花') || entry.authorUserName?.includes('罗茜茜') || entry.sourceType?.includes('ai');
            const metricCount = entry.source === 'handbook' ? entry.reuseCount : entry.likeCount;
            const isLikedByCurrentUser = entry.source === 'exp_wall' ? Boolean(entry.currentUserLiked) : metricCount > 0;
            const quoteText = pickExperienceWallText(entry);
            const authorName = entry.authorUserName || '团队';
            const projectName = entry.clientName || '';
            const isLiking = likingIds.has(entry.id);
            return (
              <div key={entry.id} className="gc-card gc-insight-card">
                <div className="gc-insight-quote">“{quoteText}”</div>
                <div className="gc-insight-meta">
                  <div className="gc-insight-author">
                    <div className={`gc-icon-token sm ${isAI ? 'brand' : 'gray'}`}>
                      {isAI ? <Sparkles size={10} color="#335CFE" /> : <Users size={10} color="#64748b" />}
                    </div>
                    <span className="gc-author-name">{authorName}</span>
                    {projectName && <span className="gc-source-tag">{projectName}</span>}
                    <span className="gc-source-tag">{sourceTypeCN(entry.sourceType || '经验')} · {weekLabelFromDate(entry.createdAt)}</span>
                  </div>
                  <button
                    className={`gc-like-btn${isLikedByCurrentUser ? ' liked' : ''}`}
                    disabled={entry.source === 'exp_wall' ? (isLikedByCurrentUser || isLiking) : false}
                    onClick={() => {
                      if (entry.source === 'exp_wall') {
                        handleLikeQuote(entry);
                        return;
                      }
                      markHandbookEntryReused(entry.id)
                        .then(() => reloadExperienceWall())
                        .catch(() => {});
                    }}
                    title={entry.source === 'handbook' ? '标记为已复用' : (isLikedByCurrentUser ? '已点亮' : '点亮这条金句')}
                  >
                    <Heart size={12} fill={isLikedByCurrentUser ? '#5B7BFE' : 'none'} color={isLikedByCurrentUser ? '#5B7BFE' : '#D1D5DB'} strokeWidth={2} />
                    {metricCount > 0 ? ` ${metricCount}` : ''}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="gc-empty">
          <div className="gc-loading-icon" style={{ margin: '0 auto 12px', animation: 'none' }}>
            <BookOpen size={20} color="#335CFE" />
          </div>
          <div className="gc-empty-title">{cloudSyncError ? '正在显示本地缓存' : '暂无组织沉淀'}</div>
          <div className="gc-empty-desc">
            {cloudSyncError
              ? '组织沉淀同步暂时不可用，本地缓存里还没有可展示的金句。'
              : '任务、会议和复盘证据会进入“能力成长”；只有被整理成可复用方法或组织金句的内容才会展示在这里。'}
          </div>
        </div>
      )}
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════
   Tab 2: Ability Growth
   ══════════════════════════════════════════════════════════════════ */
function AbilityGrowthTab({ overview }: { overview: GrowthOverview | null }) {
  const growthState = useGrowthOverviewState();
  const isLoading = !overview && (growthState?.isGrowthLoading ?? false);
  const abilities = overview?.abilities || [];
  const topGaps = useMemo(() => (overview?.abilityGaps || []).slice(0, 3), [overview]);
  const hasRealGrowthSignals = useMemo(() => {
    if (!overview) return false;
    if ((overview.weeklyXp || 0) > 0) return true;
    if ((overview.recentEntries || []).some((entry) => entry.sourceType !== 'badge_unlock')) return true;
    return abilities.some((ab) => (ab.totalXp || 0) > 0 || (ab.weeklyXp || 0) > 0);
  }, [overview, abilities]);

  const recentEntries = useMemo(() => {
    if (!overview?.recentEntries?.length) return [];
    // H3: 时间线只展示「真实工作事件」
    // ① 过滤掉徽章解锁（归到徽章 tab）
    // ② 同一 source（如同一手册条目同一天触发多个能力）合并为一条，能力维度聚合到 abilityKeys
    type Merged = XpLedgerEntry & { mergedAbilities: string[] };
    const seen = new Map<string, Merged>();
    for (const entry of overview.recentEntries) {
      if (entry.sourceType === 'badge_unlock') continue;
      const dateKey = entry.createdAt?.slice(0, 10) || '';
      // 以 sourceId+date 为键合并不同 ability 的同一事件
      const key = `${entry.sourceType}|${entry.sourceId}|${dateKey}`;
      const existing = seen.get(key);
      if (existing) {
        existing.totalXp = (existing.totalXp || existing.delta) + (entry.totalXp || entry.delta);
        if (entry.abilityLabel && !existing.mergedAbilities.includes(entry.abilityLabel)) {
          existing.mergedAbilities.push(entry.abilityLabel);
        }
      } else {
        seen.set(key, {
          ...entry,
          mergedAbilities: entry.abilityLabel ? [entry.abilityLabel] : [],
        });
      }
    }
    return [...seen.values()]
      .sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime())
      .slice(0, 8);
  }, [overview]);

  if (isLoading) {
    return (
      <div className="gc-loading">
        <div style={{ textAlign: 'center' }}>
          <div className="gc-loading-icon"><BrainCircuit size={20} color="#335CFE" /></div>
          <div className="gc-loading-text">加载能力数据...</div>
        </div>
      </div>
    );
  }

  if (!abilities.length) {
    return (
      <div className="gc-empty">
        <div className="gc-loading-icon" style={{ margin: '0 auto 12px', animation: 'none', width: 48, height: 48 }}>
          <BrainCircuit size={24} color="#335CFE" />
        </div>
        <div className="gc-empty-title">能力雷达待激活</div>
        <div className="gc-empty-desc">多数人的成长起点在这里；你的成长会体现在复盘、任务、手册和组织反馈带来的数据变化中。</div>
      </div>
    );
  }

  return (
    <div className="gc-space-y">
      {overview?.companionSummary ? (
        <div className="gc-card" style={{ padding: 20, borderColor: '#DDE6FF', background: 'linear-gradient(180deg,#fff 0%,#fafbff 100%)' }}>
          <div className="gc-section-header" style={{ marginBottom: 10 }}>
            <div>
              <div className="gc-section-title">本周成长总结</div>
              <div style={{ marginTop: 3, fontSize: 10, color: '#94a3b8' }}>
                成长陪伴自动整理
              </div>
            </div>
            {(overview.companionSummary.growthHighlights?.length || 0) > 0 && (
              <span style={{ borderRadius: 999, background: '#ecfdf5', color: '#059669', padding: '4px 9px', fontSize: 10, fontWeight: 600 }}>
                {overview.companionSummary.growthHighlights?.length} 项进步
              </span>
            )}
          </div>
          <div style={{ maxWidth: 900, fontSize: 13, lineHeight: 1.75, color: '#334155' }}>
            {overview.companionSummary.weeklySummary}
          </div>
          {(overview.companionSummary.growthHighlights?.length || 0) > 0 && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(210px,1fr))', gap: 9, marginTop: 13 }}>
              {(overview.companionSummary.growthHighlights || []).slice(0, 3).map((item) => (
                <div key={item.id} style={{ borderRadius: 11, border: '1px solid #e8edf7', background: '#fff', padding: '10px 11px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                    <span style={{ fontSize: 11, fontWeight: 600, color: '#475569' }}>{item.abilityLabel}</span>
                    <span style={{ fontSize: 10, fontWeight: 600, color: item.trend === 'up' ? '#059669' : '#5B7BFE' }}>
                      {item.trend === 'up' ? '本周提升 ↑' : item.trend === 'steady' ? '持续稳定 →' : '正在形成 ↗'}
                    </span>
                  </div>
                  <div style={{ marginTop: 5, fontSize: 11, lineHeight: 1.55, color: '#64748b' }}>{item.summary}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : null}
      {/* H4 被看见横幅已移除（用户判断：单条手册被复用对 CEO 视角信息密度不够）*/}
      {/* Radar + Ability List */}
      <div className="gc-card gc-radar-card">
        <div className="gc-radar-visual">
          {!hasRealGrowthSignals && (
            <div
              style={{
                width: '100%',
                marginBottom: 12,
                padding: '10px 12px',
                borderRadius: 10,
                background: 'rgba(91,123,254,0.06)',
                border: '1px solid rgba(91,123,254,0.12)',
                color: '#64748b',
                fontSize: 12,
                lineHeight: 1.6,
              }}
            >
              多数人的成长起点在这里；你的成长会体现在复盘、任务、手册和组织反馈带来的数据变化中。
            </div>
          )}
          {overview?.peerComparison && overview.peerComparison.peerCount > 0 && (
            <div
              style={{
                width: '100%',
                marginBottom: 16,
                padding: '12px 16px',
                borderRadius: 10,
                background: 'linear-gradient(135deg, rgba(91,123,254,0.08) 0%, rgba(91,123,254,0.02) 100%)',
                border: '1px solid rgba(91,123,254,0.15)',
              }}
            >
              <div style={{ fontSize: 11, color: '#64748b', marginBottom: 6 }}>
                成长速度 · {overview.peerComparison.roleLabel}
              </div>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 14, flexWrap: 'wrap' }}>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
                  <span style={{ fontSize: 11, color: '#94a3b8' }}>排</span>
                  <span style={{ fontSize: 28, fontWeight: 700, color: '#5B7BFE', lineHeight: 1 }}>
                    第 {overview.peerComparison.rank}
                  </span>
                  <span style={{ fontSize: 11, color: '#94a3b8' }}>/ 共 {overview.peerComparison.peerCount} 人</span>
                </div>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 4, marginLeft: 'auto' }}>
                  <span style={{ fontSize: 22, fontWeight: 600, color: '#1e293b', lineHeight: 1 }}>
                    {overview.peerComparison.yourTotalXp.toLocaleString()}
                  </span>
                  <span style={{ fontSize: 11, color: '#94a3b8' }}>XP</span>
                </div>
              </div>
            </div>
          )}
          <AbilityRadar abilities={abilities} gaps={topGaps} />
          <div className="gc-radar-legend" style={{ justifyContent: 'center', marginTop: 12 }}>
            <span><span className="gc-radar-legend-dot" style={{ background: '#5B7BFE' }} />当前</span>
            <span><span className="gc-radar-legend-dot" style={{ background: '#cbd5e1' }} />上期</span>
            {topGaps.length > 0 && <span><span className="gc-radar-legend-dot" style={{ background: '#f59e0b' }} />要求</span>}
          </div>
        </div>
        <div className="gc-ability-list">
          {[...abilities].sort((a, b) => b.weeklyXp - a.weeklyXp).map((ab) => {
            const v = ABILITY_VISUALS[ab.abilityKey] || ABILITY_VISUALS.exec;
            const AbIcon = v.icon;
            const trend = overview?.abilityTrends?.find(t => t.abilityKey === ab.abilityKey);
            return (
              <div key={ab.abilityKey}>
                <div className="gc-ability-row-top">
                  <div className="gc-ability-row-left">
                    <div className="gc-ability-icon-box" style={{ background: v.bg }}>
                      <AbIcon size={14} color={v.color} />
                    </div>
                    <span className="gc-ability-name">{ab.label}</span>
                    <span className="gc-ability-stage">{ab.stage}</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, textAlign: 'right' }}>
                    {trend && trend.points.length >= 2 && (
                      <AbilitySparkline points={trend.points} color={v.color} />
                    )}
                    {trend && trend.scoreDelta !== 0 && (
                      <span
                        style={{
                          fontSize: 10,
                          fontWeight: 600,
                          color: trend.direction === 'up' ? '#059669' : '#dc2626',
                          padding: '1px 5px',
                          borderRadius: 4,
                          background: trend.direction === 'up' ? 'rgba(5,150,105,0.08)' : 'rgba(220,38,38,0.08)',
                        }}
                      >
                        {trend.direction === 'up' ? '↑' : '↓'}{Math.abs(trend.scoreDelta)}
                      </span>
                    )}
                    <span className="gc-ability-xp">{ab.totalXp} XP</span>
                    {ab.weeklyXp > 0 && <span className="gc-ability-delta">+{ab.weeklyXp}</span>}
                  </div>
                </div>
                <div className="gc-ability-bar">
                  <div className="gc-ability-bar-fill" style={{ width: `${Math.min(ab.currentScore, 100)}%` }} />
                </div>
                <div className="gc-ability-evidence">{ab.evidence}</div>
              </div>
            );
          })}
        </div>
      </div>

      {/* L2 工作节奏（放在准时兑现上面）*/}
      {overview?.dailyActivity && overview.dailyActivity.days.length > 0 && (
        <ActivityRhythm
          days={overview.dailyActivity.days}
          activeDays={overview.dailyActivity.activeDays}
          maxStreak={overview.dailyActivity.maxStreak}
        />
      )}

      {/* M2 准时兑现 */}
      {overview?.commitmentSummary && <CommitmentCard summary={overview.commitmentSummary} />}

      {/* M3 复盘沉淀 + M4 工作类型分布（同一排）*/}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 12 }}>
        {overview?.reviewStreak && <ReviewStreakCard streak={overview.reviewStreak} />}
        {overview?.workTypeDistribution && <WorkTypeCard workType={overview.workTypeDistribution} />}
      </div>

      {/* M1 精力投入（业务覆盖）*/}
      {overview?.businessCoverage && <BusinessCoverageCard coverage={overview.businessCoverage} />}

      {/* H5 同岗位对标 已合并到雷达图边的「成长速度」面板 */}
      {/* L3/L4/L5 本周推荐学 已取消 */}
      {/* M6 累计影响力 已删除 */}

      {/* 成长时间线 + 成长机会 已移除（用户判断：信息价值不足，不能给出有意义的指导） */}
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════
   Tab 3: Badges & Rank
   ══════════════════════════════════════════════════════════════════ */
function BadgesAndRankTab({ overview, uiSessionScopeKey }: { overview: GrowthOverview | null; uiSessionScopeKey: string }) {
  const [badgeBoard, setBadgeBoard] = useRuntimeUiSessionState<BadgeBoard | null>(`${uiSessionScopeKey}:board`, null);
  const [loadedAt, setLoadedAt] = useRuntimeUiSessionState<number>(`${uiSessionScopeKey}:loaded-at`, 0);
  const [selectedBadge, setSelectedBadge] = useState<BadgeProgress | null>(null);
  const [isLoading, setIsLoading] = useState(loadedAt <= 0);
  const [loadError, setLoadError] = useRuntimeUiSessionState<string | null>(`${uiSessionScopeKey}:load-error`, null);

  const loadBadges = useCallback(async (options?: { keepVisible?: boolean }) => {
    setSelectedBadge(null);
    if (!options?.keepVisible) setBadgeBoard(null);
    setLoadError(null);
    setIsLoading(!options?.keepVisible);
    try {
      setBadgeBoard(await getGrowthBadges());
      setLoadedAt(Date.now());
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : '成长徽章加载失败');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    // Badge milestones can change after a cloud-side growth analysis. Refresh
    // whenever this tab is entered so the complete catalog, including locked
    // badges, never remains hidden behind the runtime session cache.
    void loadBadges({ keepVisible: Boolean(badgeBoard) });
  }, [loadBadges]);

  const rank = overview?.rank;
  const totalXp = overview?.totalXp ?? 0;
  const weeklyXp = overview?.weeklyXp ?? 0;
  const boardOverview = badgeBoard?.overview;
  const categories = badgeBoard?.categories ?? [];
  const rankProgressPercent = normalizeProgressPercent(rank?.progress);

  const allBadges = useMemo(() => {
    return categories.flatMap((cat) => cat.badges);
  }, [categories]);

  const litBadges = useMemo(() => {
    return allBadges
      .filter(badgeIsLit)
      .sort((a, b) => new Date(b.unlockedAt || 0).getTime() - new Date(a.unlockedAt || 0).getTime());
  }, [allBadges]);

  const upcomingBadges = useMemo(() => {
    const ids = new Set(boardOverview?.upcomingBadgeIds ?? []);
    const explicit = allBadges.filter((badge) => ids.has(badge.id) && !badgeIsLit(badge));
    const fallback = allBadges.filter((badge) => !badgeIsLit(badge) && badge.progressPercent > 0);
    const source = explicit.length > 0 ? explicit : fallback;
    return source
      .slice()
      .sort((a, b) => b.progressPercent - a.progressPercent || a.name.localeCompare(b.name, 'zh-CN'))
      .slice(0, 8);
  }, [allBadges, boardOverview?.upcomingBadgeIds]);

  const totalBadgeCount = boardOverview?.totalBadges ?? allBadges.length;
  const litBadgeCount = boardOverview?.litBadges ?? litBadges.length;
  const badgeXp = boardOverview?.totalXp ?? litBadges.reduce((sum, badge) => sum + badge.xp, 0);
  const badgeStats = [
    { label: '已点亮', value: `${litBadgeCount}/${totalBadgeCount}`, icon: Trophy },
    { label: '即将点亮', value: upcomingBadges.length, icon: Sparkles },
    { label: '本月新增', value: boardOverview?.monthlyNewBadges ?? 0, icon: CalendarClock },
    { label: '徽章 XP', value: badgeXp.toLocaleString(), icon: Swords },
  ];

  if (isLoading) {
    return (
      <div className="gc-loading">
        <div style={{ textAlign: 'center' }}>
          <div className="gc-loading-icon"><Trophy size={20} color="#335CFE" /></div>
          <div className="gc-loading-text">加载徽章数据...</div>
        </div>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="gc-empty">
        <div className="gc-loading-icon" style={{ margin: '0 auto 12px', animation: 'none' }}>
          <ShieldAlert size={20} color="#e11d48" />
        </div>
        <div className="gc-empty-title">成长徽章加载失败</div>
        <div className="gc-empty-desc">{loadError}</div>
        <button
          type="button"
          onClick={() => void loadBadges()}
          style={{
            marginTop: 16,
            border: '1px solid #dbe4ff',
            borderRadius: 999,
            background: '#fff',
            padding: '8px 16px',
            color: '#335CFE',
            cursor: 'pointer',
            fontSize: 12,
            fontWeight: 600,
          }}
        >
          重试
        </button>
      </div>
    );
  }

  return (
    <div className="gc-space-y">
      {/* XP Hero */}
      <div className="gc-xp-hero">
        <div className="gc-xp-hero-top">
          <div className="gc-icon-token xl brand-border">
            <Swords size={28} color="#335CFE" strokeWidth={1.8} />
          </div>
          <div>
            <div className="gc-xp-hero-rank">{rank?.fullLabel || '加载中'}</div>
            <div className="gc-xp-hero-num">
              {totalXp.toLocaleString()} <span className="gc-xp-label">XP</span>
            </div>
          </div>
          <div style={{ marginLeft: 'auto' }}>
            {weeklyXp > 0 && (
              <div className="gc-xp-hero-badge">
                <Sparkles size={12} /> +{weeklyXp} 本周
              </div>
            )}
          </div>
        </div>
        {rank && (
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
              <span style={{ fontSize: 11, fontWeight: 500, color: '#94a3b8' }}>
                {rank.nextName ? `距${rank.nextName}还需 ${rank.xpToNext} XP` : '已达最高段位'}
              </span>
              <span style={{ fontSize: 11, fontWeight: 500, color: '#94a3b8' }}>{rankProgressPercent}%</span>
            </div>
            <div className="gc-progress-track" style={{ height: 4 }}>
              <div className="gc-progress-fill" style={{ width: `${rankProgressPercent}%`, background: '#5B7BFE' }} />
            </div>
          </div>
        )}
        <div className="gc-xp-breakdown">
          {badgeStats.map((item) => {
            const BIcon = item.icon;
            return (
              <div key={item.label} className="gc-xp-break-card">
                <div className="gc-xp-break-top">
                  <BIcon size={14} color="#335CFE" />
                  <span className="gc-xp-break-label">{item.label}</span>
                </div>
                <div className="gc-xp-break-val">{item.value}</div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Badge milestones */}
      <div>
        <div className="gc-section-header">
          <div className="gc-section-title">我的徽章</div>
          <span className="gc-section-hint">已点亮 {litBadgeCount}/{totalBadgeCount}</span>
        </div>
        {allBadges.length > 0 ? (
          <div>
            {litBadges.length > 0 && (
              <div className="gc-badge-section">
                <div className="gc-badge-section-head">
                  <div className="gc-badge-section-title">已点亮</div>
                  <div className="gc-badge-section-hint">展示最近点亮的 {Math.min(litBadges.length, 12)} 枚</div>
                </div>
                <div className="gc-badge-grid">
                  {litBadges.slice(0, 12).map((badge) => (
                    <BadgeCell key={badge.id} badge={badge} onSelect={setSelectedBadge} />
                  ))}
                </div>
              </div>
            )}

            <div className="gc-badge-section">
              <div className="gc-badge-section-head">
                <div className="gc-badge-section-title">全部徽章分类</div>
                <div className="gc-badge-section-hint">按能力方向查看未来成长目标</div>
              </div>
              <div className="gc-badge-category-list">
                {categories.map((category) => {
                  const CategoryIcon = ABILITY_VISUALS[category.abilityKey]?.icon || ABILITY_FALLBACK_ICONS[category.abilityKey] || Trophy;
                  const categoryLitCount = category.litCount ?? category.badges.filter(badgeIsLit).length;
                  const categoryTotalCount = category.totalCount ?? category.badges.length;
                  return (
                    <div key={category.id} className="gc-badge-category-card">
                      <div className="gc-badge-category-header">
                        <div className="gc-badge-category-title">
                          <div className="gc-icon-token sm brand">
                            <CategoryIcon size={12} color="#335CFE" />
                          </div>
                          <span className="gc-badge-category-name">{category.label}</span>
                        </div>
                        <span className="gc-badge-category-meta">已点亮 {categoryLitCount}/{categoryTotalCount}</span>
                      </div>
                      <div className="gc-badge-category-grid">
                        {category.badges.map((badge) => (
                          <BadgeCell key={badge.id} badge={badge} onSelect={setSelectedBadge} />
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        ) : (
          <div className="gc-empty">
            <div className="gc-loading-icon" style={{ margin: '0 auto 12px', animation: 'none' }}>
              <Trophy size={20} color="#335CFE" />
            </div>
            <div className="gc-empty-title">还没有可展示的成长徽章</div>
            <div className="gc-empty-desc">徽章数据暂未就绪，完成更多工作后会自动解锁。</div>
          </div>
        )}
      </div>

      {selectedBadge && <BadgeModal badge={selectedBadge} onClose={() => setSelectedBadge(null)} />}
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════
   Main Export — GrowthCenterView
   ══════════════════════════════════════════════════════════════════ */
type GrowthTab = 'experience' | 'ability' | 'badges';

const TABS: { key: GrowthTab; label: string }[] = [
  { key: 'experience', label: '经验墙' },
  { key: 'ability', label: '能力成长' },
  { key: 'badges', label: '徽章与排行' },
];

export function GrowthCenterView({ uiSessionScopeKey = 'growth:local:anonymous' }: { uiSessionScopeKey?: string }) {
  const [activeTab, setActiveTab] = useRuntimeUiSessionState<GrowthTab>(`${uiSessionScopeKey}:tab`, 'experience');
  const growthState = useGrowthOverviewState();
  const [headerOverview, setHeaderOverview] = useState<GrowthOverview | null>(null);
  const contentRef = React.useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const container = contentRef.current;
    if (!container) return undefined;
    const scrollKey = `${uiSessionScopeKey}:${activeTab}:scroll-top`;
    container.scrollTop = readRuntimeUiSessionValue(scrollKey, 0);
    const remember = () => writeRuntimeUiSessionValue(scrollKey, container.scrollTop);
    container.addEventListener('scroll', remember, { passive: true });
    return () => {
      remember();
      container.removeEventListener('scroll', remember);
    };
  }, [activeTab, uiSessionScopeKey]);

  // Inject CSS on mount
  useEffect(() => { injectGrowthCSS(); }, []);

  // Load overview
  useEffect(() => {
    if (growthState?.growthOverview) return;
    getGrowthOverview().then(setHeaderOverview).catch(() => undefined);
  }, []);

  const overview = growthState?.growthOverview ?? headerOverview;
  const rankLabel = overview?.rank?.fullLabel || '加载中';
  const totalXp = overview?.totalXp ?? 0;
  const weeklyXp = overview?.weeklyXp ?? 0;

  return (
    <div className="gc-root">
      {/* Header */}
      <div className="gc-header">
        <div className="gc-header-inner">
          <div className="gc-header-top">
            <div>
              <div className="gc-page-eyebrow">Growth Center</div>
              <div className="gc-page-title">成长中心</div>
              <div className="gc-page-subtitle">把工作经验变成组织资产</div>
            </div>
            <div className="gc-xp-area">
              <div className="gc-rank-chip">
                <Swords size={14} color="#335CFE" strokeWidth={2} />
                {rankLabel}
              </div>
              <div>
                <div className="gc-xp-num">{totalXp.toLocaleString()} <span className="gc-xp-label">XP</span></div>
                {weeklyXp > 0 && <div className="gc-xp-week">+{weeklyXp}</div>}
              </div>
            </div>
          </div>
          <div className="gc-tab-bar">
            {TABS.map((tab) => (
              <button
                key={tab.key}
                className={`gc-tab-btn${activeTab === tab.key ? ' active' : ''}`}
                onClick={() => setActiveTab(tab.key)}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Content */}
      <div ref={contentRef} className="gc-content">
        <div className="gc-content-inner">
          {activeTab === 'experience' && <ExperienceWallTab uiSessionScopeKey={`${uiSessionScopeKey}:experience`} />}
          {activeTab === 'ability' && <AbilityGrowthTab overview={overview} />}
          {activeTab === 'badges' && <BadgesAndRankTab overview={overview} uiSessionScopeKey={`${uiSessionScopeKey}:badges`} />}
        </div>
      </div>
    </div>
  );
}

export default GrowthCenterView;
