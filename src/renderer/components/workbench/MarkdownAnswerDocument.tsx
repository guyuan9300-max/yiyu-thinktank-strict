import React from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

const components: Components = {
  h1: ({ children }) => (
    <div className="space-y-2">
      <h1 className="text-[22px] font-semibold leading-[1.3] tracking-[-0.02em] text-[#1f275b] xl:text-[24px]">
        {children}
      </h1>
      <div className="h-px w-16 bg-[#d8defb]" />
    </div>
  ),
  h2: ({ children }) => (
    <h2 className="pt-2 text-[19px] font-semibold leading-[1.5] tracking-[-0.01em] text-[#25306a] xl:text-[20px]">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="pt-1 text-[15px] font-semibold leading-7 text-[#2a356f] xl:text-[15.5px]">
      {children}
    </h3>
  ),
  h4: ({ children }) => <h4 className="pt-1 text-[14.5px] font-semibold leading-7 text-[#2a356f]">{children}</h4>,
  h5: ({ children }) => <h5 className="pt-1 text-[14px] font-semibold leading-7 text-[#2a356f]">{children}</h5>,
  h6: ({ children }) => <h6 className="pt-1 text-[13.5px] font-semibold leading-7 text-[#2a356f]">{children}</h6>,
  p: ({ children }) => (
    <p className="whitespace-pre-wrap text-[14.5px] leading-7 text-[#30376b] xl:text-[15px]">
      {children}
    </p>
  ),
  ol: ({ children, start }) => (
    <ol start={start} className="list-decimal space-y-2 pl-6 text-[14.5px] leading-7 text-[#2f376d] marker:text-[#4b63df] xl:text-[15px]">
      {children}
    </ol>
  ),
  ul: ({ children }) => (
    <ul className="list-disc space-y-2 pl-6 text-[14.5px] leading-7 text-[#2f376d] marker:text-[#4b63df] xl:text-[15px]">
      {children}
    </ul>
  ),
  li: ({ children }) => <li className="pl-1 [&>ol]:mt-2 [&>ul]:mt-2">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-slate-950">{children}</strong>,
  em: ({ children }) => <em className="italic text-[#30376b]">{children}</em>,
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="break-words text-[#4564dd] underline decoration-[#9babea] underline-offset-2 hover:text-[#304bc0]"
    >
      {children}
    </a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="border-l-4 border-[#cbd4fa] bg-[#f7f8fe] px-4 py-2 text-[14.5px] leading-7 text-[#4b527c]">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="border-0 border-t border-[#e5e8f6]" />,
  pre: ({ children }) => (
    <pre className="overflow-x-auto rounded-xl bg-[#20253f] p-4 text-[13px] leading-6 text-[#eef1ff]">
      {children}
    </pre>
  ),
  code: ({ children, className }) => {
    if (className) {
      return <code className={className}>{children}</code>;
    }
    return (
      <code className="rounded bg-[#eef1fb] px-1.5 py-0.5 font-mono text-[0.92em] text-[#374482]">
        {children}
      </code>
    );
  },
  table: ({ children }) => (
    <div className="overflow-x-auto rounded-xl border border-[#d8defb] bg-white">
      <table className="w-full border-collapse text-[13px] xl:text-[14px]">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-[#f3f5fc]">{children}</thead>,
  tbody: ({ children }) => <tbody>{children}</tbody>,
  tr: ({ children }) => <tr className="even:bg-[#fafbff]">{children}</tr>,
  th: ({ children }) => (
    <th className="whitespace-nowrap border-b border-[#d8defb] px-3 py-2 text-left font-semibold text-[#1f275b]">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border-b border-[#eef0f9] px-3 py-2 align-top leading-6 text-[#30376b]">
      {children}
    </td>
  ),
  input: ({ checked, type }) => (
    type === 'checkbox'
      ? <input type="checkbox" checked={Boolean(checked)} readOnly className="mr-2 align-middle accent-[#5B7BFE]" />
      : null
  ),
  img: ({ alt }) => <span className="text-[13px] text-slate-500">{alt ? `[图片：${alt}]` : '[图片]'}</span>,
};

export function MarkdownAnswerDocument({ text }: { text: string }) {
  const source = text.replace(/\r\n?/g, '\n').trim();
  if (!source) return null;
  return (
    <div className="space-y-4 text-[#2c315d] [&>ol>li>p]:inline [&>ul>li>p]:inline">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components} skipHtml>
        {source}
      </ReactMarkdown>
    </div>
  );
}
