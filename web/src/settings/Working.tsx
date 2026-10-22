// Working settings (GET/PATCH /settings, docs/SETTINGS.md). A setting the environment
// sets is locked: shown, not editable.

import { useQueryClient } from "@tanstack/react-query";
import { Lock, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "@/api/client";
import { keys, useSettings } from "@/api/queries";
import type { Tunable } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Section, selectClass, useAction } from "@/settings/common";

export function Working() {
  const settings = useSettings();
  const queryClient = useQueryClient();
  const { busy, error, run } = useAction();
  const [values, setValues] = useState<Record<string, unknown>>({});

  useEffect(() => {
    if (settings.data) setValues(Object.fromEntries(settings.data.map((item) => [item.key, item.value])));
  }, [settings.data]);

  if (settings.isError) return <p className="text-sm text-destructive">{settings.error.message}</p>;
  if (!settings.data) return <p className="text-sm text-muted-foreground">Загрузка…</p>;

  const changed = Object.fromEntries(settings.data.filter((item) => !item.locked && values[item.key] !== item.value).map((item) => [item.key, values[item.key]]));

  const save = (body: Record<string, unknown>) =>
    run("save", async () => {
      await api("/settings", { method: "PATCH", json: body });
      await queryClient.invalidateQueries({ queryKey: keys.settings });
    });

  return (
    <Section title="Рабочие настройки" description="Меняются без перезапуска. Заданные переменными окружения закреплены и здесь не меняются.">
      <div className="flex flex-col divide-y rounded-lg border">
        {settings.data.map((item) => (
          <div key={item.key} className="flex flex-col gap-1.5 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm">{item.description}</span>
              {item.locked && (
                <Badge variant="outline" title={`Переменная окружения ${item.key.toUpperCase()}`}>
                  <Lock /> окружение
                </Badge>
              )}
              {item.source === "db" && <Badge variant="secondary">изменено</Badge>}
            </div>
            <div className="flex items-center gap-2">
              <Control item={item} value={values[item.key]} onChange={(value) => setValues((current) => ({ ...current, [item.key]: value }))} />
              {item.source === "db" && (
                <Button size="sm" variant="ghost" onClick={() => save({ [item.key]: null })} disabled={busy !== null} title={`По умолчанию: ${String(item.default)}`}>
                  <RotateCcw /> По умолчанию
                </Button>
              )}
            </div>
            <span className="text-xs text-muted-foreground">{item.key}</span>
          </div>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <Button onClick={() => save(changed)} disabled={busy !== null || Object.keys(changed).length === 0}>
          Сохранить
        </Button>
        {error && <span className="text-sm text-destructive">{error}</span>}
      </div>
    </Section>
  );
}

function Control({ item, value, onChange }: { item: Tunable; value: unknown; onChange: (value: unknown) => void }) {
  if (item.type === "bool") {
    return <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} disabled={item.locked} />;
  }

  if (item.type === "choice") {
    return (
      <select className={selectClass} value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} disabled={item.locked}>
        {(item.choices ?? []).map((choice) => (
          <option key={choice} value={choice}>
            {choice || "не задано"}
          </option>
        ))}
      </select>
    );
  }

  if (item.type === "int") {
    return (
      <Input
        type="number"
        className="w-32"
        min={item.min ?? undefined}
        max={item.max ?? undefined}
        value={value === null || value === undefined ? "" : String(value)}
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
        disabled={item.locked}
      />
    );
  }

  return <Input value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} disabled={item.locked} placeholder="полный путь к папке" />;
}
