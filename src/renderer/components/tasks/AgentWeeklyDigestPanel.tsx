import type { AgentWeeklyDigest } from '../../../shared/types';

type AgentWeeklyDigestPanelProps = {
  digests: AgentWeeklyDigest[];
  title?: string;
  subtitle?: string;
};

function sourceLabel(sourceType: unknown) {
  if (sourceType === 'activity_log') return '战略动作';
  if (sourceType === 'topic_capture') return '情报处理';
  if (sourceType === 'workspace_sync') return '系统同步';
  return '真实日志';
}

export function AgentWeeklyDigestPanel({
  digests,
  title = '三个部门本周摘要',
  subtitle = '把庆华、大周、佳乐的当周真实工作痕迹收敛成 CEO 可读的部门周摘要，用来补足“组织本周到底在运转什么”这一层。',
}: AgentWeeklyDigestPanelProps) {
  if (digests.length === 0) return null;

  return (
    <div className="bg-white border border-gray-200 rounded-3xl shadow-sm overflow-hidden">
      <div className="px-6 py-5 border-b border-gray-100 bg-[linear-gradient(135deg,rgba(248,250,252,0.92),rgba(255,255,255,1))]">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-[18px] font-bold text-gray-900">{title}</h2>
          <span className="rounded-full bg-slate-100 px-3 py-1 text-[10px] font-bold tracking-[0.08em] text-slate-600">
            真实日志聚合
          </span>
        </div>
        <p className="mt-1 text-[12px] leading-6 text-gray-600">
          {subtitle}
        </p>
      </div>

      <div className="grid gap-5 p-6 xl:grid-cols-3">
        {digests.map((digest) => (
          <div key={`${digest.agentKey}:${digest.weekLabel}`} className="rounded-[28px] border border-gray-200 bg-gray-50/60 p-5">
            <div className="flex flex-wrap items-center gap-2">
              <span className="h-3 w-3 rounded-full" style={{ backgroundColor: digest.color }} />
              <p className="text-[15px] font-bold text-gray-900">{digest.departmentName}</p>
              <span className="rounded-full bg-white px-2.5 py-1 text-[10px] font-bold text-gray-500 shadow-sm">{digest.agentName}</span>
            </div>

            <div className="mt-3 flex flex-wrap gap-2">
              <span className="rounded-full bg-white px-2.5 py-1 text-[10px] font-bold text-gray-500 shadow-sm">{digest.weekLabel}</span>
              <span className="rounded-full bg-white px-2.5 py-1 text-[10px] font-bold text-gray-500 shadow-sm">{digest.evidenceCount} 条日志</span>
              <span className="rounded-full bg-white px-2.5 py-1 text-[10px] font-bold text-gray-500 shadow-sm">
                {sourceLabel(digest.sourcePolicy?.sourceType)}
              </span>
            </div>

            <p className="mt-4 text-[13px] leading-6 text-gray-700">{digest.summary}</p>

            {digest.focusItems.length > 0 && (
              <div className="mt-4 space-y-2">
                <p className="text-[12px] font-bold text-gray-900">下周延续重点</p>
                {digest.focusItems.map((item) => (
                  <div key={item} className="rounded-2xl bg-white px-4 py-3 text-[12px] leading-6 text-slate-700 shadow-sm">
                    {item}
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
