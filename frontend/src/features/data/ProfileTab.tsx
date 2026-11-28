"use client";

import React from "react";
import { Notice, Skeleton, btn, cx } from "@/components/ui";
import { errorHint, errorMessage } from "@/lib/api/client";
import { useSnapshotProfile } from "@/lib/api/snapshots";
import type { ColumnDetail, SnapshotSummary } from "@/lib/workspace";
import { DataTable, cellCls, rowCls } from "@/features/data/DataTable";

function typeText(c: ColumnDetail | undefined): string {
  if (!c) return "—";
  return [c.type, c.currency, c.leading_zero_id && "leading zeros kept", c.note].filter(Boolean).join(" · ");
}

function Bound({ value }: { value: string | null }) {
  if (value === null) return <td className={cx(cellCls, "text-secondary")}>—</td>;
  return (
    <td className={cx(cellCls, "max-w-[200px] truncate font-mono text-xs")} title={value}>
      {value}
    </td>
  );
}

export function ProfileTab({ snapshot, table }: { snapshot: SnapshotSummary; table: string }) {
  const profile = useSnapshotProfile(snapshot.snapshot_id, table);
  const details = snapshot.table_details?.find((t) => t.name === table);
  const types = new Map((details?.columns ?? []).map((c) => [c.name, c]));

  if (profile.isLoading) return <Skeleton className="h-40" />;
  if (profile.error || !profile.data) {
    return (
      <Notice
        tone="err"
        title="Couldn't profile this table"
        action={
          <button type="button" className={btn.secondary} onClick={() => void profile.refetch()}>
            Try again
          </button>
        }
      >
        {errorMessage(profile.error, "No profile is available.")} {errorHint(profile.error)}
      </Notice>
    );
  }

  const columns = profile.data.columns;
  return (
    <div className="space-y-3">
      <p className="text-sm text-body">
        {columns.length} column{columns.length === 1 ? "" : "s"}
        {details ? ` · ${details.row_count.toLocaleString()} rows` : ""}
      </p>
      {columns.some((c) => c.is_sampled) && (
        <Notice tone="info" title="Sampled statistics">
          This snapshot holds a sample of the source, so these counts describe the sample.
        </Notice>
      )}
      <DataTable
        label={`Column profile of ${table}`}
        head={[
          "Column",
          "Type",
          { label: "Nulls", align: "right" },
          { label: "Distinct", align: "right" },
          { label: "Duplicates", align: "right" },
          "Min",
          "Max",
        ]}
        minWidth="min-w-[760px]"
      >
        {columns.map((c) => (
          <tr key={c.column_name} className={rowCls}>
            <td className={cx(cellCls, "font-mono text-sm text-ink")}>{c.column_name}</td>
            <td className={cx(cellCls, "font-mono text-xs text-secondary")}>{typeText(types.get(c.column_name))}</td>
            <td className={cx(cellCls, "text-right tabular-nums")}>{c.null_count.toLocaleString()}</td>
            <td className={cx(cellCls, "text-right tabular-nums")}>{c.distinct_count.toLocaleString()}</td>
            <td className={cx(cellCls, "text-right tabular-nums")}>{c.duplicate_count.toLocaleString()}</td>
            <Bound value={c.min_value} />
            <Bound value={c.max_value} />
          </tr>
        ))}
      </DataTable>
    </div>
  );
}
