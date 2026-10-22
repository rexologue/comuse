// Shown when main cannot be reached or refuses the token. Through the dev server the
// proxy knows both; a browser on its own needs main's address and the token from
// DATA_DIR/api_token (and main's ALLOWED_ORIGINS must list this page's origin).

import { useState } from "react";

import { ApiError, currentConnection, setConnection } from "@/api/client";
import { saveConnection, type Connection } from "@/api/connection";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

export function ConnectionScreen({ error, onConnected }: { error: ApiError; onConnected: () => void }) {
  const saved = currentConnection();
  const [url, setUrl] = useState(saved.kind === "direct" ? saved.url : "http://127.0.0.1:8000");
  const [token, setToken] = useState(saved.kind === "direct" ? saved.token : "");

  const connect = (next: Connection) => {
    saveConnection(next);
    setConnection(next);
    onConnected();
  };

  return (
    <div className="flex flex-1 items-center justify-center p-4">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>Нет связи с агентом</CardTitle>
          <CardDescription>
            {error.status === 401 ? "Агент отклонил токен." : error.message}. Через dev-сервер адрес и токен подставляются сами; без него укажите их здесь.
          </CardDescription>
        </CardHeader>

        <CardContent className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm">
            Адрес main
            <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://127.0.0.1:8000" />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            Токен (из DATA_DIR/api_token или API_TOKEN)
            <Input value={token} onChange={(e) => setToken(e.target.value)} type="password" autoComplete="off" />
          </label>
        </CardContent>

        <CardFooter className="justify-between gap-2">
          <Button variant="ghost" onClick={() => connect({ kind: "proxy" })}>
            Через dev-сервер
          </Button>
          <Button onClick={() => connect({ kind: "direct", url, token })} disabled={!url || !token}>
            Подключиться
          </Button>
        </CardFooter>
      </Card>
    </div>
  );
}
