import { Plug } from "lucide-react";
import { useState } from "react";
import { Link, Outlet, useMatch } from "react-router";

import { ApiError } from "@/api/client";
import { useHealth, useLlm } from "@/api/queries";
import { ConnectionScreen } from "@/app/ConnectionScreen";
import { LeftPanel } from "@/app/LeftPanel";
import { RightPanel } from "@/app/RightPanel";
import { TopBar } from "@/app/TopBar";

export function Layout() {
  const [eventsOpen, setEventsOpen] = useState(false);
  const health = useHealth();

  // Unreachable or refused: nothing else can work, ask where the agent is.
  const lost = health.error instanceof ApiError && (health.error.status === 0 || health.error.status === 401);

  return (
    <div className="flex h-full flex-col">
      <TopBar eventsOpen={eventsOpen} onToggleEvents={() => setEventsOpen((open) => !open)} />

      {!lost && <NoModel />}

      {lost ? (
        <ConnectionScreen error={health.error as ApiError} onConnected={() => health.refetch()} />
      ) : (
        <div className="flex min-h-0 flex-1">
          <LeftPanel />
          <main className="flex min-w-0 flex-1 flex-col">
            <Outlet />
          </main>
          <RightPanel eventsOpen={eventsOpen} onCloseEvents={() => setEventsOpen(false)} />
        </div>
      )}
    </div>
  );
}


// The first start (or a removed provider): without a chat model the agent cannot answer.
function NoModel() {
  const llm = useLlm();
  const onModels = useMatch("/settings/models");

  if (!llm.data || llm.data.roles.chat !== null || onModels) return null;

  return (
    <div className="flex items-center gap-2 border-b bg-amber-500/10 px-4 py-2 text-sm">
      <Plug className="size-4 shrink-0" />
      Модель для чата не подключена: агент не сможет отвечать.
      <Link to="/settings/models" className="font-medium underline underline-offset-4">
        Подключить
      </Link>
    </div>
  );
}
