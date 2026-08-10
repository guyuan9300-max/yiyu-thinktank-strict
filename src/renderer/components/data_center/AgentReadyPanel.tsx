/**
 * C 审计 P0-3 修复 (2026-05-24)
 *
 * V3 Agent-Ready 数据中心最小前端入口 — 让顾源源硬门槛 9 真过.
 * 4 个最小面板组合到一个 panel 里, 挂在数据中心区域.
 *
 * 不要求漂亮, 但必须可打开 / 可看见 / 可刷新.
 *
 * 4 子面板:
 *   1. DataGapsPanel        — 数据缺口 (suggested_tools/clarification/priority)
 *   2. AgentRunLogsPanel    — Agent 调用历史 (tool_name/actor/status/duration)
 *   3. ToolRegistryBrowser  — 工具清单 (when_to_use/risk_level/approval)
 *   4. ApprovalQueuePanel   — 待审批 (含 approve/reject 按钮)
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  getClientDataGaps,
  listAgentRunLogs,
  getToolRegistry,
  listApprovals,
  approveApproval,
  rejectApproval,
  type DataGapItem,
  type AgentRunLogItem,
  type ToolRegistryEntry,
  type ApprovalRow,
} from '../../lib/api.js';

type TabKey = 'data_gaps' | 'agent_runs' | 'tool_registry' | 'approvals';

interface AgentReadyPanelProps {
  clientId?: string;
  defaultTab?: TabKey;
}

export function AgentReadyPanel({
  clientId,
  defaultTab = 'data_gaps',
}: AgentReadyPanelProps): JSX.Element {
  const [tab, setTab] = useState<TabKey>(defaultTab);

  const tabs: Array<{ key: TabKey; label: string }> = [
    { key: 'data_gaps', label: '数据缺口' },
    { key: 'agent_runs', label: 'AI 调用历史' },
    { key: 'tool_registry', label: '工具清单' },
    { key: 'approvals', label: '待审批' },
  ];

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-gray-700">
          数据中心运行诊断
        </h3>
        {clientId ? (
          <span className="text-xs text-gray-500">客户: {clientId.slice(0, 14)}...</span>
        ) : (
          <span className="text-xs text-orange-500">未选客户 — 部分面板只看全局</span>
        )}
      </div>

      <div className="flex gap-1 border-b border-gray-200 text-xs">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`rounded-t px-3 py-1 ${
              tab === t.key
                ? 'border border-b-white bg-white text-blue-600'
                : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="min-h-[120px]">
        {tab === 'data_gaps' && <DataGapsView clientId={clientId} />}
        {tab === 'agent_runs' && <AgentRunLogsView clientId={clientId} />}
        {tab === 'tool_registry' && <ToolRegistryView />}
        {tab === 'approvals' && <ApprovalQueueView clientId={clientId} />}
      </div>
    </div>
  );
}

// ────────────── 子面板 1: Data Gaps ─────────────

function DataGapsView({ clientId }: { clientId?: string }): JSX.Element {
  const [items, setItems] = useState<DataGapItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const clientIdRef = useRef(clientId);
  useEffect(() => {
    clientIdRef.current = clientId;
  }, [clientId]);

  const load = useCallback(async () => {
    if (!clientId) {
      setItems([]);
      return;
    }
    const capturedClientId = clientId;
    setLoading(true);
    setError(null);
    try {
      const resp = await getClientDataGaps(clientId, { status: 'open', limit: 20 });
      if (clientIdRef.current !== capturedClientId) return;
      setItems(resp.items);
    } catch (err: unknown) {
      if (clientIdRef.current !== capturedClientId) return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (clientIdRef.current === capturedClientId) setLoading(false);
    }
  }, [clientId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!clientId) return <EmptyHint text="请先选客户" />;
  if (loading) return <EmptyHint text="加载中..." />;
  if (error) return <EmptyHint text={`加载失败: ${error}`} />;
  if (items.length === 0) return <EmptyHint text="本客户当前 0 条 open data gaps" />;

  return (
    <div className="flex flex-col gap-2 text-xs">
      <RefreshBar onRefresh={load} count={items.length} label="data gaps" />
      {items.map((g) => (
        <div key={g.gap_id} className="rounded border border-gray-200 p-2">
          <div className="flex items-center gap-2">
            <PriorityChip priority={g.priority || g.severity} />
            <span className="font-medium">{g.subject || g.gap_type}</span>
            <span className="text-gray-500">({g.gap_type})</span>
          </div>
          {g.description && (
            <div className="mt-1 text-gray-600">{g.description}</div>
          )}
          {g.suggested_tools && g.suggested_tools.length > 0 && (
            <div className="mt-1 text-gray-500">
              建议工具:{' '}
              {g.suggested_tools.map((t) => (
                <code key={t} className="mr-1 rounded bg-gray-100 px-1">
                  {t}
                </code>
              ))}
            </div>
          )}
          {g.suggested_clarification && (
            <div className="mt-1 text-blue-600">
              💬 建议澄清: {g.suggested_clarification}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ────────────── 子面板 2: Agent Run Logs ─────────────

function AgentRunLogsView({ clientId }: { clientId?: string }): JSX.Element {
  const [items, setItems] = useState<AgentRunLogItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const clientIdRef = useRef(clientId);
  useEffect(() => {
    clientIdRef.current = clientId;
  }, [clientId]);

  const load = useCallback(async () => {
    const capturedClientId = clientId;
    setLoading(true);
    setError(null);
    try {
      const resp = await listAgentRunLogs({ clientId, limit: 20 });
      if (clientIdRef.current !== capturedClientId) return;
      setItems(resp.items);
    } catch (err: unknown) {
      if (clientIdRef.current !== capturedClientId) return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (clientIdRef.current === capturedClientId) setLoading(false);
    }
  }, [clientId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) return <EmptyHint text="加载中..." />;
  if (error) return <EmptyHint text={`加载失败: ${error}`} />;
  if (items.length === 0) return <EmptyHint text="暂无 AI 调用记录" />;

  return (
    <div className="flex flex-col gap-1 text-xs">
      <RefreshBar onRefresh={load} count={items.length} label="AI 调用" />
      <div className="grid grid-cols-[1fr,auto,auto,auto] gap-x-2 gap-y-1">
        <div className="font-medium text-gray-500">tool · actor</div>
        <div className="font-medium text-gray-500">status</div>
        <div className="font-medium text-gray-500">duration</div>
        <div className="font-medium text-gray-500">idem</div>
        {items.map((r) => (
          <RowFragment key={r.id} run={r} />
        ))}
      </div>
    </div>
  );
}

function RowFragment({ run }: { run: AgentRunLogItem }): JSX.Element {
  const statusColor =
    run.status === 'success'
      ? 'text-green-600'
      : run.status === 'failed'
      ? 'text-red-600'
      : 'text-gray-500';
  return (
    <>
      <div>
        <code className="rounded bg-gray-100 px-1">{run.tool_name || '?'}</code>{' '}
        <span className="text-gray-500">· {run.actor_type || '?'}</span>
      </div>
      <div className={statusColor}>{run.status || '?'}</div>
      <div className="text-gray-500">
        {run.duration_ms ? `${Math.round(run.duration_ms)}ms` : '—'}
      </div>
      <div className="text-gray-400">{run.idempotency_key ? '✓' : '—'}</div>
    </>
  );
}

// ────────────── 子面板 3: Tool Registry ─────────────

function ToolRegistryView(): JSX.Element {
  const [tools, setTools] = useState<ToolRegistryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await getToolRegistry();
      setTools(resp.tools);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) return <EmptyHint text="加载中..." />;
  if (error) return <EmptyHint text={`加载失败: ${error}`} />;

  return (
    <div className="flex flex-col gap-2 text-xs">
      <RefreshBar onRefresh={load} count={tools.length} label="tools" />
      {tools.map((t) => (
        <div
          key={t.tool_name}
          className={`rounded border p-2 ${
            t.status === 'missing'
              ? 'border-orange-200 bg-orange-50'
              : t.status === 'partial'
              ? 'border-yellow-200 bg-yellow-50'
              : 'border-gray-200'
          }`}
        >
          <div className="flex items-center gap-2">
            <code className="font-medium">{t.tool_name}</code>
            <RiskChip risk={t.risk_level} />
            {t.approval_required && (
              <span className="rounded bg-red-100 px-1.5 py-0.5 text-red-700">
                需审批
              </span>
            )}
            <span className="ml-auto text-gray-500">{t.status}</span>
          </div>
          {t.description && (
            <div className="mt-1 text-gray-700">{t.description}</div>
          )}
          {t.when_to_use && (
            <div className="mt-1 text-gray-500">✓ 何时用: {t.when_to_use}</div>
          )}
          {t.when_not_to_use && (
            <div className="text-gray-500">✗ 何时不用: {t.when_not_to_use}</div>
          )}
          {t.endpoint && (
            <div className="mt-1 text-gray-400">
              <code>{t.endpoint}</code>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ────────────── 子面板 4: Approval Queue ─────────────

function ApprovalQueueView({ clientId }: { clientId?: string }): JSX.Element {
  const [items, setItems] = useState<ApprovalRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const clientIdRef = useRef(clientId);
  useEffect(() => {
    clientIdRef.current = clientId;
  }, [clientId]);

  const load = useCallback(async () => {
    const capturedClientId = clientId;
    setLoading(true);
    setError(null);
    try {
      const resp = await listApprovals({ clientId, limit: 20 });
      if (clientIdRef.current !== capturedClientId) return;
      setItems(resp.filter((r) => r.status === 'pending'));
    } catch (err: unknown) {
      if (clientIdRef.current !== capturedClientId) return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (clientIdRef.current === capturedClientId) setLoading(false);
    }
  }, [clientId]);

  const decide = useCallback(
    async (approvalId: string, decision: 'approve' | 'reject') => {
      setBusy(approvalId);
      try {
        if (decision === 'approve') {
          await approveApproval(approvalId, 'human:ui', '前端审批面板');
        } else {
          await rejectApproval(approvalId, 'human:ui', '前端审批面板');
        }
        await load();
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(null);
      }
    },
    [load],
  );

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) return <EmptyHint text="加载中..." />;
  if (error) return <EmptyHint text={`加载失败: ${error}`} />;
  if (items.length === 0) return <EmptyHint text="无待审批项" />;

  return (
    <div className="flex flex-col gap-2 text-xs">
      <RefreshBar onRefresh={load} count={items.length} label="pending approvals" />
      {items.map((appr) => (
        <div key={appr.id} className="rounded border border-yellow-200 bg-yellow-50 p-2">
          <div className="flex items-center gap-2">
            <code className="font-medium">{appr.action_type}</code>
            <span className="text-gray-500">by {appr.actor_type}/{appr.actor_id || '?'}</span>
            <span className="ml-auto text-gray-400">
              {appr.created_at.slice(0, 16).replace('T', ' ')}
            </span>
          </div>
          {appr.reason && <div className="mt-1 text-gray-600">{appr.reason}</div>}
          {appr.target_resource && (
            <div className="text-gray-400">
              target: <code>{appr.target_resource}</code>
            </div>
          )}
          <div className="mt-2 flex gap-2">
            <button
              disabled={busy === appr.id}
              onClick={() => void decide(appr.id, 'approve')}
              className="rounded bg-green-600 px-2 py-0.5 text-white hover:bg-green-700 disabled:opacity-50"
            >
              通过
            </button>
            <button
              disabled={busy === appr.id}
              onClick={() => void decide(appr.id, 'reject')}
              className="rounded bg-gray-200 px-2 py-0.5 text-gray-700 hover:bg-gray-300 disabled:opacity-50"
            >
              拒绝
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

// ────────────── 小工具 ─────────────

function EmptyHint({ text }: { text: string }): JSX.Element {
  return <div className="py-4 text-center text-xs text-gray-400">{text}</div>;
}

function RefreshBar({
  onRefresh,
  count,
  label,
}: {
  onRefresh: () => void;
  count: number;
  label: string;
}): JSX.Element {
  return (
    <div className="flex items-center justify-between text-xs text-gray-500">
      <span>
        共 {count} 条 {label}
      </span>
      <button
        onClick={onRefresh}
        className="rounded border border-gray-200 px-2 py-0.5 hover:bg-gray-50"
      >
        🔄 刷新
      </button>
    </div>
  );
}

function PriorityChip({ priority }: { priority?: string }): JSX.Element {
  const cls =
    priority === 'high'
      ? 'bg-red-100 text-red-700'
      : priority === 'medium'
      ? 'bg-yellow-100 text-yellow-800'
      : 'bg-gray-100 text-gray-600';
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${cls}`}>
      {priority || 'low'}
    </span>
  );
}

function RiskChip({ risk }: { risk?: string }): JSX.Element {
  const cls =
    risk === 'high'
      ? 'bg-red-100 text-red-700'
      : risk === 'medium'
      ? 'bg-yellow-100 text-yellow-800'
      : 'bg-green-100 text-green-700';
  return (
    <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${cls}`}>
      {risk || 'low'}
    </span>
  );
}
