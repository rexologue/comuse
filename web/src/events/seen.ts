// Which events the user has seen: the last id, in this browser. Nothing on the server
// tracks reading.

import { useSyncExternalStore } from "react";

const KEY = "agent.events.seen";
const listeners = new Set<() => void>();

let seen: number | null = read();

function read(): number | null {
  try {
    const value = localStorage.getItem(KEY);
    return value === null ? null : Number(value);
  } catch {
    return null;
  }
}

export function markSeen(id: number): void {
  if (seen !== null && id <= seen) return;

  seen = id;
  try {
    localStorage.setItem(KEY, String(id));
  } catch {
    // Unread counts reset with the page.
  }
  listeners.forEach((listener) => listener());
}

export function useSeen(): number | null {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => seen,
  );
}
