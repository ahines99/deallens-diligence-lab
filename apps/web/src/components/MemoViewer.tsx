"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Card } from "@/components/ui/Card";
import { formatDate } from "@/lib/formatting";
import type { Memo } from "@/lib/types";

export function MemoViewer({ memo }: { memo: Memo }) {
  const updated = memo.updated_at && memo.updated_at !== memo.created_at;
  return (
    <Card bodyClassName="">
      <header className="border-b border-line px-6 py-4">
        <h2 className="font-serif text-xl font-semibold leading-tight text-ink">{memo.title}</h2>
        <p className="mt-1.5 text-xs text-muted">
          Generated {formatDate(memo.created_at)}
          {updated && <span> · Updated {formatDate(memo.updated_at)}</span>}
        </p>
      </header>
      <div className="memo-prose px-6 py-5">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{memo.markdown_content}</ReactMarkdown>
      </div>
    </Card>
  );
}

export default MemoViewer;
