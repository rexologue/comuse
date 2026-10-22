import { SendHorizontal } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

// Enter sends, Shift+Enter breaks the line.
export function Composer({ disabled, onSend, initial = "" }: { disabled: boolean; onSend: (text: string) => void; initial?: string }) {
  const [text, setText] = useState(initial);

  const send = () => {
    const value = text.trim();
    if (!value || disabled) return;

    onSend(value);
    setText("");
  };

  return (
    <div className="flex items-end gap-2">
      <Textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            send();
          }
        }}
        placeholder="Что сделать? Enter — отправить, Shift+Enter — новая строка"
        className="max-h-48 min-h-10 resize-none"
        rows={1}
      />
      <Button size="icon" onClick={send} disabled={disabled || !text.trim()} aria-label="Отправить">
        <SendHorizontal />
      </Button>
    </div>
  );
}
