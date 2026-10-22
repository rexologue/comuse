import type { RunStatus, TaskStatus, VersionStatus } from "@/api/types";

export const TASK_STATUS: Record<TaskStatus, { label: string; variant: "success" | "secondary" | "outline" }> = {
  active: { label: "работает", variant: "success" },
  paused: { label: "пауза", variant: "secondary" },
  draft: { label: "черновик", variant: "outline" },
  archived: { label: "архив", variant: "outline" },
};

export const VERSION_STATUS: Record<VersionStatus, string> = {
  draft: "черновик",
  approved: "одобрена",
  rejected: "отклонена",
  superseded: "заменена",
};

// Green: done; red: failed; grey: stopped or found nothing; blue: going.
export const RUN_STATUS: Record<RunStatus, { label: string; dot: string }> = {
  succeeded: { label: "успешно", dot: "bg-success" },
  failed: { label: "ошибка", dot: "bg-destructive" },
  cancelled: { label: "отменён", dot: "bg-muted-foreground" },
  empty: { label: "нечего делать", dot: "bg-muted-foreground" },
  queued: { label: "в очереди", dot: "bg-sky-500 animate-pulse" },
  running: { label: "идёт", dot: "bg-sky-500 animate-pulse" },
};

export const RUN_TRIGGER: Record<string, string> = {
  cron: "по расписанию",
  manual: "вручную",
  dry_run: "пробный",
};
