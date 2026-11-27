"use client";

import React, { useState } from "react";
import { ConnectorLogo } from "@/components/ConnectorLogo";
import { EmptyState, FilterChips, SearchInput, btn } from "@/components/ui";
import { CATEGORY_LABELS, CONNECTORS, ConnectorCategory, ConnectorId } from "@/lib/connectors";

type CategoryFilter = ConnectorCategory | "all";

const CATEGORIES = Object.keys(CATEGORY_LABELS) as ConnectorCategory[];

const SEARCH_TEXT = new Map(
  CONNECTORS.map((c) => [c.id, [c.label, c.sublabel, c.basedOn, CATEGORY_LABELS[c.category], ...(c.keywords ?? [])].join(" ").toLowerCase()])
);

export function ConnectorCatalog({ onSelect }: { onSelect: (id: ConnectorId) => void }) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<CategoryFilter>("all");

  const q = query.trim().toLowerCase();
  const matches = q ? CONNECTORS.filter((c) => SEARCH_TEXT.get(c.id)?.includes(q)) : CONNECTORS;
  const visible = category === "all" ? matches : matches.filter((c) => c.category === category);
  const options = [
    { value: "all" as const, label: "All", count: matches.length },
    ...CATEGORIES.map((cat) => ({ value: cat, label: CATEGORY_LABELS[cat], count: matches.filter((c) => c.category === cat).length })),
  ];

  return (
    <div className="space-y-4">
      <SearchInput label="Search connectors" placeholder="Search connectors" value={query} onChange={setQuery} />
      <FilterChips label="Connector category" options={options} value={category} onChange={setCategory} />
      {visible.length === 0 ? (
        <EmptyState
          title="No connectors match"
          body="Try another word, such as a product name or a file type."
          action={
            <button
              type="button"
              className={btn.secondary}
              onClick={() => {
                setQuery("");
                setCategory("all");
              }}
            >
              Clear filters
            </button>
          }
        />
      ) : (
        <ul role="list" aria-label="Connectors" className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
          {visible.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                onClick={() => onSelect(c.id)}
                className="flex h-full w-full items-start gap-3 border border-border bg-surface p-3.5 text-left transition-colors hover:border-plum"
              >
                <ConnectorLogo id={c.id} size="md" />
                <span className="min-w-0 flex-1">
                  <span className="block text-[13.5px] font-medium leading-snug text-ink">{c.label}</span>
                  <span className="mt-0.5 block text-xs leading-snug text-secondary">{c.sublabel}</span>
                  {c.basedOn && <span className="mt-1.5 inline-block bg-panel px-1.5 py-0.5 text-[11px] text-secondary">via {c.basedOn}</span>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
