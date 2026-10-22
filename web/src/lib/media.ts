import { useSyncExternalStore } from "react";

// Whether a media query matches now, following changes (a resized window).
export function useMedia(query: string): boolean {
  return useSyncExternalStore(
    (listener) => {
      const media = window.matchMedia(query);
      media.addEventListener("change", listener);
      return () => media.removeEventListener("change", listener);
    },
    () => window.matchMedia(query).matches,
  );
}

// Three columns from here; below, the side panels slide over the chat.
export const WIDE = "(min-width: 1280px)";
export const MEDIUM = "(min-width: 1024px)";
