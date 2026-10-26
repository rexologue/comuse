// Which events the feed shows and how each reads. agent.tool is a chat's progress, not news.

import type { AgentEvent, RunStatus, TaskSummary } from "@/api/types";
import { RUN_STATUS } from "@/task/labels";

export const FEED_TYPES = new Set([
  "notify",
  "run.started",
  "run.finished",
  "task.draft",
  "task.approved",
  "task.rejected",
  "task.pause",
  "task.resume",
  "task.archive",
]);

export type FeedLine = { text: string; detail?: string; tone: "good" | "bad" | "info" | "plain"; taskId?: string; runId?: string; sessionId?: string };

// Task titles the events themselves carry: an archived task is no longer in the task
// list, and task.* events name only the id.
export function titlesFrom(events: AgentEvent[]): Map<string, string> {
  const titles = new Map<string, string>();

  for (const event of events) {
    const p = event.payload as Record<string, unknown>;
    const title = p.title as string | undefined;
    if (typeof p.task_id === "string" && title) titles.set(p.task_id, title);
  }

  return titles;
}

export function feedLine(event: AgentEvent, tasks: Map<string, TaskSummary>, titles: Map<string, string> = new Map()): FeedLine {
  const p = event.payload as Record<string, unknown>;
  const taskId = typeof p.task_id === "string" ? p.task_id : undefined;
  const title = (p.title as string | undefined) ?? (taskId ? (tasks.get(taskId)?.title ?? titles.get(taskId)) : undefined) ?? "задача";
  const runId = typeof p.run_id === "string" ? p.run_id : undefined;

  switch (event.type) {
    case "notify":
      return { text: `«${title}» сообщает`, detail: String(p.message ?? ""), tone: "info", taskId, runId };

    case "run.started":
      return { text: `${p.dry_run ? "Пробный запуск" : "Запуск"} «${title}» начался`, tone: "plain", taskId, runId };

    case "run.finished": {
      const status = p.status as RunStatus;
      return {
        text: `${p.dry_run ? "Пробный запуск" : "Запуск"} «${title}»: ${RUN_STATUS[status]?.label ?? status}`,
        detail: typeof p.error === "string" ? p.error : undefined,
        tone: status === "succeeded" ? "good" : status === "failed" ? "bad" : "plain",
        taskId,
        runId,
      };
    }

    case "task.draft":
      return { text: `Черновик «${title}» ждёт решения`, tone: "info", taskId, sessionId: (p.session_id as string | null) ?? undefined };

    case "task.approved":
      return { text: `Задача «${title}» включена`, tone: "good", taskId };

    case "task.rejected":
      return { text: `Черновик задачи «${title}» отклонён`, tone: "plain", taskId };

    case "task.pause":
      return { text: `Задача «${title}» на паузе`, tone: "plain", taskId };

    case "task.resume":
      return { text: `Задача «${title}» возобновлена`, tone: "good", taskId };

    case "task.archive":
      return { text: `Задача «${title}» в архиве`, tone: "plain", taskId };

    default:
      return { text: event.type, tone: "plain", taskId };
  }
}

// Runs started and not finished among the events at hand.
export function runningRuns(events: AgentEvent[]): AgentEvent[] {
  const finished = new Set(events.filter((e) => e.type === "run.finished").map((e) => e.payload.run_id));
  return events.filter((e) => e.type === "run.started" && !finished.has(e.payload.run_id));
}
