// The right side when no task is open (or the bell asks): what waits for the user, what
// runs now, then every event, newest first. A line opens its chat, task or run log.

import { useQueryClient } from "@tanstack/react-query";
import { FileClock, LoaderCircle, Square } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router";

import { api, ApiError } from "@/api/client";
import { useApprovals, useRecentEvents, useTasks } from "@/api/queries";
import type { TaskSummary } from "@/api/types";
import { Button } from "@/components/ui/button";
import { FEED_TYPES, feedLine, runningRuns, titlesFrom, type FeedLine } from "@/events/feed";
import { markSeen } from "@/events/seen";
import { dateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

const TONE = { good: "bg-success", bad: "bg-destructive", info: "bg-sky-500", plain: "bg-muted-foreground" };

export function EventFeed({ onOpen }: { onOpen: () => void }) {
  const events = useRecentEvents();
  const tasks = useTasks();
  const approvals = useApprovals();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const byId = useMemo(() => new Map((tasks.data ?? []).map((task) => [task.id, task])), [tasks.data]);
  const feed = (events.data ?? []).filter((event) => FEED_TYPES.has(event.type)).reverse();
  const running = runningRuns(events.data ?? []);
  const titles = useMemo(() => titlesFrom(events.data ?? []), [events.data]);

  // Looking at the feed reads it.
  const newest = events.data?.at(-1)?.id;
  useEffect(() => {
    if (newest !== undefined) markSeen(newest);
  }, [newest]);

  const open = (line: Pick<FeedLine, "taskId" | "runId" | "sessionId">) => {
    const chat = line.sessionId ?? (line.taskId ? chatOf(byId.get(line.taskId)) : undefined);
    const base = chat ? `/chat/${chat}` : line.taskId && byId.has(line.taskId) ? `/task/${line.taskId}` : "/";

    navigate(line.runId ? `${base}?run=${line.runId}` : base);
    onOpen();
  };

  const [cancelError, setCancelError] = useState<string | null>(null);

  const cancel = async (runId: string) => {
    setCancelError(null);
    try {
      await api(`/runs/${runId}/cancel`, { method: "POST" });
    } catch (e) {
      // Already finished (409) or the runner is away: say so instead of doing nothing.
      setCancelError(e instanceof ApiError ? e.message : String(e));
    }
    queryClient.invalidateQueries({ queryKey: ["run", runId] });
  };

  return (
    <div className="flex flex-col divide-y">
      {(approvals.data?.length ?? 0) > 0 && (
        <Pinned title="Ждут решения">
          {approvals.data!.map((approval) => (
            <button key={approval.version_id} className="flex w-full items-center gap-2 rounded-md px-1 py-1.5 text-left text-sm hover:bg-accent" onClick={() => open({ taskId: approval.task_id, sessionId: approval.session_id ?? undefined })}>
              <FileClock className="size-4 shrink-0 text-sky-500" />
              <span className="truncate">{approval.title || byId.get(approval.task_id)?.title || "Черновик"}</span>
              <span className="ml-auto shrink-0 text-xs text-muted-foreground">черновик</span>
            </button>
          ))}
        </Pinned>
      )}

      {running.length > 0 && (
        <Pinned title="Идут сейчас">
          {running.map((event) => {
            const line = feedLine(event, byId, titles);
            const runId = String(event.payload.run_id);

            return (
              <div key={event.id} className="flex items-center gap-2 text-sm">
                <button className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-1 py-1.5 text-left hover:bg-accent" onClick={() => open(line)}>
                  <LoaderCircle className="size-4 shrink-0 animate-spin text-sky-500" />
                  <span className="truncate">{String(event.payload.title ?? "Запуск")}</span>
                </button>
                <Button size="sm" variant="ghost" onClick={() => cancel(runId)} aria-label="Отменить запуск">
                  <Square />
                </Button>
              </div>
            );
          })}
          {cancelError && <p className="px-1 text-xs text-destructive">{cancelError}</p>}
        </Pinned>
      )}

      <section className="flex flex-col p-2">
        {events.isSuccess && feed.length === 0 && <p className="p-2 text-sm text-muted-foreground">Событий пока нет: здесь появятся запуски задач, их сообщения и черновики на одобрение.</p>}
        {feed.map((event) => {
          const line = feedLine(event, byId, titles);

          return (
            <button key={event.id} className="flex w-full gap-2 rounded-md px-2 py-2 text-left hover:bg-accent" onClick={() => open(line)}>
              <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", TONE[line.tone])} />
              <span className="min-w-0 flex-1">
                <span className="block text-sm">{line.text}</span>
                {line.detail && <span className="line-clamp-3 block text-xs whitespace-pre-wrap text-muted-foreground">{line.detail}</span>}
                <span className="block text-xs text-muted-foreground">{dateTime(event.created_at)}</span>
              </span>
            </button>
          );
        })}
      </section>
    </div>
  );
}

function Pinned({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-1 p-3">
      <h3 className="px-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">{title}</h3>
      {children}
    </section>
  );
}

function chatOf(task: TaskSummary | undefined): string | undefined {
  return task?.source_session_id ?? undefined;
}
