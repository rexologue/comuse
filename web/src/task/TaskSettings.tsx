// The settings of a task's commands at three levels (docs/TASKS.md#настройки): the
// command's default, a value for the whole task (TaskSettings, one row per setting) and a
// value for one step (StepSettings, inside the step). Each change is saved at once.

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, ApiError } from "@/api/client";
import { keys, useTaskSettings } from "@/api/queries";
import type { SettingChange, SettingField, TaskSettings as Settings } from "@/api/types";
import { Input } from "@/components/ui/input";
import { selectClass } from "@/settings/common";

export function TaskSettings({ taskId }: { taskId: string }) {
  const settings = useTaskSettings(taskId);
  const { change, error } = useSettingChange(taskId);

  if (settings.isError) return <p className="text-sm text-destructive">{settings.error.message}</p>;
  if (!settings.data) return <p className="text-sm text-muted-foreground">Загрузка…</p>;

  const rows = settings.data.settings.flatMap((tool) => tool.fields.map((field) => ({ tool, field })));
  if (rows.length === 0) return <p className="text-sm text-muted-foreground">У команд этой задачи нет настроек.</p>;

  // The same title from two commands: each row says whose it is.
  const titles = rows.map((row) => row.field.title);

  return (
    <div className="flex flex-col">
      {rows.map(({ tool, field }) => (
        <Row
          key={`${tool.tool}.${field.name}`}
          field={field}
          caption={titles.filter((title) => title === field.title).length > 1 ? tool.tool : undefined}
          value={field.all}
          inherit={`По умолчанию · ${shown(field.default)}`}
          onChange={(value) => change({ tool: tool.tool, field: field.name, value })}
        />
      ))}
      {error && <p className="pt-2 text-sm text-destructive">{error}</p>}
    </div>
  );
}

export function StepSettings({ taskId, step }: { taskId: string; step: string }) {
  const settings = useTaskSettings(taskId);
  const { change, error } = useSettingChange(taskId);

  const rows = (settings.data?.settings ?? []).flatMap((tool) =>
    tool.fields.flatMap((field) => field.steps.filter((item) => item.step === step).map((item) => ({ tool, field, item }))),
  );
  if (rows.length === 0) return null;

  return (
    <div className="flex flex-col">
      <div className="pt-2 text-xs text-muted-foreground">Настройки шага</div>
      {rows.map(({ tool, field, item }) => (
        <Row
          key={`${tool.tool}.${field.name}`}
          field={field}
          value={item.value}
          inherit={`Как для задачи · ${shown(field.all ?? field.default)}`}
          onChange={(value) => change({ tool: tool.tool, field: field.name, step, value })}
        />
      ))}
      {error && <p className="pt-2 text-sm text-destructive">{error}</p>}
    </div>
  );
}

function useSettingChange(taskId: string) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const change = async (item: SettingChange) => {
    setError(null);
    try {
      const updated = await api<Settings>(`/tasks/${taskId}/settings`, { method: "PATCH", json: { changes: [item] } });
      queryClient.setQueryData(keys.taskSettings(taskId), updated);
    } catch (e) {
      const errors = e instanceof ApiError ? (e.details as { errors?: string[] } | undefined)?.errors : undefined;
      setError(errors?.join("; ") ?? String(e instanceof Error ? e.message : e));
    }
  };

  return { change, error };
}

// One setting: its title and description on the left, the value on the right. `null` is
// «not set here»: the value comes from the level above, named in `inherit`.
function Row({ field, caption, value, inherit, onChange }: { field: SettingField; caption?: string; value: unknown; inherit: string; onChange: (value: unknown) => void }) {
  return (
    <div className="flex items-center gap-3 border-t py-2 first:border-t-0">
      <div className="min-w-0 flex-1">
        <div className="text-sm">{field.title}</div>
        {caption && <div className="text-xs text-muted-foreground">{caption}</div>}
        {field.description && <div className="text-xs text-muted-foreground">{field.description}</div>}
      </div>
      <Value schema={field.schema} value={value} inherit={inherit} onChange={onChange} />
    </div>
  );
}

function Value({ schema, value, inherit, onChange }: { schema: Record<string, unknown>; value: unknown; inherit: string; onChange: (value: unknown) => void }) {
  const choices = (schema.enum as unknown[] | undefined) ?? (schema.type === "boolean" ? [true, false] : undefined);

  if (choices) {
    const index = value === null || value === undefined ? "" : String(choices.findIndex((choice) => choice === value));

    return (
      <select className={`${selectClass} max-w-48`} value={index} onChange={(e) => onChange(e.target.value === "" ? null : choices[Number(e.target.value)])}>
        <option value="">{inherit}</option>
        {choices.map((choice, i) => (
          <option key={String(choice)} value={String(i)}>
            {shown(choice)}
          </option>
        ))}
      </select>
    );
  }

  return <TextValue number={schema.type === "integer" || schema.type === "number"} value={value} inherit={inherit} onChange={onChange} />;
}

// Saved when the field is left or Enter pressed; emptied — back to the level above.
function TextValue({ number, value, inherit, onChange }: { number: boolean; value: unknown; inherit: string; onChange: (value: unknown) => void }) {
  const [text, setText] = useState(value === null || value === undefined ? "" : String(value));

  useEffect(() => setText(value === null || value === undefined ? "" : String(value)), [value]);

  const commit = () => {
    const next = text.trim() === "" ? null : number ? Number(text) : text;
    if (next !== (value ?? null)) onChange(next);
  };

  return (
    <Input
      className="max-w-48"
      type={number ? "number" : "text"}
      value={text}
      placeholder={inherit}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === "Enter" && commit()}
    />
  );
}

function shown(value: unknown): string {
  if (value === true) return "вкл";
  if (value === false) return "выкл";
  if (value === null || value === undefined || value === "") return "—";

  return String(value);
}

