import { MessageSquare, MessageSquarePlus, Workflow } from "lucide-react";
import type { ReactNode } from "react";
import { NavLink, useNavigate } from "react-router";

import { useChats, useTasks } from "@/api/queries";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { TASK_STATUS as STATUS } from "@/task/labels";

export function LeftPanel({ overlay = false }: { overlay?: boolean }) {
  const tasks = useTasks();
  const chats = useChats();
  const navigate = useNavigate();

  const temporary = (chats.data ?? []).filter((chat) => chat.task_id === null);

  return (
    <aside className={cn("flex w-72 shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground", overlay && "absolute inset-y-0 left-0 z-40 shadow-xl")}>
      <div className="p-3">
        <Button className="w-full justify-start" variant="outline" onClick={() => navigate("/chat/new")}>
          <MessageSquarePlus /> Новый чат
        </Button>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <Section title="Задачи" empty={tasks.isSuccess && tasks.data.length === 0 ? "Задач пока нет: опишите работу в чате и попросите собрать задачу" : null}>
          {tasks.data?.map((task) => (
            <Item key={task.id} to={task.source_session_id ? `/chat/${task.source_session_id}` : `/task/${task.id}`} icon={<Workflow />}>
              <span className="truncate">{task.title}</span>
              <Badge variant={STATUS[task.status].variant} className="ml-auto">
                {STATUS[task.status].label}
              </Badge>
            </Item>
          ))}
        </Section>

        <Section title="Чаты" hint="Чат, из которого не собрали задачу, удаляется, если в нём долго не писать (настройка chat_idle_hours)" empty={chats.isSuccess && temporary.length === 0 ? "Нет чатов" : null}>
          {temporary.map((chat) => (
            <Item key={chat.id} to={`/chat/${chat.id}`} icon={<MessageSquare />}>
              <span className="truncate">{chat.title || "Без названия"}</span>
            </Item>
          ))}
        </Section>
      </ScrollArea>
    </aside>
  );
}

function Section({ title, hint, empty, children }: { title: string; hint?: string; empty: string | null; children: ReactNode }) {
  return (
    <section className="px-3 pb-4">
      <h2 className="px-2 pb-1 text-xs font-medium text-muted-foreground" title={hint}>
        {title}
      </h2>
      {empty ? <p className="px-2 text-sm text-muted-foreground">{empty}</p> : <nav className="flex flex-col gap-0.5">{children}</nav>}
    </section>
  );
}

function Item({ to, icon, children }: { to: string; icon: ReactNode; children: ReactNode }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-sidebar-accent [&>svg]:size-4 [&>svg]:shrink-0 [&>svg]:text-muted-foreground",
          isActive && "bg-sidebar-accent font-medium",
        )
      }
    >
      {icon}
      {children}
    </NavLink>
  );
}
