CREATE TABLE meta_schema_builds (
  build_id TEXT PRIMARY KEY,
  schema_family TEXT NOT NULL,
  manifest_hash TEXT NOT NULL,
  database_generation_id TEXT NOT NULL UNIQUE,
  database_role TEXT NOT NULL,
  contract_version TEXT NOT NULL,
  migration_set_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'retired')),
  created_at TEXT NOT NULL,
  activated_at TEXT NOT NULL
) STRICT;

CREATE TABLE meta_migration_steps (
  step_id TEXT PRIMARY KEY,
  from_manifest_hash TEXT,
  to_manifest_hash TEXT NOT NULL,
  step_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('started', 'applied', 'failed')),
  applied_at TEXT NOT NULL
) STRICT;

CREATE TABLE runtime_write_fences (
  fence_id TEXT PRIMARY KEY,
  fence_epoch INTEGER NOT NULL,
  writer_id TEXT NOT NULL,
  lease_until TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('active', 'released', 'expired')),
  created_at TEXT NOT NULL,
  UNIQUE (fence_epoch)
) STRICT;

CREATE TABLE runtime_first_writes (
  write_id TEXT PRIMARY KEY,
  build_id TEXT NOT NULL REFERENCES meta_schema_builds(build_id),
  operation_id TEXT NOT NULL UNIQUE,
  fence_id TEXT NOT NULL REFERENCES runtime_write_fences(fence_id),
  state TEXT NOT NULL CHECK (state IN ('prepared', 'committed', 'aborted')),
  committed_at TEXT NOT NULL
) STRICT;

CREATE TABLE device_registry (
  device_id TEXT PRIMARY KEY,
  device_epoch INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'retired')),
  version INTEGER NOT NULL CHECK (version >= 1)
) STRICT;

CREATE TABLE workspace_sandboxes (
  sandbox_id TEXT PRIMARY KEY,
  device_id TEXT NOT NULL REFERENCES device_registry(device_id),
  sandbox_kind TEXT NOT NULL CHECK (sandbox_kind IN ('local_draft', 'organization')),
  replica_epoch INTEGER NOT NULL,
  runtime_status TEXT NOT NULL CHECK (
    runtime_status IN (
      'local_draft',
      'verifying',
      'switching',
      'ready',
      'needs_login',
      'identity_error',
      'sync_degraded',
      'schema_incompatible'
    )
  ),
  display_name TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE UNIQUE INDEX workspace_sandboxes_one_active
ON workspace_sandboxes(is_active)
WHERE is_active = 1;

CREATE TABLE workspace_bindings (
  binding_id TEXT PRIMARY KEY,
  sandbox_id TEXT NOT NULL UNIQUE REFERENCES workspace_sandboxes(sandbox_id),
  cloud_instance_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  cloud_api_url TEXT NOT NULL,
  database_generation_id TEXT NOT NULL,
  contract_version TEXT NOT NULL,
  cloud_manifest_hash TEXT NOT NULL,
  identity_state TEXT NOT NULL CHECK (
    identity_state IN ('unverified', 'verified', 'needs_login', 'identity_error')
  ),
  version INTEGER NOT NULL CHECK (version >= 1),
  verified_at TEXT,
  updated_at TEXT NOT NULL,
  UNIQUE (cloud_instance_id, organization_id)
) STRICT;

CREATE TABLE workspace_session_snapshots (
  session_snapshot_id TEXT PRIMARY KEY,
  sandbox_id TEXT NOT NULL UNIQUE REFERENCES workspace_sandboxes(sandbox_id),
  principal_id TEXT NOT NULL,
  membership_id TEXT NOT NULL,
  secret_ref TEXT NOT NULL,
  credential_fingerprint TEXT NOT NULL,
  session_snapshot_json TEXT NOT NULL,
  verified_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'needs_login', 'revoked')),
  version INTEGER NOT NULL CHECK (version >= 1),
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE projection_principals (
  sandbox_id TEXT NOT NULL REFERENCES workspace_sandboxes(sandbox_id),
  principal_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  source_version INTEGER NOT NULL,
  projection_state TEXT NOT NULL CHECK (projection_state IN ('fresh', 'stale', 'missing')),
  refreshed_at TEXT NOT NULL,
  PRIMARY KEY (sandbox_id, principal_id)
) STRICT;

CREATE TABLE projection_organizations (
  sandbox_id TEXT NOT NULL REFERENCES workspace_sandboxes(sandbox_id),
  organization_id TEXT NOT NULL,
  name TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  source_version INTEGER NOT NULL,
  projection_state TEXT NOT NULL CHECK (projection_state IN ('fresh', 'stale', 'missing')),
  refreshed_at TEXT NOT NULL,
  PRIMARY KEY (sandbox_id, organization_id)
) STRICT;

CREATE TABLE projection_memberships (
  sandbox_id TEXT NOT NULL REFERENCES workspace_sandboxes(sandbox_id),
  membership_id TEXT NOT NULL,
  principal_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  status TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  source_version INTEGER NOT NULL,
  projection_state TEXT NOT NULL CHECK (projection_state IN ('fresh', 'stale', 'missing')),
  refreshed_at TEXT NOT NULL,
  PRIMARY KEY (sandbox_id, membership_id)
) STRICT;

CREATE TABLE projection_departments (
  sandbox_id TEXT NOT NULL REFERENCES workspace_sandboxes(sandbox_id),
  department_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  name TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  source_version INTEGER NOT NULL,
  projection_state TEXT NOT NULL CHECK (projection_state IN ('fresh', 'stale', 'missing')),
  refreshed_at TEXT NOT NULL,
  PRIMARY KEY (sandbox_id, department_id)
) STRICT;

CREATE TABLE sync_cursors (
  cursor_id TEXT PRIMARY KEY,
  sandbox_id TEXT NOT NULL REFERENCES workspace_sandboxes(sandbox_id),
  stream_id TEXT NOT NULL,
  cursor_value TEXT NOT NULL,
  database_generation_id TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version >= 1),
  UNIQUE (sandbox_id, stream_id)
) STRICT;

CREATE TABLE command_envelopes (
  command_id TEXT PRIMARY KEY,
  sandbox_id TEXT NOT NULL REFERENCES workspace_sandboxes(sandbox_id),
  scope_id TEXT NOT NULL,
  cloud_instance_id TEXT,
  organization_id TEXT,
  operation_id TEXT NOT NULL UNIQUE,
  idempotency_key TEXT NOT NULL,
  aggregate_type TEXT NOT NULL,
  aggregate_id TEXT NOT NULL,
  command_type TEXT NOT NULL,
  actor_principal_id TEXT NOT NULL,
  expected_version INTEGER,
  payload_json TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK (
    status IN ('pending', 'sending', 'confirmed', 'failed', 'dead_letter')
  ),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (
    sandbox_id,
    actor_principal_id,
    command_type,
    idempotency_key
  )
) STRICT;

CREATE TABLE command_idempotency (
  record_id TEXT PRIMARY KEY,
  sandbox_id TEXT NOT NULL REFERENCES workspace_sandboxes(sandbox_id),
  actor_principal_id TEXT NOT NULL,
  command_type TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  result_hash TEXT,
  result_json TEXT,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (
    sandbox_id,
    actor_principal_id,
    command_type,
    idempotency_key
  )
) STRICT;

CREATE TABLE operation_attempts (
  attempt_id TEXT PRIMARY KEY,
  sandbox_id TEXT NOT NULL REFERENCES workspace_sandboxes(sandbox_id),
  command_id TEXT NOT NULL REFERENCES command_envelopes(command_id),
  attempt_no INTEGER NOT NULL CHECK (attempt_no >= 1),
  transport_state TEXT NOT NULL,
  lease_owner TEXT,
  lease_until TEXT,
  next_retry_at TEXT,
  error_code TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (command_id, attempt_no)
) STRICT;

CREATE TABLE delivery_outbox (
  event_id TEXT PRIMARY KEY,
  sandbox_id TEXT NOT NULL REFERENCES workspace_sandboxes(sandbox_id),
  operation_id TEXT NOT NULL,
  aggregate_type TEXT NOT NULL,
  aggregate_id TEXT NOT NULL,
  aggregate_version INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending', 'delivering', 'delivered', 'failed')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE delivery_inbox (
  receipt_id TEXT PRIMARY KEY,
  sandbox_id TEXT NOT NULL REFERENCES workspace_sandboxes(sandbox_id),
  operation_id TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  result_status TEXT NOT NULL,
  result_json TEXT,
  processed_at TEXT NOT NULL,
  UNIQUE (sandbox_id, operation_id)
) STRICT;

CREATE TABLE operation_dead_letters (
  dead_letter_id TEXT PRIMARY KEY,
  sandbox_id TEXT NOT NULL REFERENCES workspace_sandboxes(sandbox_id),
  operation_id TEXT NOT NULL,
  aggregate_type TEXT NOT NULL,
  aggregate_id TEXT NOT NULL,
  error_code TEXT NOT NULL,
  error_message TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open', 'resolved', 'discarded')),
  created_at TEXT NOT NULL,
  resolved_at TEXT
) STRICT;

CREATE TABLE audit_events (
  audit_id TEXT PRIMARY KEY,
  sandbox_id TEXT NOT NULL REFERENCES workspace_sandboxes(sandbox_id),
  operation_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  action TEXT NOT NULL,
  resource_type TEXT NOT NULL,
  resource_id TEXT NOT NULL,
  before_version INTEGER,
  after_version INTEGER,
  summary_json TEXT NOT NULL,
  previous_event_hash TEXT,
  event_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE lifecycle_events (
  lifecycle_event_id TEXT PRIMARY KEY,
  sandbox_id TEXT NOT NULL REFERENCES workspace_sandboxes(sandbox_id),
  operation_id TEXT NOT NULL,
  resource_type TEXT NOT NULL,
  resource_id TEXT NOT NULL,
  from_state TEXT,
  to_state TEXT NOT NULL,
  resource_version INTEGER NOT NULL,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE storage_objects (
  object_id TEXT PRIMARY KEY,
  sandbox_id TEXT NOT NULL REFERENCES workspace_sandboxes(sandbox_id),
  storage_key TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  media_type TEXT NOT NULL,
  byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
  lifecycle_state TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (sandbox_id, storage_key)
) STRICT;

CREATE TABLE reconciliation_runs (
  run_id TEXT PRIMARY KEY,
  sandbox_id TEXT NOT NULL REFERENCES workspace_sandboxes(sandbox_id),
  operation_id TEXT NOT NULL,
  stream_id TEXT NOT NULL,
  mismatch_count INTEGER NOT NULL CHECK (mismatch_count >= 0),
  status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
  report_json TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT
) STRICT;

CREATE TABLE projection_business_objects (
  sandbox_id TEXT NOT NULL REFERENCES workspace_sandboxes(sandbox_id),
  object_kind TEXT NOT NULL CHECK (
    object_kind IN (
      'project',
      'task',
      'task_list',
      'task_tag',
      'task_return_notice',
      'event_line',
      'source_asset',
      'knowledge_document',
      'organization_plan',
      'weekly_review',
      'intelligence',
      'growth_signal',
      'growth_evidence',
      'experience_quote',
      'growth_card',
      'narrative_output',
      'ai_answer',
      'favorite'
    )
  ),
  object_id TEXT NOT NULL,
  organization_id TEXT NOT NULL,
  project_id TEXT,
  source_version INTEGER NOT NULL CHECK (source_version >= 1),
  lifecycle_state TEXT NOT NULL,
  payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
  projection_state TEXT NOT NULL
    CHECK (projection_state IN ('active', 'stale', 'removed')),
  refreshed_at TEXT NOT NULL,
  PRIMARY KEY (sandbox_id, object_kind, object_id)
) STRICT;

CREATE INDEX projection_business_by_kind
ON projection_business_objects(
  sandbox_id,
  object_kind,
  projection_state,
  refreshed_at
);

CREATE INDEX projection_business_by_project
ON projection_business_objects(
  sandbox_id,
  project_id,
  object_kind,
  projection_state
);
