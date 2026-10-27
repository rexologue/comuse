// A scenario as numbered steps; a foreach step holds its own steps indented. With
// `detail`, a step opens on click: its command, arguments and what `detail` adds (the
// step's settings in the task panel).

import { ChevronDown, ChevronRight } from "lucide-react";
import { useState, type ReactNode } from "react";

import type { Card } from "@/api/types";

type Detail = (card: Card) => ReactNode;

export function Cards({ cards, prefix = "", detail }: { cards: Card[]; prefix?: string; detail?: Detail }) {
  if (cards.length === 0) return <p className="text-sm text-muted-foreground">Шагов нет.</p>;

  return (
    <ol className="flex flex-col gap-1.5">
      {cards.map((card, index) => (
        <Step key={card.id} card={card} number={`${prefix}${index + 1}`} detail={detail} />
      ))}
    </ol>
  );
}

function Step({ card, number, detail }: { card: Card; number: string; detail?: Detail }) {
  const [open, setOpen] = useState(false);
  const opens = detail !== undefined && card.tool !== null;

  return (
    <li className="text-sm">
      <div className="flex gap-2">
        <span className="w-6 shrink-0 text-right text-muted-foreground tabular-nums">{number}.</span>
        <div className="min-w-0 flex-1">
          {opens ? (
            <button className="flex w-full items-start gap-1 text-left hover:text-foreground" onClick={() => setOpen(!open)}>
              <span className="min-w-0 flex-1">{card.text}</span>
              {open ? <ChevronDown className="mt-0.5 size-4 shrink-0 text-muted-foreground" /> : <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted-foreground" />}
            </button>
          ) : (
            <div>{card.text}</div>
          )}
          {card.tool && !open && (
            <div className="text-xs text-muted-foreground">
              {card.tool}
            </div>
          )}
          {open && (
            <div className="mt-1.5 flex flex-col gap-1.5 rounded-md border p-2">
              <div className="text-xs text-muted-foreground">
                Команда <span className="font-mono text-foreground">{card.tool}</span>
              </div>
              <Args args={card.args} />
              {detail?.(card)}
            </div>
          )}
        </div>
      </div>
      {card.children.length > 0 && (
        <div className="mt-1.5 ml-6 border-l pl-2">
          <Cards cards={card.children} prefix={`${number}.`} detail={detail} />
        </div>
      )}
    </li>
  );
}

function Args({ args }: { args: Record<string, unknown> }) {
  const entries = Object.entries(args);
  if (entries.length === 0) return <div className="text-xs text-muted-foreground">Без аргументов</div>;

  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 gap-y-0.5 text-xs">
      {entries.map(([name, value]) => (
        <div key={name} className="contents">
          <dt className="text-muted-foreground">{name}</dt>
          <dd className="font-mono break-words whitespace-pre-wrap">{typeof value === "string" ? value : JSON.stringify(value, null, 1)}</dd>
        </div>
      ))}
    </dl>
  );
}
