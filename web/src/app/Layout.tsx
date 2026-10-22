import { Plug, ServerCrash } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, Outlet, useLocation, useMatch, useSearchParams } from "react-router";

import { ApiError } from "@/api/client";
import { useHealth, useLlm } from "@/api/queries";
import { ConnectionScreen } from "@/app/ConnectionScreen";
import { LeftPanel } from "@/app/LeftPanel";
import { RightPanel } from "@/app/RightPanel";
import { TopBar } from "@/app/TopBar";
import { MEDIUM, useMedia, WIDE } from "@/lib/media";

export function Layout() {
  const [eventsOpen, setEventsOpen] = useState(false);
  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);
  const health = useHealth();
  const wide = useMedia(WIDE);
  const medium = useMedia(MEDIUM);
  const location = useLocation();
  const [search, setSearch] = useSearchParams();

  // Unreachable or refused: nothing else can work, ask where the agent is.
  const lost = health.error instanceof ApiError && (health.error.status === 0 || health.error.status === 401);
  const down = health.isError && !lost;

  // On a narrow window a side panel covers the chat: going somewhere closes the left one.
  useEffect(() => setLeftOpen(false), [location.pathname]);

  // A run or a version asked for (a dry run, a line of the feed) is worth sliding in.
  // Settings need the room: there the events come only when asked.
  const onSettings = useMatch("/settings/*") !== null;
  const detail = search.has("run") || search.has("version");
  const rightShown = (wide && !onSettings) || rightOpen || eventsOpen || detail;

  const closeRight = () => {
    setRightOpen(false);
    setEventsOpen(false);
    if (detail)
      setSearch((params) => {
        params.delete("run");
        params.delete("version");
        return params;
      });
  };

  return (
    <div className="flex h-full flex-col">
      <TopBar
        eventsOpen={eventsOpen}
        onToggleEvents={() => setEventsOpen((open) => !open)}
        onToggleLeft={medium ? undefined : () => setLeftOpen((open) => !open)}
        onToggleRight={wide ? undefined : () => (rightShown ? closeRight() : setRightOpen(true))}
      />

      {!lost && <NoModel />}
      {down && (
        <div className="flex items-center gap-2 border-b bg-destructive/10 px-4 py-2 text-sm">
          <ServerCrash className="size-4 shrink-0" />
          Агент не отвечает: {(health.error as Error).message}. Повторю сам через несколько секунд.
        </div>
      )}

      {lost ? (
        <ConnectionScreen error={health.error as ApiError} onConnected={() => health.refetch()} />
      ) : (
        <div className="relative flex min-h-0 flex-1">
          {(medium || leftOpen) && <LeftPanel overlay={!medium} />}
          <main className="flex min-w-0 flex-1 flex-col">
            <Outlet />
          </main>
          {rightShown && <RightPanel overlay={!wide} eventsOpen={eventsOpen} onCloseEvents={() => setEventsOpen(false)} onClose={closeRight} />}

          {((!medium && leftOpen) || (!wide && rightShown)) && (
            <div className="absolute inset-0 z-30 bg-black/30" onClick={() => (setLeftOpen(false), closeRight())} aria-hidden />
          )}
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
