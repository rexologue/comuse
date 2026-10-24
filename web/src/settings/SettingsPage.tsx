import { NavLink, useParams } from "react-router";

import { cn } from "@/lib/utils";
import { About } from "@/settings/About";
import { Accounts } from "@/settings/Accounts";
import { Commands } from "@/settings/Commands";
import { Models } from "@/settings/Models";
import { Working } from "@/settings/Working";

const TABS = [
  { id: "models", title: "Модели", page: Models },
  { id: "working", title: "Рабочие", page: Working },
  { id: "accounts", title: "Аккаунты", page: Accounts },
  { id: "commands", title: "Команды", page: Commands },
  { id: "about", title: "О программе", page: About },
];

export function SettingsPage() {
  const { section = "models" } = useParams();
  const tab = TABS.find((item) => item.id === section) ?? TABS[0];
  const Page = tab.page;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <nav className="flex h-11 shrink-0 items-center gap-1 border-b px-4">
        {TABS.map((item) => (
          <NavLink
            key={item.id}
            to={`/settings/${item.id}`}
            className={cn("rounded-md px-3 py-1 text-sm hover:bg-accent", item === tab && "bg-accent font-medium")}
          >
            {item.title}
          </NavLink>
        ))}
      </nav>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-4 py-6">
          <Page />
        </div>
      </div>
    </div>
  );
}
