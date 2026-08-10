import {
  Loader2,
  RefreshCw,
} from 'lucide-react';
import type {
  MaintenanceModeStatus,
} from '../../../shared/types';

type MaintenanceSyncPanelProps = {
  maintenanceModeStatus: MaintenanceModeStatus | null;
  maintenanceModeError: string | null;
  maintenanceModeLoading: boolean;
  maintenanceModeBusyAction: 'enter' | 'exit' | null;
  onRefreshMaintenanceMode: () => void;
  onEnterMaintenanceMode: () => void;
  onExitMaintenanceMode: () => void;
};

function statusLabel(status: MaintenanceModeStatus | null, error: string | null) {
  if (error) return error;
  if (!status) return '状态未加载';
  if (status.active) return '推送同步已打开';
  if (!status.available) return status.reason || '推送同步不可用';
  if (!status.canEnter) return status.reason || '当前账号没有维护权限';
  return '推送同步已关闭';
}

export function MaintenanceSyncPanel({
  maintenanceModeStatus,
  maintenanceModeError,
  maintenanceModeLoading,
  maintenanceModeBusyAction,
  onRefreshMaintenanceMode,
  onEnterMaintenanceMode,
  onExitMaintenanceMode,
}: MaintenanceSyncPanelProps) {
  const isActive = Boolean(maintenanceModeStatus?.active);
  const canEnter = Boolean(
    maintenanceModeStatus?.available
    && maintenanceModeStatus.canEnter
    && !maintenanceModeStatus.active,
  );

  const toggleDisabled = maintenanceModeLoading
    || maintenanceModeBusyAction !== null
    || (!isActive && !canEnter);
  const handleToggle = () => {
    if (isActive) {
      onExitMaintenanceMode();
    } else {
      onEnterMaintenanceMode();
    }
  };
  const isBusy = maintenanceModeBusyAction !== null;

  return (
    <div>
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3 min-w-0">
          <button
            type="button"
            onClick={handleToggle}
            disabled={toggleDisabled}
            title={isActive ? '点击关闭维护模式' : (canEnter ? '点击打开维护模式' : '当前账号无维护权限')}
            className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-emerald-500/30 ${
              isActive ? 'bg-emerald-500' : 'bg-gray-300'
            } ${toggleDisabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer hover:opacity-90'}`}
          >
            <span
              className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                isActive ? 'translate-x-6' : 'translate-x-1'
              }`}
            />
            {isBusy && (
              <Loader2 size={10} className="absolute right-1.5 top-1.5 animate-spin text-white" />
            )}
          </button>
          <div className="min-w-0">
            <p className="text-[13px] font-medium text-gray-900">维护模式</p>
            <p className="mt-0.5 text-[11px] text-gray-500 truncate">{statusLabel(maintenanceModeStatus, maintenanceModeError)}</p>
          </div>
        </div>
        <button
          type="button"
          onClick={onRefreshMaintenanceMode}
          disabled={maintenanceModeLoading || isBusy}
          className="shrink-0 inline-flex items-center gap-1.5 rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-[11px] font-medium text-gray-500 hover:bg-gray-50 disabled:opacity-50 transition-colors"
        >
          <RefreshCw size={12} className={maintenanceModeLoading ? 'animate-spin' : ''} />
          刷新
        </button>
      </div>
    </div>
  );
}

export default MaintenanceSyncPanel;
