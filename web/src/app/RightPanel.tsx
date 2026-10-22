// What the right side shows: the open task, or every event when no task is open (or when
// the bell asks for them). A version or a run (?version= / ?run=) opens in its place.

import { ArrowLeft, Bell, Workflow, X } from "lucide-react";
import { useMatch, useSearchParams } from "react-router";

import { useChats } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { RunView } from "@/task/RunView";
import { TaskPanel } from "@/task/TaskPanel";
import { VersionView } from "@/task/VersionView";

export function RightPanel({ eventsOpen, onCloseEvents }: { eventsOpen: boolean; onCloseEvents: () => void }) {
  // The layout sits above the routes, so it reads them from the URL itself.
  const chatMatch = useMatch("/chat/:sessionId");
  const taskMatch = useMatch("/task/:taskId");
  const chats = useChats();
  const [search, setSearch] = useSearchParams();

  const sessionId = chatMatch?.params.sessionId;
  const openTask = taskMatch?.params.taskId ?? chats.data?.find((chat) => chat.id === sessionId)?.task_id ?? null;

  const runId = search.get("run");
  const versionId = search.get("version");
  const detail = runId ? "run" : versionId && openTask ? "version" : null;
  const showEvents = eventsOpen || (openTask === null && detail === null);

  const back = () =>
    setSearch((params) => {
      params.delete("run");
      params.delete("version");
      return params;
    });

  const title = showEvents ? "События" : detail === "run" ? "Журнал запуска" : detail === "version" ? "Версия" : "Задача";

  return (
    <aside className="flex w-96 shrink-0 flex-col border-l">
      <div className="flex h-11 shrink-0 items-center gap-2 border-b px-3 text-sm font-medium">
        {!showEvents && detail ? (
          <Button variant="ghost" size="icon" className="size-7" onClick={back} aria-label="Назад к задаче">
            <ArrowLeft />
          </Button>
        ) : showEvents ? (
          <Bell className="size-4" />
        ) : (
          <Workflow className="size-4" />
        )}
        {title}
        {eventsOpen && (openTask !== null || detail !== null) && (
          <Button variant="ghost" size="icon" className="ml-auto size-7" onClick={onCloseEvents} aria-label="Закрыть события">
            <X />
          </Button>
        )}
      </div>

      <ScrollArea className="min-h-0 flex-1">
        {showEvents ? (
          <p className="p-4 text-sm text-muted-foreground">Здесь будут события: ждущие решения черновики, идущие запуски и всё, что сообщают задачи.</p>
        ) : detail === "run" && runId ? (
          <RunView runId={runId} />
        ) : detail === "version" && openTask && versionId ? (
          <VersionView taskId={openTask} versionId={versionId} />
        ) : openTask ? (
          <TaskPanel taskId={openTask} />
        ) : null}
      </ScrollArea>
    </aside>
  );
}
