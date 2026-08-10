import { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Cloud,
  CloudOff,
  Loader2,
  RefreshCw,
} from 'lucide-react';
import {
  getTeamSyncStats,
  type TeamSyncStats,
} from '../../lib/api';

export function TeamSyncPanel() {
  const [stats, setStats] = useState<TeamSyncStats | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      setStats(await getTeamSyncStats());
    } catch (error) {
      setStats(null);
      const message = error instanceof Error ? error.message : String(error);
      setLoadError(message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const statusCounts = stats?.statusCounts || {};
  const total = Number(stats?.total || 0);
  const delivered = Number(statusCounts.delivered || 0);
  const inFlight = Number(statusCounts.pending || 0) + Number(statusCounts.delivering || 0);
  const failed = Number(statusCounts.failed || 0);

  return (
    <section className="rounded-xl border border-gray-100 bg-white px-6 py-5">
      <header className="mb-5 flex items-end justify-between gap-6">
        <div>
          <h3 className="flex items-center gap-2 text-[18px] font-light tracking-tight text-gray-900">
            <Cloud size={18} className="text-[#5B7BFE]" strokeWidth={1.5} />
            组织协作事件
          </h3>
          <p className="mt-1 text-[12px] leading-relaxed text-gray-400">
            查看严格组织云中与团队、成员相关的投递事件，不代表源文件上传队列。
          </p>
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading}
          className="inline-flex items-center gap-1 text-[11px] text-gray-400 transition-colors hover:text-gray-700 disabled:opacity-50"
        >
          {loading ? (
            <Loader2 size={11} className="animate-spin" strokeWidth={2} />
          ) : (
            <RefreshCw size={11} strokeWidth={2} />
          )}
          刷新
        </button>
      </header>

      {loadError ? (
        <div className="mb-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] leading-relaxed text-amber-800">
          组织协作事件暂不可用：{loadError}
        </div>
      ) : null}

      <div className="mb-5 grid grid-cols-4 gap-3">
        <StatusCard label="总计" value={total} />
        <StatusCard label="已投递" value={delivered} tone="success" icon="success" />
        <StatusCard label="处理中" value={inFlight} tone={inFlight > 0 ? 'warning' : 'neutral'} icon="pending" />
        <StatusCard label="失败" value={failed} tone={failed > 0 ? 'danger' : 'neutral'} icon="failed" />
      </div>

      {!loading && !loadError && total === 0 ? (
        <div className="rounded-lg border border-gray-100 bg-gray-50/60 px-4 py-3 text-[11px] leading-6 text-gray-500">
          当前没有团队或成员相关投递事件。这不代表项目资料为空；项目资料状态以各项目工作台中的“组织共享 / 本机私有”边界为准。
        </div>
      ) : null}

      <div className="mt-4 border-t border-gray-100 pt-4">
        <p className="text-[11px] leading-relaxed text-gray-400">
          <span className="font-medium text-gray-500">严格边界 · </span>
          项目元数据和已发布摘要在业务保存时直接写入组织云；成员源文件正文只保留在各自本机，不存在旧版文件正文批量上传或共享表。
        </p>
      </div>
    </section>
  );
}

function StatusCard({
  label,
  value,
  tone = 'neutral',
  icon,
}: {
  label: string;
  value: number;
  tone?: 'neutral' | 'success' | 'warning' | 'danger';
  icon?: 'success' | 'pending' | 'failed';
}) {
  const toneClasses = {
    neutral: 'border-gray-100 bg-gray-50/40 text-gray-400',
    success: 'border-emerald-100 bg-emerald-50/40 text-emerald-700',
    warning: 'border-amber-100 bg-amber-50/40 text-amber-700',
    danger: 'border-rose-100 bg-rose-50/40 text-rose-700',
  }[tone];
  return (
    <div className={`rounded-lg border px-4 py-3 ${toneClasses}`}>
      <div className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-[0.18em]">
        {icon === 'success' ? <CheckCircle2 size={10} strokeWidth={2} /> : null}
        {icon === 'pending' ? <CloudOff size={10} strokeWidth={2} /> : null}
        {icon === 'failed' ? <AlertTriangle size={10} strokeWidth={2} /> : null}
        {label}
      </div>
      <div className="mt-1 text-[22px] font-light tabular-nums text-current">{value}</div>
    </div>
  );
}
