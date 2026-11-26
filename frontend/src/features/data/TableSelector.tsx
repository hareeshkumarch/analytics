"use client";

import React, { useState } from "react";
import { SearchInput, btn } from "@/components/ui";
import type { DiscoveredTable } from "@/lib/api/connectors";

export function tableMeta(t: DiscoveredTable): string {
  const cols = `${t.columns.length} column${t.columns.length === 1 ? "" : "s"}`;
  return t.estimated_rows == null ? cols : `${cols} · about ${t.estimated_rows.toLocaleString()} rows`;
}

export function TableSelector({
  tables,
  selected,
  onChange,
  disabled,
}: {
  tables: DiscoveredTable[];
  selected: string[];
  onChange: (names: string[]) => void;
  disabled: boolean;
}) {
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const visible = q ? tables.filter((t) => t.name.toLowerCase().includes(q)) : tables;
  const chosen = new Set(selected);
  const hiddenSelected = q ? selected.filter((n) => !visible.some((v) => v.name === n)).length : 0;

  function setVisible(on: boolean) {
    const names = new Set(visible.map((t) => t.name));
    onChange(on ? Array.from(new Set([...selected, ...names])) : selected.filter((n) => !names.has(n)));
  }

  return (
    <fieldset disabled={disabled} className="min-w-0 space-y-3">
      <legend className="sr-only">Tables to import</legend>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[13px] text-body">
          {selected.length} of {tables.length} table{tables.length === 1 ? "" : "s"} selected
          {hiddenSelected > 0 && <span className="text-secondary"> ({hiddenSelected} outside filter)</span>}
        </p>
        <div className="flex gap-1">
          <button type="button" className={btn.ghost} onClick={() => setVisible(true)}>
            {q ? "Select matches" : "Select all"}
          </button>
          <button type="button" className={btn.ghost} onClick={() => setVisible(false)}>
            {q ? "Clear matches" : "Clear"}
          </button>
        </div>
      </div>
      {tables.length > 8 && <SearchInput label="Search tables" placeholder="Search tables" value={query} onChange={setQuery} />}
      <ul className="max-h-[360px] divide-y divide-border overflow-y-auto border border-border">
        {visible.map((t) => (
          <li key={t.name}>
            <label className="flex min-h-[44px] cursor-pointer items-center gap-3 px-3.5 py-2">
              <input
                type="checkbox"
                checked={chosen.has(t.name)}
                onChange={(e) => onChange(e.target.checked ? [...selected, t.name] : selected.filter((n) => n !== t.name))}
                className="h-4 w-4 shrink-0 accent-plum"
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-mono text-[12.5px] text-ink">{t.name}</span>
                <span className="block text-xs text-secondary">{tableMeta(t)}</span>
              </span>
            </label>
          </li>
        ))}
        {visible.length === 0 && <li className="px-3.5 py-3 text-[13px] text-secondary">No tables match.</li>}
      </ul>
    </fieldset>
  );
}
