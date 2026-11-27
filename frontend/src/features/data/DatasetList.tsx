"use client";

import React, { useState } from "react";
import { ConnectorLogo, logoIdForFamily } from "@/components/ConnectorLogo";
import { Badge, EmptyState, FilterChips, Notice, SearchInput, Skeleton, btn, cx } from "@/components/ui";
import { errorMessage } from "@/lib/api/client";
import { familyLabel } from "@/lib/connectors";
import { SnapshotSummary, datasetLabel, datasetTables, relativeTime } from "@/lib/workspace";

type StatusFilter = "all" | "draft" | "approved";

export function DatasetList({
  snapshots,
  loading,
  error,
  onRetry,
  selectedId,
  onSelect,
}: {
  snapshots: SnapshotSummary[];
  loading: boolean;
  error: unknown;
  onRetry: () => void;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");

  if (loading) {
    return (
      <div className="space-y-2" aria-hidden="true">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-14" />
        ))}
      </div>
    );
  }
  if (error) {
    return (
      <Notice
        tone="err"
        title="Couldn't load datasets"
        action={
          <button type="button" className={btn.secondary} onClick={onRetry}>
            Try again
          </button>
        }
      >
        {errorMessage(error)}
      </Notice>
    );
  }
  if (!snapshots.length) {
    return <EmptyState title="No datasets yet" body="Pick a source under Add data. Each import becomes a read-only snapshot you can review here." />;
  }

  const q = query.trim().toLowerCase();
  const matches = q
    ? snapshots.filter((s) => `${s.tables.join(" ")} ${s.source_id} ${familyLabel(s.source_family)}`.toLowerCase().includes(q))
    : snapshots;
  const approvedCount = matches.filter((s) => s.approved).length;
  const visible = status === "all" ? matches : matches.filter((s) => Boolean(s.approved) === (status === "approved"));

  return (
    <div className="space-y-3">
      {snapshots.length > 5 && (
        <div className="flex flex-col gap-2 lg:flex-row lg:items-center">
          <SearchInput className="lg:w-[240px]" label="Search datasets" placeholder="Search datasets" value={query} onChange={setQuery} />
          <FilterChips
            label="Dataset status"
            value={status}
            onChange={setStatus}
            options={[
              { value: "all", label: "All", count: matches.length },
              { value: "draft", label: "Needs approval", count: matches.length - approvedCount },
              { value: "approved", label: "Approved", count: approvedCount },
            ]}
          />
        </div>
      )}
      {visible.length === 0 ? (
        <p className="text-[13px] text-secondary">No datasets match.</p>
      ) : (
        <ul role="list" aria-label="Datasets" className="max-h-[320px] space-y-1.5 overflow-y-auto">
          {visible.map((s) => {
            const active = s.snapshot_id === selectedId;
            return (
              <li key={s.snapshot_id}>
                <button
                  type="button"
                  aria-current={active ? "true" : undefined}
                  onClick={() => onSelect(s.snapshot_id)}
                  className={cx(
                    "flex w-full items-center gap-3 border px-3 py-2.5 text-left transition-colors",
                    active ? "border-ink bg-plum-soft" : "border-border bg-surface hover:border-border-strong"
                  )}
                >
                  <ConnectorLogo id={logoIdForFamily(s.source_family) ?? "file"} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13.5px] font-medium text-ink">{datasetLabel(s)}</span>
                    <span className="block truncate text-xs text-secondary">
                      {s.display_name ? `${datasetTables(s)} · ` : ""}v{s.version} · {s.total_rows.toLocaleString()} rows ·{" "}
                      {relativeTime(s.created_at)}
                    </span>
                  </span>
                  {s.unavailable_tables?.length ? (
                    <Badge tone="err" dot>
                      Files missing
                    </Badge>
                  ) : s.approved ? (
                    <Badge tone="ok" dot>
                      Approved
                    </Badge>
                  ) : (
                    <Badge tone="warn" dot>
                      Needs approval
                    </Badge>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
