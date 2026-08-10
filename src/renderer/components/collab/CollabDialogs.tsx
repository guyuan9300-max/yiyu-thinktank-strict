import React from 'react';
import {
  AlertCircle,
  CheckCircle2,
  Database,
  Download,
  Eye,
  Layers3,
  Play,
  RefreshCw,
  Square,
  UploadCloud,
  X,
} from 'lucide-react';
import type {
  CollabPreviewSession,
  PullPreview,
  PushPreview,
} from '../../../shared/types';

type PreviewMode = 'push' | 'pull';

type CollabPreviewDialogProps = {
  open: boolean;
  mode: PreviewMode;
  preview: PushPreview | PullPreview | null;
  selectedPaths: string[];
  message: string;
  errorMessage?: string | null;
  busy: boolean;
  activePreviewSession?: CollabPreviewSession | null;
  onClose: () => void;
  onSelectPullCommit?: (targetCommit: string | null) => void;
  onMessageChange: (nextValue: string) => void;
  onConfirm: () => void;
  onStartPreview?: (targetRef?: string, label?: string) => void;
  onStopPreview?: () => void;
};

function ActionButton({
  primary,
  disabled,
  className = '',
  onClick,
  children,
}: {
  primary?: boolean;
  disabled?: boolean;
  className?: string;
  onClick?: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center justify-center gap-2 rounded-2xl px-4 py-2.5 text-[13px] font-bold transition-all ${
        primary
          ? 'bg-[#5B7BFE] text-white shadow-[0_8px_24px_rgba(91,123,254,0.24)] hover:bg-[#4a6be6] disabled:cursor-not-allowed disabled:opacity-60'
          : 'border border-gray-200 bg-white text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60'
      } ${className}`}
    >
      {children}
    </button>
  );
}

function visibilityIcon(mode: 'visible' | 'mixed' | 'background') {
  if (mode === 'visible') return <Eye size={15} className="text-[#5B7BFE]" />;
  if (mode === 'mixed') return <Layers3 size={15} className="text-emerald-600" />;
  return <Database size={15} className="text-amber-600" />;
}

function visibilityText(mode: 'visible' | 'mixed' | 'background') {
  if (mode === 'visible') return '你能直接看到';
  if (mode === 'mixed') return '界面和行为都会受影响';
  return '主要影响后台/配置';
}

function formatCommitDate(value: string) {
  if (!value) return '未知时间';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date);
}

function isPullPreview(preview: PushPreview | PullPreview): preview is PullPreview {
  return 'remoteCommits' in preview;
}

export function CollabPreviewDialog({
  open,
  mode,
  preview,
  selectedPaths,
  message,
  errorMessage,
  busy,
  activePreviewSession,
  onClose,
  onSelectPullCommit,
  onMessageChange,
  onConfirm,
  onStartPreview,
  onStopPreview,
}: CollabPreviewDialogProps) {
  if (!open || !preview) return null;
  const selectedSet = new Set(selectedPaths);
  const pullPreview = isPullPreview(preview) ? preview : null;
  const actionLabel = mode === 'push' ? '推送我的修改到 main' : '预览远端修改';
  const noPushChanges = mode === 'push' && preview.executionBlockReason === '当前没有可提交的本地文件改动。';
  const pullMainIncludedButLocalAhead = mode === 'pull'
    && pullPreview
    && (pullPreview.status.behindCount || 0) === 0
    && (pullPreview.status.aheadCount || 0) > 0
    && pullPreview.files.length === 0;
  const alreadySynced = mode === 'pull'
    && (preview.executionBlockReason === 'main 当前已经是最新。' || Boolean(pullMainIncludedButLocalAhead));
  const confirmLabel = noPushChanges
    ? '当前没有要发布的修改'
    : pullMainIncludedButLocalAhead
      ? '远端已合入，本地待推'
      : alreadySynced
      ? '当前已经是最新版本'
      : mode === 'push'
        ? '安全推送到 main'
        : '快进接收 main';
  const confirmDisabled = busy
    || Boolean(preview.executionBlockReason)
    || (mode === 'pull' && !pullPreview?.canFastForwardMain);
  const effectExplanationText = '以下说明由软件根据提交标题、变更路径和有限差异推断，用来帮助先判断功能影响；它不是 AI 审查结果，技术文件只放在折叠证据里。';

  return (
    <div className="fixed inset-0 z-[80] overflow-y-auto bg-black/30 px-4 py-8 backdrop-blur-sm">
      <div className="mx-auto flex min-h-full items-center justify-center">
        <div className="flex max-h-[calc(100vh-4rem)] w-full max-w-6xl flex-col overflow-hidden rounded-[28px] border border-white/70 bg-white shadow-[0_24px_90px_rgba(15,23,42,0.16)]">
          <div className="flex items-start justify-between gap-4 border-b border-gray-100 px-6 py-5">
            <div>
              <p className="text-[11px] font-bold uppercase tracking-[0.2em] text-[#5B7BFE]">协作同步</p>
              <h2 className="mt-2 text-[22px] font-bold text-gray-900">{actionLabel}</h2>
              <p className="mt-2 text-[13px] leading-6 text-gray-500">
                当前仓库：{preview.status.repoName || '未命名仓库'} · 分支 {preview.status.branch || '未知'}
              </p>
            </div>
            <button
              type="button"
              className="rounded-2xl border border-gray-200 bg-white p-2 text-gray-400 transition hover:text-gray-700"
              onClick={busy ? undefined : onClose}
              aria-label="关闭协作预览"
            >
              <X size={18} />
            </button>
          </div>

          <div className="min-h-0 overflow-y-auto px-6 py-6">
            <div className="grid gap-6 lg:grid-cols-[1.25fr_0.75fr]">
              <div className="space-y-4">
                {preview.notice && (
                  <div className="rounded-2xl border border-blue-200 bg-blue-50 px-4 py-3 text-[12px] font-semibold leading-6 text-[#4256C5]">
                    <div className="flex items-start gap-2">
                      <AlertCircle size={16} className="mt-0.5 shrink-0" />
                      <span>{preview.notice}</span>
                    </div>
                  </div>
                )}

                {preview.executionBlockReason && (
                  <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-[12px] font-semibold leading-6 text-amber-800">
                    <div className="flex items-start gap-2">
                      <AlertCircle size={16} className="mt-0.5 shrink-0" />
                      <span>{preview.executionBlockReason}</span>
                    </div>
                  </div>
                )}

                {pullMainIncludedButLocalAhead && (
                  <div className="rounded-3xl border border-blue-100 bg-blue-50 px-4 py-4 text-[13px] leading-6 text-blue-900">
                    <p className="font-bold">远端 main 已经在本机，不需要再快进接收。</p>
                    <p className="mt-1 text-[12px] font-semibold text-blue-800">
                      当前不能点“快进接收”，是因为本地还有 {pullPreview?.status.aheadCount || 0} 个提交尚未推到 GitHub main。
                      这不是远端没拉下来，而是本机已经比远端更新。继续测试无误后，请使用“推 main”发布这些本地提交。
                    </p>
                  </div>
                )}

                {errorMessage && (
                  <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-[12px] font-semibold leading-6 text-rose-800">
                    <div className="flex items-start gap-2">
                      <AlertCircle size={16} className="mt-0.5 shrink-0" />
                      <span>{errorMessage}</span>
                    </div>
                  </div>
                )}

                {activePreviewSession && (
                  <div className="rounded-3xl border border-emerald-100 bg-emerald-50 px-4 py-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-emerald-700">协作预览模式运行中</p>
                        <p className="mt-2 text-[13px] font-semibold leading-6 text-emerald-900">{activePreviewSession.label}</p>
                        <p className="mt-1 break-all text-[11px] text-emerald-700">数据目录：{activePreviewSession.dataDir}</p>
                      </div>
                      <ActionButton disabled={busy} onClick={onStopPreview}>
                        <Square size={13} />
                        停止预览
                      </ActionButton>
                    </div>
                  </div>
                )}

                {!(mode === 'pull' && preview.files.length === 0 && preview.effects.length === 0) && (
                <div className="rounded-3xl border border-gray-100 bg-gray-50 px-4 py-4">
                  <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-gray-400">
                    {mode === 'push' ? '这次会推送这些变化' : '你会先看到这些变化'}
                  </p>
                  <p className="mt-2 text-[13px] leading-6 text-gray-600">
                    这里解释的是用户能感受到的功能变化、后台规则变化和架构风险。{mode === 'push' ? '推送会先接到最新 main，再安全推送；' : ''}文件清单只作为技术证据。
                  </p>
                  <div className="mt-3 rounded-2xl border border-blue-100 bg-blue-50 px-3 py-3 text-[12px] font-semibold leading-6 text-blue-800">
                    <div className="flex items-start gap-2">
                      <CheckCircle2 size={15} className="mt-0.5 shrink-0" />
                      <span>{effectExplanationText}</span>
                    </div>
                  </div>
                  <div className="mt-4 grid gap-3">
                    {preview.effects.map((effect) => {
                      const selectedCount = effect.relatedPaths.filter((targetPath) => selectedSet.has(targetPath)).length;
                      return (
                        <div
                          key={effect.id}
                          className="rounded-[24px] border border-[#5B7BFE]/20 bg-white px-4 py-4 shadow-[0_12px_28px_rgba(91,123,254,0.08)]"
                        >
                          <div className="flex flex-wrap items-start justify-between gap-3">
                            <div className="min-w-0 flex-1">
                              <div className="flex flex-wrap items-center gap-2">
                                {visibilityIcon(effect.visibility)}
                                <p className="text-[15px] font-bold text-gray-900">{effect.title}</p>
                                <span className="rounded-full bg-gray-50 px-2.5 py-1 text-[11px] font-semibold text-gray-500 ring-1 ring-gray-200">
                                  {effect.scopeLabel}
                                </span>
                                <span className="rounded-full bg-gray-50 px-2.5 py-1 text-[11px] font-semibold text-gray-500 ring-1 ring-gray-200">
                                  {visibilityText(effect.visibility)}
                                </span>
                                <span className="rounded-full bg-blue-50 px-2.5 py-1 text-[11px] font-semibold text-blue-700 ring-1 ring-blue-100">
                                  功能推断
                                </span>
                              </div>
                              <p className="mt-2 text-[13px] leading-6 text-gray-600">{effect.summary}</p>
                            </div>
                          </div>

                          {effect.details.length > 0 && (
                            <div className="mt-4 space-y-2">
                              {effect.details.map((detail) => (
                                <div key={detail} className="flex items-start gap-2 text-[12px] leading-6 text-gray-600">
                                  <span className="mt-[9px] h-1.5 w-1.5 rounded-full bg-[#5B7BFE]" />
                                  <span>{detail}</span>
                                </div>
                              ))}
                            </div>
                          )}

                          <p className="mt-4 text-[12px] text-gray-400">
                            这组功能变化对应 {effect.relatedPaths.length} 个底层文件{selectedCount > 0 ? `（当前预览覆盖 ${selectedCount} 个）` : ''}。
                          </p>
                        </div>
                      );
                    })}
                    {preview.effects.length === 0 && (
                      <div className="rounded-2xl border border-dashed border-gray-200 bg-white px-4 py-4 text-[13px] leading-6 text-gray-500">
                        这次改动还没有被翻译成直观的软件效果，暂时只能通过下方文件清单确认。
                      </div>
                    )}
                  </div>
                </div>
                )}

                {pullPreview && pullPreview.remoteCommits.length > 0 && (
                  <div className="rounded-3xl border border-gray-100 bg-white px-4 py-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-gray-400">origin/main 新提交</p>
                        <p className="mt-2 text-[13px] leading-6 text-gray-600">
                          这些是 main 上尚未进入本机的提交。只有安全快进时才允许直接接收。
                        </p>
                        {pullPreview.syncTargetLabel && (
                          <p className="mt-2 text-[12px] font-bold text-[#5B7BFE]">当前查看：{pullPreview.syncTargetLabel}</p>
                        )}
                      </div>
                      <ActionButton disabled={busy || !pullPreview.syncTargetCommit} onClick={() => onSelectPullCommit?.(null)}>
                        同步到最新 main
                      </ActionButton>
                    </div>
                    <div className="mt-4 space-y-2">
                      {pullPreview.remoteCommits.map((commit, index) => {
                        const isLatest = index === pullPreview.remoteCommits.length - 1;
                        const isActive = pullPreview.syncTargetCommit ? pullPreview.syncTargetCommit === commit.hash : isLatest;
                        return (
                          <button
                            key={commit.hash}
                            type="button"
                            disabled={busy || isActive}
                            onClick={() => onSelectPullCommit?.(commit.hash)}
                            className={`w-full rounded-2xl border px-3 py-3 text-left transition ${
                              isActive
                                ? 'border-[#5B7BFE]/30 bg-[#5B7BFE]/[0.06] shadow-[0_10px_24px_rgba(91,123,254,0.10)]'
                                : 'border-gray-100 bg-gray-50 hover:border-[#5B7BFE]/30 hover:bg-white'
                            } disabled:cursor-default`}
                          >
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="rounded-full bg-white px-2.5 py-1 text-[11px] font-bold text-gray-700 ring-1 ring-gray-200">
                                {formatCommitDate(commit.authoredAt)}
                              </span>
                              <span className="rounded-full bg-white px-2.5 py-1 text-[11px] font-bold text-[#5B7BFE] ring-1 ring-[#5B7BFE]/20">
                                {commit.shortHash}
                              </span>
                              {isLatest && (
                                <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-[11px] font-bold text-emerald-700 ring-1 ring-emerald-100">
                                  最新
                                </span>
                              )}
                              {isActive && (
                                <span className="rounded-full bg-[#5B7BFE] px-2.5 py-1 text-[11px] font-bold text-white">
                                  当前查看
                                </span>
                              )}
                            </div>
                            <p className="mt-2 text-[13px] font-bold leading-6 text-gray-900">{commit.subject || '未填写提交说明'}</p>
                            <p className="mt-1 text-[12px] leading-5 text-gray-500">
                              {commit.identityLabel} · {commit.sourceLabel} · {commit.fileCount} 个文件
                            </p>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}

                {pullPreview && (pullPreview.remoteBranches || []).length > 0 && (
                  <div className="rounded-3xl border border-gray-100 bg-white px-4 py-4">
                    <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-gray-400">协作分支</p>
                    <p className="mt-2 text-[13px] leading-6 text-gray-600">
                      协作分支只用于看对方发布的修改，不会自动合并到本机 main。点“开启隔离预览窗口”会另起一个临时软件窗口，正式源码和正式数据库都不变。
                    </p>
                    <div className="mt-4 space-y-2">
                      {(pullPreview.remoteBranches || []).map((branch) => (
                        <div key={branch.ref} className="rounded-2xl border border-gray-100 bg-gray-50 px-3 py-3">
                          <div className="flex flex-wrap items-start justify-between gap-3">
                            <div className="min-w-0 flex-1">
                              <p className="break-all text-[13px] font-bold text-gray-900">{branch.branchName}</p>
                              <p className="mt-1 text-[12px] leading-5 text-gray-500">
                                {branch.shortHash} · {formatCommitDate(branch.authoredAt)} · {branch.fileCount} 个文件
                              </p>
                              {branch.subject && <p className="mt-1 text-[12px] leading-5 text-gray-500">{branch.subject}</p>}
                            </div>
                            <ActionButton disabled={busy} onClick={() => onStartPreview?.(branch.ref, branch.branchName)}>
                              <Play size={13} />
                              开启隔离预览窗口
                            </ActionButton>
                            <p className="w-full text-right text-[11px] leading-5 text-gray-400">
                              另起临时软件看效果，不改正式代码/数据库。
                            </p>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                <details className="rounded-3xl border border-gray-100 bg-white open:shadow-sm">
                  <summary className="cursor-pointer list-none px-4 py-4">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-gray-400">涉及文件</p>
                        <p className="mt-1 text-[12px] text-gray-500">这里只是技术证据，不再逐文件取消或自动解决冲突。</p>
                      </div>
                      <span className="rounded-full bg-[#5B7BFE]/10 px-3 py-1 text-[12px] font-bold text-[#5B7BFE]">
                        {preview.files.length} 个文件
                      </span>
                    </div>
                  </summary>
                  <div className="border-t border-gray-100 px-4 py-3">
                    <div className="space-y-3">
                      {preview.files.map((file) => (
                        <div
                          key={file.path}
                          className="block rounded-2xl border border-gray-100 bg-gray-50/80 px-4 py-3"
                        >
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="break-all text-[13px] font-bold text-gray-900">{file.path}</p>
                            <span className="rounded-full bg-white px-2.5 py-1 text-[11px] font-semibold text-gray-500 ring-1 ring-gray-200">
                              {file.groupLabel}
                            </span>
                            <span className="rounded-full bg-white px-2.5 py-1 text-[11px] font-semibold text-gray-500 ring-1 ring-gray-200">
                              {file.summary}
                            </span>
                          </div>
                          {file.risk && (
                            <div className="mt-3 rounded-2xl border border-amber-100 bg-amber-50 px-3 py-3">
                              <div className="flex items-start gap-2 text-[12px] font-semibold text-amber-800">
                                <AlertCircle size={15} className="mt-0.5 shrink-0" />
                                <span>{file.risk.message}</span>
                              </div>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                </details>
              </div>

              <div className="space-y-4">
                <div className="rounded-3xl border border-gray-100 bg-gray-50 px-4 py-4">
                  <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-gray-400">本次主要修改</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {preview.groups.map((group) => (
                      <span
                        key={group.key}
                        className="rounded-full border border-gray-200 bg-white px-3 py-1.5 text-[12px] font-bold text-gray-700"
                      >
                        {group.label} · {group.fileCount}
                      </span>
                    ))}
                    {preview.groups.length === 0 && (
                      <span className="text-[12px] text-gray-400">当前没有可操作的文件。</span>
                    )}
                  </div>
                </div>

                {mode === 'push' && (
                  <div className="rounded-3xl border border-gray-100 bg-gray-50 px-4 py-4">
                    <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-gray-400">提交说明</p>
                    <textarea
                      value={message}
                      onChange={(event) => onMessageChange(event.target.value)}
                      disabled={busy}
                      rows={4}
                      className="mt-3 w-full rounded-2xl border border-gray-200 bg-white px-4 py-3 text-[13px] font-semibold text-gray-700 outline-none transition focus:border-[#5B7BFE] focus:ring-2 focus:ring-[#5B7BFE]/20"
                      placeholder={preview.suggestedMessage}
                    />
                    <p className="mt-2 text-[12px] text-gray-400">确认后会优先安全推送到 GitHub main；若远端变化无法自动整合，会停止并提示用预览或协作分支兜底。</p>
                  </div>
                )}

                <div className="rounded-3xl border border-gray-100 bg-white px-4 py-4">
                  <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-gray-400">执行前提醒</p>
                  <div className="mt-3 space-y-3 text-[12px] leading-6 text-gray-600">
                    <div className="flex items-start gap-2">
                      <CheckCircle2 size={15} className="mt-1 shrink-0 text-emerald-500" />
                      <span>现在先看“用户功能会怎么变”，文件清单只作为第二层证据。</span>
                    </div>
                    <div className="flex items-start gap-2">
                      <CheckCircle2 size={15} className="mt-1 shrink-0 text-emerald-500" />
                      <span>{mode === 'push' ? '推送会优先进入 main；遇到真实冲突或异常时会停住，不覆盖对方修改。' : '只有本地干净且 main 可快进时，才允许直接接收。'}</span>
                    </div>
                    <div className="flex items-start gap-2">
                      <AlertCircle size={15} className="mt-1 shrink-0 text-amber-500" />
                      <span>复杂修改不在软件内自动合并；请用预览模式查看，或交给 Codex/Claude 收口。</span>
                    </div>
                    {pullPreview?.directReceiveBlockReason && (
                      <div className="flex items-start gap-2">
                        <AlertCircle size={15} className="mt-1 shrink-0 text-amber-500" />
                        <span>{pullPreview.directReceiveBlockReason}</span>
                      </div>
                    )}
                  </div>
                </div>

                {mode === 'pull' && (
                  <div className="rounded-3xl border border-emerald-100 bg-emerald-50 px-4 py-4">
                    <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-emerald-700">开启隔离预览窗口</p>
                    <p className="mt-2 text-[12px] font-semibold leading-6 text-emerald-900">
                      这会另起一个临时软件窗口，用来肉眼查看对方修改后的界面和功能。正式源码、正式数据库不会改变，预览环境也默认禁止云端写入。
                    </p>
                  </div>
                )}

                <div className="rounded-3xl border border-gray-100 bg-white px-4 py-4">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <p className="text-[11px] font-bold uppercase tracking-[0.18em] text-gray-400">确认执行</p>
                      <p className="mt-1 text-[12px] text-gray-500">
                        {mode === 'push'
                          ? `将把 ${preview.files.length} 个底层文件对应的修改安全推送到 main。`
                          : pullPreview?.canFastForwardMain
                            ? '当前可安全快进接收 origin/main。'
                            : '当前不能直接接收，可开启隔离预览。'}
                      </p>
                    </div>
                    {mode === 'push' ? <UploadCloud size={18} className="text-[#5B7BFE]" /> : <Download size={18} className="text-[#5B7BFE]" />}
                  </div>
                  <div className="mt-4 flex flex-wrap justify-end gap-3">
                    <ActionButton onClick={busy ? undefined : onClose}>取消</ActionButton>
                    {mode === 'pull' && (
                      <ActionButton disabled={busy} onClick={() => onStartPreview?.()}>
                        <Play size={14} />
                        开启隔离预览窗口
                      </ActionButton>
                    )}
                    <ActionButton primary disabled={confirmDisabled} onClick={onConfirm}>
                      {busy ? <RefreshCw size={14} className="animate-spin" /> : mode === 'push' ? <UploadCloud size={14} /> : <Download size={14} />}
                      {confirmLabel}
                    </ActionButton>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
