export type AgentMemoryCapability = 'chat_memory' | 'skill' | 'wiki';

export type AgentMemoryLayer = 'L0' | 'L1' | 'L2' | 'L3';

export type AgentMemoryFactKind =
  | 'human_correction'
  | 'explicit_memory'
  | 'favorite'
  | 'stable_preference'
  | 'long_term_goal'
  | 'unresolved_lead';

export type AgentMemorySourceKind =
  | 'local_original'
  | 'organization_knowledge'
  | 'official_website_fact'
  | 'explicit_memory'
  | 'favorite'
  | 'system_inference';

interface AgentMemoryScopeBase {
  sandboxId: string;
  cloudInstanceId: string;
  organizationId: string;
  principalId: string;
}

export type AgentMemoryWorkspaceScope =
  | (AgentMemoryScopeBase & { scopeKind: 'client'; clientId: string })
  | (AgentMemoryScopeBase & { scopeKind: 'principal' | 'organization'; clientId: null });

export type AgentMemoryScope = AgentMemoryWorkspaceScope & { agentId: string };

export interface AgentMemoryFactDraft {
  factKind: AgentMemoryFactKind;
  statement: string;
  sourceAssetIds: string[];
  evidenceLinkIds: string[];
  supersedesFactId: string | null;
}

export interface AgentMemoryFactRecord extends AgentMemoryFactDraft {
  factId: string;
  version: number;
  lifecycleState: 'active' | 'superseded' | 'revoked';
  createdAt: string;
  updatedAt: string;
}

export interface AgentMemorySearchRequest {
  query: string;
  factKinds: AgentMemoryFactKind[];
  limit: number;
}

export interface AgentMemorySearchHit {
  fact: AgentMemoryFactRecord;
  sourceKind: AgentMemorySourceKind;
  sourceLabel: string;
  score: number;
}

export interface AgentMemoryFactAuthorityPort {
  search(scope: AgentMemoryScope, request: AgentMemorySearchRequest): Promise<AgentMemorySearchHit[]>;
  write(
    scope: AgentMemoryScope,
    draft: AgentMemoryFactDraft,
    command: { operationKey: string; expectedVersion: number | null },
  ): Promise<AgentMemoryFactRecord>;
  revoke(
    scope: AgentMemoryScope,
    factId: string,
    command: { operationKey: string; expectedVersion: number; reason: string },
  ): Promise<AgentMemoryFactRecord>;
}

export interface AgentSkillDraft {
  shortName: string;
  description: string;
  instructions: string[];
  outputTemplate: string | null;
  allowedToolIds: string[];
  visibility: 'private' | 'organization' | 'department' | 'selected_members';
  granteePrincipalIds: string[];
}

export interface AgentSkillRecord extends AgentSkillDraft {
  skillId: string;
  version: number;
  enabled: boolean;
  publisherPrincipalId: string;
  contentHash: string;
}

export interface AgentSkillAuthorityPort {
  get(scope: AgentMemoryScope, skillId: string): Promise<AgentSkillRecord | null>;
  listEnabled(scope: AgentMemoryScope): Promise<AgentSkillRecord[]>;
  publish(
    scope: AgentMemoryScope,
    draft: AgentSkillDraft,
    command: { operationKey: string; expectedVersion: number | null },
  ): Promise<AgentSkillRecord>;
  setEnabled(
    scope: AgentMemoryScope,
    skillId: string,
    enabled: boolean,
    command: { operationKey: string; expectedVersion: number },
  ): Promise<AgentSkillRecord>;
}

interface AgentWikiSourceBase {
  sourceAssetId: string;
  documentVersionId: string;
  title: string;
  contentHash: string;
}

export interface AgentWikiLocalSource extends AgentWikiSourceBase {
  contentBoundary: 'local_original';
  content: string;
}

export interface AgentWikiOrganizationSource extends AgentWikiSourceBase {
  contentBoundary: 'organization_published';
  content: string;
}

export type AgentWikiSource = AgentWikiLocalSource | AgentWikiOrganizationSource;

export interface AgentWikiChunkDraft {
  sourceAssetId: string;
  documentVersionId: string;
  ordinal: number;
  text: string;
  contentHash: string;
}

export interface AgentWikiChunk extends AgentWikiChunkDraft {
  chunkId: string;
}

export interface AgentWikiSearchHit {
  chunk: AgentWikiChunk;
  sourceKind: AgentMemorySourceKind;
  sourceLabel: string;
  score: number;
}

export interface AgentWikiProjectionPort<
  TSource extends AgentWikiSource = AgentWikiSource,
> {
  replaceSourceProjection(
    scope: AgentMemoryScope,
    source: TSource,
    chunks: AgentWikiChunkDraft[],
    command: { operationKey: string; expectedDocumentVersion: number | null },
  ): Promise<{ knowledgeDocumentId: string; documentVersion: number }>;
  search(scope: AgentMemoryScope, query: string, limit: number): Promise<AgentWikiSearchHit[]>;
  invalidateSource(
    scope: AgentMemoryScope,
    sourceAssetId: string,
    command: { operationKey: string; reason: string },
  ): Promise<void>;
}

export type AgentWikiLocalProjectionPort = AgentWikiProjectionPort<AgentWikiLocalSource>;
export type AgentWikiOrganizationProjectionPort =
  AgentWikiProjectionPort<AgentWikiOrganizationSource>;

export interface AgentMemoryModelRequest {
  purpose: 'memory_extract' | 'memory_deduplicate' | 'skill_extract' | 'wiki_build';
  systemInstruction: string;
  userText: string;
}

export interface AgentMemoryModelPort {
  generate(scope: AgentMemoryScope, request: AgentMemoryModelRequest): Promise<{ text: string }>;
}

export interface AgentMemoryCacheEntry {
  cacheKey: string;
  contentHash: string;
  text: string;
  expiresAt: string | null;
}

export interface AgentMemoryRebuildableCachePort {
  get(scope: AgentMemoryScope, cacheKey: string): Promise<AgentMemoryCacheEntry | null>;
  put(scope: AgentMemoryScope, entry: AgentMemoryCacheEntry): Promise<void>;
  invalidate(scope: AgentMemoryScope, cacheKey: string): Promise<void>;
}

export interface AgentMemoryAuditPort {
  record(scope: AgentMemoryScope, event: {
    eventType: string;
    aggregateType: 'memory_fact' | 'agent_skill' | 'wiki_projection';
    aggregateId: string;
    operationKey: string;
    result: 'ready' | 'blocked' | 'failed_retryable' | 'failed';
  }): Promise<void>;
}

export interface AgentMemoryHostPorts {
  facts: AgentMemoryFactAuthorityPort;
  skills: AgentSkillAuthorityPort;
  localWiki: AgentWikiLocalProjectionPort;
  organizationWiki: AgentWikiOrganizationProjectionPort;
  model: AgentMemoryModelPort;
  cache: AgentMemoryRebuildableCachePort;
  audit: AgentMemoryAuditPort;
}

const SCOPE_KEYS: Array<keyof AgentMemoryScopeBase> = [
  'sandboxId',
  'cloudInstanceId',
  'organizationId',
  'principalId',
];

export function assertAgentMemoryWorkspaceScope(scope: AgentMemoryWorkspaceScope): void {
  for (const key of SCOPE_KEYS) {
    if (!scope[key].trim()) {
      throw new Error(`agent_memory_scope_missing:${key}`);
    }
  }
  if (scope.scopeKind === 'client' && !scope.clientId.trim()) {
    throw new Error('agent_memory_scope_missing:clientId');
  }
  if (scope.scopeKind !== 'client' && scope.clientId !== null) {
    throw new Error('agent_memory_scope_unexpected:clientId');
  }
}

export function assertAgentMemoryScope(scope: AgentMemoryScope): void {
  assertAgentMemoryWorkspaceScope(scope);
  if (!scope.agentId.trim()) {
    throw new Error('agent_memory_scope_missing:agentId');
  }
}

export function bindAgentMemoryAgent(
  scope: AgentMemoryWorkspaceScope,
  agentId: string,
): AgentMemoryScope {
  assertAgentMemoryWorkspaceScope(scope);
  if (!agentId.trim()) throw new Error('agent_memory_scope_missing:agentId');
  return { ...scope, agentId };
}

export function buildAgentMemoryScopeKey(scope: AgentMemoryScope): string {
  assertAgentMemoryScope(scope);
  return [
    scope.scopeKind,
    ...SCOPE_KEYS.map((key) => scope[key]),
    scope.agentId,
    scope.clientId ?? '-',
  ].map((value) => encodeURIComponent(value)).join('/');
}
