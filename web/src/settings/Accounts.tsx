// Accounts the commands use (mail and others): the form comes from the kind's declared
// fields, so nothing here knows about mail. The password goes to the secret store and
// never comes back.

import { useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { api } from "@/api/client";
import { keys, useAccountKinds, useAccounts } from "@/api/queries";
import type { Account, AccountKindSpec } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Section, selectClass, useAction } from "@/settings/common";
import { Labeled } from "@/settings/Models";

export function Accounts() {
  const accounts = useAccounts();
  const kinds = useAccountKinds();
  const [adding, setAdding] = useState(false);

  return (
    <Section title="Аккаунты" description="Почта и другие учётные записи, с которыми работают команды. Пароль хранится в хранилище секретов и обратно не показывается.">
      <div className="flex flex-col gap-2">
        {accounts.data?.map((account) => (
          <AccountRow key={account.id} account={account} kind={kinds.data?.[account.kind]} />
        ))}
        {accounts.data?.length === 0 && <p className="text-sm text-muted-foreground">Аккаунтов нет.</p>}
      </div>
      {adding && kinds.data ? (
        <AddAccount kinds={kinds.data} onDone={() => setAdding(false)} />
      ) : (
        <Button variant="outline" className="self-start" onClick={() => setAdding(true)}>
          <Plus /> Добавить аккаунт
        </Button>
      )}
    </Section>
  );
}

function AccountRow({ account, kind }: { account: Account; kind: AccountKindSpec | undefined }) {
  const queryClient = useQueryClient();
  const { busy, error, run } = useAction();
  const [checked, setChecked] = useState<string | null>(null);
  const [armed, setArmed] = useState(false);

  const shown = (kind?.fields ?? []).filter((field) => account.config[field.name] !== undefined && account.config[field.name] !== "").slice(0, 3);

  return (
    <div className="flex flex-col gap-2 rounded-lg border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{account.name}</span>
        <span className="text-sm text-muted-foreground">{kind?.title ?? account.kind}</span>
      </div>
      <div className="text-xs text-muted-foreground">{shown.map((field) => `${field.title}: ${String(account.config[field.name])}`).join(" · ")}</div>
      <div className="flex gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={() =>
            run("check", async () => {
              const result = await api<{ status: string }>(`/accounts/${account.name}/check`, { method: "POST" });
              setChecked(result.status === "ok" ? "Вход удался" : result.status === "unchecked" ? "Этот вид не умеет проверять вход" : result.status);
            })
          }
          disabled={busy !== null}
        >
          {busy === "check" ? "Проверяю…" : "Проверить вход"}
        </Button>
        <Button
          size="sm"
          variant={armed ? "destructive" : "ghost"}
          onBlur={() => setArmed(false)}
          onClick={
            armed
              ? () =>
                  run("delete", async () => {
                    await api(`/accounts/${account.name}`, { method: "DELETE" });
                    queryClient.invalidateQueries({ queryKey: keys.accounts });
                  })
              : () => setArmed(true)
          }
          disabled={busy !== null}
        >
          <Trash2 /> {armed ? "Точно удалить?" : "Удалить"}
        </Button>
      </div>
      {checked && <p className="text-sm text-success">{checked}</p>}
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  );
}

function AddAccount({ kinds, onDone }: { kinds: Record<string, AccountKindSpec>; onDone: () => void }) {
  const queryClient = useQueryClient();
  const { busy, error, run } = useAction();
  const [kindName, setKindName] = useState(Object.keys(kinds)[0] ?? "");
  const [preset, setPreset] = useState("");
  const [name, setName] = useState("");
  const [config, setConfig] = useState<Record<string, string>>({});
  const [secret, setSecret] = useState("");

  const kind = kinds[kindName];
  const presetValues = (preset && kind?.presets[preset]) || {};

  const save = () =>
    run("save", async () => {
      // Only what the user typed: the preset fills the rest on the server.
      const typed = Object.fromEntries(
        Object.entries(config)
          .filter(([, value]) => value !== "")
          .map(([key, value]) => [key, kind.fields.find((field) => field.name === key)?.type === "number" ? Number(value) : value]),
      );

      await api("/accounts", { method: "POST", json: { name, kind: kindName, preset: preset || null, config: typed, secret } });
      await queryClient.invalidateQueries({ queryKey: keys.accounts });
      onDone();
    });

  if (!kind) return <p className="text-sm text-muted-foreground">Ни одна группа команд не объявила видов аккаунтов.</p>;

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-dashed p-3">
      <div className="grid gap-2 sm:grid-cols-2">
        <Labeled title="Вид">
          <select className={selectClass} value={kindName} onChange={(e) => (setKindName(e.target.value), setPreset(""), setConfig({}))}>
            {Object.entries(kinds).map(([key, item]) => (
              <option key={key} value={key}>
                {item.title}
              </option>
            ))}
          </select>
        </Labeled>
        {Object.keys(kind.presets).length > 0 && (
          <Labeled title="Провайдер">
            <select className={selectClass} value={preset} onChange={(e) => setPreset(e.target.value)}>
              <option value="">свои настройки</option>
              {Object.keys(kind.presets).map((key) => (
                <option key={key} value={key}>
                  {key}
                </option>
              ))}
            </select>
          </Labeled>
        )}
      </div>

      <Labeled title="Имя аккаунта (латиница, цифры, _ и -) — по нему его называют задачи">
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="work" />
      </Labeled>

      {kind.fields.map((field) => {
        const fromPreset = presetValues[field.name];
        const title = `${field.title}${field.required ? " *" : ""}${field.hint ? ` — ${field.hint}` : ""}`;

        return (
          <Labeled key={field.name} title={title}>
            {field.type === "choice" ? (
              <select className={selectClass} value={config[field.name] ?? ""} onChange={(e) => setConfig((c) => ({ ...c, [field.name]: e.target.value }))}>
                <option value="">{fromPreset !== undefined ? `как у провайдера (${String(fromPreset)})` : "по умолчанию"}</option>
                {field.choices.map((choice) => (
                  <option key={choice} value={choice}>
                    {choice}
                  </option>
                ))}
              </select>
            ) : (
              <Input
                type={field.type === "number" ? "number" : "text"}
                value={config[field.name] ?? ""}
                onChange={(e) => setConfig((c) => ({ ...c, [field.name]: e.target.value }))}
                placeholder={fromPreset !== undefined ? String(fromPreset) : ""}
              />
            )}
          </Labeled>
        );
      })}

      <Labeled title={`${kind.secret_title} *`}>
        <Input type="password" autoComplete="new-password" value={secret} onChange={(e) => setSecret(e.target.value)} />
      </Labeled>

      <div className="flex gap-2">
        <Button size="sm" onClick={save} disabled={busy !== null || !name || !secret}>
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
