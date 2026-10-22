import { Bell, Menu, Monitor, Moon, PanelRight, Settings, Sun } from "lucide-react";
import { useEffect } from "react";
import { Link } from "react-router";

import { useHealth, useRecentEvents } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { FEED_TYPES } from "@/events/feed";
import { markSeen, useSeen } from "@/events/seen";
import { cn } from "@/lib/utils";
import { useTheme, type Theme } from "@/theme";

const THEMES: { value: Theme; label: string; icon: typeof Sun }[] = [
  { value: "system", label: "Как в системе", icon: Monitor },
  { value: "light", label: "Светлая", icon: Sun },
  { value: "dark", label: "Тёмная", icon: Moon },
];

type Props = {
  eventsOpen: boolean;
  onToggleEvents: () => void;
  // Given on a window too narrow for the side panels to stay.
  onToggleLeft?: () => void;
  onToggleRight?: () => void;
};

export function TopBar({ eventsOpen, onToggleEvents, onToggleLeft, onToggleRight }: Props) {
  const health = useHealth();
  const events = useRecentEvents();
  const seen = useSeen();

  // On the very first visit everything already there counts as read.
  const newest = events.data?.at(-1)?.id;
  useEffect(() => {
    if (seen === null && newest !== undefined) markSeen(newest);
  }, [seen, newest]);
  const unread = (events.data ?? []).filter((event) => FEED_TYPES.has(event.type) && seen !== null && event.id > seen).length;
  const { theme, setTheme } = useTheme();
  const ThemeIcon = THEMES.find((t) => t.value === theme)?.icon ?? Monitor;

  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b px-3">
      {onToggleLeft && (
        <Button variant="ghost" size="icon" onClick={onToggleLeft} aria-label="Задачи и чаты">
          <Menu />
        </Button>
      )}
      <Link to="/" className="truncate font-semibold">
        Офисный агент
      </Link>

      <Tooltip>
        <TooltipTrigger asChild>
          <span className={cn("size-2 rounded-full", health.isSuccess ? "bg-success" : health.isError ? "bg-destructive" : "bg-muted-foreground")} />
        </TooltipTrigger>
        <TooltipContent>{health.isSuccess ? "Агент на связи" : health.isError ? health.error.message : "Подключаюсь…"}</TooltipContent>
      </Tooltip>

      <div className="ml-auto flex items-center gap-1">
        {onToggleRight && (
          <Button variant="ghost" size="icon" onClick={onToggleRight} aria-label="Боковая панель">
            <PanelRight />
          </Button>
        )}
        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant={eventsOpen ? "secondary" : "ghost"} size="icon" onClick={onToggleEvents} aria-label="События" className="relative">
              <Bell />
              {unread > 0 && (
                <span className="absolute -top-0.5 -right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-medium text-white">
                  {unread > 99 ? "99+" : unread}
                </span>
              )}
            </Button>
          </TooltipTrigger>
          <TooltipContent>{unread > 0 ? `События: новых ${unread}` : "События"}</TooltipContent>
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
