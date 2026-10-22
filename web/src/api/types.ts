// What main answers (docs/API.md). Only the fields the interface uses.

export type TaskStatus = "draft" | "active" | "paused" | "archived";

export type TaskSummary = {
  id: string;
  title: string;
  description: string;
  status: TaskStatus;
  source_session_id: string | null;
  current_version_id: string | null;
  trigger: { type: "cron" | "manual"; expr?: string } | null;
  created_at: string;
  updated_at: string | null;
};

export type ChatSummary = {
  id: string;
  title: string;
  updated_at: string;
  // The task this chat belongs to; null: a temporary chat, deleted after chat_idle_hours.
  task_id: string | null;
};

export type Job = { task_id: string; expr: string | null; next_run_time: string | null };

// GET /events: {id, type, payload, created_at} (docs/API.md#события).
export type AgentEvent = { id: number; type: string; payload: Record<string, unknown>; created_at: string };

export type ChatRole = "user" | "assistant" | "tool" | "notice";

export type ToolCall = { id: string; function: { name: string; arguments: string } };

export type ChatMessage = {
  id: number;
  role: ChatRole;
  content: string;
  tool_calls: ToolCall[] | null;
  created_at: string;
};

// ok | error | needs_approval (waits for the user) | confirmed (ran after their yes)
export type TraceStatus = "ok" | "error" | "needs_approval" | "confirmed";

export type TraceItem = {
  id: number;
  message_id: number | null;
  tool: string;
  args: Record<string, unknown>;
  status: TraceStatus;
  result: unknown;
};

export type ChatDetail = { id: string; title: string; created_at: string; messages: ChatMessage[]; trace: TraceItem[] };

export type ToolManifest = {
  name: string;
  function_name: string;
  title: string;
  description: string;
  // true: asks once per chat unless allowed for the whole chat; "always": asks every time; false: never asks.
  confirm: boolean | "always";
};

// POST /agent/v1/chat/completions (non-streaming), the fields the interface reads.
export type ChatReply = {
  session_id: string;
  choices: { message: { role: "assistant"; content: string } }[];
  tool_events: { tool: string; status: string }[];
};
