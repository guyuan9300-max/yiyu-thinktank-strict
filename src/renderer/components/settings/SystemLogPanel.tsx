import { useCallback, useEffect, useState } from 'react';
import {
  AlertCircle,
  AlertTriangle,
  Calendar,
  ClipboardCopy,
  Download,
  Info,
  Loader2,
  RefreshCw,
  Search,
  Send,
} from 'lucide-react';
import {
  getSystemLogs,
  exportSystemLogs,
  type SystemLogEntry,
} from '../../lib/api';
import { formatDateInputValue } from '../../../shared/taskTime';

function levelIcon(level: string) {
  if (level === 'ERROR') return <AlertCircle size={12} className="text-red-500" />;
  if (level === 'WARN') return <AlertTriangle size={12} className="text-amber-500" />;
  return <Info size={12} className="text-slate-400" />;
}

function levelBg(level: string) {
  if (level === 'ERROR') return 'bg-red-50 border-red-100';
  if (level === 'WARN') return 'bg-amber-50 border-amber-100';
  return 'bg-white border-slate-100';
}

function formatTs(ts: string) {
  if (!ts) return '';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts.slice(0, 19);
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function SystemLogPanel() {
  const [logs, setLogs] = useState<SystemLogEntry[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  const today = formatDateInputValue(new Date());
  const [startDate, setStartDate] = useState(today);
  const [endDate, setEndDate] = useState(today);
  const [level, setLevel] = useState('');
  const [source, setSource] = useState('');
  const [keyword, setKeyword] = useState('');

  const [isExporting, setIsExporting] = useState(false);
  const [copied, setCopied] = useState(false);
  const [copyMessage, setCopyMessage] = useState('');

  const loadLogs = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await getSystemLogs({
        startDate: startDate || undefined,
        endDate: endDate || undefined,
        level: level || undefined,
        source: source || undefined,
        keyword: keyword || undefined,
        limit: 500,
      });
      setLogs(res.entries);
    } catch {
      setLogs([]);
    } finally {
      setIsLoading(false);
    }
  }, [startDate, endDate, level, source, keyword]);

  useEffect(() => {
    void loadLogs();
  }, []);

  const handleExport = async (mode: 'today' | 'range') => {
    setIsExporting(true);
    try {
      const params = mode === 'today'
        ? { startDate: today, endDate: today, source: source || undefined }
        : { startDate, endDate, level: level || undefined, source: source || undefined, keyword: keyword || undefined };
      const md = await exportSystemLogs(params);
      const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `yiyu-logs-${mode === 'today' ? today : `${startDate}-${endDate}`}.md`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('导出日志失败', err);
    } finally {
      setIsExporting(false);
    }
  };

  const handleCopyForSupport = async () => {
    setCopied(false);
    setCopyMessage('');
    try {
      const md = await exportSystemLogs({ startDate: today, endDate: today, level: 'ERROR', source: source || undefined });
      if (!md || !md.trim()) {
        setCopyMessage('今天没有错误日志,无需复制。');
        setTimeout(() => setCopyMessage(''), 4000);
        return;
      }
      await navigator.clipboard.writeText(md);
      setCopied(true);
      setCopyMessage('已复制到剪贴板,可以直接粘贴给技术支持。');
      setTimeout(() => { setCopied(false); setCopyMessage(''); }, 5000);
    } catch {
      setCopyMessage('复制失败,请手动导出后复制。');
      setTimeout(() => setCopyMessage(''), 4000);
    }
  };

  const errorCount = logs.filter((l) => l.level === 'ERROR').length;
  const warnCount = logs.filter((l) => l.level === 'WARN').length;

  return (
    <div className="space-y-5">
      {/* Stats bar */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-slate-50 border border-slate-100 text-[12px] font-semibold text-slate-500">
          共 {logs.length} 条
        </div>
        {errorCount > 0 && (
          <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-red-50 border border-red-100 text-[12px] font-semibold text-red-600">
            <AlertCircle size={12} /> {errorCount} 个错误
          </div>
        )}
        {warnCount > 0 && (
          <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-amber-50 border border-amber-100 text-[12px] font-semibold text-amber-600">
            <AlertTriangle size={12} /> {warnCount} 个警告
          </div>
        )}
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={() => void loadLogs()}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-slate-200 text-[12px] font-medium text-slate-500 hover:bg-slate-50 transition-colors"
          >
            <RefreshCw size={12} className={isLoading ? 'animate-spin' : ''} /> 刷新
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-2 items-center">
        {/* AUDIT-20260518-085: 桌面运行日志(electron-launch.log)只能返回当前 run,不支持日期过滤.
            选择 source=desktop 时禁用日期控件并显示说明,避免用户调日期后看到"昨天日志为空"的误导. */}
        <div className={`flex items-center gap-1.5 ${source === 'desktop' ? 'opacity-50' : ''}`}>
          <Calendar size={12} className="text-slate-400" />
          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            disabled={source === 'desktop'}
            title={source === 'desktop' ? '桌面运行日志只能查看当前 run,不支持日期范围' : undefined}
            className="border border-slate-200 rounded-lg px-2.5 py-1.5 text-[12px] text-slate-700 bg-white outline-none focus:border-blue-300 disabled:cursor-not-allowed disabled:bg-slate-50"
          />
          <span className="text-[11px] text-slate-400">至</span>
          <input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            disabled={source === 'desktop'}
            title={source === 'desktop' ? '桌面运行日志只能查看当前 run,不支持日期范围' : undefined}
            className="border border-slate-200 rounded-lg px-2.5 py-1.5 text-[12px] text-slate-700 bg-white outline-none focus:border-blue-300 disabled:cursor-not-allowed disabled:bg-slate-50"
          />
          {source === 'desktop' && (
            <span className="text-[11px] text-amber-600 ml-1">仅当前 run</span>
          )}
        </div>
        <select
          value={level}
          onChange={(e) => setLevel(e.target.value)}
          className="border border-slate-200 rounded-lg px-2.5 py-1.5 text-[12px] text-slate-700 bg-white outline-none"
        >
          <option value="">全部级别</option>
          <option value="ERROR">ERROR</option>
          <option value="WARN">WARN</option>
          <option value="INFO">INFO</option>
        </select>
        <select
          value={source}
          onChange={(e) => setSource(e.target.value)}
          className="border border-slate-200 rounded-lg px-2.5 py-1.5 text-[12px] text-slate-700 bg-white outline-none"
        >
          <option value="">全部来源</option>
          <option value="api">API 请求</option>
          <option value="activity">业务操作</option>
          <option value="system">系统事件</option>
          <option value="desktop">桌面运行日志</option>
        </select>
        <div className="relative flex-1 min-w-[160px]">
          <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="搜索关键词..."
            className="w-full border border-slate-200 rounded-lg pl-7 pr-2.5 py-1.5 text-[12px] text-slate-700 bg-white outline-none focus:border-blue-300"
          />
        </div>
        <button
          type="button"
          onClick={() => void loadLogs()}
          className="px-4 py-1.5 rounded-lg bg-[#335CFE] text-white text-[12px] font-medium hover:bg-[#2C50E0] transition-colors"
        >
          查询
        </button>
      </div>

      {/* Export buttons */}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => void handleExport('today')}
          disabled={isExporting}
          className="flex items-center gap-1.5 px-4 py-2 rounded-xl border border-slate-200 text-[12px] font-medium text-slate-600 hover:bg-slate-50 transition-colors disabled:opacity-50"
        >
          <Download size={14} /> 导出今天的日志
        </button>
        <button
          type="button"
          onClick={() => void handleExport('range')}
          disabled={isExporting}
          className="flex items-center gap-1.5 px-4 py-2 rounded-xl border border-slate-200 text-[12px] font-medium text-slate-600 hover:bg-slate-50 transition-colors disabled:opacity-50"
        >
          <Download size={14} /> 导出选定范围
        </button>
        <button
          type="button"
          onClick={() => void handleCopyForSupport()}
          className={`flex items-center gap-1.5 px-4 py-2 rounded-xl border text-[12px] font-medium transition-colors ${
            copied
              ? 'border-emerald-300 bg-emerald-50 text-emerald-700'
              : 'border-blue-200 bg-blue-50 text-blue-600 hover:bg-blue-100'
          }`}
        >
          {copied ? <><ClipboardCopy size={14} /> 已复制到剪贴板 — 可直接粘贴</> : <><Send size={14} /> 一键复制错误日志(提交给官方)</>}
        </button>
      </div>

      {copyMessage && (
        <div className={`flex items-center gap-2 px-4 py-3 rounded-2xl text-[13px] font-medium ${
          copied
            ? 'border border-emerald-200 bg-emerald-50 text-emerald-700'
            : 'border border-amber-200 bg-amber-50 text-amber-700'
        }`}>
          {copied ? <ClipboardCopy size={14} /> : <AlertTriangle size={14} />}
          {copyMessage}
        </div>
      )}

      {/* Log list */}
      <div className="space-y-1.5 max-h-[60vh] overflow-y-auto">
        {isLoading && logs.length === 0 ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 size={20} className="text-slate-300 animate-spin" />
          </div>
        ) : logs.length === 0 ? (
          <div className="text-center py-16">
            <p className="text-[13px] text-slate-400">暂无日志数据</p>
            <p className="text-[11px] text-slate-300 mt-1">使用软件后日志会自动记录</p>
          </div>
        ) : (
          logs.map((entry, idx) => {
            const isExpanded = expandedIdx === idx;
            const hasDetail = entry.traceback || entry.error || entry.detail || entry.duration_ms;

            return (
              <div
                key={idx}
                className={`rounded-[14px] border px-4 py-2.5 cursor-pointer transition-colors ${levelBg(entry.level)} ${isExpanded ? 'shadow-sm' : ''}`}
                onClick={() => setExpandedIdx(isExpanded ? null : idx)}
              >
                <div className="flex items-center gap-2">
                  {levelIcon(entry.level)}
                  <span className="text-[10px] font-mono text-slate-400 shrink-0">{formatTs(entry.ts)}</span>
                  <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
                    entry.level === 'ERROR' ? 'bg-red-100 text-red-700' : entry.level === 'WARN' ? 'bg-amber-100 text-amber-700' : 'bg-slate-100 text-slate-500'
                  }`}>
                    {entry.level}
                  </span>
                  <span className="text-[10px] font-medium text-slate-400">{entry.source}</span>
                  <span className="text-[12px] font-medium text-slate-700 truncate flex-1">{entry.message}</span>
                  {entry.duration_ms != null && (
                    <span className="text-[10px] font-mono text-slate-400 shrink-0">{entry.duration_ms}ms</span>
                  )}
                  {entry.user && (
                    <span className="text-[10px] font-medium text-slate-400 shrink-0">{entry.user}</span>
                  )}
                </div>

                {isExpanded && hasDetail && (
                  <div className="mt-3 pt-3 border-t border-slate-100 space-y-2">
                    {entry.error && (
                      <div>
                        <span className="text-[10px] font-bold text-red-600 uppercase">错误信息</span>
                        <p className="text-[12px] text-red-700 font-mono mt-1 bg-red-50 rounded-lg p-2">{entry.error}</p>
                      </div>
                    )}
                    {entry.traceback && (
                      <div>
                        <span className="text-[10px] font-bold text-slate-500 uppercase">调用栈</span>
                        <pre className="text-[10px] text-slate-600 font-mono mt-1 bg-slate-50 rounded-lg p-2 overflow-x-auto max-h-[200px] overflow-y-auto whitespace-pre-wrap">
                          {entry.traceback}
                        </pre>
                      </div>
                    )}
                    {entry.path && (
                      <div className="flex flex-wrap gap-3 text-[11px] text-slate-500">
                        <span>请求:<code className="font-mono">{entry.method} {entry.path}</code></span>
                        <span>状态码:<code className="font-mono">{entry.status}</code></span>
                        {entry.duration_ms != null && <span>耗时:<code className="font-mono">{entry.duration_ms}ms</code></span>}
                      </div>
                    )}
                    {entry.detail && (
                      <div>
                        <span className="text-[10px] font-bold text-slate-500 uppercase">详情</span>
                        <pre className="text-[10px] text-slate-600 font-mono mt-1 bg-slate-50 rounded-lg p-2 overflow-x-auto">
                          {JSON.stringify(entry.detail, null, 2)}
                        </pre>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
