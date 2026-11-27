import React from "react";
import { Badge, btn, cx } from "@/components/ui";
import { SnapshotSummary, relativeTime } from "@/lib/workspace";
import { DataTable, cellCls, rowCls } from "@/features/data/DataTable";

export function VersionsTab({
  snapshot,
  versions,
  onOpen,
}: {
  snapshot: SnapshotSummary;
  versions: SnapshotSummary[];
  onOpen: (snapshotId: string) => void;
}) {
  const sorted = [...versions].sort((a, b) => b.version - a.version);
  return (
    <div className="space-y-3">
      <p className="text-sm leading-relaxed text-body">
        {sorted.length === 1
          ? "This is the only version of this source. Importing the same source again adds a new version here; pinned answers keep the version they used."
          : `${sorted.length} versions of this source, newest first. Each version is a separate, immutable snapshot.`}
      </p>
      <DataTable label="Versions of this source" head={["Version", "Imported", "Rows", "Tables", "Status", "Open"]}>
        {sorted.map((s) => (
          <tr key={s.snapshot_id} className={rowCls}>
            <td className={cx(cellCls, "font-medium text-ink")}>v{s.version}</td>
            <td className={cx(cellCls, "whitespace-nowrap text-secondary")}>{relativeTime(s.created_at)}</td>
            <td className={cellCls}>{s.total_rows.toLocaleString()}</td>
            <td className={cx(cellCls, "max-w-[220px] truncate font-mono text-xs")} title={s.tables.join(", ")}>
              {s.tables.join(", ")}
            </td>
            <td className={cellCls}>
              {s.approved ? (
                <Badge tone="ok" dot>
                  Approved
                </Badge>
              ) : (
                <Badge tone="warn" dot>
                  Needs approval
                </Badge>
              )}
            </td>
            <td className={cellCls}>
              {s.snapshot_id === snapshot.snapshot_id ? (
                <span className="text-xs text-secondary">Viewing</span>
              ) : (
                <button type="button" className={cx(btn.secondary, "px-3")} aria-label={`Open version ${s.version}`} onClick={() => onOpen(s.snapshot_id)}>
                  Open
                </button>
              )}
            </td>
          </tr>
        ))}
      </DataTable>
    </div>
  );
}
