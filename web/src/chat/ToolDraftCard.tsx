// A plugin the agent wrote in this chat (tools.draft), waiting for the user: its tools,
// code and exact packages to read, then approve or throw away (DraftDecision). Only a
// person approves.

import { ChevronDown, ChevronRight, Wrench } from "lucide-react";
import { useState } from "react";

import { usePluginCode, usePlugins } from "@/api/queries";
import type { Plugin } from "@/api/types";
import { DraftDecision, ToolList } from "@/commands/DraftDecision";

export function ToolDraftCards({ sessionId }: { sessionId: string }) {
  const plugins = usePlugins();

  const drafts = (plugins.data ?? []).filter((plugin) => plugin.draft?.session_id === sessionId);

  return (
    <>
      {drafts.map((plugin) => (
        <ToolDraftCard key={plugin.name} plugin={plugin} />
      ))}
    </>
  );
}

function ToolDraftCard({ plugin }: { plugin: Plugin }) {
  const draft = usePluginCode(plugin.name, "draft");
  const [open, setOpen] = useState(false);

  const manifest = draft.data?.manifest;

  return (
    <div className="flex flex-col gap-3 rounded-lg border bg-card p-4">
      <div className="flex items-start gap-2">
        <Wrench className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0">
          <div className="font-medium">{manifest?.title ?? plugin.name}</div>
          <div className="text-sm text-muted-foreground">
            плагин {plugin.name} · черновик ждёт вашего решения
            {plugin.current ? " · он заменит одобренный код, и задачи с его командами будут ждать вашего решения" : ""}
          </div>
        </div>
      </div>

      {draft.data && <ToolList code={draft.data} />}

      <div className="text-sm">
        <span className="text-muted-foreground">Пакеты: </span>
        {draft.data ? (draft.data.packages === null ? "подбираются…" : draft.data.packages.length ? draft.data.packages.join(", ") : "нет") : "…"}
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
        Плагин работает отдельным процессом без ваших паролей и ключей, но может читать то же, что и вы. Одобряйте, если код делает только то, что
        нужно.
      </p>

      {draft.data && <DraftDecision code={draft.data} />}
    </div>
  );
}
