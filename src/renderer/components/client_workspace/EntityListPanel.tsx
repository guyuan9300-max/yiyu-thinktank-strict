import React, { useEffect, useMemo, useState } from 'react';

import type { Entity, EntityMergeCandidate, EntityType } from '../../../shared/types';
import { getClientEntities, getEntityMergeCandidates, mergeEntityInto, verifyEntity } from '../../lib/api';

type EntityListPanelProps = {
  clientId: string;
  refreshKey?: number;
};

interface TypeMeta {
  label: string;
  tone: string;
}

const TYPE_META: Record<EntityType, TypeMeta> = {
  person: { label: '人物', tone: 'ring-rose-200/70 text-rose-700' },
  company: { label: '公司', tone: 'ring-sky-200/70 text-sky-700' },
  project: { label: '项目', tone: 'ring-indigo-200/70 text-indigo-700' },
  product: { label: '产品', tone: 'ring-emerald-200/70 text-emerald-700' },
  competitor: { label: '竞品', tone: 'ring-amber-200/70 text-amber-700' },
  amount: { label: '金额', tone: 'ring-gray-200/70 text-gray-700' },
  date: { label: '日期', tone: 'ring-violet-200/70 text-violet-700' },
};

const TYPE_ORDER: EntityType[] = [
  'person',
  'company',
  'project',
  'product',
  'competitor',
  'amount',
  'date',
];

function formatRelative(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return '';
  const diff = Date.now() - parsed.getTime();
  const day = 24 * 60 * 60 * 1000;
  if (diff < day) return '今天';
  if (diff < 7 * day) return `${Math.floor(diff / day)} 天前`;
  if (diff < 30 * day) return `${Math.floor(diff / (7 * day))} 周前`;
  if (diff < 365 * day) return `${Math.floor(diff / (30 * day))} 个月前`;
  return `${(diff / (365 * day)).toFixed(1)} 年前`;
}

export function EntityListPanel({ clientId, refreshKey = 0 }: EntityListPanelProps) {
  const [entities, setEntities] = useState<Entity[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<EntityMergeCandidate[]>([]);
  const [candidatesOpen, setCandidatesOpen] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    if (!clientId) {
      setEntities([]);
      setCandidates([]);
      return;
    }
    setLoading(true);
    setError(null);
    Promise.all([
      getClientEntities(clientId, { limit: 200 }),
      getEntityMergeCandidates(clientId, 50).catch(() => ({ candidates: [] })),
    ])
      .then(([entResult, candResult]) => {
        if (cancelled) return;
        setEntities(entResult.entities);
        setCandidates(candResult.candidates);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : '加载实体失败';
        setError(message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [clientId, refreshKey, reloadKey]);

  // ★ ER v4 · 人工金标 verify handler (5/28 加)
  // 三种动作: canonical(真人物) / noise(ASR错误,永久过滤) / alias_of(同 X 合并)
  const handleVerify = async (
    entityId: string,
    entityName: string,
    status: 'canonical' | 'noise' | 'alias_of',
  ) => {
    if (status === 'alias_of') {
      // alias_of 需要 prompt 让用户选 target — 简化版用 window.prompt
      const targetId = window.prompt(`把"${entityName}"合并到哪个 entity? 输入 entity id:`);
      if (!targetId) return;
      try {
        await verifyEntity(entityId, { status: 'alias_of', alias_target_id: targetId, reason: `用户标记别名: ${entityName} → ${targetId}` });
        setReloadKey((v) => v + 1);
      } catch (err) {
        setError(err instanceof Error ? err.message : '别名合并失败');
      }
      return;
    }
    const reasonMap = { canonical: `用户确认是真人物: ${entityName}`, noise: `用户标记为 ASR 错误/非人物: ${entityName}` };
    try {
      await verifyEntity(entityId, { status, reason: reasonMap[status] });
      setReloadKey((v) => v + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : '标记失败');
    }
  };

  const handleMerge = async (
    survivingEntityId: string,
    mergedEntityId: string,
    survivingName: string,
    mergedName: string,
  ) => {
    if (!window.confirm(`确认把"${mergedName}"合并到"${survivingName}"？此操作会迁移所有提及/关系/事实到目标实体。`)) {
      return;
    }
    try {
      await mergeEntityInto(mergedEntityId, {
        survivingEntityId,
        mergeReason: `手动合并 · ${mergedName} → ${survivingName}`,
      });
      setReloadKey((v) => v + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : '合并失败');
    }
  };

  const grouped = useMemo(() => {
    const map = new Map<EntityType, Entity[]>();
    for (const entity of entities) {
      const list = map.get(entity.entityType) ?? [];
      list.push(entity);
      map.set(entity.entityType, list);
    }
    for (const [type, list] of map) {
      list.sort((a, b) => b.mentionCount - a.mentionCount);
      map.set(type, list);
    }
    return map;
  }, [entities]);

  if (!clientId) {
    return null;
  }

  return (
    <div className="space-y-4 rounded-2xl border border-gray-100 bg-white p-5">
      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="text-[9px] font-semibold uppercase tracking-[0.18em] text-gray-400">
            Identified Entities
          </div>
          <div className="mt-0.5 text-[13px] font-medium tracking-tight text-gray-800">
            识别出的实体 <span className="ml-1 text-gray-400">{entities.length}</span>
          </div>
        </div>
        {candidates.length > 0 && (
          <button
            type="button"
            className="rounded-full px-2.5 py-1 text-[10px] font-medium text-amber-700 ring-1 ring-inset ring-amber-200 hover:bg-amber-50/70 transition-colors"
            onClick={() => setCandidatesOpen((v) => !v)}
            title="发现可能是同一实体的相似名（如「张总」和「张总监」）"
          >
            {candidatesOpen ? '收起' : `${candidates.length} 个疑似重复`}
          </button>
        )}
      </div>

      {candidatesOpen && candidates.length > 0 && (
        <div className="space-y-2 rounded-xl bg-amber-50/40 px-3 py-2.5 ring-1 ring-inset ring-amber-100">
          <p className="text-[9px] font-semibold uppercase tracking-[0.16em] text-amber-700">
            疑似重复实体 · 选择保留方向
          </p>
          {candidates.map((c) => {
            const aIsBetter = c.mentionCountA >= c.mentionCountB;
            return (
              <div
                key={`${c.entityAId}-${c.entityBId}`}
                className="rounded-lg bg-white px-3 py-2 ring-1 ring-inset ring-gray-100"
              >
                <p className="text-[10px] font-medium text-gray-500">
                  {c.entityType} · 相似度 {Math.round(c.similarity * 100)}% · {c.reason}
                </p>
                <div className="mt-1.5 grid grid-cols-2 gap-1.5">
                  <button
                    type="button"
                    className={`rounded-md px-2 py-1 text-[10.5px] font-medium transition-colors ${
                      aIsBetter
                        ? 'bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200 hover:bg-emerald-100/60'
                        : 'bg-white text-gray-600 ring-1 ring-inset ring-gray-200 hover:bg-gray-50'
                    }`}
                    onClick={() => void handleMerge(c.entityAId, c.entityBId, c.nameA, c.nameB)}
                    title={`保留"${c.nameA}"，把"${c.nameB}"的所有提及/关系合进来`}
                  >
                    保留 {c.nameA} <span className="opacity-60">({c.mentionCountA})</span>
                  </button>
                  <button
                    type="button"
                    className={`rounded-md px-2 py-1 text-[10.5px] font-medium transition-colors ${
                      !aIsBetter
                        ? 'bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200 hover:bg-emerald-100/60'
                        : 'bg-white text-gray-600 ring-1 ring-inset ring-gray-200 hover:bg-gray-50'
                    }`}
                    onClick={() => void handleMerge(c.entityBId, c.entityAId, c.nameB, c.nameA)}
                    title={`保留"${c.nameB}"，把"${c.nameA}"的所有提及/关系合进来`}
                  >
                    保留 {c.nameB} <span className="opacity-60">({c.mentionCountB})</span>
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {loading && entities.length === 0 && (
        <p className="text-[11px] font-medium text-gray-400">加载中…</p>
      )}

      {error && (
        <p className="text-[11px] font-medium text-rose-600">{error}</p>
      )}

      {!loading && !error && entities.length === 0 && (
        <p className="text-[11px] font-medium leading-5 text-gray-400">
          这位客户暂时没有识别出的实体。导入更多资料后会自动出现。
        </p>
      )}

      {TYPE_ORDER.map((type) => {
        const list = grouped.get(type);
        if (!list || list.length === 0) return null;
        const meta = TYPE_META[type];
        return (
          <div key={type} className="space-y-1.5">
            <div className="flex items-center gap-2">
              <span className={`rounded-full px-2 py-[2px] text-[9.5px] font-semibold uppercase tracking-[0.12em] ring-1 ring-inset ${meta.tone}`}>
                {meta.label}
              </span>
              <span className="text-[10px] font-medium text-gray-400">{list.length}</span>
            </div>
            <ul className="divide-y divide-gray-100 overflow-hidden rounded-xl ring-1 ring-inset ring-gray-100">
              {list.map((entity, idx) => (
                <li
                  key={entity.id}
                  className={`flex items-center justify-between gap-2 px-3 py-1.5 ${idx % 2 === 1 ? 'bg-[#FAFAFA]' : 'bg-white'}`}
                  title={`置信度 ${(entity.confidence * 100).toFixed(0)}% · 最近 ${formatRelative(entity.lastSeenAt)} · id=${entity.id}`}
                >
                  <span className="truncate text-[12px] font-medium text-gray-800">
                    {entity.displayName}
                  </span>
                  <span className="flex shrink-0 items-center gap-2">
                    <span className="text-[10px] font-medium text-gray-400">
                      {entity.mentionCount} 次 · {formatRelative(entity.lastSeenAt)}
                    </span>
                    {/* ★ ER v4 · 3 按钮人工金标(只对 person 类显示) */}
                    {type === 'person' && (
                      <span className="flex shrink-0 gap-0.5">
                        <button
                          type="button"
                          onClick={() => void handleVerify(entity.id, entity.displayName, 'canonical')}
                          className="rounded px-1 py-0.5 text-[9px] font-semibold text-emerald-700 ring-1 ring-emerald-200 hover:bg-emerald-50"
                          title={`确认"${entity.displayName}"是真人物(永久, 不被 LLM 覆盖)`}
                        >
                          ✓真人
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleVerify(entity.id, entity.displayName, 'noise')}
                          className="rounded px-1 py-0.5 text-[9px] font-semibold text-rose-700 ring-1 ring-rose-200 hover:bg-rose-50"
                          title={`标记"${entity.displayName}"为 ASR 错误(永久过滤, 不再出现在下一步建议)`}
                        >
                          ✗错误
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleVerify(entity.id, entity.displayName, 'alias_of')}
                          className="rounded px-1 py-0.5 text-[9px] font-semibold text-amber-700 ring-1 ring-amber-200 hover:bg-amber-50"
                          title={`合并"${entity.displayName}"到另一个 entity (会提示输入 target id)`}
                        >
                          =同X
                        </button>
                      </span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}
