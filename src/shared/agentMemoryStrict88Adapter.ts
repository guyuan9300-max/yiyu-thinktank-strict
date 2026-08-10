import {
  assertAgentMemoryScope,
  assertAgentMemoryWorkspaceScope,
  type AgentMemoryAuditPort,
  type AgentMemoryFactAuthorityPort,
  type AgentMemoryHostPorts,
  type AgentMemoryModelPort,
  type AgentMemoryModelRequest,
  type AgentMemoryRebuildableCachePort,
  type AgentMemoryScope,
  type AgentMemoryWorkspaceScope,
  type AgentSkillAuthorityPort,
  type AgentWikiLocalProjectionPort,
  type AgentWikiOrganizationProjectionPort,
} from './agentMemoryPorts';
import builtinAgentRegistry from '../../contracts/agent-memory-builtins.v1.json';

const BUILTIN_AGENT_KINDS = [
  'project_workspace',
  'task_planning',
  'meeting_minutes',
  'strategy_companion',
  'intelligence_research',
  'growth_companion',
] as const;

export type BuiltinAgentKind = (typeof BUILTIN_AGENT_KINDS)[number];

function isBuiltinAgentKind(value: string): value is BuiltinAgentKind {
  return (BUILTIN_AGENT_KINDS as readonly string[]).includes(value);
}

export const BUILTIN_AGENT_DEFINITIONS: ReadonlyArray<{
  agentKind: BuiltinAgentKind;
  label: string;
  handle: string;
  capabilityPolicyVersion: string;
  serviceGoal: string;
  directOutputs: readonly string[];
  commandBoundaries: readonly string[];
  baseMode: string;
}> = builtinAgentRegistry.agents.map((row) => {
  if (!isBuiltinAgentKind(row.agentKind)) {
    throw new Error(`unknown builtin Agent kind: ${row.agentKind}`);
  }
  return {
    agentKind: row.agentKind,
    label: row.label,
    handle: row.handle,
    capabilityPolicyVersion: row.capabilityPolicyVersion,
    serviceGoal: row.serviceGoal,
    directOutputs: row.directOutputs,
    commandBoundaries: row.commandBoundaries,
    baseMode: row.baseMode,
  };
});

if (
  builtinAgentRegistry.status !== 'FROZEN_FOR_IMPLEMENTATION'
  || builtinAgentRegistry.scopeKind !== 'organization'
  || BUILTIN_AGENT_DEFINITIONS.length !== BUILTIN_AGENT_KINDS.length
) {
  throw new Error('builtin Agent registry contract is invalid');
}

export const STRICT_88_AGENT_MEMORY_TABLE_BINDINGS = {
  chatMemory: [
    'source_assets',
    'object_manifests',
    'source_sets',
    'source_set_members',
    'atomic_facts',
    'evidence_links',
    'ai_context_manifests',
    'ai_answers',
    'derivation_lineage',
    'cache_entries',
  ],
  skill: [
    'automation_rules',
    'bot_definitions',
    'execution_runs',
    'ai_proposals',
    'ai_approvals',
    'secured_resources',
    'object_grants',
  ],
  wiki: [
    'knowledge_documents',
    'document_versions',
    'content_chunks',
    'atomic_facts',
    'evidence_links',
    'relationship_triples',
    'search_index_manifests',
    'vector_index_manifests',
    'derivation_lineage',
  ],
  reliability: [
    'commands',
    'idempotency_records',
    'lifecycle_events',
    'purge_ledger',
    'audit_events',
  ],
  organizationModel: ['provider_resources'],
} as const;

export const STRICT_88_AGENT_MEMORY_REQUIRED_FIELDS: Readonly<Record<string, readonly string[]>> = {
  source_assets: ['id', 'scope_id', 'client_id', 'object_manifest_id', 'content_hash', 'availability_state', 'version', 'lifecycle_state'],
  object_manifests: ['id', 'scope_id', 'storage_key', 'content_hash', 'storage_kind', 'availability_state', 'lifecycle_state'],
  source_sets: ['id', 'scope_id', 'client_id', 'purpose_kind', 'publication_state', 'version', 'lifecycle_state'],
  source_set_members: ['id', 'scope_id', 'source_set_id', 'source_object_id', 'source_object_kind', 'source_version'],
  atomic_facts: ['id', 'scope_id', 'source_set_id', 'fact_hash', 'fact_object_manifest_id', 'verification_state', 'version', 'lifecycle_state'],
  evidence_links: ['id', 'scope_id', 'fact_id', 'source_object_id', 'source_object_kind', 'source_version', 'locator_hash'],
  ai_context_manifests: ['id', 'scope_id', 'source_set_id', 'question_hash', 'selected_source_count', 'context_object_manifest_id', 'status'],
  ai_answers: ['id', 'scope_id', 'client_id', 'bot_id', 'source_set_id', 'ai_context_manifest_id', 'provider_resource_id', 'model_name', 'answer_hash', 'status'],
  automation_rules: ['id', 'scope_id', 'record_kind', 'rule_version', 'trigger_spec', 'action_spec', 'enabled', 'version', 'lifecycle_state'],
  bot_definitions: ['id', 'scope_id', 'agent_kind', 'handle', 'enabled', 'version', 'lifecycle_state'],
  execution_runs: ['id', 'scope_id', 'bot_id', 'operation_id', 'status', 'run_kind', 'version', 'lifecycle_state'],
  ai_proposals: ['id', 'scope_id', 'answer_id', 'operation_kind', 'payload_hash', 'status', 'risk_level', 'version', 'lifecycle_state'],
  ai_approvals: ['id', 'scope_id', 'proposal_id', 'approver_principal_id', 'decision', 'decided_at', 'version', 'lifecycle_state'],
  secured_resources: ['id', 'scope_id', 'resource_kind', 'version', 'lifecycle_state'],
  object_grants: ['id', 'scope_id', 'secured_resource_id', 'policy_version_id', 'capability_set', 'status', 'version', 'lifecycle_state'],
  knowledge_documents: ['id', 'scope_id', 'client_id', 'current_version', 'document_kind', 'publication_state', 'version', 'lifecycle_state'],
  document_versions: ['id', 'scope_id', 'document_id', 'version', 'content_hash', 'object_manifest_id', 'publication_state'],
  content_chunks: ['id', 'scope_id', 'document_version_id', 'ordinal', 'chunk_hash', 'object_manifest_id', 'version', 'lifecycle_state'],
  relationship_triples: ['id', 'scope_id', 'subject_fact_id', 'object_fact_id', 'predicate', 'verification_state', 'version', 'lifecycle_state'],
  search_index_manifests: ['id', 'scope_id', 'lineage_id', 'index_version', 'index_kind', 'status', 'invalidated_at'],
  vector_index_manifests: ['id', 'scope_id', 'lineage_id', 'provider_resource_id', 'policy_version', 'embedding_model', 'status', 'invalidated_at'],
  cache_entries: ['id', 'scope_id', 'lineage_id', 'cache_kind', 'object_manifest_id', 'expires_at', 'invalidated_at'],
  derivation_lineage: ['id', 'scope_id', 'source_set_id', 'derivative_kind', 'derivative_object_id', 'invalidated_at'],
  commands: ['id', 'scope_id', 'operation_id', 'idempotency_key', 'aggregate_type', 'aggregate_id', 'command_type', 'status'],
  idempotency_records: ['id', 'scope_id', 'idempotency_key', 'payload_hash', 'result_hash', 'status'],
  lifecycle_events: ['id', 'scope_id', 'operation_id', 'secured_resource_id', 'from_state', 'to_state', 'tombstone_version'],
  purge_ledger: ['id', 'scope_id', 'operation_id', 'secured_resource_id', 'purge_generation', 'status', 'version', 'lifecycle_state'],
  provider_resources: ['id', 'scope_id', 'provider', 'resource_kind', 'owner_kind', 'model_name', 'status', 'version', 'lifecycle_state'],
  audit_events: ['id', 'scope_id', 'operation_id', 'actor_id', 'action', 'target_resource_id', 'event_hash'],
};

export interface Strict88SchemaTableSnapshot {
  name: string;
  fields: readonly string[];
}

export interface Strict88AgentMemorySchemaReadiness {
  ready: boolean;
  missingTables: string[];
  missingFields: Array<{ table: string; field: string }>;
}

export function inspectStrict88AgentMemorySchema(
  tables: readonly Strict88SchemaTableSnapshot[],
): Strict88AgentMemorySchemaReadiness {
  const inventory = new Map(tables.map((table) => [table.name, new Set(table.fields)]));
  const missingTables: string[] = [];
  const missingFields: Array<{ table: string; field: string }> = [];
  for (const [table, requiredFields] of Object.entries(STRICT_88_AGENT_MEMORY_REQUIRED_FIELDS)) {
    const fields = inventory.get(table);
    if (!fields) {
      missingTables.push(table);
      continue;
    }
    for (const field of requiredFields) {
      if (!fields.has(field)) missingFields.push({ table, field });
    }
  }
  return {
    ready: missingTables.length === 0 && missingFields.length === 0,
    missingTables,
    missingFields,
  };
}

export class AgentMemoryAdapterBlockedError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = 'AgentMemoryAdapterBlockedError';
  }
}

export interface Strict88BuiltinAgentRecord {
  botId: string;
  agentKind: BuiltinAgentKind;
  handle: string;
  enabled: boolean;
  version: number;
}

export interface Strict88OrganizationModelResource {
  resourceId: string;
  provider: string;
  resourceKind: 'organization_ai_configuration';
  ownerKind: 'organization';
  modelName: string;
  status: 'ready' | 'blocked' | 'failed_retryable' | 'failed';
  version: number;
}

export interface Strict88AgentMemoryDataPlane {
  facts: AgentMemoryFactAuthorityPort;
  skills: AgentSkillAuthorityPort;
  localWiki: AgentWikiLocalProjectionPort;
  organizationWiki: AgentWikiOrganizationProjectionPort;
  cache: AgentMemoryRebuildableCachePort;
  audit: AgentMemoryAuditPort;
  resolveBuiltinAgent(
    scope: AgentMemoryWorkspaceScope,
    agentKind: BuiltinAgentKind,
  ): Promise<Strict88BuiltinAgentRecord | null>;
  resolveOrganizationModel(scope: AgentMemoryScope): Promise<Strict88OrganizationModelResource | null>;
  invokeOrganizationModel(
    scope: AgentMemoryScope,
    resourceId: string,
    request: AgentMemoryModelRequest,
  ): Promise<{ text: string }>;
}

class Strict88OrganizationModelAdapter implements AgentMemoryModelPort {
  constructor(private readonly dataPlane: Strict88AgentMemoryDataPlane) {}

  async generate(scope: AgentMemoryScope, request: AgentMemoryModelRequest): Promise<{ text: string }> {
    assertAgentMemoryScope(scope);
    const resource = await this.dataPlane.resolveOrganizationModel(scope);
    if (!resource) {
      throw new AgentMemoryAdapterBlockedError(
        'organization_model_not_connected',
        '组织默认模型尚未配置',
      );
    }
    if (resource.resourceKind !== 'organization_ai_configuration' || resource.ownerKind !== 'organization') {
      throw new AgentMemoryAdapterBlockedError(
        'organization_model_scope_mismatch',
        '组织模型资源作用域不匹配',
      );
    }
    if (resource.status !== 'ready') {
      throw new AgentMemoryAdapterBlockedError(
        `organization_model_${resource.status}`,
        '组织默认模型当前不可用',
      );
    }
    return this.dataPlane.invokeOrganizationModel(scope, resource.resourceId, request);
  }
}

export async function resolveStrict88BuiltinAgent(
  dataPlane: Strict88AgentMemoryDataPlane,
  scope: AgentMemoryWorkspaceScope,
  agentKind: BuiltinAgentKind,
): Promise<Strict88BuiltinAgentRecord> {
  assertAgentMemoryWorkspaceScope(scope);
  const definition = BUILTIN_AGENT_DEFINITIONS.find((item) => item.agentKind === agentKind);
  if (!definition) {
    throw new AgentMemoryAdapterBlockedError('builtin_agent_unknown', '未知的内置功能 Agent');
  }
  const record = await dataPlane.resolveBuiltinAgent(scope, agentKind);
  if (!record || !record.enabled) {
    throw new AgentMemoryAdapterBlockedError('builtin_agent_not_connected', `${definition.label}尚未登记`);
  }
  if (record.agentKind !== agentKind || record.handle !== definition.handle) {
    throw new AgentMemoryAdapterBlockedError('builtin_agent_identity_mismatch', `${definition.label}身份不匹配`);
  }
  return record;
}

export function createStrict88AgentMemoryHostPorts(
  dataPlane: Strict88AgentMemoryDataPlane,
  schemaReadiness: Strict88AgentMemorySchemaReadiness,
): AgentMemoryHostPorts {
  if (!schemaReadiness.ready) {
    throw new AgentMemoryAdapterBlockedError(
      'agent_memory_schema_not_ready',
      'Agent Memory 所需的严格88表字段尚未补齐',
    );
  }
  return {
    facts: dataPlane.facts,
    skills: dataPlane.skills,
    localWiki: dataPlane.localWiki,
    organizationWiki: dataPlane.organizationWiki,
    model: new Strict88OrganizationModelAdapter(dataPlane),
    cache: dataPlane.cache,
    audit: dataPlane.audit,
  };
}
