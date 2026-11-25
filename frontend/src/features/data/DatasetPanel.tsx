"use client";

import React, { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { ConnectorLogo, logoIdForFamily } from "@/components/ConnectorLogo";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { HScroll } from "@/components/HScroll";
import { RichSelect } from "@/components/RichSelect";
import { Badge, Notice, btn, cx } from "@/components/ui";
import { ApiError, errorMessage } from "@/lib/api/client";
import { useSemantics } from "@/lib/api/semantics";
import { deleteSnapshot } from "@/lib/api/snapshots";
import { familyLabel } from "@/lib/connectors";
import { SnapshotSummary, datasetLabel, formatBytes, relativeTime, useWorkspace } from "@/lib/workspace";
import { DefinitionsTab } from "@/features/data/DefinitionsTab";
import { DiagnosticsTab } from "@/features/data/DiagnosticsTab";
import { PreviewTab } from "@/features/data/PreviewTab";
import { ProfileTab } from "@/features/data/ProfileTab";
import { VersionsTab } from "@/features/data/VersionsTab";

const TABS = [
  { id: "definitions", label: "Definitions" },
  { id: "profile", label: "Profile" },
  { id: "preview", label: "Preview" },
  { id: "versions", label: "Versions" },
  { id: "diagnostics", label: "Diagnostics" },
] as const;

export type DatasetTab = (typeof TABS)[number]["id"];

export function isDatasetTab(value: string | null): value is DatasetTab {
  return TABS.some((t) => t.id === value);
}

export function DatasetPanel({
  snapshot,
  snapshots,
  tab,
  onTabChange,
  onOpenVersion,
  reveal,
  onRevealed,
  onDeleted,
}: {
  snapshot: SnapshotSummary;
  snapshots: SnapshotSummary[];
  tab: DatasetTab;
  onTabChange: (tab: DatasetTab) => void;
  onOpenVersion: (snapshotId: string) => void;
  reveal: boolean;
  onRevealed: () => void;
  onDeleted: () => void;
}) {
  const base = useId();
  const ws = useWorkspace();
  const queryClient = useQueryClient();
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function removeDataset() {
    if (!window.confirm(`Delete ${datasetLabel(snapshot)}? Its imported data is removed; past answers stay readable.`)) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      try {
        await deleteSnapshot(ws.id, snapshot.snapshot_id);
      } catch (err) {
        if (!(err instanceof ApiError && err.status === 409 && window.confirm(`${err.message} Delete it anyway?`))) throw err;
        await deleteSnapshot(ws.id, snapshot.snapshot_id, true);
      }
      await queryClient.invalidateQueries({ queryKey: ["snapshots", ws.id] });
      onDeleted();
    } catch (err) {
      setDeleteError(errorMessage(err, "Couldn't delete this dataset."));
    } finally {
      setDeleting(false);
    }
  }
  const semantics = useSemantics(snapshot.snapshot_id);
  const approved = semantics.data?.approved ?? Boolean(snapshot.approved);
  const [table, setTable] = useState(snapshot.tables[0] ?? "");
  const headingRef = useRef<HTMLHeadingElement>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const versions = snapshots.filter((s) => s.source_id === snapshot.source_id);
  const tableDetails = new Map((snapshot.table_details ?? []).map((t) => [t.name, t]));

  useEffect(() => {
    if (!reveal) return;
    headingRef.current?.scrollIntoView({ block: "nearest" });
    headingRef.current?.focus({ preventScroll: true });
    onRevealed();
  }, [reveal, onRevealed]);

  function handleTabKey(e: React.KeyboardEvent, index: number) {
    const last = TABS.length - 1;
    const next =
      e.key === "ArrowRight" ? (index === last ? 0 : index + 1)
      : e.key === "ArrowLeft" ? (index === 0 ? last : index - 1)
      : e.key === "Home" ? 0
      : e.key === "End" ? last
      : null;
    if (next === null) return;
    e.preventDefault();
    onTabChange(TABS[next].id);
    tabRefs.current[next]?.focus();
  }

  const meta = [
    familyLabel(snapshot.source_family),
    `v${snapshot.version}`,
    `${snapshot.total_rows.toLocaleString()} rows`,
    snapshot.total_normalized_bytes != null ? formatBytes(snapshot.total_normalized_bytes) : null,
    `imported ${relativeTime(snapshot.created_at)}`,
  ].filter(Boolean);

  function renderTab() {
    switch (tab) {
      case "definitions":
        return <DefinitionsTab snapshotId={snapshot.snapshot_id} />;
      case "profile":
        return <ProfileTab snapshot={snapshot} table={table} />;
      case "preview":
        return <PreviewTab snapshot={snapshot} table={table} />;
      case "versions":
        return <VersionsTab snapshot={snapshot} versions={versions} onOpen={onOpenVersion} />;
      case "diagnostics":
        return <DiagnosticsTab snapshot={snapshot} />;
    }
  }

  return (
    <div className="space-y-4 border-t border-border pt-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <ConnectorLogo id={logoIdForFamily(snapshot.source_family) ?? "file"} size="md" />
          <div className="min-w-0">
            <h3 ref={headingRef} tabIndex={-1} className="break-words font-display text-[17px] font-semibold leading-snug text-ink outline-none">
              {datasetLabel(snapshot)}
            </h3>
            <p className="mt-0.5 text-xs text-secondary">{meta.join(" · ")}</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {approved ? (
            <Link href={`/ask?dataset=${encodeURIComponent(snapshot.snapshot_id)}`} className={btn.secondary}>
              Ask about this data
            </Link>
          ) : (
            <Badge tone="warn" dot>
              Needs approval
            </Badge>
          )}
          <button type="button" onClick={() => void removeDataset()} disabled={deleting} className={btn.ghost}>
            {deleting ? "Deleting…" : "Delete"}
          </button>
        </div>
      </div>

      {deleteError && (
        <Notice tone="err" title="Couldn't delete this dataset">
          {deleteError}
        </Notice>
      )}

      {!approved && (
        <Notice
          tone="warn"
          title="Approve the definitions before asking questions"
          action={
            tab !== "definitions" ? (
              <button type="button" className={btn.secondary} onClick={() => onTabChange("definitions")}>
                Review definitions
              </button>
            ) : undefined
          }
        >
          Ask only uses approved datasets. Check the proposed metrics, then approve them.
        </Notice>
      )}

      <HScroll label="Dataset sections">
        <div role="tablist" aria-label="Dataset sections" className="flex w-max min-w-full border-b border-border">
          {TABS.map((t, i) => {
            const active = t.id === tab;
            return (
              <button
                key={t.id}
                ref={(el) => {
                  tabRefs.current[i] = el;
                }}
                type="button"
                role="tab"
                id={`${base}-tab-${t.id}`}
                aria-selected={active}
                aria-controls={`${base}-panel-${t.id}`}
                tabIndex={active ? 0 : -1}
                onClick={() => onTabChange(t.id)}
                onKeyDown={(e) => handleTabKey(e, i)}
                className={cx(
                  "-mb-px inline-flex min-h-[40px] items-center gap-1.5 border-b-2 px-3.5 text-[13px] transition-colors",
                  active ? "border-ink font-medium text-ink" : "border-transparent text-secondary hover:text-ink"
                )}
              >
                {t.label}
                {t.id === "versions" && versions.length > 1 && <span className="tabular-nums text-[11px] text-secondary">{versions.length}</span>}
              </button>
            );
          })}
        </div>
      </HScroll>

      {TABS.map((t) => (
        <div
          key={t.id}
          role="tabpanel"
          id={`${base}-panel-${t.id}`}
          aria-labelledby={`${base}-tab-${t.id}`}
          hidden={t.id !== tab}
          tabIndex={0}
          className="space-y-4"
        >
          {t.id === tab && (
            <>
              {(tab === "profile" || tab === "preview") && snapshot.tables.length > 1 && (
                <div className="max-w-sm">
                  <RichSelect
                    compact
                    ariaLabel="Table"
                    value={table}
                    onChange={setTable}
                    options={snapshot.tables.map((name) => {
                      const rows = tableDetails.get(name)?.row_count;
                      return { value: name, label: name, sublabel: rows != null ? `${rows.toLocaleString()} rows` : undefined };
                    })}
                  />
                </div>
              )}
              <ErrorBoundary key={`${snapshot.snapshot_id}-${tab}-${table}`} title="This tab couldn't be shown">
                {renderTab()}
              </ErrorBoundary>
            </>
          )}
        </div>
      ))}
    </div>
  );
}
