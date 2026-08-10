import test from 'node:test';
import assert from 'node:assert/strict';

import { buildEvidenceCitationCards, EVIDENCE_CITATION_ROLE_LABELS } from './workspaceEvidencePresentation.js';
import type { EvidenceItem } from './types.js';

test('buildEvidenceCitationCards sorts direct contract evidence ahead of generic high score evidence', () => {
  const cards = buildEvidenceCitationCards([
    {
      id: 'meeting',
      title: '教师赋能一季度沟通会议纪要.docx',
      excerpt: '会议讨论了项目下一步推进安排。',
      sourceType: 'meeting_note',
      path: '/tmp/教师赋能一季度沟通会议纪要.docx',
      score: 9,
      matchedTerms: [],
      retrievalStage: 'raw_chunk',
    },
    {
      id: 'contract',
      title: '测试机构A咨询合同（0907）.docx',
      excerpt: '服务费用为40万元，并按合同约定分期付款。',
      sourceType: 'raw_document',
      path: '/tmp/测试机构A咨询合同（0907）.docx',
      score: 1,
      matchedTerms: [],
      retrievalStage: 'raw_chunk',
      citationRole: 'direct_support',
      citationPriority: 100,
      citationReason: 'contract_facts_answered_from_contract_source',
    },
  ] satisfies EvidenceItem[]);

  assert.equal(cards[0]?.sourceTitle, '测试机构A咨询合同（0907）.docx');
  assert.equal(cards[0]?.citationRole, 'direct_support');
  assert.equal(EVIDENCE_CITATION_ROLE_LABELS.direct_support, '直接依据');
});
