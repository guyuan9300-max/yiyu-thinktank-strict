export type GC13RebuildState = 'ready' | 'updating' | 'failed_retryable' | 'not_connected';

export type GC13EvidenceCategory =
  | 'execution'
  | 'collaboration'
  | 'analysis'
  | 'insight'
  | 'risk'
  | 'writing'
  | 'learning'
  | 'reflection';

export interface GC13GrowthEvidence {
  evidenceId: string;
  summary: string;
  category: GC13EvidenceCategory;
  validationState: 'validated';
  sourceType: string;
  sourceId: string;
  sourceVersion: number;
  contentHash: string;
  contributionScore: number;
  version: number;
  createdAt: string;
}

export interface GC13GrowthRule {
  ruleVersionId: string;
  metricKey: string;
  ruleVersion: number;
  effectiveAt: string;
  lifecycleState: string;
  spec: {
    label?: string;
    abilityLabel?: string;
    evidenceCategories?: string[];
  };
}

export interface GC13WeeklyReviewCandidate {
  candidateId: string;
  status: 'pending_confirmation' | 'confirming' | 'confirmed' | 'ignored';
  summary: string;
  category: GC13EvidenceCategory;
  reviewId: string;
  reviewVersionId: string;
  sourceVersion: number;
  sourceHash: string;
  version: number;
  createdAt: string;
}

export interface GC13GrowthModel {
  modelKind: 'metric' | 'badge' | 'ability' | 'overview';
  label?: string;
  metricKey?: string;
  badgeKey?: string;
  abilityKey?: string;
  score?: number;
  maxScore?: number;
  state?: 'earned' | 'locked';
  progressPercent?: number;
  evidenceCount?: number;
  ruleVersion?: number;
  generatedAt?: string;
}

export interface GC13GrowthSnapshot {
  schema: 'yiyu.gc13.growth-snapshot.v1';
  memberId: string;
  evidence: GC13GrowthEvidence[];
  rules: GC13GrowthRule[];
  readModel: {
    state: GC13RebuildState;
    models: GC13GrowthModel[];
    metrics: GC13GrowthModel[];
    badges: GC13GrowthModel[];
    abilities: GC13GrowthModel[];
    overview: GC13GrowthModel | null;
  };
  rebuild: {
    state: GC13RebuildState;
    retryable: boolean;
    message: string;
  };
  companion: {
    agentId: string;
    agentKind: 'growth_companion';
    mode: 'growth_companion' | 'base_mode';
    state: GC13RebuildState | 'base_mode';
    baseMode: string;
    allowedPreferences: Array<{
      preferenceId: string;
      key: string;
      label: string;
      value: string;
      origin: string;
      memberAllowed: true;
      consumer: 'growth_companion';
    }>;
    sourceLabels: string[];
    boundaries: string[];
  };
  weeklyReviewAdapter: {
    contract: 'yiyu.gc13.weekly-review-candidate-port.v1';
    status: 'awaiting_b_thread' | 'connected';
    acceptedSourceType: 'weekly_review_candidate';
    readsWeeklyReviewTables: false;
    writesCandidateBeforeMemberConfirmation: false;
  };
  weeklyReviewCandidates?: GC13WeeklyReviewCandidate[];
  skillBoundary: {
    autoCreatesSkill: false;
    readsAutomationRules: false;
  };
  projectMemoryBoundary: {
    consumed: false;
    copiedIntoGrowthEvidence: false;
  };
}

export const GC13_CATEGORY_OPTIONS: ReadonlyArray<{
  value: GC13EvidenceCategory;
  label: string;
}> = [
  { value: 'reflection', label: '复盘反思' },
  { value: 'execution', label: '执行推进' },
  { value: 'collaboration', label: '协作促进' },
  { value: 'analysis', label: '分析判断' },
  { value: 'insight', label: '洞察形成' },
  { value: 'risk', label: '风险识别' },
  { value: 'writing', label: '表达写作' },
  { value: 'learning', label: '学习实践' },
];

export function gc13StateCopy(state: GC13RebuildState) {
  if (state === 'ready') return { label: '能力已更新', tone: 'ready' as const };
  if (state === 'updating') return { label: '能力更新中', tone: 'pending' as const };
  if (state === 'failed_retryable') return { label: '更新失败，可重试', tone: 'error' as const };
  return { label: '规则待接通', tone: 'muted' as const };
}

export function gc13BoundarySummary(snapshot: GC13GrowthSnapshot) {
  return {
    evidenceCount: snapshot.evidence.length,
    metricCount: snapshot.readModel.metrics.length,
    earnedBadgeCount: snapshot.readModel.badges.filter((item) => item.state === 'earned').length,
    abilityCount: snapshot.readModel.abilities.length,
    preferenceCount: snapshot.companion.allowedPreferences.length,
    preservesEvidence: snapshot.rebuild.state !== 'ready' || snapshot.evidence.length >= 0,
    neverCreatesSkill: snapshot.skillBoundary.autoCreatesSkill === false,
    neverConsumesProjectMemory: snapshot.projectMemoryBoundary.consumed === false,
    waitsForWeeklyReviewAdapter: snapshot.weeklyReviewAdapter.status === 'awaiting_b_thread',
  };
}
