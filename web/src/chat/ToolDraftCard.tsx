// A command the agent wrote in this chat (tools.draft), waiting for the user: its code
// and the exact packages to read, then approve or throw away (DraftDecision). Only a
// person approves.

import { ChevronDown, ChevronRight, Wrench } from "lucide-react";
import { useState } from "react";

import { useExternalTools, useExternalToolVersion } from "@/api/queries";
import type { ExternalTool } from "@/api/types";
import { DraftDecision } from "@/commands/DraftDecision";

export function ToolDraftCards({ sessionId }: { sessionId: string }) {
  const tools = useExternalTools();

  const drafts = (tools.data ?? []).filter((tool) => tool.versions.some((version) => version.status === "draft" && version.session_id === sessionId));

  return (
    <>
      {drafts.map((tool) => (
        <ToolDraftCard key={tool.name} tool={tool} version={tool.draft_version as number} />
      ))}
    </>
  );
}

function ToolDraftCard({ tool, version }: { tool: ExternalTool; version: number }) {
  const draft = useExternalToolVersion(tool.name, version);
  const [open, setOpen] = useState(false);

  const manifest = draft.data?.manifest;

  return (
    <div className="flex flex-col gap-3 rounded-lg border bg-card p-4">
      <div className="flex items-start gap-2">
        <Wrench className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0">
          <div className="font-medium">{manifest?.title ?? tool.name}</div>
          <div className="text-sm text-muted-foreground">
            {tool.name} v{version} · черновик команды ждёт вашего решения
            {tool.current_version ? ` · сейчас работает v${tool.current_version}` : ""}
          </div>
        </div>
      </div>

      {manifest && <p className="text-sm">{manifest.description}</p>}

      <div className="text-sm">
        <span className="text-muted-foreground">Пакеты: </span>
        {draft.data ? (draft.data.packages.length ? draft.data.packages.join(", ") : "нет") : "…"}
      </div>

      <button className="flex items-center gap-1 self-start text-sm text-muted-foreground hover:text-foreground" onClick={() => setOpen(!open)}>
        {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />} Код
      </button>
      {open &&
        draft.data &&
        Object.entries(draft.data.files).map(([path, text]) => (
          <div key={path} className="flex flex-col gap-1">
            <div className="text-xs text-muted-foreground">{path}</div>
            <pre className="max-h-80 overflow-auto rounded-md bg-muted p-2 text-xs">{text}</pre>
          </div>
        ))}

      <p className="text-xs text-muted-foreground">
        Команда работает отдельным процессом без ваших паролей и ключей, но может читать то же, что и вы. Одобряйте, если код делает только то, что
        нужно.
      </p>

      {draft.data && <DraftDecision version={draft.data} />}
    </div>
  );
}
