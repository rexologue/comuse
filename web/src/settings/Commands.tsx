// Commands the agent and tasks use: the user's own (group.action, docs/EXTERNAL_TOOLS.md) with
// their versions, code, packages and the tasks on each version, and the built-in ones.
// A draft — written here or by the chat — is approved here too; only a person approves.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Pencil, Plus } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { api } from "@/api/client";
import { keys, useExternalTools, useExternalToolVersion, useTools } from "@/api/queries";
import type { ExternalTool, ExternalToolVersion, TaskSummary, ToolManifest } from "@/api/types";
import { DraftDecision } from "@/commands/DraftDecision";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { dateTime } from "@/lib/format";
import { Section, selectClass, useAction } from "@/settings/common";
import { Labeled } from "@/settings/Models";

export function Commands() {
  const tools = useExternalTools();
  const [adding, setAdding] = useState(false);

  return (
    <div className="flex flex-col gap-8">
      <Section
        title="Свои команды"
        description="Ваши скрипты и те, что написал чат, в своих группах: telegram.send, reports.parse. Каждая версия — свой код и свои пакеты; задачи работают на той версии, с которой их одобрили."
      >
        <div className="flex flex-col gap-2">
          {tools.data?.map((tool) => <OwnCommand key={tool.name} tool={tool} />)}
          {tools.data?.length === 0 && <p className="text-sm text-muted-foreground">Своих команд пока нет.</p>}
          {tools.isError && <p className="text-sm text-destructive">{tools.error.message}</p>}
        </div>
        {adding ? (
          <CommandForm onDone={() => setAdding(false)} />
        ) : (
          <Button variant="outline" className="self-start" onClick={() => setAdding(true)}>
            <Plus /> Добавить команду
          </Button>
        )}
      </Section>
      <BuiltIn />
    </div>
  );
}

function OwnCommand({ tool }: { tool: ExternalTool }) {
  const [open, setOpen] = useState(tool.draft_version !== null);
  const fallback = tool.draft_version ?? tool.current_version ?? tool.versions.at(-1)?.version ?? 1;
  const [picked, setPicked] = useState<number>(fallback);
  const [editing, setEditing] = useState(false);
  // A draft thrown away (or swept) is no longer there to show.
  const shown = tool.versions.some((version) => version.version === picked) ? picked : fallback;

  return (
    <div className="flex flex-col gap-3 rounded-lg border p-3">
      <button className="flex items-center gap-2 text-left" onClick={() => setOpen(!open)}>
        {open ? <ChevronDown className="size-4 shrink-0" /> : <ChevronRight className="size-4 shrink-0" />}
        <span className="font-medium">{tool.title ?? tool.name}</span>
        <span className="text-sm text-muted-foreground">{tool.name}</span>
        <span className="ml-auto flex gap-1">
          {tool.current_version && <Badge variant="secondary">v{tool.current_version}</Badge>}
          {tool.draft_version && <Badge variant="outline">черновик v{tool.draft_version}</Badge>}
        </span>
      </button>

      {open && (
        <>
          <div className="flex flex-wrap gap-1">
            {tool.versions.map((version) => (
              <Button key={version.version} size="sm" variant={version.version === shown ? "secondary" : "ghost"} onClick={() => setPicked(version.version)}>
                v{version.version}
                <span className="text-xs text-muted-foreground">
                  {version.status === "draft" ? "черновик" : version.version === tool.current_version ? "текущая" : "одобрена"} ·{" "}
                  {version.created_by === "chat" ? "чат" : "вы"}
                </span>
              </Button>
            ))}
          </div>
          {editing ? (
            <CommandForm
              tool={tool}
              base={shown}
              onDone={(saved) => {
                setEditing(false);
                if (saved) setPicked(saved);
              }}
            />
          ) : (
            <VersionDetail tool={tool} version={shown} onEdit={() => setEditing(true)} />
          )}
          <TasksOn tool={tool} />
        </>
      )}
    </div>
  );
}

function VersionDetail({ tool, version, onEdit }: { tool: ExternalTool; version: number; onEdit: () => void }) {
  const detail = useExternalToolVersion(tool.name, version);

  if (detail.isError) return <p className="text-sm text-destructive">{detail.error.message}</p>;
  if (!detail.data) return <p className="text-sm text-muted-foreground">Загрузка…</p>;

  const data: ExternalToolVersion = detail.data;

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm">{data.manifest.description}</p>
      <div className="text-xs text-muted-foreground">
        Создана {dateTime(data.created_at)}
        {data.approved_at && ` · одобрена ${dateTime(data.approved_at)}`} · спрашивать перед вызовом:{" "}
        {data.manifest.confirm === "always" ? "каждый раз" : data.manifest.confirm ? "да" : "нет"}
      </div>
      <div className="text-sm">
        <span className="text-muted-foreground">Пакеты: </span>
        {data.packages.length ? data.packages.join(", ") : "нет"}
      </div>

      {Object.entries(data.files).map(([path, text]) => (
        <details key={path} open={path === data.entry} className="text-sm">
          <summary className="cursor-pointer text-xs text-muted-foreground">{path}</summary>
          <pre className="mt-1 max-h-96 overflow-auto rounded-md bg-muted p-2 text-xs">{text || "(пусто)"}</pre>
        </details>
      ))}

      <DraftDecision version={data}>
        <Button size="sm" variant="ghost" onClick={onEdit}>
          <Pencil /> {data.status === "draft" ? "Править черновик" : "Новая версия на основе этой"}
        </Button>
      </DraftDecision>
    </div>
  );
}

function TasksOn({ tool }: { tool: ExternalTool }) {
  const tasks = useQuery({
    queryKey: ["tasks", "tool", tool.name],
    queryFn: async () => (await api<{ tasks: (TaskSummary & { tool_version: number })[] }>(`/tasks?tool=${encodeURIComponent(tool.name)}`)).tasks,
  });

  if (!tasks.data?.length) return <p className="text-xs text-muted-foreground">Задач с этой командой нет.</p>;

  return (
    <div className="flex flex-col gap-1 text-sm">
      <span className="text-xs text-muted-foreground">Задачи с этой командой</span>
      {tasks.data.map((task) => (
        <Link
          key={task.id}
          to={task.source_session_id ? `/chat/${task.source_session_id}` : `/task/${task.id}`}
          className="flex items-center gap-2 rounded-md px-1 py-0.5 hover:bg-accent"
        >
          <span className="truncate">{task.title}</span>
          <Badge variant={task.tool_version === tool.current_version ? "secondary" : "outline"} className="ml-auto">
            v{task.tool_version}
            {tool.current_version && task.tool_version < tool.current_version ? ` · есть v${tool.current_version}` : ""}
          </Badge>
        </Link>
      ))}
    </div>
  );
}

const NEW_INPUT = { type: "object", properties: { file: { type: "string", "x-path": "path", description: "Файл в рабочей папке" } }, required: ["file"] };
const NEW_OUTPUT = { type: "object", properties: { result: { type: "string" } } };
const NEW_CODE = `from agent_tool import ToolError


def run(args, ctx):
    # args["file"] — полный путь к файлу; ctx.output_path("имя") — файл в результатах;
    # ctx.log("…") — строка в журнал; raise ToolError("…") — понятная ошибка.
    return {"result": "…"}
`;

// A new command, or a draft of the next version on top of `base` (its files and
// manifest as the starting point).
function CommandForm({ tool, base, onDone }: { tool?: ExternalTool; base?: number; onDone: (saved?: number) => void }) {
  const queryClient = useQueryClient();
  const tools = useExternalTools();
  const source = useExternalToolVersion(tool?.name ?? "", base ?? 0, tool !== undefined && base !== undefined);
  const { busy, error, setError, run } = useAction();

  if (tool && !source.data) return <p className="text-sm text-muted-foreground">Загрузка…</p>;

  // A command has one draft, saved in place: saving here replaces whatever draft it has.
  const replaces = (name: string) => (tool ?? tools.data?.find((known) => known.name === name))?.draft_version ?? null;

  return <CommandFields key={source.data?.hash ?? "new"} tool={tool} source={source.data} busy={busy !== null} error={error} onDone={() => onDone()} save={(name, body) => {
    if (!tool && tools.data?.some((known) => known.name === name)) {
      setError(`Команда ${name} уже есть: откройте её и сделайте новую версию`);
      return;
    }

    const draft = replaces(name);
    if (draft !== null && draft !== base && !window.confirm(`У команды уже есть черновик v${draft}. Заменить его этим?`)) return;

    run("save", async () => {
      const saved = await api<{ version: number }>(`/external-tools/${name}/draft`, { method: "PUT", json: body });
      await queryClient.invalidateQueries({ queryKey: keys.externalTools });
      await queryClient.invalidateQueries({ queryKey: ["external-tools", name] });
      onDone(saved.version);
    });
  }} />;
}

function CommandFields({
  tool,
  source,
  busy,
  error,
  onDone,
  save,
}: {
  tool?: ExternalTool;
  source?: ExternalToolVersion;
  busy: boolean;
  error: string | null;
  onDone: () => void;
  save: (name: string, body: Record<string, unknown>) => void;
}) {
  const manifest = source?.manifest as (ExternalToolVersion["manifest"] & { card?: string; timeout?: number | null }) | undefined;
  const [name, setName] = useState(tool?.name ?? "");
  const [title, setTitle] = useState(manifest?.title ?? "");
  const [description, setDescription] = useState(manifest?.description ?? "");
  const [card, setCard] = useState(manifest?.card ?? "");
  const [confirm, setConfirm] = useState(String(manifest?.confirm ?? true));
  const [timeout, setTimeoutValue] = useState(manifest?.timeout ? String(manifest.timeout) : "");
  const [inputSchema, setInputSchema] = useState(JSON.stringify(manifest?.input_schema ?? NEW_INPUT, null, 2));
  const [outputSchema, setOutputSchema] = useState(JSON.stringify(manifest?.output_schema ?? NEW_OUTPUT, null, 2));
  const entry = source?.entry ?? "main.py";
  const [code, setCode] = useState(source?.files[entry] ?? NEW_CODE);
  const [requirements, setRequirements] = useState((source?.requirements ?? []).join("\n"));
  const [problem, setProblem] = useState<string | null>(null);

  const submit = () => {
    let input: unknown;
    let output: unknown;
    try {
      input = JSON.parse(inputSchema);
      output = JSON.parse(outputSchema);
    } catch (e) {
      setProblem(`Схема — не JSON: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    setProblem(null);

    // Other files of the version stay as they were; the manifest and packages are
    // written from the fields.
    const kept = source?.files ?? {};

    save(name.trim(), {
      manifest: {
        title,
        description,
        card,
        confirm: confirm === "always" ? "always" : confirm === "true",
        timeout: timeout ? Number(timeout) : null,
        input_schema: input,
        output_schema: output,
      },
      files: { ...kept, [entry]: code },
      requirements: requirements.split("\n").map((line) => line.trim()).filter(Boolean),
    });
  };

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-dashed p-3">
      <p className="text-xs text-muted-foreground">
        {tool ? `Сохранится как черновик ${tool.name}: задачи и чат перейдут на него только после одобрения.` : "Новая команда сохранится черновиком; работать она начнёт после одобрения."}
      </p>
      <div className="grid gap-2 sm:grid-cols-2">
        <Labeled title="Имя: группа.действие латиницей, группа своя (не mail, fs…)">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="telegram.send" disabled={tool !== undefined} />
        </Labeled>
        <Labeled title="Название">
          <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Разобрать счёт" />
        </Labeled>
      </div>
      <Labeled title="Описание — по нему агент выбирает команду">
        <Textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} />
      </Labeled>
      <div className="grid gap-2 sm:grid-cols-3">
        <Labeled title="Карточка, с {аргументами}">
          <Input value={card} onChange={(e) => setCard(e.target.value)} placeholder="Разобрать «{file}»" />
        </Labeled>
        <Labeled title="Спрашивать перед вызовом">
          <select className={selectClass} value={confirm} onChange={(e) => setConfirm(e.target.value)}>
            <option value="true">да (можно разрешить на чат)</option>
            <option value="always">каждый раз</option>
            <option value="false">нет — только читает и считает</option>
          </select>
        </Labeled>
        <Labeled title="Тайм-аут, с">
          <Input type="number" min={1} value={timeout} onChange={(e) => setTimeoutValue(e.target.value)} placeholder="общий" />
        </Labeled>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <Labeled title='Схема аргументов (путь в рабочей папке — "x-path": "path")'>
          <Textarea className="font-mono text-xs" value={inputSchema} onChange={(e) => setInputSchema(e.target.value)} rows={8} />
        </Labeled>
        <Labeled title="Схема ответа">
          <Textarea className="font-mono text-xs" value={outputSchema} onChange={(e) => setOutputSchema(e.target.value)} rows={8} />
        </Labeled>
      </div>
      <Labeled title={`${entry} — def run(args, ctx) возвращает dict`}>
        <Textarea className="font-mono text-xs" value={code} onChange={(e) => setCode(e.target.value)} rows={14} spellCheck={false} />
      </Labeled>
      <Labeled title="Пакеты из PyPI, по одному в строке (например pandas>=2)">
        <Textarea className="font-mono text-xs" value={requirements} onChange={(e) => setRequirements(e.target.value)} rows={3} />
      </Labeled>
      <div className="flex gap-2">
        <Button size="sm" onClick={submit} disabled={busy || !name.trim() || !title.trim() || !description.trim()}>
          {busy ? "Проверяю…" : "Сохранить черновик"}
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone}>
          Отмена
        </Button>
      </div>
      {(problem || error) && <p className="text-sm whitespace-pre-line text-destructive">{problem ?? error}</p>}
    </div>
  );
}

function BuiltIn() {
  const tools = useTools();

  // The Map holds each tool under its name and its function name: one row per tool.
  const builtIn = [...new Map([...(tools.data?.values() ?? [])].map((tool) => [tool.name, tool])).values()].filter(
    (tool: ToolManifest) => tool.source === "internal",
  );
  const groups = new Map<string, ToolManifest[]>();
  for (const tool of builtIn) {
    const group = tool.name.split(".")[0];
    groups.set(group, [...(groups.get(group) ?? []), tool]);
  }

  return (
    <Section title="Встроенные команды" description="Команды, которые приходят с агентом. Менять их можно только в коде (internal_tools/).">
      <div className="flex flex-col gap-3">
        {[...groups.entries()].map(([group, items]) => (
          <div key={group} className="flex flex-col gap-1">
            <span className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{group}</span>
            {items.map((tool) => (
              <div key={tool.name} className="flex flex-col rounded-md px-1 py-1 text-sm">
                <div className="flex items-center gap-2">
                  <span>{tool.title}</span>
                  <span className="text-xs text-muted-foreground">{tool.name}</span>
                  {tool.confirm !== false && (
                    <Badge variant="outline" className="ml-auto">
                      {tool.confirm === "always" ? "спрашивает каждый раз" : "спрашивает"}
                    </Badge>
                  )}
                </div>
                <span className="text-xs text-muted-foreground">{tool.description}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </Section>
  );
}
