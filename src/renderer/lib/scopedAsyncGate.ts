export type ScopedAsyncToken = {
  scopeKey: string;
  sequence: number;
  signal: AbortSignal;
};

export class ScopedAsyncGate {
  private scopeKey = '';
  private sequence = 0;
  private controller: AbortController | null = null;

  setScope(scopeKey: string): void {
    if (scopeKey === this.scopeKey) return;
    this.scopeKey = scopeKey;
    this.sequence += 1;
    this.controller?.abort();
    this.controller = null;
  }

  begin(scopeKey: string): ScopedAsyncToken {
    this.setScope(scopeKey);
    this.sequence += 1;
    this.controller?.abort();
    this.controller = new AbortController();
    return {
      scopeKey,
      sequence: this.sequence,
      signal: this.controller.signal,
    };
  }

  accepts(token: ScopedAsyncToken): boolean {
    return (
      !token.signal.aborted
      && token.scopeKey === this.scopeKey
      && token.sequence === this.sequence
    );
  }

  cancel(): void {
    this.sequence += 1;
    this.controller?.abort();
    this.controller = null;
  }
}

export function scopedAsyncKey(...parts: Array<string | null | undefined>): string {
  return parts.map((part) => String(part || '')).join('::');
}
