"use client";

import React, { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { EmptyState, Notice, Skeleton, btn, cx } from "@/components/ui";
import { errorHint, errorMessage } from "@/lib/api/client";
import { useSnapshotPreview } from "@/lib/api/snapshots";
import { toast } from "@/lib/toast";
import type { SnapshotSummary } from "@/lib/workspace";
import { DataTable, cellCls, rowCls } from "@/features/data/DataTable";

interface InspectedCell {
  column: string;
  rowIndex: number;
  text: string;
  formatted: string;
}

function formatCellValue(value: unknown): { text: string; formatted: string } {
  if (typeof value === "object") {
    return { text: JSON.stringify(value), formatted: JSON.stringify(value, null, 2) };
  }
  const str = String(value);
  const trimmed = str.trim();
  if ((trimmed.startsWith("{") && trimmed.endsWith("}")) || (trimmed.startsWith("[") && trimmed.endsWith("]"))) {
    try {
      return { text: str, formatted: JSON.stringify(JSON.parse(trimmed), null, 2) };
    } catch {}
  }
  return { text: str, formatted: str };
}

function Cell({
  value,
  column,
  rowIndex,
  onInspect,
}: {
  value: unknown;
  column: string;
  rowIndex: number;
  onInspect: (cell: InspectedCell) => void;
}) {
  if (value === null || value === undefined) return <td className={cx(cellCls, "italic text-secondary")}>null</td>;
  const { text, formatted } = formatCellValue(value);
  const truncated = text.length > 32 || text.includes("\n");
  return (
    <td className={cx(cellCls, "max-w-[280px]")}>
      <button
        type="button"
        onClick={() => onInspect({ column, rowIndex, text, formatted })}
        title={truncated ? "Click to inspect full value" : undefined}
        className="block w-full truncate whitespace-nowrap text-left hover:underline focus-visible:rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
      >
        {text}
      </button>
    </td>
  );
}

export function PreviewTab({ snapshot, table }: { snapshot: SnapshotSummary; table: string }) {
  const preview = useSnapshotPreview(snapshot.snapshot_id, table);
  const total = snapshot.table_details?.find((t) => t.name === table)?.row_count;
  const [inspected, setInspected] = useState<InspectedCell | null>(null);

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
        First {row_count.toLocaleString()} {total != null ? `of ${total.toLocaleString()} ` : ""}rows · click any cell to inspect
      </p>
      <DataTable label={`Preview of ${table}`} head={columns} rawHead scrollY minWidth="min-w-max">
        {rows.map((row, i) => (
          <tr key={i} className={rowCls}>
            {columns.map((c) => (
              <Cell key={c} column={c} rowIndex={i} value={row[c]} onInspect={setInspected} />
            ))}
          </tr>
        ))}
      </DataTable>

      <Dialog.Root open={Boolean(inspected)} onOpenChange={(open) => !open && setInspected(null)}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-black/45 backdrop-blur-[1px] dark:bg-black/65" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(92vw,560px)] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-ink/[0.08] bg-popover p-5 shadow-pop focus:outline-none dark:border-border-strong">
            <Dialog.Title className="font-mono text-sm font-semibold text-ink">{inspected?.column}</Dialog.Title>
            <Dialog.Description className="mt-0.5 text-xs text-secondary">
              Row {inspected ? inspected.rowIndex + 1 : 0} · {table}
            </Dialog.Description>
            <pre className="mt-3 max-h-[50vh] overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border bg-panel p-3 font-mono text-xs leading-relaxed text-ink">
              {inspected?.formatted}
            </pre>
            <div className="mt-4 flex items-center justify-end gap-2">
              <button
                type="button"
                className={btn.secondary}
                onClick={() => {
                  if (!inspected) return;
                  void navigator.clipboard?.writeText(inspected.formatted);
                  toast("Cell value copied.");
                }}
              >
                Copy value
              </button>
              <Dialog.Close asChild>
                <button type="button" className={btn.primary}>
                  Close
                </button>
              </Dialog.Close>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
