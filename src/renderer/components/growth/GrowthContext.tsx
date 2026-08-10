import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';

import { getGrowthOverview, refreshGrowthReadModels } from '../../lib/api';
import type { GrowthOverview } from '../../../shared/types';

const GROWTH_REFRESH_EVENT = 'yiyu:growth-refresh';

type GrowthContextValue = {
  growthOverview: GrowthOverview | null;
  isGrowthLoading: boolean;
  refreshGrowthOverview: () => Promise<GrowthOverview | null>;
};

const GrowthContext = createContext<GrowthContextValue | null>(null);

export function notifyGrowthRefresh() {
  window.dispatchEvent(new CustomEvent(GROWTH_REFRESH_EVENT));
}

export function GrowthProvider({ children }: { children: React.ReactNode }) {
  const [growthOverview, setGrowthOverview] = useState<GrowthOverview | null>(null);
  const [isGrowthLoading, setIsGrowthLoading] = useState(false);
  const mountedRef = useRef(true);

  const refreshGrowthOverview = useCallback(async () => {
    setIsGrowthLoading(true);
    try {
      // 成长陪伴 Agent 只在后台把已获授权的正式任务、会议和周复盘
      // 整理成可重建读模型；不暴露审批页，也不要求成员逐条确认。
      await refreshGrowthReadModels().catch((error) => {
        console.warn('Growth companion refresh is temporarily unavailable', error);
      });
      const nextOverview = await getGrowthOverview();
      if (mountedRef.current) {
        setGrowthOverview(nextOverview);
      }
      return nextOverview;
    } catch (error) {
      console.error('Failed to refresh growth overview', error);
      return null;
    } finally {
      if (mountedRef.current) {
        setIsGrowthLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    void refreshGrowthOverview();
    const handleRefresh = () => {
      void refreshGrowthOverview();
    };
    window.addEventListener(GROWTH_REFRESH_EVENT, handleRefresh);
    return () => {
      mountedRef.current = false;
      window.removeEventListener(GROWTH_REFRESH_EVENT, handleRefresh);
    };
  }, [refreshGrowthOverview]);

  const value = useMemo<GrowthContextValue>(
    () => ({
      growthOverview,
      isGrowthLoading,
      refreshGrowthOverview,
    }),
    [growthOverview, isGrowthLoading, refreshGrowthOverview],
  );

  return <GrowthContext.Provider value={value}>{children}</GrowthContext.Provider>;
}

export function useGrowthOverviewState() {
  return useContext(GrowthContext);
}
