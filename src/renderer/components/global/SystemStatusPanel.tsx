import React, { useMemo } from 'react';

import type { HealthResponse, OrgAiRuntimeStatus } from '../../../shared/types';

/**
 * 全局系统状态 panel —— 嵌在 sidebar 底部、系统设置按钮上方。
 *
 * 展开态：纵向 list，每行 [圆点 + label + value]。
 *   数据中心 ●  在线
 *   大模型   ●  GPT 5.4
 *   (后续可扩展：向量库 / 语音引擎 / 外部 API / 上传带宽 ...)
 *
 * 收起态：纵向迷你圆点列，hover tooltip 显示完整状态。
 *
 * 点击任一行 → onClick(category) 跳设置对应 section。
 */
export type SystemStatusRowTone = 'online' | 'warn' | 'offline' | 'pending';

export type SystemStatusRow = {
  key: string;
  label: string;
  value: string;
  tone: SystemStatusRowTone;
  tooltip?: string;
};

export type SystemStatusPanelProps = {
  health: HealthResponse | null;
  aiRuntimeStatus: OrgAiRuntimeStatus | null;
  aiSyncing: boolean;
  backendOnline: boolean;
  collapsed: boolean;
  canSyncOrganizationAi: boolean;
  onSelectSection: (sectionKey: 'data_center' | 'ai') => void;
  onRetryAi: () => void;
};

const TONE_DOT_CLASS: Record<SystemStatusRowTone, string> = {
  online: 'bg-emerald-500',
  warn: 'bg-amber-500',
  offline: 'bg-rose-500',
  pending: 'bg-gray-300',
};

const TONE_VALUE_CLASS: Record<SystemStatusRowTone, string> = {
  online: 'text-gray-400',
  warn: 'text-amber-600',
  offline: 'text-rose-600',
  pending: 'text-gray-300',
};

export function SystemStatusPanel({
  health,
  aiRuntimeStatus,
  aiSyncing,
  backendOnline,
  collapsed,
  canSyncOrganizationAi,
  onSelectSection,
  onRetryAi,
}: SystemStatusPanelProps) {
  const rows: SystemStatusRow[] = useMemo(() => {
    // 数据中心：基于 backendOnline 二元判定
    const dataCenter: SystemStatusRow = backendOnline
      ? {
          key: 'data_center',
          label: '数据中心',
          value: '在线',
          tone: 'online',
          tooltip: '内置服务连接正常',
        }
      : {
          key: 'data_center',
          label: '数据中心',
          value: '离线',
          tone: 'offline',
          tooltip: '内置服务不可达。检查后端进程或重启软件。',
        };

    // 大模型只显示当前组织已验证的运行配置。health.ai 可能仍包含
    // 进程默认值或上一次会话信息，不能把它冒充成当前组织已接通模型。
    let modelRow: SystemStatusRow;
    if (!canSyncOrganizationAi) {
      modelRow = {
        key: 'ai',
        label: '大模型',
        value: '未接通',
        tone: 'warn',
        tooltip: '登录组织并同步组织模型配置后才会显示真实模型名称',
      };
    } else if (aiSyncing || aiRuntimeStatus?.state === 'syncing') {
      modelRow = {
        key: 'ai',
        label: '大模型',
        value: '同步中',
        tone: 'pending',
        tooltip: '正在把当前组织的 AI 配置同步到本机系统密钥库',
      };
    } else if (aiRuntimeStatus?.state === 'ready_direct') {
      const shortName = shortAiModelLabel(
        aiRuntimeStatus.provider,
        aiRuntimeStatus.model,
        aiRuntimeStatus.providerLabel,
      );
      modelRow = {
        key: 'ai',
        label: '大模型',
        value: shortName,
        tone: 'online',
        tooltip: `${aiRuntimeStatus.providerLabel || aiRuntimeStatus.provider}${
          aiRuntimeStatus.model ? ` · ${aiRuntimeStatus.model}` : ''
        }${aiRuntimeStatus.usingCachedConfig ? '\n当前使用本机已验证配置' : ''}`,
      };
    } else if (aiRuntimeStatus?.state === 'not_ready' || aiRuntimeStatus?.state === 'error') {
      modelRow = {
        key: 'ai',
        label: '大模型',
        value: '未接通',
        tone: 'warn',
        tooltip: aiRuntimeStatus.lastError || '当前设备尚未同步组织 AI 配置，点击重新同步',
      };
    } else {
      modelRow = {
        key: 'ai',
        label: '大模型',
        value: '未接通',
        tone: 'warn',
        tooltip: '尚未取得当前组织的有效模型运行配置',
      };
    }
    return [dataCenter, modelRow];
  }, [aiRuntimeStatus, aiSyncing, backendOnline, canSyncOrganizationAi]);

  const handleRowClick = (row: SystemStatusRow) => {
    onSelectSection(row.key as 'data_center' | 'ai');
  };

  if (collapsed) {
    return (
      <div className="hidden md:flex flex-col items-center gap-1.5 border-t border-gray-100 px-2 pt-3 pb-2">
        {rows.map((row) => (
          <button
            key={row.key}
            type="button"
            onClick={() => handleRowClick(row)}
            title={`${row.label}：${row.value}${row.tooltip ? '\n' + row.tooltip : ''}`}
            aria-label={`${row.label} ${row.value}`}
            className="group relative flex h-4 w-4 items-center justify-center rounded hover:bg-gray-50 transition-colors"
          >
            <span className={`inline-block h-[7px] w-[7px] rounded-full ${TONE_DOT_CLASS[row.tone]}`} />
            <span className="pointer-events-none absolute left-full top-1/2 z-30 ml-3 hidden -translate-y-1/2 whitespace-nowrap rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-[11px] font-medium text-gray-700 shadow-[0_8px_24px_rgba(15,23,42,0.1)] opacity-0 transition-all duration-200 group-hover:translate-x-1 group-hover:opacity-100 md:block">
              {row.label}：<span className={TONE_VALUE_CLASS[row.tone]}>{row.value}</span>
            </span>
          </button>
        ))}
      </div>
    );
  }

  return (
    <div className="hidden md:block border-t border-gray-100 px-2.5 pt-2.5 pb-1">
      <p className="px-3 text-[9px] font-bold uppercase tracking-[0.18em] text-gray-400 mb-1">
        SYSTEM · 系统状态
      </p>
      {rows.map((row) => {
        const showSync = row.key === 'ai'
          && canSyncOrganizationAi
          && !aiSyncing
          && (aiRuntimeStatus?.state === 'not_ready' || aiRuntimeStatus?.state === 'error');
        return (
          <div key={row.key} className="group flex w-full items-center gap-1 rounded-md hover:bg-gray-50/70 transition-colors">
            <button
              type="button"
              onClick={() => handleRowClick(row)}
              title={row.tooltip || `${row.label}：${row.value}`}
              className="flex min-w-0 flex-1 items-center gap-2 px-3 py-1.5 text-left"
            >
              <span className={`inline-block h-[7px] w-[7px] rounded-full shrink-0 ${TONE_DOT_CLASS[row.tone]}`} />
              <span className="text-[11.5px] font-medium text-gray-600 group-hover:text-gray-800 transition-colors">{row.label}</span>
              <span className={`ml-auto truncate text-[10.5px] ${TONE_VALUE_CLASS[row.tone]} max-w-[92px]`}>{row.value}</span>
            </button>
            {showSync && (
              <button
                type="button"
                onClick={onRetryAi}
                className="mr-2 rounded border border-amber-200 bg-white px-1.5 py-0.5 text-[10px] font-medium text-amber-700 hover:bg-amber-50"
              >
                同步
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

/**
 * 把 provider + model + label 映射到简短显示名。
 * 没识别到时退到 providerLabel 或 model（超 14 字截尾）。
 */
export function shortAiModelLabel(
  provider?: string | null,
  model?: string | null,
  providerLabel?: string | null,
): string {
  const p = (provider || '').toLowerCase().trim();
  const m = (model || '').toLowerCase().trim();

  if (p.includes('doubao') || p.includes('volc') || p.includes('ark') || m.includes('doubao')) {
    if (m.includes('2-1') || m.includes('2.1')) return m.includes('pro') ? '豆包 Seed 2.1 Pro' : '豆包 Seed 2.1';
    if (m.includes('1-5') || m.includes('1.5')) return '豆包 1.5';
    if (m.includes('2-0') || m.includes('2.0')) return '豆包 Seed 2.0';
    const fallback = (model || providerLabel || provider || '豆包').trim();
    return fallback.length > 14 ? fallback.slice(0, 12) + '…' : fallback;
  }
  if (p.includes('qwen') || p.includes('tongyi') || m.includes('qwen')) {
    if (m.includes('vl')) return '通义 VL';
    if (m.includes('max')) return '通义 Max';
    if (m.includes('plus')) return '通义 Plus';
    return '通义';
  }
  // 5/27 加: 区分 GPT 5.5 / 5.4, 之前只看 provider=openclaw 一律返 5.4
  if (m.includes('gpt-5.5') || m.includes('gpt5.5')) {
    return 'GPT 5.5';
  }
  if (p.includes('openclaw') || m.includes('gpt-5.4') || m.includes('gpt5.4')) {
    return 'GPT 5.4';
  }
  if (p.includes('openai') || m.startsWith('gpt') || m.startsWith('o1') || m.startsWith('o3') || m.startsWith('o4')) {
    if (m.startsWith('gpt-5') || m.includes('gpt5')) return 'GPT-5';
    if (m.startsWith('gpt-4o')) return 'GPT-4o';
    if (m.startsWith('gpt-4')) return 'GPT-4';
    if (m.startsWith('gpt-3')) return 'GPT-3.5';
    if (m.startsWith('o3')) return 'o3';
    if (m.startsWith('o1')) return 'o1';
    if (m.startsWith('o4')) return 'o4';
    return 'GPT';
  }
  if (p.includes('anthropic') || p.includes('claude') || m.startsWith('claude')) {
    if (m.includes('opus')) return 'Claude Opus';
    if (m.includes('sonnet')) return 'Claude Sonnet';
    if (m.includes('haiku')) return 'Claude Haiku';
    return 'Claude';
  }
  if (p.includes('kimi') || p.includes('moonshot') || m.includes('kimi') || m.includes('moonshot')) return 'Kimi';
  if (p.includes('deepseek') || m.includes('deepseek')) return 'DeepSeek';
  if (p.includes('zhipu') || p.includes('glm') || m.startsWith('glm')) return '智谱 GLM';
  if (p.includes('baichuan') || m.includes('baichuan')) return '百川';
  if (p.includes('gemini') || m.startsWith('gemini')) return 'Gemini';
  if (p === 'mock') return '模拟';
  const fallback = (providerLabel || provider || model || 'AI').trim();
  if (fallback.length > 14) return fallback.slice(0, 12) + '…';
  return fallback || 'AI';
}
