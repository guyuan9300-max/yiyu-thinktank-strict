import React from 'react';
import { ArrowRight, FolderOpen, UploadCloud } from 'lucide-react';

import type { ClientDnaModule, KnowledgeJob, KnowledgeStatus, ProjectFlow, ProjectFlowPayload, ProjectModule, ProjectModulePayload } from '../../../shared/types';

type ClientDnaModuleMeta = {
  moduleKey: ClientDnaModule['moduleKey'];
  title: string;
  helper: string;
};

type ImportFeedback = {
  tone: 'info' | 'success' | 'error';
  text: string;
  detail?: string;
  timestamp: number;
};

// 极简版：只保留"导入文件 / 导入文件夹"按钮 + 导入反馈消息条。
// props 签名保持不变以避免破坏 App.tsx 的调用方；body 内未使用的 props 是历史遗留，
// 待 App.tsx 调用方一并瘦身时再删。
type ClientProjectSetupPageProps = {
  clientName: string;
  modules: ClientDnaModule[];
  projectModules: ProjectModule[];
  projectFlows: ProjectFlow[];
  moduleMetas: ClientDnaModuleMeta[];
  sourceDocumentCount: number;
  isKnowledgeBuilding: boolean;
  knowledgeStatus?: KnowledgeStatus | null;
  latestKnowledgeJob?: KnowledgeJob | null;
  isImportSubmitting?: boolean;
  isTemplateFilling?: boolean;
  latestImportFeedback?: ImportFeedback | null;
  onImportFiles: () => void;
  onImportFolder: () => void;
  onGenerateCandidates: () => void;
  onCopyModulePrompt: (moduleKey: ClientDnaModule['moduleKey']) => void;
  onUploadModule: (moduleKey: ClientDnaModule['moduleKey']) => void;
  onCreateProjectModule: (payload: ProjectModulePayload) => void;
  onCreateProjectFlow: (payload: ProjectFlowPayload) => void;
  onOpenDnaPanel: () => void;
  onContinueWorkspace: () => void;
};

export function ClientProjectSetupPage({
  latestImportFeedback,
  onImportFiles,
  onImportFolder,
  onContinueWorkspace,
}: ClientProjectSetupPageProps) {
  const latestImportToneClasses = latestImportFeedback?.tone === 'error'
    ? 'border-rose-200 bg-rose-50 text-rose-700'
    : latestImportFeedback?.tone === 'success'
      ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
      : 'border-gray-200 bg-gray-50 text-gray-600';

  return (
    <div className="space-y-6 xl:space-y-7">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <button
          type="button"
          onClick={onImportFiles}
          className="inline-flex items-center justify-center gap-2 rounded-2xl bg-[#5B7BFE] px-5 py-4 text-[14px] font-bold text-white shadow-[0_12px_24px_rgba(91,123,254,0.28)] transition-colors hover:bg-[#4A6BE6]"
        >
          <UploadCloud size={17} />
          导入文件
        </button>
        <button
          type="button"
          onClick={onImportFolder}
          className="inline-flex items-center justify-center gap-2 rounded-2xl border border-[#D8E5FF] bg-white px-5 py-4 text-[14px] font-bold text-[#4A63CF] transition-colors hover:border-[#5B7BFE] hover:text-[#3652c9]"
        >
          <FolderOpen size={17} />
          导入文件夹
        </button>
      </div>

      <div className="flex justify-end">
        <button
          type="button"
          onClick={onContinueWorkspace}
          className="inline-flex items-center gap-2 rounded-xl px-3 py-2 text-[13px] font-semibold text-[#4A63CF] transition-colors hover:bg-[#EEF3FF] hover:text-[#3652c9]"
        >
          暂不导入，直接提问
          <ArrowRight size={15} />
        </button>
      </div>

      {latestImportFeedback && (
        <div className={`rounded-2xl border px-4 py-3 text-[12px] leading-6 ${latestImportToneClasses}`}>
          <p className="font-semibold">{latestImportFeedback.text}</p>
          {latestImportFeedback.detail && (
            <p className="mt-1 opacity-80">{latestImportFeedback.detail}</p>
          )}
        </div>
      )}
    </div>
  );
}
