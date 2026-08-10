import { useEffect, useMemo, useState } from 'react';
import { Database, CheckCircle2, AlertCircle, RefreshCw } from 'lucide-react';

import type {
  ObjectStorageSettings,
  ObjectStorageSettingsPayload,
  ObjectStorageTestResult,
} from '../../../shared/types';
import {
  OBJECT_STORAGE_PROVIDERS,
  findObjectStorageProvider,
} from '../../../shared/objectStorageProviders';

interface ObjectStorageSettingsCardProps {
  settings: ObjectStorageSettings | null;
  canEdit: boolean;
  isSaving: boolean;
  onSave: (payload: ObjectStorageSettingsPayload) => Promise<void>;
  onTest: (payload: ObjectStorageSettingsPayload) => Promise<ObjectStorageTestResult>;
}

function buildEmptyDraft(): ObjectStorageSettingsPayload {
  return {
    provider: '',
    credentials: {},
    extraConfig: {},
    enabled: false,
  };
}

function settingsToDraft(settings: ObjectStorageSettings | null): ObjectStorageSettingsPayload {
  if (!settings) return buildEmptyDraft();
  return {
    provider: settings.provider || '',
    credentials: { ...(settings.credentials || {}) },
    extraConfig: { ...(settings.extraConfig || {}) },
    enabled: Boolean(settings.enabled),
  };
}

export function ObjectStorageSettingsCard({
  settings,
  canEdit,
  isSaving,
  onSave,
  onTest,
}: ObjectStorageSettingsCardProps) {
  const [draft, setDraft] = useState<ObjectStorageSettingsPayload>(() => settingsToDraft(settings));
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<ObjectStorageTestResult | null>(null);

  useEffect(() => {
    setDraft(settingsToDraft(settings));
    setTestResult(null);
  }, [settings?.provider, settings?.enabled, settings?.updatedAt]);

  const descriptor = useMemo(() => findObjectStorageProvider(draft.provider), [draft.provider]);
  const isSupported = Boolean(descriptor?.supported);

  const setProvider = (nextId: string) => {
    const next = findObjectStorageProvider(nextId);
    if (!next) {
      setDraft({ provider: nextId, credentials: {}, extraConfig: {}, enabled: false });
      setTestResult(null);
      return;
    }
    setDraft({
      provider: next.id,
      credentials: {},
      extraConfig: {},
      enabled: false,
    });
    setTestResult(null);
  };

  const setCredentialField = (key: string, value: string) => {
    setDraft((prev) => ({ ...prev, credentials: { ...prev.credentials, [key]: value } }));
    setTestResult(null);
  };

  const setExtraField = (key: string, value: string) => {
    setDraft((prev) => ({ ...prev, extraConfig: { ...prev.extraConfig, [key]: value } }));
    setTestResult(null);
  };

  const setEnabled = (value: boolean) => {
    setDraft((prev) => ({ ...prev, enabled: value }));
  };

  const handleSave = async () => {
    await onSave(draft);
  };

  const handleTest = async () => {
    if (!draft.provider) return;
    setTesting(true);
    setTestResult(null);
    try {
      const result = await onTest(draft);
      setTestResult(result);
    } catch (error) {
      setTestResult({
        success: false,
        message: error instanceof Error ? error.message : '测试连接失败（网络错误）',
      });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <p className="text-[12px] text-gray-500 leading-relaxed">
            语音识别 API（如火山豆包 ASR）只接受公网可访问的音频 URL，不接受 binary 直传。
            这里配置一个对象存储桶，系统会把你上传的录音先放进桶里拿到 URL，再交给语音识别模型转写。
          </p>
          {settings?.managedByCloud && (
            <p className="text-[12px] text-emerald-700 mt-2">
              已使用组织管理员配置的对象存储{settings.hasCredentials ? '，成员可直接使用相关文件能力。' : '，但凭证尚未完整。'}
            </p>
          )}
        </div>
        {canEdit && (
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={isSaving || !draft.provider}
            className="shrink-0 rounded-xl bg-[#5B7BFE] px-4 py-2 text-[12px] font-bold text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isSaving ? '保存中…' : '保存'}
          </button>
        )}
      </div>

      <div className="space-y-3">
        <label className="text-[12px] font-bold text-gray-700">服务商</label>
        <select
          value={draft.provider}
          onChange={(e) => setProvider(e.target.value)}
          disabled={!canEdit}
          className="w-full rounded-2xl border border-gray-200 bg-white px-4 py-3 text-[13px] font-medium text-gray-900 outline-none focus:border-[#5B7BFE] disabled:bg-gray-50"
        >
          <option value="">— 请选择对象存储服务商 —</option>
          {OBJECT_STORAGE_PROVIDERS.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
              {!p.supported ? ' · 敬请期待' : ''}
            </option>
          ))}
        </select>
        {descriptor && <p className="text-[12px] text-gray-500">{descriptor.description}</p>}
        {descriptor && !descriptor.supported && (
          <div className="flex items-start gap-2 rounded-2xl border border-amber-100 bg-amber-50 px-3 py-2 text-[12px] text-amber-700">
            <AlertCircle size={14} className="mt-0.5 shrink-0" />
            <span>{descriptor.unsupportedHint || '此服务商暂未支持，敬请期待。'}</span>
          </div>
        )}
      </div>

      {descriptor && (
        <>
          <div className="space-y-3 border-t border-gray-100 pt-4">
            <p className="text-[12px] font-bold text-gray-700">鉴权凭证</p>
            {descriptor.credentialFields.map((field) => (
              <div key={field.key} className="space-y-1.5">
                <label className="text-[12px] text-gray-600">{field.label}</label>
                <input
                  type={field.type === 'password' ? 'password' : 'text'}
                  value={draft.credentials[field.key] || ''}
                  onChange={(e) => setCredentialField(field.key, e.target.value)}
                  placeholder={field.placeholder || ''}
                  disabled={!canEdit}
                  className="w-full rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 text-[13px] font-medium text-gray-900 outline-none focus:border-[#5B7BFE] focus:bg-white disabled:bg-gray-100"
                />
                {field.helper && <p className="text-[11px] text-gray-400">{field.helper}</p>}
              </div>
            ))}
          </div>

          {descriptor.extraFields.length > 0 && (
            <div className="space-y-3 border-t border-gray-100 pt-4">
              <p className="text-[12px] font-bold text-gray-700">桶配置</p>
              {descriptor.extraFields.map((field) => (
                <div key={field.key} className="space-y-1.5">
                  <label className="text-[12px] text-gray-600">{field.label}</label>
                  <input
                    type={field.type === 'password' ? 'password' : 'text'}
                    value={draft.extraConfig[field.key] || ''}
                    onChange={(e) => setExtraField(field.key, e.target.value)}
                    placeholder={field.placeholder || ''}
                    disabled={!canEdit}
                    className="w-full rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 text-[13px] font-medium text-gray-900 outline-none focus:border-[#5B7BFE] focus:bg-white disabled:bg-gray-100"
                  />
                  {field.helper && <p className="text-[11px] text-gray-400">{field.helper}</p>}
                </div>
              ))}
            </div>
          )}

          <div className="flex items-center gap-3 border-t border-gray-100 pt-4">
            <label className="inline-flex items-center gap-2 text-[12px] font-medium text-gray-700">
              <input
                type="checkbox"
                checked={draft.enabled}
                onChange={(e) => setEnabled(e.target.checked)}
                disabled={!canEdit}
              />
              启用对象存储（保存后文件中转、录音转写等能力才能使用）
            </label>
          </div>

          <div className="flex items-center gap-3 border-t border-gray-100 pt-4">
            <button
              type="button"
              onClick={() => void handleTest()}
              disabled={testing || !canEdit || !draft.provider || !isSupported}
              className="inline-flex items-center gap-2 rounded-2xl border border-gray-200 bg-white px-4 py-2 text-[12px] font-bold text-gray-700 hover:border-[#5B7BFE] hover:text-[#5B7BFE] disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RefreshCw size={14} className={testing ? 'animate-spin' : ''} />
              {testing ? '测试中…' : '测试连接'}
            </button>
            {testResult && (
              <div
                className={`flex items-start gap-2 rounded-2xl border px-3 py-2 text-[12px] ${
                  testResult.success
                    ? 'border-emerald-100 bg-emerald-50 text-emerald-700'
                    : 'border-rose-100 bg-rose-50 text-rose-700'
                }`}
              >
                {testResult.success ? (
                  <CheckCircle2 size={14} className="mt-0.5 shrink-0" />
                ) : (
                  <AlertCircle size={14} className="mt-0.5 shrink-0" />
                )}
                <div className="space-y-0.5">
                  <p className="font-medium">{testResult.message}</p>
                  {testResult.detail && <p className="text-[11px] opacity-80">{testResult.detail}</p>}
                  {typeof testResult.latencyMs === 'number' && (
                    <p className="text-[11px] opacity-70">耗时 {Math.round(testResult.latencyMs)} ms</p>
                  )}
                </div>
              </div>
            )}
          </div>
        </>
      )}

      {!canEdit && (
        <p className="text-[12px] text-amber-700 bg-amber-50 border border-amber-100 rounded-2xl px-4 py-3">
          当前账号没有修改组织对象存储配置的权限，仅可查看；如需调整桶、区域或密钥，请联系组织管理员。
        </p>
      )}
    </div>
  );
}
