import React, { useEffect, useMemo, useState } from 'react';
import { CalendarDays, CheckCircle2, ExternalLink, KeyRound, RefreshCw, Unlink, Users } from 'lucide-react';

import type {
  FeishuDeliveryProfile,
  FeishuDeliveryProfilePayload,
  FeishuMemberAuthorization,
  LocalInputMemoryFeishuIntegration,
  OrgFeishuIntegration,
  OrgFeishuIntegrationPayload,
  OrgMembershipSummary,
} from '../../../shared/types';

const FEISHU_ENTERPRISE_HELP_URL = 'https://www.feishu.cn/hc/zh-CN/articles/360043741453-%E5%88%9B%E5%BB%BA%E4%BC%81%E4%B8%9A';
const FEISHU_CUSTOM_APP_HELP_URL = 'https://open.feishu.cn/document/home/introduction-to-custom-app-development/self-built-application-development-process?lang=zh-CN';
const FEISHU_APP_CONSOLE_URL = 'https://open.feishu.cn/app';
const FEISHU_LOCAL_CALDAV_HELP_URL = 'https://www.feishu.cn/hc/zh-CN/articles/360043178673-%E8%AE%BE%E7%BD%AE%E6%9C%AC%E5%9C%B0%E7%B3%BB%E7%BB%9F%E6%97%A5%E5%8E%86%E4%B8%8E%E9%A3%9E%E4%B9%A6%E6%97%A5%E5%8E%86%E4%B9%8B%E9%97%B4%E7%9A%84%E5%90%8C%E6%AD%A5';

type MemberAuthorizationFlow = {
  authorizeUrl: string;
  callbackUrl: string;
  expiresAt: string;
  qrReady: boolean;
  qrBlockedReason?: string | null;
  qrCodeDataUrl: string | null;
  isPolling: boolean;
  statusMessage: string;
};

function FeishuHelpLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-1 rounded-full bg-white px-3 py-1 font-bold text-indigo-600 transition hover:text-indigo-700"
    >
      <ExternalLink size={12} />
      {children}
    </a>
  );
}

type Props = {
  sessionMode: 'local' | 'cloud';
  membership: OrgMembershipSummary;
  integration: OrgFeishuIntegration;
  deliveryProfile: FeishuDeliveryProfile;
  memberAuthorization: FeishuMemberAuthorization;
  memberAuthorizationFlow: MemberAuthorizationFlow | null;
  memberAuthorizationBusy: boolean;
  currentUserName?: string | null;
  currentWorkspaceName?: string | null;
  saveBusy: boolean;
  savePhoneBusy: boolean;
  rememberedInputs: LocalInputMemoryFeishuIntegration;
  onSaveIntegration: (payload: OrgFeishuIntegrationPayload) => Promise<void>;
  onSaveRememberedInputs: (payload: LocalInputMemoryFeishuIntegration) => Promise<void>;
  onSaveDeliveryProfile: (payload: FeishuDeliveryProfilePayload) => Promise<void>;
  onStartMemberAuthorization: () => Promise<void>;
  onRefreshMemberAuthorization: () => Promise<void>;
  onClearMemberAuthorization: () => Promise<void>;
  onOpenMemberAuthorization: () => Promise<void>;
  onOpenOrganizationSetup?: () => void;
  onOpenCloudAuth?: () => void;
};

function statusTone(status: OrgFeishuIntegration['lastValidationStatus']) {
  if (status === 'success' || status === 'succeeded') {
    return 'border-emerald-100 bg-emerald-50 text-emerald-700';
  }
  if (status === 'failed' || status === 'failed_retryable') {
    return 'border-rose-100 bg-rose-50 text-rose-700';
  }
  return 'border-slate-100 bg-slate-50 text-slate-600';
}

function deliveryTone(status: FeishuDeliveryProfile['deliveryStatus']) {
  if (status === 'matched') {
    return 'border-emerald-100 bg-emerald-50 text-emerald-700';
  }
  if (status === 'failed') {
    return 'border-rose-100 bg-rose-50 text-rose-700';
  }
  if (status === 'not_found') {
    return 'border-amber-100 bg-amber-50 text-amber-700';
  }
  return 'border-slate-100 bg-slate-50 text-slate-600';
}

export function FeishuOrgIntegrationPanel({
  sessionMode,
  membership,
  integration,
  deliveryProfile,
  memberAuthorization,
  memberAuthorizationFlow,
  memberAuthorizationBusy,
  currentUserName,
  currentWorkspaceName,
  saveBusy,
  savePhoneBusy,
  rememberedInputs,
  onSaveIntegration,
  onSaveRememberedInputs,
  onSaveDeliveryProfile,
  onStartMemberAuthorization,
  onRefreshMemberAuthorization,
  onClearMemberAuthorization,
  onOpenMemberAuthorization,
}: Props) {
  const [appId, setAppId] = useState(integration.appId || rememberedInputs.appId || '');
  const [appSecret, setAppSecret] = useState(rememberedInputs.appSecret || '');
  const [rememberLocalInputs, setRememberLocalInputs] = useState(rememberedInputs.rememberInputs);
  const [mobile, setMobile] = useState(deliveryProfile.mobile || '');

  useEffect(() => {
    setAppId(integration.appId || rememberedInputs.appId || '');
    setAppSecret(rememberedInputs.appSecret || '');
    setRememberLocalInputs(rememberedInputs.rememberInputs);
  }, [
    integration.appId,
    rememberedInputs.appId,
    rememberedInputs.appSecret,
    rememberedInputs.rememberInputs,
  ]);

  useEffect(() => {
    setMobile(deliveryProfile.mobile || '');
  }, [deliveryProfile.mobile]);

  const integrationChanges =
    appId.trim() !== (integration.appId || '')
    || Boolean(appSecret.trim());
  const canConfigureIntegration = sessionMode === 'cloud' && membership.hasOrganization;
  const canConfigureDeliveryProfile = sessionMode === 'cloud' && membership.hasOrganization && integration.enabled;
  const canAuthorizeMember = sessionMode === 'cloud' && membership.hasOrganization && integration.enabled;
  const canSaveIntegration = canConfigureIntegration && integrationChanges && !saveBusy;
  const canSaveDeliveryProfile = canConfigureDeliveryProfile && mobile.trim() !== (deliveryProfile.mobile || '') && !savePhoneBusy;
  const taskCalendarSyncReady = Boolean(integration.enabled && memberAuthorization.linked);
  const taskCalendarStatusClass = taskCalendarSyncReady
    ? 'border-emerald-100 bg-emerald-50 text-emerald-700'
    : integration.enabled
      ? 'border-amber-100 bg-amber-50 text-amber-700'
      : 'border-slate-100 bg-slate-50 text-slate-600';
  const taskCalendarStatusText = taskCalendarSyncReady
    ? '已具备'
    : integration.enabled
      ? '待授权'
      : '待接入';

  const integrationHelper = useMemo(() => {
    if (sessionMode !== 'cloud') {
      return '连接益语云端并加入组织后，才能配置飞书自建应用。个人用户如需完整飞书同步，可先创建一人飞书企业/团队。';
    }
    if (!membership.hasOrganization) {
      return '你还没有加入任何组织。飞书同步依赖益语组织或个人部署空间，请先加入组织或创建组织。';
    }
    if (integration.enabled) {
      return '当前飞书自建应用已验证。成员完成飞书身份绑定后，可使用飞书文档权限、从飞书导入文档，并让参与的飞书任务进入益语；手机号只用于机器人提醒和任务成员匹配。';
    }
    return integration.lastValidationMessage || '完成后，益语云端会使用该飞书自建应用调用飞书开放平台，承接飞书文档、任务和日历相关能力。';
  }, [integration.lastValidationMessage, membership.hasOrganization, sessionMode]);

  const deliveryHelper = useMemo(() => {
    if (sessionMode !== 'cloud') {
      return '先连接云端，再填写你的飞书接收手机号。';
    }
    if (!membership.hasOrganization) {
      return '先加入或创建组织，再填写你的飞书接收手机号。';
    }
    if (!integration.enabled) {
      return '当前还没有接通飞书自建应用。接通后，软件才会按手机号匹配并发送任务提醒。';
    }
    return deliveryProfile.blockedReason
      || '请填写你登录飞书时使用的手机号。软件会按这个手机号匹配飞书成员，用于机器人提醒，也用于把益语负责人/协作者稳定分配到飞书任务。';
  }, [deliveryProfile.blockedReason, integration.enabled, membership.hasOrganization, sessionMode]);

  const handleSaveIntegration = async () => {
    const payload: OrgFeishuIntegrationPayload = {
      appId: appId.trim(),
      appSecret: appSecret.trim() || undefined,
      scopeKind: integration.defaultWriteScope || 'personal',
      expectedVersion: Number(
        integration.scopeVersions?.[integration.defaultWriteScope || 'personal']
        || 0,
      ),
    };
    await onSaveIntegration(payload);
	    await onSaveRememberedInputs({
	      rememberInputs: rememberLocalInputs,
	      appId: payload.appId || '',
	      callbackMode: payload.callbackMode || 'cloud_relay',
	      customCallbackUrl: payload.customCallbackUrl || '',
	      appSecret: payload.appSecret || '',
	    });
    if (!rememberLocalInputs) {
      setAppSecret('');
    }
  };

  const handleSaveDeliveryProfile = async () => {
    await onSaveDeliveryProfile({ mobile: mobile.trim() || null });
  };

  return (
    <div className="space-y-8">
      <div className="space-y-5">
        <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
          <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-slate-400">当前工作空间</p>
          <p className="mt-1 text-[14px] font-bold text-slate-900">{currentWorkspaceName || '当前工作空间'}</p>
          <p className="mt-1 text-[12px] leading-6 text-slate-500">
            飞书自建应用、成员飞书身份绑定、飞书成员匹配手机号都只属于当前工作空间；切换工作空间后会重新读取对应组织或本机的飞书状态。
          </p>
        </div>
        <div className="rounded-2xl border border-indigo-100 bg-indigo-50/80 px-4 py-3 text-[13px] font-semibold leading-6 text-slate-800">
	          请依次完成飞书自建应用接入、我的飞书身份绑定、任务与日历同步、飞书成员匹配手机号。飞书任务、文档和日历提醒依赖飞书自建应用；成员绑定飞书身份后，任务和文档才能正确落到本人权限下。
        </div>
        <div className="rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-[12px] leading-6 text-slate-700">
          当前生效：
          <span className="ml-1 font-bold text-slate-900">
            {integration.effectiveScopeKind === 'personal' ? '个人覆盖配置' : integration.effectiveScopeKind === 'organization' ? '组织默认配置' : '尚未配置'}
          </span>
          ；本次保存将写入
          <span className="mx-1 font-bold text-slate-900">
            {integration.defaultWriteScope === 'organization' ? '组织默认' : '当前成员个人'}
          </span>
          作用域。普通成员不会覆盖组织密钥。
        </div>
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-gray-500">飞书自建应用接入</p>
            <p className="text-[12px] text-gray-500 mt-1.5 leading-relaxed">
              这一步配置的是益语云端调用飞书开放平台所需的自建应用。益语云端负责益语任务数据和同步编排；飞书任务、文档、日历能力来自组织或个人配置的飞书自建应用。
            </p>
            <div className="mt-3 rounded-2xl border border-indigo-100 bg-indigo-50/70 px-4 py-3 text-[12px] leading-6 text-slate-700">
              <p className="font-bold text-slate-900">配置飞书自建应用需要完成 5 步：</p>
              <ol className="mt-2 list-decimal space-y-1 pl-5">
                <li>
                  打开飞书开放平台：
                  <a
                    href={FEISHU_APP_CONSOLE_URL}
                    target="_blank"
                    rel="noreferrer"
                    className="ml-1 font-bold text-indigo-600 hover:text-indigo-700"
                  >
                    {FEISHU_APP_CONSOLE_URL}
                  </a>
                </li>
                <li>创建“企业自建应用”。个人用户如无组织，可先按飞书官方指引创建一人企业/团队，自己作为管理员。</li>
                <li>复制 App ID 和 App Secret，填写到本页。</li>
                <li>按需启用机器人、任务、文档、日历等能力；成员完成飞书身份绑定后，才能访问个人飞书文档并参与飞书任务同步。</li>
                <li>
                  在“开发配置 → 安全设置 → 重定向 URL”中添加：
                  <span className="mt-1 block break-all rounded-xl bg-white px-3 py-2 font-mono text-[11px] text-indigo-700">
                    {integration.effectiveCallbackUrl || 'https://yiyu.love/oauth/feishu/member/callback'}
                  </span>
                </li>
              </ol>
              <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
                <span className="inline-flex items-center rounded-full bg-indigo-100 px-3 py-1 font-bold text-indigo-700">官方指引</span>
                <FeishuHelpLink href={FEISHU_ENTERPRISE_HELP_URL}>创建飞书企业/团队</FeishuHelpLink>
                <FeishuHelpLink href={FEISHU_CUSTOM_APP_HELP_URL}>企业自建应用流程</FeishuHelpLink>
              </div>
              <p className="mt-2 text-[11px] text-slate-500">
                完成后点击“验证并保存飞书自建应用接入”。检测通过后，成员只需绑定自己的飞书身份，不需要填写 App Secret。
              </p>
            </div>
          </div>
          <div className={`text-[10px] font-bold uppercase tracking-[0.12em] px-2.5 py-1 rounded-full border ${statusTone(integration.lastValidationStatus)}`}>
            {integration.enabled ? '已接通' : '未接通'}
          </div>
        </div>

        {canConfigureIntegration && (
          <>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <input
                value={appId}
                onChange={(event) => setAppId(event.target.value)}
                placeholder="飞书 App ID"
                className="w-full bg-gray-50 border border-gray-200 rounded-2xl px-4 py-3 text-[13px] font-medium outline-none disabled:text-gray-400 disabled:bg-gray-100"
              />
              <input
                type="password"
                value={appSecret}
                onChange={(event) => setAppSecret(event.target.value)}
                placeholder={integration.hasAppSecret ? '已保存组织密钥；如需更新请重新输入' : '飞书 App Secret'}
                className="w-full bg-gray-50 border border-gray-200 rounded-2xl px-4 py-3 text-[13px] font-medium outline-none disabled:text-gray-400 disabled:bg-gray-100"
              />
            </div>

            <label className="flex items-center gap-2 text-[12px] font-medium text-gray-700">
              <input
                type="checkbox"
                checked={rememberLocalInputs}
                onChange={(event) => setRememberLocalInputs(event.target.checked)}
              />
              在本机记住 App ID / App Secret
            </label>
          </>
        )}

        <div className="rounded-2xl border border-slate-100 bg-slate-50 px-4 py-3">
          <p className="text-[12px] font-bold text-slate-900">
            当前组织：{membership.organizationName || '尚未加入组织'}
          </p>
          <p className="text-[12px] text-slate-600 mt-2 leading-6">{integrationHelper}</p>
          {integration.configuredBy && integration.configuredAt ? (
            <p className="text-[11px] text-slate-400 mt-2">
              最近配置：{integration.configuredBy} · {integration.configuredAt}
            </p>
          ) : null}
        </div>

        {canConfigureIntegration && (
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              onClick={() => void handleSaveIntegration()}
              disabled={!canSaveIntegration}
              className="inline-flex items-center gap-2 rounded-2xl px-5 py-3 text-[13px] font-bold text-white bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {saveBusy ? <RefreshCw size={16} className="animate-spin" /> : <CheckCircle2 size={16} />}
              验证并保存飞书自建应用接入
            </button>
          </div>
        )}
      </div>

      <div className="border-t border-gray-100 pt-6 space-y-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-gray-500">我的飞书身份绑定</p>
            <p className="text-[12px] text-gray-500 mt-1.5 leading-relaxed">
              这一步绑定的是当前成员的个人飞书身份，用于飞书文档权限、从飞书导入文档，也用于让你参与的飞书任务自动进入益语。成员不填写 App Secret。
            </p>
          </div>
          <div className={`text-[10px] font-bold uppercase tracking-[0.12em] px-2.5 py-1 rounded-full border ${
            memberAuthorization.linked
              ? 'border-emerald-100 bg-emerald-50 text-emerald-700'
              : memberAuthorization.readyForAuthorization
                ? 'border-amber-100 bg-amber-50 text-amber-700'
                : 'border-slate-100 bg-slate-50 text-slate-600'
          }`}>
            {memberAuthorization.linked ? '已授权' : memberAuthorization.readyForAuthorization ? '待授权' : '不可授权'}
          </div>
        </div>

        <div className="rounded-2xl border border-slate-100 bg-slate-50 px-4 py-3">
          <p className="text-[12px] font-bold text-slate-900 flex items-center gap-2">
            <KeyRound size={14} />
            当前成员：{currentUserName || '当前账号'}
          </p>
            <p className="text-[12px] text-slate-600 mt-2 leading-6">
              {memberAuthorization.linked
                ? `已绑定：${memberAuthorization.name || memberAuthorization.email || memberAuthorization.openId || '当前飞书成员'}`
                : memberAuthorization.blockedReason || '飞书自建应用已接通。绑定后，软件创建的飞书文档会确保当前成员可打开和编辑，也可以从飞书导入文档；你参与的飞书任务也可进入益语。'}
            </p>
          {memberAuthorization.boundAt ? (
            <p className="text-[11px] text-slate-400 mt-2">授权时间：{memberAuthorization.boundAt}</p>
          ) : null}
          {memberAuthorization.lastError ? (
            <p className="text-[11px] text-rose-500 mt-2">{memberAuthorization.lastError}</p>
          ) : null}
          {memberAuthorizationFlow ? (
            <div className="mt-3 rounded-2xl border border-blue-100 bg-white px-4 py-3">
              <p className="text-[12px] font-bold text-blue-700">{memberAuthorizationFlow.statusMessage}</p>
              {memberAuthorizationFlow.qrCodeDataUrl ? (
                <img
                  src={memberAuthorizationFlow.qrCodeDataUrl}
                  alt="飞书成员授权二维码"
                  className="mt-3 h-80 w-80 max-w-full rounded-xl border border-gray-100 bg-white"
                />
              ) : null}
              <p className="mt-3 break-all text-[11px] leading-5 text-slate-500">
                当前授权回调：{memberAuthorizationFlow.callbackUrl}
              </p>
              <button
                type="button"
                onClick={() => void onOpenMemberAuthorization()}
                className="mt-3 inline-flex items-center gap-2 rounded-xl border border-blue-100 bg-blue-50 px-3 py-2 text-[12px] font-bold text-blue-700 transition hover:bg-blue-100"
              >
                <ExternalLink size={14} />
                打开授权页
              </button>
            </div>
          ) : null}
        </div>

        {canAuthorizeMember && (
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              onClick={() => void onStartMemberAuthorization()}
              disabled={memberAuthorizationBusy || !memberAuthorization.readyForAuthorization}
              className="inline-flex items-center gap-2 rounded-2xl px-5 py-3 text-[13px] font-bold text-white bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {memberAuthorizationBusy ? <RefreshCw size={16} className="animate-spin" /> : <ExternalLink size={16} />}
              {memberAuthorization.linked ? '重新绑定飞书身份' : '绑定飞书身份'}
            </button>
            <button
              type="button"
              onClick={() => void onRefreshMemberAuthorization()}
              disabled={memberAuthorizationBusy}
              className="inline-flex items-center gap-2 rounded-2xl border border-gray-200 bg-white px-5 py-3 text-[13px] font-bold text-slate-700 disabled:opacity-50"
            >
              <RefreshCw size={16} />
              刷新授权状态
            </button>
            {memberAuthorization.linked && (
              <button
                type="button"
                onClick={() => void onClearMemberAuthorization()}
                disabled={memberAuthorizationBusy}
                className="inline-flex items-center gap-2 rounded-2xl border border-rose-100 bg-white px-5 py-3 text-[13px] font-bold text-rose-600 disabled:opacity-50"
              >
                <Unlink size={16} />
                解除授权
              </button>
            )}
          </div>
        )}
      </div>

	      <div data-feishu-task-sync-step="true" className="border-t border-gray-100 pt-6 space-y-5">
	        <div className="flex items-start justify-between gap-4">
	          <div className="min-w-0 flex-1">
	            <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-gray-500">任务与日历同步</p>
	            <p className="text-[12px] text-gray-500 mt-1.5 leading-relaxed">
	              这一步说明益语任务、飞书任务、飞书日历事件和手机系统日历之间的关系；软件只负责益语与飞书之间的同步，手机系统日历通过飞书官方 CalDAV 完成。
	            </p>
	          </div>
	          <div className={`text-[10px] font-bold uppercase tracking-[0.12em] px-2.5 py-1 rounded-full border ${taskCalendarStatusClass}`}>
	            {taskCalendarStatusText}
	          </div>
	        </div>

	        <div className="grid gap-3 md:grid-cols-2">
	          <div className="rounded-2xl border border-slate-100 bg-slate-50 px-4 py-3">
	            <p className="text-[12px] font-bold text-slate-900 flex items-center gap-2">
	              <CalendarDays size={14} />
	              益语 ↔ 飞书
	            </p>
	            <ol className="mt-2 list-decimal space-y-1 pl-5 text-[12px] leading-6 text-slate-600">
	              <li>管理员完成飞书自建应用接入，并启用任务、日历相关能力。</li>
	              <li>成员完成“我的飞书身份绑定”。</li>
	              <li>带时间的协作任务会同步为飞书任务和飞书日历事件；你参与的飞书任务变更通常约 1 分钟内同步回益语。</li>
	              <li>任务内容请在益语或飞书任务里修改，避免把手机系统日历当作编辑入口。</li>
	            </ol>
	          </div>

	          <div className="rounded-2xl border border-slate-100 bg-slate-50 px-4 py-3">
	            <p className="text-[12px] font-bold text-slate-900 flex items-center gap-2">
	              <ExternalLink size={14} />
	              飞书日历 → 手机系统日历
	            </p>
	            <ol className="mt-2 list-decimal space-y-1 pl-5 text-[12px] leading-6 text-slate-600">
	              <li>在飞书中开启本地系统日历同步并获取 CalDAV 配置。</li>
	              <li>到 iPhone、macOS 或其他手机系统日历中添加 CalDAV 账户。</li>
	              <li>手机系统日历只用于提醒和查看；任务修改请回益语或飞书任务。</li>
	            </ol>
	            <a
	              href={FEISHU_LOCAL_CALDAV_HELP_URL}
	              target="_blank"
	              rel="noreferrer"
	              className="mt-3 inline-flex items-center gap-1 rounded-full bg-white px-3 py-1 text-[11px] font-bold text-indigo-600 transition hover:text-indigo-700"
	            >
	              <ExternalLink size={12} />
	              飞书日历同步官方指引
	            </a>
	          </div>
	        </div>
	      </div>

	      <div className="border-t border-gray-100 pt-6 space-y-5">
	        <div className="flex items-start justify-between gap-4">
	          <div className="min-w-0 flex-1">
	            <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-gray-500">飞书成员匹配手机号（推荐）</p>
	            <p className="text-[12px] text-gray-500 mt-1.5 leading-relaxed">
	              用于机器人提醒，也用于把益语负责人/协作者稳定分配到飞书任务。不填写也可创建飞书任务，但可能无法自动分配成员；飞书文档权限和飞书任务反向同步仍看上面的飞书身份绑定。
	            </p>
	          </div>
          <div className={`text-[10px] font-bold uppercase tracking-[0.12em] px-2.5 py-1 rounded-full border ${deliveryTone(deliveryProfile.deliveryStatus)}`}>
            {deliveryProfile.deliveryStatusLabel}
          </div>
        </div>

        {canConfigureDeliveryProfile && (
          <input
            value={mobile}
            onChange={(event) => setMobile(event.target.value)}
            placeholder="飞书账号对应手机号"
            className="w-full bg-gray-50 border border-gray-200 rounded-2xl px-4 py-3 text-[13px] font-medium outline-none"
          />
        )}

        <div className="rounded-2xl border border-slate-100 bg-slate-50 px-4 py-3">
          <p className="text-[12px] font-bold text-slate-900 flex items-center gap-2">
            <Users size={14} />
            当前成员：{currentUserName || '当前账号'}
          </p>
          <p className="text-[12px] text-slate-600 mt-2 leading-6">{deliveryHelper}</p>
          {deliveryProfile.lastVerifiedAt ? (
            <p className="text-[11px] text-slate-400 mt-2">
              最近校验：{deliveryProfile.lastVerifiedAt}
              {deliveryProfile.receiveId ? ` · 已匹配 ${deliveryProfile.receiveId}` : ''}
            </p>
          ) : null}
          {deliveryProfile.lastError ? (
            <p className="text-[11px] text-rose-500 mt-2">{deliveryProfile.lastError}</p>
          ) : null}
        </div>

        {canConfigureDeliveryProfile && (
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              onClick={() => void handleSaveDeliveryProfile()}
              disabled={!canSaveDeliveryProfile}
              className="inline-flex items-center gap-2 rounded-2xl px-5 py-3 text-[13px] font-bold text-white bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {savePhoneBusy ? <RefreshCw size={16} className="animate-spin" /> : <CheckCircle2 size={16} />}
              {deliveryProfile.mobile ? '更新飞书手机号' : '保存飞书手机号'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
