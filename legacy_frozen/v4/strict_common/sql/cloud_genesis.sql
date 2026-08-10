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
  fence_epoch INTEGER NOT NULL UNIQUE,
  writer_id TEXT NOT NULL,
  lease_until TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('active', 'released', 'expired')),
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE runtime_first_writes (
  write_id TEXT PRIMARY KEY,
  build_id TEXT NOT NULL REFERENCES meta_schema_builds(build_id),
  operation_id TEXT NOT NULL UNIQUE,
  fence_id TEXT NOT NULL REFERENCES runtime_write_fences(fence_id),
  state TEXT NOT NULL CHECK (state IN ('prepared', 'committed', 'aborted')),
  committed_at TEXT NOT NULL
) STRICT;

CREATE TABLE identity_cloud_instances (
  cloud_instance_id TEXT PRIMARY KEY,
  database_generation_id TEXT NOT NULL UNIQUE,
  schema_family TEXT NOT NULL,
  contract_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'maintenance', 'retired')),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE identity_principals (
  principal_id TEXT PRIMARY KEY,
  principal_kind TEXT NOT NULL DEFAULT 'human'
    CHECK (principal_kind IN ('human', 'bot')),
  display_name TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'disabled', 'deleted')),
  identity_version INTEGER NOT NULL CHECK (identity_version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE identity_contacts (
  contact_id TEXT PRIMARY KEY,
  principal_id TEXT NOT NULL REFERENCES identity_principals(principal_id),
  contact_type TEXT NOT NULL CHECK (contact_type IN ('email', 'phone')),
  normalized_value TEXT NOT NULL,
  verification_state TEXT NOT NULL CHECK (
    verification_state IN ('verified', 'unverified', 'revoked')
  ),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (contact_type, normalized_value)
) STRICT;

CREATE TABLE identity_credentials (
  credential_id TEXT PRIMARY KEY,
  principal_id TEXT NOT NULL REFERENCES identity_principals(principal_id),
  credential_type TEXT NOT NULL CHECK (credential_type = 'password'),
  secret_hash TEXT NOT NULL,
  hash_scheme TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (principal_id, credential_type)
) STRICT;

CREATE TABLE organization_records (
  organization_id TEXT PRIMARY KEY,
  cloud_instance_id TEXT NOT NULL REFERENCES identity_cloud_instances(cloud_instance_id),
  name TEXT NOT NULL,
  annual_goal TEXT NOT NULL DEFAULT '',
  annual_strategy_year TEXT NOT NULL DEFAULT '',
  annual_strategy TEXT NOT NULL DEFAULT '',
  quarterly_focus_json TEXT NOT NULL DEFAULT '[]' CHECK (
    json_valid(quarterly_focus_json) AND json_type(quarterly_focus_json) = 'array'
  ),
  leader_membership_id TEXT REFERENCES organization_memberships(membership_id),
  leader_name_override TEXT NOT NULL DEFAULT '',
  lifecycle_state TEXT NOT NULL CHECK (
    lifecycle_state IN ('active', 'paused', 'archived')
  ),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE authorization_scopes (
  scope_id TEXT PRIMARY KEY,
  scope_kind TEXT NOT NULL CHECK (scope_kind IN ('personal', 'organization')),
  principal_id TEXT REFERENCES identity_principals(principal_id),
  organization_id TEXT REFERENCES organization_records(organization_id),
  policy_version INTEGER NOT NULL CHECK (policy_version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (
    (scope_kind = 'personal' AND principal_id IS NOT NULL AND organization_id IS NULL)
    OR
    (scope_kind = 'organization' AND organization_id IS NOT NULL AND principal_id IS NULL)
  )
) STRICT;

CREATE TABLE organization_memberships (
  membership_id TEXT PRIMARY KEY,
  scope_id TEXT NOT NULL REFERENCES authorization_scopes(scope_id),
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  principal_id TEXT NOT NULL REFERENCES identity_principals(principal_id),
  system_role TEXT NOT NULL CHECK (system_role IN ('admin', 'member')),
  visibility_scope TEXT NOT NULL CHECK (
    visibility_scope IN ('organization', 'department', 'self')
  ),
  project_role_labels_json TEXT NOT NULL DEFAULT '[]'
    CHECK (
      json_valid(project_role_labels_json)
      AND json_type(project_role_labels_json) = 'array'
    ),
  current_focus TEXT NOT NULL DEFAULT '',
  task_edit_scope TEXT NOT NULL DEFAULT 'self' CHECK (
    task_edit_scope IN ('self', 'manager', 'department', 'organization')
  ),
  can_approve_tasks INTEGER NOT NULL DEFAULT 0 CHECK (can_approve_tasks IN (0, 1)),
  can_reassign_tasks INTEGER NOT NULL DEFAULT 0 CHECK (can_reassign_tasks IN (0, 1)),
  can_change_deadline INTEGER NOT NULL DEFAULT 0 CHECK (can_change_deadline IN (0, 1)),
  status TEXT NOT NULL CHECK (status IN ('active', 'disabled', 'left')),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (organization_id, principal_id)
) STRICT;

CREATE TABLE organization_departments (
  department_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  name TEXT NOT NULL,
  color TEXT NOT NULL DEFAULT '#5B7CFA',
  parent_department_id TEXT REFERENCES organization_departments(department_id),
  leader_name_override TEXT NOT NULL DEFAULT '',
  mission TEXT NOT NULL DEFAULT '',
  business_context TEXT NOT NULL DEFAULT '',
  team_context TEXT NOT NULL DEFAULT '',
  quarterly_focus_json TEXT NOT NULL DEFAULT '[]' CHECK (
    json_valid(quarterly_focus_json) AND json_type(quarterly_focus_json) = 'array'
  ),
  collaboration_department_ids_json TEXT NOT NULL DEFAULT '[]'
    CHECK (
      json_valid(collaboration_department_ids_json)
      AND json_type(collaboration_department_ids_json) = 'array'
    ),
  lifecycle_state TEXT NOT NULL CHECK (
    lifecycle_state IN ('active', 'archived')
  ),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (organization_id, name)
) STRICT;

CREATE TABLE department_memberships (
  department_membership_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  department_id TEXT NOT NULL REFERENCES organization_departments(department_id),
  membership_id TEXT NOT NULL REFERENCES organization_memberships(membership_id),
  is_department_lead INTEGER NOT NULL DEFAULT 0 CHECK (is_department_lead IN (0, 1)),
  status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (department_id, membership_id)
) STRICT;

CREATE TABLE management_titles (
  title_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  name TEXT NOT NULL,
  department_id TEXT REFERENCES organization_departments(department_id),
  level TEXT NOT NULL DEFAULT 'employee' CHECK (
    level IN ('employee', 'supervisor', 'department_lead', 'organization_lead')
  ),
  visibility_scope TEXT NOT NULL DEFAULT 'self' CHECK (
    visibility_scope IN ('organization', 'department', 'self')
  ),
  manager_title_id TEXT REFERENCES management_titles(title_id),
  is_manager INTEGER NOT NULL DEFAULT 0 CHECK (is_manager IN (0, 1)),
  goal TEXT NOT NULL DEFAULT '',
  responsibilities_json TEXT NOT NULL DEFAULT '[]'
    CHECK (
      json_valid(responsibilities_json)
      AND json_type(responsibilities_json) = 'array'
    ),
  should_avoid_json TEXT NOT NULL DEFAULT '[]' CHECK (
    json_valid(should_avoid_json) AND json_type(should_avoid_json) = 'array'
  ),
  collaboration_title_ids_json TEXT NOT NULL DEFAULT '[]'
    CHECK (
      json_valid(collaboration_title_ids_json)
      AND json_type(collaboration_title_ids_json) = 'array'
    ),
  task_edit_scope TEXT NOT NULL DEFAULT 'self' CHECK (
    task_edit_scope IN ('self', 'manager', 'department', 'organization')
  ),
  can_approve_tasks INTEGER NOT NULL DEFAULT 0 CHECK (can_approve_tasks IN (0, 1)),
  can_reassign_tasks INTEGER NOT NULL DEFAULT 0 CHECK (can_reassign_tasks IN (0, 1)),
  can_change_deadline INTEGER NOT NULL DEFAULT 0 CHECK (can_change_deadline IN (0, 1)),
  sort_order INTEGER NOT NULL DEFAULT 0,
  lifecycle_state TEXT NOT NULL CHECK (
    lifecycle_state IN ('active', 'archived')
  ),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (organization_id, name)
) STRICT;

CREATE TABLE management_title_memberships (
  assignment_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  title_id TEXT NOT NULL REFERENCES management_titles(title_id),
  membership_id TEXT NOT NULL REFERENCES organization_memberships(membership_id),
  status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (title_id, membership_id)
) STRICT;

CREATE TABLE organization_reporting_lines (
  reporting_line_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  manager_membership_id TEXT NOT NULL REFERENCES organization_memberships(membership_id),
  report_membership_id TEXT NOT NULL REFERENCES organization_memberships(membership_id),
  line_type TEXT NOT NULL CHECK (line_type IN ('business', 'administrative')),
  approves_tasks INTEGER NOT NULL DEFAULT 0 CHECK (approves_tasks IN (0, 1)),
  can_adjust_tasks INTEGER NOT NULL DEFAULT 0 CHECK (can_adjust_tasks IN (0, 1)),
  can_change_deadline INTEGER NOT NULL DEFAULT 0 CHECK (can_change_deadline IN (0, 1)),
  can_reassign_tasks INTEGER NOT NULL DEFAULT 0 CHECK (can_reassign_tasks IN (0, 1)),
  is_cross_department_approver INTEGER NOT NULL DEFAULT 0
    CHECK (is_cross_department_approver IN (0, 1)),
  lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('active', 'archived')),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (manager_membership_id != report_membership_id),
  UNIQUE (organization_id, manager_membership_id, report_membership_id, line_type)
) STRICT;

CREATE INDEX organization_reporting_lines_by_report
ON organization_reporting_lines(organization_id, report_membership_id, lifecycle_state);

CREATE UNIQUE INDEX organization_reporting_lines_one_active_manager
ON organization_reporting_lines(organization_id, report_membership_id, line_type)
WHERE lifecycle_state = 'active';

CREATE TABLE organization_task_control_rules (
  task_control_rule_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  name TEXT NOT NULL,
  control_level TEXT NOT NULL CHECK (
    control_level IN ('normal', 'leader_control', 'department_control', 'organization_control')
  ),
  department_id TEXT REFERENCES organization_departments(department_id),
  title_id TEXT REFERENCES management_titles(title_id),
  content_editable_by TEXT NOT NULL CHECK (
    content_editable_by IN ('assignee', 'manager', 'department_lead', 'organization_lead', 'creator')
  ),
  deadline_editable_by TEXT NOT NULL CHECK (
    deadline_editable_by IN ('assignee', 'manager', 'department_lead', 'organization_lead', 'creator')
  ),
  owner_editable_by TEXT NOT NULL CHECK (
    owner_editable_by IN ('assignee', 'manager', 'department_lead', 'organization_lead', 'creator')
  ),
  cancellable_by TEXT NOT NULL CHECK (
    cancellable_by IN ('assignee', 'manager', 'department_lead', 'organization_lead', 'creator')
  ),
  require_collab_confirmation INTEGER NOT NULL DEFAULT 0
    CHECK (require_collab_confirmation IN (0, 1)),
  default_approver_membership_id TEXT
    REFERENCES organization_memberships(membership_id),
  lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('active', 'archived')),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE INDEX organization_task_control_rules_by_scope
ON organization_task_control_rules(
  organization_id,
  department_id,
  title_id,
  lifecycle_state
);

CREATE TABLE organization_role_process_templates (
  role_process_template_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  title_id TEXT REFERENCES management_titles(title_id),
  name TEXT NOT NULL,
  trigger_type TEXT NOT NULL CHECK (
    trigger_type IN ('weekly_followup', 'task_created', 'meeting_closed', 'client_update', 'manual')
  ),
  trigger_condition TEXT NOT NULL DEFAULT '',
  key_steps_json TEXT NOT NULL DEFAULT '[]' CHECK (
    json_valid(key_steps_json) AND json_type(key_steps_json) = 'array'
  ),
  collaboration_step TEXT NOT NULL DEFAULT '',
  approval_step TEXT NOT NULL DEFAULT '',
  output_artifact TEXT NOT NULL DEFAULT '',
  common_blockers_json TEXT NOT NULL DEFAULT '[]'
    CHECK (
      json_valid(common_blockers_json)
      AND json_type(common_blockers_json) = 'array'
    ),
  lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('active', 'archived')),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE INDEX organization_role_process_templates_by_title
ON organization_role_process_templates(organization_id, title_id, lifecycle_state);

CREATE TABLE organization_invites (
  invite_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  invite_kind TEXT NOT NULL CHECK (
    invite_kind IN ('organization', 'department', 'management_title')
  ),
  target_id TEXT,
  code_hash TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK (status IN ('active', 'revoked', 'expired')),
  expires_at TEXT,
  version INTEGER NOT NULL CHECK (version >= 1),
  created_by_membership_id TEXT NOT NULL REFERENCES organization_memberships(membership_id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE organization_membership_applications (
  membership_application_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  membership_id TEXT NOT NULL REFERENCES organization_memberships(membership_id),
  invite_id TEXT REFERENCES organization_invites(invite_id),
  requested_department_id TEXT REFERENCES organization_departments(department_id),
  requested_management_title_id TEXT REFERENCES management_titles(title_id),
  requested_job_title TEXT NOT NULL DEFAULT '',
  requested_manager_name TEXT NOT NULL DEFAULT '',
  requested_current_focus TEXT NOT NULL DEFAULT '',
  application_state TEXT NOT NULL CHECK (
    application_state IN ('pending', 'approved', 'rejected', 'withdrawn')
  ),
  rejection_reason TEXT NOT NULL DEFAULT '',
  reviewed_by_membership_id TEXT REFERENCES organization_memberships(membership_id),
  submitted_at TEXT NOT NULL,
  reviewed_at TEXT,
  version INTEGER NOT NULL CHECK (version >= 1),
  updated_at TEXT NOT NULL
) STRICT;

CREATE UNIQUE INDEX organization_membership_applications_one_pending
ON organization_membership_applications(organization_id, membership_id)
WHERE application_state = 'pending';

CREATE INDEX organization_membership_applications_by_state
ON organization_membership_applications(
  organization_id, application_state, submitted_at
);

CREATE TABLE authentication_sessions (
  session_id TEXT PRIMARY KEY,
  principal_id TEXT NOT NULL REFERENCES identity_principals(principal_id),
  cloud_instance_id TEXT NOT NULL REFERENCES identity_cloud_instances(cloud_instance_id),
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  membership_id TEXT NOT NULL REFERENCES organization_memberships(membership_id),
  database_generation_id TEXT NOT NULL,
  access_secret_hash TEXT NOT NULL UNIQUE,
  refresh_secret_hash TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL CHECK (status IN ('active', 'revoked', 'expired')),
  issued_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  refresh_expires_at TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version >= 1),
  last_seen_at TEXT NOT NULL
) STRICT;

CREATE TABLE authorization_resources (
  resource_id TEXT PRIMARY KEY,
  scope_id TEXT NOT NULL REFERENCES authorization_scopes(scope_id),
  resource_kind TEXT NOT NULL,
  lifecycle_state TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (scope_id, resource_kind, resource_id)
) STRICT;

CREATE TABLE authorization_policy_versions (
  policy_version_id TEXT PRIMARY KEY,
  scope_id TEXT NOT NULL REFERENCES authorization_scopes(scope_id),
  resource_id TEXT NOT NULL REFERENCES authorization_resources(resource_id),
  policy_scope_kind TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version >= 1),
  policy_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (resource_id, version)
) STRICT;

CREATE TABLE authorization_grants (
  grant_id TEXT PRIMARY KEY,
  scope_id TEXT NOT NULL REFERENCES authorization_scopes(scope_id),
  resource_id TEXT NOT NULL REFERENCES authorization_resources(resource_id),
  policy_version_id TEXT NOT NULL REFERENCES authorization_policy_versions(policy_version_id),
  subject_principal_id TEXT REFERENCES identity_principals(principal_id),
  subject_membership_id TEXT REFERENCES organization_memberships(membership_id),
  capability_set TEXT NOT NULL,
  grant_generation INTEGER NOT NULL CHECK (grant_generation >= 1),
  status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (
    subject_principal_id IS NOT NULL OR subject_membership_id IS NOT NULL
  )
) STRICT;

CREATE TABLE organization_ai_configs (
  config_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL UNIQUE REFERENCES organization_records(organization_id),
  provider TEXT NOT NULL,
  base_url TEXT NOT NULL,
  model_name TEXT NOT NULL,
  encrypted_api_key TEXT NOT NULL,
  key_fingerprint TEXT NOT NULL,
  config_version INTEGER NOT NULL CHECK (config_version >= 1),
  status TEXT NOT NULL CHECK (status IN ('ready', 'disabled')),
  updated_by_membership_id TEXT NOT NULL REFERENCES organization_memberships(membership_id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE scoped_configuration_records (
  configuration_id TEXT PRIMARY KEY,
  scope_id TEXT NOT NULL REFERENCES authorization_scopes(scope_id),
  scope_kind TEXT NOT NULL CHECK (scope_kind IN ('organization', 'personal')),
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  principal_id TEXT REFERENCES identity_principals(principal_id),
  membership_id TEXT REFERENCES organization_memberships(membership_id),
  configuration_kind TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '',
  public_config_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(public_config_json)),
  encrypted_secret_bundle TEXT,
  secret_fingerprint TEXT,
  secret_envelope_version INTEGER NOT NULL DEFAULT 1
    CHECK (secret_envelope_version >= 1),
  lifecycle_state TEXT NOT NULL
    CHECK (lifecycle_state IN ('active', 'disabled', 'archived')),
  version INTEGER NOT NULL CHECK (version >= 1),
  updated_by_membership_id TEXT NOT NULL
    REFERENCES organization_memberships(membership_id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (
    (
      scope_kind = 'organization'
      AND principal_id IS NULL
      AND membership_id IS NULL
    )
    OR
    (
      scope_kind = 'personal'
      AND principal_id IS NOT NULL
      AND membership_id IS NOT NULL
    )
  ),
  CHECK (
    (
      encrypted_secret_bundle IS NULL
      AND secret_fingerprint IS NULL
    )
    OR
    (
      encrypted_secret_bundle IS NOT NULL
      AND secret_fingerprint IS NOT NULL
    )
  )
) STRICT;

CREATE UNIQUE INDEX scoped_configuration_organization_unique
ON scoped_configuration_records(organization_id, configuration_kind)
WHERE scope_kind = 'organization';

CREATE UNIQUE INDEX scoped_configuration_personal_unique
ON scoped_configuration_records(
  organization_id,
  membership_id,
  configuration_kind
)
WHERE scope_kind = 'personal';

CREATE TABLE organization_bot_profiles (
  bot_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  principal_id TEXT NOT NULL UNIQUE REFERENCES identity_principals(principal_id),
  membership_id TEXT NOT NULL UNIQUE REFERENCES organization_memberships(membership_id),
  handle TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  department_id TEXT REFERENCES organization_departments(department_id),
  reporting_policy_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(reporting_policy_json)),
  capability_policy_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(capability_policy_json)),
  token_hash TEXT NOT NULL,
  token_prefix TEXT NOT NULL,
  token_rotated_at TEXT NOT NULL,
  lifecycle_state TEXT NOT NULL
    CHECK (lifecycle_state IN ('active', 'disabled', 'archived')),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_by_membership_id TEXT NOT NULL
    REFERENCES organization_memberships(membership_id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (organization_id, handle)
) STRICT;

CREATE TABLE bot_task_plans (
  plan_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  bot_id TEXT NOT NULL REFERENCES organization_bot_profiles(bot_id),
  initiator_membership_id TEXT NOT NULL
    REFERENCES organization_memberships(membership_id),
  project_id TEXT REFERENCES work_projects(project_id),
  event_line_id TEXT REFERENCES event_line_records(event_line_id),
  task_id TEXT REFERENCES task_records(task_id),
  plan_json TEXT NOT NULL CHECK (json_valid(plan_json)),
  approval_state TEXT NOT NULL
    CHECK (approval_state IN ('draft', 'pending', 'approved', 'rejected')),
  execution_state TEXT NOT NULL CHECK (
    execution_state IN (
      'not_started',
      'queued',
      'running',
      'completed',
      'failed',
      'cancelled'
    )
  ),
  progress_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(progress_json)),
  approved_by_membership_id TEXT REFERENCES organization_memberships(membership_id),
  lifecycle_state TEXT NOT NULL
    CHECK (lifecycle_state IN ('active', 'archived')),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE command_envelopes (
  command_id TEXT PRIMARY KEY,
  scope_id TEXT NOT NULL REFERENCES authorization_scopes(scope_id),
  organization_id TEXT REFERENCES organization_records(organization_id),
  operation_id TEXT NOT NULL UNIQUE,
  idempotency_key TEXT NOT NULL,
  aggregate_type TEXT NOT NULL,
  aggregate_id TEXT NOT NULL,
  command_type TEXT NOT NULL,
  actor_principal_id TEXT NOT NULL REFERENCES identity_principals(principal_id),
  expected_version INTEGER,
  payload_json TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK (
    status IN ('accepted', 'committed', 'rejected', 'failed')
  ),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (
    scope_id,
    actor_principal_id,
    command_type,
    idempotency_key
  )
) STRICT;

CREATE TABLE bulk_operations (
  bulk_operation_id TEXT PRIMARY KEY,
  scope_id TEXT NOT NULL REFERENCES authorization_scopes(scope_id),
  organization_id TEXT REFERENCES organization_records(organization_id),
  operation_id TEXT NOT NULL UNIQUE,
  preflight_snapshot_hash TEXT NOT NULL,
  atomicity_mode TEXT NOT NULL CHECK (atomicity_mode IN ('all_or_nothing', 'per_item')),
  status TEXT NOT NULL CHECK (
    status IN ('preflight', 'accepted', 'committed', 'partial', 'rejected')
  ),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE bulk_operation_items (
  bulk_item_id TEXT PRIMARY KEY,
  bulk_operation_id TEXT NOT NULL REFERENCES bulk_operations(bulk_operation_id),
  item_key TEXT NOT NULL,
  preflight_result TEXT NOT NULL,
  commit_result TEXT,
  conflict_code TEXT,
  result_json TEXT,
  UNIQUE (bulk_operation_id, item_key)
) STRICT;

CREATE TABLE command_idempotency (
  record_id TEXT PRIMARY KEY,
  scope_id TEXT NOT NULL REFERENCES authorization_scopes(scope_id),
  actor_principal_id TEXT NOT NULL REFERENCES identity_principals(principal_id),
  command_type TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  result_hash TEXT,
  result_json TEXT,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (scope_id, actor_principal_id, command_type, idempotency_key)
) STRICT;

CREATE TABLE operation_attempts (
  attempt_id TEXT PRIMARY KEY,
  scope_id TEXT NOT NULL REFERENCES authorization_scopes(scope_id),
  command_id TEXT NOT NULL REFERENCES command_envelopes(command_id),
  attempt_no INTEGER NOT NULL CHECK (attempt_no >= 1),
  transport_state TEXT NOT NULL,
  lease_owner TEXT,
  lease_until TEXT,
  permission_revalidated_at TEXT,
  next_retry_at TEXT,
  error_code TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (command_id, attempt_no)
) STRICT;

CREATE TABLE delivery_outbox (
  event_id TEXT PRIMARY KEY,
  scope_id TEXT NOT NULL REFERENCES authorization_scopes(scope_id),
  organization_id TEXT REFERENCES organization_records(organization_id),
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
  scope_id TEXT NOT NULL REFERENCES authorization_scopes(scope_id),
  organization_id TEXT REFERENCES organization_records(organization_id),
  operation_id TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  result_status TEXT NOT NULL,
  result_json TEXT,
  processed_at TEXT NOT NULL,
  UNIQUE (scope_id, operation_id)
) STRICT;

CREATE TABLE operation_dead_letters (
  dead_letter_id TEXT PRIMARY KEY,
  scope_id TEXT NOT NULL REFERENCES authorization_scopes(scope_id),
  organization_id TEXT REFERENCES organization_records(organization_id),
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
  scope_id TEXT NOT NULL REFERENCES authorization_scopes(scope_id),
  organization_id TEXT REFERENCES organization_records(organization_id),
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
  scope_id TEXT NOT NULL REFERENCES authorization_scopes(scope_id),
  organization_id TEXT REFERENCES organization_records(organization_id),
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
  scope_id TEXT NOT NULL REFERENCES authorization_scopes(scope_id),
  organization_id TEXT REFERENCES organization_records(organization_id),
  storage_key TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  media_type TEXT NOT NULL,
  byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
  lifecycle_state TEXT NOT NULL,
  storage_receipt TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (scope_id, storage_key)
) STRICT;

CREATE TABLE external_provider_resources (
  provider_resource_id TEXT PRIMARY KEY,
  scope_id TEXT NOT NULL REFERENCES authorization_scopes(scope_id),
  organization_id TEXT REFERENCES organization_records(organization_id),
  provider TEXT NOT NULL,
  resource_kind TEXT NOT NULL,
  remote_id TEXT NOT NULL,
  retention_state TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (scope_id, provider, resource_kind, remote_id)
) STRICT;

CREATE TABLE external_side_effects (
  effect_id TEXT PRIMARY KEY,
  scope_id TEXT NOT NULL REFERENCES authorization_scopes(scope_id),
  organization_id TEXT REFERENCES organization_records(organization_id),
  operation_id TEXT NOT NULL,
  provider_resource_id TEXT NOT NULL REFERENCES external_provider_resources(provider_resource_id),
  effect_kind TEXT NOT NULL,
  outcome TEXT NOT NULL,
  receipt_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE reconciliation_runs (
  run_id TEXT PRIMARY KEY,
  scope_id TEXT NOT NULL REFERENCES authorization_scopes(scope_id),
  organization_id TEXT REFERENCES organization_records(organization_id),
  operation_id TEXT NOT NULL,
  registry_state_id TEXT NOT NULL,
  mismatch_count INTEGER NOT NULL CHECK (mismatch_count >= 0),
  status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
  report_json TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT
) STRICT;

CREATE TABLE release_gates (
  gate_id TEXT PRIMARY KEY,
  candidate_version TEXT NOT NULL,
  recovery_set_id TEXT NOT NULL,
  evidence_version TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('go', 'no_go')),
  owner TEXT NOT NULL,
  decided_at TEXT NOT NULL
) STRICT;

CREATE TABLE recovery_sets (
  recovery_set_id TEXT PRIMARY KEY,
  candidate_version TEXT NOT NULL,
  schema_build_id TEXT NOT NULL REFERENCES meta_schema_builds(build_id),
  database_generation_id TEXT NOT NULL,
  schema_manifest_hash TEXT NOT NULL,
  component_manifest_hash TEXT NOT NULL,
  database_hash TEXT NOT NULL,
  object_manifest_hash TEXT NOT NULL,
  deployment_manifest_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('created', 'verified', 'restored', 'failed')),
  created_at TEXT NOT NULL,
  verified_at TEXT
) STRICT;

CREATE TABLE backup_catalog (
  backup_id TEXT PRIMARY KEY,
  recovery_set_id TEXT NOT NULL REFERENCES recovery_sets(recovery_set_id),
  component_kind TEXT NOT NULL,
  backup_kind TEXT NOT NULL,
  storage_location TEXT NOT NULL,
  checksum TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
  retention_until TEXT NOT NULL,
  verified INTEGER NOT NULL CHECK (verified IN (0, 1)),
  status TEXT NOT NULL CHECK (status IN ('available', 'missing', 'retired')),
  created_at TEXT NOT NULL,
  verified_at TEXT
) STRICT;

CREATE TABLE work_projects (
  project_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  name TEXT NOT NULL,
  alias TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  domain TEXT NOT NULL DEFAULT '项目',
  color TEXT NOT NULL DEFAULT '#5B7BFE',
  is_default_internal_project INTEGER NOT NULL DEFAULT 0
    CHECK (is_default_internal_project IN (0, 1)),
  lifecycle_state TEXT NOT NULL
    CHECK (lifecycle_state IN ('active', 'frozen', 'archived')),
  created_by_membership_id TEXT REFERENCES organization_memberships(membership_id),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  archived_at TEXT
) STRICT;

CREATE UNIQUE INDEX work_projects_one_default_per_organization
ON work_projects(organization_id)
WHERE is_default_internal_project = 1 AND lifecycle_state != 'archived';

CREATE INDEX work_projects_by_organization
ON work_projects(organization_id, lifecycle_state, updated_at);

CREATE TABLE project_participants (
  project_id TEXT NOT NULL REFERENCES work_projects(project_id),
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  membership_id TEXT NOT NULL REFERENCES organization_memberships(membership_id),
  participant_role TEXT NOT NULL
    CHECK (participant_role IN ('owner', 'editor', 'viewer')),
  status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (project_id, membership_id)
) STRICT;

CREATE TABLE task_lists (
  task_list_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  name TEXT NOT NULL,
  color TEXT NOT NULL DEFAULT '#5B7BFE',
  scope_kind TEXT NOT NULL CHECK (scope_kind IN ('organization', 'personal')),
  owner_membership_id TEXT REFERENCES organization_memberships(membership_id),
  description TEXT NOT NULL DEFAULT '',
  sort_order INTEGER NOT NULL DEFAULT 0,
  is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0, 1)),
  lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('active', 'archived')),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  archived_at TEXT
) STRICT;

CREATE INDEX task_lists_by_organization
ON task_lists(organization_id, lifecycle_state, sort_order);

CREATE TABLE task_tags (
  task_tag_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  name TEXT NOT NULL,
  color TEXT NOT NULL DEFAULT '#5B7BFE',
  scope_kind TEXT NOT NULL CHECK (scope_kind IN ('organization', 'personal')),
  owner_membership_id TEXT REFERENCES organization_memberships(membership_id),
  lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('active', 'archived')),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  archived_at TEXT
) STRICT;

CREATE INDEX task_tags_by_organization
ON task_tags(organization_id, lifecycle_state, name);

CREATE TABLE task_records (
  task_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  project_id TEXT REFERENCES work_projects(project_id),
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  created_by_membership_id TEXT REFERENCES organization_memberships(membership_id),
  priority TEXT NOT NULL CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
  lifecycle_state TEXT NOT NULL
    CHECK (lifecycle_state IN ('todo', 'in_progress', 'completed', 'cancelled', 'archived')),
  task_kind TEXT NOT NULL DEFAULT 'task',
  visibility_scope TEXT NOT NULL
    CHECK (visibility_scope IN ('organization', 'department', 'participants', 'self')),
  start_date TEXT,
  due_date TEXT,
  scheduled_start_at TEXT,
  scheduled_end_at TEXT,
  deadline_at TEXT,
  duration_minutes INTEGER NOT NULL DEFAULT 60 CHECK (duration_minutes >= 0),
  completion_note TEXT NOT NULL DEFAULT '',
  completed_at TEXT,
  source_type TEXT NOT NULL DEFAULT 'manual',
  source_id TEXT,
  attributes_json TEXT NOT NULL DEFAULT '{}'
    CHECK (json_valid(attributes_json)),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  archived_at TEXT
) STRICT;

CREATE INDEX task_records_by_organization
ON task_records(organization_id, lifecycle_state, updated_at);

CREATE INDEX task_records_by_project
ON task_records(organization_id, project_id, lifecycle_state);

CREATE INDEX task_records_by_schedule
ON task_records(organization_id, scheduled_start_at, scheduled_end_at);

CREATE TABLE task_collaborators (
  task_id TEXT NOT NULL REFERENCES task_records(task_id),
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  membership_id TEXT NOT NULL REFERENCES organization_memberships(membership_id),
  collaborator_role TEXT NOT NULL CHECK (collaborator_role IN ('owner', 'collaborator')),
  inbox_state TEXT NOT NULL CHECK (
    inbox_state IN ('pending', 'accepted', 'acknowledged', 'returned')
  ),
  order_index INTEGER NOT NULL DEFAULT 0,
  return_reason TEXT NOT NULL DEFAULT '',
  handled_at TEXT,
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (task_id, membership_id)
) STRICT;

CREATE UNIQUE INDEX task_collaborators_one_owner
ON task_collaborators(task_id)
WHERE collaborator_role = 'owner' AND inbox_state != 'returned';

CREATE INDEX task_collaborators_by_member
ON task_collaborators(organization_id, membership_id, inbox_state);

CREATE TABLE task_list_memberships (
  task_id TEXT NOT NULL REFERENCES task_records(task_id),
  task_list_id TEXT NOT NULL REFERENCES task_lists(task_list_id),
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  order_index INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (task_id, task_list_id)
) STRICT;

CREATE TABLE task_tag_assignments (
  task_id TEXT NOT NULL REFERENCES task_records(task_id),
  task_tag_id TEXT NOT NULL REFERENCES task_tags(task_tag_id),
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  assigned_by_membership_id TEXT REFERENCES organization_memberships(membership_id),
  created_at TEXT NOT NULL,
  PRIMARY KEY (task_id, task_tag_id)
) STRICT;

CREATE TABLE task_activity_events (
  task_activity_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  task_id TEXT NOT NULL REFERENCES task_records(task_id),
  actor_membership_id TEXT REFERENCES organization_memberships(membership_id),
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(payload_json)),
  happened_at TEXT NOT NULL
) STRICT;

CREATE INDEX task_activity_by_task
ON task_activity_events(organization_id, task_id, happened_at);

CREATE TABLE task_return_notices (
  notice_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  deleted_task_id TEXT NOT NULL,
  task_title TEXT NOT NULL,
  creator_membership_id TEXT NOT NULL REFERENCES organization_memberships(membership_id),
  returned_by_membership_id TEXT NOT NULL REFERENCES organization_memberships(membership_id),
  return_reason TEXT NOT NULL,
  read_at TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (organization_id, deleted_task_id)
) STRICT;

CREATE TABLE source_assets (
  source_asset_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  project_id TEXT REFERENCES work_projects(project_id),
  storage_object_id TEXT REFERENCES storage_objects(object_id),
  file_name TEXT NOT NULL,
  media_type TEXT NOT NULL DEFAULT '',
  byte_size INTEGER NOT NULL DEFAULT 0 CHECK (byte_size >= 0),
  content_hash TEXT NOT NULL DEFAULT '',
  source_kind TEXT NOT NULL,
  source_locator TEXT NOT NULL DEFAULT '',
  lifecycle_state TEXT NOT NULL
    CHECK (lifecycle_state IN ('active', 'missing', 'archived', 'deleted')),
  created_by_membership_id TEXT REFERENCES organization_memberships(membership_id),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE INDEX source_assets_by_project
ON source_assets(organization_id, project_id, lifecycle_state);

CREATE TABLE knowledge_documents (
  document_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  project_id TEXT REFERENCES work_projects(project_id),
  project_assignment_state TEXT NOT NULL DEFAULT 'assigned'
    CHECK (project_assignment_state IN ('assigned', 'unassigned')),
  source_asset_id TEXT REFERENCES source_assets(source_asset_id),
  owner_membership_id TEXT REFERENCES organization_memberships(membership_id),
  department_id TEXT REFERENCES organization_departments(department_id),
  title TEXT NOT NULL,
  document_kind TEXT NOT NULL DEFAULT '',
  visibility_scope TEXT NOT NULL
    CHECK (visibility_scope IN ('organization', 'department', 'participants', 'self')),
  parse_state TEXT NOT NULL CHECK (
    parse_state IN ('not_requested', 'queued', 'processing', 'ready', 'partial_ready', 'failed', 'missing_source')
  ),
  lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('active', 'archived', 'deleted')),
  current_version INTEGER NOT NULL DEFAULT 0 CHECK (current_version >= 0),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK (
    (project_assignment_state = 'assigned' AND project_id IS NOT NULL)
    OR (project_assignment_state = 'unassigned' AND project_id IS NULL)
  )
) STRICT;

CREATE INDEX knowledge_documents_by_project
ON knowledge_documents(organization_id, project_id, lifecycle_state);

CREATE TABLE document_versions (
  document_version_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  document_id TEXT NOT NULL REFERENCES knowledge_documents(document_id),
  version INTEGER NOT NULL CHECK (version >= 1),
  content_hash TEXT NOT NULL,
  preview_text TEXT NOT NULL DEFAULT '',
  markdown_content TEXT NOT NULL DEFAULT '',
  section_count INTEGER NOT NULL DEFAULT 0 CHECK (section_count >= 0),
  chunk_count INTEGER NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
  generator_version TEXT NOT NULL DEFAULT 'legacy-confirmed-import-v1',
  created_at TEXT NOT NULL,
  UNIQUE (document_id, version)
) STRICT;

CREATE TABLE processing_attempts (
  processing_attempt_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  source_asset_id TEXT REFERENCES source_assets(source_asset_id),
  document_id TEXT REFERENCES knowledge_documents(document_id),
  processing_kind TEXT NOT NULL,
  state TEXT NOT NULL CHECK (
    state IN ('queued', 'processing', 'completed', 'partial', 'failed', 'cancelled')
  ),
  attempt_no INTEGER NOT NULL CHECK (attempt_no >= 1),
  error_code TEXT NOT NULL DEFAULT '',
  error_message TEXT NOT NULL DEFAULT '',
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL
) STRICT;

CREATE INDEX processing_attempts_by_document
ON processing_attempts(organization_id, document_id, created_at);

CREATE TABLE event_line_records (
  event_line_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  project_id TEXT REFERENCES work_projects(project_id),
  project_assignment_state TEXT NOT NULL DEFAULT 'assigned'
    CHECK (project_assignment_state IN ('assigned', 'unassigned')),
  created_by_membership_id TEXT REFERENCES organization_memberships(membership_id),
  department_id TEXT REFERENCES organization_departments(department_id),
  name TEXT NOT NULL,
  goal TEXT NOT NULL DEFAULT '',
  background TEXT NOT NULL DEFAULT '',
  visibility_scope TEXT NOT NULL
    CHECK (visibility_scope IN ('organization', 'department', 'participants')),
  lifecycle_state TEXT NOT NULL
    CHECK (lifecycle_state IN ('active', 'paused', 'completed', 'archived')),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  archived_at TEXT,
  CHECK (
    (project_assignment_state = 'assigned' AND project_id IS NOT NULL)
    OR (project_assignment_state = 'unassigned' AND project_id IS NULL)
  )
) STRICT;

CREATE INDEX event_lines_by_project
ON event_line_records(organization_id, project_id, lifecycle_state);

CREATE TABLE event_line_participants (
  event_line_id TEXT NOT NULL REFERENCES event_line_records(event_line_id),
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  membership_id TEXT NOT NULL REFERENCES organization_memberships(membership_id),
  status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (event_line_id, membership_id)
) STRICT;

CREATE TABLE event_line_task_links (
  event_line_id TEXT NOT NULL REFERENCES event_line_records(event_line_id),
  task_id TEXT NOT NULL REFERENCES task_records(task_id),
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  link_state TEXT NOT NULL CHECK (link_state IN ('active', 'revoked')),
  is_milestone INTEGER NOT NULL DEFAULT 0 CHECK (is_milestone IN (0, 1)),
  milestone_order INTEGER,
  linked_by_membership_id TEXT REFERENCES organization_memberships(membership_id),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (event_line_id, task_id)
) STRICT;

CREATE UNIQUE INDEX task_one_active_event_line
ON event_line_task_links(task_id)
WHERE link_state = 'active';

CREATE TABLE event_line_activities (
  event_line_activity_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  event_line_id TEXT NOT NULL REFERENCES event_line_records(event_line_id),
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  happened_at TEXT NOT NULL,
  actor_membership_id TEXT REFERENCES organization_memberships(membership_id),
  title TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  association_state TEXT NOT NULL CHECK (
    association_state IN ('confirmed', 'historical_suggestion', 'revoked')
  ),
  include_in_narrative INTEGER NOT NULL DEFAULT 1
    CHECK (include_in_narrative IN (0, 1)),
  attributes_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(attributes_json)),
  created_at TEXT NOT NULL
) STRICT;

CREATE INDEX event_line_activities_by_line
ON event_line_activities(organization_id, event_line_id, happened_at);

CREATE TABLE event_line_attachments (
  event_line_attachment_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  event_line_id TEXT NOT NULL REFERENCES event_line_records(event_line_id),
  source_asset_id TEXT NOT NULL REFERENCES source_assets(source_asset_id),
  title TEXT NOT NULL,
  purpose TEXT NOT NULL DEFAULT '',
  created_by_membership_id TEXT REFERENCES organization_memberships(membership_id),
  lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('active', 'revoked')),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE evidence_links (
  evidence_link_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  source_type TEXT NOT NULL CHECK (
    source_type IN ('source_asset', 'document_version', 'task', 'meeting', 'event_line_activity')
  ),
  source_id TEXT NOT NULL,
  target_type TEXT NOT NULL CHECK (
    target_type IN ('task', 'event_line', 'narrative_output')
  ),
  target_id TEXT NOT NULL,
  relation_kind TEXT NOT NULL,
  lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('active', 'revoked')),
  linked_by_membership_id TEXT REFERENCES organization_memberships(membership_id),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE INDEX evidence_links_by_target
ON evidence_links(organization_id, target_type, target_id, lifecycle_state);

CREATE TABLE organization_plans (
  plan_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  department_id TEXT REFERENCES organization_departments(department_id),
  period_label TEXT NOT NULL,
  owner_membership_id TEXT REFERENCES organization_memberships(membership_id),
  summary TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'completed', 'archived')),
  attributes_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(attributes_json)),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE organization_plan_items (
  plan_item_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  plan_id TEXT NOT NULL REFERENCES organization_plans(plan_id),
  title TEXT NOT NULL,
  statement TEXT NOT NULL DEFAULT '',
  owner_membership_id TEXT REFERENCES organization_memberships(membership_id),
  expected_output TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL CHECK (status IN ('active', 'completed', 'cancelled', 'archived')),
  sort_order INTEGER NOT NULL DEFAULT 0,
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE weekly_reviews (
  weekly_review_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  membership_id TEXT NOT NULL REFERENCES organization_memberships(membership_id),
  week_label TEXT NOT NULL,
  work_progress TEXT NOT NULL DEFAULT '',
  work_blocker TEXT NOT NULL DEFAULT '',
  work_direction TEXT NOT NULL DEFAULT '',
  next_week_focus TEXT NOT NULL DEFAULT '',
  support_needed TEXT NOT NULL DEFAULT '',
  work_free_note TEXT NOT NULL DEFAULT '',
  personal_growth_note TEXT NOT NULL DEFAULT '',
  private_note TEXT NOT NULL DEFAULT '',
  personal_visibility TEXT NOT NULL CHECK (
    personal_visibility IN ('organization', 'department', 'self')
  ),
  lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('draft', 'submitted', 'archived')),
  version INTEGER NOT NULL CHECK (version >= 1),
  submitted_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (organization_id, membership_id, week_label)
) STRICT;

CREATE TABLE weekly_review_sections (
  weekly_review_section_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  weekly_review_id TEXT NOT NULL REFERENCES weekly_reviews(weekly_review_id),
  section_type TEXT NOT NULL,
  content TEXT NOT NULL,
  content_domain TEXT NOT NULL,
  visibility_scope TEXT NOT NULL
    CHECK (visibility_scope IN ('organization', 'department', 'self')),
  created_at TEXT NOT NULL
) STRICT;

CREATE TABLE weekly_review_task_links (
  weekly_review_task_link_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  weekly_review_id TEXT NOT NULL REFERENCES weekly_reviews(weekly_review_id),
  task_id TEXT NOT NULL REFERENCES task_records(task_id),
  note TEXT NOT NULL DEFAULT '',
  structured_note_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(structured_note_json)),
  reviewed_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (weekly_review_id, task_id)
) STRICT;

CREATE TABLE intelligence_records (
  intelligence_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  project_id TEXT REFERENCES work_projects(project_id),
  title TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  source_url TEXT NOT NULL DEFAULT '',
  record_kind TEXT NOT NULL,
  status TEXT NOT NULL CHECK (
    status IN ('candidate', 'inbox', 'accepted', 'returned', 'archived')
  ),
  visibility_scope TEXT NOT NULL
    CHECK (visibility_scope IN ('organization', 'department', 'participants', 'self')),
  created_by_membership_id TEXT REFERENCES organization_memberships(membership_id),
  source_payload_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(source_payload_json)),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE intelligence_revisions (
  intelligence_revision_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  intelligence_id TEXT NOT NULL REFERENCES intelligence_records(intelligence_id),
  revision INTEGER NOT NULL CHECK (revision >= 1),
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  revised_by_membership_id TEXT REFERENCES organization_memberships(membership_id),
  created_at TEXT NOT NULL,
  UNIQUE (intelligence_id, revision)
) STRICT;

CREATE TABLE growth_signals (
  growth_signal_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  membership_id TEXT NOT NULL REFERENCES organization_memberships(membership_id),
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  week_label TEXT NOT NULL DEFAULT '',
  raw_text TEXT NOT NULL DEFAULT '',
  context_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(context_json)),
  dedupe_key TEXT NOT NULL,
  lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('candidate', 'confirmed', 'revoked')),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (organization_id, dedupe_key)
) STRICT;

CREATE TABLE growth_evidence (
  growth_evidence_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  growth_signal_id TEXT REFERENCES growth_signals(growth_signal_id),
  membership_id TEXT NOT NULL REFERENCES organization_memberships(membership_id),
  ability_key TEXT NOT NULL,
  evidence_type TEXT NOT NULL,
  level TEXT NOT NULL,
  confidence TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  task_id TEXT REFERENCES task_records(task_id),
  validation_state TEXT NOT NULL CHECK (
    validation_state IN ('candidate', 'confirmed', 'rejected', 'revoked')
  ),
  attributes_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(attributes_json)),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE experience_quotes (
  experience_quote_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  author_membership_id TEXT NOT NULL REFERENCES organization_memberships(membership_id),
  quote_text TEXT NOT NULL,
  source_excerpt TEXT NOT NULL DEFAULT '',
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT '方法论',
  lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('active', 'archived', 'deleted')),
  contribution_score REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  version INTEGER NOT NULL CHECK (version >= 1)
) STRICT;

CREATE TABLE experience_reactions (
  experience_reaction_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  experience_quote_id TEXT NOT NULL REFERENCES experience_quotes(experience_quote_id),
  membership_id TEXT NOT NULL REFERENCES organization_memberships(membership_id),
  reaction_type TEXT NOT NULL CHECK (reaction_type IN ('like', 'save')),
  created_at TEXT NOT NULL,
  UNIQUE (experience_quote_id, membership_id, reaction_type)
) STRICT;

CREATE TABLE growth_cards (
  growth_card_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  membership_id TEXT NOT NULL REFERENCES organization_memberships(membership_id),
  weekly_review_id TEXT REFERENCES weekly_reviews(weekly_review_id),
  content_domain TEXT NOT NULL,
  visibility_scope TEXT NOT NULL
    CHECK (visibility_scope IN ('organization', 'department', 'self')),
  summary_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(summary_json)),
  suggestions_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(suggestions_json)),
  lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('active', 'archived')),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE narrative_outputs (
  narrative_output_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  project_id TEXT REFERENCES work_projects(project_id),
  event_line_id TEXT REFERENCES event_line_records(event_line_id),
  output_kind TEXT NOT NULL CHECK (
    output_kind IN ('event_line_mainline', 'event_line_report', 'weekly_report', 'strategy_report')
  ),
  title TEXT NOT NULL,
  lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('draft', 'active', 'stale', 'blocked', 'archived')),
  latest_version INTEGER NOT NULL CHECK (latest_version >= 1),
  created_by_membership_id TEXT REFERENCES organization_memberships(membership_id),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  archived_at TEXT
) STRICT;

CREATE TABLE narrative_output_versions (
  narrative_output_version_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  narrative_output_id TEXT NOT NULL REFERENCES narrative_outputs(narrative_output_id),
  version INTEGER NOT NULL CHECK (version >= 1),
  content_markdown TEXT NOT NULL DEFAULT '',
  content_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(content_json)),
  input_fingerprint TEXT NOT NULL DEFAULT '',
  content_hash TEXT NOT NULL,
  change_summary TEXT NOT NULL DEFAULT '',
  created_by_membership_id TEXT REFERENCES organization_memberships(membership_id),
  created_at TEXT NOT NULL,
  UNIQUE (narrative_output_id, version)
) STRICT;

CREATE TABLE ai_answers (
  ai_answer_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  project_id TEXT REFERENCES work_projects(project_id),
  membership_id TEXT NOT NULL REFERENCES organization_memberships(membership_id),
  question TEXT NOT NULL,
  answer_markdown TEXT NOT NULL,
  source_manifest_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(source_manifest_json)),
  model_name TEXT NOT NULL DEFAULT '',
  lifecycle_state TEXT NOT NULL CHECK (lifecycle_state IN ('active', 'archived')),
  version INTEGER NOT NULL CHECK (version >= 1),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
) STRICT;

CREATE TABLE workbench_favorites (
  favorite_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL REFERENCES organization_records(organization_id),
  membership_id TEXT NOT NULL REFERENCES organization_memberships(membership_id),
  target_type TEXT NOT NULL CHECK (
    target_type IN ('ai_answer', 'knowledge_document', 'narrative_output')
  ),
  target_id TEXT NOT NULL,
  title TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (organization_id, membership_id, target_type, target_id)
) STRICT;
