import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";

// The agent answers in Markdown: tables, lists, code. Raw HTML in it is not rendered.
export function Markdown({ text, className }: { text: string; className?: string }) {
  return (
    <div className={cn("prose prose-sm max-w-none break-words dark:prose-invert prose-pre:overflow-x-auto prose-table:block prose-table:overflow-x-auto", className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}
