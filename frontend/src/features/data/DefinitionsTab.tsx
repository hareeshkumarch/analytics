"use client";

import React, { useMemo, useState } from "react";
import Link from "next/link";
import { Badge, EmptyState, Notice, SearchInput, Skeleton, Spinner, btn, btnSm, cx, inputSmCls } from "@/components/ui";
import { errorHint, errorMessage } from "@/lib/api/client";
import { isVersionConflict, useApproveSemantics, useSemantics, useUpdateSemantics } from "@/lib/api/semantics";
import { DataTable, cellCls, rowCls } from "@/features/data/DataTable";
import { toast } from "@/lib/toast";

interface DraftMetric {
  display_name: string;
  unit: string;
  currency: string;
}

export function DefinitionsTab({ snapshotId }: { snapshotId: string }) {
  const semantics = useSemantics(snapshotId);
  const approve = useApproveSemantics(snapshotId);
  const updateSemantics = useUpdateSemantics(snapshotId);
  const data = semantics.data;

  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, DraftMetric>>({});
  const [removedIds, setRemovedIds] = useState<Set<string>>(() => new Set());

  const metrics = data?.metrics;
  const visibleMetrics = useMemo(() => {
    const list = metrics ?? [];
    const q = query.trim().toLowerCase();
    if (!q) return list;
    return list.filter(
      (m) =>
        m.display_name.toLowerCase().includes(q) ||
        m.expression.toLowerCase().includes(q) ||
        m.grain.toLowerCase().includes(q) ||
        (m.currency ?? m.unit ?? "").toLowerCase().includes(q),
    );
  }, [metrics, query]);

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

  function startEditing() {
    if (!data) return;
    const init: Record<string, DraftMetric> = {};
    for (const m of data.metrics) {
      init[m.metric_id] = {
        display_name: m.display_name,
        unit: m.unit ?? "",
        currency: m.currency ?? "",
      };
    }
    setDrafts(init);
    setRemovedIds(new Set());
    updateSemantics.reset();
    setEditing(true);
  }

  async function saveEdits(e: React.FormEvent) {
    e.preventDefault();
    if (!data || updateSemantics.isPending) return;
    const editedMetrics = data.metrics
      .filter((m) => !removedIds.has(m.metric_id))
      .map((m) => {
        const d = drafts[m.metric_id];
        return {
          metric_id: m.metric_id,
          display_name: (d?.display_name ?? m.display_name).trim() || m.display_name,
          unit: d ? (d.unit.trim() || null) : (m.unit ?? null),
          currency: d ? (d.currency.trim() || null) : (m.currency ?? null),
        };
      });
    try {
      await updateSemantics.mutateAsync({
        expected_version: data.version,
        metrics: editedMetrics,
        remove_metric_ids: Array.from(removedIds),
      });
      setEditing(false);
      toast("Definitions updated.");
    } catch {
      // Handled via updateSemantics.error Notice below.
    }
  }

  const count = data.metrics.length;
  const activeError = updateSemantics.error ?? approve.error;

  return (
    <form onSubmit={(e) => void saveEdits(e)} className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-body">
          {count} {data.approved ? "approved" : "proposed"} metric{count === 1 ? "" : "s"} · definitions version {data.version}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          {editing ? (
            <>
              <button type="button" className={btn.ghost} onClick={() => setEditing(false)} disabled={updateSemantics.isPending}>
                Cancel
              </button>
              <button type="submit" className={btn.primary} disabled={updateSemantics.isPending}>
                {updateSemantics.isPending && <Spinner />}
                {updateSemantics.isPending ? "Saving…" : "Save changes"}
              </button>
            </>
          ) : (
            <>
              {count > 0 && (
                <button type="button" className={btn.secondary} onClick={startEditing} disabled={approve.isPending || semantics.isFetching}>
                  Edit definitions
                </button>
              )}
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
            </>
          )}
        </div>
      </div>

      {editing && data.approved && (
        <Notice tone="warn" title="Editing creates a new draft">
          Saving changes creates an unapproved draft version that needs approval before Ask uses it.
        </Notice>
      )}

      <div aria-live="polite" className="space-y-2">
        {approve.isSuccess && data.approved && !editing && (
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
        {activeError &&
          (isVersionConflict(activeError) ? (
            <Notice tone="warn" title="These definitions changed since you opened them">
              The latest version is shown below. Review it, then try again.
            </Notice>
          ) : (
            <Notice tone="err" title={errorMessage(activeError, "Couldn't update the definitions.")}>
              {errorHint(activeError)}
            </Notice>
          ))}
      </div>

      {count > 0 && (
        <div className="max-w-xs">
          <SearchInput dense label="Filter metrics" placeholder="Filter metrics" value={query} onChange={setQuery} />
        </div>
      )}

      {count === 0 ? (
        <EmptyState title="No metrics were proposed" body="Approving still lets Ask query the imported tables directly." />
      ) : visibleMetrics.length === 0 ? (
        <EmptyState title="No matching metrics" body="Clear the filter to see every proposed metric." />
      ) : (
        <DataTable
          label="Proposed metric definitions"
          head={editing ? ["Metric", "Calculation", "Table", "Unit / Currency", ""] : ["Metric", "Calculation", "Table", "Unit"]}
        >
          {visibleMetrics.map((m) => {
            const removed = removedIds.has(m.metric_id);
            const draft = drafts[m.metric_id] ?? {
              display_name: m.display_name,
              unit: m.unit ?? "",
              currency: m.currency ?? "",
            };
            return (
              <tr key={m.metric_id} className={cx(rowCls, removed && "opacity-50")}>
                <td className={cx(cellCls, "font-medium text-ink")}>
                  {editing && !removed ? (
                    <input
                      aria-label={`Metric name for ${m.display_name}`}
                      value={draft.display_name}
                      maxLength={120}
                      onChange={(ev) =>
                        setDrafts((prev) => ({
                          ...prev,
                          [m.metric_id]: { ...draft, display_name: ev.target.value },
                        }))
                      }
                      className={cx(inputSmCls, "min-w-[140px]")}
                    />
                  ) : (
                    <span className={removed ? "line-through" : undefined}>{m.display_name}</span>
                  )}
                </td>
                <td className={cx(cellCls, "font-mono text-xs text-body")}>{m.expression}</td>
                <td className={cx(cellCls, "font-mono text-xs text-secondary")}>{m.grain}</td>
                <td className={cx(cellCls, "text-secondary")}>
                  {editing && !removed ? (
                    <div className="flex items-center gap-1.5">
                      <input
                        aria-label={`Unit for ${m.display_name}`}
                        placeholder="Unit"
                        value={draft.unit}
                        maxLength={32}
                        onChange={(ev) =>
                          setDrafts((prev) => ({
                            ...prev,
                            [m.metric_id]: { ...draft, unit: ev.target.value },
                          }))
                        }
                        className={cx(inputSmCls, "w-20 font-mono")}
                      />
                      <input
                        aria-label={`Currency for ${m.display_name}`}
                        placeholder="CCY"
                        value={draft.currency}
                        maxLength={16}
                        onChange={(ev) =>
                          setDrafts((prev) => ({
                            ...prev,
                            [m.metric_id]: { ...draft, currency: ev.target.value },
                          }))
                        }
                        className={cx(inputSmCls, "w-20 font-mono uppercase")}
                      />
                    </div>
                  ) : (
                    m.currency ?? m.unit
                  )}
                </td>
                {editing && (
                  <td className={cx(cellCls, "text-right")}>
                    <button
                      type="button"
                      className={btnSm.ghost}
                      onClick={() =>
                        setRemovedIds((prev) => {
                          const next = new Set(prev);
                          if (next.has(m.metric_id)) next.delete(m.metric_id);
                          else next.add(m.metric_id);
                          return next;
                        })
                      }
                    >
                      {removed ? "Restore" : "Remove"}
                    </button>
                  </td>
                )}
              </tr>
            );
          })}
        </DataTable>
      )}
    </form>
  );
}
