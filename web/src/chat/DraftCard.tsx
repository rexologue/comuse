// A draft built or changed in this chat, waiting for the user: its steps and schedule,
// a dry run, approve or reject, and a change in words (a new draft with what changed).

import { useQueryClient } from "@tanstack/react-query";
import { FileClock, Pencil } from "lucide-react";
import { useState } from "react";

import { api, ApiError } from "@/api/client";
import { keys, useApprovals, useVersion } from "@/api/queries";
import type { Approval, PatchResult } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Cards } from "@/task/Cards";
import { DraftActions } from "@/task/DraftActions";
import { DiffLines } from "@/task/VersionView";

export function DraftCards({ sessionId }: { sessionId: string }) {
  const approvals = useApprovals(sessionId);

  return (
    <>
      {approvals.data?.map((approval) => (
        // Keyed by task: a change in words replaces the approval, the card (and what it
        // says changed) stays.
        <DraftCard key={approval.task_id} approval={approval} sessionId={sessionId} />
      ))}
    </>
  );
}

function DraftCard({ approval, sessionId }: { approval: Approval; sessionId: string }) {
  const version = useVersion(approval.task_id, approval.task_version_id);
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [changed, setChanged] = useState<string[] | null>(null);

  const patch = async () => {
    setBusy(true);
    setError(null);

    try {
      const result = await api<PatchResult>("/compiler/patch", { method: "POST", json: { task_id: approval.task_id, instruction, session_id: sessionId } });
      setChanged(result.diff);
      setEditing(false);
      setInstruction("");
      queryClient.invalidateQueries({ queryKey: ["approvals"] });
      queryClient.invalidateQueries({ queryKey: ["task"] });
      queryClient.invalidateQueries({ queryKey: keys.tasks });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-3 rounded-lg border bg-card p-4">
      <div className="flex items-start gap-2">
        <FileClock className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0">
          <div className="font-medium">{approval.payload.title ?? version.data?.summary ?? "Черновик задачи"}</div>
          <div className="text-sm text-muted-foreground">Черновик ждёт вашего решения · {version.data?.schedule ?? "…"}</div>
        </div>
      </div>

      {changed && changed.length > 0 && <DiffLines lines={changed} />}
      <Cards cards={version.data?.cards ?? approval.payload.cards ?? []} />

      <DraftActions taskId={approval.task_id} versionId={approval.task_version_id} />

      {editing ? (
        <div className="flex flex-col gap-2">
          <Textarea value={instruction} onChange={(e) => setInstruction(e.target.value)} placeholder="Что поменять? Например: «по пятницам в 9:00» или «добавь в копию бухгалтерию»" />
          <div className="flex gap-2">
            <Button size="sm" onClick={patch} disabled={busy || !instruction.trim()}>
              {busy ? "Меняю…" : "Изменить"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)} disabled={busy}>
              Отмена
            </Button>
          </div>
        </div>
      ) : (
        <Button size="sm" variant="ghost" className="self-start" onClick={() => setEditing(true)}>
          <Pencil /> Изменить словами
        </Button>
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  );
}
