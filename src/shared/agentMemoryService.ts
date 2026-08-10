import {
  bindAgentMemoryAgent,
  type AgentMemoryFactDraft,
  type AgentMemoryFactRecord,
  type AgentMemorySearchHit,
  type AgentMemorySearchRequest,
  type AgentMemoryWorkspaceScope,
  type AgentSkillDraft,
  type AgentSkillRecord,
  type AgentWikiSearchHit,
  type AgentWikiSource,
} from './agentMemoryPorts';
import {
  AgentMemoryAdapterBlockedError,
  createStrict88AgentMemoryHostPorts,
  resolveStrict88BuiltinAgent,
  type BuiltinAgentKind,
  type Strict88AgentMemoryDataPlane,
  type Strict88AgentMemorySchemaReadiness,
} from './agentMemoryStrict88Adapter';
import {
  chunkAgentWikiText,
  type AgentWikiChunkOptions,
} from './agentMemoryWikiChunker';

export interface AgentMemoryInvocationContext {
  scope: AgentMemoryWorkspaceScope;
  agentKind: BuiltinAgentKind;
}

export interface AgentMemoryCommand {
  operationKey: string;
  expectedVersion: number | null;
}

export interface AgentMemoryServiceStatus {
  state: 'ready' | 'blocked';
  code: 'ready' | 'agent_memory_schema_not_ready';
  missingTables: string[];
  missingFields: Array<{ table: string; field: string }>;
}

export interface AgentMemoryService {
  status(): AgentMemoryServiceStatus;
  remember(
    context: AgentMemoryInvocationContext,
    draft: AgentMemoryFactDraft,
    command: AgentMemoryCommand,
  ): Promise<AgentMemoryFactRecord>;
  recall(
    context: AgentMemoryInvocationContext,
    request: AgentMemorySearchRequest,
  ): Promise<AgentMemorySearchHit[]>;
  revokeMemory(
    context: AgentMemoryInvocationContext,
    factId: string,
    command: { operationKey: string; expectedVersion: number; reason: string },
  ): Promise<AgentMemoryFactRecord>;
  createSkill(
    context: AgentMemoryInvocationContext,
    draft: AgentSkillDraft,
    command: AgentMemoryCommand,
  ): Promise<AgentSkillRecord>;
  listEnabledSkills(context: AgentMemoryInvocationContext): Promise<AgentSkillRecord[]>;
  setSkillEnabled(
    context: AgentMemoryInvocationContext,
    skillId: string,
    enabled: boolean,
    command: { operationKey: string; expectedVersion: number },
  ): Promise<AgentSkillRecord>;
  buildWiki(
    context: AgentMemoryInvocationContext,
    source: AgentWikiSource,
    command: { operationKey: string; expectedDocumentVersion: number | null },
    options?: AgentWikiChunkOptions,
  ): Promise<{ knowledgeDocumentId: string; documentVersion: number; chunkCount: number }>;
  searchWiki(
    context: AgentMemoryInvocationContext,
    query: string,
    limit: number,
  ): Promise<AgentWikiSearchHit[]>;
  invalidateWikiSource(
    context: AgentMemoryInvocationContext,
    sourceAssetId: string,
    contentBoundary: AgentWikiSource['contentBoundary'],
    command: { operationKey: string; reason: string },
  ): Promise<void>;
}

async function sha256Text(text: string): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

class Strict88AgentMemoryService implements AgentMemoryService {
  private readonly ports;

  constructor(
    private readonly dataPlane: Strict88AgentMemoryDataPlane,
    private readonly schemaReadiness: Strict88AgentMemorySchemaReadiness,
  ) {
    this.ports = schemaReadiness.ready
      ? createStrict88AgentMemoryHostPorts(dataPlane, schemaReadiness)
      : null;
  }

  status(): AgentMemoryServiceStatus {
    return {
      state: this.schemaReadiness.ready ? 'ready' : 'blocked',
      code: this.schemaReadiness.ready ? 'ready' : 'agent_memory_schema_not_ready',
      missingTables: [...this.schemaReadiness.missingTables],
      missingFields: this.schemaReadiness.missingFields.map((item) => ({ ...item })),
    };
  }

  private requirePorts() {
    if (!this.ports) {
      throw new AgentMemoryAdapterBlockedError(
        'agent_memory_schema_not_ready',
        'Agent Memory 所需的严格88表字段尚未补齐',
      );
    }
    return this.ports;
  }

  private async resolveScope(context: AgentMemoryInvocationContext) {
    const agent = await resolveStrict88BuiltinAgent(
      this.dataPlane,
      context.scope,
      context.agentKind,
    );
    return bindAgentMemoryAgent(context.scope, agent.botId);
  }

  async remember(
    context: AgentMemoryInvocationContext,
    draft: AgentMemoryFactDraft,
    command: AgentMemoryCommand,
  ) {
    const ports = this.requirePorts();
    const scope = await this.resolveScope(context);
    return ports.facts.write(scope, draft, command);
  }

  async recall(context: AgentMemoryInvocationContext, request: AgentMemorySearchRequest) {
    const ports = this.requirePorts();
    const scope = await this.resolveScope(context);
    return ports.facts.search(scope, request);
  }

  async revokeMemory(
    context: AgentMemoryInvocationContext,
    factId: string,
    command: { operationKey: string; expectedVersion: number; reason: string },
  ) {
    const ports = this.requirePorts();
    const scope = await this.resolveScope(context);
    return ports.facts.revoke(scope, factId, command);
  }

  async createSkill(
    context: AgentMemoryInvocationContext,
    draft: AgentSkillDraft,
    command: AgentMemoryCommand,
  ) {
    const ports = this.requirePorts();
    const scope = await this.resolveScope(context);
    return ports.skills.publish(scope, draft, command);
  }

  async listEnabledSkills(context: AgentMemoryInvocationContext) {
    const ports = this.requirePorts();
    const scope = await this.resolveScope(context);
    return ports.skills.listEnabled(scope);
  }

  async setSkillEnabled(
    context: AgentMemoryInvocationContext,
    skillId: string,
    enabled: boolean,
    command: { operationKey: string; expectedVersion: number },
  ) {
    const ports = this.requirePorts();
    const scope = await this.resolveScope(context);
    return ports.skills.setEnabled(scope, skillId, enabled, command);
  }

  async buildWiki(
    context: AgentMemoryInvocationContext,
    source: AgentWikiSource,
    command: { operationKey: string; expectedDocumentVersion: number | null },
    options: AgentWikiChunkOptions = {},
  ) {
    const ports = this.requirePorts();
    const scope = await this.resolveScope(context);
    const texts = chunkAgentWikiText(source.content, options);
    const chunks = await Promise.all(texts.map(async (text, ordinal) => ({
      sourceAssetId: source.sourceAssetId,
      documentVersionId: source.documentVersionId,
      ordinal,
      text,
      contentHash: await sha256Text(text),
    })));
    const result = source.contentBoundary === 'local_original'
      ? await ports.localWiki.replaceSourceProjection(scope, source, chunks, command)
      : await ports.organizationWiki.replaceSourceProjection(scope, source, chunks, command);
    return { ...result, chunkCount: chunks.length };
  }

  async searchWiki(context: AgentMemoryInvocationContext, query: string, limit: number) {
    const ports = this.requirePorts();
    const scope = await this.resolveScope(context);
    const [localHits, organizationHits] = await Promise.all([
      ports.localWiki.search(scope, query, limit),
      ports.organizationWiki.search(scope, query, limit),
    ]);
    return [...localHits, ...organizationHits]
      .sort((left, right) => right.score - left.score)
      .slice(0, limit);
  }

  async invalidateWikiSource(
    context: AgentMemoryInvocationContext,
    sourceAssetId: string,
    contentBoundary: AgentWikiSource['contentBoundary'],
    command: { operationKey: string; reason: string },
  ) {
    const ports = this.requirePorts();
    const scope = await this.resolveScope(context);
    return contentBoundary === 'local_original'
      ? ports.localWiki.invalidateSource(scope, sourceAssetId, command)
      : ports.organizationWiki.invalidateSource(scope, sourceAssetId, command);
  }
}

export function createAgentMemoryService(
  dataPlane: Strict88AgentMemoryDataPlane,
  schemaReadiness: Strict88AgentMemorySchemaReadiness,
): AgentMemoryService {
  return new Strict88AgentMemoryService(dataPlane, schemaReadiness);
}
