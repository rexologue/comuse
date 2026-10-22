import { describe, expect, it } from "vitest";

import type { ChatDetail, ChatMessage, TraceItem } from "@/api/types";
import { pendingQuestions } from "@/chat/ChatPage";
import { toolTitle } from "@/chat/tools";

const message = (id: number, role: ChatMessage["role"]): ChatMessage => ({ id, role, content: "", tool_calls: null, created_at: "" });
const call = (id: number, message_id: number, status: TraceItem["status"]): TraceItem => ({ id, message_id, tool: "fs.move", args: {}, status, result: null });

function chat(messages: ChatMessage[], trace: TraceItem[]): ChatDetail {
  return { id: "ses_1", title: "", created_at: "", messages, trace };
}

describe("pendingQuestions", () => {
  it("shows a question asked after the user's last message", () => {
    const asked = call(1, 2, "needs_approval");

    expect(pendingQuestions(chat([message(1, "user"), message(2, "assistant")], [asked]))).toEqual([asked]);
  });

  it("drops it once the user has answered", () => {
    const asked = call(1, 2, "needs_approval");

    expect(pendingQuestions(chat([message(1, "user"), message(2, "assistant"), message(3, "user")], [asked]))).toEqual([]);
  });

  it("ignores commands that ran", () => {
    expect(pendingQuestions(chat([message(1, "user"), message(2, "assistant")], [call(1, 2, "confirmed")]))).toEqual([]);
  });
});

describe("toolTitle", () => {
  it("names the agent's own tools in words and falls back to the id", () => {
    expect(toolTitle("meta:task_compile", undefined)).toBe("Собрать задачу");
    expect(toolTitle("fs__list", undefined)).toBe("fs.list");
  });
});
