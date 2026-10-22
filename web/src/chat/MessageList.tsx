import { BellRing } from "lucide-react";

import type { ChatMessage, TraceItem } from "@/api/types";
import { Markdown } from "@/chat/Markdown";
import { ToolCalls } from "@/chat/ToolCalls";

export function MessageList({ messages, trace }: { messages: ChatMessage[]; trace: TraceItem[] }) {
  const calls = new Map<number, TraceItem[]>();
  for (const item of trace) {
    if (item.message_id === null) continue;
    calls.set(item.message_id, [...(calls.get(item.message_id) ?? []), item]);
  }

  return (
    <>
      {messages.map((message) => {
        // Raw tool results: their command row already shows them.
        if (message.role === "tool") return null;

        if (message.role === "user") return <UserBubble key={message.id} text={message.content} />;

        if (message.role === "notice") {
          return (
            <div key={message.id} className="flex gap-2 rounded-lg border border-dashed bg-muted/40 p-3">
              <BellRing className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0">
                <div className="text-xs text-muted-foreground">Сообщение задачи · {time(message.created_at)}</div>
                <Markdown text={message.content} />
              </div>
            </div>
          );
        }

        const items = calls.get(message.id) ?? [];
        if (!message.content && items.length === 0) return null;

        return (
          <div key={message.id} className="flex flex-col gap-2">
            <ToolCalls items={items} />
            {message.content && <Markdown text={message.content} />}
          </div>
        );
      })}
    </>
  );
}

export function UserBubble({ text }: { text: string }) {
  return (
    <div className="ml-auto max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-3 py-2 text-sm whitespace-pre-wrap text-primary-foreground">{text}</div>
  );
}

function time(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
