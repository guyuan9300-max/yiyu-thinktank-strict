export const DEFAULT_RENDERER_WORKSPACE_THREAD = 'latest';

type RendererLaunchQueryOptions = {
  packaged: boolean;
  defaultWorkspaceThread?: string;
};

function isSettingsNavigationValue(value: string | null) {
  return (value || '').trim() === 'settings';
}

export function removeSettingsLaunchNavigation(params: URLSearchParams) {
  const targetsSettings =
    isSettingsNavigationValue(params.get('tab'))
    || isSettingsNavigationValue(params.get('activeTab'));
  if (!targetsSettings) return false;

  params.delete('tab');
  params.delete('activeTab');
  params.delete('settingsSection');
  params.delete('section');
  return true;
}

export function buildRendererLaunchQuery(rawValue: string | null | undefined, options: RendererLaunchQueryOptions) {
  const query = (rawValue || '').trim().replace(/^\?+/, '');
  const params = new URLSearchParams(query);
  removeSettingsLaunchNavigation(params);

  if (options.packaged && !params.has('workspaceThread')) {
    params.set('workspaceThread', options.defaultWorkspaceThread || DEFAULT_RENDERER_WORKSPACE_THREAD);
  }

  const serialized = params.toString();
  return serialized ? `?${serialized}` : '';
}
