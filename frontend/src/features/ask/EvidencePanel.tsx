"use client";

import React, { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api/client";
import { HScroll } from "@/components/HScroll";
import { Details, Notice, Skeleton, btn } from "@/components/ui";

export interface EvidenceRecord {
  evidence_id: string;
  task_id?: string;
  operation: string;
  purpose: string;
  sql: string;
  row_count: number;
  truncated: boolean;
  snapshot_id: string;
  execution_ms?: number;
  query_ir_hash: string;
  result_hash: string;
  columns: string[];
  rows: unknown[][];
}

function cell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return String(value);
}

/** Side sheet showing the query and rows behind a cited figure. Radix handles focus trap, Escape and focus return. */
export function EvidencePanel({
  workspaceId,
  runId,
  evidenceId,
  onClose,
}: {
  workspaceId: string;
  runId: string;
  evidenceId: string;
  onClose: () => void;
}) {
  const query = useQuery({
    queryKey: ["evidence", workspaceId, runId, evidenceId],
    queryFn: () =>
      apiFetch<EvidenceRecord>(
        `/workspaces/${encodeURIComponent(workspaceId)}/runs/${encodeURIComponent(runId)}/evidence/${encodeURIComponent(evidenceId)}`
      ),
    staleTime: Infinity,
    retry: false,
  });

  const [copiedSql, setCopiedSql] = useState(false);
  const ev = query.data;
  const shown = ev?.rows?.length ?? 0;

  async function copySql() {
    if (!ev?.sql) return;
    try {
      await navigator.clipboard.writeText(ev.sql);
      setCopiedSql(true);
      window.setTimeout(() => setCopiedSql(false), 1500);
    } catch {
      setCopiedSql(false);
    }
  }

  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/45 backdrop-blur-[1px]" />
        <Dialog.Content
          aria-describedby={undefined}
          className="fixed inset-y-0 right-0 z-50 flex w-full max-w-[640px] flex-col border-l border-border bg-surface shadow-pop focus:outline-none"
        >
          <header className="flex items-start justify-between gap-3 border-b border-border px-5 py-4">
            <div className="min-w-0">
              <p className="text-[12px] font-medium uppercase tracking-wide text-secondary">Evidence</p>
              <Dialog.Title className="mt-1 font-display text-[17px] font-semibold text-ink">{ev?.purpose || "Query behind this figure"}</Dialog.Title>
              {ev && (
                <p className="mt-1 text-[12.5px] text-secondary">
                  {(ev.operation ?? "query").replaceAll("_", " ")} · {(ev.row_count ?? 0).toLocaleString()} row{ev.row_count === 1 ? "" : "s"}
                  {ev.execution_ms !== undefined ? ` · ${ev.execution_ms} ms` : ""}
                </p>
              )}
            </div>
            <Dialog.Close className={btn.secondary}>Close</Dialog.Close>
          </header>

          <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
            {query.isLoading && <Skeleton className="h-32" />}
            {query.isError && (
              <Notice
                tone="err"
                title="This evidence isn't available"
                action={
                  <button type="button" className={btn.secondary} onClick={() => void query.refetch()}>
                    Try again
                  </button>
                }
              >
                It couldn&apos;t be loaded. The run may have been deleted or the API may be unreachable; try again, or re-run the question.
              </Notice>
            )}
            {ev && (
              <>
                <section aria-label="Result rows" className="space-y-2">
                  <h3 className="text-[12px] font-semibold uppercase tracking-wide text-secondary">
                    Result{shown < ev.row_count ? ` · first ${shown} of ${ev.row_count.toLocaleString()}` : ""}
                    {ev.truncated ? " · limited by the row cap" : ""}
                  </h3>
                  {shown === 0 ? (
                    <p className="text-[13px] text-secondary">The query returned no rows.</p>
                  ) : (
                    <HScroll className="border border-border" label="Evidence rows">
                      <table className="w-full border-collapse text-left text-[12.5px]">
                        <thead>
                          <tr className="bg-panel">
                            {ev.columns.map((col) => (
                              <th key={col} className="whitespace-nowrap px-3 py-2 font-semibold text-ink">
                                {col}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {ev.rows.map((row, r) => (
                            <tr key={r} className="border-t border-border">
                              {ev.columns.map((_, c) => (
                                <td key={c} className="whitespace-nowrap px-3 py-2 font-mono text-body">
                                  {cell(row[c])}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </HScroll>
                  )}
                </section>

                <section aria-label="SQL" className="space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <h3 className="text-[12px] font-semibold uppercase tracking-wide text-secondary">Query (read-only SQL)</h3>
                    <button type="button" onClick={() => void copySql()} className="inline-flex min-h-[32px] items-center px-2 text-xs text-secondary transition-colors hover:text-ink">
                      {copiedSql ? "Copied SQL" : "Copy SQL"}
                    </button>
                  </div>
                  <HScroll className="border border-border bg-panel" label="SQL query">
                    <pre className="px-3.5 py-3 font-mono text-[12px] leading-relaxed text-ink">
                      <code>{ev.sql}</code>
                    </pre>
                  </HScroll>
                </section>

                <Details summary="Technical identifiers">
                  <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-[12px]">
                    <dt className="text-secondary">Evidence</dt>
                    <dd className="break-all font-mono text-body">{evidenceId}</dd>
                    <dt className="text-secondary">Snapshot</dt>
                    <dd className="break-all font-mono text-body">{ev.snapshot_id}</dd>
                    {ev.task_id && (
                      <>
                        <dt className="text-secondary">Step</dt>
                        <dd className="break-all font-mono text-body">{ev.task_id}</dd>
                      </>
                    )}
                    <dt className="text-secondary">Query hash</dt>
                    <dd className="break-all font-mono text-body">{ev.query_ir_hash}</dd>
                    <dt className="text-secondary">Result hash</dt>
                    <dd className="break-all font-mono text-body">{ev.result_hash}</dd>
                  </dl>
                </Details>
              </>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
