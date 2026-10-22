import { useDiff, useTask, useVersion } from "@/api/queries";
import { Badge } from "@/components/ui/badge";
import { dateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Cards } from "@/task/Cards";
import { DraftActions } from "@/task/DraftActions";
import { VERSION_STATUS } from "@/task/labels";

const CREATED_BY: Record<string, string> = { compile: "собрана в чате", patch: "правка словами", manual: "правка вручную" };

export function VersionView({ taskId, versionId }: { taskId: string; versionId: string }) {
  const task = useTask(taskId);
  const version = useVersion(taskId, versionId);
  const hasParent = Boolean(version.data?.parent_version_id);
  const diff = useDiff(taskId, versionId, hasParent);

  if (version.isError) return <p className="p-4 text-sm text-destructive">{version.error.message}</p>;
  if (!version.data) return <p className="p-4 text-sm text-muted-foreground">Загрузка…</p>;

  const v = version.data;
  const active = task.data?.current_version_id === v.id;

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex items-center gap-2">
        <h2 className="font-semibold">Версия {v.version_no}</h2>
        <Badge variant={active ? "success" : v.status === "draft" ? "outline" : "secondary"}>{active ? "активная" : VERSION_STATUS[v.status]}</Badge>
      </div>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="text-muted-foreground">Создана</dt>
        <dd>
          {dateTime(v.created_at)} · {CREATED_BY[v.created_by] ?? v.created_by}
        </dd>
        <dt className="text-muted-foreground">Когда</dt>
        <dd>{v.schedule ?? "—"}</dd>
        {v.approved_at && (
          <>
            <dt className="text-muted-foreground">Одобрена</dt>
            <dd>{dateTime(v.approved_at)}</dd>
          </>
        )}
      </dl>

      {v.status === "draft" && <DraftActions taskId={taskId} versionId={v.id} />}

      <section className="flex flex-col gap-2">
        <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Что изменилось</h3>
        {!hasParent && <p className="text-sm text-muted-foreground">Первая версия задачи.</p>}
        {diff.isError && <p className="text-sm text-destructive">{diff.error.message}</p>}
        {diff.data && diff.data.lines.length === 0 && <p className="text-sm text-muted-foreground">Для пользователя ничего не изменилось.</p>}
        {diff.data && <DiffLines lines={diff.data.lines} />}
      </section>

      <section className="flex flex-col gap-2">
        <h3 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Сценарий</h3>
        <Cards cards={v.cards} />
      </section>
    </div>
  );
}

// "+ новый шаг", "− убран шаг", "~ было → стало" (compiler/cards.py).
export function DiffLines({ lines }: { lines: string[] }) {
  return (
    <ul className="flex flex-col gap-1 text-sm">
      {lines.map((line, index) => (
        <li
          key={index}
          className={cn(
            "rounded-md px-2 py-1",
            line.startsWith("+") && "bg-success/10 text-success",
            line.startsWith("−") && "bg-destructive/10 text-destructive",
            line.startsWith("~") && "bg-amber-500/10",
          )}
        >
          {line}
        </li>
      ))}
    </ul>
  );
}
