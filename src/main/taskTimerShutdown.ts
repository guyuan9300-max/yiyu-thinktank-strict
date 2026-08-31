export type PauseRunningTaskTimersResult = {
  state?: string;
  pausedCount?: number;
  taskIds?: string[];
  observedAt?: string;
};

type FetchLike = typeof fetch;

export async function requestPauseRunningTaskTimers(options: {
  port: number;
  desktopToken: string;
  reason: 'app_quit' | 'window_closed';
  timeoutMs?: number;
  fetchImpl?: FetchLike;
}): Promise<PauseRunningTaskTimersResult> {
  if (!Number.isInteger(options.port) || options.port <= 0) {
    throw new Error('本地后端端口尚未就绪');
  }
  if (!options.desktopToken.trim()) {
    throw new Error('本地后端授权尚未就绪');
  }
  const controller = new AbortController();
  const timeout = setTimeout(
    () => controller.abort(),
    Math.max(250, options.timeoutMs ?? 4_000),
  );
  try {
    const response = await (options.fetchImpl ?? fetch)(
      `http://127.0.0.1:${options.port}/api/v2/ui/tasks/timers/pause-running`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Yiyu-Desktop-Token': options.desktopToken,
          'Idempotency-Key': `desktop-timer-pause-${options.reason}-${Date.now()}`,
        },
        body: JSON.stringify({ reason: options.reason }),
        signal: controller.signal,
      },
    );
    if (!response.ok) {
      throw new Error(`退出前暂停个人计时失败，状态码 ${response.status}`);
    }
    return await response.json() as PauseRunningTaskTimersResult;
  } finally {
    clearTimeout(timeout);
  }
}
