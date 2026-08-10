import { Download, FolderOpen, RefreshCw, UploadCloud } from 'lucide-react';
import type { CollabRepoStatus } from '../../../shared/types';

type CollabSyncCardProps = {
  collapsed: boolean;
  status: CollabRepoStatus | null;
  loading: boolean;
  busyAction: 'push' | 'pull' | 'rebuild' | null;
  onRevealRepo: () => void;
  onPreviewPush: () => void;
  onPreviewPull: () => void;
};

function shortStatusText(status: CollabRepoStatus | null) {
  if (!status?.isConfigured) return '未绑定源码目录';
  if (!status.isValid) return '目录无效';
  const localChanges = status.localChangeCount || 0;
  const behind = status.behindCount || 0;
  const ahead = status.aheadCount || 0;
  if (localChanges === 0 && behind === 0 && ahead === 0) return '同步';
  const parts: string[] = [];
  if (localChanges > 0) parts.push(`本地 ${localChanges} 改动`);
  if (behind > 0) parts.push(`远端领先 ${behind}`);
  if (ahead > 0) parts.push(`本地未推 ${ahead}`);
  return parts.join(' · ');
}

export function CollabSyncCard({
  collapsed,
  status,
  loading,
  busyAction,
  onRevealRepo,
  onPreviewPush,
  onPreviewPull,
}: CollabSyncCardProps) {
  const actionDisabled = loading || busyAction !== null;
  const localChanges = status?.localChangeCount || 0;
  const behind = status?.behindCount || 0;
  const ahead = status?.aheadCount || 0;
  const isSynced = status?.isConfigured && status?.isValid && localChanges === 0 && behind === 0 && ahead === 0;
  const dotCls = !status?.isConfigured || !status?.isValid
    ? 'bg-gray-300'
    : localChanges > 0 || behind > 0 || ahead > 0
      ? 'bg-amber-500'
      : 'bg-emerald-500';

  if (collapsed) {
    return (
      <div className="px-2 mt-4 hidden md:block">
        <div className="border-t border-gray-100 pt-3 flex flex-col items-center gap-1">
          <span className={`mb-1 inline-block h-[6px] w-[6px] rounded-full ${dotCls}`} title={shortStatusText(status)} />
          <button
            type="button"
            className="w-9 h-9 inline-flex items-center justify-center rounded-md text-gray-500 hover:text-[#5B7BFE] hover:bg-gray-50 disabled:opacity-40 transition-colors"
            onClick={onPreviewPush}
            disabled={actionDisabled}
            title="安全推送我的修改到 main"
          >
            {busyAction === 'push' ? <RefreshCw size={14} className="animate-spin" /> : <UploadCloud size={14} />}
          </button>
          <button
            type="button"
            className="w-9 h-9 inline-flex items-center justify-center rounded-md text-gray-500 hover:text-[#5B7BFE] hover:bg-gray-50 disabled:opacity-40 transition-colors"
            onClick={onPreviewPull}
            disabled={actionDisabled}
            title="预览 main 和协作分支修改"
          >
            {busyAction === 'pull' ? <RefreshCw size={14} className="animate-spin" /> : <Download size={14} />}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="px-3 mt-5 hidden md:block">
      <div className="border-t border-gray-100 pt-4 space-y-3">
        {/* eyebrow + 仓库名 */}
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">COLLAB · 协作同步</p>
          <p className="mt-1.5 text-[12px] font-medium text-gray-900 truncate">
            {status?.repoName || '未绑定源码目录'}
          </p>
        </div>

        {/* 状态行:dot + branch + 短文字 */}
        {status?.isConfigured && status?.isValid && (
          <div className="flex items-center gap-2 text-[11px] text-gray-500">
            <span className={`inline-block h-[6px] w-[6px] rounded-full shrink-0 ${dotCls}`} />
            <span className="truncate">
              <span className="text-gray-700">{status.branch || 'main'}</span>
              <span className="mx-1 text-gray-300">·</span>
              <span>{isSynced ? '同步' : shortStatusText(status)}</span>
            </span>
          </div>
        )}

        {/* 推荐目录提示 */}
        {!status?.isConfigured && status?.suggestedRepoPath && (
          <p className="text-[10.5px] text-gray-400 leading-4 break-all">
            检测到 <span className="text-gray-600">{status.suggestedRepoPath.split('/').pop()}</span>,点下方按钮开始绑定。
          </p>
        )}

        {/* 在 Finder 中显示 */}
        {status?.repoPath && (
          <button
            type="button"
            className="inline-flex items-center gap-1 text-[10.5px] text-gray-400 hover:text-[#5B7BFE] transition-colors"
            onClick={onRevealRepo}
          >
            <FolderOpen size={11} />
            在 Finder 中显示
          </button>
        )}

        {/* 两个 ghost 按钮 */}
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            className="flex-1 inline-flex items-center justify-center gap-1 rounded-md border border-gray-200 bg-white px-2 py-1.5 text-[11px] font-medium text-gray-700 hover:bg-gray-50 hover:border-gray-300 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            onClick={onPreviewPush}
            disabled={actionDisabled}
          >
            {busyAction === 'push' ? <RefreshCw size={11} className="animate-spin" /> : <UploadCloud size={11} />}
            推 main
          </button>
          <button
            type="button"
            className="flex-1 inline-flex items-center justify-center gap-1 rounded-md border border-gray-200 bg-white px-2 py-1.5 text-[11px] font-medium text-gray-700 hover:bg-gray-50 hover:border-gray-300 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            onClick={onPreviewPull}
            disabled={actionDisabled}
          >
            {busyAction === 'pull' ? <RefreshCw size={11} className="animate-spin" /> : <Download size={11} />}
            预览
          </button>
        </div>
      </div>
    </div>
  );
}
