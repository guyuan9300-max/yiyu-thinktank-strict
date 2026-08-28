export const BRAND_SETTINGS_UNLOCK_CLICKS = 5;
export const BRAND_SETTINGS_UNLOCK_WINDOW_MS = 4_000;

export interface BrandSettingsUnlockProgress {
  count: number;
  startedAt: number;
  unlocked: boolean;
}

export function nextBrandSettingsUnlockProgress(
  current: BrandSettingsUnlockProgress,
  clickedAt: number,
): BrandSettingsUnlockProgress {
  if (current.unlocked) return current;
  const expired = current.count === 0
    || clickedAt < current.startedAt
    || clickedAt - current.startedAt > BRAND_SETTINGS_UNLOCK_WINDOW_MS;
  const count = expired ? 1 : current.count + 1;
  return {
    count,
    startedAt: expired ? clickedAt : current.startedAt,
    unlocked: count >= BRAND_SETTINGS_UNLOCK_CLICKS,
  };
}

export function organizationBrandUnlockSessionKey(scopeKey: string): string {
  return `yiyu.organization-brand-settings-unlocked.v1:${scopeKey}`;
}
