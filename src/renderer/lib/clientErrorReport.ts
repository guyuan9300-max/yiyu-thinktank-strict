/**
 * 前端错误上报: 让所有 UI 错误自动进 backend jsonl 日志.
 *
 * 设计:
 *   - fire-and-forget (sendBeacon / fetch keepalive), 不阻塞 UI
 *   - 失败仅 console.warn, 不再 flash 否则死循环
 *   - 仅 error/warn 入日志, 不上报 success/info (避免噪声)
 *
 * 用法:
 *   import { reportClientError, alertWithLog } from './lib/clientErrorReport';
 *   reportClientError('error', '某操作失败');         // 静默上报
 *   alertWithLog('某操作失败');                       // 弹窗 + 上报
 */

const CLIENT_ERROR_ENDPOINT = 'http://127.0.0.1:47829/api/v2/ui/system/client-error';

export type ClientErrorLevel = 'error' | 'warn';

interface ReportPayload {
  level: ClientErrorLevel;
  message: string;
  route?: string;
  feature?: string;
  userAgent?: string;
  extra?: unknown;
}

export function reportClientError(
  level: ClientErrorLevel,
  message: string,
  options?: { feature?: string; extra?: unknown },
): void {
  try {
    const payload: ReportPayload = {
      level,
      message,
      route: typeof window !== 'undefined' ? window.location.hash || window.location.pathname : '',
      userAgent: typeof navigator !== 'undefined' ? navigator.userAgent : '',
      feature: options?.feature,
      extra: options?.extra,
    };
    const body = JSON.stringify(payload);
    // sendBeacon 优先 (浏览器卸载时也能送达); 失败 / 不支持时降级到 fetch keepalive.
    if (typeof navigator !== 'undefined' && 'sendBeacon' in navigator) {
      const blob = new Blob([body], { type: 'application/json' });
      if (navigator.sendBeacon(CLIENT_ERROR_ENDPOINT, blob)) return;
    }
    void fetch(CLIENT_ERROR_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      keepalive: true,
    }).catch(() => undefined);
  } catch {
    // 上报机制本身失败 — 不再 flash 否则可能死循环, 只 console.
    // eslint-disable-next-line no-console
    console.warn('[client-error report] swallow', message);
  }
}

/**
 * 替代 window.alert: 弹原生对话框 + 自动上报为 error.
 * 用于"必须用户立即看到"的错误 (比如选区失效, 上传严重错误).
 */
export function alertWithLog(message: string, options?: { feature?: string; extra?: unknown }): void {
  reportClientError('error', message, options);
  try {
    if (typeof window !== 'undefined' && typeof window.alert === 'function') {
      window.alert(message);
    }
  } catch {
    // alert 被禁用 (extension / popup blocker) 时, 至少已上报
  }
}
