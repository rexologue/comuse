// The server state the screens share; events (later) invalidate these keys.

import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { ChatSummary, Job, TaskSummary } from "@/api/types";

export const keys = {
  health: ["health"] as const,
  tasks: ["tasks"] as const,
  chats: ["chats"] as const,
  jobs: ["jobs"] as const,
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
