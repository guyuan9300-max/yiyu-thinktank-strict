ALTER TABLE identity_principals
ADD COLUMN principal_kind TEXT NOT NULL DEFAULT 'human'
CHECK (principal_kind IN ('human', 'bot'));

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
