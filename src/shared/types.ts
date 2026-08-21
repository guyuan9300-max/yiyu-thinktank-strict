export type Priority = 'low' | 'normal' | 'high';
export type TaskStatus = 'inbox' | 'todo' | 'doing' | 'done' | 'rejected';
export type TaskDueDatePreset = 'today' | 'none';
export type TaskListSortMode = 'dueDate' | 'priority' | 'manual';
export type TaskViewPreference = 'inbox' | 'list' | 'calendar' | 'review';
export type TaskReviewScope = 'work' | 'personal';
export type TaskScopeMode = 'COLLAB_SHARED' | 'PERSONAL_ONLY';
export type ReviewCompletionStatus = 'done_on_time' | 'done_late' | 'in_progress' | 'not_done';
export type ReviewAlignmentStatus = 'aligned' | 'partial' | 'misaligned' | 'unknown';
export type ReviewLightweightTag = '' | '资料不足' | '等待他人' | '方向不清' | '资源不够' | '工作过度饱和';
export type AgentDepartmentKey = 'strategy_design' | 'tech_development' | 'info_data';
export type AgentPlanStatus = 'planned' | 'doing' | 'done' | 'blocked';
export type TopicTaskOwnerMode = 'self' | 'empty';
export type TopicCandidateStatus = 'candidate' | 'tracking' | 'promoted' | 'archived';
export type TopicCandidateInsightStatus = 'pending' | 'ready' | 'failed';
export type MeetingStage = 'prepared' | 'ingested' | 'extracted' | 'resolved' | 'published';
export type AiProvider = 'mock' | 'openai_compatible' | 'qwen' | 'doubao' | 'openclaw';
export type AiModelMode = 'auto' | 'online_first' | 'local_first' | 'local_only';
export type AiModelProfileKey = 'online_primary' | 'local_text_deep' | 'local_vision_ocr' | 'local_fast';
export type AiModelCapability = 'online_primary' | 'deep_analysis' | 'vision_ocr' | 'fast_structured';
export type AccountStatus = 'pending' | 'approved' | 'active' | 'rejected' | 'disabled';
export type MembershipStatus = 'none' | 'pending' | 'approved' | 'active' | 'rejected';
// [B] 5/25 PM (顾源源 path C): 加 'ai_agent' 让真 bot 同事 (庆华等) 能作为组织成员存在.
// 跟 admin/employee 一样平权, 但 isLegacyOrganizationEmployee 永远放行.
export type EmployeeRole = 'admin' | 'employee' | 'ai_agent';
export type CollaboratorInboxStatus = 'pending' | 'accepted' | 'returned';
export type OrgRoleLevel = 'employee' | 'supervisor' | 'department_lead' | 'organization_lead';
export type OrgVisibilityScope = 'organization' | 'department' | 'self';
export type OrgReportingLineType = 'business' | 'administrative';
export type OrgTaskEditScope = 'self' | 'manager' | 'department' | 'organization';
export type OrgTaskControlLevel = 'normal' | 'leader_control' | 'department_control' | 'organization_control';
export type OrgRuleActorScope = 'assignee' | 'manager' | 'department_lead' | 'organization_lead' | 'creator';
export type OrgWorkflowTriggerType = 'weekly_followup' | 'task_created' | 'meeting_closed' | 'client_update' | 'manual';
export type OrgFocusPriority = 'high' | 'medium' | 'low';
export type OrgFocusStatus = 'draft' | 'active' | 'paused' | 'done';
export type OrgDepartmentPlanStatus = 'draft' | 'active' | 'closed';
export type OrgDepartmentPlanItemStatus = 'active' | 'paused' | 'done' | 'dropped';
export type TaskPlanLinkSource = 'ai' | 'manager' | 'rule';
export type SupportRequestTargetScope = 'manager' | 'department' | 'organization' | 'cross_department';
export type SupportRequestType = 'resource' | 'decision' | 'collaboration' | 'workload' | 'clarification';
export type SupportRequestStatus = 'open' | 'accepted' | 'resolved' | 'dismissed';
export type DnaSourceLevel = 'organization' | 'client';
export type OrganizationDnaModuleKey = 'organization_intro' | 'business_intro' | 'team_intro' | 'market_intro';
export type FeishuReceiveIdType = 'open_id' | 'user_id' | 'email' | 'chat_id';
export type GrowthAbilityKey = 'exec' | 'collab' | 'analyze' | 'insight' | 'risk' | 'write';
export type GrowthEvidenceType = 'reflection' | 'codification' | 'reuse' | 'improvement';
export type GrowthConfidence = 'high' | 'medium' | 'low';
export type LearningContentType = 'method_card' | 'practice_card' | 'correction_card';
export type LearningRecommendationStatus = 'active' | 'accepted' | 'dismissed';
export type GrowthContributionTag = 'knowledge_asset' | 'critical_resolution' | 'collaboration_enablement' | 'risk_alignment' | 'mechanism_building';
export type GrowthValidationState = 'candidate' | 'observed' | 'validated' | 'institutionalized';
export type GrowthPendingCaptureState = 'open' | 'dismissed' | 'reviewed' | 'promoted';
export type BadgeState = 'locked' | 'progress' | 'ready' | 'lit' | 'mastered';
export type AnalysisScopeType = 'client' | 'event_line' | 'meeting' | 'task' | 'module' | 'flow';
export type AnalysisJobType = 'asset_ingest' | 'evidence_extract' | 'customer_compare' | 'meeting_enhance' | 'dna_refresh' | 'strategy_pack';
export type AnalysisJobStatus =
  | 'queued'
  | 'running'
  | 'preparing'
  | 'extracting'
  | 'clustering'
  | 'comparing'
  | 'drafting'
  | 'awaiting_review'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'rolled_back';
export type AnalysisReviewState = 'draft' | 'awaiting_review' | 'awaiting_revision' | 'approved' | 'rejected' | 'superseded';
export type AnalysisStageStatus = 'queued' | 'running' | 'completed' | 'failed' | 'skipped';
export type AnalysisOriginType = 'projection' | 'analysis' | 'human_override';
export type AnalysisAuthorityLevel = 'fallback' | 'candidate' | 'approved';
export type AnalysisQualityTier = 'legacy' | 'normalized' | 'reviewed';
export type AnalysisIntentProfile = 'task_ai' | 'weekly_review' | 'meeting_enhance' | 'client_overview' | 'strategic_cockpit' | 'dna_summary';
export type AnalysisStaleReason =
  | 'superseded_by_newer_judgment'
  | 'source_snapshot_changed'
  | 'approval_revoked'
  | 'scope_no_longer_primary'
  | 'insufficient_evidence'
  | 'manual_invalidation';
export type AnalysisRejectedReason =
  | 'authority_too_low'
  | 'scope_less_relevant'
  | 'stale'
  | 'superseded'
  | 'insufficient_evidence'
  | 'not_approved_for_official_use';
export type ApprovalDecision = 'approved' | 'rejected' | 'returned_for_revision';
export type ApprovalTargetType = 'judgment_version' | 'dna_delta' | 'conflict_group' | 'proposal_record';
export type AnalysisLane = 'light_extractor' | 'local_deep' | 'cloud_final';

export interface Operator {
  id: string;
  name: string;
  role: string;
  team: string;
  color: string;
  isCurrent: boolean;
}

export interface AiModelProfileRecord {
  enabled: boolean;
  provider: AiProvider;
  providerLabel: string;
  baseUrl: string;
  model: string;
  capability: AiModelCapability;
  isLocal: boolean;
}

export interface AppSettings {
  currentOperatorId: string;
  aiProvider: AiProvider;
  aiProviderLabel: string;
  aiBaseUrl: string;
  aiModel: string;
  dataDir: string;
  backupDir: string;
  cloudApiUrl: string;
  lastBackupAt?: string | null;
  foldersRootLabel: string;
  aiConfigured: boolean;
  aiCredentialSource: string;
  aiFingerprint?: string | null;
  aiConfigVersion?: number;
  advancedAiRoutingEnabled: boolean;
  advancedAiRoutingExecutionState?: string;
  advancedAiRoutingExecutionReason?: string | null;
  aiModelMode: AiModelMode;
  aiModelProfiles: Partial<Record<AiModelProfileKey, AiModelProfileRecord>>;
  demoDataLoaded: boolean;
}

export type SandboxKind = 'local' | 'organization';
export type SandboxStatus = 'active' | 'archived';
export type WorkspaceRuntimeStatus =
  | 'local_draft'
  | 'verifying'
  | 'switching'
  | 'ready'
  | 'needs_login'
  | 'identity_error'
  | 'sync_degraded';
export type WorkspaceIdentityState = 'unverified' | 'verified' | 'needs_login' | 'mismatch' | 'error';

export interface SandboxWorkspaceRecord {
  id: string;
  kind: SandboxKind;
  name: string;
  status: SandboxStatus;
  cloudApiUrl: string;
  cloudConnected: boolean;
  cloudConnectionStatus?: 'not_configured' | 'signed_out' | 'needs_login' | 'failed_retryable' | 'connected';
  cloudNeedsLogin?: boolean;
  cloudInstanceId?: string;
  identityState?: WorkspaceIdentityState;
  identityVerifiedAt?: string | null;
  identityError?: string;
  runtimeStatus?: WorkspaceRuntimeStatus;
  statusMessage?: string;
  requiresLogin?: boolean;
  sessionSnapshot?: Record<string, unknown>;
  cloudUserFullName?: string | null;
  cloudUserEmail?: string | null;
  organizationId?: string | null;
  organizationName?: string | null;
  localIdentityId?: string | null;
  isLegacyDefault: boolean;
  metadata: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
  lastActiveAt?: string | null;
}

export interface SandboxLocalDraftSummary {
  available: boolean;
  active: boolean;
  hasData: boolean;
  migrated: boolean;
  migratedToSandboxId?: string | null;
  migratedAt?: string | null;
  clients: number;
  tasks: number;
  taskLists: number;
  taskTags: number;
  documents: number;
  experienceQuotes: number;
}

export interface SandboxWorkspacesResponse {
  activeSandboxId: string;
  workspaces: SandboxWorkspaceRecord[];
  localDraftSummary?: SandboxLocalDraftSummary | null;
}

export interface WorkspaceTaskMirrorRepairResponse {
  sandboxId: string;
  buildFingerprint: string;
  status: 'completed' | 'skipped' | 'deferred';
  ran: boolean;
  scannedCount: number;
  removedCount: number;
  preservedCount: number;
  reason: string;
  completedAt?: string | null;
}

export interface WorkspaceSessionDiagnosticsRecord {
  sandboxId: string;
  kind: SandboxKind;
  name: string;
  cloudApiUrl: string;
  cloudInstanceId: string;
  organizationId?: string | null;
  organizationName?: string | null;
  identityState: WorkspaceIdentityState;
  identityVerifiedAt?: string | null;
  identityError: string;
  runtimeStatus: WorkspaceRuntimeStatus;
  requiresLogin: boolean;
  hasCloudApiUrl: boolean;
  hasAccessToken: boolean;
  hasRefreshToken: boolean;
  hasSessionUser: boolean;
  sessionOrganizationId: string;
  sessionUserId: string;
  sessionUserEmail: string;
  sessionUserFullName: string;
  lastActiveAt?: string | null;
  updatedAt: string;
}

export interface WorkspaceSessionDiagnosticsResponse {
  activeSandboxId: string;
  workspaces: WorkspaceSessionDiagnosticsRecord[];
}

export interface SandboxWorkspaceCreatePayload {
  kind: SandboxKind;
  name: string;
  cloudApiUrl?: string;
}

export interface SandboxWorkspaceUpdatePayload {
  name?: string;
  cloudApiUrl?: string;
}

export interface SessionUser {
  id: string;
  organizationId: string;
  organizationName?: string | null;
  email: string;
  phone?: string | null;
  fullName: string;
  primaryRole: EmployeeRole;
  accountStatus: AccountStatus;
  membershipStatus?: MembershipStatus;
  membershipRejectedReason?: string | null;
  departmentId?: string | null;
  departmentName?: string | null;
  isDepartmentLead?: boolean;
  visibilityScope?: OrgVisibilityScope;
  managementTitleId?: string | null;
  managementTitleName?: string | null;
  pendingInviteCode?: string | null;
  jobTitle?: string | null;
  managerName?: string | null;
  currentFocus?: string | null;
}

export interface OrganizationCandidate {
  organizationId: string;
  organizationName?: string | null;
  organizationSlug?: string | null;
  memberId: string;
  fullName: string;
  email: string;
  primaryRole: EmployeeRole;
  accountStatus: AccountStatus;
  membershipStatus?: MembershipStatus;
  departmentId?: string | null;
  departmentName?: string | null;
}

export interface ViewerAuthorizationProjection {
  state: 'loading' | 'ready' | 'empty' | 'blocked' | 'failed_retryable' | 'not_connected';
  freshness: 'current' | 'stale' | 'expired' | 'unknown';
  reasonCode?: string | null;
  retryable: boolean;
  principalId?: string | null;
  membershipId?: string | null;
  organizationId?: string | null;
  scopeId?: string | null;
  systemRole?: string | null;
  visibilityScope?: OrgVisibilityScope | null;
  policyVersion?: number | null;
  policyVersionId?: string | null;
  projectionId?: string | null;
  surfaces: string[];
  capabilities: string[];
  generatedAt?: string | null;
  lastConfirmedAt?: string | null;
  leaseExpiresAt?: string | null;
  sourceVersion?: number | null;
}

export interface AuthState {
  authenticated: boolean;
  user?: SessionUser | null;
  message?: string | null;
  sessionMode?: 'local' | 'cloud';
  requiresLocalIdentitySetup?: boolean;
  localIdentityStatus?: 'needs_setup' | 'ready' | 'none' | 'draft' | null;
  // 后端 auth/me 在"网络中断 + 本地缓存兜底"时置 true。前端据此把"成员资格未确认"
  // 当成 last-known-good（沿用上次已知身份/数据），而不是当成"被拒绝"去清空客户列表 / 强制身份页。
  degraded?: boolean;
  organizationSelectionRequired?: boolean;
  organizationSelectionToken?: string | null;
  organizations?: OrganizationCandidate[];
  authorization?: ViewerAuthorizationProjection | null;
}

export type ConsultationKnowledgeTarget = 'vector_memory' | 'document_archive';
export type ConsultationKnowledgeRequestStatus = 'pending' | 'processing' | 'completed' | 'failed';

export interface ConsultationKnowledgeRequestRecord {
  id: string;
  answerId: string;
  organizationId: string;
  target: ConsultationKnowledgeTarget;
  status: ConsultationKnowledgeRequestStatus;
  requestedByUserId: string;
  requestedByName: string;
  clientId?: string | null;
  clientName?: string | null;
  taskId?: string | null;
  eventLineId?: string | null;
  planningCycleId?: string | null;
  question: string;
  answer: string;
  errorMessage?: string | null;
  localDocumentId?: string | null;
  localDocumentPath?: string | null;
  completedAt?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ConsultationKnowledgeProcessSummary {
  totalPending: number;
  processedCount: number;
  completedCount: number;
  failedCount: number;
  skippedCount: number;
  updatedAt: string;
  items: ConsultationKnowledgeRequestRecord[];
}

export interface EmployeeRecord {
  id: string;
  email: string;
  phone?: string | null;
  fullName: string;
  primaryRole: EmployeeRole;
  accountStatus: AccountStatus;
  membershipStatus?: MembershipStatus;
  membershipApplicationId?: string | null;
  membershipApplicationState?: 'pending' | 'approved' | 'rejected' | 'withdrawn' | null;
  membershipApplicationVersion?: number | null;
  membershipApplicationRequestedDepartmentId?: string | null;
  membershipApplicationRequestedDepartmentName?: string | null;
  membershipApplicationRequestedManagementTitleId?: string | null;
  membershipApplicationRequestedManagementTitleName?: string | null;
  membershipApplicationRequestedJobTitle?: string | null;
  membershipApplicationRequestedManagerName?: string | null;
  membershipApplicationRequestedCurrentFocus?: string | null;
  membershipRejectedReason?: string | null;
  membershipSubmittedAt?: string | null;
  departmentId?: string | null;
  departmentName?: string | null;
  jobTitle?: string | null;
  managerName?: string | null;
  currentFocus?: string | null;
  isBot?: boolean;
  isDepartmentLead?: boolean;
  visibilityScope?: OrgVisibilityScope;
  managementTitleId?: string | null;
  managementTitleName?: string | null;
  approvedAt?: string | null;
  rejectedReason?: string | null;
  disabledAt?: string | null;
  lastLoginAt?: string | null;
  createdAt: string;
}

export interface MaintenanceModeStatus {
  available: boolean;
  active: boolean;
  canEnter: boolean;
  canManagePermissions: boolean;
  organizationId?: string | null;
  userId?: string | null;
  reason?: string | null;
}

export interface DepartmentOption {
  id: string;
  name: string;
  color: string;
}

export interface OrgInviteResolveResult {
  valid: boolean;
  organizationId?: string | null;
  organizationName?: string | null;
  targetType?: 'department' | 'management_role' | null;
  departmentId?: string | null;
  departmentName?: string | null;
  roleKey?: string | null;
  roleName?: string | null;
  managementTitleId?: string | null;
  managementTitleName?: string | null;
  message?: string | null;
}

export interface OrgProfileSettings {
  version?: number;
  organizationId: string;
  name: string;
  annualGoal: string;
  annualStrategyYear: string;
  annualStrategy: string;
  quarterPlans: OrgQuarterPlanSettings[];
  quarterlyFocus: string[];
  leaderUserId?: string | null;
  leaderName?: string;
  introDocument?: OrgIntroDocumentSettings | null;
  managementUserIds: string[];
  updatedAt: string;
}

export type OrgQuarterKey = 'Q1' | 'Q2' | 'Q3' | 'Q4';

export interface OrgQuarterPlanSettings {
  id: string;
  version?: number;
  year: string;
  quarter: OrgQuarterKey;
  theme: string;
  objective: string;
  keyResults: string[];
  keyActions: string[];
  majorRisks: string[];
  updatedAt: string;
}

export interface OrgDepartmentQuarterPlanSettings {
  planId?: string;
  planVersion?: number;
  year: string;
  quarter: OrgQuarterKey;
  objective: string;
  deliverables: string[];
  successMetrics: string[];
  majorRisks: string[];
  updatedAt: string;
}

export interface OrgDepartmentSettings {
  id: string;
  version?: number;
  name: string;
  color: string;
  leaderUserId?: string | null;
  leaderName?: string;
  introDocument?: OrgIntroDocumentSettings | null;
  parentDepartmentId?: string | null;
  mission: string;
  businessContext: string;
  teamContext: string;
  quarterPlan: OrgDepartmentQuarterPlanSettings;
  quarterlyFocus: string[];
  collaborationDepartmentIds: string[];
  active: boolean;
  updatedAt: string;
}

export interface OrgIntroDocumentSettings {
  documentId?: string;
  version?: number;
  expectedVersion?: number;
  fileName: string;
  fileType: string;
  markdownContent: string;
  normalizedText: string;
  summary: string;
  contentHash: string;
  uploadedBy: string;
  uploadedAt: string;
}

export interface OrgRoleTemplateSettings {
  id: string;
  version?: number;
  departmentId?: string | null;
  name: string;
  level: OrgRoleLevel;
  visibilityScope?: OrgVisibilityScope;
  managerRoleId?: string | null;
  isManager: boolean;
  goal: string;
  responsibilities: string[];
  shouldAvoid: string[];
  collaborationRoleIds: string[];
  taskEditScope: OrgTaskEditScope;
  canApproveTasks: boolean;
  canReassignTasks: boolean;
  canChangeDeadline: boolean;
  sortOrder: number;
  active: boolean;
  /**
   * 顾源源 5/24 V2.1 lab: 若为非空, 表示该岗位的持岗人是某个机器人同事 (bot_member.id).
   * 与员工持岗人 (通过 bindings.primaryRoleId 反查) 互斥: 任一被设置时另一方应为空.
   * 旧数据无该字段, 加载时按 null 处理, 0 风险向后兼容.
   */
  holderBotId?: string | null;
  updatedAt: string;
}

export interface OrgEmployeeBindingSettings {
  userId: string;
  version?: number;
  departmentId?: string | null;
  primaryRoleId?: string | null;
  managerUserId?: string | null;
  isManager: boolean;
  visibilityScope?: OrgVisibilityScope;
  projectRoleLabels: string[];
  currentFocus: string;
  taskEditScope: OrgTaskEditScope;
  canApproveTasks: boolean;
  canReassignTasks: boolean;
  canChangeDeadline: boolean;
  updatedAt: string;
}

export interface OrgReportingLineSettings {
  id: string;
  version?: number;
  managerUserId: string;
  reportUserId: string;
  lineType: OrgReportingLineType;
  approvesTasks: boolean;
  canAdjustTasks: boolean;
  canChangeDeadline: boolean;
  canReassignTasks: boolean;
  isCrossDepartmentApprover: boolean;
  active: boolean;
  updatedAt: string;
}

export interface OrgTaskControlRuleSettings {
  id: string;
  version?: number;
  name: string;
  controlLevel: OrgTaskControlLevel;
  departmentId?: string | null;
  roleTemplateId?: string | null;
  contentEditableBy: OrgRuleActorScope;
  deadlineEditableBy: OrgRuleActorScope;
  ownerEditableBy: OrgRuleActorScope;
  cancellableBy: OrgRuleActorScope;
  requireCollabConfirmation: boolean;
  defaultApproverUserId?: string | null;
  active: boolean;
  updatedAt: string;
}

export interface OrgRoleProcessTemplateSettings {
  id: string;
  version?: number;
  roleTemplateId?: string | null;
  name: string;
  triggerType: OrgWorkflowTriggerType;
  triggerCondition: string;
  keySteps: string[];
  collaborationStep: string;
  approvalStep: string;
  outputArtifact: string;
  commonBlockers: string[];
  active: boolean;
  updatedAt: string;
}

export interface OrgFocusItemSettings {
  id: string;
  version?: number;
  planVersion?: number;
  periodKey: string;
  title: string;
  statement: string;
  ownerUserId?: string | null;
  priority: OrgFocusPriority;
  status: OrgFocusStatus;
  evidenceKeywords: string[];
  updatedAt: string;
}

export interface OrgDepartmentPlanItemSettings {
  id: string;
  version?: number;
  focusItemId?: string | null;
  title: string;
  statement: string;
  ownerUserId?: string | null;
  status: OrgDepartmentPlanItemStatus;
  expectedOutput: string;
  sortOrder: number;
  updatedAt: string;
}

export interface OrgDepartmentPlanSettings {
  id: string;
  version?: number;
  departmentId?: string | null;
  clientId?: string | null;
  weekLabel: string;
  ownerUserId?: string | null;
  title?: string;
  summary: string;
  majorRisks: string[];
  dependencies: string[];
  status: OrgDepartmentPlanStatus;
  items: OrgDepartmentPlanItemSettings[];
  updatedAt: string;
}

export interface TaskPlanLinkRecord {
  taskId: string;
  planningCycleId?: string | null;
  departmentPlanItemId?: string | null;
  focusItemId?: string | null;
  linkedBy: TaskPlanLinkSource;
  confidence: number;
  updatedAt: string;
}

export interface TaskPlanLinkUpsertPayload {
  planningCycleId?: string | null;
  departmentPlanItemId?: string | null;
  focusItemId?: string | null;
}

export interface SupportRequestRecord {
  id: string;
  taskId?: string | null;
  requesterUserId: string;
  targetScope: SupportRequestTargetScope;
  targetRefId?: string | null;
  requestType: SupportRequestType;
  urgency: OrgFocusPriority;
  summary: string;
  status: SupportRequestStatus;
  resolutionNote: string;
  createdAt: string;
  updatedAt: string;
}

export interface SupportRequestCreatePayload {
  taskId?: string | null;
  eventLineId?: string | null;
  targetScope: SupportRequestTargetScope;
  targetRefId?: string | null;
  requestType: SupportRequestType;
  urgency: OrgFocusPriority;
  summary: string;
}

export interface SupportRequestResolvePayload {
  resolutionNote?: string;
  status?: 'accepted' | 'resolved' | 'dismissed';
}

export interface TaskOrgBackfillResult {
  organizationId: string;
  totalTasks: number;
  linkedTasks: number;
  createdLinks: number;
  updatedLinks: number;
  updatedAt: string;
}

export interface OrgModelSettings {
  organization: OrgProfileSettings;
  departments: OrgDepartmentSettings[];
  roles: OrgRoleTemplateSettings[];
  bindings: OrgEmployeeBindingSettings[];
  reportingLines: OrgReportingLineSettings[];
  taskControlRules: OrgTaskControlRuleSettings[];
  roleProcessTemplates: OrgRoleProcessTemplateSettings[];
  focusItems: OrgFocusItemSettings[];
  departmentPlans: OrgDepartmentPlanSettings[];
  updatedAt: string;
  authorityStates?: {
    identityStructure?: { state: string; authority?: string[] };
    organizationPlans?: { state: string; authority?: string[] };
    unfrozenSemanticFields?: {
      state: 'blocked' | 'ready' | 'not_connected';
      reasonCode?: string;
      message?: string;
      fields?: string[];
    };
    roleProcessAutomation?: {
      state: 'blocked' | 'ready';
      reasonCode?: string;
      message?: string;
      configurationAuthority?: string;
      executionAttemptCreated?: boolean;
    };
  };
}

export interface MentionCandidate {
  id: string;
  fullName: string;
  email: string;
  primaryRole: EmployeeRole;
  isSelf: boolean;
}

export interface HealthAiState {
  provider: AiProvider;
  providerLabel: string;
  baseUrl: string;
  model: string;
  ready: boolean;
  detail: string;
  credentialSource: string;
  fingerprint?: string | null;
  profileKey?: string;
  mode?: AiModelMode;
}

export interface LastCloudAiSyncStatus {
  state: 'never' | 'syncing' | 'ready_direct' | 'uploaded' | 'failed' | 'skipped' | 'not_ready' | 'error';
  at?: string | null;
  reason?: string | null;
  provider?: string | null;
  providerLabel?: string | null;
  model?: string | null;
  baseUrl?: string | null;
  hasApiKey: boolean;
  fingerprint?: string | null;
}

export interface OrgAiRuntimeStatus {
  state: 'syncing' | 'ready_direct' | 'not_ready' | 'error';
  source: 'organization_direct';
  sandboxId: string;
  organizationId: string;
  provider: string;
  providerLabel: string;
  model: string;
  configVersion: string;
  fingerprint?: string | null;
  syncedAt?: string | null;
  lastError?: string | null;
  usingCachedConfig: boolean;
}

export interface HealthResponse {
  backend: 'online';
  pollingEnabled?: boolean;
  appName: string;
  appVersion: string;
  buildVersion: string;
  gitCommit?: string | null;
  backendBuildHash?: string;
  backendSchemaVersion?: number;
  runtimeMode?: 'packaged' | 'dev';
  startedAt: string;
  featureFlags: string[];
  dataDir: string;
  stats: {
    clients: number;
    tasks: number;
    topics: number;
    handbookEntries: number;
    analysisRuns: number;
  };
  ai: HealthAiState;
  aiProfiles?: Partial<Record<AiModelProfileKey | 'unified', HealthAiState>>;
  advancedAiRoutingEnabled?: boolean;
  aiModelMode?: AiModelMode;
  linkMaterialDiagnostics?: {
    ytDlpVersion?: string | null;
    curlCffiAvailable?: boolean;
    impersonationAvailable?: boolean;
    impersonationTarget?: string | null;
    ffmpegAvailable?: boolean;
    supportedCookieBrowsers?: string[];
    bbdownAvailable?: boolean;
  };
  platformCapabilities?: Partial<Record<
    'backgroundTasks' | 'audioTranscription' | 'localAi' | 'ollama' | 'feishu',
    {
      state: 'ready' | 'idle' | 'active' | 'not_connected' | 'blocked' | 'failed_retryable' | 'unknown' | string;
      pollingEnabled: boolean;
      message?: string;
    }
  >>;
}

export interface ClientSummary {
  id: string;
  name: string;
  alias: string;
  domain: string;
  type: string;
  intro: string;
  stage: string;
  color?: string;
  folderCount: number;
  documentCount: number;
  taskCount: number;
  lastActivityAt?: string | null;
  // P7：项目编辑弹窗扩展字段
  //   relatedUserIds：勾选的相关同事 user.id（批 3 接通 cloud sync 后驱动跨用户可见）
  //   isDataCenterIncluded：是否进入数据中心计算（false = 仅工作台可见）
  relatedUserIds?: string[];
  /** Names already resolved while the project authority row is loaded. */
  relatedUsers?: MentionCandidate[];
  /** Stable membership that created and owns the project. */
  ownerMembershipId?: string | null;
  /** Project creator plus explicitly shared organization administrators. */
  managerNames?: string[];
  /** Explicitly shared ordinary members, excluding project managers. */
  sharedMemberCount?: number;
  isDataCenterIncluded?: boolean;
  isDefaultInternalProject?: boolean;
  // 全局冷冻:true 表示该项目被冷冻,所有自动 job/列表/下拉都跳过
  isFrozen?: boolean;
  frozenAt?: string | null;
  syncStatus?: 'local' | 'syncing' | 'synced' | 'pending' | 'error' | 'remote_missing' | null;
  cloudId?: string | null;
  lastSyncError?: string | null;
  officialWebsiteUrl?: string | null;
  /** GC-02 object-grant capabilities for the current membership. */
  viewerCapabilities?: Array<'read' | 'write' | 'contributeKnowledge' | 'manageSharing' | string>;
  /** Strict cloud CAS version captured when the editor opens. */
  _strictVersion?: number;
}

export interface ClientFolder {
  id: string;
  clientId: string;
  label: string;
  path: string;
  fileCount: number;
  lastScannedAt?: string | null;
  folderKind?: string;
  sourceType?: string;
  isSystem?: boolean;
  isHidden?: boolean;
  sortOrder?: number;
  createdByRule?: string;
  suggested?: boolean;
  confidence?: number;
}

export interface ClientFolderRecommendation {
  targetFolderLabel: string;
  confidence: number;
  reason: string;
  suggestedTags: string[];
  needsReview?: boolean;
  documentCount?: number;
  exampleDocuments?: string[];
}

export interface ClientFolderRecommendationPlan {
  clientId: string;
  generatedAt: string;
  visibleFolderLimit: number;
  visibleFolderBudget?: number;
  recommendedVisibleFolders?: string[];
  hiddenLegacyFolders?: string[];
  pendingReasonCounts?: Record<string, number>;
  folders: ClientFolderRecommendation[];
  totalDocumentCount: number;
  pendingDocumentCount: number;
  lowConfidenceDocumentCount: number;
}

export interface DocumentAutoRepairPreviewPayload {
  documentIds?: string[];
  limit?: number;
  includeHumanRequired?: boolean;
}

export interface DocumentAutoRepairApplyPayload {
  previewId?: string | null;
  documentIds?: string[];
  includeHumanRequired?: boolean;
  limit?: number;
}

export type DocumentAutoRepairHealthStatus =
  | 'v2_ready'
  | 'original_nonzero_no_v2'
  | 'zero_byte_original'
  | 'md_compat_candidate'
  | 'missing_original'
  | 'parse_failed'
  | 'duplicate_candidate'
  | 'unknown';

export type DocumentAutoRepairStage =
  | 'ready_classify'
  | 'repair_ingest'
  | 'repair_markdown'
  | 'repair_dedupe'
  | 'soft_cleanup'
  | 'minimal_human_check'
  | 'skip';

export interface DocumentAutoRepairItem {
  documentId: string;
  v2DocumentId?: string | null;
  title: string;
  kind: string;
  healthStatus: DocumentAutoRepairHealthStatus;
  stage: DocumentAutoRepairStage;
  nextSystemAction: string;
  targetFolder: string;
  tags: string[];
  searchPolicy: 'include' | 'include_low_weight' | 'exclude_until_repaired' | 'exclude';
  requiresHuman: boolean;
  humanQuestion?: string | null;
  confidence: number;
  reason: string;
  sourcePath?: string | null;
  duplicateOfDocumentId?: string | null;
}

export interface DocumentAutoRepairPreview {
  previewId: string;
  clientId: string;
  generatedAt: string;
  visibleFolderBudget: number;
  recommendedVisibleFolders: string[];
  pendingReasonCounts: Record<string, number>;
  summary: Record<string, number>;
  items: DocumentAutoRepairItem[];
}

export interface DocumentAutoRepairApplyResult {
  jobId?: string | null;
  status: 'queued' | 'completed' | 'failed';
  queuedCount: number;
  skippedCount: number;
  humanConfirmationCount: number;
  message: string;
}

export interface DocumentRecord {
  id: string;
  clientId: string;
  folderId?: string | null;
  title: string;
  path: string;
  originalSourcePath?: string | null;
  kind: string;
  // 后端实际有 30+ 种 source 值（folder/file/task_attachment/workspace_native/answer_memory_doc/auto_repair...），
  // 老 union 类型不全导致前端无法 type-safe 比较；放宽为 string，由消费方明确处理已知集合。
  source: string;
  excerpt: string;
  tags: string[];
  importedAt: string;
  parseStatus?: 'not_requested' | 'queued' | 'processing' | 'ready' | 'blocked' | 'failed_retryable' | string;
  wikiStatus?: 'not_requested' | 'queued' | 'processing' | 'ready' | 'blocked' | 'failed_retryable' | string;
  processingAttemptId?: string | null;
  processingAttemptNo?: number;
  processingStage?: string | null;
  processingErrorCode?: string | null;
  processingMessage?: string | null;
  processingRetryable?: boolean;
  processedAt?: string | null;
  sharedSummaryState?: 'not_requested' | 'ready' | 'blocked' | 'failed_retryable' | string;
  sharedSummaryMessage?: string | null;
  sharedSummaryUpdatedAt?: string | null;
}

export interface KnowledgeStatus {
  totalDocuments: number;
  totalChunks: number;
  ocrReadyRate?: number;  // R13: OCR 完整识别率（加权 % · ready=100/partial=70/failed=0）
  vectorizedDocuments: number;
  dedupedDocuments: number;
  reviewPendingDocuments: number;
  surrogateCount: number;
  memoryDocCount: number;
  masterIndexCount: number;
  reclassifiedDocumentCount: number;
  qdrantReady: boolean;
  lastUpdatedAt?: string | null;
  pendingJobs: number;
  runningJobs: number;
  lastJobStatus: 'idle' | 'queued' | 'running' | 'completed' | 'failed';
  lastJobError?: string | null;
  lastSuccessfulRunAt?: string | null;
  embeddingMode: string;
  embeddingModel?: string | null;
  embeddingError?: string | null;
  embeddingProvider?: string | null;
  embeddingDimension?: number | null;
  embeddingSignature?: string | null;
  activeVectorCollection?: string | null;
  vectorIndexStatus?: 'ready' | 'stale' | 'building' | 'failed' | null;
  routerEnabled?: boolean;
  routerModel?: string | null;
  rerankEnabled?: boolean;
}

export interface OrganizationNotebookSnapshot {
  id: string;
  clientId: string;
  organizationIntro: string;
  collaborationRelationship: string;
  currentStage: string;
  businessModules: string[];
  keyPeople: string[];
  keyProducts: string[];
  currentChallenges: string[];
  collaborationGoals: string[];
  recentFacts: string[];
  informationGaps: string[];
  updatedAt: string;
  confidence: number;
}

export interface EventLineMemorySnapshot {
  id: string;
  eventLineId: string;
  lineName: string;
  currentStage: string;
  currentWork: string;
  currentBlocker: string;
  recentDecision: string;
  nextStep: string;
  evidenceRefs: string[];
  clarificationNeeds: string[];
  analysisSignals: string[];
  predictionReadiness: number;
  updatedAt: string;
  confidence: number;
}

export interface MemoryFact {
  id: string;
  scopeType: 'client' | 'person' | 'product' | 'event_line' | 'task';
  scopeId: string;
  factKey: string;
  factValue: string;
  sourceType: string;
  sourceId: string;
  confidence: number;
  freshness: number;
  evidenceRefs: string[];
  createdAt: string;
  updatedAt: string;
}

export interface ClarificationRecord {
  id: string;
  scopeType: 'client' | 'person' | 'product' | 'event_line' | 'task';
  scopeId: string;
  slotKey: string;
  question: string;
  status: 'pending' | 'answered';
  answerText?: string | null;
  writeScope: string[];
  resolvedFactIds: string[];
  reusable: boolean;
  createdAt: string;
  answeredAt?: string | null;
  updatedAt: string;
}

export interface MemoryStatus {
  clientId: string;
  notebookCompleteness: number;
  notebookConfidence: number;
  eventLineCoverage: number;
  totalEventLines: number;
  coveredEventLines: number;
  pendingClarifications: number;
  lowEvidenceJudgments: number;
  updatedAt: string;
}

export interface BackgroundReadiness {
  score: number;
  level: 'low' | 'medium' | 'high';
  missingSlots: string[];
  backgroundSources: string[];
}

export interface DocumentCard {
  id: string;
  docId: string;
  clientId: string;
  documentId: string;
  title: string;
  originalPath: string;
  sourcePath: string;
  logicalCategory?: string | null;
  logicalSubcategory?: string | null;
  classificationReason?: string | null;
  importSourcePath?: string | null;
  currentHumanPath?: string | null;
  humanFolderCategory?: string | null;
  normalizedPath?: string | null;
  surrogateMdPath?: string | null;
  kind: string;
  primaryCategory: string;
  secondaryCategory: string;
  shortSummary: string;
  summary: string;
  retrievalSummary: string;
  documentRole: string;
  queryHints: string[];
  distinctFindings: string[];
  coreQuestions: string[];
  keywords: string[];
  tags: string[];
  entities: string[];
  dateRange?: string | null;
  classificationConfidence: number;
  needsReview: boolean;
  deepRead: boolean;
  lastHitQuestion?: string | null;
  dedupStatus: string;
  vectorStatus: string;
  version: number;
  chunkCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface ImportDocumentRecord {
  documentId: string;
  title: string;
  fileName: string;
  path: string;
}

export interface ImportRecord {
  id: string;
  clientId: string;
  sourcePath: string;
  mode: 'folder' | 'file';
  status: 'queued' | 'processing' | 'completed' | 'partial' | 'failed' | 'scanned';
  localState?: string;
  cloudMetadataState?: string;
  overallState?: string;
  retryable?: boolean;
  message?: string;
  importedCount: number;
  skippedCount: number;
  duplicateCount?: number;
  versionUpgradeCount?: number;
  unsupportedCount?: number;
  filteredSystemCount?: number;
  filteredVideoCount?: number;
  skippedFiles?: Array<{
    path: string;
    fileName: string;
    reason: string;
    category: string;
  }>;
  createdAt: string;
  jobId?: string | null;
  documents?: ImportDocumentRecord[];
}

export interface WorkspaceImportBackfillResponse {
  importId: string;
  jobId: string;
  sourceRoot: string;
  discovered: number;
  imported: number;
  skipped: number;
}

export interface ClientTemplateFillField {
  label: string;
  value: string;
  status: 'filled' | 'missing';
  evidenceTitles: string[];
  webSourceTitles?: string[];
  fieldType?: 'precise_fact' | 'structural_summary' | 'governance_mechanism' | 'quantitative_result' | 'attachment_material' | 'general' | null;
  valueKind?: 'fact' | 'summary' | 'inference' | 'missing' | null;
  confidence?: number | null;
  basisSummary?: string | null;
  followUpQuestion?: string | null;
  suggestedSources?: string[];
  reviewRequired?: boolean;
}

export interface ClientTemplateFillResponse {
  path: string;
  fileName: string;
  fieldCount: number;
  filledCount: number;
  missingCount: number;
  reviewFieldCount?: number;
  attachmentChecklist?: string[];
  fields: ClientTemplateFillField[];
}

export interface ClientTemplateFillRun {
  id: string;
  clientId: string;
  templateName: string;
  templatePath: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  phase: 'queued' | 'parsing' | 'retrieving' | 'writing' | 'completed' | 'failed';
  progress: number;
  stageLabel?: string | null;
  elapsedMs: number;
  fieldCount: number;
  processedCount: number;
  filledCount: number;
  missingCount: number;
  reviewFieldCount?: number;
  currentFieldLabel?: string | null;
  evidenceTitles: string[];
  attachmentChecklist?: string[];
  fields: ClientTemplateFillField[];
  outputPath?: string | null;
  errorMessage?: string | null;
  createdAt: string;
  updatedAt: string;
}

export type LinkMaterialPlatform = 'bilibili' | 'xiaohongshu' | 'wechat_article';
export type LinkMaterialImportStatus = 'queued' | 'running' | 'completed' | 'failed' | 'canceled';
export type LinkMaterialMediaCacheStatus = 'not_downloaded' | 'cleaned' | 'retained' | 'failed';
export type LinkMaterialCookieBrowser = 'firefox' | 'chrome' | 'edge' | 'safari';

export interface LinkMaterialImportRun {
  runId: string;
  clientId: string;
  sourcePlatform: LinkMaterialPlatform;
  sourceUrl: string;
  title?: string | null;
  status: LinkMaterialImportStatus;
  state?: string;
  stage: string;
  progress: number;
  documentId?: string | null;
  documentPath?: string | null;
  mediaCacheStatus: LinkMaterialMediaCacheStatus;
  error?: string | null;
  errorCode?: string | null;
  retryable?: boolean;
  pollingEnabled?: boolean;
  sharedKnowledgeState?: string | null;
  sharedKnowledgeError?: string | null;
  publishedSummaryDocumentId?: string | null;
  metadata: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export interface GoalRecord {
  id: string;
  clientId: string;
  title: string;
  quarter: string;
  progress: number;
  ownerName: string;
}

export interface DnaTerm {
  id: string;
  clientId: string;
  category: string;
  canonicalName: string;
  aliases: string[];
  description: string;
  sourceLevel: DnaSourceLevel;
}

export interface EvidenceItem {
  id: string;
  title: string;
  excerpt: string;
  sourceType: string;
  documentId?: string | null;
  documentFamilyId?: string | null;
  canonicalKind?: string | null;
  originType?: string | null;
  originId?: string | null;
  isSearchable?: boolean | null;
  path?: string | null;
  originalPath?: string | null;
  managedPath?: string | null;
  markdownPath?: string | null;
  openableKind?: 'original_file' | 'machine_markdown' | 'system_card' | 'unknown' | string | null;
  sourceAvailability?: 'original_available' | 'machine_readable_only' | 'invalid_source' | 'unknown' | string | null;
  originalAvailable?: boolean | null;
  machineReadableAvailable?: boolean | null;
  openOriginalDisabledReason?: string | null;
  score?: number | null;
  coverage?: number | null;
  sectionLabel?: string | null;
  retrievalStage?: 'master_index' | 'surrogate' | 'raw_chunk' | 'state_pool' | null;
  isFallback?: boolean;
  matchedTerms: string[];
  citationRole?: 'direct_quote' | 'direct_support' | 'background' | string | null;
  citationPriority?: number | null;
  citationReason?: string | null;
}

export interface AiStructuredResponse {
  content: string;
  judgment: string;
  analysis: string;
  actions: string;
  timeline: string;
}

export type JudgmentQueryMode = 'registry_only' | 'hybrid' | 'evidence_based_synthesis';

export type EvidenceSupportMode =
  | 'none'
  | 'linked_state_evidence'
  | 'evidence_cards'
  | 'raw_doc_drilldown'
  | 'generic_retrieval_fallback';

export type WorkspaceAnswerIntent =
  | 'intro_profile'
  | 'business_profile'
  | 'strategy_profile'
  | 'project_intro'
  | 'meeting_summary'
  | 'next_actions'
  | 'official_judgment_registry'
  | 'evidence_question'
  | 'status_progress'
  | 'general';

export type RetrievalDecisionReason =
  | 'state_first_default'
  | 'document_drilldown_requested'
  | 'search_cache_requested'
  | 'intro_query_needs_evidence'
  | 'identity_query_needs_evidence'
  | 'project_intro_needs_evidence'
  | 'meeting_summary_needs_evidence'
  | 'next_actions_needs_evidence'
  | 'evidence_question_needs_evidence'
  | 'official_registry_requested'
  | 'status_progress_needs_hybrid_evidence'
  | 'default_hybrid_evidence'
  | 'state_pool_insufficient'
  | 'state_pool_empty';

export type PageContextPage =
  | 'client_workspace'
  | 'workspace_chat'
  | 'task_detail'
  | 'task_ai'
  | 'meeting_detail'
  | 'event_line_detail'
  | 'project_module_detail'
  | 'project_flow_detail'
  | 'mobile_consult'
  | 'topic_radar'
  | 'strategic_cockpit';

export type PageIntentType =
  | 'intro_profile'
  | 'business_profile'
  | 'strategy_profile'
  | 'project_intro'
  | 'meeting_summary'
  | 'next_actions'
  | 'official_judgment_registry'
  | 'evidence_question'
  | 'status_progress'
  | 'task_context'
  | 'task_next_action'
  | 'proposal_gap'
  | 'general';

export type AnswerLevel = 'official' | 'candidate' | 'evidence_based' | 'fallback' | 'insufficient';
export type ContextQualityLevel = 'none' | 'weak' | 'usable' | 'strong';

export interface PageIntent {
  rawPrompt: string;
  intent: PageIntentType;
  requiresOfficialJudgment: boolean;
  requiresRawEvidence: boolean;
  requiresNextActions: boolean;
  requiresIntroProfile: boolean;
  requiresTaskContext: boolean;
  routeReason: string;
}

export interface ContextQuality {
  stateObjectCount: number;
  approvedJudgmentCount: number;
  candidateJudgmentCount: number;
  evidenceCardCount: number;
  rawEvidenceCount: number;
  openQuestionCount: number;
  taskCount: number;
  meetingCount: number;
  contextQuality: ContextQualityLevel;
  canUseAnalysisFirst: boolean;
  mustFallbackToLegacy: boolean;
}

export type RouteMode =
  | 'registry_only'
  | 'raw_doc_drilldown'
  | 'meeting_evidence'
  | 'task_context'
  | 'state_first'
  | 'hybrid';

export interface RetrievalModelSettings {
  embeddingProvider: string;
  embeddingModel: string;
  embeddingDimension: number;
  embeddingMode: 'local' | 'doubao' | 'hash_fallback';
  embeddingProfile?: 'legacy_fastembed_256' | 'bge_small_native' | 'bge_m3_dense';
  embeddingProjection?: boolean;
  routerEnabled: boolean;
  routerProvider: 'rules' | 'local_semantic' | 'local_llm' | 'doubao';
  routerMode?: 'rules' | 'semantic_shadow' | 'semantic' | 'semantic_plus_llm';
  routerModel: string;
  routerConfidenceThreshold?: number;
  rerankEnabled: boolean;
  rerankProvider: 'rules' | 'bge_reranker' | 'reserved';
  rerankModel?: string;
  answerLayerEnabled?: boolean;
  dataCenterKernelEnabled?: boolean;
  chatKernelPrimaryEnabled?: boolean;
  chatKernelPrimaryClientAllowlist?: string[];
  qualityGateMode?: 'observe' | 'warn' | 'block';
  shadowMode: boolean;
  updatedAt: string;
}

export type RetrievalMode = 'state_only' | 'raw_only' | 'hybrid' | 'deferred';

export interface RouteDecision {
  intent: PageIntentType;
  routeMode: RouteMode;
  dataSources: string[];
  retrievalMode: RetrievalMode;
  judgmentQueryMode?: JudgmentQueryMode | null;
  evidenceSupportMode?: EvidenceSupportMode | null;
  shouldUseRawEvidence: boolean;
  shouldUseStatePool: boolean;
  shouldUseTaskContext: boolean;
  shouldUseMeetingContext: boolean;
  shouldCreateProposal: boolean;
  queryPlan: string[];
  embeddingProfile: string;
  rerankNeeded: boolean;
  answerLevelHint: 'auto' | AnswerLevel;
  confidence: number;
  routeReason: string;
  routerSource: 'rules' | 'local_semantic' | 'local_llm' | 'smart_router' | 'fallback';
  fallbackUsed: boolean;
}

export interface RetrievalTrace {
  routeDecision: RouteDecision;
  embeddingProvider: string;
  embeddingModel: string;
  embeddingDimension: number;
  embeddingSignature: string;
  vectorCollection?: string | null;
  lexicalHitCount: number;
  vectorHitCount: number;
  mergedHitCount: number;
  rerankHitCount: number;
  rawChunkHitCount: number;
  fallbackUsed: boolean;
  latencyMs: Record<string, number>;
}

export interface RetrievalHealthComponent {
  provider: string;
  model: string;
  dimension?: number | null;
  signature?: string | null;
  ready: boolean;
  error?: string | null;
}

export interface RetrievalHealth {
  embedding: RetrievalHealthComponent;
  router: RetrievalHealthComponent;
  rerank: {
    enabled?: boolean;
    provider?: string;
  };
  shadowMode: boolean;
}

export interface RetrievalShadowRun {
  id: string;
  clientId: string;
  page: string;
  prompt: string;
  baselineSummary: Record<string, unknown>;
  candidateSummary: Record<string, unknown>;
  overlapRate: number;
  candidateBetter: boolean;
  failureReason?: string | null;
  createdAt: string;
}

export interface RetrievalShadowSummary {
  total: number;
  candidateBetterRate: number;
  overlapRateAvg: number;
  latencyDeltaMsAvg: number;
  failures: number;
}

export interface DataCenterShadowRun {
  id: string;
  scopeType: string;
  scopeId: string;
  page: string;
  mode: string;
  prompt: string;
  baseline: Record<string, unknown>;
  candidate: Record<string, unknown>;
  routeDecision: Record<string, unknown>;
  retrievalTrace: Record<string, unknown>;
  answerPlan: Record<string, unknown>;
  answerQuality: Record<string, unknown>;
  actionSuggestion: Array<Record<string, unknown>>;
  overlapRate: number;
  candidateFailed: boolean;
  failureReason?: string | null;
  createdAt: string;
}

export interface DataCenterShadowSummary {
  total: number;
  answerQualityPassRate: number;
  directAnswerPassRate: number;
  evidenceListOnlyFailRate: number;
  candidateBetterRate: number;
  candidateBetterByGradeRate?: number;
  gradeDeltaAvg?: number;
  independentChainPassRate?: number;
  overlapRateAvg: number;
  failures: number;
}

export interface GenerationRuntimeState {
  clientId: string;
  answerIntent: string;
  provider?: string | null;
  model?: string | null;
  recentTotal: number;
  recentTimeouts: number;
  recentLocalFallbacks: number;
  recentSuccesses: number;
  stableFallbackActive: boolean;
  stableFallbackReason?: string | null;
  cooldownUntil?: string | null;
  updatedAt: string;
}

export interface GenerationRuntimeDecision {
  shouldAttemptLlm: boolean;
  shouldUseCompactFirst: boolean;
  shouldUseLocalOnly: boolean;
  shouldQueueLongAnswerRetry: boolean;
  shouldProbeAfterCooldown: boolean;
  reason: string;
  cooldownActive: boolean;
}

export interface DiagnosticsBucket {
  status: 'ok' | 'warning' | 'critical';
  details: Record<string, unknown>;
}

export interface WorkspaceChatDiagnostics {
  clientId: string;
  recentMessages: number;
  groundedFallbackRate: number;
  llmTimeoutRate: number;
  sourceIntegrityMatch?: boolean | null;
  runningBuildVersion?: string | null;
  expectedBuildVersion?: string | null;
  dominantLlmErrorKind?: string | null;
  fallbackTemplateUsedRate?: number;
  dataCenterPrimaryEnabledRate?: number;
  partialPreservedRate?: number;
  systemFailureRate?: number;
  stableFallbackActive: boolean;
  stableFallbackReason?: string | null;
  avgRetrievalMs: number;
  avgLlmMs: number;
  intentDistribution: Record<string, number>;
  materialQuality: {
    pptNoiseRatio: number;
    generatedDraftRatio: number;
    memoryAnswerRatio: number;
  };
  dataCenterQuality: {
    approvedJudgmentCount: number;
    candidateJudgmentCount: number;
    parseFailedDocuments: number;
    parseFailureBuckets?: Record<string, number>;
    lastParseRetryAt?: string | null;
    lastParseRetrySucceeded?: boolean | null;
    contextQuality: string;
  };
  breakdown: Record<string, DiagnosticsBucket>;
  rootCauseSummary: string[];
  recommendedFixes: string[];
  kernelP95Ms?: number;
  kernelSlowRunCount?: number;
  kernelSlowestStage?: string | null;
}

export interface WorkspaceAnswerFinalization {
  content: string;
  answerMode: 'grounded_answer' | 'grounded_fallback' | 'low_confidence_answer' | 'general_answer' | 'system_failure';
  failureReason?: string | null;
  fallbackPresentationMode?: FallbackPresentationMode | null;
  userVisibleQualityStatus: 'ready' | 'usable_with_boundary' | 'degraded' | 'needs_retry';
  shouldShowRetryBanner: boolean;
  qualityGrade: 'pass' | 'warn' | 'fail';
  internalGenerationStatus?: string;
  notes?: string[];
}

export interface WorkspaceAnswerPresentationSection {
  // P2.12 FREEZE(answer-section-titles): 当前回答卡 section 标题先冻结，
  // 避免前后端在验证期内继续扩张或改名。
  title: '直接回答' | '关键依据' | '边界与待确认' | '下一步建议' | string;
  content?: string;
  items?: string[];
}

export interface WorkspaceAnswerPresentation {
  sections: WorkspaceAnswerPresentationSection[];
}

export interface WorkspaceAnswerEvidenceChip {
  id: string;
  title: string;
  sourceType: string;
  sourceKind: string;
  excerpt: string;
  qualityLabel: 'high' | 'medium' | 'low' | 'noise';
  documentId?: string | null;
  path?: string | null;
}

export interface WorkspaceAnswerActionCard {
  actionType: 'create_proposal' | 'create_task' | 'request_evidence' | 'review_judgment' | 'refresh_context' | 'prepare_meeting';
  title: string;
  summary: string;
  riskLevel: 'low' | 'medium' | 'high';
  draftId?: string | null;
  proposalId?: string | null;
  enabled?: boolean;
  disabledReason?: string;
}

export interface WorkspaceAnswerExperience {
  status: 'ready' | 'usable_with_boundary' | 'degraded' | 'needs_retry';
  headline: string;
  directAnswer: string;
  keyPoints: string[];
  evidenceChips: WorkspaceAnswerEvidenceChip[];
  boundaryNotes: string[];
  nextActions: string[];
  actionCards: WorkspaceAnswerActionCard[];
  trustSignals: string[];
  userMessage: string;
}

export interface WorkspaceAnswerValueTopItem {
  key: string;
  count: number;
}

export interface WorkspaceAnswerValueDiagnostics {
  clientId: string;
  recentMessages: number;
  answerModeDistribution: Record<string, number>;
  fallbackReasonDistribution: Record<string, number>;
  fallbackPresentationModeDistribution: Record<string, number>;
  retryBannerWouldShowCount: number;
  retryBannerWouldShowRate: number;
  lowConfidenceCount: number;
  groundedFallbackCount: number;
  groundedAnswerCount: number;
  usableAnswerCount: number;
  usableAnswerRate: number;
  readyOrUsableCount: number;
  readyOrUsableRate: number;
  needsRetryCount: number;
  needsRetryRate: number;
  degradedCount: number;
  degradedRate: number;
  kernelPrimaryUsedCount: number;
  kernelPrimaryFallbackUsedCount: number;
  kernelPrimaryUsedRate: number;
  llmTimeoutCount: number;
  llmTimeoutRate: number;
  answerQualityPassCount: number;
  answerQualityFailCount: number;
  groundedAnswerPassRate: number;
  officialBoundaryViolationCount: number;
  candidateBoundaryViolationCount: number;
  avgSelectedEvidenceCount: number;
  evidenceSupportedCount: number;
  evidenceSupportedRate: number;
  businessSlotAnswerCount: number;
  businessSlotAnswerRate: number;
  strategySlotAnswerCount: number;
  strategySlotAnswerRate: number;
  answerTooShortCount: number;
  answerTooShortRate: number;
  answerTooTemplateLikeCount: number;
  answerTooTemplateLikeRate: number;
  topFailureReasons: WorkspaceAnswerValueTopItem[];
  recommendedFixes: string[];
  metricErrors: string[];
}

export interface WorkspaceAnswerValueReview {
  id: string;
  clientId: string;
  messageId: string;
  prompt: string;
  answerMode: string;
  userVisibleQualityStatus: 'ready' | 'usable_with_boundary' | 'degraded' | 'needs_retry';
  shouldShowRetryBanner: boolean;
  usableAnswer?: boolean | null;
  reviewerNote: string;
  manualBaselineMinutes?: number | null;
  dataCenterReviewMinutes?: number | null;
  savedMinutes?: number | null;
  createdAt: string;
}

export interface WorkspaceAnswerValueSummary {
  clientId: string;
  reviewCount: number;
  usableAnswerRate: number;
  retryBannerRate: number;
  averageManualBaselineMinutes: number;
  averageDataCenterReviewMinutes: number;
  estimatedTimeSavedRate: number;
  positiveReviewCount: number;
  negativeReviewCount: number;
  lastReviewedAt?: string | null;
  proposalCreatedFromAnswerCount?: number;
  executionTicketCreatedFromAnswerCount?: number;
  metricErrors: string[];
}

export interface WorkspaceValueValidationQuestion {
  id: string;
  prompt: string;
}

export interface WorkspaceValueValidationSessionSummary {
  sessionId: string;
  clientId: string;
  completed: number;
  usableAnswerRate: number;
  estimatedTimeSavedRate: number;
  retryBannerRate: number;
  proposalCreatedCount: number;
  executionTicketCreatedCount: number;
  verdict: 'pass' | 'hold' | 'fail';
}

export interface WorkspaceValueValidationSession {
  id: string;
  clientId: string;
  status: 'running' | 'completed' | 'failed';
  questionSet: WorkspaceValueValidationQuestion[];
  completedQuestionIds: string[];
  summary: WorkspaceValueValidationSessionSummary;
  createdAt: string;
  updatedAt: string;
}

export interface DataCenterArtifactStatusItem {
  key: string;
  label: string;
  path: string;
  exists: boolean;
  verdict: 'pass' | 'fail' | 'hold' | 'unknown';
  stale: boolean;
  generatedAt?: string | null;
  gitCommit?: string | null;
  backendBuildHash?: string | null;
  runtimeMode?: string | null;
  dataDir?: string | null;
  sourceRunId?: string | null;
  blockingIssues: string[];
}

export interface DataCenterArtifactStatus {
  generatedAt: string;
  overallPass: boolean;
  items: DataCenterArtifactStatusItem[];
}

export interface DataCenterSchemaStatus {
  generatedAt: string;
  ensuredTables: string[];
  missingTables: string[];
  errors: string[];
  permissionDiagnostics?: Record<string, number>;
}

export interface WorkspaceAnswerActionCardResult {
  messageId: string;
  actionType: string;
  status: 'created' | 'reused';
  summary: string;
  draftId?: string | null;
  proposalId?: string | null;
  taskId?: string | null;
  autoApproved?: boolean;
  autoExecuted?: boolean;
  taskDraft?: {
    clientId: string;
    title: string;
    description: string;
    priority: string;
    sourceType?: string;
    sourceId?: string;
  } | null;
}

export interface WorkspaceAnswerQualityFailure {
  id: string;
  clientId: string;
  messageId?: string | null;
  prompt: string;
  failureType:
    | 'retry_banner'
    | 'too_template_like'
    | 'no_evidence'
    | 'no_direct_answer'
    | 'boundary_violation'
    | 'kernel_not_used'
    | 'answer_too_short'
    | 'user_marked_not_usable';
  severity: 'low' | 'medium' | 'high';
  details: Record<string, unknown>;
  status: 'open' | 'resolved';
  createdAt: string;
  updatedAt: string;
}

export interface SourceIntegrityReport {
  runningBackendRoot: string;
  workspaceBackendRoot?: string | null;
  runningHash: string;
  workspaceHash?: string | null;
  match: boolean | null;
  warning?: string | null;
  buildVersion?: string | null;
  gitCommit?: string | null;
  runtimeMode?: 'packaged' | 'dev' | string | null;
  frontendBuildVersion?: string | null;
  frontendGitCommit?: string | null;
  workspaceBuildVersion?: string | null;
  workspaceGitCommit?: string | null;
}

export interface LlmHealthcheckResult {
  provider: string;
  model: string;
  success: boolean;
  latencyMs: number;
  error?: string | null;
  errorKind?: 'connect_timeout' | 'read_timeout' | 'ssl_handshake_timeout' | 'auth_error' | 'rate_limit' | 'unknown' | null;
}

export interface LlmProviderProbeResult {
  clientId?: string | null;
  prompt: string;
  generatedAt: string;
  results: LlmHealthcheckResult[];
}

export interface AnswerPolicy {
  canAnswer: boolean;
  answerLevel: AnswerLevel;
  mustDiscloseCandidateBoundary: boolean;
  mustUseRawEvidence: boolean;
  shouldCreateProposal: boolean;
  fallbackToLegacyRetrieval: boolean;
  reason: string;
}

export interface PageContextPack {
  page: PageContextPage;
  scopeType: string;
  scopeId: string;
  clientId?: string | null;
  intent: PageIntentType;
  officialJudgments: Array<Record<string, unknown>>;
  candidateJudgments: Array<Record<string, unknown>>;
  overlayJudgments: Array<Record<string, unknown>>;
  evidenceCards: Array<Record<string, unknown>>;
  rawEvidence: Array<Record<string, unknown>>;
  openQuestions: Array<Record<string, unknown>>;
  conflicts: Array<Record<string, unknown>>;
  themeClusters: Array<Record<string, unknown>>;
  relatedTasks: Array<Record<string, unknown>>;
  relatedMeetings: Array<Record<string, unknown>>;
  relatedDocuments: Array<Record<string, unknown>>;
  notebookSummary?: Record<string, unknown> | null;
  memoryFacts: string[];
  contextPack?: Record<string, unknown> | null;
  judgmentBundle?: Record<string, unknown> | null;
  resolutionTrace?: Record<string, unknown> | null;
  stateProjection?: Record<string, unknown> | null;
  missingContext: string[];
  boundaryNotes: string[];
  sourceSummary: Record<string, number>;
  answerPolicy: AnswerPolicy;
  retrievalPlan: Record<string, unknown>;
  quality: ContextQuality;
  routeDecision?: RouteDecision | null;
  retrievalTrace?: RetrievalTrace | null;
}

export interface AnswerPlan {
  intent: PageIntentType;
  answerShape:
    | 'open_answer'
    | 'direct_profile'
    | 'business_profile'
    | 'strategy_profile'
    | 'status_brief'
    | 'evidence_answer'
    | 'meeting_summary'
    | 'task_next_action'
    | 'official_registry'
    | 'candidate_judgment'
    | 'insufficient';
  requiredSections: string[];
  mustStartWithDirectAnswer: boolean;
  mustCiteEvidence: boolean;
  mustDiscloseBoundary: boolean;
  allowCandidateJudgment: boolean;
  maxEvidenceItems: number;
  maxAnswerChars: number;
  routeReason: string;
}

export interface AnswerMaterial {
  directAnswerSeed: string;
  keyFacts: string[];
  structuredPoints: string[];
  evidenceHighlights: EvidenceItem[];
  stateHighlights: string[];
  boundaryNotes: string[];
  missingContext: string[];
  nextActions: string[];
  sourceLabels: string[];
  businessProfile?: BusinessProfileSlots | null;
  strategyProfile?: StrategyProfileSlots | null;
}

export interface BusinessProfileSlots {
  businessModules: string[];
  serviceObjects: string[];
  productsOrPrograms: string[];
  deliveryModel: string[];
  evidenceRefs: string[];
  unknowns: string[];
}

export interface StrategyProfileSlots {
  strategicDirections: string[];
  keyActions: string[];
  timeBoundary: string;
  risks: string[];
  evidenceRefs: string[];
  unknowns: string[];
}

export interface EvidenceQualitySignal {
  isNoise: boolean;
  noiseReasons: string[];
  sourceKind:
    | 'raw_document'
    | 'meeting_note'
    | 'meeting_decision'
    | 'meeting_action'
    | 'meeting_risk'
    | 'task_attachment'
    | 'judgment'
    | 'topic_candidate'
    | 'generated_answer'
    | 'memory_answer'
    | 'ppt_visual'
    | 'ppt_master'
    | 'template_page'
    | 'short_excerpt'
    | 'unknown';
  qualityScore: number;
  demotionScore: number;
  freshnessScore: number;
  authorityHint: 'raw' | 'state' | 'candidate' | 'generated' | 'unknown';
}

export interface ActionSuggestion {
  id: string;
  actionType:
    | 'create_task'
    | 'create_proposal'
    | 'request_evidence'
    | 'refresh_context_pack'
    | 'confirm_candidate_judgment'
    | 'prepare_meeting'
    | 'record_handbook';
  title: string;
  summary: string;
  rationale: string;
  riskLevel: 'low' | 'medium' | 'high';
  requiresApproval: boolean;
  sourceRefs: string[];
  targetRefs: ProposalTargetRef[];
}

export interface FactContradiction {
  id: string;
  clientId: string;
  subjectText: string;
  attribute: string;
  valueA: string;
  valueB: string;
  evidenceA: string;
  evidenceB: string;
  factAId: string;
  factBId: string;
  factAAt: string;
  factBAt: string;
  docAFileName?: string | null;
  docAImportedAt?: string | null;
  docAOriginalPath?: string | null;
  docASizeBytes?: number | null;
  docBFileName?: string | null;
  docBImportedAt?: string | null;
  docBOriginalPath?: string | null;
  docBSizeBytes?: number | null;
  contradictionType: 'value_diff' | 'temporal' | 'scope';
  severity: 'low' | 'medium' | 'high';
  reviewStatus: 'pending' | 'dismissed' | 'resolved';
  resolutionNote?: string | null;
  detectedAt: string;
}

export interface FactContradictionListResponse {
  contradictions: FactContradiction[];
  total: number;
}

export type EntityType =
  | 'person'
  | 'company'
  | 'project'
  | 'product'
  | 'competitor'
  | 'amount'
  | 'date';

export interface Entity {
  id: string;
  clientId: string;
  entityType: EntityType;
  normalizedName: string;
  displayName: string;
  aliases: string[];
  attributes: Record<string, string>;
  mentionCount: number;
  confidence: number;
  firstSeenAt: string;
  lastSeenAt: string;
  status: 'active' | 'merged' | 'deleted';
}

export interface EntityListResponse {
  entities: Entity[];
  total: number;
}

export interface EntityMergeCandidate {
  entityAId: string;
  entityBId: string;
  entityType: EntityType;
  nameA: string;
  nameB: string;
  mentionCountA: number;
  mentionCountB: number;
  similarity: number;
  reason: string;
}

export interface EntityMergeCandidatesResponse {
  candidates: EntityMergeCandidate[];
}

export interface EntityMergeResult {
  mentionsMoved: number;
  triplesMoved: number;
  factsMoved: number;
}

export interface GlossaryEntry {
  id: string;
  clientId: string;
  term: string;
  normalizedTerm: string;
  definition: string;
  aliases: string[];
  category: string;
  createdAt: string;
  updatedAt: string;
}

export interface GlossaryListResponse {
  entries: GlossaryEntry[];
  total: number;
}

export interface DataCenterSearchHit {
  title: string;
  excerpt: string;
  sourceType: string;
  documentId?: string | null;
  path?: string | null;
  originalPath?: string | null;
  managedPath?: string | null;
  markdownPath?: string | null;
  openableKind?: 'original_file' | 'machine_markdown' | 'system_card' | 'unknown' | string | null;
  sourceAvailability?: 'original_available' | 'machine_readable_only' | 'invalid_source' | 'unknown' | string | null;
  originalAvailable?: boolean | null;
  machineReadableAvailable?: boolean | null;
  openOriginalDisabledReason?: string | null;
  score?: number | null;
  sectionLabel?: string | null;
  retrievalStage?: string | null;
  selectedForAnswer: boolean;
  qualityFlags: string[];
  annotationId?: string | null;
  humanLabel?: 'useful' | 'noise' | 'needs_review' | null;
  freshnessScore?: number | null;
  createdAt?: string | null;
  docType?: string | null;
  versionNumber?: number | null;
  versionChainId?: string | null;
  lifecycleStatus?: 'current' | 'superseded' | 'deleted' | null;
  chainTotalVersions?: number | null;
  semanticType?:
    | 'fact'
    | 'judgment'
    | 'opinion'
    | 'action'
    | 'question'
    | 'conclusion'
    | 'background'
    | 'unclassified'
    | null;
  semanticConfidence?: number | null;
}

export interface DataCenterSearchResult {
  query: string;
  routeDecision: RouteDecision;
  retrievalTrace?: RetrievalTrace | null;
  answerPlan?: AnswerPlan | null;
  hits: DataCenterSearchHit[];
  selectedHits: DataCenterSearchHit[];
  missingContext: string[];
  suggestedFollowups: string[];
}

export interface DataCenterPrepSection {
  title: string;
  bullets: string[];
  evidenceRefs: string[];
}

export interface DataCenterPrepResult {
  prepType: 'task' | 'meeting' | 'client_conversation';
  title: string;
  objective: string;
  knownFacts: string[];
  keyRisks: string[];
  openQuestions: string[];
  recommendedAgenda: string[];
  nextActions: string[];
  materials: PrepPackMaterial[];
  sections: DataCenterPrepSection[];
  boundaryNotes: string[];
}

export interface DataCenterProposalDraft {
  id?: string | null;
  kind:
    | 'task_prep'
    | 'meeting_prep'
    | 'meeting_followup'
    | 'evidence_request'
    | 'judgment_review'
    | 'context_refresh';
  title: string;
  summary: string;
  rationale: string;
  riskLevel: 'low' | 'medium' | 'high';
  targetRefs: ProposalTargetRef[];
  sourceRefs: string[];
  boundaryNotes: string[];
  payload: Record<string, unknown>;
  requiresApproval: boolean;
  status?: 'draft' | 'reviewed' | 'rejected' | 'promoted' | 'expired';
  dedupeKey?: string | null;
  sourcePrompt?: string;
  scopeType?: string | null;
  scopeId?: string | null;
  clientId?: string | null;
  page?: string | null;
  mode?: string;
  reviewedAt?: string | null;
  rejectedAt?: string | null;
  rejectedReason?: string | null;
  promotedProposalId?: string | null;
  proposalStatus?: ProposalRecord['status'] | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface DataCenterProposalDraftPromoteResponse {
  draft: DataCenterProposalDraft;
  proposalId?: string | null;
  taskId?: string | null;
  refreshEventId?: string | null;
  effectType?:
    | 'proposal'
    | 'proposal_record'
    | 'task'
    | 'evidence_request'
    | 'meeting_prep'
    | 'judgment_confirmation'
    | 'context_refresh';
}

export interface ExternalEvidenceCard {
  id: string;
  sourceUrl: string;
  sourceDomain: string;
  sourceTier: 'official' | 'trusted_media' | 'partner' | 'unknown';
  title: string;
  publishedAt?: string | null;
  factExcerpt: string;
  summary: string;
  tags: string[];
  relatedScopeType: string;
  relatedScopeId: string;
  confidence: number;
  status: 'candidate' | 'accepted' | 'rejected';
  reviewedBy?: string | null;
  reviewedAt?: string | null;
  reviewNote?: string;
  linkedProposalIds?: string[];
  createdAt: string;
  updatedAt: string;
}

export interface KnowledgeParseFailure {
  documentId: string;
  title: string;
  path: string;
  kind: string;
  parseStatus: string;
  error: string;
  failureType: string;
  recoverable: boolean;
  pageCount?: number | null;
  lastRetryAt?: string | null;
  recommendedAction: string;
}

export interface DataCenterScope {
  page: PageContextPage;
  scopeType:
    | 'client'
    | 'task'
    | 'meeting'
    | 'event_line'
    | 'project_module'
    | 'project_flow'
    | 'topic'
    | 'strategic_cockpit'
    | 'system';
  scopeId: string;
  clientId?: string | null;
  taskId?: string | null;
  meetingId?: string | null;
  eventLineId?: string | null;
  projectModuleId?: string | null;
  projectFlowId?: string | null;
  topicId?: string | null;
}

export interface DataCenterRequest {
  scope: DataCenterScope;
  prompt?: string;
  mode?: 'answer' | 'page_context' | 'search' | 'prep' | 'proposal' | 'diagnostic';
  includeRawEvidence?: boolean;
  includeActionSuggestions?: boolean;
  shadow?: boolean;
  persistDrafts?: boolean;
  persistQuality?: boolean;
}

export interface AnswerQualityReport {
  hasDirectAnswer: boolean;
  evidenceListOnly: boolean;
  evidenceQuoteOnly: boolean;
  leakedInternalMarkers: string[];
  candidateAsOfficialRisk: boolean;
  officialBoundaryViolation?: boolean;
  missingRawEvidenceForIntent: boolean;
  offTopicRisk: boolean;
  factSlotHit?: boolean;
  factSlotMissingReason?: string | null;
  grade: 'pass' | 'warn' | 'fail';
  reason: string;
}

export interface DataCenterKernelResult {
  scope: DataCenterScope;
  pageContext?: PageContextPack | null;
  routeDecision?: RouteDecision | null;
  retrievalTrace?: RetrievalTrace | null;
  answerPlan?: AnswerPlan | null;
  answerMaterial?: AnswerMaterial | null;
  searchResult?: DataCenterSearchResult | null;
  prepResult?: DataCenterPrepResult | null;
  proposalDrafts: DataCenterProposalDraft[];
  persistedProposalDraftIds: string[];
  dedupedDraftIds: string[];
  actionSuggestions: ActionSuggestion[];
  quality?: ContextQuality | null;
  debug: Record<string, unknown>;
}

export interface KnowledgeParseFailureRetryItem {
  documentId: string;
  title: string;
  status: 'succeeded' | 'failed' | 'skipped';
  failureType?:
    | 'file_missing'
    | 'empty_text'
    | 'empty_pdf'
    | 'unsupported_format'
    | 'ocr_required'
    | 'parser_exception'
    | 'permission_denied'
    | 'managed_path_missing'
    | 'unknown'
    | null;
  message: string;
}

export interface KnowledgeParseFailureRetryResult {
  batchId: string;
  attempted: number;
  succeeded: number;
  failed: number;
  skipped: number;
  failureBuckets: Record<string, number>;
  items: KnowledgeParseFailureRetryItem[];
}

export type WorkspaceDataCenterReadinessActionType =
  | 'retry_parse'
  | 'rebuild_client_knowledge'
  | 'regenerate_document_cards'
  | 'sync_master_index'
  | 'sync_vector_index'
  | 'refresh_context_pack'
  | 'inspect_failed_documents'
  | 'cleanup_invalid_documents'
  | 'rebind_original_file'
  | 'auto_repair_documents'
  | 'enqueue_local_model_optimization'
  | 'retry_local_model_optimization'
  | 'internet_enrichment';

export interface WorkspaceDocumentProcessingStatus {
  documentId: string;
  v2DocumentId?: string | null;
  knowledgeDocumentId?: string | null;
  title: string;
  fileName: string;
  kind: string;
  materialLayer: string;
  parseStatus: string;
  parseError?: string | null;
  parseErrorCategory?:
    | 'file_missing'
    | 'permission_denied'
    | 'unsupported_format'
    | 'ocr_required'
    | 'empty_text'
    | 'empty_pdf'
    | 'parser_exception'
    | 'unknown'
    | null;
  hasDocumentCard: boolean;
  hasSurrogate: boolean;
  hasMasterIndex: boolean;
  vectorStatus?: string | null;
  chunkCount: number;
  sectionCount: number;
  usedByLatestContextPack: boolean;
  lastHitAt?: string | null;
  updatedAt: string;
  sourceAvailability: 'original_available' | 'machine_readable_only' | 'invalid_source' | 'unknown' | string;
  originalAvailable: boolean;
  machineReadableAvailable: boolean;
  openOriginalDisabledReason?: string | null;
}

export interface WorkspaceDataCenterReadinessSummary {
  totalDocuments: number;
  readyDocuments: number;
  partialReadyDocuments: number;
  parsingDocuments: number;
  queuedDocuments: number;
  runningDocuments: number;
  failedDocuments: number;
  invalidDocuments: number;
  sourceMissingDocuments: number;
  placeholderOnlyDocuments: number;
  autoRepairableDocuments: number;
  zeroByteDocuments: number;
  legacyFolderDocumentsWithoutV2: number;
  machineReadableOnlyDocuments: number;
  dedupeCandidateDocuments: number;
  orphanTaskCount: number;
  orphanEventLineCount: number;
  skippedOrphanClientIngestCount: number;
  parseFailureBuckets: Record<string, number>;
  ocrRecoverableCount: number;
  documentCards: number;
  surrogates: number;
  masterIndexEntries: number;
  vectorReadyDocuments: number;
  vectorStatus: string;
  vectorMasterIndexed: number;
  vectorChunkIndexed: number;
  latestContextPackAt?: string | null;
  contextQuality: string;
  missingContextCount: number;
  refreshEventQueuedCount: number;
  refreshEventRunningCount: number;
  refreshEventFailedCount: number;
  internetEnrichmentStatus: string;
  internetSourceCount: number;
  internetFactCardCount: number;
  remainingUserRequiredGaps: string[];
  lastInternetEnrichmentAt?: string | null;
}

export interface WorkspaceDataCenterReadinessJobEvent {
  jobId: string;
  level: string;
  message: string;
  createdAt: string;
}

export interface WorkspaceDataCenterLocalOptimizationStatus {
  enabled: boolean;
  paused: boolean;
  inWindow: boolean;
  nextWindowLabel?: string | null;
  modelProfileId: string;
  modelName: string;
  concurrency: number;
  queueTotal: number;
  queuedTasks: number;
  runningTasks: number;
  completedTasks: number;
  failedTasks: number;
  pendingDocumentCards: number;
  pendingPathOptimizations: number;
  appliedPathOptimizations: number;
  pendingPathConfirmations: number;
  lastCompletedAt?: string | null;
  lastError?: string | null;
}

export interface WorkspaceDataCenterReadinessJobs {
  runningKnowledgeJobs: number;
  failedKnowledgeJobs: number;
  latestJobEvents: WorkspaceDataCenterReadinessJobEvent[];
  localOptimization?: WorkspaceDataCenterLocalOptimizationStatus | null;
}

export interface WorkspaceDataCenterReadinessFix {
  id: string;
  label: string;
  actionType: WorkspaceDataCenterReadinessActionType;
  severity: 'info' | 'warning' | 'critical';
  reason: string;
  targetIds: string[];
  estimatedImpact: string;
}

export interface WorkspaceDataCenterReadinessRecentJob {
  id: string;
  jobType: string;
  status: string;
  processedItems: number;
  totalItems: number;
  lastError?: string | null;
  updatedAt: string;
}

export interface WorkspaceDataCenterReadiness {
  clientId: string;
  generatedAt: string;
  summary: WorkspaceDataCenterReadinessSummary;
  documents: WorkspaceDocumentProcessingStatus[];
  jobs: WorkspaceDataCenterReadinessJobs;
  recommendedFixes: WorkspaceDataCenterReadinessFix[];
  recentJobs: WorkspaceDataCenterReadinessRecentJob[];
  recentRefreshEvents: WorkspaceContextRefreshEvent[];
}

export interface WorkspaceDataCenterReadinessActionPayload {
  actionType: WorkspaceDataCenterReadinessActionType;
  targetIds?: string[];
  reason?: string;
  ocrMaxPages?: number;
  ocrBatchSize?: number;
  ocrContinueToEnd?: boolean;
  forceOcr?: boolean;
  seedUrls?: string[];
  seedQueries?: string[];
  gaps?: string[];
  maxPages?: number;
  maxDepth?: number;
  targetType?: string;
  targetId?: string | null;
  title?: string;
}

export interface WorkspaceDataCenterReadinessActionResult {
  actionType: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  jobId?: string | null;
  refreshEventId?: string | null;
  affectedCount: number;
  message: string;
  errors: string[];
}

export interface WorkspaceContextRefreshEvent {
  id: string;
  clientId: string;
  scopeType: string;
  scopeId: string;
  sourceType: string;
  sourceId?: string | null;
  reason: string;
  priority: 'low' | 'normal' | 'high';
  status: 'queued' | 'running' | 'completed' | 'failed' | 'canceled';
  jobId?: string | null;
  dedupeKey: string;
  error?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface WorkspaceContextRefreshEnqueuePayload {
  sourceType: string;
  sourceId?: string | null;
  reason: string;
  scopeType?: 'client' | 'task' | 'meeting' | 'event_line' | 'project_module' | 'project_flow' | 'strategic_cockpit';
  scopeId?: string | null;
  priority?: 'low' | 'normal' | 'high';
}

export interface WorkspaceContextRefreshEnqueueResult {
  event: WorkspaceContextRefreshEvent;
  deduped: boolean;
}

export interface WorkspaceProposalDraftCreatePayload {
  sourceMessageId?: string | null;
  sourceType?: 'action_suggestion' | 'proposal_draft' | 'manual';
  actionSuggestionId?: string | null;
  sourceMessageDraftId?: string | null;
  sourceMessageDraftPayload?: Record<string, unknown>;
  kind: DataCenterProposalDraft['kind'];
  title: string;
  summary: string;
  rationale?: string;
  riskLevel?: DataCenterProposalDraft['riskLevel'];
  targetRefs?: ProposalTargetRef[];
  sourceRefs?: string[];
  boundaryNotes?: string[];
  payload?: Record<string, unknown>;
  scopeType?: 'client' | 'task' | 'meeting' | 'event_line' | 'project_module' | 'project_flow' | 'strategic_cockpit';
  scopeId?: string | null;
}

export type FallbackPresentationMode = 'state_cards_only' | 'compact_user_answer' | 'full_answer';

export interface StateAnswerSections {
  official: string[];
  candidate: string[];
  draftFindings: string[];
  evidenceSupport: string[];
  actions: string[];
  risks: string[];
  unknowns: string[];
}

export interface StateSourceSummary {
  judgments: number;
  meetings: number;
  tasks: number;
  openQuestions: number;
  conflicts: number;
  documents: number;
}

export interface ChatMessage {
  id: string;
  threadId: string;
  role: 'user' | 'assistant';
  content: string;
  createdAt: string;
  status: 'success' | 'loading';
  modelRoute?: string | null;
  llmInvoked?: boolean;
  providerUsed?: string | null;
  answerMode?: 'grounded_answer' | 'grounded_fallback' | 'low_confidence_answer' | 'general_answer' | 'system_failure' | null;
  evidenceStatus?: 'sufficient' | 'partial' | 'none' | null;
  failureReason?: string | null;
  fallbackReason?: string | null;
  fallbackPresentationMode?: FallbackPresentationMode | null;
  stateConfidence?: 'low' | 'medium' | 'high' | null;
  stateSources?: string[];
  boundaryNotes?: string[];
  answerIntent?: WorkspaceAnswerIntent | null;
  retrievalDecisionReason?: RetrievalDecisionReason | null;
  judgmentQueryMode?: JudgmentQueryMode | null;
  evidenceSupportMode?: EvidenceSupportMode | null;
  stateAnswerSections?: StateAnswerSections | null;
  stateSourceSummary?: StateSourceSummary | null;
  timing?: Record<string, number>;
  retrievalSummary?: Record<string, unknown>;
  answerVariant?: 'standard' | 'compact' | 'long_retry' | 'manual_retry' | null;
  parentAssistantMessageId?: string | null;
  structuredData?: AiStructuredResponse | null;
  evidence: EvidenceItem[];
  deepThinkingRequested?: boolean;
  activeSkillId?: string | null;
  activeSkillIds?: string[];
  creativityMode?: CreativityMode | null;
}

/**
 * A question-specific analysis outline deliberately written for the user.
 * It is an auditable product artifact, not the provider's hidden reasoning
 * tokens or an internal chain-of-thought transcript.
 */
export interface PublicAnalysisPlan {
  narrative: string;
  intent: string;
  directions: string[];
  plannedSources: string[];
  cautions: string[];
}

export interface PublicAnalysisPlanResponse {
  plan: PublicAnalysisPlan;
  elapsedMs: number;
}

export type CreativityMode = 'creative' | 'balanced' | 'strict';

export interface WritingSkill {
  id: string;
  name: string;
  description: string;
  distilledMd: string;
  isBuiltin: boolean;
  sortOrder: number;
  createdAt: string;
  updatedAt: string;
}

export interface AgentSkill {
  skillId: string;
  shortName: string;
  description: string;
  instructions: string[];
  outputTemplate: string | null;
  allowedToolIds: string[];
  visibility: 'private' | 'organization' | 'department' | 'selected_members';
  departmentId: string | null;
  granteeMembershipIds: string[];
  /** @deprecated Sharing authority uses stable membership IDs. */
  granteePrincipalIds: string[];
  agentKinds: string[];
  version: number;
  enabled: boolean;
  publisherPrincipalId: string;
  publisherMembershipId: string;
  contentHash: string;
  capabilityBoundary: 'declarative_only';
  canManage: boolean;
  authorizationProjection: {
    state?: string;
    policyVersionId?: string;
    policyVersion?: number;
    viewerGrantId?: string;
    viewerCapabilities?: string[];
    canManage?: boolean;
  };
}

export interface WritingSkillDistillResult {
  distilledMd: string;
  samplesProcessed: number;
  suggestedName: string;
}

export interface ChatThread {
  id: string;
  clientId: string;
  title: string;
  createdAt: string;
  updatedAt: string;
}

export interface ChatStartResponse {
  threadId: string;
  userMessage: ChatMessage;
  assistantMessage: ChatMessage;
  analysisRun: ClientAnalysisRun;
  memoryUpdate?: {
    state: 'ready' | 'blocked' | 'failed_retryable';
    message: string;
    memoryId?: string | null;
    memoryKind: 'explicit_memory';
  } | null;
}

export interface AnswerFactCorrectionResult {
  clientId: string;
  answerId: string;
  factId: string;
  correctionKind: 'correction' | 'supplement' | 'remember';
  version: number;
  verificationState: 'verified' | string;
  localState: 'ready' | string;
  cloudState: 'ready' | string;
  overallState: 'ready' | string;
  contextInvalidated: boolean;
  canReanswer: boolean;
  originalQuestion: string;
  updatedAt: string;
  idempotentReplay?: boolean;
  retryable?: boolean;
  consumerPropagation?: {
    state: 'completed' | 'failed_retryable' | string;
    retryable: boolean;
    message: string;
    directConsumers: string[];
    pendingConsumers: string[];
    local?: Record<string, unknown> | null;
    cloud?: Record<string, unknown> | null;
  };
}

export interface ChatThreadDetailResponse {
  thread: ChatThread;
  messages: ChatMessage[];
}

export interface WorkspaceStateItem {
  id: string;
  signalType: 'change' | 'progress' | 'risk' | 'question' | 'judgment' | 'meeting' | 'task' | 'noise';
  sourceType: string;
  sourceId: string;
  title: string;
  summary: string;
  authority: 'approved' | 'candidate' | 'informational' | 'warning';
  updatedAt?: string | null;
}

export interface WorkspaceStateProjection {
  changeItems: WorkspaceStateItem[];
  progressItems: WorkspaceStateItem[];
  signalNoiseFlags: string[];
  boundaryNotes: string[];
  stateConfidence: 'low' | 'medium' | 'high';
}

export interface StateQueryPlan {
  primaryIntent: 'overview' | 'changes' | 'progress' | 'risk' | 'questions' | 'judgment' | 'timeline';
  focusAreas: string[];
  needsBoundaryGuard: boolean;
}

export interface StateQueryHit {
  sourceType: string;
  sourceId: string;
  label: string;
  summary: string;
  signalKind: 'change' | 'progress' | 'risk' | 'question' | 'judgment' | 'timeline';
  authorityLevel: 'approved' | 'candidate' | 'informational' | 'warning';
}

export interface StateAnswerContextPack {
  plan: StateQueryPlan;
  summary: string;
  stateSources: string[];
  boundaryNotes: string[];
  stateConfidence: 'low' | 'medium' | 'high';
  hits: StateQueryHit[];
  sections: StateAnswerSections;
  sourceSummary: StateSourceSummary;
  candidateLeakageCount: number;
  fallbackReason?: string | null;
}

export interface AgendaItem {
  id: string;
  title: string;
  description: string;
}

export interface DecisionItem {
  id: string;
  summary: string;
}

export interface RiskItem {
  id: string;
  summary: string;
  severity: Priority;
}

export interface AmbiguityItem {
  id: string;
  rawText: string;
  candidates: string[];
  status: 'pending' | 'resolved';
}

export interface MeetingSummary {
  id: string;
  clientId: string;
  title: string;
  stage: MeetingStage;
  scheduledAt?: string | null;
  updatedAt: string;
}

export interface MeetingDetail extends MeetingSummary {
  transcriptText: string;
  notes: string;
  agendaItems: AgendaItem[];
  decisions: DecisionItem[];
  actionItems: Task[];
  risks: RiskItem[];
  ambiguities: AmbiguityItem[];
}

export interface FeishuMeetingLaunchResult {
  meeting: MeetingDetail;
  deliveryStatus: 'sent' | 'skipped' | 'failed';
  deliveryMessage: string;
  commandHint: string;
  noticeText: string;
  deliveryMode: 'bound_user' | 'configured_receiver' | 'none';
  deliveryTarget?: string | null;
}

export interface TaskList {
  id: string;
  name: string;
  color: string;
  sortOrder: number;
  isDefault: boolean;
  scope?: 'org' | 'personal';
  archivedAt?: string | null;
}

export interface TaskTag {
  id: string;
  name: string;
  color: string;
  scope: 'org' | 'self';
  ownerUserId?: string | null;
  createdBy?: string | null;
  updatedAt: string;
  archivedAt?: string | null;
}

export interface Task {
  id: string;
  organizationId?: string | null;
  title: string;
  desc: string;
  status: TaskStatus;
  creatorId?: string | null;
  creatorName?: string | null;
  priority: Priority;
  listId: string;
  listName: string;
  listColor: string;
  ddl: string;
  startDate?: string | null;
  dueDate?: string | null;
  durationMinutes?: number;
  deadlineAt?: string | null;
  scheduledStartAt?: string | null;
  scheduledEndAt?: string | null;
  // 任务提醒(2026-05-29): 0=准时 / 5=提前5分钟 / null|undefined=不提醒。相对 scheduledStartAt(无则 deadlineAt)。
  reminderMinutesBefore?: number | null;
  completedAt?: string | null;
  scopeMode?: TaskScopeMode;
  clientId?: string | null;
  clientName?: string | null;
  eventLineId?: string | null;
  eventLineName?: string | null;
  planningCycleId?: string | null;
  projectModuleId?: string | null;
  projectModuleName?: string | null;
  projectFlowId?: string | null;
  projectFlowName?: string | null;
  ownerId?: string | null;
  ownerName: string;
  sourceType: string;
  sourceId?: string | null;
  businessCategory?: string | null;
  currentBlocker?: string | null;
  nextAction?: string | null;
  recentDecision?: string | null;
  evidenceCount: number;
  tags: TaskTag[];
  note?: string | null;
  attachments: TaskAttachment[];
  collaborators: TaskCollaborator[];
  collaborationSummary: Record<string, number>;
  viewerInboxStatus?: CollaboratorInboxStatus | null;
  viewerCollaborationRole?: 'owner' | 'collaborator' | null;
  orgContext?: TaskOrgContext | null;
  projectContext?: TaskProjectContext | null;
  memoryHints?: string[];
  backgroundReadiness?: BackgroundReadiness | null;
  linkedFactsPreview?: MemoryFact[];
  syncStatus?: 'local' | 'syncing' | 'synced' | 'pending' | 'error' | null;
  localMutationSeq?: number;
  syncError?: string | null;
  createdAt: string;
  updatedAt: string;
  version?: number;
}

export interface TaskAttachment {
  id: string;
  taskId: string;
  clientId: string;
  eventLineId?: string | null;
  documentId?: string | null;
  title: string;
  summary?: string | null;
  path: string;
  kind: string;
  source: string;
  sizeBytes: number;
  createdAt: string;
  isAudio?: boolean;
  processingStatus?:
    | 'not_requested'
    | 'queued'
    | 'processing'
    | 'ready'
    | 'failed'
    | 'failed_retryable'
    | 'blocked'
    | null;
  processingError?: string | null;
  processingProgress?: number | null;
  processingStage?: string | null;
  processingJobId?: string | null;
  transcriptAttachmentId?: string | null;
  transcriptDocumentId?: string | null;
  transcriptPath?: string | null;
}

export type TaskAttachmentRecord = TaskAttachment;

export interface TaskOrgContext {
  departmentId?: string | null;
  roleTemplateId?: string | null;
  controlRuleId?: string | null;
  controlLevel?: OrgTaskControlLevel | null;
  organizationFocusKey?: string | null;
  departmentFocusKey?: string | null;
  focusItemId?: string | null;
  departmentPlanItemId?: string | null;
  isCrossDepartment: boolean;
  approvalState?: string | null;
  blockedAtStep?: string | null;
  needsReview: boolean;
}

export interface TaskProjectContext {
  clientId: string;
  clientName: string;
  stage?: string | null;
  projectModuleId?: string | null;
  projectModuleName?: string | null;
  projectModuleSummary?: string | null;
  projectFlowId?: string | null;
  projectFlowName?: string | null;
  projectFlowSummary?: string | null;
  backgroundSummary: string;
  goalSummary: string;
  riskSummary: string;
  currentFocus?: string | null;
  currentBlocker?: string | null;
  nextAction?: string | null;
  recentProgress?: string | null;
  infoCompleteness: 'low' | 'medium' | 'high';
  sourceEvidence: string[];
}

export type EventLineKind = 'project_line' | 'issue_line' | 'coordination_line' | 'case_line' | 'custom';
export type EventLineStatus = 'active' | 'blocked' | 'paused' | 'done' | 'archived';
export type EventLineVisibilityScope = 'private' | 'department' | 'project_public';
export type EventLineReadinessLevel = 'incomplete' | 'general' | 'substantial';

export interface EventLineViewerCapabilities {
  canView: boolean;
  canContribute: boolean;
  canManageStructure: boolean;
  canAssignOwner: boolean;
  canArchive: boolean;
  canReparentProject: boolean;
  canAddParticipants: boolean;
  canManageParticipants: boolean;
  canSetMilestone: boolean;
}

export interface EventLine {
  id: string;
  name: string;
  kind: EventLineKind;
  status: EventLineStatus;
  visibilityScope: EventLineVisibilityScope;
  businessCategory?: string | null;
  stage?: string | null;
  summary?: string | null;
  intent?: string | null;
  summaryConfirmedAt?: string | null;
  summaryConfirmedByUserId?: string | null;
  intentConfirmedAt?: string | null;
  intentConfirmedByUserId?: string | null;
  currentBlocker?: string | null;
  recentDecision?: string | null;
  nextStep?: string | null;
  evidenceCount: number;
  taskCount?: number;
  attachmentCount?: number;
  activityCount?: number;
  ownerId?: string | null;
  ownerName?: string | null;
  createdByUserId?: string | null;
  createdByName?: string | null;
  primaryClientId?: string | null;
  primaryClientName?: string | null;
  primaryDepartmentId?: string | null;
  primaryDepartmentName?: string | null;
  participantIds: string[];
  materialRequirements: string[];
  closedAt?: string | null;
  closedByUserId?: string | null;
  syncStatus?: 'local' | 'syncing' | 'synced' | 'pending' | 'error' | null;
  cloudId?: string | null;
  pendingSyncAction?: 'create' | 'update' | 'archive' | null;
  lastSyncError?: string | null;
  completenessScore?: number;
  completenessStatus?: 'insufficient' | 'summary_ready' | 'forecast_ready' | 'high_confidence';
  completenessMissingSlots?: string[];
  readinessLevel: EventLineReadinessLevel;
  readinessMissingItems: string[];
  version: number;
  viewerCapabilities: EventLineViewerCapabilities;
  createdAt: string;
  updatedAt: string;
}

export interface EventLineReparentPreview {
  eventLineId: string;
  eventLineName: string;
  currentClientId?: string | null;
  currentClientName?: string | null;
  targetClientId: string;
  targetClientName: string;
  taskCount: number;
  taskAttachmentCount: number;
  eventLineAttachmentCount: number;
  reportCount: number;
}

export interface EventLineMergePreviewItem {
  table: string;
  rows: number;
}

export interface EventLineMergePreview {
  targetId: string;
  targetName: string;
  sources: Array<{ id: string; name: string; status: string }>;
  impact: EventLineMergePreviewItem[];
  totalRows: number;
}

export interface EventLineActivity {
  id: string;
  eventLineId: string;
  sourceType: 'task_activity' | 'meeting' | 'meeting_minute' | 'support_request' | 'review' | 'attachment' | 'manual_note' | 'merge' | 'document_ingest' | 'atomic_fact';
  sourceId: string;
  happenedAt: string;
  actorId?: string | null;
  actorName?: string | null;
  title: string;
  summary: string;
  metadata?: Record<string, unknown>;
  isKey?: boolean;
  keySource?: '' | 'legacy_system' | 'human';
  keyUpdatedByUserId?: string | null;
  keyUpdatedAt?: string | null;
  associationStatus?: 'confirmed' | 'historical_suggestion';
  includeInNarrative?: boolean;
}

export interface EventLineDetail {
  eventLine: EventLine;
  tasks: Task[];
  activities: EventLineActivity[];
  memorySnapshot?: EventLineMemorySnapshot | null;
  predictionReadiness?: number | null;
  clarificationNeeds?: string[];
}

export interface TaskSmartBriefActionItem {
  text: string;
  sourceLabel: string;
  internalSuggestedOwner?: string;
  actionKind?: string;
  dueHint?: string;
  deliverable?: string;
  actionKey?: string;
  taskTitleSuggestion?: string;
  taskDescriptionSuggestion?: string;
}

export interface TaskSmartBrief {
  taskId: string;
  summary: string;
  summarySourceLabels: string[];
  actionItems: TaskSmartBriefActionItem[];
}

export interface TaskContextBrief {
  id?: string | null;
  taskId: string;
  clientId?: string | null;
  eventLineId?: string | null;
  brief: string;
  shouldDisplay: boolean;
  materialPackHash: string;
  usedProjectSignals: string[];
  materialBoundary:
    | string
    | {
        sourceFileContentIncluded?: boolean;
        sourceFilePathsIncluded?: boolean;
        storageLocatorsIncluded?: boolean;
        unpublishedDocumentContentIncluded?: boolean;
        localPrivateSource?: boolean;
        localPrivateUploadedToOrganizationCloud?: boolean;
        localSourcePathsIncludedInContext?: boolean;
      };
  qualityFlags: string[];
  generationModel: string;
  generationPromptVersion: string;
  updatedAt: string;
}

export interface MeetingContextBrief extends Omit<TaskContextBrief, 'taskId'> {
  meetingId: string;
}

export interface PrepPackMaterial {
  sourceType: string;
  sourceId: string;
  title: string;
  summary: string;
  authorityLevel?: string;
}

export interface PrepPackCard {
  taskId: string;
  title: string;
  summary: string;
  materials: PrepPackMaterial[];
  openQuestions: string[];
  judgments: string[];
  risks: string[];
  boundaryNotes: string[];
  sourceLabels: string[];
  proposalId?: string | null;
}

export interface ProposalTargetRef {
  targetType: 'client' | 'task' | 'meeting' | 'event_line' | 'judgment';
  targetId: string;
  label: string;
}

export interface ProposalRecord {
  id: string;
  clientId: string;
  kind:
    | 'task_prep'
    | 'meeting_prep'
    | 'meeting_followup'
    | 'evidence_request'
    | 'judgment_review'
    | 'context_refresh';
  status: 'draft' | 'pending_review' | 'approved' | 'rejected' | 'execution_pending' | 'executed' | 'failed';
  riskLevel: 'low' | 'medium' | 'high';
  title: string;
  summary: string;
  rationale: string;
  targetRefs: ProposalTargetRef[];
  sourceRefs: string[];
  boundaryNotes: string[];
  payload: Record<string, unknown>;
  createdBy: string;
  decidedBy?: string | null;
  decidedAt?: string | null;
  rejectedReason?: string | null;
  executionTicketId?: string | null;
  executionTicket?: ExecutionTicket | null;
  createdAt: string;
  updatedAt: string;
}

export type ExecutionTicketResultType = 'recorded_only' | 'prep_artifact_ready' | 'followup_task_created' | 'failed';

export interface ExecutionTicketArtifactRef {
  artifactType: string;
  refId: string;
  title: string;
}

export interface ExecutionTicketResult {
  resultType: ExecutionTicketResultType;
  summary: string;
  createdTaskIds: string[];
  artifactRefs: ExecutionTicketArtifactRef[];
}

export interface ExecutionTicket {
  id: string;
  proposalId: string;
  clientId: string;
  executionType: string;
  status: 'pending' | 'running' | 'executed' | 'failed';
  payload: Record<string, unknown>;
  result: ExecutionTicketResult;
  idempotencyKey?: string | null;
  retryCount?: number;
  maxRetries?: number;
  lastError?: string | null;
  lastAttemptAt?: string | null;
  errorMessage?: string | null;
  executedAt?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ExecutionTicketLog {
  id: string;
  ticketId: string;
  stage: 'validate' | 'prepare_payload' | 'execute_action' | 'write_result' | 'update_proposal_status' | 'retry';
  status: 'started' | 'success' | 'failed';
  message: string;
  payload: Record<string, unknown>;
  createdAt: string;
}

export interface ProposalExecutionResponse {
  proposal: ProposalRecord;
  executionTicket?: ExecutionTicket | null;
}

export interface ProposalApprovalPayload {
  decidedBy?: string;
  note?: string;
  comment?: string;
}

export interface ProposalExecutionPayload {
  requestedBy?: string;
  dryRun?: boolean;
}

export interface ProposalExecutionPreview {
  proposalId: string;
  executionType: string;
  riskLevel: 'low' | 'medium' | 'high';
  willCreateTask: boolean;
  willCreatePrepArtifact: boolean;
  willCreateEvidenceRequest: boolean;
  willUpdateEventLine: boolean;
  summary: string;
  warnings: string[];
}

export interface ProposalApprovalResult {
  proposal: ProposalRecord;
  executionPreview?: ProposalExecutionPreview | null;
}

export interface ProposalExecutionResult {
  proposal: ProposalRecord;
  executionTicket?: ExecutionTicket | null;
}

export interface ProposalBatchActionPayload {
  proposalIds: string[];
  decidedBy?: string;
  note?: string;
}

export interface ProposalBatchResult {
  total: number;
  succeeded: number;
  failed: number;
  failedIds: string[];
}

export type KernelPrimaryRolloutStage = 'stage_1_client' | 'stage_3_clients' | 'stage_10_clients';
export type KernelPrimaryRolloutStatus = 'planned' | 'running' | 'completed' | 'rolled_back' | 'failed';

export interface KernelPrimaryRolloutRun {
  id: string;
  stage: KernelPrimaryRolloutStage;
  clientIds: string[];
  status: KernelPrimaryRolloutStatus;
  metricsBefore: Record<string, unknown>;
  metricsAfter: Record<string, unknown>;
  verdict?: 'pass' | 'fail' | 'watch' | null;
  recommendedAction?: 'keep' | 'rollback' | null;
  note: string;
  rollbackReason?: string | null;
  startedAt?: string | null;
  completedAt?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface KernelPrimaryRolloutStartPayload {
  stage: KernelPrimaryRolloutStage;
  clientIds: string[];
  note?: string;
}

export interface KernelPrimaryRolloutRollbackPayload {
  reason?: string;
}

export interface ExecutionRetryMetricsTopItem {
  key: string;
  count: number;
}

export interface ExecutionRetryMetricsAlert {
  level: 'info' | 'warning' | 'critical';
  message: string;
}

export interface ExecutionRetryMetrics {
  windowDays: number;
  totalTickets: number;
  failedTickets: number;
  retriedTickets: number;
  retryExhaustedTickets: number;
  retrySuccessRate?: number;
  avgRetryCount?: number;
  oldestFailedTicketAgeHours?: number;
  failureReasonTopN: ExecutionRetryMetricsTopItem[];
  failedStageTopN: ExecutionRetryMetricsTopItem[];
  alerts: ExecutionRetryMetricsAlert[];
}

export interface EvidenceQualityFeedbackSnapshot {
  id: string;
  windowStart: string;
  windowEnd: string;
  labelCounts: Record<string, number>;
  usefulExamples: Array<Record<string, unknown>>;
  noiseExamples: Array<Record<string, unknown>>;
  needsReviewExamples: Array<Record<string, unknown>>;
  recommendedRules: string[];
  createdAt: string;
}

export interface RollbackDrillPayload {
  clientIds?: string[];
  dryRun?: boolean;
}

export interface RollbackDrillResult {
  dryRun: boolean;
  wouldDisableWorkspacePrimary: boolean;
  wouldDisableChatKernelPrimary: boolean;
  wouldClearAllowlist: boolean;
  wouldKeepDrafts: boolean;
  wouldKeepExecutionTickets: boolean;
  wouldKeepEvidenceLabels?: boolean;
  warnings: string[];
  affectedClientIds: string[];
  applied: boolean;
}

export interface DataCenterOperationalStatus {
  fullRegressionVerdict?: 'pass' | 'fail' | 'hold' | 'unknown';
  p22StrictPass?: boolean;
  p23StrictPass?: boolean;
  rolloutStage?: string;
  rolloutLatestVerdict?: string;
  retryAlerts?: string[];
  latestSnapshotAt?: string | null;
  rollbackDrillPass?: boolean;
  releaseReportVerdict?: 'pass' | 'fail' | 'hold' | 'unknown';
  blockingIssues?: string[];
}

export interface MobileDataCenterSnapshotSummary {
  clientId: string;
  latestContextPack?: Record<string, unknown> | null;
  latestJudgments: Array<Record<string, unknown>>;
  openQuestions: Array<Record<string, unknown>>;
  conflicts: Array<Record<string, unknown>>;
  relatedTasks: Array<Record<string, unknown>>;
  recentMeetings: Array<Record<string, unknown>>;
  stateProjection?: Record<string, unknown> | null;
  proposalDraftSummary?: Record<string, number>;
  openProposalSummary?: Record<string, number>;
  latestExecutionTickets?: ExecutionTicket[];
  evidenceQualitySummary?: Record<string, number>;
  kernelReadiness?: 'ready' | 'partial' | 'weak';
  generatedAt: string;
}

export interface EvidenceQualityAnnotation {
  id: string;
  sourceType: string;
  sourceId: string;
  documentId?: string | null;
  path?: string | null;
  excerptHash: string;
  sourceKind: EvidenceQualitySignal['sourceKind'];
  qualityScore: number;
  demotionScore: number;
  noiseReasons: string[];
  authorityHint: EvidenceQualitySignal['authorityHint'];
  humanLabel?: 'useful' | 'noise' | 'needs_review' | null;
  humanNote: string;
  createdAt: string;
  updatedAt: string;
}

export interface EventLineNarrativeNode {
  id: string;
  time: string;
  title: string;
  narrative: string;
  confidence: 'high' | 'medium' | 'low' | string;
  linkedTaskIds: string[];
  linkedActivityIds: string[];
  linkedAttachmentIds: string[];
  evidenceSummary?: string;
  evidenceGaps?: string[];
}

export interface EventLineTimelineNarrative {
  eventLineId: string;
  rev: number;
  headline: string;
  opening: string;
  closing: string;
  nodes: EventLineNarrativeNode[];
  overallConfidence: number;
  generator: string;
  modelName: string;
  updatedAt: string;
  triggeredByDisplayName?: string;
  outputKind?: 'legacy_mainline' | 'material_overview' | 'formal_mainline';
  sourceSetId?: string | null;
  eventLineVersion?: number;
  milestoneTaskIds?: string[];
  isStale?: boolean;
  formalReady?: boolean;
  missingRequirements?: string[];
  availabilityStatus?: 'ready' | 'stale' | 'blocked';
  availabilityReason?: string;
  staleReasons?: string[];
}

export interface EventLineDraft {
  draft: string;
  citations: Array<{ id: string; type: string; title: string }>;
  warning?: string | null;
}

export interface EventLineReadinessAnalysis {
  summary: string;
  findings: Array<{
    title: string;
    reason: string;
    suggestion: string;
  }>;
  deterministicMissingItems: string[];
  analyzedAt: string;
}

export interface EventLineTaskCandidate {
  id: string;
  title: string;
  description: string;
  clientId?: string | null;
  clientName?: string | null;
  eventLineId?: string | null;
  eventLineName?: string | null;
  progressStatus: string;
  updatedAt: string;
}

export interface EventLineReportAttachment {
  id: string;
  taskId: string;
  documentId?: string | null;
  sourceKind?: 'task_attachment' | 'event_line_attachment' | 'meeting_attachment' | 'calendar_attachment' | null;
  title: string;
  fileName?: string | null;
  kind: string;
  mimeType?: string | null;
  sizeBytes: number;
  downloadUrl: string;
  openUrl?: string | null;
  actorName?: string | null;
  purpose?: string | null;
  createdAt: string;
  parseStatus?: 'ready' | 'pending' | 'missing_document' | 'missing_source' | 'unsupported' | string | null;
  parseError?: string | null;
  parseJobStatus?: string | null;
  parseUpdatedAt?: string | null;
  parsedPreview?: string;
  chunkCount?: number;
  sectionCount?: number;
}

export type EventLineTimelineNodeKind =
  | 'project_start'
  | 'material_intake'
  | 'project_review'
  | 'continuing_task'
  | 'admin_archive'
  | 'needs_review'
  | 'system_trace';

export type EventLineTimelineNodeWarning = string;

export interface EventLineTimelineNode {
  id: string;
  kind: EventLineTimelineNodeKind;
  title: string;
  time: string;
  timeRange?: { start?: string; end?: string };
  summary: string;
  sourceTaskIds?: string[];
  sourceTaskId?: string;
  sourceActivityIds: string[];
  attachments: EventLineReportAttachment[];
  materialCount?: number;
  includeInReport?: boolean;
  evidenceSummary: string;
  warnings: EventLineTimelineNodeWarning[];
  tags: string[];
  actorName?: string | null;
  ownerName?: string | null;
}

export interface EventLineReportSnapshot {
  eventLine: EventLine;
  activities: EventLineActivity[];
  tasks: Task[];
  attachments: EventLineReportAttachment[];
  timelineNodes?: EventLineTimelineNode[];
  participantNames: string[];
  snapshotAt: string;
  canEdit: boolean;
  sourceState: 'cloud_ready' | 'local_editable' | 'local_history' | 'sync_degraded';
  readOnlyReason?: string | null;
  taskMirrorStatus?: 'ready' | 'failed';
  taskMirrorError?: string | null;
}

/** 事件线文档附件 — 为 PDF 汇报功能预留 */
export type EventLineAttachmentDisplayMode = 'expanded' | 'collapsed';

export interface EventLineAttachment {
  id: string;
  eventLineId: string;
  fileName: string;
  fileType: string;
  displayMode: EventLineAttachmentDisplayMode;
  description: string;
  uploadedBy: string;
  uploadedAt: string;
  /** 本地文件路径（不同步到云端） */
  localPath?: string | null;
  /** 票据/图片预览 URL */
  previewUrl?: string | null;
  parseStatus?: 'uploaded' | 'queued' | 'parsing' | 'ready' | 'partial_ready' | 'failed' | 'missing_source';
  parseError?: string | null;
  parseJobId?: string | null;
  parseUpdatedAt?: string | null;
}

/** 事件线审批节点 */
export type EventLineApprovalStatus = 'pending' | 'approved' | 'rejected';

export interface EventLineApprovalNode {
  id: string;
  eventLineId: string;
  title: string;
  requestedBy: string;
  approverName: string;
  status: EventLineApprovalStatus;
  note: string;
  createdAt: string;
  resolvedAt?: string | null;
}

export interface EventLineMutationPayload {
  expectedVersion?: number;
  id?: string | null;
  name: string;
  kind?: EventLineKind;
  status?: EventLineStatus;
  visibilityScope?: EventLineVisibilityScope;
  businessCategory?: string | null;
  stage?: string | null;
  summary?: string | null;
  intent?: string | null;
  confirmSummary?: boolean;
  confirmIntent?: boolean;
  currentBlocker?: string | null;
  recentDecision?: string | null;
  nextStep?: string | null;
  evidenceCount?: number | null;
  ownerId?: string | null;
  primaryClientId: string;
  primaryDepartmentId?: string | null;
  participantIds?: string[];
  materialRequirements?: string[];
  syncLinkedTaskClientIds?: boolean;
}

export interface EventLineClarificationDraftPayload {
  conversationText: string;
}

export interface EventLineClarificationDraftResult {
  summary: string;
  stage: string;
  intent: string;
  currentBlocker: string;
  nextStep: string;
  recentDecision: string;
  missingInfo: string[];
  confidence: 'low' | 'medium' | 'high';
}

export interface ProjectModule {
  id: string;
  clientId: string;
  name: string;
  alias?: string | null;
  goal: string;
  description: string;
  ownerName?: string | null;
  deliverables: string[];
  keywords: string[];
  templateTasksJson?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ProjectFlow {
  id: string;
  clientId: string;
  moduleId: string;
  moduleName?: string | null;
  name: string;
  description: string;
  scenario: string;
  triggerCondition: string;
  steps: string[];
  inputs: string[];
  outputs: string[];
  collaborators: string[];
  riskPoints: string[];
  createdAt: string;
  updatedAt: string;
}

export interface ProjectStructureResponse {
  modules: ProjectModule[];
  flows: ProjectFlow[];
}

export interface ProjectModuleDetail extends ProjectModule {
  relatedTaskIds: string[];
  relatedTaskTitles: string[];
  flowIds: string[];
  flowNames: string[];
  contextSummary: string;
}

export interface ProjectFlowDetail extends ProjectFlow {
  relatedTaskIds: string[];
  relatedTaskTitles: string[];
  contextSummary: string;
}

export interface TaskCollaborator {
  userId: string;
  fullName: string;
  email: string;
  orderIndex: number;
  isOwner: boolean;
  assignmentState?: 'assigned' | 'awaiting_owner' | 'returned' | string;
  inboxStatus: CollaboratorInboxStatus;
  returnReason?: string | null;
  handledAt?: string | null;
  version?: number;
}

export interface TaskInboxBatchSkippedItem {
  taskId: string;
  reason: string;
}

export interface TaskInboxBatchHandleResult {
  acceptedIds: string[];
  acknowledgedIds: string[];
  skippedItems: TaskInboxBatchSkippedItem[];
}

export interface TaskReturnResult {
  task: Task;
  collaborator: Record<string, unknown>;
  restoredOwnerCollaborator?: Record<string, unknown> | null;
  notificationResult: {
    state: 'not_connected' | 'failed_retryable' | 'delivered';
    deliveryCount: number;
    partialSuccess: boolean;
    message?: string;
  };
  projection?: Record<string, unknown>;
}

export interface TaskActivityRecord {
  id: string;
  taskId: string;
  actorId: string;
  actorName: string;
  eventType: string;
  payload: Record<string, unknown>;
  createdAt: string;
}

export interface WeeklyReview {
  id: string;
  userId: string;
  userName: string;
  weekLabel: string;
  workProgress?: string;
  workBlocker?: string;
  blockerType?: string;
  workDirection?: string;
  nextWeekFocus?: string;
  supportNeeded?: string;
  relatedPlanIds?: string[];
  workFreeNote?: string;
  personalGrowthNote?: string;
  personalPrivateNote?: string;
  personalVisibility?: 'self';
  submittedAt: string;
  createdAt: string;
  updatedAt: string;
}

export interface AgentWorklog {
  id: string;
  agentKey: AgentDepartmentKey;
  agentName: string;
  departmentName: string;
  color: string;
  date: string;
  weekLabel: string;
  title: string;
  summary: string;
  detailLines: string[];
  sourceType: 'activity_log' | 'topic_capture' | 'workspace_sync';
  createdAt: string;
}

export interface AgentWeeklyDigest {
  agentKey: AgentDepartmentKey;
  agentName: string;
  departmentName: string;
  color: string;
  weekLabel: string;
  summary: string;
  focusItems: string[];
  evidenceCount: number;
  sourcePolicy: Record<string, unknown>;
}

export interface AgentWeeklyPlanItem {
  id: string;
  title: string;
  rationale: string;
  scheduleHint: string;
  status: AgentPlanStatus;
}

export interface AgentWeeklyPlan {
  agentKey: AgentDepartmentKey;
  agentName: string;
  departmentName: string;
  color: string;
  weekLabel: string;
  summary: string;
  planItems: AgentWeeklyPlanItem[];
  sourcePolicy: Record<string, unknown>;
}

export interface AgentWorklogResponse {
  month: string;
  worklogs: AgentWorklog[];
  weeklyDigests: AgentWeeklyDigest[];
  weeklyPlans: AgentWeeklyPlan[];
}

export interface AgentWeeklyPlanItemPayload {
  title: string;
  rationale: string;
  scheduleHint: string;
  status: AgentPlanStatus;
}

export interface AgentWeeklyPlanPayload {
  weekLabel: string;
  agentKey: AgentDepartmentKey;
  summary: string;
  planItems: AgentWeeklyPlanItemPayload[];
}

export interface WeeklyReviewTaskSnapshot {
  title: string;
  status: TaskStatus;
  startDate?: string | null;
  dueDate?: string | null;
  deadlineAt?: string | null;
  scheduledStartAt?: string | null;
  scheduledEndAt?: string | null;
  completedAt?: string | null;
  createdAt: string;
  ownerId?: string | null;
  ownerName?: string | null;
  clientId?: string | null;
  clientName?: string | null;
  eventLineId?: string | null;
  eventLineName?: string | null;
  tags: TaskTag[];
  listName: string;
  listColor: string;
  orgContext?: TaskOrgContext | null;
  projectContext?: TaskProjectContext | null;
  eventLineContext?: WeeklyReviewEventLineContext | null;
}

export interface WeeklyReviewEventLineContext {
  id?: string | null;
  name?: string | null;
  businessCategory?: string | null;
  stage?: string | null;
  summary?: string | null;
  intent?: string | null;
  currentBlocker?: string | null;
  recentDecision?: string | null;
  nextStep?: string | null;
  evidenceCount: number;
  primaryClientId?: string | null;
  primaryClientName?: string | null;
  primaryDepartmentId?: string | null;
  primaryDepartmentName?: string | null;
}

export interface WeeklyReviewTaskStructuredNote {
  reflection: string;
  lightweightTag: ReviewLightweightTag;
  planCommitment: string;
  progress: string;
  completionStatus: ReviewCompletionStatus;
  departmentPlanId?: string | null;
  departmentPlanAlignment: ReviewAlignmentStatus;
  organizationPlanId?: string | null;
  organizationPlanAlignment: ReviewAlignmentStatus;
  successReason: string;
  successExperience: string;
  blockerReason: string;
  failureInsight: string;
  supportNeeded: string;
  nextAction: string;
}

export interface ReviewMetricCard {
  key: 'timely_completion' | 'department_alignment' | 'strategy_alignment' | 'reflection_capture';
  label: string;
  valueText: string;
  numerator: number;
  denominator: number;
  rate: number;
  description: string;
  tone: 'positive' | 'neutral' | 'warning' | 'risk';
}

export interface WeeklyReviewTaskEntry {
  id: string;
  reviewId?: string | null;
  taskId: string;
  weekLabel: string;
  contentDomain: 'work' | 'personal';
  note: string;
  structuredNote: WeeklyReviewTaskStructuredNote;
  reviewedAt?: string | null;
  taskSnapshot: WeeklyReviewTaskSnapshot;
}

export interface ReviewEvidenceWeight {
  sourceType: 'user_note' | 'task_fact' | 'organization_dna' | 'team_plan' | 'focus_plan' | 'project_context' | 'external_context';
  label: string;
  weight: 'high' | 'medium' | 'low';
  rationale: string;
}

export interface ReviewHypothesis {
  id: string;
  lens: 'execution' | 'organization' | 'business' | 'team' | 'market' | 'growth';
  title: string;
  statement: string;
  confidence: 'high' | 'medium' | 'low';
  reason: string;
  relatedTaskIds: string[];
  evidenceSources: string[];
  assumptionNote: string;
}

export interface EventLineEvidenceSlot {
  key:
    | 'stage'
    | 'goal'
    | 'blocker'
    | 'next_action'
    | 'recent_change'
    | 'owner_chain'
    | 'recent_decision'
    | 'project_link';
  label: string;
  coverage: 'full' | 'partial' | 'missing';
  evidenceStrength: 'strong' | 'medium' | 'weak' | 'none';
  sourceTypes: Array<'event_line' | 'task_fact' | 'project_context' | 'user_note' | 'uploaded_doc' | 'manual_clarification'>;
  summary: string;
  recommendedFix: 'upload_docs' | 'clarify_now' | 'wait_for_more_trace';
}

export interface EventLineCompleteness {
  eventLineId: string;
  title: string;
  score: number;
  status: 'insufficient' | 'summary_ready' | 'forecast_ready' | 'high_confidence';
  missingSlots: string[];
  strongestSlots: string[];
  memoryConfidence?: number | null;
  backgroundSources?: string[];
  slots: EventLineEvidenceSlot[];
}

export interface ReviewDashboardEvidenceRef {
  sourceType: 'task' | 'meeting' | 'support_request' | 'attachment' | 'clarification' | 'event_line' | 'notebook' | 'event_line_memory';
  sourceId: string;
  title: string;
  summary?: string;
}

export interface ReviewDashboardCardTarget {
  targetType: 'event_line' | 'task_view' | 'meeting' | 'support_request' | 'attachment_group' | 'task';
  targetId: string;
  targetLabel?: string;
  targetFilters?: Record<string, unknown>;
  evidenceRefs?: ReviewDashboardEvidenceRef[];
}

export interface EventLineContextFact {
  sourceType: 'task' | 'meeting' | 'attachment' | 'support_request' | 'clarification' | 'notebook' | 'event_line_memory';
  sourceId: string;
  title: string;
  summary: string;
  happenedAt?: string | null;
}

export interface EventLineJudgment {
  eventLineId: string;
  title: string;
  viewerRole: 'employee' | 'department_lead' | 'admin';
  judgmentVersion: string;
  bundleFingerprint: string;
  coverageScore: number;
  confidenceScore: number;
  safeOutputMode: 'needs_input' | 'summary_only' | 'full_judgment';
  publishState: 'local_preview' | 'publish_ready' | 'published_by_human' | 'published_by_robot' | 'stale';
  publishedAt?: string | null;
  publishedBy?: string | null;
  invalidatedAt?: string | null;
  whatHappened: string;
  whyItMatters: string;
  coreBlocker: string;
  blockerType: 'business' | 'collaboration' | 'decision' | 'structure' | 'capacity' | 'evidence';
  evidenceSummary: string;
  managerImplication: string;
  nextWeekFocus: string;
  minimumAction: string;
  riskIfIgnored: string;
  opportunityIfAmplified: string;
  evidenceRefs: ReviewDashboardEvidenceRef[];
  target?: ReviewDashboardCardTarget | null;
}

export interface EventLineContextBundle {
  eventLineId: string;
  lineName: string;
  businessCategory: string;
  stage: string;
  summary: string;
  intent: string;
  currentWork: string;
  currentBlocker: string;
  recentDecision: string;
  nextStep: string;
  recentProgress: string;
  projectName: string;
  collaborationRelationship: string;
  organizationIntro: string;
  currentChallenges: string[];
  collaborationGoals: string[];
  keyPeople: string[];
  keyProducts: string[];
  recentFacts: string[];
  taskFacts: EventLineContextFact[];
  meetingFacts: EventLineContextFact[];
  attachmentFacts: EventLineContextFact[];
  clarificationFacts: EventLineContextFact[];
  evidenceRefs: ReviewDashboardEvidenceRef[];
  trendSignals: TrendSignal[];
  taskCount: number;
  meetingCount: number;
  attachmentCount: number;
  supportRequestCount: number;
  readiness: 'low' | 'medium' | 'high';
}

export interface EventLineSummaryCard {
  eventLineId: string;
  title: string;
  kind: 'project_line' | 'issue_line' | 'coordination_line' | 'case_line' | 'custom';
  status: 'active' | 'blocked' | 'paused' | 'done' | 'archived';
  projectName?: string | null;
  moduleName?: string | null;
  flowName?: string | null;
  whatThisLineIs: string;
  whatHappenedThisWeek: string;
  currentState: string;
  mainBlocker: string;
  nextCriticalMove: string;
  ownerNames: string[];
  completenessScore: number;
  predictionReadiness: 'not_ready' | 'summary_only' | 'conservative_forecast' | 'strong_forecast';
  missingSlots: string[];
  memoryConfidence?: number | null;
  backgroundSources?: string[];
  evidencePreview: string[];
  publishState?: 'local_preview' | 'publish_ready' | 'published_by_human' | 'published_by_robot' | 'stale';
  publishedAt?: string | null;
  publishedBy?: string | null;
  invalidatedAt?: string | null;
  target?: ReviewDashboardCardTarget | null;
  evidenceRefs?: ReviewDashboardEvidenceRef[];
}

export interface EventLineRiskCard {
  eventLineId: string;
  title: string;
  riskType: 'schedule_drift' | 'collaboration_friction' | 'decision_lag' | 'goal_drift' | 'workflow_breakdown' | 'overload';
  statement: string;
  forecastWindow: '1w' | '2w' | '3w';
  probability: 'high' | 'medium' | 'low';
  impactScope: 'person' | 'team' | 'project' | 'org';
  triggerSignals: string[];
  whyNow: string;
  ifIgnored: string;
  suggestedAction: string;
  ownerRole: string;
  publishState?: 'local_preview' | 'publish_ready' | 'published_by_human' | 'published_by_robot' | 'stale';
  publishedAt?: string | null;
  publishedBy?: string | null;
  invalidatedAt?: string | null;
  target?: ReviewDashboardCardTarget | null;
  evidenceRefs?: ReviewDashboardEvidenceRef[];
}

export interface EventLineOpportunityCard {
  eventLineId: string;
  title: string;
  opportunityType: 'repeatable_pattern' | 'momentum_building' | 'process_upgrade' | 'capability_signal' | 'leverage_point';
  statement: string;
  forecastWindow: '1w' | '2w' | '3w';
  confidence: 'high' | 'medium' | 'low';
  upside: string;
  supportingSignals: string[];
  recommendedAmplifier: string;
  ownerRole: string;
  publishState?: 'local_preview' | 'publish_ready' | 'published_by_human' | 'published_by_robot' | 'stale';
  publishedAt?: string | null;
  publishedBy?: string | null;
  invalidatedAt?: string | null;
  target?: ReviewDashboardCardTarget | null;
  evidenceRefs?: ReviewDashboardEvidenceRef[];
}

export interface TrendSignal {
  key: string;
  title: string;
  statement: string;
  signalType:
    | 'repeat_reschedule'
    | 'repeat_review_pending'
    | 'repeat_support_request'
    | 'stalled_event_line'
    | 'escalating_blocker'
    | 'thin_evidence';
  severity: 'high' | 'medium' | 'low';
  windowLabel: string;
  relatedEventLineId?: string | null;
  relatedTaskIds: string[];
  evidenceRefs: ReviewDashboardEvidenceRef[];
  target?: ReviewDashboardCardTarget | null;
}

export interface WeeklyReviewAnalysis {
  scope: TaskReviewScope;
  emphasis: 'summary' | 'analysis';
  headline: string;
  caution: string;
  weeklyOverview: string;
  weeklyFocusLines: string[];
  weeklyNextFocus: string[];
  dnaModuleTitles: string[];
  metricCards: ReviewMetricCard[];
  evidenceWeights: ReviewEvidenceWeight[];
  confirmedFacts: string[];
  hypothesisHighlights: ReviewHypothesis[];
  nextWeekFocus: string[];
  eventLineSummaries: EventLineSummaryCard[];
  eventLineCompleteness: EventLineCompleteness[];
  eventLineContextBundles: EventLineContextBundle[];
  eventLineJudgments: EventLineJudgment[];
  riskCards: EventLineRiskCard[];
  opportunityCards: EventLineOpportunityCard[];
  trendSignals: TrendSignal[];
  narrativeAnalyses: NarrativeAnalysis[];
}

export interface WeeklyMainlineCard {
  id?: string;
  title: string;
  taskCount: number;
  completedCount: number;
  pendingCount: number;
  progressText: string;
  nextGoalText: string;
}

export interface WeeklyMainlineCards {
  summaryText: string;
  mainlines: WeeklyMainlineCard[];
  generatedBy: 'ai' | 'fallback';
  evidenceMeta?: Record<string, unknown>;
}

export type WeeklyEventReviewCardKind = 'event_line' | 'task_cluster' | 'single_task' | 'needs_assignment';

export interface WeeklyEventReviewCard {
  id?: string;
  title: string;
  cardKind: WeeklyEventReviewCardKind;
  taskIds: string[];
  taskTitles: string[];
  reflectionPromptText: string;
  progressText: string;
  nextActionText: string;
  materialSuggestionText: string;
  confidence: 'low' | 'medium' | 'high';
  generatedBy: 'ai' | 'fallback';
}

export interface WeeklyEventReviewCards {
  cards: WeeklyEventReviewCard[];
  generatedBy: 'ai' | 'fallback';
  evidenceMeta?: Record<string, unknown>;
}

export interface WeeklyOverviewRefreshPayload {
  weekLabel?: string | null;
  perspective?: ReviewPerspectiveKey | null;
  departmentId?: string | null;
  force?: boolean;
}

export interface WeeklyOverviewRefreshStatus {
  weekLabel: string;
  perspective: ReviewPerspectiveKey;
  departmentId?: string | null;
  viewerUserId: string;
  status: 'idle' | 'running' | 'succeeded' | 'failed';
  startedAt?: string | null;
  generatedAt?: string | null;
  failureReason?: string;
  sourceCounts?: Record<string, unknown>;
  cacheKey?: string;
}

export interface TaskContextPreview {
  taskId: string;
  clientId?: string | null;
  clientName?: string | null;
  contextBundle: EventLineContextBundle;
  judgment: EventLineJudgment;
  judgmentVersion: string;
  bundleFingerprint: string;
  coverageScore: number;
  confidenceScore: number;
  safeOutputMode: 'needs_input' | 'summary_only' | 'full_judgment';
  publishState: 'local_preview' | 'publish_ready' | 'published_by_human' | 'published_by_robot' | 'stale';
  summaryChips: string[];
  readiness: 'low' | 'medium' | 'high';
}

export interface PlanNode {
  id: string;
  level: 'ceo' | 'director' | 'manager' | 'project';
  title: string;
  summary: string;
  status: string;
  ownerUserId?: string | null;
  ownerName?: string | null;
  ownerUnitId?: string | null;
  startsAt?: string | null;
  endsAt?: string | null;
}

export interface ManagementSignalCard {
  id: string;
  reviewId: string;
  userId: string;
  userName: string;
  weekLabel: string;
  contentDomain: 'work';
  visibilityScope: 'team' | 'department' | 'org';
  eligibleForAggregation: boolean;
  eligibleForManagerRetrieval: boolean;
  signals: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

export interface PersonalGrowthCard {
  id: string;
  reviewId: string;
  userId: string;
  contentDomain: 'personal';
  visibilityScope: 'self';
  summary: string;
  suggestions: string[];
  createdAt: string;
  updatedAt: string;
}

export interface ReviewActionCard {
  id: string;
  actionType: 'task' | 'support_request' | 'resource_request' | 'meeting' | 'one_on_one';
  title: string;
  payload: Record<string, unknown>;
  status: string;
  createdAt: string;
  publishState?: 'local_preview' | 'publish_ready' | 'published_by_human' | 'published_by_robot' | 'stale';
  publishedAt?: string | null;
  publishedBy?: string | null;
  invalidatedAt?: string | null;
  target?: ReviewDashboardCardTarget | null;
  evidenceRefs?: ReviewDashboardEvidenceRef[];
}

export interface ReviewActionExecutionResult {
  objectType: 'task' | 'support_request' | 'meeting';
  objectId: string;
  objectLabel: string;
  targetClientId?: string | null;
  targetClientName?: string | null;
  targetEventLineId?: string | null;
  targetEventLineName?: string | null;
  canOpen?: boolean;
  supportRequest?: SupportRequestRecord;
}

export interface HierarchyReport {
  id: string;
  scopeType: 'employee' | 'team' | 'org';
  scopeRefId: string;
  weekLabel: string;
  logicMode: string;
  judgmentVersion?: string | null;
  bundleFingerprint?: string | null;
  coverageScore?: number | null;
  confidenceScore?: number | null;
  safeOutputMode?: 'needs_input' | 'summary_only' | 'full_judgment' | null;
  headline: string;
  summary: string;
  summaryMetrics: ReviewMetricCard[];
  focusAreas: string[];
  supportSignals: string[];
  suggestedActions: string[];
  anonymousInsights: string[];
  sourcePolicy: Record<string, unknown>;
  actions: ReviewActionCard[];
  publishState?: 'local_preview' | 'publish_ready' | 'published_by_human' | 'published_by_robot' | 'stale';
  publishedAt?: string | null;
  publishedBy?: string | null;
  invalidatedAt?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface TaskViewFilterSet {
  sourceTypes?: string[];
  businessCategories?: string[];
  eventLineIds?: string[];
  onlyRisky?: boolean;
  onlyWithEventLine?: boolean;
  needsReview?: boolean | null;
  minimumEvidenceCount?: number | null;
}

export interface TaskViewDefinition {
  id: string;
  name: string;
  kind: 'event_line' | 'risk' | 'source' | 'business_category' | 'custom';
  description: string;
  calendarScope: 'all' | 'event_line' | 'risk' | 'source' | 'business_category';
  shareability: 'private' | 'org';
  sortBy: 'updatedAt' | 'dueDate' | 'priority' | 'evidenceCount';
  sortDirection: 'asc' | 'desc';
  visibleFields: string[];
  filterSet: TaskViewFilterSet;
  builtIn: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface TaskViewPreset {
  key: 'event_line' | 'risk' | 'source' | 'business_category';
  label: string;
  description: string;
  viewId: string;
}

export interface TaskViewsResponse {
  views: TaskViewDefinition[];
  presets: TaskViewPreset[];
}

export interface TaskViewMutationPayload {
  name: string;
  kind?: 'event_line' | 'risk' | 'source' | 'business_category' | 'custom';
  description?: string;
  calendarScope?: 'all' | 'event_line' | 'risk' | 'source' | 'business_category';
  shareability?: 'private' | 'org';
  sortBy?: 'updatedAt' | 'dueDate' | 'priority' | 'evidenceCount';
  sortDirection?: 'asc' | 'desc';
  visibleFields?: string[];
  filterSet?: TaskViewFilterSet;
}

export interface ReviewDashboardDrillTargetResponse {
  target: ReviewDashboardCardTarget;
  eventLineDetail?: EventLineDetail | null;
  eventLineMemory?: EventLineMemorySnapshot | null;
  tasks: Task[];
  meetings: MeetingSummary[];
  supportRequests: SupportRequestRecord[];
  attachments: TaskAttachmentRecord[];
}

export interface ReviewSimulationBundle {
  sampleSize: number;
  label: string;
  orgReport?: HierarchyReport | null;
  departmentReports: HierarchyReport[];
}

export type ReviewPerspectiveKey = 'organization' | 'department' | 'mine';

export interface ReviewPerspectiveOption {
  key: ReviewPerspectiveKey;
  label: string;
  departmentId?: string | null;
  departmentName?: string | null;
}

export interface DepartmentSignalActionAlert {
  id: string;
  kind:
    | 'plan_unclaimed'
    | 'collaboration_pileup'
    | 'low_throughput'
    | 'dispatch_unconfirmed'
    | 'client_commitment_at_risk'
    | string;
  severity: 'high' | 'medium' | 'low' | string;
  title: string;
  advice: string;
  involvedDepartmentId?: string | null;
  involvedDepartmentName?: string | null;
  involvedUserIds?: string[];
  involvedUserNames?: string[];
  metricLabel?: string | null;
  metricValueText?: string | null;
  daysLeft?: number | null;
  sourceQuote?: string | null;
}

export interface DepartmentSignalOneOnOneSuggestion {
  userId: string;
  userName: string;
  departmentId?: string | null;
  departmentName?: string | null;
  reason: string;
  questionPrompts: string[];
  weekCreatedCount: number;
  weekCompletedCount: number;
  trendCompletedByWeek?: number[];
  trendCreatedByWeek?: number[];
}

export interface DepartmentSnapshot {
  departmentId: string;
  departmentName: string;
  leaderUserId?: string | null;
  leaderName?: string | null;
  status: 'tight' | 'stable' | 'abnormal' | string;
  completionRate: number;
  planTotalCount: number;
  planDoneCount: number;
  planAssignedCount: number;
  planLinkedCount: number;
  headlines: string[];
  temperatureLevel: number;
  burndownIdeal: number[];
  burndownActual: number[];
}

export interface ExecutiveHealthIndicator {
  key: string;
  label: string;
  valueText: string;
  unitText?: string | null;
  deltaText?: string | null;
  trendDirection: 'up' | 'down' | 'flat' | string;
  accent: 'success' | 'warning' | 'danger' | 'neutral' | string;
  helperText?: string | null;
}

export interface ExecutiveDecision {
  id: string;
  rank: number;
  severity: 'critical' | 'important' | 'normal' | string;
  title: string;
  situation: string;
  decision: string;
  cost: string;
  actionLabel?: string | null;
  actionTarget?: Record<string, unknown> | null;
  sourceRefs?: Array<Record<string, unknown>>;
}

export interface DepartmentScoreRow {
  departmentId: string;
  departmentName: string;
  leaderName?: string | null;
  valueProductionScore: number;
  fulfillmentRatePct: number;
  monthlyProgressPct: number;
  humanEfficiencyScore: number;
  headlineInsight?: string | null;
  status: string;
}

export interface DepartmentSignalsResponse {
  weekLabel: string;
  viewerRole: 'admin' | 'department_lead' | 'employee' | string;
  healthIndicators: ExecutiveHealthIndicator[];
  executiveDecisions: ExecutiveDecision[];
  departmentScoreboard: DepartmentScoreRow[];
  actionAlerts: DepartmentSignalActionAlert[];
  oneOnOneSuggestions: DepartmentSignalOneOnOneSuggestion[];
  departmentSnapshots: DepartmentSnapshot[];
}

export interface ReviewDashboard {
  weekLabel?: string;
  resolvedWeekLabel?: string | null;
  currentReview?: WeeklyReview | null;
  workItems: WeeklyReviewTaskEntry[];
  personalItems: WeeklyReviewTaskEntry[];
  availablePerspectives: ReviewPerspectiveOption[];
  activePerspective: ReviewPerspectiveKey;
  activeDepartmentId?: string | null;
  activeDepartmentName?: string | null;
  workAnalysis?: WeeklyReviewAnalysis | null;
  personalAnalysis?: WeeklyReviewAnalysis | null;
  weeklyMainlineCards?: WeeklyMainlineCards | null;
  weeklyEventReviewCards?: WeeklyEventReviewCards | null;
  weeklyOverviewGenerationStatus?: WeeklyOverviewRefreshStatus | null;
  selfReport?: HierarchyReport | null;
  workSignalCard?: ManagementSignalCard | null;
  personalGrowthCard?: PersonalGrowthCard | null;
  teamReport?: HierarchyReport | null;
  orgReport?: HierarchyReport | null;
  executiveOrgReport?: HierarchyReport | null;
  departmentReports: HierarchyReport[];
  agentDepartmentDigests: AgentWeeklyDigest[];
  agentDepartmentPlans: AgentWeeklyPlan[];
  simulationBundle?: ReviewSimulationBundle | null;
  plans: PlanNode[];
}

// ── UnderstandingSnapshotV1: 统一理解输出对象 ──

export type UnderstandingMode = 'basic' | 'enhanced';

export interface UnderstandingSourceBreakdown {
  sourceType: 'org_dna' | 'client_background' | 'quarterly_focus' | 'task_title' | 'task_desc' | 'review_note' | 'event_line_memory' | 'meeting' | 'support_request' | 'calendar' | 'attachment';
  available: boolean;
  label: string;
}

export interface UnderstandingOptionalAdvice {
  realBlocker?: string | null;
  timeGate?: string | null;
  minimumAction?: string | null;
  supportAsk?: string | null;
}

export interface UnderstandingSnapshotV1 {
  taskId: string;
  mode: UnderstandingMode;
  coverage: number;
  confidence: number;
  humanBrief?: string | null;
  whatIsThis: string;
  whyItMatters: string;
  progressNow: string;
  unknowns: string;
  knownFacts: string[];
  optionalAdvice?: UnderstandingOptionalAdvice | null;
  sourceBreakdown: UnderstandingSourceBreakdown[];
}

// ── Phase 1: 客户战略画像 + 合作关系 + 事件线周历史 ──

export type CooperationType = 'strategic_companion' | 'single_project' | 'exploring' | 'dormant';
export type RelationshipHealth = 'thriving' | 'steady' | 'cooling' | 'at_risk';

/** 客户战略画像 — 补充 ClientSummary 中缺失的深层信息 */
export interface ClientStrategicProfile {
  clientId: string;
  industry: string;
  scale: string;
  influence: string;
  currentNeeds: string;
  painPoints: string;
  strategicValueToYiyu: string;
  decisionChain: string;
  updatedAt: string;
}

/** 益语与客户的合作关系 */
export interface CooperationRelationship {
  id: string;
  clientId: string;
  clientName: string;
  whyConnected: string;
  meaningToYiyu: string;
  meaningToClient: string;
  cooperationType: CooperationType;
  relationshipHealth: RelationshipHealth;
  keyStakeholders: CooperationStakeholder[];
  milestones: string;
  startedAt: string;
  updatedAt: string;
}

export interface CooperationStakeholder {
  name: string;
  role: string;
  relationship: string;
}

/** 事件线周快照历史 — 每周复盘时自动归档 */
export interface EventLineWeeklySnapshot {
  id: string;
  eventLineId: string;
  eventLineName: string;
  weekLabel: string;
  stageAtThatTime: string;
  keyDecisions: string[];
  turningPoints: string[];
  blockersThen: string[];
  progressDelta: string;
  taskCount: number;
  completedCount: number;
  createdAt: string;
}

/** 五层上下文叙事分析 — LLM 生成 */
export interface NarrativeAnalysis {
  eventLineId: string;
  eventLineName: string;
  clientId?: string | null;
  clientName?: string | null;
  whatThisIs: string;
  whyImportant: string;
  currentProgress: string;
  missingUnderstanding: string;
  riskNote?: string | null;
  timeGate?: string | null;
  minimumAction?: string | null;
  managementAdvice?: string | null;
  contextLayersUsed: string[];
  confidenceLevel: 'low' | 'medium' | 'high';
}

export type StrategicJudgmentStatus = 'system_draft' | 'confirmed' | 'waiting';
export type StrategicHealthStatus = 'healthy' | 'watch' | 'risk' | 'uncalibrated';
export type StrategicLineMomentum = '加码' | '稳住' | '收口' | '暂停';
export type StrategicItemPriority = 'high' | 'medium' | 'low';

export interface StrategicPermission {
  canEdit: boolean;
  isCeo: boolean;
  leaderUserId?: string | null;
  notice?: string | null;
}

export interface StrategicReadiness {
  status: 'ready' | 'insufficient';
  score: number;
  summary: string;
  gaps: string[];
}

export interface StrategicJudgment {
  value: string;
  status: StrategicJudgmentStatus;
  sources: string[];
}

export interface StrategicHeadline {
  weekSummary: StrategicJudgment;
  mainContradiction: StrategicJudgment;
  coreBreakthrough: StrategicJudgment;
  focusItems: string[];
  focusStatus: StrategicJudgmentStatus;
  freshness: string;
}

export interface StrategicHealthLine {
  key: string;
  title: string;
  status: StrategicHealthStatus;
  trend: string;
  summary: string;
  evidence: string[];
}

export interface StrategicLine {
  id: string;
  title: string;
  summary: string;
  module?: string | null;
  flow?: string | null;
  stage?: string | null;
  blocker: string;
  decision: string;
  nextStep: string;
  momentum: StrategicLineMomentum;
  evidence: string[];
  memoryConfidence?: number | null;
  predictionReadiness?: number | null;
  clarificationNeeds?: string[];
}

export interface StrategicLineDetail extends StrategicLine {
  clientId: string;
  clientName: string;
  stageLabel: string;
  relatedTaskIds: string[];
  relatedTaskTitles: string[];
  contextSummary: string;
}

export interface StrategicChecklistItem {
  title: string;
  detail: string;
  source: string;
  priority: StrategicItemPriority;
}

export interface StrategicChecklistGroup {
  key: string;
  title: string;
  description: string;
  items: StrategicChecklistItem[];
}

export interface StrategicChangePoint {
  title: string;
  summary: string;
  confidence: string;
  signals: string[];
}

export interface StrategicEvidenceCard {
  label: string;
  value: string;
}

export interface StrategicEvidencePreview {
  summary: string;
  cards: StrategicEvidenceCard[];
  boundaries: string[];
  keyFacts: string[];
  keyWarnings: string[];
}

export interface StrategicAssetCandidate {
  title: string;
  source: string;
  summary: string;
  nextAction: string;
}

export interface StrategicMeetingPackDraft {
  title: string;
  agenda: string[];
  groups: StrategicChecklistGroup[];
}

export interface StrategicCockpitSnapshot {
  clientId: string;
  clientName: string;
  clientTagline: string;
  stageLabel: string;
  permission: StrategicPermission;
  readiness: StrategicReadiness;
  headline: StrategicHeadline;
  health: StrategicHealthLine[];
  strategicLines: StrategicLine[];
  twoWeekChanges: StrategicChangePoint[];
  pendingDecisions: StrategicChecklistItem[];
  pendingMaterials: StrategicChecklistItem[];
  meetingPackDraft: StrategicMeetingPackDraft;
  evidencePreview: StrategicEvidencePreview;
  assetCandidates: StrategicAssetCandidate[];
  officialLayer: Record<string, unknown>;
  radarLayer: Record<string, unknown>;
  officialLayerStatus: 'ready' | 'empty';
  officialEmptyReason?: string | null;
  resolutionTrace: Record<string, unknown>;
  notebookSummary?: OrganizationNotebookSnapshot | null;
  memoryStatus?: MemoryStatus | null;
  linkedEventLineMemories?: EventLineMemorySnapshot[];
}

export interface StrategicCockpitConfirmPayload {
  weekSummary: string;
  mainContradiction: string;
  coreBreakthrough: string;
  focusItems: string[];
}

export type StrategicThoughtScope = 'client' | 'project' | 'system';
export type StrategicThoughtStatus = 'draft' | 'confirmed' | 'dismissed' | 'task_created' | 'waiting_evidence';
export type StrategicThoughtConfidenceLevel = 'low' | 'medium' | 'high' | 'none';
export type StrategicInsightType =
  | 'strategic_shift'
  | 'risk_signal'
  | 'opportunity_window'
  | 'execution_bottleneck'
  | 'narrative_upgrade'
  | 'operating_model';

export type StrategicThoughtSourceType =
  | 'strategic_cockpit'
  | 'strategic_line'
  | 'headline'
  | 'pending_decision'
  | 'pending_material'
  | 'brain_dashboard'
  | 'judgment_version'
  | 'theme_cluster'
  | 'conflict_group'
  | 'open_question'
  | 'event_line'
  | 'meeting'
  | 'review'
  | 'knowledge'
  | 'analysis_run'
  | 'client_dna'
  | 'document'
  | 'task'
  | 'project_module'
  | 'project_flow'
  | 'system';

export interface StrategicThoughtSource {
  sourceType: StrategicThoughtSourceType;
  sourceId?: string | null;
  label: string;
  detail?: string | null;
}

export interface StrategicThoughtReview {
  thoughtId: string;
  status: StrategicThoughtStatus;
  note: string;
  taskId?: string | null;
  judgmentId?: string | null;
  reviewedAt?: string | null;
  reviewedBy?: string | null;
}

export interface StrategicThought {
  id: string;
  scope: StrategicThoughtScope;
  clientId?: string | null;
  clientName: string;
  projectModuleId?: string | null;
  projectModuleName?: string | null;
  line: string;
  observation: string;
  suggestion: string;
  confidence?: number | null;
  confidenceLevel: StrategicThoughtConfidenceLevel;
  status: StrategicThoughtStatus;
  isSystem: boolean;
  dueDateHint: string;
  tags: string[];
  sources: StrategicThoughtSource[];
  evidenceCount: number;
  generatedAt: string;
  staleReason?: string | null;
  evidenceLevel?: 'none' | 'weak' | 'medium' | 'strong' | null;
  reason?: string | null;
  insightType?: StrategicInsightType | null;
  insightText?: string | null;
  futureJudgment?: string | null;
  whyItMatters?: string | null;
  recommendedAction?: string | null;
  evidenceSummary?: string | null;
  evidenceLabels?: string[];
  signalScore?: number;
  sourceFingerprint?: string | null;
  isFavorite?: boolean;
  isDeleted?: boolean;
  review?: StrategicThoughtReview | null;
}

export interface StrategicThoughtsResponse {
  items: StrategicThought[];
  total: number;
  generatedAt: string;
  selectedClientId?: string | null;
  selectedProjectModuleId?: string | null;
  usingMockData?: boolean;
}

export interface StrategicThoughtRefreshPayload {
  clientId?: string | null;
  projectModuleId?: string | null;
  limit?: number;
}

export interface StrategicThoughtStatePayload {
  action: 'favorite' | 'unfavorite' | 'delete' | 'restore';
}

export interface StrategicThoughtReviewPayload {
  action: 'confirm' | 'dismiss' | 'mark_task_created';
  note?: string;
  taskId?: string | null;
  createJudgment?: boolean;
}

export interface ReviewHistoryEntry {
  weekLabel: string;
  submittedAt: string;
  workItemCount: number;
  personalItemCount: number;
}

export interface ReviewHistoryResponse {
  items: ReviewHistoryEntry[];
}

export interface TaskSettings {
  defaultListId?: string | null;
  defaultPriority: Priority;
  defaultDueDatePreset: TaskDueDatePreset;
  defaultViewMode: TaskViewPreference;
  listSortMode: TaskListSortMode;
  showCompletedTasks: boolean;
  defaultReviewScope: TaskReviewScope;
  autoAssignSelf: boolean;
  updatedAt: string;
}

export interface ClientDnaModule {
  clientId: string;
  moduleKey: OrganizationDnaModuleKey;
  title: string;
  markdownContent: string;
  normalizedText: string;
  summary: string;
  fileName?: string | null;
  contentHash?: string | null;
  sourceKind: 'manual' | 'generated';
  missingInfo: string[];
  updatedAt?: string | null;
  updatedBy?: string | null;
  hasDocument: boolean;
}

export interface ClientDnaModulesResponse {
  modules: ClientDnaModule[];
}

export interface ClientDnaGeneratePayload {
  refreshGenerated?: boolean;
}

export interface ClientWorkspaceSettings {
  meetingPublishDefaultListId?: string | null;
  meetingPublishDefaultPriority: Priority;
  defaultGoalQuarter: string;
  defaultMeetingTitlePrefix: string;
  clientDnaModeLabel: string;
  updatedAt: string;
}

export interface TopicsSettings {
  chineseOnly: boolean;
  requireInsightBeforeActions: boolean;
  defaultTaskOwnerMode: TopicTaskOwnerMode;
  defaultTimeRange: string;
  defaultSourceStrategy: string;
  updatedAt: string;
}

export interface DiagnosisProfileRecord {
  id: string;
  groupKey: 'platform_fundraising' | 'monthly_donor' | 'key_person';
  deepDnaId?: string | null;
  label: string;
  fileName: string;
  filePath: string;
  markdownContent: string;
  summary: string;
  corePreferences: string[];
  riskTriggers: string[];
  tonePreference?: string;
  updatedAt: string;
}

export interface OrganizationRiskDnaDocument {
  fileName: string;
  filePath: string;
  markdownContent: string;
  summary: string;
  coreRisks: string[];
  sensitiveScenarios: string[];
  tonePreference?: string;
  updatedAt: string;
}

export interface FundraisingKnowledgeDocument {
  id: string;
  title: string;
  fileName: string;
  filePath: string;
  markdownContent: string;
  summary: string;
  scenes: string[];
  tags: string[];
  principles: string[];
  riskSignals: string[];
  updatedAt: string;
}

export interface DeepDnaSourceRecord {
  id: string;
  kind: 'manual' | 'import' | 'web';
  title: string;
  excerpt: string;
  sourceUrl?: string | null;
  fileName?: string | null;
  filePath?: string | null;
  createdAt: string;
}

export interface DeepDnaRecord {
  id: string;
  groupKey: 'platform_fundraising' | 'monthly_donor' | 'key_person';
  label: string;
  status: 'draft' | 'published';
  sourceKind: 'manual' | 'import' | 'web';
  identitySummary: string;
  corePreferences: string[];
  supportTriggers: string[];
  redFlags: string[];
  evidencePreferences: string[];
  voiceStyle: string[];
  commonQuestions: string[];
  sources: DeepDnaSourceRecord[];
  confidenceScore: number;
  confidenceLevel: 'low' | 'medium' | 'high';
  authorizationStatus: 'public' | 'authorized_internal' | 'restricted';
  rawContent: string;
  searchQuery?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface DeepDnaDraft {
  id: string;
  groupKey: 'platform_fundraising' | 'monthly_donor' | 'key_person';
  label: string;
  searchQuery: string;
  draftRecord: DeepDnaRecord;
  previewSources: DeepDnaSourceRecord[];
  createdAt: string;
  updatedAt: string;
}

export interface CoachCaseRecord {
  id: string;
  title: string;
  summary: string;
  whyEffective: string;
  takeaways: string[];
  keyExcerpt: string;
  scenes: string[];
  tags: string[];
  issueTypes: string[];
  sourceType: 'system' | 'organization';
  sourceLabel: string;
  createdAt: string;
  updatedAt: string;
}

export interface CoachReminderRule {
  id: string;
  title: string;
  modeIds: string[];
  knowledgeKey: string;
  issuePattern: string;
  message: string;
  createdAt: string;
  updatedAt: string;
}

export interface OrgWritingNorm {
  id: string;
  title: string;
  description: string;
  instruction: string;
  modeIds: string[];
  triggerKeywords: string[];
  createdAt: string;
  updatedAt: string;
}

export interface CoachCardRecord {
  id: string;
  issueKey: string;
  insightTitle: string;
  issueWhat: string;
  whyImportant: string;
  knowledgePointTitle: string;
  knowledgePointBody: string;
  caseIds: string[];
  selfRewriteHint: string;
  learningAction: string;
  referenceDraft?: string | null;
}

export interface CoachPayload {
  cards: CoachCardRecord[];
  triggeredReminders: CoachReminderRule[];
  appliedNorms: OrgWritingNorm[];
}

export interface RunComparison {
  currentRunId: string;
  previousRunId?: string | null;
  resultChanges: string[];
  learningChanges: string[];
  resolvedIssues: string[];
  newIssues: string[];
  repeatedIssues: string[];
}

export interface AnalysisWorkbenchSettings {
  enabledTemplateIds: string[];
  defaultTemplateId?: string | null;
  defaultTitlePrefix: string;
  allowEmployeeTemplateEditing: boolean;
  diagnosisProfiles?: DiagnosisProfileRecord[];
  organizationRiskDna?: OrganizationRiskDnaDocument | null;
  fundraisingKnowledgeLibrary?: FundraisingKnowledgeDocument[];
  deepDnaLibrary?: DeepDnaRecord[];
  coachCaseLibrary?: CoachCaseRecord[];
  coachReminderRules?: CoachReminderRule[];
  orgWritingNorms?: OrgWritingNorm[];
  updatedAt: string;
  authorityStates?: {
    personalPreferences?: { state: string; authority?: string };
    businessKnowledge?: {
      state: 'blocked' | 'ready';
      reasonCode?: string;
      message?: string;
    };
  };
  unsupportedFields?: string[];
}

export interface HandbookSettings {
  defaultTags: string[];
  defaultCategory: string;
  allowTaskSource: boolean;
  allowAnalysisSource: boolean;
  visibilityBoundary: string;
  updatedAt: string;
}

export interface SystemAdminSettings {
  allowBusinessSettingsForEmployees: boolean;
  allowOrgDnaForEmployees: boolean;
  protectEmployeeAdmin: boolean;
  protectAiAndCloud: boolean;
  protectCloudSecurity: boolean;
  updatedAt: string;
}

export interface FeishuBotSettings {
  appId: string;
  receiveIdType: FeishuReceiveIdType;
  receiverId: string;
  botName: string;
  userBindingCallbackUrl: string;
  ready: boolean;
  hasAppSecret: boolean;
  secretSource: string;
  secretFingerprint?: string | null;
  lastConnectionStatus: 'idle' | 'success' | 'failed';
  lastConnectionMessage?: string | null;
  lastConnectedAt?: string | null;
  lastTestMessageAt?: string | null;
  updatedAt: string;
}

export interface FeishuUserBinding {
  linked: boolean;
  readyForAuthorization: boolean;
  appId: string;
  userId: string;
  openId?: string | null;
  unionId?: string | null;
  feishuUserId?: string | null;
  name?: string | null;
  enName?: string | null;
  avatarUrl?: string | null;
  email?: string | null;
  tenantKey?: string | null;
  boundAt?: string | null;
  lastVerifiedAt?: string | null;
  lastError?: string | null;
}

export interface FeishuUserBindingStartResult {
  authorizeUrl: string;
  state: string;
  expiresAt: string;
  callbackUrl: string;
  qrReady: boolean;
  qrBlockedReason?: string | null;
}

export interface OrgMembershipSummary {
  hasOrganization: boolean;
  organizationId?: string | null;
  organizationName?: string | null;
  organizationSlug?: string | null;
  departmentId?: string | null;
  departmentName?: string | null;
  membershipStatus?: MembershipStatus;
  applicationId?: string | null;
  applicationState?: 'none' | 'pending' | 'approved' | 'rejected' | 'withdrawn';
  applicationVersion?: number | null;
  applicationMessage?: string | null;
  membershipSubmittedAt?: string | null;
  membershipRejectedReason?: string | null;
  organizationWorkspaceClientId?: string | null;
  organizationInternalProjectId?: string | null;
}

export interface UpdateOrgIdentity {
  organizationId?: string | null;
  organizationSlug?: string | null;
  organizationName?: string | null;
  cloudBackendUrl?: string | null;
  platform?: 'mac' | 'windows' | string | null;
}

export interface OfficialPushUpdatePayload {
  title: string;
  releaseId?: string | null;
  version: string;
  releaseVersion?: string | null;
  currentVersion: string;
  packageKind: 'release' | 'custom';
  customPackageId?: string | null;
  customPackageName?: string | null;
  fileName?: string | null;
  sizeBytes?: number | null;
  sha512?: string | null;
  downloadUrl?: string | null;
  publishedAt?: string | null;
  userNotes?: Record<string, string[]>;
  organizationCode?: string | null;
  relation: 'upgrade' | 'downgrade' | 'switch-custom' | 'different' | 'unknown';
}

export interface ReleaseVersionMetadata {
  requestedVersion: string;
  matchedVersion: string;
  exact: boolean;
  releaseId?: string | null;
  publishedAt?: string | null;
  userNotes?: Record<string, string[]>;
}

export interface OrgFeishuIntegrationAuditRecord {
  id: string;
  organizationId: string;
  actorUserId?: string | null;
  actorName?: string | null;
  appId: string;
  validationStatus: 'success' | 'failed';
  validationMessage: string;
  createdAt: string;
}

export interface OrgFeishuIntegration {
  organizationId?: string | null;
  organizationName?: string | null;
  appId: string;
  callbackMode?: string | null;
  customCallbackUrl?: string | null;
  effectiveCallbackUrl?: string | null;
  enabled: boolean;
  hasAppSecret: boolean;
  configuredBy?: string | null;
  effectiveScopeKind?: 'organization' | 'personal' | null;
  defaultWriteScope?: 'organization' | 'personal';
  scopeVersions?: {
    organization?: number;
    personal?: number;
  };
  expectedVersion?: number;
  botName?: string | null;
  tenantName?: string | null;
  sharedBotLabel?: string | null;
  notificationCoverage?: {
    total: number;
    notifiable: number;
    awaitingConversation: number;
    pendingConfirmation: number;
    failedRetryable: number;
    members?: Array<{
      membershipId: string;
      displayName: string;
      status: 'notifiable' | 'awaiting_conversation' | 'pending_confirmation' | 'failed_retryable';
    }>;
  };
  configuredAt?: string | null;
  updatedAt: string;
  lastValidationStatus: 'idle' | 'success' | 'failed' | 'succeeded' | 'failed_retryable';
  lastValidationMessage?: string | null;
  authorizationReady?: boolean;
  authorizationBlockedReason?: string | null;
  state?: 'ready' | 'not_connected' | 'blocked' | 'failed_retryable';
  retryable?: boolean;
  recentAudits: OrgFeishuIntegrationAuditRecord[];
}

export interface OrgFeishuIntegrationPayload {
  appId?: string;
  appSecret?: string;
  clearAppSecret?: boolean;
  callbackMode?: string | null;
  customCallbackUrl?: string | null;
  scopeKind?: 'organization' | 'personal';
  expectedVersion?: number;
}

export interface FeishuDeliveryProfile {
  userId: string;
  organizationId?: string | null;
  organizationName?: string | null;
  mobile: string;
  normalizedMobile?: string | null;
  deliveryStatus: 'missing_org' | 'integration_pending' | 'missing_mobile' | 'matched' | 'not_found' | 'failed';
  deliveryStatusLabel: string;
  readyForNotifications: boolean;
  receiveId?: string | null;
  lastVerifiedAt?: string | null;
  lastError?: string | null;
  blockedReason?: string | null;
}

export interface FeishuDeliveryProfilePayload {
  mobile?: string | null;
}

export type FeishuSyncStatus =
  | 'idle'
  | 'not_configured'
  | 'skipped'
  | 'time_invalid'
  | 'queued'
  | 'syncing'
  | 'synced'
  | 'imported_missing_mapping'
  | 'blocked'
  | 'failed_retryable'
  | 'failed';

export interface FeishuSyncStatusRecord {
  localType: string;
  localId: string;
  remoteType: string;
  remoteId?: string | null;
  remoteUrl?: string | null;
  status: FeishuSyncStatus;
  message: string;
  lastSyncedAt?: string | null;
  updatedAt: string;
  details: Record<string, unknown>;
}

export interface FeishuTaskCalendarSyncPayload {
  notify?: boolean;
}

export interface FeishuDocumentSyncPayload {
  localType?: string;
  localId: string;
  title: string;
  content: string;
  clientId?: string | null;
  triggerSource?: string;
  notifyOnCreate?: boolean;
}

export interface FeishuMemberAuthorization {
  linked: boolean;
  readyForAuthorization: boolean;
  organizationId?: string | null;
  organizationName?: string | null;
  appId: string;
  userId: string;
  openId?: string | null;
  unionId?: string | null;
  feishuUserId?: string | null;
  name?: string | null;
  enName?: string | null;
  avatarUrl?: string | null;
  email?: string | null;
  tenantKey?: string | null;
  boundAt?: string | null;
  lastVerifiedAt?: string | null;
  lastError?: string | null;
  blockedReason?: string | null;
}

export interface FeishuMemberAuthorizationStartResult {
  authorizeUrl: string;
  state: string;
  expiresAt: string;
  callbackUrl: string;
  qrReady: boolean;
  qrBlockedReason?: string | null;
}

export interface FeishuDocImportStatus {
  ready: boolean;
  linked: boolean;
  reason?: string | null;
  organizationId?: string | null;
  userId?: string | null;
  boundAt?: string | null;
}

export interface FeishuDocImportCandidate {
  token: string;
  type: string;
  title: string;
  url: string;
  ownerName?: string | null;
  updatedAt?: string | null;
  source: 'search' | 'link';
}

export interface FeishuDocImportSearchResult {
  items: FeishuDocImportCandidate[];
  message: string;
}

export interface FeishuDocImportImportedItem {
  token: string;
  title: string;
  status: 'imported' | 'failed';
  documentId?: string | null;
  fileName?: string | null;
  path?: string | null;
  remoteUrl: string;
  message: string;
}

export interface FeishuDocImportResult {
  clientId: string;
  importedCount: number;
  failedCount: number;
  items: FeishuDocImportImportedItem[];
}

export interface TopicRadar {
  id: string;
  title: string;
  prompt: string;
  timeRange: string;
  preferredSources: TopicRadarPreferredSource[];
  createdAt: string;
}

export interface IntelligenceProfileBackgroundEnrichment {
  id?: string;
  title: string;
  sourceUrl?: string | null;
  status?: string | null;
}

export interface IntelligenceProfileFetchSummary {
  status?: string | null;
  failureReason?: string | null;
  completedAt?: string | null;
  createdCount?: number;
  weakSignalCount?: number;
  backgroundEnrichmentCount?: number;
}

export interface IntelligenceProfile {
  id: string;
  title: string;
  radarId?: string | null;
  radarTitle?: string | null;
  profileKind?: 'auto' | 'custom';
  scopeType?: 'organization' | 'client' | 'project_module' | string | null;
  scopeId?: string | null;
  clientId?: string | null;
  projectModuleId?: string | null;
  status?: string | null;
  profileReadiness?: string | null;
  summary: string;
  effectiveSummary?: string | null;
  adminSummaryOverride?: string | null;
  adminFocus: string[];
  adminExcludeTerms: string[];
  adminPriorityUrls: string[];
  adminProfileRefreshEnabled: boolean;
  adminProfileRefreshFrequency: 'manual' | 'daily' | 'weekly' | 'workday' | string;
  adminPushEnabled: boolean;
  adminPushFrequency: 'manual' | 'daily' | 'weekly' | 'workday' | string;
  materialCount?: number;
  materialSummary: string[];
  workContext: string[];
  priorityNeeds: string[];
  targetBeneficiaries: string[];
  regions: string[];
  opportunityTypes: string[];
  materialGaps: string[];
  groundingFacts: string[];
  backgroundEnrichments: IntelligenceProfileBackgroundEnrichment[];
  lastFetch?: IntelligenceProfileFetchSummary | null;
  nextProfileRefreshAt?: string | null;
  nextIntelligenceFetchAt?: string | null;
  lastAutomationResult?: string | null;
  deletedAt?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface IntelligenceProfileMutationPayload {
  title?: string;
  summary?: string;
  focus?: string[];
  excludeTerms?: string[];
  priorityUrls?: string[];
  profileRefreshEnabled?: boolean;
  profileRefreshFrequency?: string;
  pushEnabled?: boolean;
  pushFrequency?: string;
}

export interface TopicRadarPreferredSource {
  url: string;
  label: string;
}

export interface TopicCandidate {
  id: string;
  radarId: string;
  title: string;
  summary: string;
  source: string;
  sourceUrl?: string | null;
  publishedAt?: string | null;
  captureMethod: string;
  capturedBy?: string | null;
  status: TopicCandidateStatus;
  evidenceStatus?: 'none' | 'candidate' | 'accepted' | 'rejected' | string | null;
  primaryBadge?: string | null;
  insightStatus: TopicCandidateInsightStatus;
  insightUpdatedAt?: string | null;
  deepAnalysis?: Record<string, unknown>;
  convertedTaskId?: string | null;
  contentKind?: string | null;
  whyRecommended?: string | null;
  relevanceReason?: string | null;
  suggestedAction?: string | null;
  recommendationBasis?: string[];
  groundingFactRefs?: string[];
  scopeType?: string | null;
  scopeId?: string | null;
  clientId?: string | null;
  projectModuleId?: string | null;
  createdAt: string;
}

export interface TopicCandidateInsight {
  candidateId: string;
  overview: string;
  keyPoints: string[];
  recommendationReasons: string[];
  practicalUses: string[];
  editorialNote: string;
  discussionPrompts: string[];
  advisorMemo?: string;
  createdAt: string;
  updatedAt: string;
}

export interface TopicCandidateChatMessage {
  role: 'user' | 'assistant';
  content: string;
  createdAt: string;
}

export interface TopicCandidateChatPayload {
  question: string;
  history?: TopicCandidateChatMessage[];
}

export interface TopicCandidateChatResponse {
  candidateId: string;
  question: string;
  answer: string;
  generatedAt: string;
  message: TopicCandidateChatMessage;
}

export interface TopicCaptureRun {
  radarId: string;
  radarTitle: string;
  query: string;
  fetchedCount: number;
  createdCount: number;
  skippedCount: number;
  candidates: TopicCandidate[];
}

export interface TopicCaptureBatchResult {
  runs: TopicCaptureRun[];
  totalCreated: number;
  totalSkipped: number;
}

export interface TopicTaskSuggestion {
  title: string;
  desc: string;
  dueDate?: string | null;
  ddl: string;
  note: string;
  priority: Priority;
  tags: string[];
}

export interface TopicTaskPlanResult {
  candidateId: string;
  candidateTitle: string;
  candidateSummary: string;
  candidateSource: string;
  candidateSourceUrl?: string | null;
  overview: string;
  tasks: TopicTaskSuggestion[];
}

export interface TopicTaskPromotionDraft {
  title: string;
  desc: string;
  priority: Priority;
  listId: string;
  dueDate?: string | null;
  ddl: string;
  ownerId?: string | null;
  ownerName: string;
  collaboratorIds: string[];
  tagIds?: string[];
  eventLineId?: string | null;
  tags: string[];
  note: string;
  ownerRecipient?: { userId: string; fullName: string; email?: string | null } | null;
  collaboratorRecipients?: { userId: string; fullName: string; email?: string | null }[];
  actorId?: string | null;
  actorName?: string | null;
  autoShare?: boolean;
}

export interface TopicTaskPromotionResult {
  tasks: Task[];
  createdCount: number;
  flowbackResults?: string[];
  warnings?: string[];
}

export interface AnalysisTemplate {
  id: string;
  title: string;
  description: string;
  templateKey: string;
}

export interface AnalysisRun {
  id: string;
  templateId: string;
  title: string;
  inputText: string;
  output: AiStructuredResponse;
  parentRunId?: string | null;
  coachPayload?: CoachPayload | null;
  createdAt: string;
  status: 'success' | 'failed';
}

export interface HandbookEntry {
  id: string;
  title: string;
  summary: string;
  tags: string[];
  sourceType: string;
  clientName?: string | null;
  clientId?: string | null;
  authorUserId?: string | null;
  authorUserName?: string | null;
  sourceObjectType?: string | null;
  sourceObjectId?: string | null;
  sourceTitle?: string | null;
  eventLineId?: string | null;
  eventLineName?: string | null;
  projectModuleId?: string | null;
  projectModuleName?: string | null;
  projectFlowId?: string | null;
  projectFlowName?: string | null;
  projectStage?: string | null;
  businessCategory?: string | null;
  abilityKeys: GrowthAbilityKey[];
  evidenceRefs: string[];
  contextSummary: string;
  reuseCount: number;
  lastReusedAt?: string | null;
  linkedContexts: GrowthContextLink[];
  createdAt: string;
}

export interface GrowthContextLink {
  objectType: string;
  objectId: string;
  label: string;
  subtitle: string;
  tab: string;
  statusLabel: string;
}

export interface HandbookEntryDetail extends HandbookEntry {
  relatedLedgerEntries: XpLedgerEntry[];
  originContexts: GrowthContextLink[];
  reuseHistory: HandbookReuseRecord[];
}

export interface GrowthExperienceWallItem {
  id: string;
  source: 'handbook' | 'exp_wall';
  text: string;
  summary: string;
  authorUserId?: string | null;
  authorUserName?: string | null;
  clientId?: string | null;
  clientName?: string | null;
  sourceType: string;
  sourceObjectId: string;
  sourceTitle?: string | null;
  category: string;
  reuseCount: number;
  likeCount: number;
  saveCount: number;
  currentUserLiked?: boolean;
  linkedContexts: GrowthContextLink[];
  createdAt: string;
}

export interface GrowthExperienceWallResponse {
  items: GrowthExperienceWallItem[];
  refreshedFromCloud: boolean;
  cloudSyncError?: string | null;
}

export interface HandbookReuseRecord {
  id: string;
  sourceType: string;
  sourceId: string;
  sourceLabel: string;
  note: string;
  contextSummary: string;
  gainedXp: number;
  createdAt: string;
  linkedContexts: GrowthContextLink[];
}

export interface XpLedgerEntry {
  id: string;
  userId: string;
  userName: string;
  abilityKey: GrowthAbilityKey;
  abilityLabel: string;
  evidenceId: string;
  xpType: GrowthEvidenceType;
  delta: number;
  baseXp: number;
  premiumRate: number;
  premiumXp: number;
  totalXp: number;
  reason: string;
  sourceType: string;
  sourceId: string;
  sourceTitle?: string | null;
  handbookEntryId?: string | null;
  taskId?: string | null;
  meetingId?: string | null;
  reviewId?: string | null;
  clientId?: string | null;
  clientName?: string | null;
  eventLineId?: string | null;
  eventLineName?: string | null;
  businessCategory?: string | null;
  projectStage?: string | null;
  sourceRoute: string[];
  evidenceRefs: string[];
  contextSummary: string;
  strategicLink?: string | null;
  linkedContexts: GrowthContextLink[];
  contributionTags: GrowthContributionTag[];
  validationState: GrowthValidationState;
  orgContributionScore: number;
  weekLabel: string;
  createdAt: string;
  reversedAt?: string | null;
}

export interface GrowthAbilityScore {
  abilityKey: GrowthAbilityKey;
  label: string;
  currentScore: number;
  previousScore: number;
  totalXp: number;
  weeklyXp: number;
  stage: string;
  nextStage: string;
  evidence: string;
}

export interface GrowthRank {
  key: string;
  name: string;
  division?: string | null;
  fullLabel: string;
  progress: number;
  nextName?: string | null;
  xpToNext: number;
}

export interface LearningRecommendation {
  id: string;
  userId: string;
  userName: string;
  abilityKey: GrowthAbilityKey;
  abilityLabel: string;
  contentItemId: string;
  contentType: LearningContentType;
  title: string;
  summary: string;
  body: string;
  practiceTask: string;
  reason: string;
  linkedTaskId?: string | null;
  clientId?: string | null;
  clientName?: string | null;
  eventLineId?: string | null;
  eventLineName?: string | null;
  projectStage?: string | null;
  triggerNode?: string | null;
  whyNow: string;
  linkedContexts: GrowthContextLink[];
  priority: 'high' | 'normal' | 'low';
  status: LearningRecommendationStatus;
  acceptedTaskId?: string | null;
  dismissedReason?: string | null;
  dedupeKey: string;
  createdAt: string;
  updatedAt: string;
}

export interface GrowthCompanionSummary {
  state: 'ready' | 'base_mode' | 'failed_retryable';
  weeklySummary: string;
  patterns: string[];
  blindSpots: string[];
  suggestions: string[];
  sourceCount: number;
  generatedAt: string;
  modelName?: string | null;
}

export interface GrowthEvidenceItem {
  evidenceId: string;
  summary: string;
  category: string;
  sourceType: string;
  sourceId: string;
  sourceVersion: number;
  version: number;
  createdAt: string;
}

export interface GrowthOverview {
  userId: string;
  userName: string;
  totalXp: number;
  weeklyXp: number;
  weeklyBaseXp: number;
  weeklyPremiumXp: number;
  level: number;
  stageLabel: string;
  xpToNext: number;
  rank: GrowthRank;
  abilities: GrowthAbilityScore[];
  recentEntries: XpLedgerEntry[];
  recommendations: LearningRecommendation[];
  companionSummary?: GrowthCompanionSummary | null;
  evidenceItems?: GrowthEvidenceItem[];
  sourceCoverage: GrowthSourceCoverage;
  socialFeedback?: GrowthSocialFeedback;
  abilityTrends?: GrowthAbilityTrend[];
  dailyActivity?: GrowthDailyActivityResponse;
  commitmentSummary?: GrowthCommitmentSummary;
  businessCoverage?: GrowthBusinessCoverage;
  reviewStreak?: GrowthReviewStreak;
  workTypeDistribution?: GrowthWorkType;
  impactCurve?: GrowthImpact;
  learning?: GrowthLearning;
  peerComparison?: GrowthPeerComparison;
  projectGrowthHighlights: GrowthProjectHighlight[];
  eventLineGrowthHighlights: GrowthProjectHighlight[];
  strategicAlignmentHighlights: GrowthProjectHighlight[];
  pendingCaptures: GrowthPendingCapture[];
  currentFocusActions: GrowthFocusAction[];
  abilityGaps: GrowthAbilityGap[];
  updatedAt: string;
}

export interface GrowthSourceCoverage {
  taskSignals: number;
  meetingSignals: number;
  strategicSignals: number;
  reviewSignals: number;
  handbookSignals: number;
  expWallSignals?: number;
  memorySignals?: number;
  documentSignals?: number;
  clientCount: number;
  eventLineCount: number;
}

export interface GrowthSocialFeedback {
  handbookReuseCount: number;
  handbookEntriesReused: number;
  expWallLikeCount: number;
  expWallSaveCount: number;
  expWallQuoteCount: number;
  periodLabel: string;
}

export interface GrowthAbilityTrendPoint {
  weekLabel: string;
  score: number;
  totalXp: number;
}

export interface GrowthAbilityTrend {
  abilityKey: string;
  label: string;
  points: GrowthAbilityTrendPoint[];
  scoreDelta: number;
  direction: 'up' | 'down' | 'flat';
}

export interface GrowthDailyActivity {
  date: string;
  count: number;
  level: number;
}

export interface GrowthDailyActivityResponse {
  days: GrowthDailyActivity[];
  totalDays: number;
  activeDays: number;
  maxStreak: number;
}

export interface GrowthCommitmentTrendPoint {
  weekLabel: string;
  totalCount: number;
  fulfilledCount: number;
  rate: number;
}
export interface GrowthCommitmentItem {
  id: string;
  content: string;
  recipient: string;
  deadline: string | null;
  status: string;
  daysOverdue: number;
}
export interface GrowthCommitmentCumulativePoint {
  weekIndex: number;
  weekLabel: string;
  currentCumulative: number;
  previousCumulative: number;
}

export interface GrowthCommitmentSummary {
  totalCount: number;
  fulfilledCount: number;
  pendingCount: number;
  overdueCount: number;
  rate: number;
  trend: GrowthCommitmentTrendPoint[];
  upcomingPending: GrowthCommitmentItem[];
  currentStreakDays?: number;
  longestStreakDays?: number;
  monthlyFulfilledCount?: number;
  lastMonthFulfilledCount?: number;
  growthPercent?: number;
  cumulativeCurve?: GrowthCommitmentCumulativePoint[];
}

export interface GrowthBusinessCoverageItem {
  label: string;
  taskCount: number;
  documentCount: number;
  glossaryTermCount: number;
  score: number;
}
export interface GrowthBusinessCoverage {
  items: GrowthBusinessCoverageItem[];
  coveredClients: number;
  coveredProjects: number;
}

export interface GrowthReviewWeekPoint {
  weekLabel: string;
  entryCount: number;
  charCount: number;
}
export interface GrowthReviewDayPoint {
  date: string;
  entryCount: number;
  charCount: number;
}
export interface GrowthReviewStreak {
  currentStreakWeeks: number;
  maxStreakWeeks: number;
  totalReviewWeeks: number;
  lastReviewedWeekLabel: string;
  monthlyEntryCount?: number;
  lastMonthEntryCount?: number;
  entryGrowthPercent?: number;
  monthlyCharCount?: number;
  lastMonthCharCount?: number;
  charGrowthPercent?: number;
  weeklyTrend?: GrowthReviewWeekPoint[];
  dailyTrend?: GrowthReviewDayPoint[];
}

export interface GrowthWorkTypeSlice {
  label: string;
  count: number;
}
export interface GrowthWorkType {
  slices: GrowthWorkTypeSlice[];
  totalTasks: number;
  unlabeledTasks: number;
}

export interface GrowthImpactCurvePoint {
  monthLabel: string;
  cumulativeReuses: number;
  cumulativeLikes: number;
  cumulativeSaves: number;
}
export interface GrowthImpact {
  points: GrowthImpactCurvePoint[];
  totalReuses: number;
  totalLikes: number;
  totalSaves: number;
}

export interface GrowthLearningPick {
  source: string;
  sourceId: string;
  title: string;
  detail: string;
  authorName: string;
  matchedAbility: string;
  matchedAbilityLabel: string;
  reusedCount: number;
  likedCount: number;
  savedCount: number;
}
export interface GrowthLearning {
  internalPicks: GrowthLearningPick[];
  githubPicks: GrowthLearningPick[];
  frontierPicks: GrowthLearningPick[];
  weakestAbilities: string[];
  externalEnabled: boolean;
  externalConfigHint: string;
}

export interface GrowthPeerComparison {
  roleLabel: string;
  peerCount: number;
  rank: number;
  yourTotalXp: number;
  peerMedianXp: number;
  peerTopXp: number;
  perAbilityRank: Record<string, number>;
}

export interface GrowthProjectHighlight {
  id: string;
  label: string;
  type: string;
  weeklyXp: number;
  entryCount: number;
  summary: string;
  abilityKeys: GrowthAbilityKey[];
  contexts: GrowthContextLink[];
}

export interface GrowthPendingCapture {
  id: string;
  sourceType: string;
  sourceId: string;
  status: GrowthPendingCaptureState;
  title: string;
  summary: string;
  clientId?: string | null;
  clientName?: string | null;
  eventLineId?: string | null;
  eventLineName?: string | null;
  projectStage?: string | null;
  nextActionText: string;
  missingReasons: string[];
  abilityKeys: GrowthAbilityKey[];
  linkedContexts: GrowthContextLink[];
  stateReason: string;
  promotedHandbookEntryId?: string | null;
  updatedAt: string;
}

export interface GrowthFocusAction {
  id: string;
  title: string;
  summary: string;
  whyNow: string;
  linkedTaskId?: string | null;
  clientId?: string | null;
  clientName?: string | null;
  eventLineId?: string | null;
  eventLineName?: string | null;
  projectStage?: string | null;
  triggerNode?: string | null;
  linkedContexts: GrowthContextLink[];
}

export interface GrowthAbilityGap {
  abilityKey: GrowthAbilityKey;
  label: string;
  currentScore: number;
  requiredScore: number;
  gap: number;
  reason: string;
  sourceLabel: string;
  sourceType: string;
  sourceId: string;
}

export interface GrowthTaskIntent {
  taskKind: string;
  goal: string;
  deliverable: string;
  riskTypes: string[];
  requiredAbilities: GrowthAbilityKey[];
  confidence: number;
  whyRelevant: string;
}

export interface GrowthUniversalSkillItem {
  id: string;
  cardType: '动作卡' | '检查卡' | '话术卡' | '模板卡';
  title: string;
  summary: string;
  whyRelevant: string;
  checklist: string[];
  talkTrack: string[];
  templateHint: string;
  sourceKind: 'rule' | 'project_context' | 'ai_supplement';
  expectedOutput: string;
  linkedContext?: GrowthContextLink | null;
}

export interface GrowthProjectContextPack {
  title: string;
  taskNotes: string[];
  attachments: string[];
  memoryHints: string[];
  linkedFacts: string[];
  clientSummary: string;
  recentMeetings: string[];
  eventLineSummary: string;
  strategicFocus: string[];
  keyWarnings: string[];
  contextGaps: string[];
}

export interface GrowthActionPlanItem {
  id: string;
  phaseGroup: 'before' | 'during' | 'after';
  title: string;
  purpose: string;
  expectedOutput: string;
  ifMissing: string;
  actionLabel: string;
  sourceKind: 'rule' | 'project_context' | 'ai_supplement';
  linkedContext?: GrowthContextLink | null;
}

export interface GrowthMaterialRef {
  id: string;
  title: string;
  summary: string;
  sourceKind: 'task_material' | 'project_context' | 'client_workspace' | 'event_line' | 'strategic_focus' | 'rule' | 'ai_supplement';
  linkedContext?: GrowthContextLink | null;
}

export interface GrowthWorkbenchStep {
  id: string;
  name: string;
  output: string;
  bottlenecks: string[];
}

export interface GrowthWorkbenchTask {
  id: string;
  title: string;
  project: string;
  clientName?: string | null;
  eventLineName?: string | null;
  deadline: string;
  urgency: string;
  urgencyColor: string;
  phase: string;
  risks: string[];
  nextAdvice: string;
  robotReady: boolean;
  robotReasons: string[];
  recommendationId?: string | null;
  linkedTaskId?: string | null;
  linkedContexts: GrowthContextLink[];
  xpReward: number;
  contextSummary: string;
  projectModuleName?: string | null;
  projectFlowName?: string | null;
  projectStage?: string | null;
  businessCategory?: string | null;
  sourceEvidence: string[];
  currentBlocker?: string | null;
  missingSignals: string[];
  hasBackground: boolean;
  hasDeadline: boolean;
  isCrossDepartment: boolean;
  needsReview: boolean;
  evidenceCount: number;
  pendingCollaborations: number;
  taskIntent: GrowthTaskIntent;
  universalSkills: GrowthUniversalSkillItem[];
  projectContextPack: GrowthProjectContextPack;
  actionPlan: GrowthActionPlanItem[];
  materialRefs: GrowthMaterialRef[];
}

export interface GrowthWorkbenchAction {
  id: string;
  title: string;
  output: string;
  scenario: string;
  actionLabel: string;
  supportTitle: string;
  detail: string;
  kind: 'schedule' | 'support' | 'process' | 'compose' | 'task';
  recommendationId?: string | null;
  linkedContext?: GrowthContextLink | null;
  seedTitle?: string | null;
  seedSummary?: string | null;
}

export interface GrowthWorkbenchMaterial {
  id: string;
  title: string;
  type: '流程说明' | '经验案例' | '模板工具';
  scenario: string;
  summary: string;
  linkedContext?: GrowthContextLink | null;
}

export interface GrowthLearningSummary {
  headline: string;
  whyItMatters: string;
  immediateMove: string;
  generator: 'rules' | 'ai';
  confidence: GrowthConfidence;
}

export interface GrowthGenericLesson {
  id: string;
  title: string;
  judgment: string;
  applicableScene: string;
  whyItWorks: string;
  reuseHint: string;
  linkedContext?: GrowthContextLink | null;
}

export interface GrowthProjectGuidance {
  id: string;
  title: string;
  judgment: string;
  whySpecial: string;
  guidanceType: 'project_specific' | 'stage_risk' | 'context_gap';
  linkedContexts: GrowthContextLink[];
  evidenceRefs: string[];
}

export interface GrowthReasoningInput {
  id: string;
  sourceType: 'task' | 'event_line' | 'client' | 'project_module' | 'project_flow' | 'focus_action' | 'pending_capture' | 'recommendation' | 'rule';
  label: string;
  detail: string;
}

export interface GrowthReasoningTrace {
  mode: 'rules_only' | 'ai_synthesized';
  usedInputs: GrowthReasoningInput[];
  evidenceRefs: string[];
  missingContext: string[];
  aiContribution: string[];
  modelLabel?: string | null;
  confidence: GrowthConfidence;
}

export interface GrowthRobotAssist {
  ready: boolean;
  canDelegate: string[];
  mustStayHuman: string[];
  why: string[];
}

export interface GrowthAfterActionCapture {
  title: string;
  summary: string;
  experienceType: string;
  recommendedWriteback: string;
}

export interface GrowthWorkbenchSupportCopy {
  title: string;
  intro: string;
  bullets: string[];
}

export interface GrowthWorkbenchSnapshot {
  tasks: GrowthWorkbenchTask[];
  activeTaskId?: string | null;
  learningSummary: GrowthLearningSummary;
  genericLessons: GrowthGenericLesson[];
  projectGuidance: GrowthProjectGuidance[];
  reasoningTrace: GrowthReasoningTrace;
  robotAssist: GrowthRobotAssist;
  afterActionCapture: GrowthAfterActionCapture;
  processSteps: GrowthWorkbenchStep[];
  activeProcessId?: string | null;
  actionsBefore: GrowthWorkbenchAction[];
  actionsDuring: GrowthWorkbenchAction[];
  actionsAfter: GrowthWorkbenchAction[];
  supportMaterials: GrowthWorkbenchMaterial[];
  checklistItems: string[];
  supportCopy: GrowthWorkbenchSupportCopy;
  robotPlan: string[];
  sourceMode: 'task' | 'growth_seed' | 'empty';
  scopeMode?: 'global' | 'strategic';
  scopeClientId?: string | null;
  scopeClientName?: string | null;
  updatedAt: string;
}

export interface GrowthLedgerResponse {
  entries: XpLedgerEntry[];
}

export interface GrowthRecommendationDismissPayload {
  reason?: string;
}

export interface GrowthRecommendationActionResponse {
  recommendation: LearningRecommendation;
  task?: Task | null;
}

export interface BadgeActionLink {
  label: string;
  tab: string;
}

export interface BadgeEvidence {
  id: string;
  title: string;
  sourceType: string;
  sourceId: string;
  subtitle: string;
  occurredAt: string;
}

export interface BadgeProgress {
  id: string;
  code: string;
  name: string;
  categoryId: string;
  categoryLabel: string;
  abilityKey: GrowthAbilityKey;
  abilityLabel: string;
  roles: string[];
  xp: number;
  iconMotif: string;
  description: string;
  whyItMatters: string;
  systemHowText: string;
  state: BadgeState;
  progressValue: number;
  progressTarget: number;
  progressPercent: number;
  progressText: string;
  nextActionText: string;
  actionLinks: BadgeActionLink[];
  evidence: BadgeEvidence[];
  linkedContexts: GrowthContextLink[];
  missingSignals: string[];
  unlockedAt?: string | null;
  masteryLevel: number;
  historical: boolean;
}

export interface BadgeCategory {
  id: string;
  label: string;
  abilityKey: GrowthAbilityKey;
  abilityLabel: string;
  litCount: number;
  totalCount: number;
  badges: BadgeProgress[];
}

export interface BadgeBoardOverview {
  totalBadges: number;
  litBadges: number;
  readyBadges: number;
  inProgressBadges: number;
  monthlyNewBadges: number;
  totalXp: number;
  upcomingBadgeIds: string[];
}

export interface BadgeBoard {
  overview: BadgeBoardOverview;
  categories: BadgeCategory[];
  updatedAt: string;
}

export interface GrowthValidationPayload {
  note?: string;
  sourceType?: string;
  sourceId?: string | null;
  sourceLabel?: string;
  contextSummary?: string;
  linkedContexts?: GrowthContextLink[];
}

export interface GrowthPendingCaptureActionPayload {
  status: GrowthPendingCaptureState;
  reason?: string;
  handbookEntryId?: string | null;
}

export interface GrowthPendingCaptureActionResponse {
  capture: GrowthPendingCapture;
}

export interface GrowthValidationActionResponse {
  entryId: string;
  eventType: 'handbook_reused';
  gainedXp: number;
  createdEntries: number;
  validationState: GrowthValidationState;
  duplicate: boolean;
  sourceId: string;
  createdAt: string;
}

export interface ClientAnalysisEvidenceSummary {
  summaryText: string;
  masterHitCount: number;
  surrogateHitCount: number;
  rawChunkHitCount: number;
  drillthroughUsed: boolean;
  coveredCategories: string[];
  missingCategories: string[];
  evidenceList: KnowledgeSearchHit[];
}

export interface ClientAnalysisRun {
  id: string;
  clientId: string;
  threadId: string;
  userMessageId: string;
  assistantMessageId: string;
  question: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'canceled';
  phase: 'queued' | 'retrieving' | 'evidence_ready' | 'generating_long_answer' | 'generating_summary' | 'completed' | 'failed' | 'canceled';
  progress: number;
  progressFloor: number;
  progressCeiling: number;
  stageLabel?: string | null;
  elapsedMs: number;
  evidenceSummary: ClientAnalysisEvidenceSummary;
  longAnswerStatus: 'pending' | 'ready' | 'fallback' | 'failed';
  summaryStatus: 'pending' | 'ready' | 'fallback' | 'failed';
  longAnswer?: string | null;
  structuredSummary?: AiStructuredResponse | null;
  answerMode?: 'grounded_answer' | 'grounded_fallback' | 'low_confidence_answer' | 'general_answer' | 'system_failure' | null;
  llmInvoked: boolean;
  providerUsed?: string | null;
  failureReason?: string | null;
  timing: Record<string, number>;
  assistantMessage?: ChatMessage | null;
  createdAt: string;
  updatedAt: string;
}

export interface AnalysisJobCreatePayload {
  jobType: AnalysisJobType;
  clientId: string;
  scopeType?: AnalysisScopeType;
  scopeId: string;
  priority?: Priority;
  triggerType?: string;
  intentProfile?: AnalysisIntentProfile;
  question?: string;
  sourceScope?: Record<string, string[]>;
  featureFlags?: Record<string, boolean>;
}

export interface AnalysisJob {
  id: string;
  jobType: AnalysisJobType;
  clientId: string;
  scopeType: AnalysisScopeType;
  scopeId: string;
  status: AnalysisJobStatus;
  priority: Priority;
  triggerType: string;
  intentProfile: AnalysisIntentProfile;
  question: string;
  sourceSnapshot: string;
  sourceSnapshotHash: string;
  dedupeKey: string;
  featureFlags: Record<string, boolean>;
  progress: number;
  stageLabel?: string | null;
  runLogId?: string | null;
  error?: string | null;
  lockedBy?: string | null;
  lockedAt?: string | null;
  lockExpiresAt?: string | null;
  attemptCount: number;
  lastError?: string | null;
  createdAt: string;
  updatedAt: string;
  startedAt?: string | null;
  finishedAt?: string | null;
}

export interface AnalysisJobStageRun {
  id: string;
  jobId: string;
  stageName: string;
  status: AnalysisStageStatus;
  provider?: string | null;
  modelName?: string | null;
  lane: AnalysisLane;
  cacheKey?: string | null;
  cacheHit: boolean;
  degraded: boolean;
  evidenceCount: number;
  topicCount: number;
  conflictCount: number;
  contextTimeRange?: string | null;
  metrics: Record<string, number | string>;
  detail?: string | null;
  correlationId?: string | null;
  startedAt?: string | null;
  finishedAt?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface RuntimeRunLog {
  id: string;
  clientId: string;
  jobId?: string | null;
  analysisJobId?: string | null;
  stageRunId?: string | null;
  contextPackId?: string | null;
  judgmentVersionId?: string | null;
  correlationId?: string | null;
  provider?: string | null;
  model?: string | null;
  lane: AnalysisLane;
  cacheHit: boolean;
  degraded: boolean;
  documentCount: number;
  evidenceCount: number;
  conflictCount: number;
  contextTimeRange?: string | null;
  promptVersion?: string | null;
  schemaVersion?: string | null;
  summary: string;
  detail: Record<string, unknown>;
  createdAt: string;
}

export interface ThemeCluster {
  id: string;
  clientId: string;
  scopeType: AnalysisScopeType;
  scopeId: string;
  originType: AnalysisOriginType;
  authorityLevel: AnalysisAuthorityLevel;
  qualityTier: AnalysisQualityTier;
  themeKey: string;
  title: string;
  supportIds: string[];
  opposeIds: string[];
  gapSummary: string;
  latestChangeSummary: string;
  evidenceCount: number;
  version: number;
  createdAt: string;
  updatedAt: string;
}

export interface ConflictGroup {
  id: string;
  clientId: string;
  scopeType: AnalysisScopeType;
  scopeId: string;
  originType: AnalysisOriginType;
  authorityLevel: AnalysisAuthorityLevel;
  qualityTier: AnalysisQualityTier;
  conflictType: string;
  title: string;
  summary: string;
  evidenceIds: string[];
  unresolvedQuestionIds: string[];
  resolutionStatus: AnalysisReviewState;
  severity: 'low' | 'medium' | 'high';
  createdAt: string;
  updatedAt: string;
}

export interface OpenQuestion {
  id: string;
  clientId: string;
  scopeType: AnalysisScopeType;
  scopeId: string;
  originType: AnalysisOriginType;
  authorityLevel: AnalysisAuthorityLevel;
  qualityTier: AnalysisQualityTier;
  themeKey: string;
  question: string;
  reason: string;
  blockerLevel: 'low' | 'medium' | 'high';
  status: AnalysisReviewState;
  createdAt: string;
  updatedAt: string;
}

export interface ContextPack {
  id: string;
  clientId: string;
  jobId?: string | null;
  targetType: AnalysisScopeType;
  targetId: string;
  originType: AnalysisOriginType;
  authorityLevel: AnalysisAuthorityLevel;
  qualityTier: AnalysisQualityTier;
  supersedesId?: string | null;
  sourceSnapshotHash: string;
  staleReason?: AnalysisStaleReason | null;
  invalidatedBy?: string | null;
  promptVersion: string;
  sourceCount: number;
  evidenceCount: number;
  payload: Record<string, unknown>;
  staleAt?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface DnaDelta {
  id: string;
  clientId: string;
  dimension: string;
  previousVersion?: string | null;
  originType: AnalysisOriginType;
  authorityLevel: AnalysisAuthorityLevel;
  qualityTier: AnalysisQualityTier;
  supersedesId?: string | null;
  sourceSnapshotHash: string;
  staleReason?: AnalysisStaleReason | null;
  invalidatedBy?: string | null;
  proposedChange: string;
  summary: string;
  evidenceIds: string[];
  confidence: GrowthConfidence;
  status: AnalysisReviewState;
  contextPackId?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface DnaDeltaCreatePayload {
  clientId: string;
  dimension: string;
  proposedChange: string;
  summary?: string;
  evidenceIds?: string[];
  confidence?: GrowthConfidence;
  contextPackId?: string | null;
}

export interface JudgmentVersion {
  id: string;
  clientId: string;
  targetType: AnalysisScopeType;
  targetId: string;
  topic: string;
  version: number;
  status: AnalysisReviewState;
  originType: AnalysisOriginType;
  authorityLevel: AnalysisAuthorityLevel;
  qualityTier: AnalysisQualityTier;
  supersedesId?: string | null;
  sourceSnapshotHash: string;
  staleReason?: AnalysisStaleReason | null;
  invalidatedBy?: string | null;
  summary: string;
  evidenceIds: string[];
  contextPackId?: string | null;
  riskLevel: 'low' | 'medium' | 'high';
  confidence: GrowthConfidence;
  createdAt: string;
  updatedAt: string;
}

export interface JudgmentConfirmPayload {
  judgmentId: string;
  action: ApprovalDecision;
  note?: string;
}

export interface ApprovalDecisionPayload {
  targetType: ApprovalTargetType;
  targetId: string;
  decision: ApprovalDecision;
  comment?: string;
  policyType?: string;
  metadata?: Record<string, unknown>;
}

export interface ApprovalRecord {
  id: string;
  approvalTargetType: ApprovalTargetType;
  approvalTargetId: string;
  clientId: string;
  policyType: string;
  decision: ApprovalDecision;
  comment: string;
  decidedBy: string;
  decidedAt: string;
  metadata: Record<string, unknown>;
}

export interface ApprovalState {
  targetType: ApprovalTargetType;
  targetId: string;
  currentDecision?: ApprovalDecision | null;
  currentStatus?: AnalysisReviewState | null;
  lastApproval?: ApprovalRecord | null;
}

export interface ResolutionScope {
  scopeType: AnalysisScopeType;
  scopeId: string;
}

export interface ResolutionCandidate {
  objectId?: string | null;
  topic?: string | null;
  scopeType: AnalysisScopeType;
  scopeId: string;
  originType?: AnalysisOriginType | null;
  authorityLevel?: AnalysisAuthorityLevel | null;
  qualityTier?: AnalysisQualityTier | null;
  staleReason?: AnalysisStaleReason | null;
  status?: AnalysisReviewState | null;
  rejectedReason?: AnalysisRejectedReason | null;
}

export interface ResolutionTrace {
  selectedCandidate?: ResolutionCandidate | null;
  consideredCandidates: ResolutionCandidate[];
  requestedScope: ResolutionScope;
  resolvedScope?: ResolutionScope | null;
  writebackScope: ResolutionScope;
  fallbackUsed: boolean;
  fallbackReason?: string | null;
}

export interface JudgmentBundle {
  baselineJudgment?: JudgmentVersion | null;
  overlayDeltas: JudgmentVersion[];
  resolutionTrace: ResolutionTrace;
}

export interface AnalysisMigrationMetrics {
  windowDays: number;
  newObjectHitRate: number;
  fallbackRate: number;
  approvalBacklog: number;
  approvalLagHoursMedian: number;
  candidateReviewWarningCount: number;
  candidateReviewOverdueCount: number;
  newCandidateUnreviewed24h: number;
  candidateToApprovedConversionRate: number;
  staleApprovedJudgmentCount: number;
  resolverMismatchRate: number;
  pageBreakdown: Record<string, AnalysisMigrationMetricBucket>;
}

export interface AnalysisMigrationMetricBucket {
  newObjectHitRate: number;
  fallbackRate: number;
  resolverMismatchRate: number;
  totalRuns: number;
}

export interface AnalysisWorkerCounterSnapshot {
  claimCounts: Record<string, number>;
  lockContention: Record<string, number>;
  backfillThrottle: Record<string, number>;
}

export interface MainChainCanaryObservation {
  recordedAt: string;
  timeRange: string;
  clientCount: number;
  enqueuedJobs: number;
  completedJobs: number;
  failedJobs: number;
  newObjectHitRateBefore: number;
  newObjectHitRateAfter: number;
  fallbackRateBefore: number;
  fallbackRateAfter: number;
  resolverMismatchRateBefore: number;
  resolverMismatchRateAfter: number;
  approvalBacklog: number;
  approvalLagHoursMedian: number;
  claimCounts: Record<string, number>;
  lockContention: Record<string, number>;
  backfillThrottle: Record<string, number>;
  impactedRealtimeTasks: boolean;
  latestJudgmentsShadowOff: boolean;
  verdict: 'pass' | 'watch' | 'fail';
  conclusion: string;
}

export interface MainChainCanaryObservationPayload {
  timeRange?: string | null;
  clientCount?: number | null;
  enqueuedJobs?: number | null;
  completedJobs?: number | null;
  failedJobs?: number | null;
  newObjectHitRateBefore?: number | null;
  newObjectHitRateAfter?: number | null;
  fallbackRateBefore?: number | null;
  fallbackRateAfter?: number | null;
  resolverMismatchRateBefore?: number | null;
  resolverMismatchRateAfter?: number | null;
  approvalBacklog?: number | null;
  approvalLagHoursMedian?: number | null;
  claimCounts?: Record<string, number> | null;
  lockContention?: Record<string, number> | null;
  backfillThrottle?: Record<string, number> | null;
  impactedRealtimeTasks?: boolean | null;
  latestJudgmentsShadowOff?: boolean | null;
  verdict?: 'pass' | 'watch' | 'fail' | null;
  conclusion?: string | null;
}

export interface MainChainStabilitySettings {
  latestJudgmentsShadowOff: boolean;
  backfillPaused: boolean;
  workerCounters: AnalysisWorkerCounterSnapshot;
  lastCanaryObservation?: MainChainCanaryObservation | null;
  updatedAt: string;
}

export interface MainChainStabilitySettingsPayload {
  latestJudgmentsShadowOff?: boolean | null;
  backfillPaused?: boolean | null;
  lastCanaryObservation?: MainChainCanaryObservationPayload | null;
}

export interface AnalysisCenterSummary {
  clientId: string;
  evidenceCardCount: number;
  themeClusterCount: number;
  conflictGroupCount: number;
  openQuestionCount: number;
  draftJudgmentCount: number;
  approvedJudgmentCount: number;
  analysisJobCount: number;
  latestJobStatus?: AnalysisJobStatus | null;
  latestJobLabel?: string | null;
  latestContextPackUpdatedAt?: string | null;
  latestRunLogId?: string | null;
  latestRunSummary?: string | null;
}

export type ProjectKnowledgeSourceState =
  | 'ready'
  | 'empty'
  | 'blocked'
  | 'failed_retryable'
  | 'not_connected';

export interface ProjectKnowledgeMaterial {
  sourceScope: 'organization_shared' | 'local_private';
  sourceType: string;
  sourceId: string;
  sourceVersion: number;
  contentHash?: string | null;
  title?: string | null;
  summary: string;
  sourceDescription: string;
  updatedAt?: string | null;
  processingState?: string | null;
}

export interface ProjectKnowledgeContext {
  sandboxId: string;
  cloudInstanceId: string;
  organizationId: string;
  project: {
    projectId: string;
    name: string;
    summary: string;
    lifecycleState: string;
    version: number;
    updatedAt?: string | null;
  };
  organizationSharedKnowledge: ProjectKnowledgeMaterial[];
  localPrivateKnowledge: ProjectKnowledgeMaterial[];
  materialBoundary: {
    organizationSharedSource: 'strict_organization_cloud_v2';
    localPrivateSource: 'current_device_managed_storage';
    cloudSourceFileContentIncluded: false;
    cloudSourceFilePathsIncluded: false;
    localPrivateUploadedToOrganizationCloud: false;
    localSourcePathsIncludedInContext: false;
  };
  counts: {
    organizationShared: number;
    localPrivate: number;
    projectMetadata: number;
    localRetrievalReady: number;
    localMetadataOnly: number;
  };
  state: {
    overall: ProjectKnowledgeSourceState;
    organizationShared: ProjectKnowledgeSourceState;
    localPrivate: ProjectKnowledgeSourceState;
    organizationSharedMessage: string;
    localPrivateMessage: string;
  };
}

export interface ClientWorkspace {
  client: ClientSummary;
  folders: ClientFolder[];
  documents: DocumentRecord[];
  documentCards: DocumentCard[];
  imports: ImportRecord[];
  knowledgeStatus?: KnowledgeStatus | null;
  knowledgeJobs: KnowledgeJob[];
  recentReclassEvents: FileReclassEvent[];
  surrogateCount: number;
  memoryDocCount: number;
  memoryCards: KnowledgeMemoryRecord[];
  threads: ChatThread[];
  recentMessages: ChatMessage[];
  analysisRuns: ClientAnalysisRun[];
  meetings: MeetingSummary[];
  goals: GoalRecord[];
  dnaModules: ClientDnaModule[];
  projectModules: ProjectModule[];
  projectFlows: ProjectFlow[];
  dnaTerms: DnaTerm[];
  relatedTasks: Task[];
  notebookSummary?: OrganizationNotebookSnapshot | null;
  memoryStatus?: MemoryStatus | null;
  analysisCenter?: AnalysisCenterSummary | null;
  latestContextPack?: ContextPack | null;
  judgmentBundle?: JudgmentBundle | null;
  latestResolutionTrace?: ResolutionTrace | null;
  latestJudgments: JudgmentVersion[];
  latestTopics: ThemeCluster[];
  latestConflicts: ConflictGroup[];
  latestOpenQuestions: OpenQuestion[];
  latestRunLogs: RuntimeRunLog[];
  stateProjection?: WorkspaceStateProjection | null;
  knowledgeContext?: ProjectKnowledgeContext | null;
}

export interface AnalysisBackfillMainChainJob {
  clientId: string;
  scopeType: AnalysisScopeType;
  scopeId: string;
  jobType: AnalysisJobType;
  triggerType: string;
  intentProfile: AnalysisIntentProfile;
}

export interface AnalysisBackfillMainChainPayload {
  clientIds?: string[];
  dryRun?: boolean;
  batchSize?: number;
  maxJobs?: number;
  pauseRequested?: boolean;
}

export interface AnalysisBackfillMainChainResult {
  dryRun: boolean;
  pauseRequested: boolean;
  paused: boolean;
  scannedClients: number;
  queuedJobs: number;
  skippedJobs: number;
  candidates: AnalysisBackfillMainChainJob[];
}

export interface FileReclassEvent {
  id: string;
  knowledgeDocumentId: string;
  fromPath: string;
  toPath: string;
  fromCategory?: string | null;
  toCategory: string;
  reason: string;
  confidence: number;
  createdAt: string;
}

export interface KnowledgeJob {
  id: string;
  clientId: string;
  jobType: string;
  status: 'queued' | 'running' | 'interrupted' | 'completed' | 'failed';
  totalItems: number;
  processedItems: number;
  lastError?: string | null;
  currentItemLabel?: string | null;
  lastEventMessage?: string | null;
  recentEvents?: KnowledgeJobEvent[];
  queuedItemLabels?: string[];
  resumeDocumentIds?: string[];
  createdAt: string;
  startedAt?: string | null;
  finishedAt?: string | null;
  updatedAt: string;
}

export interface KnowledgeJobEvent {
  level: string;
  message: string;
  processedItems?: number | null;
  itemLabel?: string | null;
  createdAt: string;
}

export interface KnowledgeProgress {
  knowledgeStatus: KnowledgeStatus;
  knowledgeJobs: KnowledgeJob[];
}

export interface KnowledgeSearchHit {
  title: string;
  excerpt: string;
  score?: number | null;
  keywordScore?: number | null;
  semanticScore?: number | null;
  stage: 'master_index' | 'surrogate' | 'raw_chunk';
  sourceType?:
    | 'local_document'
    | 'organization_knowledge'
    | 'official_website'
    | 'explicit_memory'
    | 'favorite'
    | 'system_inference'
    | 'organization_summary';
  documentId?: string | null;
  knowledgeDocumentId?: string | null;
  documentVersionId?: string | null;
  chunkId?: string | null;
  factId?: string | null;
  evidenceId?: string | null;
  evidenceLocator?: string | null;
  path?: string | null;
  originalPath?: string | null;
  sourceAvailability?: string | null;
  originalAvailable?: boolean | null;
  machineReadableAvailable?: boolean | null;
  openableKind?: 'original_file' | 'machine_markdown' | 'system_card' | 'unknown' | string | null;
  sectionLabel?: string | null;
  matchedTerms: string[];
  retrievalMode?: 'keyword' | 'semantic' | 'hybrid' | string | null;
  citationRole?: 'direct_quote' | 'direct_support' | 'background' | string | null;
  citationPriority?: number | null;
  citationReason?: string | null;
}

export type KnowledgeSourceKey =
  | 'local_original'
  | 'organization_knowledge'
  | 'official_website'
  | 'explicit_memory'
  | 'favorite'
  | 'system_inference';

export interface KnowledgeSourceGroup {
  key: KnowledgeSourceKey;
  label: string;
  authority: string;
  state: string;
  count: number;
  message?: string | null;
}

export interface KnowledgeSavedMemory {
  id: string;
  documentVersionId?: string | null;
  sourceAnswerId?: string | null;
  title: string;
  summary: string;
  memoryKind: 'explicit_memory' | 'favorite' | 'system_inference' | string;
  contentHash?: string | null;
  publicationState?: string | null;
  version?: number;
  updatedAt?: string | null;
  authority: 'current_device' | 'organization_cloud' | string;
}

export interface KnowledgeMemorySyncStatus {
  clientId: string;
  organizationId: string;
  cloudInstanceId: string;
  localState: 'ready' | 'stale' | 'not_connected' | 'failed_retryable' | string;
  cloudState: 'ready' | 'not_connected' | 'failed_retryable' | string;
  overallState: 'ready' | 'not_connected' | 'failed_retryable' | string;
  conflictState: 'none' | 'not_checked' | 'conflict' | string;
  retryable: boolean;
  lastSyncedAt?: string | null;
  localSummary: {
    memoryCount: number;
    counts: {
      explicitMemory: number;
      favorite: number;
      correction: number;
    };
    memoryDigest: string;
    snapshotVersion: number;
  };
  cloudSummary: {
    memoryCount: number;
    counts?: {
      explicitMemory: number;
      favorite: number;
      correction: number;
    };
    memoryDigest?: string;
    manifestVersion?: number;
    state: string;
  };
  message: string;
  boundary: {
    l0ConversationIncluded: boolean;
    answerBodyIncluded: boolean;
    fileBodyIncluded: boolean;
    localPathIncluded: boolean;
    secretIncluded: boolean;
    sourceHashesIncluded: boolean;
  };
  generatorVersion: string;
  idempotentReplay?: boolean;
}

export interface KnowledgeRelationshipCard {
  id: string;
  subject: string;
  predicate: string;
  object: string;
  verificationState: string;
  confidence?: number | null;
  version: number;
  authority: 'current_device' | 'organization_cloud' | string;
}

export interface KnowledgePresentation {
  clientId: string;
  sourceGroups: KnowledgeSourceGroup[];
  savedMemories: KnowledgeSavedMemory[];
  relationshipCards: KnowledgeRelationshipCard[];
  organizationSharedState: string;
  organizationSharedMessage?: string | null;
  updatedAt: string;
}

export interface KnowledgeSearchResult {
  searchId: string;
  clientId: string;
  query: string;
  coverage: number;
  matchedTerms: string[];
  masterHitCount: number;
  surrogateHitCount: number;
  rawChunkHitCount: number;
  drillthroughUsed: boolean;
  strategicMode?: boolean;
  categoryCoverage?: string[];
  preferredCategories?: string[];
  phase?: 'retrieving' | 'grounding' | 'generating' | 'completed' | 'failed';
  progress?: number;
  progressFloor?: number;
  progressCeiling?: number;
  stageLabel?: string | null;
  lastUpdatedAt?: string | null;
  failureReason?: string | null;
  hits: KnowledgeSearchHit[];
  previewSummary?: string | null;
  strictState?: string | null;
  retrievalMode?: string | null;
  keywordIndexState?: string | null;
  semanticIndexState?: string | null;
  semanticModel?: string | null;
  organizationSharedState?: string | null;
  organizationSharedMessage?: string | null;
  sourceGroups?: KnowledgeSourceGroup[];
  savedMemories?: KnowledgeSavedMemory[];
  relationshipCards?: KnowledgeRelationshipCard[];
}

export interface DocumentReadingPreview {
  documentId: string;
  title: string;
  parseStatus: string;
  folderLabel?: string | null;
  sectionCount: number;
  chunkCount: number;
  sourceKind: string;
  readSummary: string;
  keyHeadings: string[];
  availableForChat: boolean;
  failureReason?: string | null;
}

export interface KnowledgeMemoryRecord {
  id: string;
  clientId: string;
  sourceType: string;
  title: string;
  folderCategory: string;
  surrogateMdPath: string;
  overviewSummary?: string;
  retrievalSummary?: string;
  documentRole?: string;
  sourceLinks?: Array<Record<string, unknown>>;
  createdAt: string;
  updatedAt: string;
  // C: 哪条 chat message 收藏出来的(用于"已收藏"识别和取消收藏)
  chatMessageId?: string | null;
  memoryKind?: 'explicit_memory' | 'favorite' | 'system_inference' | string;
  version?: number;
  status?: 'active' | 'archived' | 'deleted' | string;
}

export interface SettingsPayload {
  currentOperatorId?: string;
  cloudApiUrl?: string;
  aiProvider?: AiProvider;
  aiProviderLabel?: string;
  aiBaseUrl?: string;
  aiModel?: string;
  aiConfigVersion?: number;
  apiKey?: string;
  clearApiKey?: boolean;
  advancedAiRoutingEnabled?: boolean;
  aiModelMode?: AiModelMode;
  aiModelProfiles?: Partial<Record<AiModelProfileKey, AiModelProfileRecord>>;
  aiModelProfileApiKeys?: Partial<Record<AiModelProfileKey, string>>;
  clearAiModelProfileApiKeys?: AiModelProfileKey[];
}

export interface LegacyScanReport {
  path: string;
  found: string[];
  entries: Array<{
    path: string;
    kind: string;
    importable: boolean;
  }>;
  message: string;
}

export interface DemoDataReport {
  loaded: boolean;
  clients: number;
  documents: number;
  tasks: number;
  topics: number;
  handbookEntries: number;
}

export interface ClientMutationPayload {
  name: string;
  alias: string;
  domain: string;
  type: string;
  intro: string;
  stage: string;
  color?: string;
  // P7：扩展字段（后端 default 兼容旧调用方）
  relatedUserIds?: string[];
  isDataCenterIncluded?: boolean;
  officialWebsiteUrl?: string;
  expectedVersion?: number;
}

export interface TaskMutationPayload {
  expectedVersion?: number;
  title: string;
  desc: string;
  progressStatus?: TaskStatus;
  priority: Priority;
  listId: string;
  startDate?: string | null;
  dueDate?: string | null;
  durationMinutes?: number;
  deadlineAt?: string | null;
  scheduledStartAt?: string | null;
  scheduledEndAt?: string | null;
  // 任务提醒(2026-05-29): 0=准时 / 5=提前5分钟 / null|undefined=不提醒。相对 scheduledStartAt(无则 deadlineAt)。
  reminderMinutesBefore?: number | null;
  completedAt?: string | null;
  scopeMode?: TaskScopeMode;
  clientId?: string | null;
  eventLineId?: string | null;
  planningCycleId?: string | null;
  projectModuleId?: string | null;
  projectFlowId?: string | null;
  ddl: string;
  ownerId?: string | null;
  ownerName: string;
  collaboratorIds: string[];
  tagIds: string[];
  tags?: string[];
  sourceType?: string;
  sourceId?: string | null;
  businessCategory?: string | null;
  currentBlocker?: string | null;
  nextAction?: string | null;
  recentDecision?: string | null;
  evidenceCount?: number | null;
}

export interface AuthRegisterPayload {
  email: string;
  phone: string;
  fullName: string;
  password: string;
  cloudApiUrl?: string | null;
  organizationName?: string | null;
  inviteCode?: string | null;
  departmentId?: string | null;
  jobTitle?: string | null;
  managerName?: string | null;
  currentFocus?: string | null;
  isDepartmentLead?: boolean;
}

export interface AuthLoginPayload {
  email?: string;
  identifier?: string;
  password: string;
  rememberMe?: boolean;
  cloudApiUrl?: string | null;
}

export interface SelectOrganizationPayload {
  organizationSelectionToken: string;
  organizationId: string;
  cloudApiUrl?: string | null;
}

export interface CreateOrganizationPayload {
  organizationName: string;
  cloudApiUrl?: string | null;
}

export interface JoinOrganizationPayload {
  inviteCode: string;
  cloudApiUrl?: string | null;
  departmentId?: string | null;
  jobTitle?: string | null;
  managerName?: string | null;
  currentFocus?: string | null;
}

export interface LocalAuthRegisterPayload {
  email: string;
  phone: string;
  fullName: string;
  password: string;
  organizationMode?: 'create' | 'join';
  organizationName?: string | null;
  inviteCode?: string | null;
  departmentId?: string | null;
  jobTitle?: string | null;
  managerName?: string | null;
  currentFocus?: string | null;
}

export interface LocalAuthLoginPayload {
  identifier: string;
  password: string;
  rememberMe?: boolean;
}

export interface RememberedCloudAuthAccount {
  email: string;
  identifier?: string | null;
  fullName: string;
  password: string;
  updatedAt: string;
}

export interface LocalInputMemoryCloudAuth {
  rememberInputs: boolean;
  lastEmail?: string | null;
  accounts: RememberedCloudAuthAccount[];
}

export interface LocalInputMemoryAiSettings {
  rememberApiKey: boolean;
  apiKey: string;
}

export interface LocalInputMemoryFeishuIntegration {
  rememberInputs: boolean;
  appId: string;
  callbackMode: string;
  customCallbackUrl: string;
  appSecret: string;
}

export interface LocalInputMemory {
  cloudAuth: LocalInputMemoryCloudAuth;
  aiSettings: LocalInputMemoryAiSettings;
  feishuIntegration: LocalInputMemoryFeishuIntegration;
  authorityState?: 'ready' | 'not_connected' | 'blocked';
  authorityMessage?: string;
  publicPreferencePersisted?: boolean;
  secretPersistedLocally?: boolean;
  retryable?: boolean;
  credentialBoundary?: {
    cloudAuthPasswords: 'device';
    publicPreferenceCache: string;
    aiApiKey: string;
    feishuAppSecret: string;
  };
}

export interface SaveCloudAuthInputMemoryPayload {
  rememberInputs: boolean;
  email: string;
  identifier?: string | null;
  fullName?: string | null;
  password?: string | null;
}

export interface SaveAiInputMemoryPayload {
  rememberApiKey: boolean;
  apiKey?: string | null;
}

export interface SaveFeishuInputMemoryPayload {
  rememberInputs: boolean;
  appId?: string | null;
  callbackMode?: string | null;
  customCallbackUrl?: string | null;
  appSecret?: string | null;
}

export interface EmployeeRolePayload {
  role: EmployeeRole;
}

export interface EmployeeDepartmentPayload {
  departmentId?: string | null;
  departmentLead?: boolean;
}

export interface EmployeeManagementTitlePayload {
  managementTitleId?: string | null;
}

export interface AdminTransferPayload {
  targetUserId: string;
  currentAdminAction?: 'keep_admin' | 'demote_to_member' | 'disable_self';
  currentAdminDepartmentId?: string | null;
}

export interface EmployeeRejectPayload {
  reason: string;
}

export interface ChangePasswordPayload {
  currentPassword: string;
  newPassword: string;
}

export interface UpdateProfilePayload {
  fullName?: string;
  email?: string;
  phone?: string | null;
}

export interface OrgMembershipApplyPayload {
  inviteCode?: string | null;
  departmentId?: string | null;
  managementTitleId?: string | null;
  jobTitle?: string | null;
  managerName?: string | null;
  currentFocus?: string | null;
}

export interface OrgAdminClaimStatus {
  hasOrganization: boolean;
  organizationId?: string | null;
  organizationName?: string | null;
  hasAdmin: boolean;
  canClaim: boolean;
  reason?: string | null;
  currentUserRole?: EmployeeRole | null;
  currentUserMembershipStatus?: MembershipStatus | null;
}

export interface AdminResetPasswordPayload {
  newPassword: string;
}

export interface TaskTagSuggestionPayload {
  title: string;
  desc: string;
  collaboratorNames: string[];
  dueDate?: string | null;
  module: string;
}

export interface TaskTagMutationPayload {
  name: string;
  color?: string;
  scope: 'org' | 'self';
  archived?: boolean;
}

export interface TaskListMutationPayload {
  name: string;
  color: string;
  isDefault?: boolean;
  scope?: 'org' | 'personal';
  archived?: boolean;
  sortOrder?: number;
}

export interface TaskSettingsPayload {
  defaultListId?: string | null;
  defaultPriority?: Priority;
  defaultDueDatePreset?: TaskDueDatePreset;
  defaultViewMode?: TaskViewPreference;
  listSortMode?: TaskListSortMode;
  showCompletedTasks?: boolean;
  defaultReviewScope?: TaskReviewScope;
  autoAssignSelf?: boolean;
}

export interface OrganizationDnaUploadPayload {
  filePath?: string;
  markdownContent?: string;
  fileName?: string;
}

export interface OrgIntroDocumentUploadPayload {
  filePath?: string;
  markdownContent?: string;
  fileName?: string;
  title?: string;
}

export interface ProjectModulePayload {
  name: string;
  alias?: string | null;
  goal?: string | null;
  description?: string | null;
  ownerName?: string | null;
  deliverables?: string[];
  keywords?: string[];
  templateTasksJson?: string | null;
}

export interface ProjectFlowPayload {
  moduleId: string;
  name: string;
  description?: string | null;
  scenario?: string | null;
  triggerCondition?: string | null;
  steps?: string[];
  inputs?: string[];
  outputs?: string[];
  collaborators?: string[];
  riskPoints?: string[];
}

export interface ClientWorkspaceSettingsPayload {
  meetingPublishDefaultListId?: string | null;
  meetingPublishDefaultPriority?: Priority;
  defaultGoalQuarter?: string;
  defaultMeetingTitlePrefix?: string;
  clientDnaModeLabel?: string;
}

export interface TopicsSettingsPayload {
  chineseOnly?: boolean;
  requireInsightBeforeActions?: boolean;
  defaultTaskOwnerMode?: TopicTaskOwnerMode;
  defaultTimeRange?: string;
  defaultSourceStrategy?: string;
}

export interface AnalysisWorkbenchSettingsPayload {
  enabledTemplateIds?: string[];
  defaultTemplateId?: string | null;
  defaultTitlePrefix?: string;
  allowEmployeeTemplateEditing?: boolean;
  diagnosisProfiles?: DiagnosisProfileRecord[];
  organizationRiskDna?: OrganizationRiskDnaDocument | null;
  fundraisingKnowledgeLibrary?: FundraisingKnowledgeDocument[];
  deepDnaLibrary?: DeepDnaRecord[];
  coachCaseLibrary?: CoachCaseRecord[];
  coachReminderRules?: CoachReminderRule[];
  orgWritingNorms?: OrgWritingNorm[];
}

export interface HandbookSettingsPayload {
  defaultTags?: string[];
  defaultCategory?: string;
  allowTaskSource?: boolean;
  allowAnalysisSource?: boolean;
  visibilityBoundary?: string;
}

export interface SystemAdminSettingsPayload {
  allowBusinessSettingsForEmployees?: boolean;
  allowOrgDnaForEmployees?: boolean;
  protectEmployeeAdmin?: boolean;
  protectAiAndCloud?: boolean;
  protectCloudSecurity?: boolean;
}

export interface FeishuBotSettingsPayload {
  appId?: string;
  receiveIdType?: FeishuReceiveIdType;
  receiverId?: string;
  botName?: string;
  userBindingCallbackUrl?: string;
  appSecret?: string;
  clearAppSecret?: boolean;
  sendTestMessage?: boolean;
  testMessage?: string;
}

export interface WeeklyReviewPayload {
  weekLabel: string;
  taskEntries: Array<{
    taskId: string;
    contentDomain: 'work' | 'personal';
    note: string;
    structuredNote?: WeeklyReviewTaskStructuredNote;
    delete?: boolean;
  }>;
  workProgress?: string;
  workBlocker?: string;
  blockerType?: string;
  workDirection?: string;
  nextWeekFocus?: string;
  supportNeeded?: string;
  relatedPlanIds?: string[];
  workFreeNote?: string;
  personalGrowthNote?: string;
  personalPrivateNote?: string;
}

export interface TopicRadarPayload {
  title: string;
  prompt: string;
  timeRange: string;
  preferredSources: TopicRadarPreferredSource[];
}

export interface MeetingPipelineResult {
  meeting: MeetingDetail;
  message: string;
}

export interface TopicCandidatePayload {
  radarId: string;
  title: string;
  summary: string;
  source: string;
}

export interface HandbookEntryPayload {
  title: string;
  summary: string;
  tags: string[];
  sourceType: string;
  clientId?: string | null;
  sourceObjectType?: string | null;
  sourceObjectId?: string | null;
  sourceTitle?: string | null;
  eventLineId?: string | null;
  eventLineName?: string | null;
  projectModuleId?: string | null;
  projectModuleName?: string | null;
  projectFlowId?: string | null;
  projectFlowName?: string | null;
  projectStage?: string | null;
  businessCategory?: string | null;
  abilityKeys?: GrowthAbilityKey[];
  evidenceRefs?: string[];
  contextSummary?: string;
}

export interface AnalysisRunPayload {
  templateId: string;
  title: string;
  inputText: string;
  parentRunId?: string | null;
}

export type DiagnosisScene = 'fundraising' | 'pr' | 'project';
export type DiagnosisAudienceType = 'donor' | 'media' | 'public' | 'key_person' | 'beneficiary' | 'partner';
export type DiagnosisEngineMode = 'fast' | 'standard' | 'deep';

export interface DiagnosisContextReference {
  title: string;
  summary: string;
}

export interface ExternalDiagnosisRequest {
  scene: DiagnosisScene;
  audienceType: DiagnosisAudienceType;
  title: string;
  content: string;
  workspaceLabel?: string;
  modeLabel?: string;
  focusPoints?: string[];
  organizationContext?: {
    name?: string;
    mission?: string;
    projectType?: string;
    sensitivePoints?: string[];
  };
  dnaSummary?: {
    corePreferences?: string[];
    riskTriggers?: string[];
    tonePreference?: string;
  };
  knowledgeRefs?: DiagnosisContextReference[];
  caseRefs?: DiagnosisContextReference[];
  analysisOptions?: {
    engineMode: DiagnosisEngineMode;
    needEmotion?: boolean;
    needRiskPoints?: boolean;
    needMisunderstanding?: boolean;
    needSimulation?: boolean;
  };
}

export interface DiagnosisEngineHealth {
  engineKey: 'bettafish' | 'mirofish';
  enabled: boolean;
  reachable: boolean;
  status: 'healthy' | 'disabled' | 'not_configured' | 'not_installed' | 'unreachable' | 'error';
  detail: string;
  baseUrl: string;
  latencyMs?: number | null;
}

export interface BettaFishSignal {
  engineKey: 'bettafish';
  emotion: string;
  credibility: string;
  riskPoints: string[];
  misunderstandingPoints: string[];
  generatedAt: string;
  mode: DiagnosisEngineMode;
}

export interface DesktopAppInfo {
  appVersion: string;
  frontendBuildVersion?: string | null;
  frontendGitCommit?: string | null;
  bundleManifestId?: string | null;
  runtimeMode?: 'packaged' | 'dev';
  collabPreviewMode?: boolean;
  isPackaged: boolean;
  platform: string;
  arch: string;
  appBundlePath: string;
  executablePath: string;
  releasePlanPath: string;
  releaseArtifactsPath: string;
  cloudBackendUrl?: string | null;
  updateChannel: 'stable' | 'beta';
  updaterPhase: 'planning' | 'preparing_release' | 'ready_for_feed' | 'ready_for_in_app_update';
  recommendedInstallPath: string;
  installStatus: 'ok' | 'warning';
  installWarning: string | null;
  currentRendererEntry?: string | null;
  currentRendererHash?: string | null;
  backendSourceHash?: string | null;
  startupGateStatus?: 'ok' | 'warning' | 'blocked';
  startupGateReason?: string | null;
  installReceiptStatus?: 'ok' | 'missing' | 'mismatch';
  installSmokeStatus?: 'ok' | 'missing' | 'failed';
  detectedAppPaths: string[];
  legacyAppPaths: string[];
}

export interface DesktopStartupGateResumeResult {
  resumed: boolean;
  appInfo: DesktopAppInfo;
  loadMode: 'blocked' | 'dev' | 'http' | 'app' | 'error';
}

export type CollabChangeGroupKey =
  | 'shared_settings'
  | 'renderer'
  | 'desktop_shell'
  | 'local_backend'
  | 'cloud_backend'
  | 'scripts_docs'
  | 'other';

export type CollabFileChangeType = 'modified' | 'added' | 'deleted' | 'renamed' | 'untracked';

export type CollabConflictRiskKind = 'overlap' | 'unmerged' | 'binary' | 'rename' | 'delete_replace';

export interface CollabConflictRisk {
  kind: CollabConflictRiskKind;
  message: string;
}

export interface CollabFileChange {
  path: string;
  previousPath?: string | null;
  type: CollabFileChangeType;
  groupKey: CollabChangeGroupKey;
  groupLabel: string;
  summary: string;
  risk?: CollabConflictRisk | null;
}

export interface CollabChangeGroup {
  key: CollabChangeGroupKey;
  label: string;
  fileCount: number;
}

export type CollabEffectVisibility = 'visible' | 'mixed' | 'background';

export interface CollabEffectPreview {
  id: string;
  title: string;
  summary: string;
  visibility: CollabEffectVisibility;
  scopeLabel: string;
  details: string[];
  relatedPaths: string[];
  beforeLabel?: string | null;
  afterLabel?: string | null;
  explanationSource?: 'user_feature_rules';
}

export interface CollabRepoStatus {
  repoPath: string | null;
  repoName: string | null;
  suggestedRepoPath?: string | null;
  workingRepoPath?: string | null;
  workingBranch?: string | null;
  workingChangeCount?: number;
  permissionOnlyChangeCount?: number;
  isConfigured: boolean;
  isValid: boolean;
  branch: string | null;
  isMainBranch: boolean;
  hasLocalChanges: boolean;
  hasPermissionOnlyChanges?: boolean;
  hasUnmergedPaths: boolean;
  aheadCount: number;
  behindCount: number;
  localChangeCount: number;
  remoteChangeCount: number;
  statusText: string;
}

export interface PushPreview {
  status: CollabRepoStatus;
  suggestedMessage: string;
  effects: CollabEffectPreview[];
  groups: CollabChangeGroup[];
  files: CollabFileChange[];
  suggestedCollabBranchName?: string | null;
  notice?: string | null;
  executionBlockReason?: string | null;
}

export interface PullPreview {
  status: CollabRepoStatus;
  suggestedMessage: string;
  commitSummaries: string[];
  remoteCommits: CollabRemoteCommit[];
  remoteBranches?: CollabRemoteBranch[];
  syncTargetCommit?: string | null;
  syncTargetLabel?: string | null;
  canFastForwardMain?: boolean;
  directReceiveBlockReason?: string | null;
  effects: CollabEffectPreview[];
  groups: CollabChangeGroup[];
  files: CollabFileChange[];
  notice?: string | null;
  executionBlockReason?: string | null;
}

export type CollabMergeStatus =
  | 'ready'
  | 'autoMerged'
  | 'conflictsNeedResolution'
  | 'blocked'
  | 'pushed'
  | 'synced'
  | 'collabBranchPublished'
  | 'mainFastForwarded'
  | 'previewStarted'
  | 'previewStopped';

export type CollabConflictDecisionChoice = 'keep_both' | 'remote_main' | 'local';

export interface CollabConflictGroup {
  id: string;
  title: string;
  summary: string;
  operationHint: string;
  paths: string[];
  riskLevel: 'low' | 'medium' | 'high';
  aiAvailable: boolean;
  aiUnavailableReason?: string | null;
}

export interface CollabConflictDecision {
  groupId: string;
  choice: CollabConflictDecisionChoice;
}

export interface CollabRemoteCommit {
  hash: string;
  shortHash: string;
  subject: string;
  authoredAt: string;
  committedAt: string;
  authorName: string;
  authorEmail: string;
  committerName: string;
  committerEmail: string;
  identityLabel: string;
  sourceLabel: string;
  changedPaths: string[];
  fileCount: number;
}

export interface CollabRemoteBranch {
  ref: string;
  branchName: string;
  shortName: string;
  hash: string;
  shortHash: string;
  subject: string;
  authoredAt: string;
  authorName: string;
  authorEmail: string;
  changedPaths: string[];
  fileCount: number;
}

export interface PublishCollabBranchPayload {
  repoPath: string;
  message: string;
  branchName?: string | null;
}

export interface PushMainPayload {
  repoPath: string;
  message: string;
}

export interface FastForwardMainPayload {
  repoPath: string;
}

export interface StartCollabPreviewPayload {
  repoPath: string;
  targetRef: string;
  label?: string | null;
}

export interface StopCollabPreviewPayload {
  previewId: string;
}

export interface CollabPreviewSession {
  previewId: string;
  targetRef: string;
  label: string;
  repoPath: string;
  dataDir: string;
  logPath: string;
  pid?: number | null;
}

export interface CollabActionResult {
  status: CollabRepoStatus;
  changedPaths: string[];
  createdCommit: boolean;
  commitMessage?: string | null;
  mergeStatus?: CollabMergeStatus;
  conflictGroups?: CollabConflictGroup[];
  explanation?: string | null;
  collabBranchName?: string | null;
  collabBranchRef?: string | null;
  previewSession?: CollabPreviewSession | null;
  // P1-2: stash pop 失败时填(本地未选中改动已 stash 但未恢复),
  // UI 必须提示用户手动 `git stash pop` 找回工作区.
  stashRestoreWarning?: string | null;
}

export type StrictRebuildInstallState =
  | 'installing'
  | 'built_admin_required'
  | 'blocked_missing_toolchain'
  | 'not_packaged';

export interface StrictRebuildInstallResult {
  state: StrictRebuildInstallState;
  message: string;
  installed: boolean;
  artifactPath?: string | null;
  logPath?: string | null;
}

// === 输入广度线程：语音识别模型配置 ===

export type SpeechProvider = 'volcano' | 'openai_whisper' | 'aliyun_tongyi' | 'xunfei';

export interface SpeechModelSettings {
  provider: string;
  credentials: Record<string, string>;
  modelId: string;
  extraConfig: Record<string, string>;
  enabled: boolean;
  updatedAt: string;
}

export interface SpeechModelSettingsPayload {
  provider: string;
  credentials: Record<string, string>;
  modelId: string;
  extraConfig: Record<string, string>;
  enabled: boolean;
}

export interface SpeechModelTestResult {
  success: boolean;
  message: string;
  detail?: string | null;
  latencyMs?: number | null;
}

// === I1b-1：对象存储配置 ===

export type ObjectStorageProviderId = 'volcano_tos' | 'aliyun_oss' | 'aws_s3';

export interface ObjectStorageSettings {
  provider: string;
  credentials: Record<string, string>;
  extraConfig: Record<string, string>;
  enabled: boolean;
  updatedAt: string;
  hasCredentials?: boolean;
  managedByCloud?: boolean;
  configuredBy?: string | null;
}

export interface ObjectStorageSettingsPayload {
  provider: string;
  credentials: Record<string, string>;
  extraConfig: Record<string, string>;
  enabled: boolean;
}

export interface ObjectStorageTestResult {
  success: boolean;
  message: string;
  detail?: string | null;
  latencyMs?: number | null;
}

// === I1b-2：本地 ASR（SenseVoice via sherpa-onnx）===

export interface LocalAsrModelStatus {
  modelName: string;
  installed: boolean;
  modelDir: string;
  sizeBytes: number;
  downloadInProgress: boolean;
  downloadBytesDownloaded: number;
  downloadBytesTotal: number;
  downloadCurrentFile: string;
  downloadCompleted: boolean;
  downloadError?: string | null;
  downloadElapsedSeconds: number;
  state?: 'ready' | 'not_connected' | 'blocked' | 'failed_retryable' | string;
  retryable?: boolean;
  pollingEnabled?: boolean;
}

export interface LocalAsrDownloadStartResponse {
  started: boolean;
  message: string;
}

export interface LocalAsrDownloadCancelResponse {
  cancelled: boolean;
}

export interface LocalAsrTranscriptionSegment {
  startMs: number;
  endMs: number;
  text: string;
  emotion?: string | null;
  event?: string | null;
}

export interface LocalAsrTestTranscriptionResponse {
  success: boolean;
  text: string;
  durationMs: number;
  elapsedMs: number;
  language: string;
  segments: LocalAsrTranscriptionSegment[];
  errorMessage?: string | null;
}

// === P0-②：Ollama 本地 LLM 管理 ===

export interface OllamaInstalledModel {
  name: string;
  sizeBytes: number;
  digest: string;
  modifiedAt: string;
}

export interface OllamaHealthResponse {
  running: boolean;
  baseUrl: string;
  installedModels: OllamaInstalledModel[];
  error?: string | null;
  version?: string | null;
  state?: 'ready' | 'not_connected' | 'blocked' | 'failed_retryable' | string;
  retryable?: boolean;
  pollingEnabled?: boolean;
}

export interface OllamaRecommendedModel {
  name: string;
  sizeGb: number;
  description: string;
  default: boolean;
}

export interface OllamaRecommendedModelsResponse {
  capability: string;
  models: OllamaRecommendedModel[];
}

export interface OllamaPullStartResponse {
  started: boolean;
  message: string;
}

export interface OllamaPullStatusResponse {
  inProgress: boolean;
  modelName: string;
  status: string;
  bytesDownloaded: number;
  bytesTotal: number;
  elapsedSeconds: number;
  completed: boolean;
  error?: string | null;
  pollingEnabled?: boolean;
}

export interface OllamaPullCancelResponse {
  cancelled: boolean;
}

export interface OllamaDeleteModelResponse {
  success: boolean;
  message: string;
}

// ──────────────────────────────────────────────────────────────────────
// 报告生成器（与 backend/app/models.py R0.5 模型一一对应；
// 因后端 Pydantic 用 snake_case 字段、未加 alias_generator，前端类型保持 snake_case）
// ──────────────────────────────────────────────────────────────────────

export type ReportChartKind =
  | 'pie'
  | 'progress_bar_h'
  | 'timeline'
  | 'grouped_bar'
  | 'risk_bubble'
  | 'table_only'
  | 'callout_only';

export interface ChartHint {
  kind: ReportChartKind;
  title: string;
  caption?: string | null;
  data_source_hint: string;
}

export interface SectionPlan {
  level: number;
  title: string;
  goal: string;
  data_sources: string[];
  chart_hints: ChartHint[];
  citation_budget: number;
  estimated_words: number;
}

export interface ReportBlueprint {
  title: string;
  subtitle?: string | null;
  report_kind: string;
  audience: string;
  tone: string;
  period_start: string;
  period_end: string;
  sections: SectionPlan[];
  inferred_theme: string;
  confidence: number;
  open_questions_for_human: string[];
  event_line_id: string | null;
  client_id: string;
  generated_at: string;
}

export type ReportCitationType =
  | 'judgment'
  | 'event'
  | 'task'
  | 'document'
  | 'metric'
  | 'commit';

export interface CitationRef {
  type: ReportCitationType;
  id: string;
  label: string;
  excerpt?: string | null;
}

export interface GeneratedChart {
  hint: ChartHint;
  png_bytes_base64: string;
  width_cm: number;
}

export interface SectionContent {
  plan: SectionPlan;
  markdown: string;
  citations: CitationRef[];
  charts: GeneratedChart[];
  data_source_annotation: string;
  confidence: number;
  warnings: string[];
}

export interface DraftBlueprintRequest {
  event_line_id?: string | null;
  client_id?: string | null;
  period_start?: string | null;
  period_end?: string | null;
  intent_hint?: string | null;
  audience_hint?: string | null;
  tone_hint?: string | null;
  workingDocumentIds?: string[];
  activeSkillIds?: string[];
}

export interface DraftSectionsRequest {
  section_indices?: number[] | null;
  max_workers?: number;
  overall_feedback?: string | null;
  section_feedback?: Record<number, string>;
}

export type ReportRunStatus =
  | 'blueprint_pending'
  | 'blueprint_confirmed'
  | 'drafting'
  | 'body_ready'
  | 'saved'
  | 'rendered'
  | 'published'
  | 'failed';

export type ReportSectionStatus = 'pending' | 'drafting' | 'done' | 'failed';

export type ReportFileFormat = 'docx' | 'pdf' | 'md';

export interface ReportArtifactVersionSummary {
  id: string;
  artifact_id: string;
  version: number;
  title: string;
  content_markdown: string;
  content_payload: Record<string, unknown>;
  source_set_id: string;
  narrative_id: string;
  narrative_rev: number;
  event_line_version: number;
  input_fingerprint: string;
  security_label_set_version: string;
  content_hash: string;
  change_summary: string;
  created_by_display_name: string;
  restored_from_version?: number | null;
  created_at: string;
}

export interface ReportArtifactSummary {
  id: string;
  event_line_id: string;
  client_id?: string | null;
  title: string;
  status: 'active' | 'stale' | 'archived';
  latest_version: number;
  is_stale: boolean;
  availability_status: 'ready' | 'stale' | 'blocked';
  availability_reason: string;
  stale_reasons: string[];
  updated_at: string;
  latest: ReportArtifactVersionSummary;
}

export interface RenderedReportArtifactFile {
  artifact_id: string;
  artifact_version: number;
  format: ReportFileFormat;
  file_path: string;
  file_name: string;
}

export interface ReportRunSummary {
  id: string;
  client_id: string;
  event_line_id: string | null;
  period_start: string | null;
  period_end: string | null;
  intent_hint: string | null;
  status: ReportRunStatus;
  blueprint: ReportBlueprint | null;
  sections_status: ReportSectionStatus[];
  sections: Array<SectionContent | null>;
  body_markdown: string;
  warnings: string[];
  source_set_id: string;
  narrative_id: string;
  narrative_rev: number;
  event_line_version: number;
  input_fingerprint: string;
  artifact?: ReportArtifactSummary | null;
  saved_at?: string | null;
  error_message?: string | null;
  output_files: Partial<Record<ReportFileFormat, string>>;
  total_llm_tokens: number;
  created_at: string;
  updated_at: string;
  working_document_ids?: string[];
  active_skill_ids?: string[];
  source_manifest?: Array<Record<string, unknown>>;
  agent_manifest?: Array<Record<string, unknown>>;
}


// ──────────────────────────────────────────────────────────────────────
// 客户项目情报流（同事 push 的新一代资讯情报站接口）—— 2026-05-13 补回
// 来源：origin-main-backup-before-force-push-2026-05-13 tag 中 types.ts L4614-4956
// IntelligenceStationView.tsx 依赖这些类型；force push 时漏带，现在补回
// ──────────────────────────────────────────────────────────────────────

// profile_completion 已下线（2026-05-18），由数据中心负责
// P12（2026-05-19）：新增 brand_mirror 作为主秀
//   brand_mirror     - 品牌镜子（主秀，基于官方/媒体/合作信源）
//   timely_intelligence - 时效情报（保留）
//   public_opinion   - 舆情监控（保留但降级，因为 UGC 反爬挡死）
export type IntelligenceContentKind = 'brand_mirror' | 'timely_intelligence' | 'public_opinion';
export type IntelligenceWorkObjectType = 'client' | 'project_module';
export type IntelligenceFocusScopeType = 'global' | 'client' | 'project_module';
export type IntelligenceItemUserStatus = 'active' | 'dismissed' | 'following';
export type IntelligenceSearchIntentStatus = 'missing' | 'stale' | 'ready' | 'running' | 'failed';
export type IntelligenceSupplyStatus = 'missing' | 'stale' | 'ready' | 'running' | 'failed';

export interface IntelligenceWorkObject {
  type: IntelligenceWorkObjectType;
  id: string;
  clientId: string;
  projectModuleId?: string | null;
  name: string;
  subtitle: string;
  color: string;
  updatedAt?: string | null;
  searchIntentStatus: IntelligenceSearchIntentStatus;
  searchIntentHint?: string | null;
  sourceCoverageStatus: IntelligenceSupplyStatus;
  candidateRefreshStatus: IntelligenceSupplyStatus;
  candidateRefreshHint?: string | null;
  lastCandidateFetchAt?: string | null;
  candidateCounts: Record<string, number>;
}

export interface IntelligenceSourceDiagnosticSource {
  id: string;
  sourceType: string;
  sourceName: string;
  sourceUrlTemplate: string;
  contentKinds: IntelligenceContentKind[];
  region: string;
  reliabilityTier: string;
  priority: number;
  enabled: boolean;
  discoverySource: string;
  discoveryReason: string;
  discoverySamples: Array<Record<string, string>>;
  healthScore: number;
  successCount: number;
  failureCount: number;
  candidateCount: number;
  promotedCount: number;
  duplicateCount: number;
  lastStatus: string;
  lastCheckedAt?: string | null;
  lastSuccessAt?: string | null;
  lastFailureAt?: string | null;
  nextDueAt?: string | null;
}

export interface IntelligenceSourceDiagnosticFetchJob {
  id: string;
  contentKind: IntelligenceContentKind | 'source_discovery';
  provider: string;
  sourceConfigId?: string | null;
  query: string;
  status: string;
  rawCount: number;
  dedupedCount: number;
  candidateCount: number;
  sampleHits: Array<Record<string, string>>;
  failureReason: string;
  durationMs: number;
  createdAt: string;
}

export interface IntelligenceSourceDiagnosticsResponse {
  scopeType: IntelligenceWorkObjectType;
  scopeId: string;
  contentKind?: IntelligenceContentKind | null;
  sourceCoverageStatus: IntelligenceSupplyStatus;
  candidateRefreshStatus: IntelligenceSupplyStatus;
  candidateRefreshHint?: string | null;
  lastCandidateFetchAt?: string | null;
  candidateCounts: Record<string, number>;
  officialSiteDiscoveredCount: number;
  coverageGaps: string[];
  sources: IntelligenceSourceDiagnosticSource[];
  recentFetchJobs: IntelligenceSourceDiagnosticFetchJob[];
  officialSiteDiscoverySamples: IntelligenceSourceDiagnosticFetchJob[];
}

export interface IntelligenceFocusDirective {
  id: string;
  scopeType: IntelligenceFocusScopeType;
  scopeId?: string | null;
  profileCompletionFocus: string[];
  timelyIntelligenceFocus: string[];
  exclude: string[];
  createdAt: string;
  updatedAt: string;
}

export interface IntelligenceFocusDirectivePayload {
  scopeType: IntelligenceFocusScopeType;
  scopeId?: string | null;
  profileCompletionFocus: string[];
  timelyIntelligenceFocus: string[];
  exclude: string[];
}

export interface IntelligenceRefreshCycleSettings {
  profileCompletionHours: number;
  timelyIntelligenceHours: number;
}

export interface IntelligenceRefreshCycleSettingsPayload {
  profileCompletionHours?: number | null;
  timelyIntelligenceHours?: number | null;
}

export interface IntelligenceItem {
  id: string;
  contentKind: IntelligenceContentKind;
  scopeType?: string | null;
  scopeId?: string | null;
  clientId?: string | null;
  projectModuleId?: string | null;
  title: string;
  summary: string;
  keyPoints: string[];
  analysis: string;
  impact: string;
  intelligenceType?: string | null;
  timelinessLabel?: string | null;
  relevanceReason: string;
  suggestedAction: string;
  followupQuestions: string[];
  tags: string[];
  source: string;
  sourceUrl?: string | null;
  publishedAt?: string | null;
  capturedAt: string;
  verifiedAt?: string | null;
  dataCenterIngestEventId?: string | null;
  externalEvidenceCardId?: string | null;
  topicCandidateId?: string | null;
  convertedTaskId?: string | null;
  verificationStatus: string;
  verificationReason: string;
  userStatus: IntelligenceItemUserStatus;
  createdAt: string;
  updatedAt: string;
}

export interface IntelligenceCandidateSample {
  id: string;
  contentKind: IntelligenceContentKind;
  scopeType: string;
  scopeId: string;
  clientId?: string | null;
  projectModuleId?: string | null;
  title: string;
  url?: string | null;
  snippet: string;
  source: string;
  publishedAt?: string | null;
  capturedAt: string;
  confidenceScore: number;
  classificationStatus: string;
  promotionReason: string;
  verificationStatus: string;
  verificationReason: string;
  bodyFetchStatus: string;
  summaryStatus: string;
  mappedTags: string[];
  isUserVisibleCandidate: boolean;
}

export interface IntelligenceItemsResponse {
  items: IntelligenceItem[];
  candidateSamples: IntelligenceCandidateSample[];
  total: number;
  page: number;
  pageSize: number;
}

export interface IntelligenceRefreshPayload {
  scopeType: 'all' | IntelligenceWorkObjectType;
  scopeId?: string | null;
  contentKind: IntelligenceContentKind;
  force?: boolean;
  triggerSource?: string;
}

export interface IntelligenceAutoRefreshDuePayload {
  contentKinds?: Array<Extract<IntelligenceContentKind, 'profile_completion' | 'timely_intelligence'>>;
  scopeType?: 'all' | 'client';
  scopeId?: string | null;
}

export interface IntelligenceAutoRefreshDueResultItem {
  scopeType: 'client';
  scopeId: string;
  clientId: string;
  name: string;
  contentKind: Extract<IntelligenceContentKind, 'profile_completion' | 'timely_intelligence'>;
  queued: boolean;
  queuedJobId?: string | null;
  lastRunAt?: string | null;
  skippedReason?: string;
  autoDueReason?: string;
}

export interface IntelligenceAutoRefreshDueResult {
  checkedAt: string;
  queuedCount: number;
  skippedCount: number;
  results: IntelligenceAutoRefreshDueResultItem[];
  message: string;
}

export interface IntelligenceRefreshObjectResult {
  scopeType: IntelligenceWorkObjectType;
  scopeId: string;
  clientId: string;
  projectModuleId?: string | null;
  name: string;
  contentKind: IntelligenceContentKind;
  status: 'completed' | 'no_results' | 'failed';
  intentCount: number;
  diagnosticRunCount: number;
  diagnosticSuccessCount: number;
  fetchJobCount: number;
  candidateCount: number;
  promotedCount: number;
  duplicateCount: number;
  failedCount: number;
  bodyFetchedCount: number;
  verifiedCount: number;
  summarySuccessCount: number;
  rejectionCounts: Record<string, number>;
  sourceCoverageStatus: IntelligenceSupplyStatus;
  candidateRefreshStatus: IntelligenceSupplyStatus;
  lastCandidateFetchAt?: string | null;
  candidateCounts: Record<string, number>;
  candidateSamples: IntelligenceCandidateSample[];
  queuedJobId?: string | null;
  message: string;
  errors: string[];
}

export interface IntelligenceRefreshTotals {
  objectCount: number;
  completedCount: number;
  noResultCount: number;
  failedCount: number;
  intentCount: number;
  fetchJobCount: number;
  candidateCount: number;
  promotedCount: number;
  duplicateCount: number;
  bodyFetchedCount: number;
  verifiedCount: number;
  summarySuccessCount: number;
  rejectionCounts: Record<string, number>;
}

export interface IntelligenceRefreshResult {
  status: 'completed' | 'no_results' | 'failed' | 'partial_failed';
  contentKind: IntelligenceContentKind;
  scopeType: 'all' | IntelligenceWorkObjectType;
  scopeId?: string | null;
  results: IntelligenceRefreshObjectResult[];
  totals: IntelligenceRefreshTotals;
  message: string;
  generatedAt: string;
}

export interface IntelligenceRefreshRun {
  id: string;
  scopeType: 'all' | IntelligenceWorkObjectType | '';
  scopeId?: string | null;
  clientId?: string | null;
  projectModuleId?: string | null;
  contentKind: IntelligenceContentKind;
  triggerSource: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  stage: string;
  message: string;
  result: Record<string, unknown>;
  rejectionSummary: Record<string, number>;
  createdAt: string;
  updatedAt: string;
  startedAt?: string | null;
  finishedAt?: string | null;
}

export type IntelligenceDismissReasonCode = 'irrelevant' | 'inaccurate' | 'duplicate' | 'outdated' | 'low_value';

export interface IntelligenceDismissPayload {
  reasonCode: IntelligenceDismissReasonCode;
  note?: string;
}

export type IntelligenceFollowMode = 'same_theme' | 'same_source' | 'same_work_object';

export interface IntelligenceFollowPayload {
  followMode: IntelligenceFollowMode;
  note?: string;
}

export interface IntelligenceVerificationRule {
  id: string;
  scopeType: IntelligenceFocusScopeType;
  scopeId?: string | null;
  positiveRules: string[];
  excludeRules: string[];
  identityAnchors: string[];
  clarificationExamples: string[];
  createdAt: string;
  updatedAt: string;
}

export interface IntelligenceVerificationRulePayload {
  scopeType: IntelligenceFocusScopeType;
  scopeId?: string | null;
  positiveRules: string[];
  excludeRules: string[];
  identityAnchors: string[];
}

export interface IntelligenceVerificationFeedbackPayload {
  targetType: 'item' | 'candidate';
  targetId: string;
  scopeType: IntelligenceFocusScopeType;
  scopeId?: string | null;
  note: string;
}

export interface IntelligenceItemChatResponse {
  itemId: string;
  question: string;
  answer: string;
  generatedAt: string;
  message: TopicCandidateChatMessage;
}

export interface IntelligenceTaskDraftPayload {
  title?: string | null;
  desc?: string | null;
  priority: Priority;
  listId?: string | null;
  dueDate?: string | null;
  ddl: string;
  ownerId?: string | null;
  ownerName: string;
  collaboratorIds?: string[];
  tags: string[];
  note: string;
  ownerRoleHint?: string;
  collaboratorRoleHints?: string[];
}

export interface IntelligenceTaskDraftResponse {
  itemId: string;
  draft: IntelligenceTaskDraftPayload;
}

export interface IntelligenceTaskCreatePayload extends IntelligenceTaskDraftPayload {
  title: string;
}

export interface IntelligenceTaskCreateResponse {
  item: IntelligenceItem;
  task: Task;
}

export interface IntelligenceFeedbackSummaryRecord {
  targetType: string;
  targetLabel: string;
  positiveCount: number;
  negativeCount: number;
  neutralCount: number;
  score: number;
  lastEventAt?: string | null;
}

export interface IntelligenceFeedbackEventRecord {
  id: string;
  contentKind: IntelligenceContentKind;
  itemId?: string | null;
  candidateId?: string | null;
  actionType: string;
  reasonCode: string;
  note: string;
  extractedTopics: string[];
  source: string;
  sourceDomain: string;
  scoreDelta: number;
  createdAt: string;
}

export interface IntelligenceFeedbackDiagnosticsResponse {
  scopeType: IntelligenceFocusScopeType;
  scopeId: string;
  contentKind?: IntelligenceContentKind | null;
  summaries: IntelligenceFeedbackSummaryRecord[];
  events: IntelligenceFeedbackEventRecord[];
}

export interface DesktopRuntime {
  apiBaseUrl: string;
  desktopToken: string;
  appName: string;
  appVersion: string;
}

export interface RenderedOfficialWebsitePage {
  title: string;
  url: string;
  text: string;
  contentHash: string;
  capturedAt: string;
  discoveredUrl: string;
  canonicalPublicUrl: string;
}

export interface RenderedOfficialWebsiteCapture {
  pages: RenderedOfficialWebsitePage[];
  state: 'ready' | 'failed_retryable' | 'blocked';
  message: string;
}

declare global {
  interface Window {
    __YIYU_TEST_DIALOGS__?: {
      selectFiles?: () => Promise<string[]>;
      selectFolder?: () => Promise<string | null>;
      selectCollabRepo?: () => Promise<string | null>;
      openPath?: (targetPath: string) => Promise<boolean>;
      revealInFinder?: (targetPath: string) => Promise<boolean>;
      saveFileAs?: (sourcePath: string, suggestedName?: string) => Promise<string | null>;
    };
    yiyuWorkbench: {
      backendBaseUrl: string;
      desktopToken: string;
      setMiniMode(enter: boolean, height?: number): Promise<{ mini: boolean }>;
      setUpdateOrgIdentity(identity: UpdateOrgIdentity | null): Promise<{ ok: boolean; reason?: string }>;
      setUpdateOrgCode(orgCode: string | null): Promise<{ ok: boolean; reason?: string }>;
      getDesktopAppInfo(): Promise<DesktopAppInfo>;
      resumeFromStartupGate(): Promise<DesktopStartupGateResumeResult>;
      selectFiles(): Promise<string[]>;
      selectFolder(): Promise<string | null>;
      selectCollabRepo(): Promise<string | null>;
      getCollabRepoStatus(repoPath?: string | null): Promise<CollabRepoStatus>;
      previewPushToMain(repoPath: string): Promise<PushPreview>;
      pushSafelyToMain(payload: PushMainPayload): Promise<CollabActionResult>;
      publishCollabBranch(payload: PublishCollabBranchPayload): Promise<CollabActionResult>;
      previewPullFromMain(repoPath: string, targetCommit?: string | null): Promise<PullPreview>;
      fastForwardMain(payload: FastForwardMainPayload): Promise<CollabActionResult>;
      startCollabPreview(payload: StartCollabPreviewPayload): Promise<CollabActionResult>;
      stopCollabPreview(payload: StopCollabPreviewPayload): Promise<CollabActionResult>;
      rebuildAndInstallFromRepo(repoPath: string): Promise<StrictRebuildInstallResult>;
      setWorkspaceInteractionState(payload: {
        active: boolean;
        source: string;
        detail?: string | null;
      }): Promise<{
        active: boolean;
        source: string;
        detail?: string | null;
        updatedAt: string;
      }>;
      getDroppedFilePath(file: File): string | null;
      readTextFile(targetPath: string): Promise<string>;
      openPath(targetPath: string): Promise<boolean>;
      openExternalUrl(targetUrl: string): Promise<boolean>;
      renderOfficialWebsite(targetUrl: string): Promise<RenderedOfficialWebsiteCapture>;
      revealInFinder(targetPath: string): Promise<boolean>;
      saveFileAs(sourcePath: string, suggestedName?: string): Promise<string | null>;
      quitApp(): Promise<boolean>;
      saveRecordingBlob(payload: {
        buffer: ArrayBuffer;
        extension?: string;
        sessionId?: string;
      }): Promise<{ absolutePath: string; sizeBytes: number; sessionId: string }>;
      readRecordingFile(absolutePath: string): Promise<{ buffer: Uint8Array; sizeBytes: number; name: string }>;
      setRecordingActive(payload: { active: boolean; taskTitle?: string }): Promise<{ active: boolean }>;
      setBackgroundTasks?(payload: {
        tasks: { kind: string; label: string; status?: string; severity?: 'loss' | 'queued' }[];
      }): Promise<{ ok: boolean; count: number }>;
      checkForUpdates?(): Promise<{ ok: boolean; version?: string | null; reason?: string; officialPush?: OfficialPushUpdatePayload | null }>;
      getCurrentReleaseMetadata?(): Promise<ReleaseVersionMetadata | null>;
      installOfficialPushUpdate?(): Promise<{ ok: boolean; version?: string | null; reason?: string; fileName?: string | null }>;
      onUpdateEvent?(callback: (payload: UpdateEventPayload) => void): () => void;
    };
    strictDesktop: {
      getRuntime(): Promise<DesktopRuntime>;
    };
  }
}

export interface UpdateEventPayload {
  kind:
    | 'checking'
    | 'available'
    | 'not-available'
    | 'download-progress'
    | 'downloaded'
    | 'error'
    | 'official-push-available'
    | 'official-push-not-available';
  version?: string;
  releaseNotes?: string | null;
  percent?: number;
  bytesPerSecond?: number;
  transferred?: number;
  total?: number;
  message?: string;
  officialPush?: OfficialPushUpdatePayload | null;
}

// P13-D 品牌镜子 LLM 画像 snapshot (后端 /api/v2/ui/intelligence/brand-mirror/analyze 返回结构)
export type BrandMirrorTone = 'positive' | 'neutral' | 'negative';

export interface BrandMirrorSelfPresentation {
  label: string;
  score: number; // 1-100
  rationale: string;
}

export interface BrandMirrorBlindspot {
  label: string;
  rationale: string;
}

export interface BrandMirrorMediaCoverage {
  source: string;
  tone: BrandMirrorTone;
  summary: string;
}

export interface BrandMirrorPartner {
  name: string;
  type: string; // foundation/corporate/government/media/academic
  evidence: string;
}

export interface BrandMirrorWordCloudItem {
  word: string;
  weight: number; // 1-100
  tone: BrandMirrorTone;
  sourceDiversity: number; // 1-5
}

export interface BrandMirrorSnapshot {
  id: string;
  corpusDocCount: number;
  corpusCharCount: number;
  websiteAuditId: string | null;
  selfPresentation: BrandMirrorSelfPresentation[];
  blindspots: BrandMirrorBlindspot[];
  consistency: string;
  mediaCoverage: BrandMirrorMediaCoverage[];
  partners: BrandMirrorPartner[];
  wordCloud: BrandMirrorWordCloudItem[];
  llmModel: string;
  error: string | null;
  createdAt: string;
}

// P14-D 战略推演树 (从战略陪伴 .md 抽出的应然结构)
export interface BrandStrategyStakeholder {
  name: string;
  rationale?: string;
  distinguishingFeature?: string;
  coreMessage: string;
  desiredAction: string;
  keyExamples?: string[];
}

export interface BrandStrategyExtract {
  clientId: string;
  strategicObjective: string;
  strategicObjectiveSources: string[];
  methodology: string;
  methodologySources: string[];
  stakeholders: BrandStrategyStakeholder[];
  sourceStrategyMdHash: string;
  sourceMethodologyMdHash: string;
  llmModel: string;
  error: string | null;
  extractedAt: string;
  confirmedBy: string | null;
  confirmedAt: string | null;
  isStale: boolean;
}
