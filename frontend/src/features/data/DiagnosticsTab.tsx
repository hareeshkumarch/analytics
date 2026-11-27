"use client";

import React from "react";
import { Details, Notice, Skeleton, btn } from "@/components/ui";
import { errorMessage } from "@/lib/api/client";
import { useSnapshotDiagnostics } from "@/lib/api/snapshots";
import { SnapshotSummary, formatBytes, humanize } from "@/lib/workspace";

const CONSISTENCY: Record<string, string> = {
  static_export: "Static export: a fixed copy of the file",
  repeatable_read: "Consistent: read in one repeatable-read transaction",
  best_effort_window: "Best effort: records read over a short time window",
};

function formatTime(iso: string | undefined): string {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(t) ? "—" : new Date(t).toLocaleString();
}

function duration(start: string | undefined, end: string | undefined): string {
  const ms = Date.parse(end ?? "") - Date.parse(start ?? "");
  if (!Number.isFinite(ms) || ms < 0) return "—";
  return ms < 60_000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms / 60_000)} min`;
}

function FactList({ facts }: { facts: [string, string][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
      {facts.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="text-xs text-secondary">{label}</dt>
          <dd className="mt-0.5 break-words text-sm text-ink">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function DiagnosticsTab({ snapshot }: { snapshot: SnapshotSummary }) {
  const query = useSnapshotDiagnostics(snapshot.snapshot_id);

  if (query.isLoading) return <Skeleton className="h-40" />;
  if (query.error || !query.data) {
    return (
      <Notice
        tone="err"
        title="Couldn't load extraction details"
        action={
          <button type="button" className={btn.secondary} onClick={() => void query.refetch()}>
            Try again
          </button>
        }
      >
        {errorMessage(query.error, "No details are available.")}
      </Notice>
    );
  }

  const data = query.data;
  const d = data.diagnostics;
  const level = data.consistency_level ?? "";
  const facts: [string, string][] = [
    ["Consistency", CONSISTENCY[level] ?? (level ? humanize(level) : "—")],
    ["Rows", data.total_rows.toLocaleString()],
    ["Stored size", formatBytes(data.total_normalized_bytes)],
  ];
  if (d) {
    facts.push(
      ["Started", formatTime(d.extraction_started_at)],
      ["Took", duration(d.extraction_started_at, d.extraction_completed_at)],
      ["Complete", d.is_complete === false ? "No, partial extraction" : "Yes"],
      ["Sampled", d.is_sampled ? "Yes, a sample of the source" : "No, every row"],
      ...Object.entries(d.source_summary ?? {})
        .filter(([key]) => key !== "family")
        .map(([key, value]): [string, string] => [humanize(key), String(value)])
    );
  }
  const warnings = d?.warnings ?? [];
  const technical: [string, string][] = [
    ["Snapshot ID", snapshot.snapshot_id],
    ["Source ID", snapshot.source_id],
    ["Definitions ID", snapshot.semantic_version_id ?? "—"],
    ["Content SHA-256", snapshot.sha256 ?? "—"],
  ];

  return (
    <div className="space-y-4">
      {!data.available && (
        <Notice tone="info" title="No extraction details recorded">
          This snapshot was imported before extraction details were stored. Import the source again to record them.
        </Notice>
      )}
      <FactList facts={facts} />
      {d && (
        <div className="space-y-1.5">
          <h4 className="text-sm font-semibold text-ink">Warnings</h4>
          {warnings.length ? (
            <ul className="list-disc space-y-1 pl-5 text-sm text-body">
              {warnings.map((w, i) => (
                <li key={i}>{typeof w === "string" ? w : JSON.stringify(w)}</li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-secondary">No warnings.</p>
          )}
        </div>
      )}
      <Details summary="Technical details">
        <div className="font-mono text-xs">
          <FactList facts={technical} />
        </div>
      </Details>
    </div>
  );
}
