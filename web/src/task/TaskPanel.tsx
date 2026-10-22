// The task on the right, top to bottom: what it is and when it runs, its scenario, its
// versions, its settings and its runs. A version or a run opens in place (?version= /
// ?run= in the address), with a way back.

import { useQueryClient } from "@tanstack/react-query";
import { Archive, ChevronRight, Pause, Play } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router";

import { api, ApiError } from "@/api/client";
import { keys, useJobs, useTask, useTaskRuns, useVersion } from "@/api/queries";
import type { Run, TaskDetail, Version } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { dateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Cards } from "@/task/Cards";
import { DraftActions } from "@/task/DraftActions";
import { RUN_STATUS, RUN_TRIGGER, TASK_STATUS, VERSION_STATUS } from "@/task/labels";
import { TaskSettingsForm } from "@/task/TaskSettingsForm";

export function TaskPanel({ taskId }: { taskId: string }) {
  const task = useTask(taskId);

  // A task not approved yet has no active version: its newest draft stands in.
  const draftId = task.data && !task.data.current_version ? task.data.versions[0]?.id : null;
  const draft = useVersion(taskId, draftId);

  if (task.isError) return <p className="p-4 text-sm text-destructive">{task.error.message}</p>;
  if (!task.data) return <p className="p-4 text-sm text-muted-foreground">Загрузка…</p>;

  const version = task.data.current_version ?? draft.data ?? null;

  return (
    <div className="flex flex-col divide-y">
      <Header task={task.data} version={version} />
      <Scenario task={task.data} version={version} />
      <Versions task={task.data} />
      <Block title="Настройки задачи">
        <TaskSettingsForm taskId={taskId} />
      </Block>
      <Runs taskId={taskId} />
    </div>
  );
}

function Block({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="flex flex-col gap-2 p-4">
      <div className="flex items-center gap-2">
        <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{title}</h3>
        <div className="ml-auto">{aside}</div>
      </div>
      {children}
    </section>
  );
}

function Header({ task, version }: { task: TaskDetail; version: Version | null }) {
  const jobs = useJobs();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [, setSearch] = useSearchParams();
  const [busy, setBusy] = useState(false);
  const [archiveArmed, setArchiveArmed] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const next = jobs.data?.find((job) => job.task_id === task.id)?.next_run_time;

  useEffect(() => {
    if (!archiveArmed) return;
    const timer = setTimeout(() => setArchiveArmed(false), 3000);
    return () => clearTimeout(timer);
  }, [archiveArmed]);

  const act = async (call: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);

    try {
      await call();
      queryClient.invalidateQueries({ queryKey: keys.task(task.id) });
      queryClient.invalidateQueries({ queryKey: keys.tasks });
      queryClient.invalidateQueries({ queryKey: keys.jobs });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const runNow = () =>
    act(async () => {
      const run = await api<Run>("/runs", { method: "POST", json: { task_id: task.id } });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      setSearch((search) => {
        search.set("run", run.id);
        return search;
      });
    });

  const archive = () =>
    act(async () => {
      await api(`/tasks/${task.id}/archive`, { method: "POST" });
      navigate("/");
    });

  return (
    <section className="flex flex-col gap-2 p-4">
      <div className="flex items-start gap-2">
        <h2 className="min-w-0 flex-1 font-semibold">{task.title}</h2>
        <Badge variant={TASK_STATUS[task.status].variant}>{TASK_STATUS[task.status].label}</Badge>
      </div>
      {task.description && <p className="text-sm text-muted-foreground">{task.description}</p>}
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="text-muted-foreground">Когда</dt>
        <dd>
          {task.schedule ?? version?.schedule ?? "—"}
          {!task.current_version && <span className="text-muted-foreground"> · после одобрения</span>}
        </dd>
        {task.status === "active" && next && (
          <>
            <dt className="text-muted-foreground">Следующий</dt>
            <dd>{dateTime(next)}</dd>
          </>
        )}
      </dl>
      <div className="flex flex-wrap gap-2 pt-1">
        <Button size="sm" onClick={runNow} disabled={busy || task.status !== "active"}>
          <Play /> Запустить
        </Button>
        {task.status === "active" && (
          <Button size="sm" variant="outline" onClick={() => act(() => api(`/tasks/${task.id}/pause`, { method: "POST" }))} disabled={busy}>
            <Pause /> Пауза
          </Button>
        )}
        {task.status === "paused" && (
          <Button size="sm" variant="outline" onClick={() => act(() => api(`/tasks/${task.id}/resume`, { method: "POST" }))} disabled={busy}>
            <Play /> Возобновить
          </Button>
        )}
        <Button size="sm" variant={archiveArmed ? "destructive" : "ghost"} onClick={archiveArmed ? archive : () => setArchiveArmed(true)} disabled={busy}>
          <Archive /> {archiveArmed ? "Точно в архив?" : "В архив"}
        </Button>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
    </section>
  );
}

function Scenario({ task, version }: { task: TaskDetail; version: Version | null }) {
  return (
    <Block title={task.current_version ? "Сценарий" : "Сценарий черновика"}>
      {version ? <Cards cards={version.cards} /> : <p className="text-sm text-muted-foreground">Загрузка…</p>}
      {!task.current_version && version && (
        <div className="pt-2">
          <DraftActions taskId={task.id} versionId={version.id} />
        </div>
      )}
    </Block>
  );
}

function Versions({ task }: { task: TaskDetail }) {
  const [, setSearch] = useSearchParams();

  return (
    <Block title="Версии">
      <ul className="flex flex-col">
        {task.versions.map((version) => (
          <li key={version.id}>
            <button
              className="flex w-full items-center gap-2 rounded-md px-1 py-1.5 text-left text-sm hover:bg-accent"
              onClick={() =>
                setSearch((search) => {
                  search.set("version", version.id);
                  search.delete("run");
                  return search;
                })
              }
            >
              <span className="w-8 shrink-0 text-muted-foreground tabular-nums">v{version.version_no}</span>
              <span className="min-w-0 flex-1 truncate">{version.summary || "—"}</span>
              <Badge variant={version.id === task.current_version_id ? "success" : version.status === "draft" ? "outline" : "secondary"}>
                {version.id === task.current_version_id ? "активная" : VERSION_STATUS[version.status]}
              </Badge>
              <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
            </button>
          </li>
        ))}
      </ul>
    </Block>
  );
}

function Runs({ taskId }: { taskId: string }) {
  const [withEmpty, setWithEmpty] = useState(false);
  const runs = useTaskRuns(taskId, withEmpty);
  const [, setSearch] = useSearchParams();

  return (
    <Block
      title="Запуски"
      aside={
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <input type="checkbox" checked={withEmpty} onChange={(e) => setWithEmpty(e.target.checked)} />
          пустые проверки
        </label>
      }
    >
      {runs.data?.length === 0 && <p className="text-sm text-muted-foreground">Запусков ещё не было.</p>}
      <ul className="flex flex-col">
        {runs.data?.map((run) => (
          <li key={run.id}>
            <button
              className="flex w-full items-center gap-2 rounded-md px-1 py-1.5 text-left text-sm hover:bg-accent"
              onClick={() =>
                setSearch((search) => {
                  search.set("run", run.id);
                  search.delete("version");
                  return search;
                })
              }
            >
              <span className={cn("size-2.5 shrink-0 rounded-full", RUN_STATUS[run.status].dot)} aria-label={RUN_STATUS[run.status].label} />
              <span className="tabular-nums">{dateTime(run.started_at ?? run.finished_at)}</span>
              <span className="truncate text-muted-foreground">
                {run.dry_run ? "пробный" : (RUN_TRIGGER[run.trigger] ?? run.trigger)} · {RUN_STATUS[run.status].label}
              </span>
              <ChevronRight className="ml-auto size-4 shrink-0 text-muted-foreground" />
            </button>
          </li>
        ))}
      </ul>
    </Block>
  );
}
