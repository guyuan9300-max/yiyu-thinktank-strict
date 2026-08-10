import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  MessageSquarePlus,
  ChevronDown,
  RefreshCw,
  Send,
  CheckCircle2,
  Upload,
  AlertCircle,
} from 'lucide-react';
import type { DesktopAppInfo } from '../../../shared/types';
import {
  listSoftwareFeedback,
  submitSoftwareFeedback,
  type SoftwareFeedbackItem,
  type SoftwareFeedbackSeverity,
} from '../../lib/api';

type FeedbackKind = 'bug' | 'suggestion';

const KIND_OPTIONS: ReadonlyArray<{ key: FeedbackKind; label: string; helper: string }> = [
  { key: 'bug', label: '软件 bug / 使用异常', helper: '报错、卡顿、闪退、功能没反应、结果明显不对' },
  { key: 'suggestion', label: '功能开发建议', helper: '希望新增能力、优化流程、调整使用方式' },
];

const SEVERITIES: ReadonlyArray<{ key: SoftwareFeedbackSeverity; label: string }> = [
  { key: 'medium', label: '一般' },
  { key: 'high', label: '影响使用' },
  { key: 'critical', label: '用不了' },
];

interface ChipProps {
  active: boolean;
  label: string;
  helper?: string;
  onClick: () => void;
}

function Chip({ active, label, helper, onClick }: ChipProps): React.ReactElement {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        'rounded-md border px-3 py-2 text-left text-[12px] transition ' +
        (active
          ? 'border-[#5B7BFE] bg-[#5B7BFE]/8 font-medium text-[#3A53C5]'
          : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50')
      }
    >
      <span className="block">{label}</span>
      {helper ? <span className="mt-0.5 block text-[11px] font-normal text-gray-400">{helper}</span> : null}
    </button>
  );
}

interface FeedbackSectionProps {
  desktopAppInfo: DesktopAppInfo | null;
}

function formatFeedbackDate(value: string): string {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value.slice(0, 10);
  return parsed.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}

function feedbackCategoryLabel(category: string): string {
  return category === 'suggestion' ? '功能建议' : 'bug / 异常';
}

function feedbackStatusLabel(item: SoftwareFeedbackItem): { text: string; tone: string } {
  if (item.centralStatus === 'not_connected') {
    return { text: '已存组织 · 中心未连接', tone: 'bg-amber-50 text-amber-700' };
  }
  if (item.queued) return { text: '待联网提交', tone: 'bg-amber-50 text-amber-700' };
  if (item.resolutionNote) return { text: '已回复', tone: 'bg-emerald-50 text-emerald-700' };
  return { text: '已提交', tone: 'bg-slate-100 text-slate-600' };
}

export function FeedbackSection({ desktopAppInfo }: FeedbackSectionProps): React.ReactElement {
  const [formOpen, setFormOpen] = useState(false);
  const [kind, setKind] = useState<FeedbackKind>('bug');
  const [severity, setSeverity] = useState<SoftwareFeedbackSeverity>('medium');
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [screenshot, setScreenshot] = useState<File | null>(null);
  const [contextOpen, setContextOpen] = useState(false);
  const [submitState, setSubmitState] = useState<'idle' | 'submitting' | 'submitted' | 'queued' | 'blocked'>('idle');
  const [submitError, setSubmitError] = useState('');
  const [listOpen, setListOpen] = useState(false);
  const [feedbackItems, setFeedbackItems] = useState<SoftwareFeedbackItem[]>([]);
  const [feedbackLoading, setFeedbackLoading] = useState(false);
  const [feedbackError, setFeedbackError] = useState('');
  const [centralError, setCentralError] = useState('');

  const versionLabel = desktopAppInfo?.appVersion ?? '未知';
  const platformLabel = desktopAppInfo ? `${desktopAppInfo.platform} · ${desktopAppInfo.arch}` : '未知';
  const deviceInfo = useMemo(() => {
    const userAgent = typeof navigator !== 'undefined' ? navigator.userAgent : '';
    return [platformLabel, userAgent].filter(Boolean).join(' · ');
  }, [platformLabel]);

  const resetForm = () => {
    setKind('bug');
    setSeverity('medium');
    setTitle('');
    setDescription('');
    setScreenshot(null);
    setContextOpen(false);
    setSubmitState('idle');
    setSubmitError('');
  };

  const loadFeedback = useCallback(async () => {
    setFeedbackLoading(true);
    setFeedbackError('');
    try {
      const response = await listSoftwareFeedback();
      setFeedbackItems(response.items || []);
      setCentralError(response.centralError || '');
    } catch (error) {
      const message = error instanceof Error ? error.message : '反馈列表加载失败';
      setFeedbackError(message);
    } finally {
      setFeedbackLoading(false);
    }
  }, []);

  useEffect(() => {
    if (listOpen) void loadFeedback();
  }, [listOpen, loadFeedback]);

  const handleScreenshotChange = (file: File | null) => {
    setSubmitError('');
    if (!file) {
      setScreenshot(null);
      return;
    }
    const allowedMime = ['image/png', 'image/jpeg', 'image/jpg', 'image/webp'];
    const allowedExt = ['.png', '.jpg', '.jpeg', '.webp'];
    const lowerName = file.name.toLowerCase();
    const hasAllowedExt = allowedExt.some((ext) => lowerName.endsWith(ext));
    if (!allowedMime.includes(file.type) && !hasAllowedExt) {
      setSubmitError('截图仅支持 png / jpg / webp。');
      return;
    }
    if (file.size > 6 * 1024 * 1024) {
      setSubmitError('截图不能超过 6MB。');
      return;
    }
    setScreenshot(file);
  };

  const handleSubmit = async () => {
    const cleanTitle = title.trim();
    if (!cleanTitle || submitState === 'submitting') return;
    setSubmitState('submitting');
    setSubmitError('');
    try {
      const response = await submitSoftwareFeedback({
        category: kind,
        severity: kind === 'suggestion' ? 'low' : severity,
        title: cleanTitle,
        description,
        appVersion: desktopAppInfo?.appVersion ?? null,
        platform: desktopAppInfo ? `${desktopAppInfo.platform}/${desktopAppInfo.arch}` : null,
        pageRoute: 'settings/feedback',
        deviceInfo,
        screenshot,
      });
      setSubmitState(
        response.record.centralStatus === 'not_connected'
          ? 'blocked'
          : (response.queued ? 'queued' : 'submitted'),
      );
      setListOpen(true);
      await loadFeedback();
    } catch (error) {
      const message = error instanceof Error ? error.message : '反馈提交失败';
      setSubmitError(message);
      setSubmitState('idle');
    }
  };

  const submitMessage = submitState === 'blocked'
    ? '反馈和截图已保存在当前组织；中心反馈平台尚未连接，本软件不会把未投递显示为已提交。'
    : submitState === 'queued'
      ? '反馈已进入可投递队列，稍后会自动重试。感谢你的帮助。'
      : '反馈已提交，我们会用于后续问题修复和产品优化。感谢你的帮助。';

  return (
    <div className="rounded-2xl border border-gray-100 bg-white p-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-gray-400">FEEDBACK</p>
          <h3 className="mt-2 text-[18px] font-light tracking-tight text-gray-900">反馈与建议</h3>
          <p className="mt-1.5 text-[12px] leading-relaxed text-gray-500">
            报错、异常或功能想法都可以直接告诉我们。提交时会自动带上版本、页面和最近脱敏错误日志帮助定位。
          </p>
        </div>
        {!formOpen && (
          <button
            type="button"
            onClick={() => setFormOpen(true)}
            className="inline-flex shrink-0 items-center gap-2 rounded-md bg-[#5B7BFE] px-4 py-2 text-[13px] font-medium text-white hover:bg-[#4A6AEF]"
          >
            <MessageSquarePlus size={14} />
            写一条反馈
          </button>
        )}
      </div>

      {formOpen && submitState !== 'submitted' && submitState !== 'queued' && submitState !== 'blocked' && (
        <div className="mt-5 space-y-4">
          <div>
            <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-gray-400">类型</p>
            <div className="mt-2 grid gap-2 md:grid-cols-2">
              {KIND_OPTIONS.map((item) => (
                <Chip
                  key={item.key}
                  active={kind === item.key}
                  label={item.label}
                  helper={item.helper}
                  onClick={() => setKind(item.key)}
                />
              ))}
            </div>
          </div>

          {kind === 'bug' && (
            <div>
              <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-gray-400">影响程度</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {SEVERITIES.map((item) => (
                  <Chip key={item.key} active={severity === item.key} label={item.label} onClick={() => setSeverity(item.key)} />
                ))}
              </div>
            </div>
          )}

          <div>
            <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-gray-400">一句话标题</p>
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder={kind === 'bug' ? '例如：点击检查更新后长时间没有结果' : '例如：希望支持按组织批量导出反馈'}
              className="mt-2 w-full rounded-md border border-gray-200 px-3 py-2 text-[13px] text-gray-800 outline-none focus:border-[#5B7BFE]"
            />
          </div>

          <div>
            <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-gray-400">详细描述</p>
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              rows={3}
              placeholder={kind === 'bug' ? '发生了什么、怎么复现、期望是什么。' : '你希望软件帮你完成什么事，最好放在哪个入口。'}
              className="mt-2 w-full resize-none rounded-md border border-gray-200 px-3 py-2 text-[13px] text-gray-800 outline-none focus:border-[#5B7BFE]"
            />
          </div>

          <div className="rounded-lg border border-gray-100 bg-gray-50/60 p-3">
            <button
              type="button"
              onClick={() => setContextOpen((value) => !value)}
              className="flex w-full items-center justify-between text-left"
            >
              <span className="text-[12px] text-gray-600">将自动附带：版本 {versionLabel} · 系统 · 当前页面 · 最近脱敏错误日志</span>
              <ChevronDown size={14} className={`shrink-0 text-gray-400 transition ${contextOpen ? 'rotate-180' : ''}`} />
            </button>
            {contextOpen && (
              <dl className="mt-3 grid grid-cols-2 gap-2 text-[12px]">
                <div><dt className="text-gray-400">版本</dt><dd className="text-gray-700">{versionLabel}</dd></div>
                <div><dt className="text-gray-400">系统</dt><dd className="text-gray-700">{platformLabel}</dd></div>
                <div><dt className="text-gray-400">当前页面</dt><dd className="text-gray-700">系统设置 / 反馈与建议</dd></div>
                <div><dt className="text-gray-400">错误日志</dt><dd className="text-gray-700">仅最近 ERROR/WARN 摘要</dd></div>
              </dl>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <label className="inline-flex cursor-pointer items-center gap-2 rounded-md border border-gray-200 bg-white px-3 py-2 text-[12px] text-gray-600 hover:bg-gray-50">
                <Upload size={13} className="text-gray-400" />
                {screenshot ? '更换截图' : '可选上传截图'}
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  className="hidden"
                  onChange={(event) => handleScreenshotChange(event.target.files?.[0] || null)}
                />
              </label>
              {screenshot ? (
                <button type="button" onClick={() => setScreenshot(null)} className="text-[12px] text-gray-400 hover:text-gray-600">
                  {screenshot.name} · 移除
                </button>
              ) : null}
            </div>
            <p className="mt-2 flex items-center gap-1.5 text-[11px] text-gray-400">
              <AlertCircle size={12} />
              请勿上传含敏感客户资料的截图。日志会先脱敏再提交。
            </p>
          </div>

          {submitError ? <p className="rounded-md bg-rose-50 px-3 py-2 text-[12px] text-rose-700">{submitError}</p> : null}

          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => void handleSubmit()}
              disabled={!title.trim() || submitState === 'submitting'}
              className="inline-flex items-center gap-2 rounded-md bg-[#5B7BFE] px-4 py-2 text-[13px] font-medium text-white hover:bg-[#4A6AEF] disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Send size={14} />
              {submitState === 'submitting' ? '提交中...' : '提交'}
            </button>
            <button
              type="button"
              onClick={() => { setFormOpen(false); resetForm(); }}
              className="text-[12px] text-gray-400 hover:text-gray-600"
            >
              取消
            </button>
          </div>
        </div>
      )}

      {formOpen && (submitState === 'submitted' || submitState === 'queued' || submitState === 'blocked') && (
        <div className="mt-5 flex items-start gap-2 rounded-md bg-emerald-50 px-3 py-3 text-[12px] text-emerald-700">
          <CheckCircle2 size={14} className="mt-[2px] shrink-0" />
          <div>
            <p className="font-medium">{submitMessage}</p>
            <button
              type="button"
              onClick={resetForm}
              className="mt-2 text-[12px] font-medium text-emerald-700 underline underline-offset-2"
            >
              再写一条
            </button>
          </div>
        </div>
      )}

      <div className="mt-6 border-t border-gray-100 pt-4">
        <button
          type="button"
          onClick={() => setListOpen((value) => !value)}
          className="flex w-full items-center justify-between text-left"
        >
          <span className="text-[13px] font-medium text-gray-700">我的反馈{feedbackItems.length ? ` · ${feedbackItems.length}` : ''}</span>
          <ChevronDown size={15} className={`text-gray-400 transition ${listOpen ? 'rotate-180' : ''}`} />
        </button>
        {listOpen && (
          <div className="mt-3 space-y-3">
            <div className="flex items-center justify-between">
              <p className="text-[11px] text-gray-400">{centralError ? '中央反馈后台暂不可用，已显示本地待发送反馈。' : '这里只显示你自己提交的反馈。'}</p>
              <button
                type="button"
                onClick={() => void loadFeedback()}
                disabled={feedbackLoading}
                className="inline-flex items-center gap-1.5 rounded-md border border-gray-200 px-2.5 py-1.5 text-[12px] text-gray-500 hover:bg-gray-50 disabled:opacity-50"
              >
                <RefreshCw size={12} className={feedbackLoading ? 'animate-spin' : ''} />
                刷新
              </button>
            </div>
            {feedbackError ? <p className="rounded-md bg-rose-50 px-3 py-2 text-[12px] text-rose-700">{feedbackError}</p> : null}
            {!feedbackLoading && feedbackItems.length === 0 ? (
              <p className="rounded-lg border border-dashed border-gray-200 px-3 py-4 text-center text-[12px] text-gray-400">暂无反馈</p>
            ) : null}
            <ul className="space-y-2">
              {feedbackItems.map((item) => {
                const status = feedbackStatusLabel(item);
                return (
                  <li key={`${item.id}-${item.localOutboxId || ''}`} className="rounded-lg border border-gray-100 px-3 py-2.5">
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-[13px] text-gray-800">{item.title}</p>
                        <p className="mt-0.5 text-[11px] text-gray-400">
                          {feedbackCategoryLabel(item.category)} · {formatFeedbackDate(item.createdAt)}
                        </p>
                      </div>
                      <span className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-medium ${status.tone}`}>
                        {status.text}
                      </span>
                    </div>
                    {item.resolutionNote ? (
                      <p className="mt-2 rounded-md bg-emerald-50 px-3 py-2 text-[12px] leading-relaxed text-emerald-700">
                        {item.resolutionNote}
                      </p>
                    ) : null}
                    {item.queued && item.lastError ? (
                      <p className="mt-2 text-[11px] text-amber-600">上次提交未成功，联网后会自动重试。</p>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
