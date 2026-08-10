import React from 'react';

type WorkStatusPanelProps = {
  contextQualityLabel?: string | null;
  primarySources?: string[];
  routeIntent?: string | null;
  actionSuggestionTitles?: string[];
  proposalTitles?: string[];
  missingContext?: string[];
  boundaryNotes?: string[];
};

function Chip({ children }: { children: React.ReactNode }) {
  return <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-bold text-slate-600">{children}</span>;
}

export function WorkStatusPanel({
  contextQualityLabel,
  primarySources = [],
  routeIntent,
  actionSuggestionTitles = [],
  proposalTitles = [],
  missingContext = [],
  boundaryNotes = [],
}: WorkStatusPanelProps) {
  const notes = [...boundaryNotes, ...missingContext].filter(Boolean).slice(0, 4);
  return (
    <div className="space-y-3 rounded-3xl border border-slate-100 bg-white p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Chip>{routeIntent || '工作状态'}</Chip>
        {contextQualityLabel && <Chip>{contextQualityLabel}</Chip>}
        {primarySources.slice(0, 3).map((source) => <Chip key={source}>{source}</Chip>)}
      </div>
      {(actionSuggestionTitles.length > 0 || proposalTitles.length > 0) && (
        <div className="space-y-1.5 text-[12px] leading-5 text-slate-700">
          {[...actionSuggestionTitles, ...proposalTitles].slice(0, 5).map((item) => <p key={item}>• {item}</p>)}
        </div>
      )}
      {notes.length > 0 && (
        <div className="rounded-2xl bg-amber-50 px-4 py-3 text-[12px] leading-5 text-amber-800">
          {notes.map((item) => <p key={item}>{item}</p>)}
        </div>
      )}
    </div>
  );
}
