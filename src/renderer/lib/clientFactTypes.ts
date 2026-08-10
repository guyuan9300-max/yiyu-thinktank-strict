/**
 * v2.2 Phase 1 F1.5 · 前端 ClientFactBundle TS 类型
 *
 * 跟 backend/app/modules/client/facts.py 里的 dataclass 对齐,
 * 镜像 backend/app/models.py 里的 Pydantic Response model。
 *
 * 数据源: GET /api/v2/ui/clients/{client_id}/fact-bundle
 */

export interface EventLineFact {
  id: string;
  name: string;
  kind: string;
  status: string;
  stage: string;
  summary: string;
  intent: string;
  current_blocker: string;
  recent_decision: string;
  next_step: string;
  evidence_count: number;
  owner_id: string | null;
  owner_name: string | null;
  primary_client_id: string;
  primary_client_name: string;
  created_at: string;
  updated_at: string;
}

export interface TaskFact {
  id: string;
  title: string;
  description_preview: string;
  status: string;
  priority: string;
  progress_status: string;
  owner_id: string | null;
  owner_name: string;
  creator_id: string;
  deadline_at: string | null;
  due_date: string | null;
  scheduled_start_at: string | null;
  completed_at: string | null;
  event_line_id: string | null;
  business_category: string | null;
  current_blocker: string;
  next_action: string;
  recent_decision: string;
  evidence_count: number;
  source_type: string;
  source_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface CommitmentFact {
  id: string;
  committer: string;
  recipient: string;
  commitment_type: string;
  content: string;
  deadline: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface DnaDocumentRef {
  module_key: string;
  title: string;
  summary: string;
  file_name: string;
  source_kind: string;
  updated_at: string;
  updated_by: string;
  has_full_content: boolean;
}

export interface AtomicFactRef {
  id: string;
  subject_text: string;
  attribute: string;
  value_text: string;
  confidence: number;
  source_v2_document_id: string | null;
  source_v2_chunk_id: string | null;
  evidence_text: string | null;
  status: string;
  updated_at: string;
}

export interface ClientFactRecord {
  id: string;
  name: string;
  alias: string;
  domain: string;
  type: string;
  intro: string;
  stage: string;
  color: string;
  created_at: string;
  updated_at: string;
}

export interface ClientFactBundle {
  client: ClientFactRecord;
  event_lines: EventLineFact[];
  tasks: TaskFact[];
  commitments: CommitmentFact[];
  dna_documents: DnaDocumentRef[];
  atomic_facts: AtomicFactRef[];
  key_decisions: unknown[];  // Phase 2 才填
  snapshot_at: string;
  sources: Record<string, string>;
  counts: Record<string, number>;
}

export interface FetchClientFactBundleOptions {
  /** 包含 stage='archived' 的客户(默认 false 排除) */
  includeArchived?: boolean;
  /** lite 模式: 只返回 counts, 不拿事实 list (给客户列表用) */
  lite?: boolean;
}

/**
 * 判断一个 bundle 是不是 lite 版 (内部所有 list 都是空的)
 */
export function isLiteBundle(bundle: ClientFactBundle | null | undefined): boolean {
  if (!bundle) return false;
  return (
    bundle.event_lines.length === 0 &&
    bundle.tasks.length === 0 &&
    bundle.commitments.length === 0 &&
    bundle.dna_documents.length === 0 &&
    bundle.atomic_facts.length === 0 &&
    (bundle.counts.event_lines || 0) +
      (bundle.counts.tasks || 0) +
      (bundle.counts.commitments || 0) +
      (bundle.counts.dna_documents || 0) +
      (bundle.counts.atomic_facts || 0) > 0
  );
}
