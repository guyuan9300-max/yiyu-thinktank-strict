export const CLIENT_KNOWLEDGE_CHANGED_EVENT = 'yiyu:client-knowledge-changed';

export type ClientKnowledgeChangeReason =
  | 'material_imported'
  | 'memory_updated'
  | 'fact_corrected'
  | 'official_website_refreshed'
  | 'client_updated'
  | 'narrative_updated';

const clientKnowledgeRevisions = new Map<string, number>();

export function markClientKnowledgeChanged(
  clientId: string,
  reason: ClientKnowledgeChangeReason,
): number {
  if (!clientId) return 0;
  const revision = Date.now();
  clientKnowledgeRevisions.set(clientId, revision);
  window.dispatchEvent(new CustomEvent(CLIENT_KNOWLEDGE_CHANGED_EVENT, {
    detail: { clientId, reason, revision },
  }));
  return revision;
}

export function getClientKnowledgeRevision(clientId: string): number {
  return clientKnowledgeRevisions.get(clientId) || 0;
}

