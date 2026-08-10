import React, { useEffect, useState } from 'react';

import type { GlossaryEntry } from '../../../shared/types';
import {
  createGlossaryEntry,
  deleteGlossaryEntry,
  getClientGlossary,
  updateGlossaryEntry,
} from '../../lib/api';

type GlossaryPanelProps = {
  clientId: string;
  refreshKey?: number;
};

export function GlossaryPanel({ clientId, refreshKey = 0 }: GlossaryPanelProps) {
  const [entries, setEntries] = useState<GlossaryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [newTerm, setNewTerm] = useState('');
  const [newDefinition, setNewDefinition] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTerm, setEditTerm] = useState('');
  const [editDefinition, setEditDefinition] = useState('');

  useEffect(() => {
    let cancelled = false;
    if (!clientId) {
      setEntries([]);
      return;
    }
    setLoading(true);
    setError(null);
    getClientGlossary(clientId, { limit: 200 })
      .then((result) => {
        if (cancelled) return;
        setEntries(result.entries);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : '加载术语失败');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [clientId, refreshKey, reloadKey]);

  const handleAdd = async () => {
    const term = newTerm.trim();
    if (!term) return;
    try {
      await createGlossaryEntry(clientId, {
        term,
        definition: newDefinition.trim(),
      });
      setNewTerm('');
      setNewDefinition('');
      setReloadKey((v) => v + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : '添加失败');
    }
  };

  const handleSaveEdit = async (id: string) => {
    try {
      await updateGlossaryEntry(id, {
        term: editTerm.trim() || undefined,
        definition: editDefinition.trim(),
      });
      setEditingId(null);
      setReloadKey((v) => v + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败');
    }
  };

  const handleDelete = async (id: string, term: string) => {
    if (!window.confirm(`删除术语「${term}」？`)) return;
    try {
      await deleteGlossaryEntry(id);
      setReloadKey((v) => v + 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除失败');
    }
  };

  if (!clientId) return null;

  return (
    <div className="space-y-4 rounded-2xl border border-gray-100 bg-white p-5">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-[9px] font-semibold uppercase tracking-[0.18em] text-gray-400">
            Client Glossary
          </div>
          <div className="mt-0.5 text-[13px] font-medium tracking-tight text-gray-800">
            客户专有术语库 <span className="ml-1 text-gray-400">{entries.length}</span>
          </div>
        </div>
      </div>

      <p className="text-[10.5px] leading-snug text-gray-500">
        登记这位客户的内部黑话 / 产品代号 / 专有名词,让 AI 回答时使用正确含义。
        例如 <span className="text-gray-700">「红队」</span>在这位客户里可能意指内部审计组。
      </p>

      {error && <p className="text-[11px] font-medium text-rose-600">{error}</p>}

      <div className="space-y-1.5 rounded-xl bg-[#FAFAFA] px-3 py-2.5 ring-1 ring-inset ring-gray-100">
        <input
          type="text"
          placeholder="术语,例如 红队 / 曙光计划"
          className="w-full rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-[12px] text-gray-800 placeholder-gray-400 focus:border-[#5B7BFE] focus:outline-none"
          value={newTerm}
          onChange={(e) => setNewTerm(e.target.value)}
        />
        <input
          type="text"
          placeholder="含义(可选),例如:客户内部审计组的称呼"
          className="w-full rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-[12px] text-gray-800 placeholder-gray-400 focus:border-[#5B7BFE] focus:outline-none"
          value={newDefinition}
          onChange={(e) => setNewDefinition(e.target.value)}
        />
        <button
          type="button"
          className="w-full rounded-md bg-[#5B7BFE] px-2 py-1.5 text-[11px] font-medium text-white hover:bg-[#4A63CF] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          onClick={() => void handleAdd()}
          disabled={!newTerm.trim()}
        >
          添加术语
        </button>
      </div>

      {loading && entries.length === 0 && (
        <p className="text-[11px] font-medium text-gray-400">加载中…</p>
      )}

      {!loading && entries.length === 0 && (
        <p className="text-[11px] font-medium leading-5 text-gray-400">
          这位客户暂时没有登记术语。上方添加第一条。
        </p>
      )}

      <ul className="divide-y divide-gray-100 overflow-hidden rounded-xl ring-1 ring-inset ring-gray-100">
        {entries.map((entry, idx) => {
          const isEditing = editingId === entry.id;
          return (
            <li
              key={entry.id}
              className={`px-3 py-2 ${idx % 2 === 1 ? 'bg-[#FAFAFA]' : 'bg-white'}`}
            >
              {isEditing ? (
                <div className="space-y-1.5">
                  <input
                    type="text"
                    className="w-full rounded-md border border-gray-200 bg-white px-2 py-1 text-[12px] focus:border-[#5B7BFE] focus:outline-none"
                    value={editTerm}
                    onChange={(e) => setEditTerm(e.target.value)}
                  />
                  <input
                    type="text"
                    className="w-full rounded-md border border-gray-200 bg-white px-2 py-1 text-[12px] focus:border-[#5B7BFE] focus:outline-none"
                    value={editDefinition}
                    onChange={(e) => setEditDefinition(e.target.value)}
                  />
                  <div className="flex gap-1.5">
                    <button
                      type="button"
                      className="flex-1 rounded-md bg-emerald-50 px-2 py-1 text-[10px] font-medium text-emerald-700 ring-1 ring-inset ring-emerald-200 hover:bg-emerald-100/70 transition-colors"
                      onClick={() => void handleSaveEdit(entry.id)}
                    >
                      保存
                    </button>
                    <button
                      type="button"
                      className="flex-1 rounded-md bg-white px-2 py-1 text-[10px] font-medium text-gray-600 ring-1 ring-inset ring-gray-200 hover:bg-gray-50 transition-colors"
                      onClick={() => setEditingId(null)}
                    >
                      取消
                    </button>
                  </div>
                </div>
              ) : (
                <div>
                  <div className="flex items-start justify-between gap-2">
                    <p className="text-[12px] font-medium text-gray-800">{entry.term}</p>
                    <div className="flex gap-2">
                      <button
                        type="button"
                        className="text-[10px] font-medium text-gray-500 hover:text-[#5B7BFE] transition-colors"
                        onClick={() => {
                          setEditingId(entry.id);
                          setEditTerm(entry.term);
                          setEditDefinition(entry.definition);
                        }}
                      >
                        编辑
                      </button>
                      <button
                        type="button"
                        className="text-[10px] font-medium text-gray-500 hover:text-rose-600 transition-colors"
                        onClick={() => void handleDelete(entry.id, entry.term)}
                      >
                        删除
                      </button>
                    </div>
                  </div>
                  {entry.definition && (
                    <p className="mt-0.5 text-[11px] leading-5 text-gray-600">{entry.definition}</p>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
