// One SSE connection to main's GET /events for the whole interface. After a drop it
// reconnects with `since` = the last event seen, so nothing is missed. Every event
// refreshes the data it changes; screens that want the events themselves (the chat's
// live progress, the feed) subscribe with useEvents.

import { fetchEventSource } from "@microsoft/fetch-event-source";
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useRef, type ReactNode } from "react";

import { currentConnection } from "@/api/client";
import { authHeaders, baseUrl } from "@/api/connection";
import { keys, RECENT_EVENTS } from "@/api/queries";
import type { AgentEvent } from "@/api/types";

type Listener = (event: AgentEvent) => void;

const RECONNECT_MS = 3000;

const EventsContext = createContext<Set<Listener>>(new Set());

export function EventsProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const listeners = useRef(new Set<Listener>()).current;

  useEffect(() => {
    let lastId: number | null = null;
    let controller: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;

    const connect = () => {
      const connection = currentConnection();
      controller = new AbortController();

      fetchEventSource(`${baseUrl(connection)}/events${lastId !== null ? `?since=${lastId}` : ""}`, {
        headers: authHeaders(connection),
        signal: controller.signal,
        openWhenHidden: true,
        async onopen(response) {
          if (!response.ok) throw new Error(`events: HTTP ${response.status}`);
        },
        onmessage(message) {
          if (!message.data) return;

          const event = JSON.parse(message.data) as AgentEvent;
          lastId = event.id;

          refresh(queryClient, event);
          listeners.forEach((listener) => listener(event));
        },
        // Our own reconnect, with `since`: the library's would start from now.
        onclose() {
          throw new Error("events: closed");
        },
        onerror(error) {
          throw error;
        },
      }).catch(() => {
        if (!stopped) timer = setTimeout(connect, RECONNECT_MS);
      });
    };

    connect();

    return () => {
      stopped = true;
      clearTimeout(timer);
      controller?.abort();
    };
  }, [queryClient, listeners]);

  return <EventsContext.Provider value={listeners}>{children}</EventsContext.Provider>;
}

export function useEvents(listener: Listener): void {
  const listeners = useContext(EventsContext);
  const latest = useRef(listener);
  latest.current = listener;

  useEffect(() => {
    const call: Listener = (event) => latest.current(event);
    listeners.add(call);

    return () => {
      listeners.delete(call);
    };
  }, [listeners]);
}

// What each event changes on the server.
function refresh(queryClient: QueryClient, event: AgentEvent): void {
  const [group] = event.type.split(".");

  // The feed's list grows by the event itself, without asking again.
  queryClient.setQueryData<AgentEvent[]>(keys.events, (events) =>
    events && !events.some((known) => known.id === event.id) ? [...events, event].slice(-RECENT_EVENTS) : events,
  );

  if (group === "task") {
    queryClient.invalidateQueries({ queryKey: keys.tasks });
    queryClient.invalidateQueries({ queryKey: keys.chats });
    queryClient.invalidateQueries({ queryKey: ["task"] });
    queryClient.invalidateQueries({ queryKey: ["approvals"] });
    queryClient.invalidateQueries({ queryKey: keys.jobs });
  }

  if (group === "run") {
    queryClient.invalidateQueries({ queryKey: keys.tasks });
    queryClient.invalidateQueries({ queryKey: ["runs"] });
    queryClient.invalidateQueries({ queryKey: ["run"] });
    // A cron run moved the job's next time on.
    queryClient.invalidateQueries({ queryKey: keys.jobs });
  }

  // A run's message lands in its task's chat.
  if (event.type === "notify") {
    queryClient.invalidateQueries({ queryKey: ["chat"] });
  }
}
