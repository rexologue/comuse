// A scenario as numbered steps; a foreach step holds its own steps indented.

import type { Card } from "@/api/types";

export function Cards({ cards, prefix = "" }: { cards: Card[]; prefix?: string }) {
  if (cards.length === 0) return <p className="text-sm text-muted-foreground">Шагов нет.</p>;

  return (
    <ol className="flex flex-col gap-1.5">
      {cards.map((card, index) => {
        const number = `${prefix}${index + 1}`;

        return (
          <li key={card.id} className="text-sm">
            <div className="flex gap-2">
              <span className="w-6 shrink-0 text-right text-muted-foreground tabular-nums">{number}.</span>
              <div className="min-w-0">
                <div>{card.text}</div>
                {card.tool && <div className="text-xs text-muted-foreground">{card.tool}</div>}
              </div>
            </div>
            {card.children.length > 0 && (
              <div className="mt-1.5 ml-6 border-l pl-2">
                <Cards cards={card.children} prefix={`${number}.`} />
              </div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
