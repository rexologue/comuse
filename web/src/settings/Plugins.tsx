// Plugins (docs/PLUGINS.md): every tool besides the built-in ones, a group each — ours
// and the chat's (functions run(args, ctx)) or a ready server from elsewhere (a command).
// Their approved code and draft, packages, tools, whether the server runs; a draft —
// written here or by the chat — is approved here too: only a person approves. And the
// built-in commands, which only the code changes.

import { useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Pencil, Plus, RotateCw, Trash2 } from "lucide-react";
import { useState } from "react";

import { api } from "@/api/client";
import { keys, usePluginCode, usePlugins, useTools } from "@/api/queries";
import type { CodeState, Plugin, PluginCode, ToolManifest } from "@/api/types";
import { DraftDecision, ToolList } from "@/commands/DraftDecision";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { dateTime } from "@/lib/format";
import { Section, useAction } from "@/settings/common";
import { Labeled } from "@/settings/Models";

export function Plugins() {
  const plugins = usePlugins();
  const [adding, setAdding] = useState(false);

  return (
    <div className="flex flex-col gap-8">
      <Section
        title="Плагины"
        description="Всё, что умеет агент сверх встроенного: ваши команды, команды чата и готовые MCP-серверы. Плагин — группа команд (telegram.send, telegram.read) со своим кодом и пакетами. Задача помнит команды, с которыми её одобрили: после нового кода плагина она ждёт вашего решения."
      >
        <div className="flex flex-col gap-2">
          {plugins.data?.map((plugin) => <PluginCard key={plugin.name} plugin={plugin} />)}
          {plugins.data?.length === 0 && <p className="text-sm text-muted-foreground">Плагинов пока нет.</p>}
          {plugins.isError && <p className="text-sm text-destructive">{plugins.error.message}</p>}
        </div>
        {adding ? (
          <PluginForm onDone={() => setAdding(false)} />
        ) : (
          <Button variant="outline" className="self-start" onClick={() => setAdding(true)}>
            <Plus /> Добавить плагин
          </Button>
        )}
      </Section>
      <BuiltIn />
    </div>
  );
}

const STATES: { which: CodeState; label: string }[] = [
  { which: "current", label: "одобренный код" },
  { which: "draft", label: "черновик" },
];

function PluginCard({ plugin }: { plugin: Plugin }) {
  const [open, setOpen] = useState(plugin.draft !== null || plugin.error !== null);
  const fallback: CodeState = plugin.draft ? "draft" : "current";
  const [picked, setPicked] = useState<CodeState>(fallback);
  const [editing, setEditing] = useState(false);
  // A draft thrown away (or approved) is no longer there to show.
  const shown = plugin[picked] ? picked : fallback;

  return (
    <div className="flex flex-col gap-3 rounded-lg border p-3">
      <button className="flex items-center gap-2 text-left" onClick={() => setOpen(!open)}>
        {open ? <ChevronDown className="size-4 shrink-0" /> : <ChevronRight className="size-4 shrink-0" />}
        <span className="font-medium">{plugin.title ?? plugin.name}</span>
        <span className="text-sm text-muted-foreground">{plugin.name}</span>
        <span className="ml-auto flex gap-1">
          {plugin.current && (plugin.running ? <Badge variant="secondary">работает</Badge> : <Badge variant="destructive">не запущен</Badge>)}
          {plugin.draft && <Badge variant="outline">черновик</Badge>}
        </span>
      </button>

      {open && (
        <>
          {plugin.error && <p className="text-sm whitespace-pre-line text-destructive">{plugin.error}</p>}
          <div className="flex flex-wrap gap-1">
            {STATES.filter(({ which }) => plugin[which]).map(({ which, label }) => (
              <Button key={which} size="sm" variant={which === shown ? "secondary" : "ghost"} onClick={() => setPicked(which)}>
                {label}
                <span className="text-xs text-muted-foreground">{plugin[which]?.created_by === "chat" ? "чат" : "вы"}</span>
              </Button>
            ))}
          </div>
          {editing ? (
            <PluginForm
              plugin={plugin}
              base={shown}
              onDone={(saved) => {
                setEditing(false);
                if (saved) setPicked("draft");
              }}
            />
          ) : (
            <CodeDetail plugin={plugin} which={shown} onEdit={() => setEditing(true)} />
          )}
          <PluginActions plugin={plugin} />
        </>
      )}
    </div>
  );
}

function CodeDetail({ plugin, which, onEdit }: { plugin: Plugin; which: CodeState; onEdit: () => void }) {
  const detail = usePluginCode(plugin.name, which);

  if (detail.isError) return <p className="text-sm text-destructive">{detail.error.message}</p>;
  if (!detail.data) return <p className="text-sm text-muted-foreground">Загрузка…</p>;

  const data: PluginCode = detail.data;

  return (
    <div className="flex flex-col gap-3">
      {data.manifest.description && <p className="text-sm">{data.manifest.description}</p>}
      <div className="text-xs text-muted-foreground">
        {data.manifest.command ? `Свой сервер: ${data.manifest.command.join(" ")}` : "Функции run(args, ctx)"} · записан {dateTime(data.created_at)}
        {data.approved_at && ` · одобрен ${dateTime(data.approved_at)}`}
      </div>
      <ToolList code={data} />
      <div className="text-sm">
        <span className="text-muted-foreground">Пакеты: </span>
        {data.packages === null ? "подбираются…" : data.packages.length ? data.packages.join(", ") : "нет"}
      </div>

      {Object.entries(data.files).map(([path, text]) => (
        <details key={path} className="text-sm">
          <summary className="cursor-pointer text-xs text-muted-foreground">{path}</summary>
          <pre className="mt-1 max-h-96 overflow-auto rounded-md bg-muted p-2 text-xs">{text || "(пусто)"}</pre>
        </details>
      ))}

      <DraftDecision code={data}>
        <Button size="sm" variant="ghost" onClick={onEdit}>
          <Pencil /> {data.status === "draft" ? "Править черновик" : "Черновик на основе этого кода"}
        </Button>
      </DraftDecision>
    </div>
  );
}

function PluginActions({ plugin }: { plugin: Plugin }) {
  const queryClient = useQueryClient();
  const { busy, error, run } = useAction();
  const [armed, setArmed] = useState(false);

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: keys.plugins });
    await queryClient.invalidateQueries({ queryKey: keys.tools });
  };

  const restart = () => run("restart", () => api(`/plugins/${plugin.name}/restart`, { method: "POST" }).finally(refresh));
  const remove = () => run("remove", () => api(`/plugins/${plugin.name}`, { method: "DELETE" }).finally(refresh));

  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap gap-2">
        {plugin.current && (
          <Button size="sm" variant="ghost" onClick={restart} disabled={busy !== null}>
            <RotateCw /> {busy === "restart" ? "Запускаю…" : "Перезапустить"}
          </Button>
        )}
        <Button size="sm" variant={armed ? "destructive" : "ghost"} onBlur={() => setArmed(false)} onClick={armed ? remove : () => setArmed(true)} disabled={busy !== null}>
          <Trash2 /> {armed ? "Точно удалить плагин?" : "Удалить плагин"}
        </Button>
      </div>
      {error && <p className="text-sm whitespace-pre-line text-destructive">{error}</p>}
    </div>
  );
}

const NEW_MANIFEST = {
  title: "Разобрать счёт",
  description: "",
  tools: [
    {
      name: "parse",
      title: "Разобрать счёт",
      description: "Достаёт из файла счёта сумму и дату",
      card: "Разобрать «{file}»",
      confirm: false,
      entry: "main.py",
      input_schema: { type: "object", properties: { file: { type: "string", "x-path": "path", description: "Файл в рабочей папке" } }, required: ["file"] },
      output_schema: { type: "object", properties: { result: { type: "string" } } },
    },
  ],
};

const NEW_CODE = `from agent_tool import ToolError


def run(args, ctx):
    # args["file"] — полный путь к файлу; ctx.output_path("имя") — файл в результатах;
    # ctx.log("…") — строка в журнал; ctx.dry_run — пробный запуск: ничего не менять;
    # raise ToolError("…") — понятная ошибка.
    return {"result": "…"}
`;

// A new plugin, or its draft on top of `base` (its manifest, files and packages as the
// starting point). plugin.json is edited as it is: functions (`tools`) or a server of
// its own (`command`).
function PluginForm({ plugin, base, onDone }: { plugin?: Plugin; base?: CodeState; onDone: (saved?: boolean) => void }) {
  const source = usePluginCode(plugin?.name ?? "", base ?? "current", plugin !== undefined && base !== undefined);

  if (plugin && !source.data) return <p className="text-sm text-muted-foreground">Загрузка…</p>;

  return <PluginFields key={source.data?.hash ?? "new"} plugin={plugin} base={base} source={source.data} onDone={onDone} />;
}

function PluginFields({ plugin, base, source, onDone }: { plugin?: Plugin; base?: CodeState; source?: PluginCode; onDone: (saved?: boolean) => void }) {
  const queryClient = useQueryClient();
  const plugins = usePlugins();
  const { busy, error, setError, run } = useAction();

  const [name, setName] = useState(plugin?.name ?? "");
  const [manifest, setManifest] = useState(JSON.stringify(source?.manifest ?? NEW_MANIFEST, null, 2));
  const [files, setFiles] = useState<[string, string][]>(Object.entries(source?.files ?? { "main.py": NEW_CODE }));
  const [requirements, setRequirements] = useState((source?.requirements ?? []).join("\n"));

  const submit = () => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(manifest);
    } catch (e) {
      setError(`plugin.json — не JSON: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }

    const target = name.trim();
    const known = plugin ?? plugins.data?.find((item) => item.name === target);
    if (!plugin && known) {
      setError(`Плагин ${target} уже есть: откройте его и сделайте черновик`);
      return;
    }
    // A plugin has one draft, saved in place: saving here replaces whatever draft it has.
    if (known?.draft && base !== "draft" && !window.confirm("У плагина уже есть черновик. Заменить его этим?")) return;

    run("save", async () => {
      await api(`/plugins/${target}/draft`, {
        method: "PUT",
        json: {
          manifest: parsed,
          files: Object.fromEntries(files.filter(([path]) => path.trim())),
          requirements: requirements.split("\n").map((line) => line.trim()).filter(Boolean),
        },
      });
      await queryClient.invalidateQueries({ queryKey: keys.plugins });
      onDone(true);
    });
  };

  const setFile = (index: number, path: string, text: string) => setFiles(files.map((item, i) => (i === index ? [path, text] : item)));

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-dashed p-3">
      <p className="text-xs text-muted-foreground">
        {plugin ? `Сохранится как черновик ${plugin.name}: работать он начнёт после одобрения.` : "Новый плагин сохранится черновиком; работать он начнёт после одобрения."}
      </p>
      <Labeled title="Имя — группа его команд: латиница, цифры, _ (не mail, fs…)">
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="telegram" disabled={plugin !== undefined} />
      </Labeled>
      <Labeled title="plugin.json — tools (функции run) или command (свой MCP-сервер)">
        <Textarea className="font-mono text-xs" value={manifest} onChange={(e) => setManifest(e.target.value)} rows={14} spellCheck={false} />
      </Labeled>
      {files.map(([path, text], index) => (
        <div key={index} className="flex flex-col gap-1">
          <div className="flex gap-2">
            <Input className="font-mono text-xs" value={path} onChange={(e) => setFile(index, e.target.value, text)} placeholder="main.py" />
            <Button size="sm" variant="ghost" onClick={() => setFiles(files.filter((_, i) => i !== index))}>
              <Trash2 />
            </Button>
          </div>
          <Textarea className="font-mono text-xs" value={text} onChange={(e) => setFile(index, path, e.target.value)} rows={10} spellCheck={false} />
        </div>
      ))}
      <Button size="sm" variant="ghost" className="self-start" onClick={() => setFiles([...files, ["", ""]])}>
        <Plus /> Файл
      </Button>
      <Labeled title="Пакеты из PyPI, по одному в строке (например pandas>=2)">
        <Textarea className="font-mono text-xs" value={requirements} onChange={(e) => setRequirements(e.target.value)} rows={3} />
      </Labeled>
      <div className="flex gap-2">
        <Button size="sm" onClick={submit} disabled={busy !== null || !name.trim()}>
          {busy ? "Проверяю…" : "Сохранить черновик"}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => onDone()}>
          Отмена
        </Button>
      </div>
      {error && <p className="text-sm whitespace-pre-line text-destructive">{error}</p>}
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
