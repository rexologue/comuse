// The task's settings of its command groups (docs/TASKS.md#настройки-групп-команд): a
// form from each field's JSON schema. Only values change; groups and fields come from
// the code.

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, ApiError } from "@/api/client";
import { keys, useTaskSettings } from "@/api/queries";
import type { SettingField } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

type Values = Record<string, Record<string, unknown>>;

export function TaskSettingsForm({ taskId }: { taskId: string }) {
  const settings = useTaskSettings(taskId);
  const queryClient = useQueryClient();
  const [values, setValues] = useState<Values>({});
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ error: boolean; text: string } | null>(null);

  useEffect(() => {
    if (!settings.data) return;
    setValues(Object.fromEntries(Object.entries(settings.data.settings).map(([group, fields]) => [group, Object.fromEntries(Object.entries(fields).map(([name, field]) => [name, field.value]))])));
  }, [settings.data]);

  if (settings.isError) return <p className="text-sm text-destructive">{settings.error.message}</p>;
  if (!settings.data) return <p className="text-sm text-muted-foreground">Загрузка…</p>;

  const groups = Object.entries(settings.data.settings);
  if (groups.length === 0) return <p className="text-sm text-muted-foreground">У команд этой задачи нет настроек.</p>;

  const changed: Values = {};
  for (const [group, fields] of groups) {
    for (const [name, field] of Object.entries(fields)) {
      if (JSON.stringify(values[group]?.[name]) !== JSON.stringify(field.value)) (changed[group] ??= {})[name] = values[group]?.[name];
    }
  }

  const save = async () => {
    setSaving(true);
    setMessage(null);

    try {
      await api(`/tasks/${taskId}/settings`, { method: "PUT", json: changed });
      await queryClient.invalidateQueries({ queryKey: keys.taskSettings(taskId) });
      setMessage({ error: false, text: "Сохранено" });
    } catch (e) {
      const errors = e instanceof ApiError ? (e.details as { errors?: unknown[] } | undefined)?.errors : undefined;
      setMessage({ error: true, text: errors ? errors.map((item) => (typeof item === "string" ? item : JSON.stringify(item))).join("; ") : String(e instanceof Error ? e.message : e) });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      {groups.map(([group, fields]) =>
        Object.entries(fields).map(([name, field]) => (
          <Field
            key={`${group}.${name}`}
            field={field}
            value={values[group]?.[name]}
            onChange={(value) => setValues((current) => ({ ...current, [group]: { ...current[group], [name]: value } }))}
          />
        )),
      )}
      <div className="flex items-center gap-2">
        <Button size="sm" onClick={save} disabled={saving || Object.keys(changed).length === 0}>
          Сохранить
        </Button>
        {message && <span className={message.error ? "text-sm text-destructive" : "text-sm text-muted-foreground"}>{message.text}</span>}
      </div>
    </div>
  );
}

function Field({ field, value, onChange }: { field: SettingField; value: unknown; onChange: (value: unknown) => void }) {
  const type = field.schema.type;
  const choices = field.schema.enum as unknown[] | undefined;

  const label = (
    <span className="flex flex-col">
      <span className="text-sm">{field.title}</span>
      {field.description && <span className="text-xs text-muted-foreground">{field.description}</span>}
    </span>
  );

  if (type === "boolean") {
    return (
      <label className="flex items-start gap-2">
        <input type="checkbox" className="mt-1" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
        {label}
      </label>
    );
  }

  if (choices) {
    return (
      <label className="flex flex-col gap-1">
        {label}
        <select className="h-9 rounded-md border bg-transparent px-2 text-sm" value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}>
          {choices.map((choice) => (
            <option key={String(choice)} value={String(choice)}>
              {String(choice)}
            </option>
          ))}
        </select>
      </label>
    );
  }

  if (type === "integer" || type === "number") {
    return (
      <label className="flex flex-col gap-1">
        {label}
        <Input type="number" value={value === null || value === undefined ? "" : String(value)} onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />
      </label>
    );
  }

  // A list of strings: one per line.
  if (type === "array") {
    return (
      <label className="flex flex-col gap-1">
        {label}
        <Textarea
          value={Array.isArray(value) ? value.join("\n") : ""}
          onChange={(e) => onChange(e.target.value.split("\n").map((line) => line.trim()).filter(Boolean))}
          placeholder="по одному на строку"
        />
      </label>
    );
  }

  return (
    <label className="flex flex-col gap-1">
      {label}
      <Input value={value === null || value === undefined ? "" : String(value)} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}
