import { useState } from "react";
import { Outlet } from "react-router";

import { ApiError } from "@/api/client";
import { useHealth } from "@/api/queries";
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
