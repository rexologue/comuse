// A command's draft waiting for a person: approve exactly the code on screen (its hash)
// or throw it away. The chat's card and the Commands page both decide through this.

import { useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { api } from "@/api/client";
import { keys } from "@/api/queries";
import type { ExternalToolVersion } from "@/api/types";
import { Button } from "@/components/ui/button";
import { useAction } from "@/settings/common";

export function DraftDecision({ version, children }: { version: ExternalToolVersion; children?: ReactNode }) {
  const queryClient = useQueryClient();
  const { busy, error, run } = useAction();
  const [armed, setArmed] = useState(false);

  // A refused approval too: the draft may have changed, and it is shown anew. An open
  // task panel offers the move to a version approved here.
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: keys.externalTools });
    await queryClient.invalidateQueries({ queryKey: keys.tools });
    await queryClient.invalidateQueries({ queryKey: ["task"] });
  };

  const approve = () =>
    run("approve", () => api(`/external-tools/${version.name}/versions/${version.version}/approve`, { method: "POST", json: { hash: version.hash } }).finally(refresh));
  const discard = () => run("discard", () => api(`/external-tools/${version.name}/draft`, { method: "DELETE" }).finally(refresh));

  return (
    <>
      <div className="flex flex-wrap gap-2">
        {version.status === "draft" && (
          <>
            <Button size="sm" onClick={approve} disabled={busy !== null}>
              {busy === "approve" ? "Ставлю пакеты…" : "Одобрить"}
            </Button>
            <Button size="sm" variant={armed ? "destructive" : "ghost"} onBlur={() => setArmed(false)} onClick={armed ? discard : () => setArmed(true)} disabled={busy !== null}>
              {armed ? "Точно выбросить?" : "Выбросить черновик"}
            </Button>
          </>
        )}
        {children}
      </div>
      {error && <p className="text-sm whitespace-pre-line text-destructive">{error}</p>}
    </>
  );
}
