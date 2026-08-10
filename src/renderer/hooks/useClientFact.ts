/**
 * v2.2 Phase 1 F1.5 · useClientFact · React hook
 *
 * 服务: V2.2_NORTH_STAR.md 目标 A (现有功能不掉链) + 目标 B (机器人能拿全数据)
 *
 * 设计:
 * - 给 6 大 view (IntelligenceStationView / StrategicBrainView / EventLineReportPanel ...) 用
 * - 替代各 view 自己直 fetch 模式 (现有 150+ 处 await get*() 调用)
 * - 内部消费 GET /api/v2/ui/clients/{client_id}/fact-bundle (L2 共识层 endpoint)
 *
 * 用法:
 *   function MyView({ clientId }: { clientId: string }) {
 *     const { bundle, isLoading, error, refresh } = useClientFact({ clientId });
 *     if (isLoading) return <Loading />;
 *     if (error) return <Error />;
 *     return <div>{bundle?.client.name} 有 {bundle?.counts.tasks} 个任务</div>;
 *   }
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { fetchClientFactBundle } from '../lib/api';
import type {
  ClientFactBundle,
  FetchClientFactBundleOptions,
} from '../lib/clientFactTypes';

export interface UseClientFactOptions extends FetchClientFactBundleOptions {
  /** 客户 id. null/空 时 hook 不发请求 (bundle 为 null) */
  clientId: string | null | undefined;
  /** 是否自动随 clientId 变化重新拉. 默认 true */
  autoFetch?: boolean;
}

export interface ClientFactState {
  bundle: ClientFactBundle | null;
  isLoading: boolean;
  error: Error | null;
  /** 手动重新拉. Promise 在拉取完成后 resolve */
  refresh: () => Promise<void>;
}

/**
 * v2.2 F1.5 · useClientFact React hook
 *
 * 跨 view 一致性: 多个 view 用同一个 clientId 调本 hook, 数据相同
 * (不再像之前 EventLineReportPanel 改 task → App.tsx 不感知 那种 bug)
 */
export function useClientFact(options: UseClientFactOptions): ClientFactState {
  const { clientId, autoFetch = true, includeArchived, lite } = options;

  const [bundle, setBundle] = useState<ClientFactBundle | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<Error | null>(null);

  // 用 ref 防止 clientId 切换后旧请求的回调污染新 state (竞态)
  const latestClientIdRef = useRef<string | null | undefined>(clientId);

  const doFetch = useCallback(async (): Promise<void> => {
    if (!clientId) {
      setBundle(null);
      setIsLoading(false);
      setError(null);
      return;
    }
    const requestedClientId = clientId;
    latestClientIdRef.current = requestedClientId;
    setIsLoading(true);
    setError(null);
    try {
      const result = await fetchClientFactBundle(requestedClientId, {
        includeArchived,
        lite,
      });
      // 竞态保护: 拉完后如果 clientId 已经切走, 丢弃这次结果
      if (latestClientIdRef.current !== requestedClientId) {
        return;
      }
      setBundle(result);
    } catch (err) {
      if (latestClientIdRef.current !== requestedClientId) return;
      setError(err instanceof Error ? err : new Error(String(err)));
      setBundle(null);
    } finally {
      if (latestClientIdRef.current === requestedClientId) {
        setIsLoading(false);
      }
    }
  }, [clientId, includeArchived, lite]);

  useEffect(() => {
    if (autoFetch) {
      void doFetch();
    }
    // clientId 切换时清掉旧 bundle, 防止 view 短暂显示前一个客户的数据
    return () => {
      latestClientIdRef.current = null;
    };
  }, [doFetch, autoFetch]);

  return {
    bundle,
    isLoading,
    error,
    refresh: doFetch,
  };
}
