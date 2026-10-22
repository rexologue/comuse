// The server state the screens share; events (later) invalidate these keys.

import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { ChatDetail, ChatSummary, Job, TaskSummary, ToolManifest } from "@/api/types";

export const keys = {
  health: ["health"] as const,
  tasks: ["tasks"] as const,
  chats: ["chats"] as const,
  jobs: ["jobs"] as const,
  tools: ["tools"] as const,
  chat: (id: string) => ["chat", id] as const,
};

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
