import React, { useEffect, useImperativeHandle, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { alertWithLog } from '../../lib/clientErrorReport';
import { appPrompt } from '../../lib/appPrompt';
import {
  Sparkles,
  Wand2,
  FileText,
  Languages,
  PenTool as PenToolIcon,
  Type,
  Palette,
  Download,
  History,
  Users as UsersIcon,
  Maximize2,
  Table as TableIcon,
  Image as ImageIcon,
  Link as LinkIcon,
  Scale,
  ShieldCheck,
  Loader2,
  X,
  Play,
  Paperclip,
  Undo2,
  Redo2,
} from 'lucide-react';

import type { DocumentAiAction, DocumentAiCreativityMode } from '../../lib/api';

export type RichTextDocumentEditorWritingSkill = {
  id: string;
  name: string;
};

export type RichTextDocumentEditorAgentSkill = {
  skillId: string;
  shortName: string;
};

export type RichTextDocumentEditorAiOpts = {
  userRequest: string;
  creativityMode: DocumentAiCreativityMode;
  activeSkillId: string | null;
  activeAgentSkillIds: string[];
  // P14a：当前用户在编辑器里框选的纯文本（window.getSelection()）。
  // 非空 → 后端只处理这段、前端只替换这段；空 → 处理整篇。
  selectionText: string;
  // 用户在 popover 弹出时已 attach 的 working docs(可能后续在 popover 里 × 掉)
  workingDocumentIds: string[];
};

// P14a：onAiAction 的返回值。
// targetScope = "selection"      → 用户框选了一段:还原选区 → insertMarkdown 替换选区
// targetScope = "cursor_insert"  → 用户没框选,只有光标:还原光标位置 → insertMarkdown 在光标处插入,不动其他内容
// targetScope = "full_doc"       → 替换整篇(老逻辑,目前后端只在 fallback 路径用)
// 不返回（undefined）→ App 层已经自己处理过（兼容 __export_docx / __toggle_fullscreen）
export type RichTextDocumentEditorAiResult = {
  content: string;
  targetScope: 'selection' | 'cursor_insert' | 'full_doc';
};

import {
  MDXEditor,
  type MDXEditorMethods,
  headingsPlugin,
  listsPlugin,
  quotePlugin,
  thematicBreakPlugin,
  markdownShortcutPlugin,
  linkPlugin,
  linkDialogPlugin,
  tablePlugin,
  imagePlugin,
  toolbarPlugin,
  BoldItalicUnderlineToggles,
  StrikeThroughSupSubToggles,
  UndoRedo,
  // 替换 BlockTypeSelect + ListsToggle 为自定义中文下拉（BlockChoiceSelect），
  // 直接读 currentBlockType$/currentListType$ 并通过 applyBlockType$/applyListType$ 切换。
  currentBlockType$,
  applyBlockType$,
  currentListType$,
  applyListType$,
  // Tab Bar 常驻 Undo/Redo:跨 tab 不卸载,避免 UndoRedo 切走再回来 canUndo state 丢失
  activeEditor$,
} from '@mdxeditor/editor';
import { CAN_UNDO_COMMAND, CAN_REDO_COMMAND, UNDO_COMMAND, REDO_COMMAND, COMMAND_PRIORITY_CRITICAL } from 'lexical';
import { useCellValues, usePublisher } from '@mdxeditor/gurx';
import { ChevronDown } from 'lucide-react';
import '@mdxeditor/editor/style.css';

/**
 * 客户工作台内嵌"WPS / Word 风格"文档编辑器。
 *
 * Ribbon UI：5 个 tab，模仿 Word 的功能组分布
 *   开始     —— 字体/段落/列表（全 wire，复用 MDXEditor primitives）
 *   插入     —— 表格/图片/链接/代码块/分割线（全 wire）
 *   AI 助手  —— 扩写/改写/总结/翻译/风格切换（stub，下一步打通 AI）
 *   文档     —— 历史/版本/导出/可见性（stub）
 *   样式     —— 主题色/字号（stub）
 *
 * MDXEditor primitives（BoldItalicUnderlineToggles 等）必须在 toolbarPlugin
 * 的 toolbarContents 里渲染，否则没 lexical context 会崩——所以整个 ribbon UI
 * 也必须包在 toolbarContents 内。
 */
export type RichTextDocumentEditorProps = {
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  /** 强制最小高度（默认 360px） */
  minHeight?: number;
  /** 是否禁用编辑（保存中可以传 true） */
  readOnly?: boolean;
  /**
   * AI action 执行回调。
   * - action ∈ DocumentAiAction：调 backend，**应该 return 后端结果**（编辑器据此决定替换选区还是替换全文）。
   * - action='__export_docx' / '__toggle_fullscreen' 等本地动作：App 层自己处理，返回 void/undefined 即可。
   * opts.selectionText 是用户当前框选的纯文本，由编辑器组件在调用前抓取塞进来。
   */
  onAiAction?: (
    action: string,
    opts?: RichTextDocumentEditorAiOpts,
  ) => Promise<RichTextDocumentEditorAiResult | void> | RichTextDocumentEditorAiResult | void;
  /** 写作风格 skill 列表（来自 listWritingSkills 接口） */
  writingSkills?: RichTextDocumentEditorWritingSkill[];
  /** 默认选中的 skill id（从外部 chat 同步） */
  defaultActiveSkillId?: string | null;
  /** 当前工作台已启用的声明式 Agent Skill；与写作风格分别生效。 */
  agentSkills?: RichTextDocumentEditorAgentSkill[];
  defaultActiveAgentSkillIds?: string[];
  /** 默认写作模式（从外部 chat 同步） */
  defaultCreativityMode?: DocumentAiCreativityMode;
  /**
   * 用户从右侧文件列表 attach 到对话引用的 working docs(跟 chat composer 共用 state)。
   * AI 生成 popover 渲染为 chip 列表,提交时一起发给后端做 priority 召回。
   */
  workingDocuments?: RichTextDocumentEditorWorkingDoc[];
  /** 让用户在 popover 里点 × 移除某条 working doc(透传到外层 setActiveWorkingDocuments) */
  onRemoveWorkingDocument?: (documentId: string) => void;
  /**
   * 外部触发 AI popover 自动打开的 counter — 用户在右侧文件列表点 ← 箭头 attach 文件后,
   * 外层 bump 这个 key,编辑器 useEffect 检测到变化就开 popover,
   * popover 里直接显示刚加进来的 chip,用户接着输入指令就可以执行。
   */
  triggerOpenAiPromptKey?: number;
  /** 事件线报告已有独立的保存和下载区时，可隐藏会造成共享语义歧义的“文档”页。 */
  showDocumentTab?: boolean;
};

export type RichTextDocumentEditorWorkingDoc = {
  documentId: string;
  title: string;
  status: 'queued' | 'processing' | 'ready' | 'partial_ready' | 'failed';
};

// P12：字号 + 主题 内置 state（不污染 App.tsx）
type EditorFontSize = 'sm' | 'md' | 'lg' | 'xl';
type EditorTheme = 'light' | 'sepia' | 'dark';

const FONT_SIZE_CLASS: Record<EditorFontSize, string> = {
  sm: 'text-[13px] leading-6',
  md: 'text-[14px] leading-7',
  lg: 'text-[16px] leading-8',
  xl: 'text-[18px] leading-9',
};
const FONT_SIZE_LABEL: Record<EditorFontSize, string> = {
  sm: '小', md: '中', lg: '大', xl: '特大',
};

const THEME_WRAPPER_CLASS: Record<EditorTheme, string> = {
  light: 'bg-white',
  sepia: 'bg-[#FBF7EC]',
  dark: 'bg-slate-900',
};
const THEME_CONTENT_CLASS: Record<EditorTheme, string> = {
  light: 'text-gray-900',
  sepia: 'text-stone-800',
  dark: 'text-slate-100 prose-invert',
};
const THEME_LABEL: Record<EditorTheme, string> = {
  light: '默认', sepia: '米黄', dark: '暗夜',
};

export const RichTextDocumentEditor = React.forwardRef<MDXEditorMethods, RichTextDocumentEditorProps>(
  function RichTextDocumentEditor(
    {
      value,
      onChange,
      placeholder,
      minHeight = 360,
      readOnly = false,
      onAiAction,
      writingSkills,
      defaultActiveSkillId,
      agentSkills,
      defaultActiveAgentSkillIds,
      defaultCreativityMode,
      workingDocuments,
      onRemoveWorkingDocument,
      triggerOpenAiPromptKey,
      showDocumentTab = true,
    },
    ref,
  ) {
    const editorRef = useRef<MDXEditorMethods>(null);
    const contentRevisionRef = useRef(0);
    useImperativeHandle(ref, () => editorRef.current as MDXEditorMethods, []);
    const [fontSize, setFontSize] = useState<EditorFontSize>('md');
    const [theme, setTheme] = useState<EditorTheme>('light');

    const contentClass = `prose prose-sm mx-auto max-w-[880px] px-10 py-10 min-h-[480px] outline-none ${FONT_SIZE_CLASS[fontSize]} ${THEME_CONTENT_CLASS[theme]}`;

    return (
      <div
        className={`mdx-placeholder-light transition ${THEME_WRAPPER_CLASS[theme]}`}
        style={{ minHeight }}
      >
        <MDXEditor
          ref={editorRef}
          markdown={value}
          onChange={(next) => {
            contentRevisionRef.current += 1;
            onChange(next);
          }}
          placeholder={placeholder || '把文档贴在这里，或直接开始写。⌘B 加粗、⌘I 斜体；从飞书/网页粘贴时格式保留。'}
          readOnly={readOnly}
          contentEditableClassName={contentClass}
          plugins={[
            headingsPlugin(),
            listsPlugin(),
            quotePlugin(),
            thematicBreakPlugin(),
            linkPlugin(),
            linkDialogPlugin(),
            tablePlugin(),
            imagePlugin(),
            markdownShortcutPlugin(),
            toolbarPlugin({
              toolbarClassName:
                'sticky top-0 z-20 border-b border-gray-200 bg-white px-0 py-0 flex flex-col items-stretch',
              toolbarContents: () => (
                <RibbonToolbar
                  onAiAction={onAiAction}
                  editorRef={editorRef}
                  notifyContentChange={onChange}
                  contentRevisionRef={contentRevisionRef}
                  writingSkills={writingSkills || []}
                  defaultActiveSkillId={defaultActiveSkillId ?? null}
                  agentSkills={agentSkills || []}
                  defaultActiveAgentSkillIds={defaultActiveAgentSkillIds || []}
                  defaultCreativityMode={defaultCreativityMode ?? 'balanced'}
                  workingDocuments={workingDocuments || []}
                  onRemoveWorkingDocument={onRemoveWorkingDocument}
                  triggerOpenAiPromptKey={triggerOpenAiPromptKey}
                  showDocumentTab={showDocumentTab}
                  fontSize={fontSize}
                  setFontSize={setFontSize}
                  theme={theme}
                  setTheme={setTheme}
                />
              ),
            }),
          ]}
        />
      </div>
    );
  },
);

// ─── Ribbon Toolbar ───────────────────────────────────────────────
// 模仿 Word/WPS 顶部多 tab 工具栏。MDXEditor primitives（B I U / 块类型 / 列表
// / 链接 / 表格 / 图片）保持原生组件，外层 wrap tailwind 样式让视觉一致。

type RibbonTab = 'start' | 'insert' | 'ai' | 'document' | 'style';

const RIBBON_TABS: { key: RibbonTab; label: string }[] = [
  { key: 'start', label: '开始' },
  { key: 'insert', label: '插入' },
  { key: 'ai', label: 'AI 助手' },
  { key: 'document', label: '文档' },
  { key: 'style', label: '样式' },
];

// AI 提示面板的 UI state（点 AI 按钮才显示）
type AiPromptState = {
  action: DocumentAiAction;
  label: string;
  userRequest: string;
  creativityMode: DocumentAiCreativityMode;
  activeSkillId: string | null;
  activeAgentSkillIds: string[];
  submitting: boolean;
};

const AI_ACTION_LABELS: Record<DocumentAiAction, string> = {
  expand: '扩写',
  rewrite_pro: '改写 · 专业',
  rewrite_short: '改写 · 简洁',
  summarize: '总结',
  extract: '提取要点',
  translate: '翻译',
  style_distilled: '切换语言风格',
  // P13b/c：资料增强类 op 人话名（UI 暴露见 P13c 改版）
  insert_from_materials: '从资料生成此处',
  rewrite_by_strategy: '按战略方向重写',
  insert_data_table: '插入数据表',
};

function RibbonToolbar({
  onAiAction,
  editorRef,
  notifyContentChange,
  contentRevisionRef,
  writingSkills,
  defaultActiveSkillId,
  agentSkills,
  defaultActiveAgentSkillIds,
  defaultCreativityMode,
  workingDocuments,
  onRemoveWorkingDocument,
  triggerOpenAiPromptKey,
  showDocumentTab,
  fontSize,
  setFontSize,
  theme,
  setTheme,
}: {
  onAiAction?: (
    action: string,
    opts?: RichTextDocumentEditorAiOpts,
  ) => Promise<RichTextDocumentEditorAiResult | void> | RichTextDocumentEditorAiResult | void;
  editorRef: React.RefObject<MDXEditorMethods | null>;
  /** P14a：editor.setMarkdown() 不一定触发 onChange，AI 应用结果后手动通知父组件同步 */
  notifyContentChange?: (next: string) => void;
  contentRevisionRef: React.MutableRefObject<number>;
  writingSkills: RichTextDocumentEditorWritingSkill[];
  defaultActiveSkillId: string | null;
  agentSkills: RichTextDocumentEditorAgentSkill[];
  defaultActiveAgentSkillIds: string[];
  defaultCreativityMode: DocumentAiCreativityMode;
  workingDocuments: RichTextDocumentEditorWorkingDoc[];
  onRemoveWorkingDocument?: (documentId: string) => void;
  triggerOpenAiPromptKey?: number;
  showDocumentTab: boolean;
  fontSize: EditorFontSize;
  setFontSize: React.Dispatch<React.SetStateAction<EditorFontSize>>;
  theme: EditorTheme;
  setTheme: React.Dispatch<React.SetStateAction<EditorTheme>>;
}) {
  const [active, setActive] = useState<RibbonTab>('start');
  const ribbonTabs = showDocumentTab
    ? RIBBON_TABS
    : RIBBON_TABS.filter((tab) => tab.key !== 'document');
  const [aiPrompt, setAiPrompt] = useState<AiPromptState | null>(null);
  // P14a：用户点 AI 按钮"那一刻"的选区快照（用于送到后端做 prompt 上下文）。
  const [capturedSelection, setCapturedSelection] = useState<string>('');
  // WPS 风浮层定位:popover 出现在 caret/选区附近,而不是 toolbar 下方全宽。
  const [popoverAnchor, setPopoverAnchor] = useState<{ top: number; left: number } | null>(null);
  // 异步 AI 任务托盘:submit 后立刻关 popover + 设这个 state,Tab Bar 右侧渲染进度环 icon,
  // 用户可继续干别的;完成后插入结果到 snapshot.range,清 state,icon 隐藏。
  const [aiInFlight, setAiInFlight] = useState<{ startedAt: number; action: DocumentAiAction } | null>(null);
  const aiInFlightRef = useRef(aiInFlight);
  aiInFlightRef.current = aiInFlight;
  // 飞行期保留的选区 Range:让"框选高光"在 AI 改写期间持续存在并锁定该段,
  // 滚动/resize 时据此重算高光矩形,结果回来替换后再清。
  const flightRangeRef = useRef<Range | null>(null);
  // 飞行期用户再发(右键 / ← 箭头)→ icon 闪一下提示"AI 在跑别再发"
  const [flashing, setFlashing] = useState(false);
  const flashingTimeoutRef = useRef<number | null>(null);
  const triggerFlashIcon = () => {
    setFlashing(true);
    if (flashingTimeoutRef.current !== null) window.clearTimeout(flashingTimeoutRef.current);
    flashingTimeoutRef.current = window.setTimeout(() => setFlashing(false), 550);
  };
  // 组件卸载时清挂着的 flashing timeout, 防止 setFlashing 在已 unmounted 组件上调用
  // (用户在 AI 飞行中切走文档 tab 就会触发).
  useEffect(() => {
    return () => {
      if (flashingTimeoutRef.current !== null) {
        window.clearTimeout(flashingTimeoutRef.current);
        flashingTimeoutRef.current = null;
      }
    };
  }, []);
  // P14a-fix：DOM Range 快照。点 AI 按钮后 AiPromptPanel textarea 抢 focus 会把
  // window selection 清空，提交时已经没法用 window.getSelection() 拿回选区。
  // 用 ref 而不是 state——Range 是 mutable DOM 对象，存在 ref 里避免 stale closure。
  // submit 时把这个 Range 还原回 window selection，editor 内部 Lexical 选区会
  // 自动从 DOM selection 同步，然后 editor.insertMarkdown() 就能精确替换选区。
  const capturedRangeRef = useRef<Range | null>(null);

  // 蓝色选区高亮叠层的矩形(viewport 坐标)。AI popover 打开期间,把"将被替换/参考的
  // 选区"用蓝框画出来——原生 selection 在 popover textarea autoFocus 后会消失,用 overlay 还原视觉。
  const [aiSelectionRects, setAiSelectionRects] = useState<DOMRect[]>([]);

  // P14b-fix：持续记录"最后一次编辑器内的非空选区"。
  // 病根:从编辑器外部触发 AI(右侧文件列表 ← 箭头引用文件)时,那一下点击发生在
  // contentEditable 外,浏览器已把 window selection 清空;随后 openAiPrompt 再抓就抓到空,
  // selectionText='' → 后端只能判 cursor_insert(光标插入而非替换选区)。
  // 有了这个 ref,captureCurrentSelection 在当前选区为空时回退到它,拿回真正要替换的那段。
  const lastSelectionRef = useRef<{ range: Range; text: string } | null>(null);
  useEffect(() => {
    const onSelChange = () => {
      const sel = window.getSelection();
      if (!sel || sel.rangeCount === 0) return;
      const anchor = sel.anchorNode;
      const el = anchor
        ? anchor.nodeType === 1
          ? (anchor as Element)
          : anchor.parentElement
        : null;
      const insideEditor = !!(el && typeof el.closest === 'function' && el.closest('[contenteditable="true"]'));
      if (sel.isCollapsed) {
        // 用户在编辑器内主动放光标 = 放弃选区 → 清记忆,避免后续误替换旧选区。
        // (在编辑器外点击导致的折叠不在此列:insideEditor=false 时不动 ref。)
        if (insideEditor) lastSelectionRef.current = null;
        return;
      }
      if (!insideEditor) return;
      const text = sel.toString().trim();
      if (!text) return;
      lastSelectionRef.current = { range: sel.getRangeAt(0).cloneRange(), text };
    };
    document.addEventListener('selectionchange', onSelChange);
    return () => document.removeEventListener('selectionchange', onSelChange);
  }, []);

  // 飞行期(AI 改写中):选区高光持续显示并锁定该段。滚动/resize 时按 flightRange 重算矩形,
  // 保证高光始终贴住那段文字;飞行结束(结果替换 / 失败)由 finally 清掉高光。
  useEffect(() => {
    if (!aiInFlight || !flightRangeRef.current) return;
    const recompute = () => {
      const range = flightRangeRef.current;
      if (range) setAiSelectionRects(Array.from(range.getClientRects()));
    };
    recompute();
    window.addEventListener('scroll', recompute, true);
    window.addEventListener('resize', recompute);
    return () => {
      window.removeEventListener('scroll', recompute, true);
      window.removeEventListener('resize', recompute);
    };
  }, [aiInFlight]);

  const captureCurrentSelection = (): { text: string; range: Range | null; rect: DOMRect | null } => {
    try {
      const sel = window.getSelection();
      // Case A:当前确实有非空选区 → 直接用(右键 / AI tab 按钮等编辑器内触发)。
      if (sel && sel.rangeCount > 0 && !sel.isCollapsed) {
        const text = sel.toString().trim();
        if (text) {
          const range = sel.getRangeAt(0).cloneRange();
          return { text, range, rect: range.getBoundingClientRect() };
        }
      }
      // Case B:当前选区为空,但最近在编辑器里框选过 → 回退到那次选区。
      // 专治"右侧文件 ← 箭头引用文件触发 AI":点击在编辑器外,window selection 已被清空,
      // 这里靠 lastSelectionRef 拿回真正要替换的那段(只在 DOM 节点仍在场时才用)。
      const last = lastSelectionRef.current;
      if (
        last &&
        document.contains(last.range.startContainer) &&
        document.contains(last.range.endContainer)
      ) {
        const range = last.range.cloneRange();
        return { text: last.text, range, rect: range.getBoundingClientRect() };
      }
      // Case C:真·纯光标(无选区也无记忆)→ 还原折叠 Range 给 cursor_insert 用。
      if (sel && sel.rangeCount > 0) {
        const range = sel.getRangeAt(0).cloneRange();
        return { text: '', range, rect: range.getBoundingClientRect() };
      }
      return { text: '', range: null, rect: null };
    } catch {
      return { text: '', range: null, rect: null };
    }
  };

  // 算 popover viewport 坐标:输入是"参考点"(可以是选区 rect 或一个 click 坐标点),
  // 输出是 popover 左上角坐标,做了 right/bottom 边缘 flip。
  const computePopoverAnchor = (refRect: {
    top: number; left: number; bottom: number; right: number;
  }): { top: number; left: number } => {
    const POPOVER_W = 360;
    const POPOVER_H = 160;
    const MARGIN = 12;
    let left = refRect.left;
    let top = refRect.bottom + 8;
    if (left + POPOVER_W + MARGIN > window.innerWidth) {
      left = window.innerWidth - POPOVER_W - MARGIN;
    }
    if (top + POPOVER_H + MARGIN > window.innerHeight) {
      top = Math.max(MARGIN, refRect.top - POPOVER_H - 8);
    }
    if (left < MARGIN) left = MARGIN;
    return { top, left };
  };

  const openAiPrompt = (
    action: DocumentAiAction,
    labelOverride?: string,
    // 右键触发时传 click 坐标(零宽零高的"点 rect");不传则按 caret/选区算
    anchorOverride?: { top: number; left: number; bottom: number; right: number },
  ) => {
    const { text, range, rect } = captureCurrentSelection();
    setCapturedSelection(text);
    capturedRangeRef.current = range;
    // 画蓝色选区高亮(仅当真有选区;纯光标 cursor_insert 场景不画)
    setAiSelectionRects(text && range ? Array.from(range.getClientRects()) : []);
    let anchor: { top: number; left: number };
    if (anchorOverride) {
      anchor = computePopoverAnchor(anchorOverride);
    } else if (rect && rect.width + rect.height > 0) {
      anchor = computePopoverAnchor(rect);
    } else {
      // 兜底:落到 viewport 中上
      anchor = {
        top: window.innerHeight / 3,
        left: Math.max(12, (window.innerWidth - 360) / 2),
      };
    }
    setPopoverAnchor(anchor);
    setAiPrompt({
      action,
      label: labelOverride ?? AI_ACTION_LABELS[action],
      userRequest: '',
      creativityMode: defaultCreativityMode,
      activeSkillId: defaultActiveSkillId,
      activeAgentSkillIds: defaultActiveAgentSkillIds.filter((id) =>
        agentSkills.some((skill) => skill.skillId === id),
      ).slice(0, 5),
      submitting: false,
    });
  };

  // 全局 contextmenu 监听 — 用户在编辑器内容区右键 = 触发 AI 生成 popover
  // 在 click 坐标处弹出;阻止浏览器默认菜单。
  // 飞行期(aiInFlightRef 非 null):不开 popover,触发 icon flash 提示。
  const openAiPromptRef = useRef(openAiPrompt);
  openAiPromptRef.current = openAiPrompt;
  const triggerFlashIconRef = useRef(triggerFlashIcon);
  triggerFlashIconRef.current = triggerFlashIcon;
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      const target = e.target as Element | null;
      if (!target || typeof target.closest !== 'function') return;
      // 只在 contentEditable 编辑区域内的右键才劫持(避免 toolbar / 外部右键被吃掉)
      if (!target.closest('[contenteditable="true"]')) return;
      e.preventDefault();
      // 飞行期 block + flash 提示
      if (aiInFlightRef.current) {
        triggerFlashIconRef.current();
        return;
      }
      openAiPromptRef.current('expand', 'AI 生成', {
        top: e.clientY,
        left: e.clientX,
        bottom: e.clientY,
        right: e.clientX,
      });
    };
    document.addEventListener('contextmenu', handler);
    return () => document.removeEventListener('contextmenu', handler);
  }, []);

  // 外部触发(用户在右侧文件 ← 箭头 attach 文件)→ 自动开 popover。
  // 飞行期 block + flash 提示;文件还是 attach 到 state 了(外层做的),只是 popover 不弹。
  const prevTriggerKeyRef = useRef<number | undefined>(triggerOpenAiPromptKey);
  useEffect(() => {
    if (triggerOpenAiPromptKey === undefined) return;
    if (prevTriggerKeyRef.current === triggerOpenAiPromptKey) return;
    prevTriggerKeyRef.current = triggerOpenAiPromptKey;
    if (triggerOpenAiPromptKey > 0) {
      if (aiInFlightRef.current) {
        triggerFlashIconRef.current();
        return;
      }
      openAiPromptRef.current('expand', 'AI 生成');
    }
  }, [triggerOpenAiPromptKey]);

  // popover 打开期间,滚动 / resize 时重算蓝色选区框坐标,保持和文字对齐。
  useEffect(() => {
    if (!aiPrompt) return;
    const recompute = () => {
      const range = capturedRangeRef.current;
      if (!range || !document.contains(range.startContainer)) {
        setAiSelectionRects([]);
        return;
      }
      const rects = Array.from(range.getClientRects());
      if (rects.length > 0) setAiSelectionRects(rects);
    };
    window.addEventListener('scroll', recompute, true);
    window.addEventListener('resize', recompute);
    return () => {
      window.removeEventListener('scroll', recompute, true);
      window.removeEventListener('resize', recompute);
    };
  }, [aiPrompt]);

  // P14a-fix：把 capturedRangeRef 里的 DOM Range 还原回 window selection，
  // 让 editor 重新拿到这段选区。返回 true 表示还原成功。
  // 失败情况：Range 引用的 DOM 节点已被卸载/重渲染（极少数，比如 AI 调用期间
  // 父组件强制更新了 value prop）。
  // 还原 Range(选区 OR 光标位置)。
  // - 选区场景:还原非折叠 Range,后续 insertMarkdown 是"替换选区"
  // - 光标场景:还原折叠 Range(只有光标位置),后续 insertMarkdown 是"光标处插入"
  // 任何场景下成功还原都返 true(老版本只对非折叠返 true,导致光标位置无法走 insert 路径)。
  const restoreCapturedRange = (): boolean => {
    const range = capturedRangeRef.current;
    if (!range) return false;
    try {
      // Range.startContainer 仍在 document 里才能用
      if (!document.contains(range.startContainer) || !document.contains(range.endContainer)) {
        return false;
      }
      const sel = window.getSelection();
      if (!sel) return false;
      sel.removeAllRanges();
      sel.addRange(range);
      return sel.rangeCount > 0;  // 折叠也算成功(光标位置仍是合法 insert 锚点)
    } catch {
      return false;
    }
  };

  const submitAiPrompt = async () => {
    if (!aiPrompt || !onAiAction) return;
    // 飞行期再次 submit(理论不该发生,popover 关了应该没法点) → flash 提示
    if (aiInFlightRef.current) {
      triggerFlashIcon();
      return;
    }
    // 1) snapshot 所有提交时刻状态(关 popover 后这些 state 会清,异步回来还要用)
    const snapshot = {
      action: aiPrompt.action,
      userRequest: aiPrompt.userRequest.trim(),
      creativityMode: aiPrompt.creativityMode,
      activeSkillId: aiPrompt.activeSkillId,
      activeAgentSkillIds: aiPrompt.activeAgentSkillIds,
      selectionText: capturedSelection,
      workingDocumentIds: workingDocuments
        .filter((d) => d.status !== 'failed')
        .map((d) => d.documentId),
      range: capturedRangeRef.current,
      contentRevision: contentRevisionRef.current,
    };
    // 2) 立刻关 popover + 设 in-flight(用户感觉"提交了就消失了,我可以干别的")
    setAiPrompt(null);
    setCapturedSelection('');
    setPopoverAnchor(null);
    // 选区场景:不再清高光 —— 让框选的那段在飞行期持续高亮并锁定,结果回来直接替换它,
    //          用户能清楚看到"刚替换了哪段"。把 range 存进 flightRangeRef 供滚动重算。
    // 光标插入场景(无选区):没有要锁的段,清掉高光。
    const flightRange = snapshot.range && !snapshot.range.collapsed ? snapshot.range : null;
    flightRangeRef.current = flightRange;
    if (!flightRange) setAiSelectionRects([]);
    capturedRangeRef.current = null;
    setAiInFlight({ startedAt: Date.now(), action: snapshot.action });
    try {
      const result = await onAiAction(snapshot.action, {
        userRequest: snapshot.userRequest,
        creativityMode: snapshot.creativityMode,
        activeSkillId: snapshot.activeSkillId,
        activeAgentSkillIds: snapshot.activeAgentSkillIds,
        selectionText: snapshot.selectionText,
        workingDocumentIds: snapshot.workingDocumentIds,
      });
      if (result && result.content) {
        if (contentRevisionRef.current !== snapshot.contentRevision) {
          throw new Error('AI 处理期间正文已被修改，已保留你的最新内容；请重新发起本次修改');
        }
        const editor = editorRef.current;
        if (editor) {
          // 还原 submit 那一刻的 range(用户在飞行期间可能光标动过,但我们要插到原位置)
          capturedRangeRef.current = snapshot.range;
          // 关键修复:用户在飞行期间可能点击编辑器其他位置,Lexical 内部 selection 已经移到 B。
          // 单纯 restoreCapturedRange + editor.focus 不会强制 Lexical 重新 sync(编辑器已 focused),
          // 导致 insertMarkdown 命中 B 而不是 A。
          // 解法:先 blur 当前 focused 元素 → Lexical 失去内部 selection → restoreCapturedRange 设
          // window selection 到 A → editor.focus 把 Lexical re-sync 到 A → insertMarkdown 命中 A。
          const activeEl = document.activeElement as HTMLElement | null;
          if (activeEl && typeof activeEl.blur === 'function') {
            try {
              activeEl.blur();
            } catch {
              /* blur 失败不阻断后续 */
            }
          }
          if (result.targetScope === 'selection' && snapshot.range) {
            const restored = restoreCapturedRange();
            if (restored) {
              editor.focus(() => {
                try {
                  editor.insertMarkdown(result.content);
                } catch (err) {
                  // eslint-disable-next-line no-console
                  console.warn('[submitAiPrompt selection] insertMarkdown failed', err);
                }
              });
            } else {
              throw new Error('选区已失效,请重新选中文字再点 AI');
            }
          } else if (result.targetScope === 'cursor_insert') {
            const restored = snapshot.range ? restoreCapturedRange() : false;
            if (restored) {
              editor.focus(() => {
                try {
                  editor.insertMarkdown(result.content);
                } catch (err) {
                  // eslint-disable-next-line no-console
                  console.warn('[submitAiPrompt cursor_insert] insertMarkdown failed', err);
                }
              });
            } else {
              editor.focus(
                () => {
                  try {
                    editor.insertMarkdown('\n\n' + result.content);
                  } catch (err) {
                    // eslint-disable-next-line no-console
                    console.warn('[submitAiPrompt cursor_insert fallback] insertMarkdown failed', err);
                  }
                },
                { defaultSelection: 'rootEnd' },
              );
            }
          } else {
            editor.setMarkdown(result.content);
            notifyContentChange?.(result.content);
            editor.focus();
          }
        }
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'AI 操作失败,请重试';
      // eslint-disable-next-line no-console
      console.warn('[editor-ai] submitAiPrompt failed:', err);
      alertWithLog(`⚠️ ${message}`, { feature: 'editor_ai_action', extra: { action: snapshot.action } });
    } finally {
      capturedRangeRef.current = null;
      // 飞行结束(结果已替换 / 失败):清掉锁定高光与飞行 range。
      flightRangeRef.current = null;
      setAiSelectionRects([]);
      setAiInFlight(null);
    }
  };

  return (
    <>
      {/* Tab Bar — AI 生成走"在编辑器内右键"触发;Tab Bar 右侧条件渲染 AI 进度 icon(只在飞行期可见) */}
      <div className="flex items-center justify-between border-b border-gray-100 bg-[#FAFAFB] px-3">
        <div className="flex items-center gap-0">
          {ribbonTabs.map((tab) => {
            const isActive = active === tab.key;
            return (
              <button
                key={tab.key}
                type="button"
                onClick={() => {
                  setActive(tab.key);
                  // 不 clear aiPrompt — 切 tab 时 popover 跟随光标位置,跟 tab 解耦
                }}
                className={`relative px-4 py-2 text-[12px] font-medium transition-colors ${
                  isActive
                    ? 'text-[#5B7BFE]'
                    : 'text-gray-500 hover:text-gray-800'
                }`}
              >
                {tab.label}
                {isActive && (
                  <span className="absolute inset-x-2 -bottom-px h-0.5 bg-[#5B7BFE] rounded-t" />
                )}
              </button>
            );
          })}
        </div>
        {/* Tab Bar 右侧常驻控件:撤销/重做(不卸载 → history state 一直有效)+ AI 进度 icon */}
        <div className="flex items-center gap-2">
          <RibbonUndoRedo />
        {/* AI 进度指示 icon:
            - 只在 aiInFlight 非 null 时渲染(平时完全隐藏)
            - 不可点击(pointer-events-none),纯状态指示
            - 进度环:外圈渐变 spin(animate-spin) + 中间 Sparkles icon
            - flashing:用户飞行期再触发 → scale + 蓝色 ring 闪一下,提示"AI 还在跑别再发" */}
        {aiInFlight && (
          <div
            className={`pointer-events-none relative inline-flex h-7 w-7 items-center justify-center transition-all duration-200 ease-out ${
              flashing ? 'scale-150 ring-2 ring-[#5B7BFE]/60 rounded-full' : 'scale-100'
            }`}
            title="AI 正在生成,请稍候"
            aria-label="AI 正在生成"
          >
            {/* 进度环 — 旋转 indeterminate spinner */}
            <div className="absolute inset-0 rounded-full border-2 border-blue-100 border-t-[#5B7BFE] animate-spin" />
            <Sparkles size={12} className="text-[#5B7BFE] relative z-10" />
          </div>
        )}
        </div>
      </div>

      {/* Tool Area */}
      <div className="flex flex-wrap items-center gap-2 px-3 py-2 bg-white">
        {active === 'start' && <StartGroup />}
        {active === 'insert' && <InsertGroup editorRef={editorRef} />}
        {active === 'ai' && <AiGroup onOpenPrompt={openAiPrompt} currentAction={aiPrompt?.action ?? null} />}
        {active === 'document' && <DocumentGroup onAction={(a) => onAiAction?.(a)} />}
        {active === 'style' && (
          <StyleGroup
            fontSize={fontSize}
            setFontSize={setFontSize}
            theme={theme}
            setTheme={setTheme}
          />
        )}
      </div>

      {/* AI Prompt Panel —— WPS 风浮层,跟随 caret/选区位置,不占工具栏全宽 */}
      {aiPrompt && popoverAnchor && (
        <AiPromptPanel
          state={aiPrompt}
          setState={setAiPrompt}
          writingSkills={writingSkills}
          agentSkills={agentSkills}
          capturedSelection={capturedSelection}
          anchor={popoverAnchor}
          workingDocuments={workingDocuments}
          onRemoveWorkingDocument={onRemoveWorkingDocument}
          onCancel={() => {
            setAiPrompt(null);
            setCapturedSelection('');
            setPopoverAnchor(null);
            setAiSelectionRects([]);
          }}
          onSubmit={submitAiPrompt}
        />
      )}

      {/* 蓝色选区高亮:AI popover 打开期间标出"将被替换/参考的选区"。
          - 用 capturedRange 的 viewport 矩形(fixed 定位),不动文档内容,绝对安全。
          - pointer-events-none 不挡点击;z-40 在 popover(z-50)之下、内容之上。
          这样从右键 / AI tab / 右侧 ← 箭头任意入口触发,选中那段都保持蓝色,最后被结果替换。 */}
      {(aiPrompt || aiInFlight) && aiSelectionRects.map((r, i) => (
        <div
          key={i}
          aria-hidden="true"
          // 飞行期(aiInFlight):锁定态 —— 拦截鼠标交互(onMouseDown preventDefault)让这段不能被点选/编辑,
          //   呼吸高亮提示"AI 改写中,结果将替换此段";非飞行(仅 popover 打开):纯视觉,不挡点击。
          onMouseDown={aiInFlight ? (e) => e.preventDefault() : undefined}
          className={
            aiInFlight
              ? 'fixed z-40 cursor-not-allowed rounded-[2px] bg-[#5B7BFE]/15 ring-1 ring-[#5B7BFE]/60 animate-pulse'
              : 'pointer-events-none fixed z-40 rounded-[2px] bg-[#5B7BFE]/25 ring-1 ring-[#5B7BFE]/45'
          }
          style={{ top: r.top, left: r.left, width: r.width, height: r.height }}
        />
      ))}
      {/* 飞行期在锁定段右上角挂一个"AI 改写中"小标,让"已锁定+即将替换"更直观 */}
      {aiInFlight && aiSelectionRects.length > 0 && (
        <div
          aria-hidden="true"
          className="pointer-events-none fixed z-40 flex items-center gap-1 rounded-full bg-[#5B7BFE] px-2 py-0.5 text-[10px] font-medium text-white shadow-sm"
          style={{
            top: Math.max(2, aiSelectionRects[0].top - 18),
            left: aiSelectionRects[0].left,
          }}
        >
          <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-white" />
          AI 改写中,将替换此段
        </div>
      )}
    </>
  );
}

function AiPromptPanel({
  state,
  setState,
  writingSkills,
  agentSkills,
  capturedSelection,
  anchor,
  workingDocuments,
  onRemoveWorkingDocument,
  onCancel,
  onSubmit,
}: {
  state: AiPromptState;
  setState: React.Dispatch<React.SetStateAction<AiPromptState | null>>;
  writingSkills: RichTextDocumentEditorWritingSkill[];
  agentSkills: RichTextDocumentEditorAgentSkill[];
  capturedSelection: string;
  anchor: { top: number; left: number };
  workingDocuments: RichTextDocumentEditorWorkingDoc[];
  onRemoveWorkingDocument?: (documentId: string) => void;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  const hasSelection = capturedSelection.length > 0;
  const popoverRef = useRef<HTMLDivElement>(null);

  // Esc 关闭 + 点击外部关闭(submitting 时不关 — 让用户能看 spinner 同时干别的)
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !state.submitting) onCancel();
    };
    const handleClickOutside = (e: MouseEvent) => {
      if (state.submitting) return;
      const target = e.target as Node;
      if (popoverRef.current && !popoverRef.current.contains(target)) {
        onCancel();
      }
    };
    document.addEventListener('keydown', handleKey);
    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      document.removeEventListener('keydown', handleKey);
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [state.submitting, onCancel]);

  const creativityModes = [
    { key: 'strict' as DocumentAiCreativityMode, icon: ShieldCheck, shortLabel: '资料', hint: '资料优先 · 原文不足就明确说明,不臆测' },
    { key: 'balanced' as DocumentAiCreativityMode, icon: Scale, shortLabel: '兼顾', hint: '兼顾资料 · 事实底色 + 自由措辞(默认)' },
    { key: 'creative' as DocumentAiCreativityMode, icon: Sparkles, shortLabel: '创意', hint: '创意优先 · 保留原文事实边界,自由发挥' },
  ];
  const activeMode = creativityModes.find((m) => m.key === state.creativityMode) ?? creativityModes[1];

  return (
    <div
      ref={popoverRef}
      className="fixed z-50 w-[360px] rounded-xl border border-gray-200 bg-white shadow-[0_8px_32px_rgba(0,0,0,0.12)] overflow-hidden"
      style={{ top: anchor.top, left: anchor.left }}
    >
      {/* Header:label + scope + 关 */}
      <div className="flex items-center justify-between gap-2 border-b border-gray-100 bg-gradient-to-r from-blue-50/60 to-white px-3 py-1.5">
        <div className="flex min-w-0 items-center gap-1.5">
          <Sparkles size={12} className="shrink-0 text-[#5B7BFE]" />
          <span className="shrink-0 text-[11.5px] font-bold text-[#5B7BFE]">{state.label}</span>
          {hasSelection ? (
            <span className="shrink-0 rounded-full bg-blue-100 px-1.5 py-px text-[10px] font-medium text-[#5B7BFE]">
              选区 {capturedSelection.length} 字
            </span>
          ) : (
            <span className="shrink-0 rounded-full bg-gray-100 px-1.5 py-px text-[10px] font-medium text-gray-500">
              光标处插入
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={onCancel}
          disabled={state.submitting}
          className="rounded p-0.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700 disabled:opacity-40"
          title="关闭 (Esc)"
        >
          <X size={14} />
        </button>
      </div>

      {/* Body:chip 列表(从右侧文件 ← 加进来的)+ textarea */}
      <div className="px-3 py-2">
        {workingDocuments.length > 0 && (
          <div className="mb-1.5 flex max-h-[60px] flex-wrap gap-1 overflow-y-auto">
            {workingDocuments.map((doc) => {
              const tone = doc.status === 'failed'
                ? 'border-rose-200 bg-rose-50 text-rose-700'
                : doc.status === 'ready' || doc.status === 'partial_ready'
                  ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                  : 'border-blue-200 bg-blue-50 text-blue-700';
              return (
                <span
                  key={doc.documentId}
                  className={`group inline-flex max-w-full items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10.5px] font-medium ${tone}`}
                  title={doc.title}
                >
                  <Paperclip size={10} className="shrink-0" />
                  <span className="max-w-[160px] truncate">{doc.title}</span>
                  {onRemoveWorkingDocument && (
                    <button
                      type="button"
                      onClick={() => onRemoveWorkingDocument(doc.documentId)}
                      disabled={state.submitting}
                      aria-label={`移除引用 ${doc.title}`}
                      className="shrink-0 rounded-full p-0.5 opacity-55 transition hover:bg-white/70 hover:opacity-100 disabled:opacity-30"
                    >
                      <X size={9} />
                    </button>
                  )}
                </span>
              );
            })}
          </div>
        )}
        {agentSkills.length > 0 && (
          <div className="mb-1.5 flex flex-wrap items-center gap-1" aria-label="分析 Skill">
            <span className="text-[10px] text-gray-400">Skill</span>
            {agentSkills.map((skill) => {
              const selected = state.activeAgentSkillIds.includes(skill.skillId);
              return (
                <button
                  key={skill.skillId}
                  type="button"
                  disabled={state.submitting}
                  aria-pressed={selected}
                  onClick={() => setState((prev) => {
                    if (!prev) return prev;
                    const next = selected
                      ? prev.activeAgentSkillIds.filter((id) => id !== skill.skillId)
                      : [...prev.activeAgentSkillIds, skill.skillId].slice(0, 5);
                    return { ...prev, activeAgentSkillIds: next };
                  })}
                  className={`rounded-full border px-2 py-0.5 text-[10px] font-medium transition ${
                    selected
                      ? 'border-indigo-300 bg-indigo-50 text-indigo-700'
                      : 'border-gray-200 bg-white text-gray-500 hover:border-indigo-200 hover:text-indigo-600'
                  }`}
                  title={`使用 Skill「${skill.shortName}」`}
                >
                  {skill.shortName}
                </button>
              );
            })}
          </div>
        )}
        <textarea
          value={state.userRequest}
          onChange={(event) => setState((prev) => (prev ? { ...prev, userRequest: event.target.value } : prev))}
          placeholder={hasSelection ? '补充要求（可选）' : '补充要求（可选，留空可直接执行）'}
          rows={2}
          disabled={state.submitting}
          autoFocus
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !state.submitting) {
              e.preventDefault();
              onSubmit();
            }
          }}
          className="w-full resize-none rounded-md border border-gray-200 bg-white px-2 py-1.5 text-[12px] leading-5 text-gray-800 outline-none focus:border-[#5B7BFE] disabled:opacity-60"
        />
      </div>

      {/* Footer:模式 icons + 风格(精简) + 执行 */}
      <div className="flex items-center justify-between gap-1.5 border-t border-gray-100 bg-gray-50/60 px-2.5 py-1.5">
        {/* 左:当前模式 label + 3 个 icon */}
        <div className="flex shrink-0 items-center gap-1">
          <span className="text-[10.5px] font-medium text-[#5B7BFE] tabular-nums">
            {activeMode.shortLabel}
          </span>
          <div className="flex items-center gap-0.5">
            {creativityModes.map((m) => {
              const isActive = state.creativityMode === m.key;
              const Icon = m.icon;
              return (
                <button
                  key={m.key}
                  type="button"
                  onClick={() => setState((prev) => (prev ? { ...prev, creativityMode: m.key } : prev))}
                  disabled={state.submitting}
                  title={m.hint}
                  className={`rounded p-1 transition-colors ${
                    isActive
                      ? 'bg-[#5B7BFE]/10 text-[#5B7BFE]'
                      : 'text-gray-400 hover:bg-gray-200 hover:text-gray-700'
                  }`}
                >
                  <Icon size={13} />
                </button>
              );
            })}
          </div>
        </div>

        {writingSkills.length > 0 && (
          <select
            value={state.activeSkillId || ''}
            onChange={(event) =>
              setState((prev) => (prev ? { ...prev, activeSkillId: event.target.value || null } : prev))
            }
            disabled={state.submitting}
            title="写作风格"
            className="w-[88px] shrink-0 truncate rounded border border-gray-200 bg-white px-1.5 py-0.5 text-[10.5px] text-gray-600 outline-none focus:border-[#5B7BFE] disabled:opacity-60"
          >
            <option value="">默认风格</option>
            {writingSkills.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        )}

        <button
          type="button"
          onClick={onSubmit}
          disabled={state.submitting}
          title="直接执行；补充要求可选（⌘ Enter）"
          className="inline-flex shrink-0 items-center gap-1 rounded-md bg-[#5B7BFE] px-2.5 py-1 text-[11px] font-bold text-white shadow-sm hover:bg-[#4A6AEE] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {state.submitting ? (
            <>
              <Loader2 size={11} className="animate-spin" />
              处理中
            </>
          ) : (
            <>
              <Play size={11} />
              执行
            </>
          )}
        </button>
      </div>
    </div>
  );
}

// 工具组：每组用细分隔线分开，跟 Word 的 ribbon 风格一致
function ToolGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center px-2 py-0.5 border-r border-gray-100 last:border-r-0">
      <div className="flex items-center gap-0.5">{children}</div>
      <p className="text-[9px] text-gray-400 mt-0.5 select-none">{label}</p>
    </div>
  );
}

// 中文版「格式」下拉：合并 MDXEditor 的 BlockTypeSelect + ListsToggle，
// 选项带 T/H1/H2/H3/Hn/1./•/☐/" badge + 中文标签，点击即把当前光标段落转成该格式。
type ApplyBlockFn = (value: 'paragraph' | 'quote' | 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6' | '') => void;
type ApplyListFn = (value: 'number' | 'bullet' | 'check' | '') => void;
type BlockChoice = {
  key: string;
  label: string;
  badge: string;
  isActive: (block: string, list: string) => boolean;
  apply: (applyBlock: ApplyBlockFn, applyList: ApplyListFn) => void;
};
const BLOCK_CHOICES: BlockChoice[] = [
  { key: 'paragraph', label: '正文', badge: 'T', isActive: (b, l) => b === 'paragraph' && !l, apply: (b, l) => { l(''); b('paragraph'); } },
  { key: 'h1', label: '一级标题', badge: 'H1', isActive: (b, l) => b === 'h1' && !l, apply: (b, l) => { l(''); b('h1'); } },
  { key: 'h2', label: '二级标题', badge: 'H2', isActive: (b, l) => b === 'h2' && !l, apply: (b, l) => { l(''); b('h2'); } },
  { key: 'h3', label: '三级标题', badge: 'H3', isActive: (b, l) => b === 'h3' && !l, apply: (b, l) => { l(''); b('h3'); } },
  { key: 'hn', label: '其他标题', badge: 'Hn', isActive: (b, l) => (b === 'h4' || b === 'h5' || b === 'h6') && !l, apply: (b, l) => { l(''); b('h4'); } },
  { key: 'ol', label: '有序列表', badge: '1.', isActive: (_b, l) => l === 'number', apply: (_b, l) => { l('number'); } },
  { key: 'ul', label: '无序列表', badge: '•', isActive: (_b, l) => l === 'bullet', apply: (_b, l) => { l('bullet'); } },
  { key: 'check', label: '任务列表', badge: '☐', isActive: (_b, l) => l === 'check', apply: (_b, l) => { l('check'); } },
  { key: 'quote', label: '引用', badge: '"', isActive: (b, l) => b === 'quote' && !l, apply: (b, l) => { l(''); b('quote'); } },
];

// Tab Bar 常驻 Undo/Redo 按钮 — 不像 MDXEditor 内置的 UndoRedo(那个在"开始" tab 里,
// 切走会被卸载,导致 canUndo state 重置)。这个组件在 RibbonToolbar 顶层渲染,
// 全程不卸载,所以 Lexical 的 CAN_UNDO_COMMAND/CAN_REDO_COMMAND 监听一直在,
// 不管用户切到哪个 tab 都能撤销/重做。
function RibbonUndoRedo() {
  const [activeEditor] = useCellValues(activeEditor$);
  const [canUndo, setCanUndo] = useState(false);
  const [canRedo, setCanRedo] = useState(false);

  useEffect(() => {
    if (!activeEditor) return;
    // mergeRegister 简化版:返回 cleanup 数组 → 一起 cleanup
    const unregUndo = activeEditor.registerCommand(
      CAN_UNDO_COMMAND,
      (payload: boolean) => {
        setCanUndo(payload);
        return false;
      },
      COMMAND_PRIORITY_CRITICAL,
    );
    const unregRedo = activeEditor.registerCommand(
      CAN_REDO_COMMAND,
      (payload: boolean) => {
        setCanRedo(payload);
        return false;
      },
      COMMAND_PRIORITY_CRITICAL,
    );
    return () => {
      unregUndo();
      unregRedo();
    };
  }, [activeEditor]);

  const isMac = typeof navigator !== 'undefined' && /Mac|iPod|iPhone|iPad/.test(navigator.platform);
  const undoShortcut = isMac ? '⌘Z' : 'Ctrl+Z';
  const redoShortcut = isMac ? '⌘⇧Z' : 'Ctrl+Y';
  return (
    <div className="flex items-center gap-0.5">
      <button
        type="button"
        onClick={() => activeEditor?.dispatchCommand(UNDO_COMMAND, undefined)}
        disabled={!canUndo}
        title={`撤销 (${undoShortcut})`}
        aria-label="撤销"
        className="rounded p-1 text-gray-500 transition-colors hover:bg-gray-200 hover:text-gray-800 disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-gray-500"
      >
        <Undo2 size={14} />
      </button>
      <button
        type="button"
        onClick={() => activeEditor?.dispatchCommand(REDO_COMMAND, undefined)}
        disabled={!canRedo}
        title={`重做 (${redoShortcut})`}
        aria-label="重做"
        className="rounded p-1 text-gray-500 transition-colors hover:bg-gray-200 hover:text-gray-800 disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-gray-500"
      >
        <Redo2 size={14} />
      </button>
    </div>
  );
}

function BlockChoiceSelect() {
  const [blockType, listType] = useCellValues(currentBlockType$, currentListType$);
  const applyBlock = usePublisher(applyBlockType$) as ApplyBlockFn;
  const applyList = usePublisher(applyListType$) as ApplyListFn;
  const [open, setOpen] = useState(false);
  // 触发器 button 的屏幕坐标，用于把下拉面板 fixed-position 渲染到 portal。
  // 不用 absolute 是因为 MDXEditor toolbar 有 sticky stacking context，
  // 子层 z-index 无法穿越到编辑器其他区域。
  const [anchorRect, setAnchorRect] = useState<{ left: number; top: number; width: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  // 打开时记录触发器位置（每次打开都重新算，防 sticky 滚动位置变了）
  useEffect(() => {
    if (open && triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect();
      setAnchorRect({ left: rect.left, top: rect.bottom + 4, width: rect.width });
    }
  }, [open]);

  // 点击下拉外面关闭 — 触发器和面板都不算"外面"
  useEffect(() => {
    if (!open) return;
    const handler = (event: MouseEvent) => {
      const target = event.target as Node;
      if (triggerRef.current?.contains(target)) return;
      if (panelRef.current?.contains(target)) return;
      setOpen(false);
    };
    window.addEventListener('mousedown', handler);
    return () => window.removeEventListener('mousedown', handler);
  }, [open]);

  const currentBlock = (blockType as string) || '';
  const currentList = (listType as string) || '';
  const active = BLOCK_CHOICES.find((c) => c.isActive(currentBlock, currentList)) || BLOCK_CHOICES[0];

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="inline-flex items-center gap-1.5 rounded-md border border-gray-200 bg-white px-2 py-1 text-[11px] font-semibold text-gray-700 hover:border-[#5B7BFE] hover:text-[#5B7BFE]"
        onClick={() => setOpen((v) => !v)}
        title="切换段落格式"
      >
        <span className="inline-flex h-4 min-w-[18px] items-center justify-center rounded bg-gray-100 px-1 text-[10px] font-black text-gray-700">{active.badge}</span>
        <span>{active.label}</span>
        <ChevronDown size={11} />
      </button>
      {open && anchorRect && createPortal(
        <div
          ref={panelRef}
          className="rounded-lg border border-gray-200 bg-white shadow-lg py-1"
          style={{
            position: 'fixed',
            left: anchorRect.left,
            top: anchorRect.top,
            minWidth: Math.max(160, anchorRect.width),
            zIndex: 10000,
          }}
        >
          {BLOCK_CHOICES.map((choice) => {
            const isActive = choice.isActive(currentBlock, currentList);
            return (
              <button
                key={choice.key}
                type="button"
                className={`flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-[12px] hover:bg-blue-50 ${
                  isActive ? 'bg-blue-50 text-[#5B7BFE] font-bold' : 'text-gray-700'
                }`}
                onClick={() => {
                  choice.apply(applyBlock, applyList);
                  setOpen(false);
                }}
              >
                <span className="inline-flex h-5 min-w-[24px] items-center justify-center rounded bg-gray-100 text-[10px] font-black text-gray-700">{choice.badge}</span>
                <span>{choice.label}</span>
              </button>
            );
          })}
        </div>,
        document.body,
      )}
    </>
  );
}

// ─── 开始 tab ───
function StartGroup() {
  return (
    <>
      <ToolGroup label="撤销">
        <UndoRedo />
      </ToolGroup>
      <ToolGroup label="字体">
        <BoldItalicUnderlineToggles />
        <StrikeThroughSupSubToggles />
      </ToolGroup>
      <ToolGroup label="格式">
        <BlockChoiceSelect />
      </ToolGroup>
    </>
  );
}

// ─── 插入 tab ───
// 用自定义按钮直接写 markdown：
//   - MDXEditor 内置 <InsertTable/> <InsertImage/> <CreateLink/> 的 dialog 在 inline
//     editor 的 absolute overlay stacking context 内被遮,点击无反应;
//   - Electron renderer 禁用 window.prompt(contextIsolation+nodeIntegration=false 默认行为),
//     返回 null 静默失败,所以不能用 window.prompt;
//   - 统一改用全局 appPrompt() 弹窗(挂在 main.tsx <AppPromptHost />),
//     editor.insertMarkdown() 写入 markdown,100% 可工作。
function InsertGroup({ editorRef }: { editorRef: React.RefObject<MDXEditorMethods | null> }) {
  // MDXEditor 的 insertMarkdown 需要 editor 已 focus 才能命中正确光标位置。
  // 工具栏按钮点击会抢走 focus → 编辑器 selection 失效 → insertMarkdown 静默失败。
  // 用 focus(callback) 形式确保 selection 同步到位后再 insert。
  const insertAt = (text: string) => {
    const editor = editorRef.current;
    if (!editor) return;
    editor.focus(
      () => {
        try {
          editor.insertMarkdown(text);
        } catch (err) {
          // eslint-disable-next-line no-console
          console.warn('[editor-insert] insertMarkdown failed', err);
        }
      },
      { defaultSelection: 'rootEnd' },
    );
  };
  const handleInsertTable = () => {
    insertAt('\n\n| 列 1 | 列 2 | 列 3 |\n| --- | --- | --- |\n|  |  |  |\n|  |  |  |\n\n');
  };
  const handleInsertImage = async () => {
    const result = await appPrompt({
      title: '插入图片',
      fields: [
        {
          name: 'url',
          label: '图片 URL（http/https 或 data:）',
          placeholder: 'https://... 或 data:image/png;base64,...',
          required: true,
        },
        { name: 'alt', label: '图片说明（可选）' },
      ],
      confirmLabel: '插入',
    });
    if (!result) return;
    insertAt(`![${(result.alt || '').trim()}](${result.url.trim()})`);
  };
  const handleInsertLink = async () => {
    const result = await appPrompt({
      title: '插入链接',
      fields: [
        {
          name: 'url',
          label: '链接 URL（含 https://）',
          placeholder: 'https://example.com',
          required: true,
        },
        { name: 'text', label: '显示文字（可选，留空用 URL）' },
      ],
      confirmLabel: '插入',
    });
    if (!result) return;
    const url = result.url.trim();
    const text = (result.text || '').trim() || url;
    insertAt(`[${text}](${url})`);
  };
  return (
    <>
      <ToolGroup label="表格">
        <RibbonInsertButton icon={<TableIcon size={16} />} title="插入 3 列表格" onClick={handleInsertTable} />
      </ToolGroup>
      <ToolGroup label="图片">
        <RibbonInsertButton icon={<ImageIcon size={16} />} title="插入图片" onClick={handleInsertImage} />
      </ToolGroup>
      <ToolGroup label="链接">
        <RibbonInsertButton icon={<LinkIcon size={16} />} title="插入链接" onClick={handleInsertLink} />
      </ToolGroup>
    </>
  );
}

function RibbonInsertButton({
  icon,
  title,
  onClick,
}: {
  icon: React.ReactNode;
  title: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      className="inline-flex h-7 w-7 items-center justify-center rounded text-gray-600 hover:bg-blue-50 hover:text-[#5B7BFE] transition-colors"
    >
      {icon}
    </button>
  );
}

// ─── AI 助手 tab —— stub ───
function AiGroup({
  onOpenPrompt,
  currentAction,
}: {
  onOpenPrompt: (action: DocumentAiAction) => void;
  currentAction: DocumentAiAction | null;
}) {
  const buttons: { key: DocumentAiAction; icon: React.ReactNode; label: string }[] = [
    { key: 'expand', icon: <Wand2 size={16} />, label: '扩写' },
    { key: 'rewrite_pro', icon: <PenToolIcon size={16} />, label: '改写 · 专业' },
    { key: 'rewrite_short', icon: <PenToolIcon size={16} />, label: '改写 · 简洁' },
    { key: 'summarize', icon: <FileText size={16} />, label: '总结' },
    { key: 'extract', icon: <Sparkles size={16} />, label: '提取要点' },
    { key: 'translate', icon: <Languages size={16} />, label: '翻译' },
    { key: 'style_distilled', icon: <Sparkles size={16} />, label: '切换风格' },
  ];
  return (
    <>
      {buttons.map((b) => {
        const isActive = currentAction === b.key;
        return (
          <button
            key={b.key}
            type="button"
            onClick={() => onOpenPrompt(b.key)}
            className={`flex flex-col items-center justify-center gap-1 rounded-md px-3 py-1.5 transition-colors ${
              isActive
                ? 'bg-blue-50 text-[#5B7BFE]'
                : 'text-gray-600 hover:bg-blue-50 hover:text-[#5B7BFE]'
            }`}
            title={`${b.label}（点击后展开输入框写具体要求）`}
          >
            {b.icon}
            <span className="text-[10px] leading-tight whitespace-nowrap">{b.label}</span>
          </button>
        );
      })}
    </>
  );
}

// ─── 文档 tab：报告版本历史、导出和全屏由外层处理；普通文档仍保留提示 ───
function DocumentGroup({ onAction }: { onAction: (a: string) => void }) {
  return (
    <>
      <button
        type="button"
        onClick={() => onAction('history')}
        className="flex flex-col items-center justify-center gap-1 rounded-md px-3 py-1.5 text-gray-600 hover:bg-blue-50 hover:text-[#5B7BFE] transition-colors"
        title="查看版本记录"
      >
        <History size={16} />
        <span className="text-[10px] leading-tight">版本历史</span>
      </button>
      <button
        type="button"
        onClick={() => onAction('__export_docx')}
        className="flex flex-col items-center justify-center gap-1 rounded-md px-3 py-1.5 text-gray-600 hover:bg-blue-50 hover:text-[#5B7BFE] transition-colors"
        title="把当前内容生成 docx 并打开"
      >
        <Download size={16} />
        <span className="text-[10px] leading-tight">导出 docx</span>
      </button>
      <button
        type="button"
        onClick={() => onAction('share')}
        className="flex flex-col items-center justify-center gap-1 rounded-md px-3 py-1.5 text-gray-600 hover:bg-blue-50 hover:text-[#5B7BFE] transition-colors"
        title="相关同事可见（下一步打通）"
      >
        <UsersIcon size={16} />
        <span className="text-[10px] leading-tight">相关同事可见</span>
      </button>
      <button
        type="button"
        onClick={() => onAction('__toggle_fullscreen')}
        className="flex flex-col items-center justify-center gap-1 rounded-md px-3 py-1.5 text-gray-600 hover:bg-blue-50 hover:text-[#5B7BFE] transition-colors"
        title="切换全屏（隐藏左右栏，更专注写作）"
      >
        <Maximize2 size={16} />
        <span className="text-[10px] leading-tight">全屏</span>
      </button>
    </>
  );
}

// ─── 样式 tab：字号 + 主题 cycle 按钮，都 wire 到 RichTextDocumentEditor 内部 state ───
function StyleGroup({
  fontSize,
  setFontSize,
  theme,
  setTheme,
}: {
  fontSize: EditorFontSize;
  setFontSize: React.Dispatch<React.SetStateAction<EditorFontSize>>;
  theme: EditorTheme;
  setTheme: React.Dispatch<React.SetStateAction<EditorTheme>>;
}) {
  const cycleFontSize = () => {
    const order: EditorFontSize[] = ['sm', 'md', 'lg', 'xl'];
    setFontSize(order[(order.indexOf(fontSize) + 1) % order.length]);
  };
  const cycleTheme = () => {
    const order: EditorTheme[] = ['light', 'sepia', 'dark'];
    setTheme(order[(order.indexOf(theme) + 1) % order.length]);
  };
  return (
    <>
      <button
        type="button"
        onClick={cycleFontSize}
        className="flex flex-col items-center justify-center gap-1 rounded-md px-3 py-1.5 text-gray-600 hover:bg-blue-50 hover:text-[#5B7BFE] transition-colors"
        title="切换字号（小 → 中 → 大 → 特大 → 小）"
      >
        <Type size={16} />
        <span className="text-[10px] leading-tight">字号 · {FONT_SIZE_LABEL[fontSize]}</span>
      </button>
      <button
        type="button"
        onClick={cycleTheme}
        className="flex flex-col items-center justify-center gap-1 rounded-md px-3 py-1.5 text-gray-600 hover:bg-blue-50 hover:text-[#5B7BFE] transition-colors"
        title="切换主题（默认 → 米黄护眼 → 暗夜 → 默认）"
      >
        <Palette size={16} />
        <span className="text-[10px] leading-tight">主题 · {THEME_LABEL[theme]}</span>
      </button>
    </>
  );
}

/** 给用户预览效果用的样例 markdown */
