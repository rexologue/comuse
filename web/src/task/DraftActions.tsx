// What a draft version allows: a dry run (its log opens on the right), approving and
// rejecting. Approving is the user's alone: no agent tool does it.

import { useQueryClient } from "@tanstack/react-query";
import { Check, FlaskConical, X } from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router";

import { api, ApiError } from "@/api/client";
import { keys } from "@/api/queries";
import type { Run } from "@/api/types";
import { Button } from "@/components/ui/button";

export function DraftActions({ taskId, versionId }: { taskId: string; versionId: string }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [, setSearch] = useSearchParams();
  const queryClient = useQueryClient();

  const act = async (name: string, call: () => Promise<unknown>) => {
    setBusy(name);
    setError(null);

    try {
      await call();
      queryClient.invalidateQueries({ queryKey: ["task"] });
      queryClient.invalidateQueries({ queryKey: keys.tasks });
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const dryRun = () =>
    act("dry", async () => {
      const run = await api<Run>("/runs", { method: "POST", json: { task_version_id: versionId, dry_run: true } });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      setSearch((search) => {
        search.set("run", run.id);
        return search;
      });
    });

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={dryRun} disabled={busy !== null}>
          <FlaskConical /> Пробный запуск
        </Button>
        <Button size="sm" onClick={() => act("approve", () => api(`/tasks/${taskId}/versions/${versionId}/approve`, { method: "POST" }))} disabled={busy !== null}>
          <Check /> Одобрить
        </Button>
        <Button size="sm" variant="ghost" onClick={() => act("reject", () => api(`/tasks/${taskId}/versions/${versionId}/reject`, { method: "POST" }))} disabled={busy !== null}>
          <X /> Отклонить
        </Button>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  );
}
