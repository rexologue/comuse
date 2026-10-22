// The agent asked before a command that changes something (docs/CHAT.md): the answer is an
// ordinary message, these buttons only type it. "Don't ask again" exists only for
// commands that allow it (confirm: true); mail.send asks every time.

import { CircleHelp } from "lucide-react";

import { useTools } from "@/api/queries";
import type { TraceItem } from "@/api/types";
import { toolTitle } from "@/chat/tools";
import { Button } from "@/components/ui/button";

export const ANSWERS = {
  yes: "Да",
  always: "Да, и больше не спрашивай об этом в этом чате",
  no: "Нет",
};

export function ConfirmBar({ items, disabled, onAnswer }: { items: TraceItem[]; disabled: boolean; onAnswer: (text: string) => void }) {
  const tools = useTools();

  const canRemember = items.every((item) => tools.data?.get(item.tool)?.confirm === true);

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-amber-500/40 bg-amber-500/5 p-3">
      <div className="flex items-center gap-2 text-sm font-medium">
        <CircleHelp className="size-4 text-amber-500" />
        Агент ждёт разрешения: {items.map((item) => toolTitle(item.tool, tools.data)).join(", ")}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={() => onAnswer(ANSWERS.yes)} disabled={disabled}>
          Да
        </Button>
        {canRemember && (
          <Button size="sm" variant="secondary" onClick={() => onAnswer(ANSWERS.always)} disabled={disabled}>
            Да, больше не спрашивать в этом чате
          </Button>
        )}
        <Button size="sm" variant="outline" onClick={() => onAnswer(ANSWERS.no)} disabled={disabled}>
          Нет
        </Button>
      </div>
    </div>
  );
}
