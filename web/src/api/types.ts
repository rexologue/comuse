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
