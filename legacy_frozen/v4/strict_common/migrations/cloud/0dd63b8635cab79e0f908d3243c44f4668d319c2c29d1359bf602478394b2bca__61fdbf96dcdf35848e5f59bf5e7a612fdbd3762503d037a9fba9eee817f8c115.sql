ALTER TABLE organization_records
ADD COLUMN annual_goal TEXT NOT NULL DEFAULT '';

ALTER TABLE organization_records
ADD COLUMN annual_strategy_year TEXT NOT NULL DEFAULT '';

ALTER TABLE organization_records
ADD COLUMN annual_strategy TEXT NOT NULL DEFAULT '';

ALTER TABLE organization_records
ADD COLUMN quarterly_focus_json TEXT NOT NULL DEFAULT '[]'
CHECK (
  json_valid(quarterly_focus_json) AND json_type(quarterly_focus_json) = 'array'
);

ALTER TABLE organization_records
ADD COLUMN leader_membership_id TEXT
REFERENCES organization_memberships(membership_id);

ALTER TABLE organization_records
ADD COLUMN leader_name_override TEXT NOT NULL DEFAULT '';

ALTER TABLE organization_memberships
ADD COLUMN project_role_labels_json TEXT NOT NULL DEFAULT '[]'
CHECK (
  json_valid(project_role_labels_json)
  AND json_type(project_role_labels_json) = 'array'
);

ALTER TABLE organization_memberships
ADD COLUMN current_focus TEXT NOT NULL DEFAULT '';

ALTER TABLE organization_memberships
ADD COLUMN task_edit_scope TEXT NOT NULL DEFAULT 'self'
CHECK (task_edit_scope IN ('self', 'manager', 'department', 'organization'));

ALTER TABLE organization_memberships
ADD COLUMN can_approve_tasks INTEGER NOT NULL DEFAULT 0
CHECK (can_approve_tasks IN (0, 1));

ALTER TABLE organization_memberships
ADD COLUMN can_reassign_tasks INTEGER NOT NULL DEFAULT 0
CHECK (can_reassign_tasks IN (0, 1));

ALTER TABLE organization_memberships
ADD COLUMN can_change_deadline INTEGER NOT NULL DEFAULT 0
CHECK (can_change_deadline IN (0, 1));

ALTER TABLE organization_departments
ADD COLUMN color TEXT NOT NULL DEFAULT '#5B7CFA';

ALTER TABLE organization_departments
ADD COLUMN parent_department_id TEXT
REFERENCES organization_departments(department_id);

ALTER TABLE organization_departments
ADD COLUMN leader_name_override TEXT NOT NULL DEFAULT '';

ALTER TABLE organization_departments
ADD COLUMN mission TEXT NOT NULL DEFAULT '';

ALTER TABLE organization_departments
ADD COLUMN business_context TEXT NOT NULL DEFAULT '';

ALTER TABLE organization_departments
ADD COLUMN team_context TEXT NOT NULL DEFAULT '';

ALTER TABLE organization_departments
ADD COLUMN quarterly_focus_json TEXT NOT NULL DEFAULT '[]'
CHECK (
  json_valid(quarterly_focus_json) AND json_type(quarterly_focus_json) = 'array'
);

ALTER TABLE organization_departments
ADD COLUMN collaboration_department_ids_json TEXT NOT NULL DEFAULT '[]'
CHECK (
  json_valid(collaboration_department_ids_json)
  AND json_type(collaboration_department_ids_json) = 'array'
);

ALTER TABLE management_titles
ADD COLUMN department_id TEXT
REFERENCES organization_departments(department_id);

ALTER TABLE management_titles
ADD COLUMN level TEXT NOT NULL DEFAULT 'employee'
CHECK (
  level IN ('employee', 'supervisor', 'department_lead', 'organization_lead')
);

ALTER TABLE management_titles
ADD COLUMN visibility_scope TEXT NOT NULL DEFAULT 'self'
CHECK (visibility_scope IN ('organization', 'department', 'self'));

ALTER TABLE management_titles
ADD COLUMN manager_title_id TEXT
REFERENCES management_titles(title_id);

ALTER TABLE management_titles
ADD COLUMN is_manager INTEGER NOT NULL DEFAULT 0
CHECK (is_manager IN (0, 1));

ALTER TABLE management_titles
ADD COLUMN goal TEXT NOT NULL DEFAULT '';

ALTER TABLE management_titles
ADD COLUMN responsibilities_json TEXT NOT NULL DEFAULT '[]'
CHECK (
  json_valid(responsibilities_json)
  AND json_type(responsibilities_json) = 'array'
);

ALTER TABLE management_titles
ADD COLUMN should_avoid_json TEXT NOT NULL DEFAULT '[]'
CHECK (
  json_valid(should_avoid_json)
  AND json_type(should_avoid_json) = 'array'
);

ALTER TABLE management_titles
ADD COLUMN collaboration_title_ids_json TEXT NOT NULL DEFAULT '[]'
CHECK (
  json_valid(collaboration_title_ids_json)
  AND json_type(collaboration_title_ids_json) = 'array'
);

ALTER TABLE management_titles
ADD COLUMN task_edit_scope TEXT NOT NULL DEFAULT 'self'
CHECK (task_edit_scope IN ('self', 'manager', 'department', 'organization'));

ALTER TABLE management_titles
ADD COLUMN can_approve_tasks INTEGER NOT NULL DEFAULT 0
CHECK (can_approve_tasks IN (0, 1));

ALTER TABLE management_titles
ADD COLUMN can_reassign_tasks INTEGER NOT NULL DEFAULT 0
CHECK (can_reassign_tasks IN (0, 1));

ALTER TABLE management_titles
ADD COLUMN can_change_deadline INTEGER NOT NULL DEFAULT 0
CHECK (can_change_deadline IN (0, 1));

ALTER TABLE management_titles
ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0;

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
