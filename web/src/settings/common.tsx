import { useState, type ReactNode } from "react";

import { ApiError } from "@/api/client";

export function Section({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <div>
        <h2 className="font-semibold">{title}</h2>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {children}
    </section>
  );
}

export const selectClass = "h-9 rounded-md border bg-transparent px-2 text-sm dark:bg-input/30";

// A call with its pending state and the error main sent, in words.
export function useAction() {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async <T,>(name: string, call: () => Promise<T>): Promise<T | undefined> => {
    setBusy(name);
    setError(null);

    try {
      return await call();
    } catch (e) {
      setError(describe(e));
      return undefined;
    } finally {
      setBusy(null);
    }
  };

  return { busy, error, setError, run };
}

export function describe(e: unknown): string {
  if (!(e instanceof ApiError)) return String(e instanceof Error ? e.message : e);

  const details = e.details as Record<string, unknown> | undefined;
  if (details && !Array.isArray(details) && Object.values(details).every((v) => typeof v === "string")) {
    return `${e.message}: ${Object.entries(details).map(([key, value]) => `${key} — ${value}`).join("; ")}`;
  }

  return e.message;
}
