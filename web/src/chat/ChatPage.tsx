// One chat: its history, the turn in progress (with the commands the agent is calling,
// live from the events) and the composer. "new" is a chat not started yet: the first
// answer brings its session_id.

import { useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, RotateCcw, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router";

import { api, ApiError } from "@/api/client";
import { useEvents } from "@/api/events";
import { keys, useChat, useChats, useTools } from "@/api/queries";
import type { ChatDetail, ChatReply, TraceItem } from "@/api/types";
import { ConfirmBar } from "@/chat/ConfirmBar";
import { Composer } from "@/chat/Composer";
import { DraftCards } from "@/chat/DraftCard";
import { ToolDraftCards } from "@/chat/ToolDraftCard";
import { MessageList, UserBubble } from "@/chat/MessageList";
import { toolTitle } from "@/chat/tools";
import { Button } from "@/components/ui/button";

type Live = { tool: string; status: string };

export function ChatPage() {
  const { sessionId } = useParams();
  const isNew = sessionId === "new";

  const chat = useChat(isNew ? undefined : sessionId);
  const chats = useChats();
  const tools = useTools();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const [sending, setSending] = useState<string | null>(null);
  const [live, setLive] = useState<Live[]>([]);
  // The message that failed to go, to send again as it was.
  const [error, setError] = useState<{ text: string; message: string } | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  // A turn this page did not start (another tab, before a reload) still runs: wait for it.
  const elsewhere = sending === null && Boolean(chat.data?.busy);
  const working = sending !== null || elsewhere;

  useEffect(() => {
    if (!elsewhere) setLive([]);
  }, [elsewhere]);

  // A new chat has no id until the first answer: then any session's progress is ours.
  useEvents((event) => {
    if (event.type !== "agent.tool" || !working) return;
    if (!isNew && event.payload.session_id !== sessionId) return;

    setLive((items) => [...items, { tool: String(event.payload.tool), status: String(event.payload.status) }]);
  });

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [chat.data, sending, live.length]);

  // Another chat opened: forget this one's turn state.
  useEffect(() => {
    setError(null);
    setLive([]);
  }, [sessionId]);

  const send = async (text: string) => {
    setSending(text);
    setLive([]);
    setError(null);

    try {
      const reply = await api<ChatReply>("/agent/v1/chat/completions", {
        method: "POST",
        json: { messages: [{ role: "user", content: text }], ...(isNew ? {} : { session_id: sessionId }) },
      });

      await queryClient.invalidateQueries({ queryKey: keys.chat(reply.session_id) });
      queryClient.invalidateQueries({ queryKey: keys.chats });
      queryClient.invalidateQueries({ queryKey: ["approvals"] });

      if (isNew) navigate(`/chat/${reply.session_id}`, { replace: true });
    } catch (e) {
      setError({ text, message: failure(e) });
    } finally {
      setSending(null);
      setLive([]);
    }
  };

  const summary = chats.data?.find((item) => item.id === sessionId);
  const waiting = !working && chat.data ? pendingQuestions(chat.data) : [];

  if (chat.isError) {
    return <p className="p-6 text-sm text-muted-foreground">{chat.error instanceof ApiError && chat.error.status === 404 ? "Такого чата нет: возможно, он удалён." : chat.error.message}</p>;
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="flex h-11 shrink-0 items-center gap-2 border-b px-4">
        <h1 className="truncate text-sm font-medium">{isNew ? "Новый чат" : chat.data?.title || "Чат"}</h1>
        {summary?.task_id && <span className="text-xs text-muted-foreground">· чат задачи</span>}
        {summary && summary.task_id === null && <DeleteChat sessionId={summary.id} />}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-6">
          {isNew && sending === null && <p className="text-center text-sm text-muted-foreground">Опишите, что нужно сделать: с файлами, документами, таблицами или почтой.</p>}
          {chat.data && <MessageList messages={chat.data.messages} trace={chat.data.trace} />}
          {!isNew && sessionId && !working && <DraftCards sessionId={sessionId} />}
          {!isNew && sessionId && !working && <ToolDraftCards sessionId={sessionId} />}

          {sending !== null && (
            <>
              <UserBubble text={sending} />
              <div className="flex flex-col gap-1 text-sm text-muted-foreground">
                <div className="flex items-center gap-2">
                  <LoaderCircle className="size-4 animate-spin" /> Агент работает…
                </div>
                {live.map((item, index) => (
                  <div key={index} className="pl-6 text-xs">
                    {toolTitle(item.tool, tools.data)}
                    {item.status === "needs_approval" ? " — спросит разрешения" : item.status === "error" ? " — ошибка" : ""}
                  </div>
                ))}
              </div>
            </>
          )}

          {elsewhere && (
            <div className="flex flex-col gap-1 text-sm text-muted-foreground">
              <div className="flex items-center gap-2">
                <LoaderCircle className="size-4 animate-spin" /> Агент ещё отвечает на последнее сообщение…
              </div>
              {live.map((item, index) => (
                <div key={index} className="pl-6 text-xs">
                  {toolTitle(item.tool, tools.data)}
                </div>
              ))}
            </div>
          )}

          {error && (
            <div className="flex flex-col gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
              <p className="text-destructive">{error.message}</p>
              <p className="line-clamp-2 text-muted-foreground">Не отправлено: «{error.text}»</p>
              <Button size="sm" variant="outline" className="self-start" onClick={() => send(error.text)}>
                <RotateCcw /> Повторить
              </Button>
            </div>
          )}
          <div ref={bottom} />
        </div>
      </div>

      <div className="mx-auto flex w-full max-w-3xl flex-col gap-2 px-4 pb-4">
        {waiting.length > 0 && <ConfirmBar items={waiting} disabled={working} onAnswer={send} />}
        <Composer disabled={working} onSend={send} />
      </div>
    </div>
  );
}

// Commands waiting for the user's yes: asked in the latest answer, nothing written since.
export function pendingQuestions(chat: ChatDetail): TraceItem[] {
  const lastUser = [...chat.messages].reverse().find((message) => message.role === "user");

  return chat.trace.filter((item) => item.status === "needs_approval" && item.message_id !== null && (!lastUser || item.message_id > lastUser.id));
}

function DeleteChat({ sessionId }: { sessionId: string }) {
  const [armed, setArmed] = useState(false);
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  useEffect(() => {
    if (!armed) return;
    const timer = setTimeout(() => setArmed(false), 3000);
    return () => clearTimeout(timer);
  }, [armed]);

  const remove = async () => {
    await api(`/agent/sessions/${sessionId}`, { method: "DELETE" });
    queryClient.invalidateQueries({ queryKey: keys.chats });
    navigate("/", { replace: true });
  };

  return (
    <Button variant={armed ? "destructive" : "ghost"} size="sm" className="ml-auto" onClick={armed ? remove : () => setArmed(true)}>
      <Trash2 /> {armed ? "Точно удалить?" : "Удалить чат"}
    </Button>
  );
}

function failure(e: unknown): string {
  if (!(e instanceof ApiError)) return String(e instanceof Error ? e.message : e);

  if (e.status === 503) return `Агент ещё запускается или модель не подключена: ${e.message}`;
  if (e.status === 0 || e.status >= 500) return `Агент не ответил: ${e.message}`;

  return e.message;
}
