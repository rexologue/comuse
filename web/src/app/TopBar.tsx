import { Bell, Monitor, Moon, Settings, Sun } from "lucide-react";
import { Link } from "react-router";

import { useHealth } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { useTheme, type Theme } from "@/theme";

const THEMES: { value: Theme; label: string; icon: typeof Sun }[] = [
  { value: "system", label: "Как в системе", icon: Monitor },
  { value: "light", label: "Светлая", icon: Sun },
  { value: "dark", label: "Тёмная", icon: Moon },
];

export function TopBar({ eventsOpen, onToggleEvents }: { eventsOpen: boolean; onToggleEvents: () => void }) {
  const health = useHealth();
  const { theme, setTheme } = useTheme();
  const ThemeIcon = THEMES.find((t) => t.value === theme)?.icon ?? Monitor;

  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b px-3">
      <Link to="/" className="font-semibold">
        Офисный агент
      </Link>

      <Tooltip>
        <TooltipTrigger asChild>
          <span className={cn("size-2 rounded-full", health.isSuccess ? "bg-success" : health.isError ? "bg-destructive" : "bg-muted-foreground")} />
        </TooltipTrigger>
        <TooltipContent>{health.isSuccess ? "Агент на связи" : health.isError ? health.error.message : "Подключаюсь…"}</TooltipContent>
      </Tooltip>

      <div className="ml-auto flex items-center gap-1">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant={eventsOpen ? "secondary" : "ghost"} size="icon" onClick={onToggleEvents} aria-label="События">
              <Bell />
            </Button>
          </TooltipTrigger>
          <TooltipContent>События</TooltipContent>
        </Tooltip>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" aria-label="Тема">
              <ThemeIcon />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {THEMES.map(({ value, label, icon: Icon }) => (
              <DropdownMenuItem key={value} onSelect={() => setTheme(value)}>
                <Icon /> {label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>

        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon" asChild aria-label="Настройки">
              <Link to="/settings">
                <Settings />
              </Link>
            </Button>
          </TooltipTrigger>
          <TooltipContent>Настройки</TooltipContent>
        </Tooltip>
      </div>
    </header>
  );
}
