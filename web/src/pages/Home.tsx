import { MessageSquarePlus } from "lucide-react";
import { useNavigate } from "react-router";

import { Button } from "@/components/ui/button";

export function Home() {
  const navigate = useNavigate();

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 p-6 text-center">
      <div className="max-w-md space-y-2">
        <h1 className="text-xl font-semibold">Что сделать?</h1>
        <p className="text-sm text-muted-foreground">
          Объясните работу в чате: агент сделает её командами и покажет результат. Когда получится как надо, попросите собрать из этого задачу —
          дальше она будет выполняться сама, по расписанию или по кнопке.
        </p>
      </div>
      <Button onClick={() => navigate("/chat/new")}>
        <MessageSquarePlus /> Новый чат
      </Button>
    </div>
  );
}
