import type { ReactNode } from 'react';

interface ClientWorkspaceViewProps {
  children: () => ReactNode;
}

export function ClientWorkspaceView({ children }: ClientWorkspaceViewProps) {
  return <>{children()}</>;
}
