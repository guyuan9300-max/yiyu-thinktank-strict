import React from 'react';
import { Gauge, Sparkles, Wrench } from 'lucide-react';

const GROUPS = [
  { key: '新增', label: '新增功能', icon: Sparkles, tone: 'text-indigo-600' },
  { key: '修复', label: '问题修复', icon: Wrench, tone: 'text-emerald-600' },
  { key: '优化', label: '体验优化', icon: Gauge, tone: 'text-sky-600' },
] as const;

interface UpdateContentCardProps {
  version: string;
  userNotes?: Record<string, string[]> | null;
}

export function UpdateContentCard({ version, userNotes }: UpdateContentCardProps): React.ReactElement | null {
  const groups = GROUPS
    .map((group) => ({ ...group, items: (userNotes?.[group.key] || []).filter(Boolean) }))
    .filter((group) => group.items.length > 0);
  if (!groups.length) return null;

  return (
    <div className="mt-3 rounded-lg border border-blue-100 bg-white/75 p-3">
      <p className="text-[11px] font-semibold text-blue-900">版本 {version} 更新内容</p>
      <div className="mt-2.5 space-y-2.5">
        {groups.map((group) => {
          const Icon = group.icon;
          return (
            <div key={group.key} className="flex gap-2.5">
              <Icon size={13} className={`mt-[2px] shrink-0 ${group.tone}`} />
              <div className="min-w-0">
                <p className="text-[11px] font-semibold text-gray-700">{group.label}</p>
                <ul className="mt-1 space-y-0.5">
                  {group.items.map((item) => (
                    <li key={item} className="text-[11px] leading-5 text-gray-500">{item}</li>
                  ))}
                </ul>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
