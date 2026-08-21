import React, { useEffect, useState } from 'react';
import { CheckCircle2, ExternalLink, RefreshCw, ShieldCheck } from 'lucide-react';

import type {
  FeishuDeliveryProfile, FeishuDeliveryProfilePayload, FeishuMemberAuthorization,
  LocalInputMemoryFeishuIntegration, OrgFeishuIntegration, OrgFeishuIntegrationPayload,
  OrgMembershipSummary,
} from '../../../shared/types';

const FEISHU_APP_CONSOLE_URL = 'https://open.feishu.cn/app';
const FEISHU_CREATE_APP_HELP_URL = 'https://open.feishu.cn/document/uYjL24iN/uMTMuMTMuMTM/development-guide/step1';
const FEISHU_EXTERNAL_BOT_HELP_URL = 'https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/develop-robots/add-bot-to-external-group';
const FEISHU_CALLBACK_URL = 'https://yiyu.love/oauth/feishu/member/callback';

function HelpLink({ href, children }: { href: string; children: React.ReactNode }) {
  return <a href={href} target="_blank" rel="noreferrer" className="ml-1 inline-flex items-center gap-1 font-bold text-indigo-600 hover:text-indigo-700">
    {children}<ExternalLink size={11} />
  </a>;
}

type MemberAuthorizationFlow = { authorizeUrl: string; callbackUrl: string; expiresAt: string; qrReady: boolean; qrBlockedReason?: string | null; qrCodeDataUrl: string | null; isPolling: boolean; statusMessage: string };
type Props = {
  sessionMode: 'local' | 'cloud'; membership: OrgMembershipSummary; integration: OrgFeishuIntegration;
  deliveryProfile: FeishuDeliveryProfile; memberAuthorization: FeishuMemberAuthorization;
  memberAuthorizationFlow: MemberAuthorizationFlow | null; memberAuthorizationBusy: boolean;
  currentUserName?: string | null; currentWorkspaceName?: string | null; saveBusy: boolean;
  savePhoneBusy: boolean; rememberedInputs: LocalInputMemoryFeishuIntegration; canManage?: boolean;
  onSaveIntegration: (payload: OrgFeishuIntegrationPayload) => Promise<void>;
  onSaveRememberedInputs: (payload: LocalInputMemoryFeishuIntegration) => Promise<void>;
  onSaveDeliveryProfile: (payload: FeishuDeliveryProfilePayload) => Promise<void>;
  onStartMemberAuthorization: () => Promise<void>; onRefreshMemberAuthorization: () => Promise<void>;
  onClearMemberAuthorization: () => Promise<void>; onOpenMemberAuthorization: () => Promise<void>;
  onOpenOrganizationSetup?: () => void; onOpenCloudAuth?: () => void;
};

export function FeishuOrgIntegrationPanel({ sessionMode, membership, integration,
  saveBusy, canManage = false, onSaveIntegration, onSaveRememberedInputs }: Props) {
  const [appId, setAppId] = useState(integration.appId || '');
  const [appSecret, setAppSecret] = useState('');
  useEffect(() => {
    setAppId(integration.appId || '');
    setAppSecret('');
  }, [integration.appId]);
  const canConfigure = sessionMode === 'cloud' && membership.hasOrganization && canManage;
  const changed = appId.trim() !== (integration.appId || '') || Boolean(appSecret.trim());

  async function save() {
    const payload: OrgFeishuIntegrationPayload = { appId: appId.trim(), appSecret: appSecret.trim() || undefined,
      scopeKind: 'organization', expectedVersion: Number(integration.scopeVersions?.organization || integration.expectedVersion || 0) };
    await onSaveIntegration(payload);
    await onSaveRememberedInputs({ rememberInputs: false, appId: '', callbackMode: 'cloud_relay', customCallbackUrl: '', appSecret: '' });
    setAppSecret('');
  }

  return <div className="space-y-6">
    <section className="space-y-4">
      <div><p className="text-[11px] font-bold uppercase tracking-[0.12em] text-gray-500">飞书机器人配置</p>
        <p className="mt-1.5 text-[12px] leading-6 text-gray-500">可使用任一飞书企业授权的企业自建应用作为本组织机器人。管理员只需验证并保存 App ID / Secret。</p></div>
      {canConfigure ? <>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <input value={appId} onChange={(e) => setAppId(e.target.value)} placeholder="飞书 App ID" className="w-full rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 text-[13px] font-medium outline-none" />
          <input type="password" value={appSecret} onChange={(e) => setAppSecret(e.target.value)} placeholder={integration.hasAppSecret ? '已保存密钥；更新时重新输入' : '飞书 App Secret'} className="w-full rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 text-[13px] font-medium outline-none" />
        </div>
        <button type="button" onClick={() => void save()} disabled={!changed || saveBusy} className="inline-flex items-center gap-2 rounded-2xl bg-indigo-500 px-5 py-3 text-[13px] font-bold text-white disabled:cursor-not-allowed disabled:opacity-50">{saveBusy ? <RefreshCw size={16} className="animate-spin" /> : <CheckCircle2 size={16} />}验证并保存</button>
      </> : <p className="rounded-2xl border border-slate-100 bg-slate-50 px-4 py-3 text-[12px] leading-6 text-slate-600">{integration.enabled ? '组织管理员已完成配置。' : '请由组织管理员配置飞书机器人。'}</p>}
      {integration.lastValidationMessage ? <p className="text-[12px] text-slate-500">{integration.lastValidationMessage}</p> : null}
    </section>

    <section className="rounded-2xl border border-indigo-100 bg-indigo-50/70 px-4 py-4 text-[12px] leading-6 text-slate-700">
      <div className="flex items-center gap-2 font-bold text-slate-900"><ShieldCheck size={15} />配置企业自建应用（管理员简明教程）</div>
      <ol className="mt-2 list-decimal space-y-1 pl-5">
        <li>进入<HelpLink href={FEISHU_APP_CONSOLE_URL}>飞书开放平台开发者后台</HelpLink>，创建“企业自建应用”，填写名称、描述和图标；可参考<HelpLink href={FEISHU_CREATE_APP_HELP_URL}>官方创建流程</HelpLink>。</li>
        <li>在“添加应用能力”中启用“机器人”；在“权限管理”中开通消息发送、成员读取权限。</li>
        <li>在“开发配置 → 安全设置 → 重定向 URL”中添加下方固定地址。管理员无需自行寻找回调地址，也不必为了基础通知额外配置消息卡片。
          <span className="my-1 block break-all rounded-xl bg-white px-3 py-1.5 font-mono text-[11px] text-indigo-700">{FEISHU_CALLBACK_URL}</span>
        </li>
        <li>进入“应用发布 → 版本管理与发布”，创建版本、申请发布，并由应用所属飞书企业的管理员审核通过。</li>
        <li>发布完成后，回到本页填写 App ID / Secret，点击“验证并保存”。验证通过即代表本组织飞书机器人已接通。</li>
        <li>需要服务外部成员时，再在已发布版本的“对外共享设置”中开启“允许机器人被添加到外部群”和“允许外部用户与机器人单聊”，首次外部单聊建议设为无需审批；具体位置见<HelpLink href={FEISHU_EXTERNAL_BOT_HELP_URL}>官方外部群教程</HelpLink>。</li>
        <li>由机器人所属企业的管理员先添加外部用户为好友，建议把需要接收通知的外部用户统一拉进一个外部群，再把机器人添加进群。</li>
        <li>外部成员点击名片、发送“你好”，按机器人回复完成一次身份确认；若登录了多个软件工作空间，需明确选择本次绑定的工作空间。</li>
      </ol>
    </section>
  </div>;
}
