import React, { useEffect, useRef, useState } from 'react';
import { CheckCircle2, ImagePlus, RefreshCw, RotateCcw, Save } from 'lucide-react';
import { updateOrganizationBrandSettings } from '../../lib/api';
import {
  DEFAULT_ORGANIZATION_BRAND_NAME,
  loadOrganizationBrand,
  organizationBrandDisplayName,
  publishOrganizationBrand,
  useOrganizationBrand,
} from '../../lib/organizationBrandStore';
import { BrandLogoMark } from './BrandLogoSettingsCard';

const MAX_SOURCE_FILE_BYTES = 5 * 1024 * 1024;
const MAX_PROCESSED_IMAGE_BYTES = 150 * 1024;

function dataUrlByteLength(dataUrl: string): number {
  const encoded = dataUrl.split(',', 2)[1] || '';
  return Math.ceil((encoded.length * 3) / 4);
}

async function processLogoFile(file: File): Promise<string> {
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
    throw new Error('仅支持 PNG、JPEG 或 WebP 图片');
  }
  if (file.size > MAX_SOURCE_FILE_BYTES) {
    throw new Error('原始图片不能超过 5 MB');
  }
  const objectUrl = URL.createObjectURL(file);
  try {
    const image = await new Promise<HTMLImageElement>((resolve, reject) => {
      const candidate = new Image();
      candidate.onload = () => resolve(candidate);
      candidate.onerror = () => reject(new Error('无法读取该图片'));
      candidate.src = objectUrl;
    });
    for (const [size, quality] of [[256, 0.9], [224, 0.82], [192, 0.74], [160, 0.68]] as const) {
      const canvas = document.createElement('canvas');
      canvas.width = size;
      canvas.height = size;
      const context = canvas.getContext('2d');
      if (!context) throw new Error('当前设备无法处理 Logo');
      const ratio = Math.min(size / image.naturalWidth, size / image.naturalHeight);
      const width = Math.max(1, Math.round(image.naturalWidth * ratio));
      const height = Math.max(1, Math.round(image.naturalHeight * ratio));
      context.clearRect(0, 0, size, size);
      context.drawImage(image, Math.round((size - width) / 2), Math.round((size - height) / 2), width, height);
      const dataUrl = canvas.toDataURL('image/webp', quality);
      if (dataUrlByteLength(dataUrl) <= MAX_PROCESSED_IMAGE_BYTES) return dataUrl;
    }
    throw new Error('Logo 处理后仍过大，请选择更简洁的图片');
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

export function OrganizationBrandSettingsCard({
  organizationScopeKey,
}: {
  organizationScopeKey: string;
}): React.ReactElement {
  const brand = useOrganizationBrand(organizationScopeKey);
  const [displayName, setDisplayName] = useState('');
  const [logoDataUrl, setLogoDataUrl] = useState('');
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (brand.status === 'ready' && !dirty) {
      setDisplayName(brand.displayName);
      setLogoDataUrl(brand.logoDataUrl);
    }
  }, [brand.displayName, brand.logoDataUrl, brand.status, dirty]);

  const handleFile = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = '';
    if (!file) return;
    setMessage(null);
    try {
      setLogoDataUrl(await processLogoFile(file));
      setDirty(true);
    } catch (error) {
      setMessage({ kind: 'error', text: error instanceof Error ? error.message : '图片处理失败' });
    }
  };

  const handleSave = async () => {
    if (!organizationScopeKey || saving) return;
    const normalizedName = displayName.trim();
    if (normalizedName.length > 32) {
      setMessage({ kind: 'error', text: '组织品牌名称不能超过 32 个字符' });
      return;
    }
    setSaving(true);
    setMessage(null);
    try {
      const saved = await updateOrganizationBrandSettings({
        displayName: normalizedName,
        logoDataUrl,
        expectedVersion: brand.expectedVersion,
      });
      publishOrganizationBrand(organizationScopeKey, saved);
      setDisplayName(saved.displayName);
      setLogoDataUrl(saved.logoDataUrl);
      setDirty(false);
      setMessage({ kind: 'success', text: '组织品牌已保存，当前组织成员下次读取时会看到新品牌。' });
    } catch (error) {
      const text = error instanceof Error ? error.message : '保存失败';
      setMessage({ kind: 'error', text });
      if (text.includes('配置已变化') || text.includes('version')) {
        setDirty(false);
        await loadOrganizationBrand(organizationScopeKey, { force: true });
      }
    } finally {
      setSaving(false);
    }
  };

  const preview = {
    ...brand,
    displayName,
    logoDataUrl,
  };

  return (
    <section className="rounded-2xl border border-indigo-100 bg-white p-6 shadow-[0_12px_36px_rgba(91,123,254,0.06)]" aria-labelledby="organization-brand-settings-title">
      <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-indigo-400">ORGANIZATION BRAND</p>
          <h3 id="organization-brand-settings-title" className="mt-2 text-[18px] font-light tracking-tight text-gray-900">组织品牌设置</h3>
          <p className="mt-1.5 max-w-xl text-[12px] leading-6 text-gray-500">
            仅更改当前组织在软件内的品牌展示，不会修改组织法定名称、安装包名称、签名或更新链路。
          </p>
        </div>
        <div className="flex min-w-[180px] items-center gap-3 rounded-xl border border-gray-100 bg-gray-50/70 px-4 py-3">
          {preview.logoDataUrl ? (
            <img src={preview.logoDataUrl} alt="品牌 Logo 预览" className="h-10 w-10 shrink-0 object-contain" />
          ) : (
            <BrandLogoMark className="h-10 w-10" organizationScopeKey="" />
          )}
          <span className="min-w-0 truncate text-[14px] font-medium text-gray-800">
            {organizationBrandDisplayName(preview)}
          </span>
        </div>
      </div>

      <div className="mt-6 grid grid-cols-1 gap-5 sm:grid-cols-[minmax(0,1fr)_220px]">
        <label className="block">
          <span className="text-[11px] font-bold uppercase tracking-[0.12em] text-gray-400">展示名称</span>
          <input
            value={displayName}
            maxLength={32}
            onChange={(event) => {
              setDisplayName(event.target.value);
              setDirty(true);
              setMessage(null);
            }}
            placeholder={DEFAULT_ORGANIZATION_BRAND_NAME}
            className="mt-2 h-10 w-full rounded-lg border border-gray-200 bg-white px-3 text-[13px] text-gray-900 outline-none transition focus:border-indigo-300 focus:ring-2 focus:ring-indigo-100"
          />
          <span className="mt-1.5 block text-[11px] text-gray-400">留空时使用默认名称 “{DEFAULT_ORGANIZATION_BRAND_NAME}”。</span>
        </label>

        <div>
          <span className="text-[11px] font-bold uppercase tracking-[0.12em] text-gray-400">Logo</span>
          <input ref={fileInputRef} type="file" accept="image/png,image/jpeg,image/webp" onChange={handleFile} className="hidden" />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="mt-2 inline-flex h-10 w-full items-center justify-center gap-2 rounded-lg border border-gray-200 bg-white px-3 text-[13px] font-medium text-gray-700 transition hover:border-indigo-200 hover:bg-indigo-50/40"
          >
            <ImagePlus size={15} />选择图片
          </button>
          <span className="mt-1.5 block text-[11px] text-gray-400">PNG / JPEG / WebP，保存前自动压缩。</span>
        </div>
      </div>

      {brand.status === 'loading' && (
        <p className="mt-4 flex items-center gap-2 text-[12px] text-gray-400"><RefreshCw size={13} className="animate-spin" />正在读取云端组织品牌…</p>
      )}
      {brand.status === 'error' && !message && (
        <p className="mt-4 text-[12px] text-rose-600">{brand.errorMessage}</p>
      )}
      {message && (
        <p className={`mt-4 flex items-start gap-2 text-[12px] ${message.kind === 'success' ? 'text-emerald-600' : 'text-rose-600'}`} role="status">
          {message.kind === 'success' && <CheckCircle2 size={14} className="mt-px shrink-0" />}
          {message.text}
        </p>
      )}

      <div className="mt-6 flex flex-wrap items-center justify-end gap-3 border-t border-gray-100 pt-5">
        <button
          type="button"
          onClick={() => {
            setDisplayName('');
            setLogoDataUrl('');
            setDirty(true);
            setMessage(null);
          }}
          className="inline-flex h-9 items-center gap-2 rounded-md border border-gray-200 bg-white px-3 text-[12px] font-medium text-gray-600 transition hover:bg-gray-50"
        >
          <RotateCcw size={14} />恢复默认
        </button>
        <button
          type="button"
          onClick={() => void handleSave()}
          disabled={!dirty || saving || brand.status === 'loading' || !organizationScopeKey}
          className="inline-flex h-9 items-center gap-2 rounded-md bg-[#5B7BFE] px-4 text-[12px] font-medium text-white transition hover:bg-[#4A6AEF] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? <RefreshCw size={14} className="animate-spin" /> : <Save size={14} />}
          {saving ? '正在保存…' : '保存品牌'}
        </button>
      </div>
    </section>
  );
}
