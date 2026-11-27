"use client";

import React from "react";
import { EmptyState, Notice, Skeleton, btn, cx } from "@/components/ui";
import { errorHint, errorMessage } from "@/lib/api/client";
import { useSnapshotPreview } from "@/lib/api/snapshots";
import type { SnapshotSummary } from "@/lib/workspace";
import { DataTable, cellCls, rowCls } from "@/features/data/DataTable";

function Cell({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <td className={cx(cellCls, "italic text-secondary")}>null</td>;
  const text = typeof value === "object" ? JSON.stringify(value) : String(value);
  return (
    <td className={cx(cellCls, "max-w-[280px] truncate whitespace-nowrap")} title={text.length > 32 ? text : undefined}>
      {text}
    </td>
  );
}

export function PreviewTab({ snapshot, table }: { snapshot: SnapshotSummary; table: string }) {
  const preview = useSnapshotPreview(snapshot.snapshot_id, table);
  const total = snapshot.table_details?.find((t) => t.name === table)?.row_count;

  if (preview.isLoading) return <Skeleton className="h-56" />;
  if (preview.error || !preview.data) {
    return (
      <Notice
        tone="err"
        title="Couldn't load a preview"
        action={
          <button type="button" className={btn.secondary} onClick={() => void preview.refetch()}>
            Try again
          </button>
        }
      >
        {errorMessage(preview.error, "No preview is available.")} {errorHint(preview.error)}
      </Notice>
    );
  }

  const { columns, rows, row_count } = preview.data;
  if (!rows.length) return <EmptyState title="This table has no rows" body="The import succeeded, but the source table was empty." />;
  return (
    <div className="space-y-3">
      <p className="text-sm text-body">
        First {row_count.toLocaleString()} {total != null ? `of ${total.toLocaleString()} ` : ""}rows
      </p>
      <DataTable label={`Preview of ${table}`} head={columns} rawHead scrollY minWidth="min-w-max">
        {rows.map((row, i) => (
          <tr key={i} className={rowCls}>
            {columns.map((c) => (
              <Cell key={c} value={row[c]} />
            ))}
          </tr>
        ))}
      </DataTable>
    </div>
  );
}
