import { useEffect, useState } from 'react';

import { getLocalAiCoverage } from '../../lib/api';

type DeepReadRateBadgeProps = {
  clientId: string | null;
  /** 点击 → 跳到「系统设置 / AI 与云端」的深度解析卡。 */
  onNavigate?: () => void;
};

/** 工作台资料状态条 · 解析率徽章。
 *  - 显示该客户「解析率」= 已深读 document_card / 总文档（公司大脑深度理解的覆盖率）。
 *  - <100% 时琥珀色脉冲提醒 + 可点击 → 跳深度解析设置（不直接跑，避免偷偷占资源把软件卡住）。
 *  - 拉取失败/无文档时不显示。
 */
export function DeepReadRateBadge({ clientId, onNavigate }: DeepReadRateBadgeProps) {
  const [pct, setPct] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (!clientId) {
      setPct(null);
      return;
    }
    getLocalAiCoverage(clientId)
      .then((result) => {
        if (cancelled) return;
        const row = result.perClient.find((p) => p.clientId === clientId) ?? result.perClient[0];
        setPct(row && row.documents > 0 ? Math.round((row.deepRead / row.documents) * 100) : null);
      })
      .catch(() => {
        if (!cancelled) setPct(null);
      });
    return () => {
      cancelled = true;
    };
  }, [clientId]);

  if (!clientId || pct === null) return null;

  const done = pct >= 100;
  return (
    <button
      type="button"
      onClick={onNavigate}
      className="inline-flex items-center gap-1 text-[10px] font-medium text-gray-500 transition-colors hover:text-gray-700"
      title={done ? '已全部深度解析 · 点击查看设置' : '点击去「系统设置 / AI 与云端」开始深度解析（占用内存/算力，可后台进行）'}
    >
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${done ? 'bg-emerald-500' : 'bg-amber-500 animate-pulse'}`} />
      解析率 {pct}%
    </button>
  );
}

export default DeepReadRateBadge;
