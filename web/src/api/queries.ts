// The server state the screens share; events (later) invalidate these keys.

import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import type {
  Approval,
  ChatDetail,
  ChatSummary,
  Diff,
  Job,
  LogEntry,
  Run,
  TaskDetail,
  TaskSettings,
  TaskSummary,
  ToolManifest,
  Version,
} from "@/api/types";

export const keys = {
  health: ["health"] as const,
  tasks: ["tasks"] as const,
  chats: ["chats"] as const,
  jobs: ["jobs"] as const,
  tools: ["tools"] as const,
  chat: (id: string) => ["chat", id] as const,
  task: (id: string) => ["task", id] as const,
  version: (taskId: string, id: string) => ["task", taskId, "version", id] as const,
  diff: (taskId: string, id: string) => ["task", taskId, "diff", id] as const,
  taskSettings: (id: string) => ["task", id, "settings"] as const,
  runs: (taskId: string, empty: boolean) => ["runs", taskId, empty] as const,
  run: (id: string) => ["run", id] as const,
  runLog: (id: string) => ["run", id, "log"] as const,
  approvals: (sessionId?: string) => ["approvals", sessionId ?? "all"] as const,
};

// A run still going is polled: its steps land in the log as they finish.
const LIVE = new Set(["queued", "running"]);
const LIVE_MS = 1500;

export function useHealth() {
  return useQuery({
    queryKey: keys.health,
    // /settings, not /health: it also proves the token works.
    queryFn: () => api<unknown>("/settings"),
    refetchInterval: 15_000,
    retry: false,
  });
}

export function useTasks() {
  return useQuery({ queryKey: keys.tasks, queryFn: async () => (await api<{ tasks: TaskSummary[] }>("/tasks")).tasks });
}

export function useChats() {
  return useQuery({ queryKey: keys.chats, queryFn: async () => (await api<{ sessions: ChatSummary[] }>("/agent/sessions")).sessions });
}

export function useJobs() {
  return useQuery({ queryKey: keys.jobs, queryFn: async () => (await api<{ jobs: Job[] }>("/jobs")).jobs });
}

export function useChat(id: string | undefined) {
  return useQuery({
    queryKey: keys.chat(id ?? ""),
    queryFn: () => api<ChatDetail>(`/agent/sessions/${id}`),
    enabled: Boolean(id),
  });
}

// The catalog changes only with the code: titles and `confirm` of every command.
export function useTools() {
  return useQuery({
    queryKey: keys.tools,
    queryFn: async () => {
      const tools = (await api<{ tools: ToolManifest[] }>("/tools")).tools;
      return new Map(tools.flatMap((tool) => [[tool.name, tool] as const, [tool.function_name, tool] as const]));
    },
    staleTime: Infinity,
  });
}

export function useTask(id: string | null | undefined) {
  return useQuery({ queryKey: keys.task(id ?? ""), queryFn: () => api<TaskDetail>(`/tasks/${id}`), enabled: Boolean(id) });
}

export function useVersion(taskId: string | undefined, id: string | null | undefined) {
  return useQuery({
    queryKey: keys.version(taskId ?? "", id ?? ""),
    queryFn: () => api<Version>(`/tasks/${taskId}/versions/${id}`),
    enabled: Boolean(taskId && id),
  });
}

export function useDiff(taskId: string, id: string, enabled: boolean) {
  return useQuery({ queryKey: keys.diff(taskId, id), queryFn: () => api<Diff>(`/tasks/${taskId}/versions/${id}/diff`), enabled });
}

export function useTaskSettings(id: string) {
  return useQuery({ queryKey: keys.taskSettings(id), queryFn: () => api<TaskSettings>(`/tasks/${id}/settings`) });
}

export function useTaskRuns(taskId: string, includeEmpty: boolean) {
  return useQuery({
    queryKey: keys.runs(taskId, includeEmpty),
    queryFn: async () => (await api<{ runs: Run[] }>(`/tasks/${taskId}/runs?include_empty=${includeEmpty}`)).runs,
    refetchInterval: (query) => (query.state.data?.some((run) => LIVE.has(run.status)) ? LIVE_MS : false),
  });
}

export function useRun(id: string) {
  return useQuery({
    queryKey: keys.run(id),
    queryFn: () => api<Run>(`/runs/${id}`),
    refetchInterval: (query) => (query.state.data && LIVE.has(query.state.data.status) ? LIVE_MS : false),
  });
}

export function useRunLog(id: string, live: boolean) {
  return useQuery({
    queryKey: keys.runLog(id),
    queryFn: async () => (await api<{ log: LogEntry[] }>(`/runs/${id}/log`)).log,
    refetchInterval: live ? LIVE_MS : false,
  });
}

export function useApprovals(sessionId?: string) {
  return useQuery({
    queryKey: keys.approvals(sessionId),
    queryFn: async () => (await api<{ approvals: Approval[] }>(`/approvals?status=pending${sessionId ? `&session_id=${sessionId}` : ""}`)).approvals,
  });
}
