// Providers (a model at a server) and which one serves each role. A role changes only if
// the provider passes that role's check (docs/LLM.md).

import { useQueryClient } from "@tanstack/react-query";
import { Check, KeyRound, Plus, Trash2, X } from "lucide-react";
import { useState, type ReactNode } from "react";

import { api } from "@/api/client";
import { keys, useLlm } from "@/api/queries";
import type { CheckResult, Provider, RoleName } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Section, selectClass, useAction } from "@/settings/common";

const ROLES: { role: RoleName; title: string; hint: string }[] = [
  { role: "chat", title: "Чат", hint: "агент в чате и сборка задач; модель должна вызывать инструменты" },
  { role: "instruct", title: "Инструкции", hint: "команды llm.* и разметка шаблонов; не назначена — работает модель чата" },
  { role: "embed", title: "Эмбеддинги", hint: "отбор команд под запрос; не назначена — агент видит все команды" },
];

export function Models() {
  const llm = useLlm();
  const [adding, setAdding] = useState(false);

  if (llm.isError) return <p className="text-sm text-destructive">{llm.error.message}</p>;
  if (!llm.data) return <p className="text-sm text-muted-foreground">Загрузка…</p>;

  return (
    <div className="flex flex-col gap-8">
      <Section title="Роли" description="Какая модель что делает. Перед назначением провайдер проверяется на роль.">
        <div className="flex flex-col gap-3">
          {ROLES.map((item) => (
            <RoleRow key={item.role} {...item} providers={llm.data.providers} current={llm.data.roles[item.role]} />
          ))}
        </div>
      </Section>

      <Section title="Провайдеры" description="Провайдер — одна модель на OpenAI-совместимом сервере: адрес, ключ, имя модели и название.">
        <div className="flex flex-col gap-2">
          {llm.data.providers.map((provider) => (
            <ProviderRow key={provider.id} provider={provider} />
          ))}
          {llm.data.providers.length === 0 && <p className="text-sm text-muted-foreground">Провайдеров нет: добавьте первый.</p>}
        </div>
        {adding ? (
          <AddProvider onDone={() => setAdding(false)} />
        ) : (
          <Button variant="outline" className="self-start" onClick={() => setAdding(true)}>
            <Plus /> Добавить провайдера
          </Button>
        )}
      </Section>
    </div>
  );
}

function RoleRow({ role, title, hint, providers, current }: { role: RoleName; title: string; hint: string; providers: Provider[]; current: { provider: string; follows_chat?: boolean } | null }) {
  const queryClient = useQueryClient();
  const { busy, error, run } = useAction();
  const own = current && !current.follows_chat ? current.provider : "";
  const [choice, setChoice] = useState(own);

  const assign = () =>
    run("assign", async () => {
      await api("/llm", { method: "PATCH", json: { [role]: choice || null } });
      await queryClient.invalidateQueries({ queryKey: keys.llm });
    });

  return (
    <div className="flex flex-col gap-1.5 rounded-lg border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="w-28 font-medium">{title}</span>
        <select className={selectClass} value={choice} onChange={(e) => setChoice(e.target.value)} disabled={busy !== null}>
          <option value="">{role === "instruct" ? "как у чата" : "не назначено"}</option>
          {providers.map((provider) => (
            <option key={provider.id} value={provider.id}>
              {provider.name} · {provider.model}
            </option>
          ))}
        </select>
        <Button size="sm" onClick={assign} disabled={busy !== null || choice === own}>
          {busy ? "Проверяю…" : "Назначить"}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{hint}</p>
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  );
}

function ProviderRow({ provider }: { provider: Provider }) {
  const queryClient = useQueryClient();
  const { busy, error, run } = useAction();
  const [check, setCheck] = useState<CheckResult | null>(null);
  const [editing, setEditing] = useState(false);
  const [armed, setArmed] = useState(false);

  const test = (role: RoleName) => run("check", async () => setCheck(await api<CheckResult>(`/llm/providers/${provider.id}/check`, { method: "POST", json: { role } })));

  const remove = () =>
    run("delete", async () => {
      await api(`/llm/providers/${provider.id}`, { method: "DELETE" });
      queryClient.invalidateQueries({ queryKey: keys.llm });
    });

  return (
    <div className="flex flex-col gap-2 rounded-lg border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{provider.name}</span>
        <span className="text-sm text-muted-foreground">{provider.model}</span>
        {provider.has_key && <KeyRound className="size-3.5 text-muted-foreground" aria-label="есть ключ" />}
        {provider.roles.map((role) => (
          <Badge key={role} variant="secondary">
            {ROLES.find((r) => r.role === role)?.title}
          </Badge>
        ))}
      </div>
      <div className="truncate text-xs text-muted-foreground">{provider.base_url}</div>

      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={() => test("chat")} disabled={busy !== null}>
          Проверить как чат
        </Button>
        <Button size="sm" variant="outline" onClick={() => test("embed")} disabled={busy !== null}>
          Как эмбеддинги
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setEditing((open) => !open)}>
          Изменить
        </Button>
        <Button size="sm" variant={armed ? "destructive" : "ghost"} onClick={armed ? remove : () => setArmed(true)} onBlur={() => setArmed(false)} disabled={busy !== null}>
          <Trash2 /> {armed ? "Точно удалить?" : "Удалить"}
        </Button>
      </div>

      {busy === "check" && <p className="text-sm text-muted-foreground">Проверяю…</p>}
      {check && (
        <p className={check.ok ? "flex items-center gap-1 text-sm text-success" : "flex items-center gap-1 text-sm text-destructive"}>
          {check.ok ? <Check className="size-4" /> : <X className="size-4" />}
          {check.role === "embed" ? "Эмбеддинги" : "Чат"}: {check.ok ? (check.dimensions ? `работает, размерность ${check.dimensions}` : "работает, вызывает инструменты") : check.error}
        </p>
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}
      {editing && <EditProvider provider={provider} onDone={() => setEditing(false)} />}
    </div>
  );
}

function EditProvider({ provider, onDone }: { provider: Provider; onDone: () => void }) {
  const queryClient = useQueryClient();
  const { busy, error, run } = useAction();
  const [name, setName] = useState(provider.name);
  const [baseUrl, setBaseUrl] = useState(provider.base_url);
  const [model, setModel] = useState(provider.model);
  const [key, setKey] = useState("");
  const [dropKey, setDropKey] = useState(false);

  const save = () =>
    run("save", async () => {
      const body: Record<string, unknown> = { name, base_url: baseUrl, model };
      if (key) body.api_key = key;
      else if (dropKey) body.api_key = null;

      await api(`/llm/providers/${provider.id}`, { method: "PATCH", json: body });
      await queryClient.invalidateQueries({ queryKey: keys.llm });
      onDone();
    });

  return (
    <div className="flex flex-col gap-2 border-t pt-2">
      <Labeled title="Название">
        <Input value={name} onChange={(e) => setName(e.target.value)} />
      </Labeled>
      <Labeled title="Адрес /v1">
        <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
      </Labeled>
      <Labeled title="Модель">
        <Input value={model} onChange={(e) => setModel(e.target.value)} />
      </Labeled>
      <Labeled title={provider.has_key ? "Новый ключ (пусто — оставить прежний)" : "Ключ"}>
        <Input type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} />
      </Labeled>
      {provider.has_key && (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={dropKey} onChange={(e) => setDropKey(e.target.checked)} disabled={Boolean(key)} /> Убрать ключ
        </label>
      )}
      <div className="flex gap-2">
        <Button size="sm" onClick={save} disabled={busy !== null}>
          Сохранить
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone}>
          Отмена
        </Button>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  );
}

// Address and key → the models that server lists → one of them → a name.
function AddProvider({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient();
  const { busy, error, run } = useAction();
  const [baseUrl, setBaseUrl] = useState("");
  const [key, setKey] = useState("");
  const [models, setModels] = useState<string[] | null>(null);
  const [model, setModel] = useState("");
  const [name, setName] = useState("");

  const list = () =>
    run("models", async () => {
      const found = (await api<{ models: string[] }>("/llm/models", { method: "POST", json: { base_url: baseUrl, api_key: key || null } })).models;
      setModels(found);
      if (found.length > 0 && !model) setModel(found[0]);
    });

  const add = () =>
    run("add", async () => {
      await api("/llm/providers", { method: "POST", json: { name: name || model, base_url: baseUrl, model, api_key: key || null } });
      await queryClient.invalidateQueries({ queryKey: keys.llm });
      onDone();
    });

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-dashed p-3">
      <Labeled title="Адрес OpenAI-совместимого /v1">
        <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="http://localhost:11434/v1" />
      </Labeled>
      <Labeled title="Ключ (если сервер его требует)">
        <Input type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} />
      </Labeled>
      <Button size="sm" variant="outline" className="self-start" onClick={list} disabled={busy !== null || !baseUrl}>
        {busy === "models" ? "Спрашиваю сервер…" : "Получить список моделей"}
      </Button>

      <Labeled title="Модель">
        {models && models.length > 0 ? (
          <select className={selectClass} value={model} onChange={(e) => setModel(e.target.value)}>
            {models.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        ) : (
          <Input value={model} onChange={(e) => setModel(e.target.value)} placeholder="имя модели, если сервер не отдаёт список" />
        )}
      </Labeled>
      <Labeled title="Название">
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={model || "например, Сервер команды"} />
      </Labeled>

      <div className="flex gap-2">
        <Button size="sm" onClick={add} disabled={busy !== null || !baseUrl || !model}>
          Добавить
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone}>
          Отмена
        </Button>
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  );
}

export function Labeled({ title, children }: { title: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-muted-foreground">{title}</span>
      {children}
    </label>
  );
}
