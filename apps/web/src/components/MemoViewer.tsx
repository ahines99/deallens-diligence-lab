"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { formatDate } from "@/lib/formatting";
import type { Memo } from "@/lib/types";

export function MemoViewer({ memo }: { memo: Memo }) {
  const updated = memo.updated_at && memo.updated_at !== memo.created_at;
  return (
    <article className="rounded-lg border border-slate-200 bg-white shadow-sm">
      <header className="border-b border-slate-100 px-6 py-4">
        <h2 className="text-lg font-semibold text-slate-900">{memo.title}</h2>
        <p className="mt-1 text-xs text-slate-500">
          Generated {formatDate(memo.created_at)}
          {updated && <span> · Updated {formatDate(memo.updated_at)}</span>}
        </p>
      </header>
      <div className="memo-prose px-6 py-5">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{memo.markdown_content}</ReactMarkdown>
      </div>
    </article>
  );
}

export default MemoViewer;
