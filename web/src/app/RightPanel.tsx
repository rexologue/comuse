// What the right side shows: the open task, or every event when no task is open (or when
// the bell asks for them).

import { Bell, Workflow, X } from "lucide-react";
import { useMatch } from "react-router";

import { useChats } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";

export function RightPanel({ eventsOpen, onCloseEvents }: { eventsOpen: boolean; onCloseEvents: () => void }) {
  // The layout sits above the routes, so it reads them from the URL itself.
  const chatMatch = useMatch("/chat/:sessionId");
  const taskMatch = useMatch("/task/:taskId");
  const chats = useChats();

  const sessionId = chatMatch?.params.sessionId;
  const openTask = taskMatch?.params.taskId ?? chats.data?.find((chat) => chat.id === sessionId)?.task_id ?? null;
  const showEvents = eventsOpen || openTask === null;

  return (
    <aside className="flex w-96 shrink-0 flex-col border-l">
      <div className="flex h-11 shrink-0 items-center gap-2 border-b px-3 text-sm font-medium">
        {showEvents ? <Bell className="size-4" /> : <Workflow className="size-4" />}
        {showEvents ? "События" : "Задача"}
        {eventsOpen && openTask !== null && (
          <Button variant="ghost" size="icon" className="ml-auto size-7" onClick={onCloseEvents} aria-label="Вернуться к задаче">
            <X />
          </Button>
        )}
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <p className="p-4 text-sm text-muted-foreground">
          {showEvents
            ? "Здесь будут события: ждущие решения черновики, идущие запуски и всё, что сообщают задачи."
            : "Здесь будет задача: сценарий, версии, настройки и история запусков."}
        </p>
      </ScrollArea>
    </aside>
  );
}
