// A plugin's draft waiting for a person: read its tools (a server of its own shows them
// once it runs: "Проверить запуск"), then approve exactly the code on screen (its hash)
// or throw it away. The chat's card and the Plugins page both decide through this.

import { useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { api } from "@/api/client";
import { keys } from "@/api/queries";
import type { PluginCode } from "@/api/types";
import { Button } from "@/components/ui/button";
import { useAction } from "@/settings/common";

export function DraftDecision({ code, children }: { code: PluginCode; children?: ReactNode }) {
  const queryClient = useQueryClient();
  const { busy, error, run } = useAction();
  const [armed, setArmed] = useState(false);

  // A refused approval too: the draft may have changed, and it is shown anew. The tasks
  // with the plugin's tools now wait for their user: their panels say what changed.
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: keys.plugins });
    await queryClient.invalidateQueries({ queryKey: keys.tools });
    await queryClient.invalidateQueries({ queryKey: ["task"] });
  };

  const check = () => run("check", () => api(`/plugins/${code.name}/draft/check`, { method: "POST" }).finally(refresh));
  const approve = () => run("approve", () => api(`/plugins/${code.name}/draft/approve`, { method: "POST", json: { hash: code.hash } }).finally(refresh));
  const discard = () => run("discard", () => api(`/plugins/${code.name}/draft`, { method: "DELETE" }).finally(refresh));

  const unseen = code.tools === null;

  return (
    <>
      <div className="flex flex-wrap gap-2">
        {code.status === "draft" && (
          <>
            {unseen && (
              <Button size="sm" variant="outline" onClick={check} disabled={busy !== null}>
                {busy === "check" ? "Запускаю…" : "Проверить запуск"}
              </Button>
            )}
            <Button size="sm" onClick={approve} disabled={busy !== null || unseen || code.packages === null}>
              {busy === "approve" ? "Ставлю пакеты…" : "Одобрить"}
            </Button>
            <Button size="sm" variant={armed ? "destructive" : "ghost"} onBlur={() => setArmed(false)} onClick={armed ? discard : () => setArmed(true)} disabled={busy !== null}>
              {armed ? "Точно выбросить?" : "Выбросить черновик"}
            </Button>
          </>
        )}
        {children}
      </div>
      {code.status === "draft" && unseen && (
        <p className="text-xs text-muted-foreground">Сервер плагина ещё не запускали: одобряют, увидев, какие инструменты он отдаёт.</p>
      )}
      {error && <p className="text-sm whitespace-pre-line text-destructive">{error}</p>}
    </>
  );
}

export function ToolList({ code }: { code: PluginCode }) {
  if (code.tools === null) return null;

  return (
    <div className="flex flex-col gap-1 text-sm">
      <span className="text-xs text-muted-foreground">Инструменты</span>
      {code.tools.map((tool) => (
        <div key={tool.name} className="flex flex-col rounded-md border px-2 py-1">
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs">{tool.name}</span>
            <span>{tool.title}</span>
            <span className="ml-auto text-xs text-muted-foreground">
              {tool.confirm === "always" ? "спрашивает каждый раз" : tool.confirm ? "спрашивает" : "не спрашивает"}
            </span>
          </div>
          <span className="text-xs text-muted-foreground">{tool.description}</span>
        </div>
      ))}
    </div>
  );
}
