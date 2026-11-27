"use client";

import React from "react";
import Link from "next/link";
import { Badge, EmptyState, Notice, Skeleton, Spinner, btn, cx } from "@/components/ui";
import { errorHint, errorMessage } from "@/lib/api/client";
import { isVersionConflict, useApproveSemantics, useSemantics } from "@/lib/api/semantics";
import { DataTable, cellCls, rowCls } from "@/features/data/DataTable";

export function DefinitionsTab({ snapshotId }: { snapshotId: string }) {
  const semantics = useSemantics(snapshotId);
  const approve = useApproveSemantics(snapshotId);
  const data = semantics.data;

  if (semantics.isLoading) return <Skeleton className="h-40" />;
  if (semantics.error || !data) {
    return (
      <Notice
        tone="err"
        title="Couldn't load the definitions"
        action={
          <button type="button" className={btn.secondary} onClick={() => void semantics.refetch()}>
            Try again
          </button>
        }
      >
        {errorMessage(semantics.error, "No definitions were found for this dataset.")}
      </Notice>
    );
  }

  const count = data.metrics.length;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-body">
          {count} proposed metric{count === 1 ? "" : "s"} · definitions version {data.version}
        </p>
        {data.approved ? (
          <Badge tone="ok" dot>
            Approved
          </Badge>
        ) : (
          <button
            type="button"
            className={btn.primary}
            disabled={approve.isPending || semantics.isFetching}
            onClick={() => {
              if (!approve.isPending) approve.mutate(data.version);
            }}
          >
            {approve.isPending && <Spinner />}
            {approve.isPending ? "Approving…" : "Approve definitions"}
          </button>
        )}
      </div>

      <div aria-live="polite" className="space-y-2">
        {approve.isSuccess && data.approved && (
          <Notice
            tone="ok"
            title="Definitions approved"
            action={
              <Link href={`/ask?dataset=${encodeURIComponent(snapshotId)}`} className={btn.secondary}>
                Ask about this data
              </Link>
            }
          >
            Ask can now answer questions from this dataset.
          </Notice>
        )}
        {approve.error &&
          (isVersionConflict(approve.error) ? (
            <Notice tone="warn" title="These definitions changed since you opened them">
              The latest version is shown below. Review it, then approve again.
            </Notice>
          ) : (
            <Notice tone="err" title={errorMessage(approve.error, "Couldn't approve the definitions.")}>
              {errorHint(approve.error)}
            </Notice>
          ))}
      </div>

      {count === 0 ? (
        <EmptyState title="No metrics were proposed" body="Approving still lets Ask query the imported tables directly." />
      ) : (
        <DataTable label="Proposed metric definitions" head={["Metric", "Calculation", "Table", "Unit"]}>
          {data.metrics.map((m) => (
            <tr key={m.metric_id} className={rowCls}>
              <td className={cx(cellCls, "font-medium text-ink")}>{m.display_name}</td>
              <td className={cx(cellCls, "font-mono text-xs text-body")}>{m.expression}</td>
              <td className={cx(cellCls, "font-mono text-xs text-secondary")}>{m.grain}</td>
              <td className={cx(cellCls, "text-secondary")}>{m.currency ?? m.unit}</td>
            </tr>
          ))}
        </DataTable>
      )}
    </div>
  );
}
