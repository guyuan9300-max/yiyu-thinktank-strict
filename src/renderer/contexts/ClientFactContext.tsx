/**
 * v2.2 Phase 1 F1.6 · ClientFactContext
 *
 * 服务: V2.2_NORTH_STAR.md N1/N2
 *  - N1 (功能顺畅): 多个 view 共享同一份 client bundle, 消除 B 方案 #9 多池并列
 *  - N2 (数据中心入口统一): 所有 view 读 client 维度数据走 L2 ClientFactBundle 单一通道
 *
 * 设计:
 * - 包一层 Provider 在 App 顶部, 注入当前 currentClientId
 * - 各 view 通过 useClientContext() 拿到当前 bundle, 不再重复 fetch
 * - 切换 client 时所有接入 view 自动同步刷新
 * - 任何 view 触发数据变更后调 refresh(), 全局重新拉
 *
 * N3 (3.0 接入预留): 接口稳定, 后续加 actor_type / verification_status 不破坏调用方
 *
 * 用法:
 *   // App.tsx 顶部
 *   <ClientFactProvider currentClientId={currentClientId}>
 *     <YourViews />
 *   </ClientFactProvider>
 *
 *   // 任意 view
 *   const { bundle, isLoading, error, refresh } = useClientContext();
 *   if (!bundle) return null;
 *   return <div>{bundle.client.name}</div>;
 */
import React, { createContext, useContext, useMemo } from 'react';

import { useClientFact, type ClientFactState } from '../hooks/useClientFact';
import type { ClientFactBundle } from '../lib/clientFactTypes';

export interface ClientFactContextValue {
  /** 当前 client id (Provider 注入). null/empty 时 bundle 为 null */
  currentClientId: string | null;
  /** L2 client bundle (跨表合并好的客户事实) */
  bundle: ClientFactBundle | null;
  isLoading: boolean;
  error: Error | null;
  /** 任何 view 改了 client 数据后调它, 全局所有接入 view 重新拉 */
  refresh: () => Promise<void>;
}

const DEFAULT_VALUE: ClientFactContextValue = {
  currentClientId: null,
  bundle: null,
  isLoading: false,
  error: null,
  refresh: async () => undefined,
};

const ClientFactContext = createContext<ClientFactContextValue>(DEFAULT_VALUE);

export interface ClientFactProviderProps {
  currentClientId: string | null;
  /** 是否自动随 currentClientId 变化重新拉. 默认 true */
  autoFetch?: boolean;
  /** 是否包含 archived (归档) 客户. 默认 false */
  includeArchived?: boolean;
  /** lite 模式 — 只要基础字段, 不展开 atomic_facts / dna_documents */
  lite?: boolean;
  children: React.ReactNode;
}

/**
 * v2.2 F1.6 · ClientFactProvider
 *
 * 通常在 App.tsx 顶部包一次, currentClientId 跟 App.tsx 已有的 useState 同步即可。
 * 不破坏现有 App.tsx 60+ useState 模式, 只新增一条统一数据通道。
 */
export function ClientFactProvider({
  currentClientId,
  autoFetch = true,
  includeArchived,
  lite,
  children,
}: ClientFactProviderProps) {
  const state: ClientFactState = useClientFact({
    clientId: currentClientId,
    autoFetch,
    includeArchived,
    lite,
  });

  const value = useMemo<ClientFactContextValue>(
    () => ({
      currentClientId: currentClientId || null,
      bundle: state.bundle,
      isLoading: state.isLoading,
      error: state.error,
      refresh: state.refresh,
    }),
    [currentClientId, state.bundle, state.isLoading, state.error, state.refresh],
  );

  return (
    <ClientFactContext.Provider value={value}>
      {children}
    </ClientFactContext.Provider>
  );
}

/**
 * v2.2 F1.6 · useClientContext
 *
 * 在 ClientFactProvider 包住的子树里调, 拿当前 client bundle。
 * 跨 view 一致性保证: 所有 useClientContext() 拿到同一引用 (单例 hook)。
 *
 * 没有 Provider 包住时返回 DEFAULT_VALUE (bundle = null), 不抛错 —
 * 这是为了让接入 view 可以渐进迁移, 不强制 App 顶部立即包 Provider。
 */
export function useClientContext(): ClientFactContextValue {
  return useContext(ClientFactContext);
}
