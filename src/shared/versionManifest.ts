import { createHash } from 'node:crypto';

export type BundleVersionManifest = {
  appVersion: string;
  buildVersion: string;
  gitCommit: string | null;
  builtAt: string;
  rendererEntry: string;
  rendererHash: string;
  backendSourceHash: string;
  schemaVersionMin: number;
};

function stableSerialize(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableSerialize(item)).join(',')}]`;
  }
  if (value && typeof value === 'object') {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${stableSerialize(item)}`)
      .join(',')}}`;
  }
  return JSON.stringify(value);
}

export function computeBundleManifestId(manifest: BundleVersionManifest): string {
  return createHash('sha256').update(stableSerialize(manifest)).digest('hex');
}

export function bundleManifestMatches(left: BundleVersionManifest | null, right: BundleVersionManifest | null): boolean {
  if (!left || !right) {
    return false;
  }
  return computeBundleManifestId(left) === computeBundleManifestId(right);
}
