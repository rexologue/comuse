// The commands an assistant message called, as compact rows; a row opens to its
// arguments and result.

import { Check, CircleHelp, X } from "lucide-react";

import { useTools } from "@/api/queries";
import type { TraceItem } from "@/api/types";
import { toolTitle } from "@/chat/tools";
import { cn } from "@/lib/utils";

const STATUS = {
  ok: { icon: Check, className: "text-success", label: "выполнено" },
  confirmed: { icon: Check, className: "text-success", label: "выполнено после согласия" },
  error: { icon: X, className: "text-destructive", label: "ошибка" },
  needs_approval: { icon: CircleHelp, className: "text-amber-500", label: "ждёт вашего ответа" },
} as const;

export function ToolCalls({ items }: { items: TraceItem[] }) {
  const tools = useTools();

  if (items.length === 0) return null;

  return (
    <div className="flex flex-col gap-1">
      {items.map((item) => {
        const status = STATUS[item.status] ?? STATUS.ok;
        const Icon = status.icon;

        return (
          <details key={item.id} className="group rounded-md border bg-muted/40 text-xs">
            <summary className="flex cursor-pointer list-none items-center gap-2 px-2 py-1">
              <Icon className={cn("size-3.5 shrink-0", status.className)} aria-label={status.label} />
              <span className="font-medium">{toolTitle(item.tool, tools.data)}</span>
              <span className="truncate text-muted-foreground">{item.tool.replace(/^meta:/, "")}</span>
            </summary>
            <div className="space-y-1 border-t px-2 py-1.5">
              <Json label="Аргументы" value={item.args} />
              {item.result !== null && item.result !== undefined && <Json label="Результат" value={item.result} />}
            </div>
          </details>
        );
      })}
    </div>
  );
}

function Json({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <div className="text-muted-foreground">{label}</div>
      <pre className="max-h-64 overflow-auto rounded bg-background p-1.5 whitespace-pre-wrap">{JSON.stringify(value, null, 2)}</pre>
    </div>
  );
}
