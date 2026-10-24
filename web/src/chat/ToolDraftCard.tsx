// A command the agent wrote in this chat (tools.draft), waiting for the user: its code
// and the exact packages to read, then approve or throw away. Only a person approves.

import { useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Wrench } from "lucide-react";
import { useState } from "react";

import { api, ApiError } from "@/api/client";
import { keys, useExternalTools, useExternalToolVersion } from "@/api/queries";
import type { ExternalTool } from "@/api/types";
import { Button } from "@/components/ui/button";

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
  const queryClient = useQueryClient();
  const draft = useExternalToolVersion(tool.name, version);
  const [open, setOpen] = useState(false);
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const act = (name: string, call: () => Promise<unknown>) => async () => {
    setBusy(name);
    setError(null);
    try {
      await call();
      await queryClient.invalidateQueries({ queryKey: keys.externalTools });
      await queryClient.invalidateQueries({ queryKey: keys.tools });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const approve = act("approve", () => api(`/external-tools/${tool.name}/versions/${version}/approve`, { method: "POST" }));
  const discard = act("discard", () => api(`/external-tools/${tool.name}/draft`, { method: "DELETE" }));

  const manifest = draft.data?.manifest;

  return (
    <div className="flex flex-col gap-3 rounded-lg border bg-card p-4">
      <div className="flex items-start gap-2">
        <Wrench className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0">
          <div className="font-medium">{manifest?.title ?? tool.tool}</div>
          <div className="text-sm text-muted-foreground">
            {tool.tool} v{version} · черновик команды ждёт вашего решения
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

      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={approve} disabled={busy !== null}>
          {busy === "approve" ? "Ставлю пакеты…" : "Одобрить"}
        </Button>
        <Button
          size="sm"
          variant={armed ? "destructive" : "ghost"}
          onBlur={() => setArmed(false)}
          onClick={armed ? discard : () => setArmed(true)}
          disabled={busy !== null}
        >
          {armed ? "Точно выбросить?" : "Выбросить"}
        </Button>
      </div>
      {error && <p className="text-sm whitespace-pre-line text-destructive">{error}</p>}
    </div>
  );
}
