export type StartupGateStatus = 'ok' | 'warning' | 'blocked';
export type InstallReceiptStatus = 'ok' | 'missing' | 'mismatch';
export type InstallSmokeStatus = 'ok' | 'missing' | 'failed';
export type InstallPathStatus = 'recommended' | 'unexpected';
export type ManifestStatus = 'ok' | 'missing' | 'invalid';
export type BackendManifestStatus = 'ok' | 'missing' | 'mismatch';
export type BackendFeatureStatus = 'ok' | 'missing';
export type BackendSchemaStatus = 'ok' | 'stale';
export type BackendRuntimeModeStatus = 'ok' | 'mismatch';

export type DesktopStartupGateInput = {
  isPackaged: boolean;
  installPathStatus: InstallPathStatus;
  manifestStatus: ManifestStatus;
  installReceiptStatus: InstallReceiptStatus;
  installSmokeStatus: InstallSmokeStatus;
  backendManifestStatus: BackendManifestStatus;
  backendFeatureStatus: BackendFeatureStatus;
  backendSchemaStatus: BackendSchemaStatus;
  backendRuntimeModeStatus: BackendRuntimeModeStatus;
  legacyAppCount: number;
  duplicateAppCount: number;
};

export type DesktopStartupGateResult = {
  status: StartupGateStatus;
  reason: string | null;
  installStatus: 'ok' | 'warning';
  installWarning: string | null;
};

function duplicateWarning(input: DesktopStartupGateInput): string | null {
  if (input.legacyAppCount > 0) {
    return '当前安装可能不是最新，请更新。';
  }
  if (input.duplicateAppCount > 0) {
    return '当前安装可能不是最新，请更新。';
  }
  return null;
}

function installUpdateWarning(input: DesktopStartupGateInput): string | null {
  const hasInstallDrift =
    input.installPathStatus !== 'recommended'
    || input.manifestStatus !== 'ok'
    || input.installReceiptStatus !== 'ok'
    || input.installSmokeStatus !== 'ok'
    || input.backendManifestStatus !== 'ok'
    || input.backendFeatureStatus !== 'ok'
    || input.backendSchemaStatus !== 'ok'
    || input.backendRuntimeModeStatus !== 'ok';
  return hasInstallDrift ? '当前安装可能不是最新，请更新。' : null;
}

export function evaluateDesktopStartupGate(input: DesktopStartupGateInput): DesktopStartupGateResult {
  const installWarning = duplicateWarning(input) || installUpdateWarning(input);
  if (!input.isPackaged) {
    return {
      status: installWarning ? 'warning' : 'ok',
      reason: null,
      installStatus: installWarning ? 'warning' : 'ok',
      installWarning,
    };
  }

  return {
    status: installWarning ? 'warning' : 'ok',
    reason: null,
    installStatus: installWarning ? 'warning' : 'ok',
    installWarning,
  };
}
