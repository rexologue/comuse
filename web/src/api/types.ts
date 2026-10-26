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
  // The trigger in words: «по будням в 09:00», «только вручную».
  schedule: string | null;
  cards: Card[];
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

// busy: a turn runs in the chat now (maybe sent from another tab, or before a reload).
export type ChatDetail = { id: string; title: string; created_at: string; messages: ChatMessage[]; trace: TraceItem[]; busy: boolean };

export type ToolManifest = {
  name: string;
  // internal: comes with the agent; external: an own command (group.action, versioned).
  source: "internal" | "external";
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

// A step as the user reads it (compiler/cards.py); foreach steps have children.
export type Card = {
  id: string;
  tool: string | null;
  // The version of an own command the step runs.
  version?: number | null;
  text: string;
  args: Record<string, unknown>;
  confirm?: boolean | "always";
  children: Card[];
};

export type VersionStatus = "draft" | "approved" | "rejected" | "superseded";

export type VersionItem = { id: string; version_no: number; status: VersionStatus; created_by: string; summary: string; created_at: string };

export type Version = VersionItem & {
  task_id: string;
  spec: Record<string, unknown>;
  cards: Card[];
  schedule: string | null;
  session_id: string | null;
  parent_version_id: string | null;
  approved_at: string | null;
};

// An external tool with a newer approved version than the task runs.
export type ToolUpdate = { tool: string; version: number; current: number };

export type TaskDetail = TaskSummary & { current_version: Version | null; versions: VersionItem[]; tool_updates: ToolUpdate[] };

export type Change = { kind: "added" | "removed" | "changed" | "trigger" | "title"; step?: string; before?: string; after?: string };

export type Diff = { changes: Change[]; lines: string[] };

export type RunStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled" | "empty";

export type Run = {
  id: string;
  task_id: string;
  task_version_id: string;
  trigger: string;
  status: RunStatus;
  dry_run: boolean;
  result: unknown;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
};

// One record of a run's log (docs/RUNS.md#журнал-запуска): start, step, notify, end.
export type LogEntry = {
  seq: number;
  at: string;
  kind: "start" | "step" | "notify" | "end";
  step_key: string | null;
  [field: string]: unknown;
};

// GET /tasks/{id}/settings (docs/TASKS.md#настройки): per tool its fields, each with the
// default, the task's value (null — not changed) and every step that runs the tool.
export type StepSetting = { step: string; card: string; value: unknown; effective: unknown };
export type SettingField = {
  name: string;
  title: string;
  description: string;
  schema: Record<string, unknown>;
  default: unknown;
  all: unknown;
  steps: StepSetting[];
};
export type ToolSettings = { group: string; tool: string; tool_title: string; fields: SettingField[] };
export type TaskSettings = { task_id: string; settings: ToolSettings[] };
// PATCH /tasks/{id}/settings: no step — for the whole task; value null takes it back.
export type SettingChange = { tool: string; field: string; step?: string; value: unknown };

// GET /approvals: a draft version waiting for the user (the draft is the approval).
export type Approval = {
  task_id: string;
  version_id: string;
  session_id: string | null;
  title: string;
  summary: string;
  cards: Card[];
  created_at: string;
};

// POST /compiler/patch: the new draft with what changed.
export type PatchResult = { task_id: string; version_id: string; title: string; summary: string; diff: string[]; cards: Card[] };

// GET /llm (docs/LLM.md).
export type RoleName = "chat" | "instruct" | "embed";

export type Provider = { id: string; name: string; base_url: string; model: string; has_key: boolean; roles: RoleName[]; created_at: string; updated_at: string };

export type RoleInfo = { role: RoleName; title: string; hint: string };

export type LlmOverview = {
  roles: Record<RoleName, { provider: string; name: string; model: string; follows_chat?: boolean } | null>;
  // What each role does, in the order to show them (common/llm.py ROLE_INFO).
  role_info: RoleInfo[];
  providers: Provider[];
};

export type CheckResult = { role: RoleName; model: string; ok: boolean; error?: string; dimensions?: number };

// GET /settings (docs/SETTINGS.md).
export type Tunable = {
  key: string;
  value: unknown;
  default: unknown;
  source: "env" | "db" | "default";
  locked: boolean;
  type: "int" | "float" | "bool" | "choice" | "dir";
  min: number | null;
  max: number | null;
  choices: string[] | null;
  description: string;
};

// GET /external-tools (docs/EXTERNAL_TOOLS.md).
export type ExternalToolVersionItem = {
  version: number;
  status: "draft" | "approved";
  title: string | null;
  created_by: "user" | "chat";
  session_id: string | null;
  created_at: string;
  approved_at: string | null;
};

export type ExternalTool = {
  // group.action, like a built-in command (telegram.send)
  name: string;
  title: string | null;
  current_version: number | null;
  draft_version: number | null;
  versions: ExternalToolVersionItem[];
};

export type ExternalToolVersion = ExternalToolVersionItem & {
  name: string;
  current: boolean;
  manifest: { title: string; description: string; confirm: boolean | "always"; input_schema: unknown; output_schema: unknown };
  packages: string[];
  hash: string;
  // The code (the version's bookkeeping files aside); `entry` holds def run(args, ctx).
  entry: string;
  files: Record<string, string>;
  requirements: string[];
};


// GET /accounts, /accounts/kinds (docs/TOOLS.md#виды-аккаунтов).
export type Account = { id: string; name: string; kind: string; config: Record<string, unknown> };

export type AccountFieldSpec = {
  name: string;
  title: string;
  type: "text" | "number" | "choice";
  required: boolean;
  choices: string[];
  hint: string;
  // What an empty value means ("ssl"), or "".
  default: string;
};

export type AccountKindSpec = { title: string; fields: AccountFieldSpec[]; secret_title: string; presets: Record<string, Record<string, unknown>> };

export type ServiceStatus = Record<string, { url: string | null; last_seen: string | null }>;
