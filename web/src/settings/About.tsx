// How the agent is doing: its services, the connection, the tail of its logs.

import { useEffect, useRef, useState } from "react";

import { currentConnection, setConnection } from "@/api/client";
import { saveConnection } from "@/api/connection";
import { useLogs, useServices } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { dateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Section, selectClass } from "@/settings/common";

export function About() {
  const services = useServices();
  const [lines, setLines] = useState(200);
  const logs = useLogs(lines);
  const [file, setFile] = useState<string | null>(null);
  const connection = currentConnection();

  const files = Object.keys(logs.data?.files ?? {});
  const shown = file && files.includes(file) ? file : files[0];
  const text = shown ? (logs.data?.files[shown] ?? []).join("\n") : "Логов пока нет.";

  // The newest lines are at the bottom: open there.
  const pre = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (pre.current) pre.current.scrollTop = pre.current.scrollHeight;
  }, [text]);

  return (
    <div className="flex flex-col gap-8">
      <Section title="Подключение">
        <p className="text-sm">
          {connection.kind === "proxy" ? "Через dev-сервер: адрес и токен подставляет он." : `Напрямую к ${connection.url}.`}
        </p>
        {connection.kind === "direct" && (
          <Button
            size="sm"
            variant="outline"
            className="self-start"
            onClick={() => {
              saveConnection({ kind: "proxy" });
              setConnection({ kind: "proxy" });
              window.location.reload();
            }}
          >
            Переключиться на dev-сервер
          </Button>
        )}
      </Section>

      <Section title="Сервисы" description="Зарегистрированы в main; обновляется само.">
        <ul className="flex flex-col gap-1 text-sm">
          {Object.entries(services.data ?? {}).map(([name, status]) => {
            const fresh = status.last_seen && Date.now() - new Date(status.last_seen).getTime() < 60_000;

            return (
              <li key={name} className="flex items-center gap-2">
                <span className={cn("size-2 rounded-full", fresh ? "bg-success" : "bg-destructive")} />
                <span className="w-28">{name}</span>
                <span className="text-xs text-muted-foreground">отмечался {dateTime(status.last_seen)}</span>
              </li>
            );
          })}
        </ul>
      </Section>

      <Section title="Логи" description={logs.data ? `Папка: ${logs.data.dir}` : undefined}>
        <div className="flex flex-wrap items-center gap-2">
          <select className={selectClass} value={shown ?? ""} onChange={(e) => setFile(e.target.value)}>
            {files.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          <select className={selectClass} value={lines} onChange={(e) => setLines(Number(e.target.value))}>
            {[100, 200, 500, 2000].map((count) => (
              <option key={count} value={count}>
                последние {count}
              </option>
            ))}
          </select>
          <Button size="sm" variant="outline" onClick={() => logs.refetch()}>
            Обновить
          </Button>
        </div>
        <pre ref={pre} className="max-h-[60vh] overflow-auto rounded-lg bg-muted p-3 text-xs leading-relaxed whitespace-pre-wrap">
          {text}
        </pre>
      </Section>
    </div>
  );
}
