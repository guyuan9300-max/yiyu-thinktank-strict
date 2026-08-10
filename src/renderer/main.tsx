import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { AppPromptHost } from './lib/appPrompt';
import './styles.css';

declare global {
  interface Window {
    __YIYU_BOOT_EVENTS__?: string[];
    __YIYU_APP_RENDERED__?: boolean;
    __YIYU_HIDE_BOOT_DIAGNOSTIC__?: () => void;
  }
}

function recordBootEvent(event: string) {
  if (!Array.isArray(window.__YIYU_BOOT_EVENTS__)) {
    window.__YIYU_BOOT_EVENTS__ = [];
  }
  window.__YIYU_BOOT_EVENTS__.push(`${new Date().toISOString()} ${event}`);
  console.info(`[renderer:boot] ${event}`);
}

class RendererErrorBoundary extends React.Component<
  React.PropsWithChildren,
  { error: Error | null; stack: string }
> {
  state = {
    error: null as Error | null,
    stack: '',
  };

  static getDerivedStateFromError(error: Error) {
    return { error, stack: '' };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    recordBootEvent(`error-boundary:${error.name}:${error.message}`);
    console.error(`[renderer:error-boundary] ${error.name}: ${error.message}\n${error.stack || ''}\n${info.componentStack || ''}`);
    this.setState({ error, stack: info.componentStack || '' });
  }

  render() {
    if (!this.state.error) {
      return this.props.children;
    }

    return (
      <div className="min-h-screen bg-[#F9FAFB] px-8 py-10 text-gray-800">
        <div className="mx-auto max-w-[960px] rounded-[28px] border border-rose-100 bg-white p-8 shadow-[0_20px_60px_rgba(15,23,42,0.12)]">
          <p className="text-[12px] font-bold tracking-[0.22em] text-rose-500 uppercase">Renderer Startup Failed</p>
          <h1 className="mt-3 text-[28px] font-bold text-gray-900">桌面界面启动失败</h1>
          <p className="mt-4 text-[14px] leading-7 text-gray-600">
            React 在渲染阶段捕获到错误，已经阻止白屏。请把下面的信息发给我，我会继续修复。
          </p>

          <div className="mt-6 rounded-2xl border border-rose-100 bg-rose-50 px-4 py-4">
            <p className="text-[13px] font-bold text-rose-700">
              {this.state.error.name}: {this.state.error.message}
            </p>
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <div className="rounded-2xl border border-gray-200 bg-gray-50 px-4 py-4">
              <p className="text-[12px] font-bold tracking-[0.18em] text-gray-500 uppercase">Boot Events</p>
              <pre className="mt-3 whitespace-pre-wrap text-[12px] leading-6 text-gray-700">
                {(window.__YIYU_BOOT_EVENTS__ || []).join('\n') || 'No boot events'}
              </pre>
            </div>
            <div className="rounded-2xl border border-gray-200 bg-gray-50 px-4 py-4">
              <p className="text-[12px] font-bold tracking-[0.18em] text-gray-500 uppercase">Component Stack</p>
              <pre className="mt-3 whitespace-pre-wrap text-[12px] leading-6 text-gray-700">
                {this.state.stack || 'No component stack'}
              </pre>
            </div>
          </div>
        </div>
      </div>
    );
  }
}

function BootMarker() {
  React.useEffect(() => {
    window.__YIYU_APP_RENDERED__ = true;
    recordBootEvent('app-committed');
    window.__YIYU_HIDE_BOOT_DIAGNOSTIC__?.();
  }, []);
  return null;
}

recordBootEvent('main.tsx:module-evaluated');

window.addEventListener('error', (event) => {
  const detail = event.error instanceof Error
    ? `${event.error.name}: ${event.error.message}\n${event.error.stack || ''}`
    : `${event.message} @ ${event.filename}:${event.lineno}:${event.colno}`;
  recordBootEvent(`window-error:${detail.split('\n')[0]}`);
  console.error(`[renderer:window-error] ${detail}`);
});

window.addEventListener('unhandledrejection', (event) => {
  const reason = event.reason instanceof Error
    ? `${event.reason.name}: ${event.reason.message}\n${event.reason.stack || ''}`
    : String(event.reason);
  recordBootEvent(`unhandled-rejection:${reason.split('\n')[0]}`);
  console.error(`[renderer:unhandled-rejection] ${reason}`);
});

const rootElement = document.getElementById('root');

if (!rootElement) {
  throw new Error('Renderer root element not found');
}

rootElement.innerHTML = '<div style="min-height:100vh;display:flex;align-items:center;justify-content:center;background:#F9FAFB;color:#6B7280;font:500 14px PingFang SC, sans-serif;">正在初始化界面…</div>';
recordBootEvent('main.tsx:before-createRoot');

const root = ReactDOM.createRoot(rootElement);
recordBootEvent('main.tsx:before-root-render');

root.render(
  <React.StrictMode>
    <RendererErrorBoundary>
      <BootMarker />
      <App />
      <AppPromptHost />
    </RendererErrorBoundary>
  </React.StrictMode>
);
