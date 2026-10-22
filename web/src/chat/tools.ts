// Names the user reads instead of tool ids.

import type { ToolManifest } from "@/api/types";

// The agent's own tools (agent_loop/meta_tools.py); traced with a `meta:` prefix.
const META: Record<string, string> = {
  task_compile: "Собрать задачу",
  task_dry_run: "Пробный запуск",
  task_reject: "Отклонить черновик",
  task_patch: "Изменить задачу",
  task_list: "Список задач",
  task_run_now: "Запустить задачу",
  task_control: "Пауза, возобновление или архив задачи",
  run_status: "Состояние запуска",
  action_confirm: "Выполнить после согласия",
};

export function toolTitle(name: string, tools: Map<string, ToolManifest> | undefined): string {
  const bare = name.replace(/^meta:/, "");

  return tools?.get(bare)?.title ?? META[bare] ?? bare.replace("__", ".");
}
