// One run's log, as it goes: the start, every step (and every attempt) with what it got
// and gave back, the messages to the user, the end. A run still going refreshes itself.

import { useQueryClient } from "@tanstack/react-query";
import { Check, CircleSlash, Square, X } from "lucide-react";
import { useState } from "react";

import { api, ApiError } from "@/api/client";
import { useRun, useRunLog, useTools } from "@/api/queries";
import type { LogEntry } from "@/api/types";
import { toolTitle } from "@/chat/tools";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { dateTime, duration } from "@/lib/format";
import { cn } from "@/lib/utils";
import { RUN_STATUS, RUN_TRIGGER } from "@/task/labels";

export function RunView({ runId }: { runId: string }) {
  const run = useRun(runId);
  const live = run.data?.status === "queued" || run.data?.status === "running";
  const log = useRunLog(runId, live);
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  if (run.isError) return <p className="p-4 text-sm text-destructive">{run.error.message}</p>;
  if (!run.data) return <p className="p-4 text-sm text-muted-foreground">Загрузка…</p>;

  const r = run.data;
  const status = RUN_STATUS[r.status];

  const cancel = async () => {
    try {
      await api(`/runs/${runId}/cancel`, { method: "POST" });
      queryClient.invalidateQueries({ queryKey: ["run", runId] });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  };

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex items-center gap-2">
        <span className={cn("size-3 rounded-full", status.dot)} />
        <h2 className="font-semibold">{r.dry_run ? "Пробный запуск" : "Запуск"}</h2>
        <Badge variant={r.status === "succeeded" ? "success" : r.status === "failed" ? "destructive" : "secondary"}>{status.label}</Badge>
        {live && (
          <Button size="sm" variant="outline" className="ml-auto" onClick={cancel}>
            <Square /> Отменить
          </Button>
        )}
      </div>

      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="text-muted-foreground">Как</dt>
        <dd>{r.dry_run ? "пробный: команды, которые что-то меняют, только пишут, что сделали бы" : (RUN_TRIGGER[r.trigger] ?? r.trigger)}</dd>
        <dt className="text-muted-foreground">Начат</dt>
        <dd>{dateTime(r.started_at)}</dd>
        {r.finished_at && (
          <>
            <dt className="text-muted-foreground">Длился</dt>
            <dd>{duration(r.started_at, r.finished_at) ?? "—"}</dd>
          </>
        )}
      </dl>

      {r.error && <p className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">{r.error}</p>}
      {error && <p className="text-sm text-destructive">{error}</p>}

      <ol className="flex flex-col gap-2">
        {log.data?.map((entry) => (
          <li key={entry.seq}>
            <Entry entry={entry} />
          </li>
        ))}
      </ol>
    </div>
  );
}

function Entry({ entry }: { entry: LogEntry }) {
  const tools = useTools();

  if (entry.kind === "start") {
    const variables = entry.variables as Record<string, unknown> | undefined;

    return (
      <div className="text-xs text-muted-foreground">
        {dateTime(entry.at)} · начало{variables && Object.keys(variables).length > 0 && <Json label="Переменные" value={variables} />}
      </div>
    );
  }

  if (entry.kind === "notify") {
    return (
      <div className="rounded-md border border-dashed p-2 text-sm">
        <div className="text-xs text-muted-foreground">Сообщение пользователю</div>
        <div className="whitespace-pre-wrap">{String(entry.message)}</div>
      </div>
    );
  }

  if (entry.kind === "end") {
    return (
      <div className="text-xs text-muted-foreground">
        {dateTime(entry.at)} · конец: {RUN_STATUS[entry.status as keyof typeof RUN_STATUS]?.label ?? String(entry.status)}
        {entry.result !== undefined && entry.result !== null && <Json label="Итог" value={entry.result} />}
      </div>
    );
  }

  const ok = entry.status === "ok";
  const skipped = entry.status === "skipped";
  const Icon = ok ? Check : skipped ? CircleSlash : X;
  const lines = (entry.logs as string[] | undefined) ?? [];

  return (
    <details className="rounded-md border text-sm" open={!ok}>
      <summary className="flex cursor-pointer list-none items-center gap-2 px-2 py-1.5">
        <Icon className={cn("size-4 shrink-0", ok ? "text-success" : skipped ? "text-muted-foreground" : "text-destructive")} />
        <span className="font-medium">{entry.tool ? toolTitle(String(entry.tool), tools.data) : String(entry.step_id ?? entry.step_key)}</span>
        <span className="truncate text-xs text-muted-foreground">
          {entry.step_key}
          {Number(entry.attempt) > 1 && ` · попытка ${entry.attempt}`}
          {typeof entry.duration_ms === "number" && ` · ${(entry.duration_ms / 1000).toFixed(1)} с`}
        </span>
      </summary>
      <div className="flex flex-col gap-2 border-t px-2 py-2">
        {lines.length > 0 && (
          <ul className="text-xs">
            {lines.map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ul>
        )}
        {entry.error !== undefined && <p className="text-xs text-destructive">{String(entry.error)}</p>}
        {entry.input !== undefined && <Json label="Аргументы" value={entry.input} />}
        {entry.output !== undefined && entry.output !== null && <Json label="Результат" value={entry.output} />}
        {entry.traceback !== undefined && <Json label="Traceback" value={entry.traceback} />}
      </div>
    </details>
  );
}

function Json({ label, value }: { label: string; value: unknown }) {
  return (
    <details className="mt-1 text-xs">
      <summary className="cursor-pointer text-muted-foreground">{label}</summary>
      <pre className="mt-1 max-h-72 overflow-auto rounded bg-muted p-2 whitespace-pre-wrap">{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}
