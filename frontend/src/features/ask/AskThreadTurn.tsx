"use client";

import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AnswerCharts } from "@/components/AnswerCharts";
import { AnswerVerification } from "@/components/AnswerVerification";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { Icon } from "@/components/Icon";
import { EvidenceContext, SafeMarkdown } from "@/components/SafeMarkdown";
import { Badge, Details, Notice, btn, btnSm, cx, dangerConfirmBtnCls, inputBaseCls, outlineCls } from "@/components/ui";
import { AnswerMenu } from "@/features/ask/AnswerMenu";
import {
  BUDGET_RESOURCES,
  OPERATION_LABELS,
  ROUTE_LABELS,
  type Session,
  eventSummary,
  friendlyError,
  isTerminal,
  statusLabel,
  statusTone,
  taskName,
} from "@/features/ask/askTypes";
import type { SnapshotOut, TaskRecordListOut } from "@/lib/api/types";
import { apiFetch } from "@/lib/api/client";
import { progressSteps } from "@/lib/progress";
import { citationNumbers, headlineFigure, stripEvidenceIds } from "@/lib/text";
import { toast } from "@/lib/toast";
import type { RunMode } from "@/lib/types";
import { datasetLabel, relativeTime, workspacePath } from "@/lib/workspace";
import type { SseEnvelope } from "@/lib/sse";

export function RunProgress({
  events,
  workspaceId,
  runId,
  status,
  mode,
  showInitial,
}: {
  events: SseEnvelope[];
  workspaceId: string;
  runId: string | null;
  status: string;
  mode: RunMode;
  showInitial: boolean;
}) {
  const changes = events.filter((e) => e.type === "task.status" || e.type === "plan.updated").length;
  const records = useQuery({
    queryKey: ["run-tasks", workspaceId, runId, changes],
    queryFn: () => apiFetch<TaskRecordListOut>(workspacePath(workspaceId, `/runs/${encodeURIComponent(runId!)}/tasks`)),
    enabled: Boolean(runId) && changes > 0,
    placeholderData: (previous) => previous,
  });
  const steps = progressSteps(events, records.data?.tasks);
  if (!steps.length) {
    if (!showInitial) return null;
    return (
      <p className="flex items-center gap-2.5 text-sm text-secondary">
        <span aria-hidden="true" className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-warn-dot" />
        <span>{status === "queued" ? "Queued…" : mode === "deep" ? "Planning the investigation…" : "Preparing query…"}</span>
      </p>
    );
  }
  return (
    <ol className="space-y-1.5" aria-label="Progress">
      {steps.map(({ taskId: t, status: st, queries }) => (
        <li key={t} className="flex animate-fade-in items-center gap-2.5 text-sm">
          {/* A finished step's dot turns into a check mark; both markers take the same 14px. */}
          {st === "completed" ? (
            <span aria-hidden="true" className="flex h-3.5 w-3.5 shrink-0 animate-check-in items-center justify-center rounded-full bg-ok-dot text-white">
              <Icon name="check" className="h-2.5 w-2.5" strokeWidth={3} />
            </span>
          ) : (
            <span
              aria-hidden="true"
              className={cx(
                "mx-[3px] h-2 w-2 shrink-0 rounded-full",
                st === "failed" ? "bg-err-dot" : st === "running" ? "animate-pulse bg-warn-dot" : "bg-border-strong",
              )}
            />
          )}
          <span className={st === "pending" ? "text-secondary" : "text-ink"}>{taskName(t)}</span>
          <span className="text-xs text-secondary">
            {st === "running"
              ? "working"
              : st === "completed"
                ? "done"
                : st === "failed"
                  ? "failed"
                  : st === "superseded"
                    ? "replaced"
                    : st === "cancelled"
                      ? "skipped"
                      : "waiting"}
            {queries ? ` · ${queries} quer${queries === 1 ? "y" : "ies"}` : ""}
          </span>
        </li>
      ))}
    </ol>
  );
}

interface AskThreadTurnProps {
  session: Session;
  turnIndex: number;
  isFocusedTurn: boolean;
  isLatestTurn: boolean;
  snapshotsLoading: boolean;
  snapshotById: Map<string, SnapshotOut>;
  fallbackSnapshot: SnapshotOut | undefined;
  workspaceId: string;
  canAsk: boolean;
  submitting: boolean;
  pinningRunId: string | null;
  pinnedRunIds: Set<string>;
  confirmDeleteId: string | null;
  clarificationSubmitting: boolean;
  maxClarificationChars?: number;
  registerRef?: (el: HTMLElement | null) => void;
  onPin: (session: Session) => void;
  onCancel: (session: Session) => void;
  onRequestDelete: (session: Session) => void;
  onConfirmDelete: (session: Session) => void;
  onCancelDelete: () => void;
  onRetry: (session: Session) => void;
  onClarificationChange: (sessionId: string, draft: string) => void;
  onClarificationSubmit: (session: Session, e: React.FormEvent) => void;
  onReconnect: (session: Session) => void;
  onOpenEvidence: (runId: string, evidenceId: string) => void;
  onAskSuggestion: (suggestion: string, session: Session) => void;
}

export function AskThreadTurn({
  session: s,
  turnIndex,
  isFocusedTurn,
  isLatestTurn,
  snapshotsLoading,
  snapshotById,
  fallbackSnapshot,
  workspaceId,
  canAsk,
  submitting,
  pinningRunId,
  pinnedRunIds,
  confirmDeleteId,
  clarificationSubmitting,
  maxClarificationChars,
  registerRef,
  onPin,
  onCancel,
  onRequestDelete,
  onConfirmDelete,
  onCancelDelete,
  onRetry,
  onClarificationChange,
  onClarificationSubmit,
  onReconnect,
  onOpenEvidence,
  onAskSuggestion,
}: AskThreadTurnProps) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState<string | null>(null);
  const [techOpen, setTechOpen] = useState(false);
  const techId = useId();
  const cancelDeleteRef = useRef<HTMLButtonElement>(null);
  const actionsRef = useRef<HTMLDivElement>(null);

  const confirmOpen = confirmDeleteId === s.id;
  useEffect(() => {
    if (confirmOpen) cancelDeleteRef.current?.focus();
  }, [confirmOpen]);

  const dataset = s.snapshotId ? snapshotById.get(s.snapshotId) : undefined;
  const datasetRemoved = Boolean(s.snapshotId) && !snapshotsLoading && !dataset;
  const isBusy = !isTerminal(s.status);
  const isAwaitingInput = s.status === "awaiting_input";
  const isWorking = isBusy && !isAwaitingInput;
  const canCopy = s.status === "completed" && Boolean(s.runId && s.answer);
  const canDelete = Boolean(s.runId) && !isBusy && isTerminal(s.status);
  const canPin = s.status === "completed" && Boolean(s.runId);
  const hasTechDetails = Boolean(s.runId) || s.events.length > 0;

  const headline = useMemo(() => (s.status === "completed" && s.answer ? headlineFigure(s.answer) : null), [s.status, s.answer]);
  const sourceNumbers = useMemo(() => citationNumbers(s.answer), [s.answer]);

  const toolEvents = s.events.filter((e) => e.type === "tool.result");
  const nonSchemaTools = toolEvents.filter(
    (e, i, all) =>
      e.payload.tool !== "inspect_schema" &&
      (typeof e.payload.evidence_id !== "string" || all.findIndex((o) => o.payload.evidence_id === e.payload.evidence_id) === i),
  );
  const finishedEvent = [...s.events].reverse().find((e) => e.type === "run.finished");
  const timeLimited = Boolean(finishedEvent?.payload.time_budget_exceeded);
  const budgetWarnings = s.events.filter((e) => e.type === "budget.warning");
  const planEvent = [...s.events].reverse().find((e) => e.type === "plan.updated");
  const route = typeof planEvent?.payload.route === "string" ? planEvent.payload.route : null;
  const models = Array.from(
    new Set(s.events.filter((e) => e.type === "route.selected").map((e) => `${String(e.payload.provider ?? "")} · ${String(e.payload.model ?? "")}`)),
  );
  const showSteps = s.events.filter((e) => e.type !== "answer.delta");
  const errorInfo = s.error ? friendlyError(s.error) : null;

  async function copyAnswer() {
    if (!s.answer) return;
    setCopyError(null);
    try {
      await navigator.clipboard.writeText(stripEvidenceIds(s.answer));
      setCopied(true);
      toast("Answer copied.");
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
      setCopyError("Couldn't copy automatically; select and copy the answer text below.");
    }
  }

  return (
    <article
      ref={registerRef}
      aria-label={isFocusedTurn ? "Current question" : `Follow-up ${turnIndex}: ${s.question}`}
      // Scroll margins keep a scrolled-to turn clear of the sticky header and the pinned follow-up box.
      className="group/turn scroll-mb-40 scroll-mt-6 animate-fade-up space-y-4"
    >
      <div className="flex justify-end">
        <p className="max-w-[560px] whitespace-pre-wrap break-words rounded-xl bg-bubble px-4 py-2.5 text-base leading-relaxed">
          {s.question}
        </p>
      </div>

      <section
        aria-label={isFocusedTurn ? "Answer" : `Previous reply ${turnIndex + 1}: ${s.question}`}
        aria-busy={isWorking}
        className="space-y-4"
      >
        <div className="flex min-h-[32px] flex-wrap items-center justify-between gap-2.5">
          <span className="flex min-w-0 flex-wrap items-center gap-1.5 text-xs text-secondary">
            <span>{s.mode === "deep" ? "Deep investigation" : "Quick answer"}</span>
            {dataset && <span>· {datasetLabel(dataset)} v{dataset.version}</span>}
            {datasetRemoved && (
              <>
                <span aria-hidden="true">·</span>
                <Badge tone="neutral">Removed dataset</Badge>
              </>
            )}
            {s.createdAt && <span>· {relativeTime(s.createdAt)}</span>}
          </span>
          {/* A finished answer needs no status pill: the verification line under it says how far to trust it. */}
          {s.status !== "completed" && (
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={statusTone(s.status)} dot pulse={isWorking}>
                {statusLabel(s.status)}
              </Badge>
              {isBusy && s.runId && (
                <button type="button" onClick={() => onCancel(s)} className={btn.secondary}>
                  Cancel
                </button>
              )}
            </div>
          )}
        </div>

        {s.connectionLost && isBusy && (
          <Notice
            tone="warn"
            title="Live updates stopped"
            action={
              <button type="button" className={btn.secondary} onClick={() => onReconnect(s)}>
                Reconnect
              </button>
            }
          >
            The run may still be working. This page also checks its status every few seconds.
          </Notice>
        )}

        {errorInfo && (
          <Notice
            tone={s.status === "failed" ? "err" : "warn"}
            title={errorInfo.title}
            action={
              s.status === "failed" && canAsk && (dataset?.approved || fallbackSnapshot) ? (
                <button type="button" className={btn.secondary} disabled={submitting} onClick={() => onRetry(s)}>
                  Retry question
                </button>
              ) : undefined
            }
          >
            {errorInfo.hint}
            {errorInfo.title !== s.error && (
              <Details summary="Details" className="mt-1">
                <p className="break-words font-mono text-xs opacity-80">{s.error}</p>
              </Details>
            )}
          </Notice>
        )}

        {timeLimited && (
          <Notice tone="warn" title="Stopped at the time limit">
            The answer may be incomplete. Try Quick mode or a narrower question.
          </Notice>
        )}
        {budgetWarnings.length > 0 && (
          <Notice tone="warn" title="This run is close to its budget">
            {budgetWarnings
              .map((e) => `${BUDGET_RESOURCES[String(e.payload.resource)] ?? String(e.payload.resource)}: ${Number(e.payload.used).toLocaleString()} of ${Number(e.payload.limit).toLocaleString()} used`)
              .join(" · ")}
            . Later steps may be skipped and the answer may be partial.
          </Notice>
        )}
        {copyError && <Notice tone="err" title={copyError} />}

        {isAwaitingInput && s.clarificationPrompt && (
          <form onSubmit={(e) => onClarificationSubmit(s, e)} className="space-y-3 rounded-lg border border-border-strong bg-canvas p-4">
            <Notice tone="warn" title="A detail is needed to continue">
              {s.clarificationPrompt}
            </Notice>
            <textarea
              aria-label="Clarification answer"
              value={s.clarificationDraft}
              onChange={(event) => onClarificationChange(s.id, event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }}
              maxLength={maxClarificationChars}
              className={cx(inputBaseCls, "min-h-[84px] resize-y py-2.5")}
              placeholder="Add the missing detail"
            />
            <div className="flex justify-end">
              <button type="submit" disabled={!s.clarificationDraft.trim() || clarificationSubmitting} className={btn.primary}>
                {clarificationSubmitting ? "Sending…" : "Continue"}
              </button>
            </div>
          </form>
        )}

        {isWorking && (
          <RunProgress
            events={s.events}
            workspaceId={workspaceId}
            runId={s.runId ?? null}
            status={s.status}
            mode={s.mode}
            showInitial={!s.answer}
          />
        )}

        {s.answer ? (
          <ErrorBoundary title="This answer couldn't be formatted">
            {headline && <p className="mb-2 text-[32px] font-semibold leading-tight tracking-tight text-ink tabular-nums">{headline}</p>}
            <EvidenceContext.Provider value={s.runId ? (evId) => onOpenEvidence(s.runId!, evId) : null}>
              {/* While the answer streams in, a caret blinks after its last line (see .streaming-caret in globals.css). */}
              <div className={isWorking ? "streaming-caret" : undefined}>
                <SafeMarkdown content={s.answer} />
              </div>
              {s.status === "completed" && <AnswerCharts charts={s.charts} />}
            </EvidenceContext.Provider>
            {s.status === "completed" && <AnswerVerification outcome={s.outcome} criteria={s.criteria} />}
          </ErrorBoundary>
        ) : (
          isWorking && (
            <div className="space-y-2.5" aria-hidden="true">
              <div className="h-3 w-[86%] animate-pulse rounded bg-bubble" />
              <div className="h-3 w-[64%] animate-pulse rounded bg-bubble" />
              <div className="h-3 w-[72%] animate-pulse rounded bg-bubble" />
            </div>
          )
        )}

        {s.status === "completed" && s.limitations.length > 0 && (
          <section aria-label="Limitations" className="rounded-lg border border-border bg-panel/60 px-3.5 py-2.5">
            <p className="text-xs font-medium uppercase tracking-wide text-secondary">Limitations</p>
            <ul className="mt-1 list-disc space-y-1 pl-4 text-xs leading-relaxed text-body">
              {s.limitations.map((lim, idx) => (
                <li key={idx}>{lim}</li>
              ))}
            </ul>
          </section>
        )}

        {nonSchemaTools.length > 0 && (
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
            <p className="text-xs text-secondary">Sources</p>
            <ul aria-label="Sources" className="flex flex-wrap gap-1.5">
              {nonSchemaTools.map((evt) => {
                const p = evt.payload;
                const evId = typeof p.evidence_id === "string" ? p.evidence_id : null;
                const purpose = typeof p.purpose === "string" ? p.purpose.trim().replace(/\.$/, "") : "";
                const cited = evId ? sourceNumbers.get(evId) : undefined;
                const label = `${cited ? `${cited}. ` : ""}${purpose || (OPERATION_LABELS[String(p.operation ?? "")] ?? "Query")}${
                  p.row_count !== undefined ? ` · ${Number(p.row_count).toLocaleString()} rows` : ""
                }`;
                return (
                  <li key={evt.event_id}>
                    {evId && s.runId ? (
                      <button type="button" onClick={() => onOpenEvidence(s.runId!, evId)} className={cx(btnSm.secondary, "font-normal")}>
                        <span className="h-1.5 w-1.5 rounded-full bg-ok-dot" aria-hidden="true" />
                        {label}
                      </button>
                    ) : (
                      <span className="inline-flex min-h-[28px] items-center gap-1.5 rounded-md bg-panel px-2.5 text-sm text-secondary">
                        <span className={cx("h-1.5 w-1.5 rounded-full", p.ok ? "bg-secondary" : "bg-err-dot")} aria-hidden="true" />
                        {p.ok ? label : `${label} · failed (retried)`}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        {/* One row ends every answer: its actions on the left, the technical-details toggle on the right. */}
        {(canPin || canCopy || canDelete || hasTechDetails) && (
          <div className="-ml-1 flex flex-wrap items-center gap-1">
            <div
              ref={actionsRef}
              className={cx(
                "flex flex-wrap items-center gap-1",
                // Older turns keep their actions out of the way until hovered or focused; touch screens always show them.
                !isLatestTurn &&
                  !confirmOpen &&
                  "transition-opacity duration-150 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:focus-within:opacity-100 [@media(hover:hover)]:group-hover/turn:opacity-100",
              )}
            >
              {canPin &&
                (pinnedRunIds.has(s.runId!) ? (
                  <Link href="/dashboard" className={btn.ghost}>
                    <Icon name="check" className="h-3.5 w-3.5 text-ok" strokeWidth={2.25} />
                    Pinned · open Dashboard
                  </Link>
                ) : (
                  <button type="button" onClick={() => onPin(s)} disabled={pinningRunId === s.runId} className={btn.ghost}>
                    <Icon name="grid" className="h-3.5 w-3.5" />
                    {pinningRunId === s.runId ? "Pinning…" : "Pin to Dashboard"}
                  </button>
                ))}
              {confirmOpen ? (
                <>
                  <button type="button" onClick={() => onConfirmDelete(s)} className={dangerConfirmBtnCls}>
                    Confirm delete
                  </button>
                  <button
                    ref={cancelDeleteRef}
                    type="button"
                    onClick={() => {
                      onCancelDelete();
                      requestAnimationFrame(() => actionsRef.current?.querySelector<HTMLButtonElement>("[aria-haspopup='menu']")?.focus());
                    }}
                    className={btn.ghost}
                  >
                    Keep it
                  </button>
                </>
              ) : (
                (canCopy || canDelete) && (
                  <AnswerMenu
                    workspaceId={workspaceId}
                    runId={canCopy ? s.runId : null}
                    question={s.question}
                    copied={copied}
                    onCopy={canCopy ? () => void copyAnswer() : undefined}
                    onDelete={canDelete ? () => onRequestDelete(s) : undefined}
                  />
                )
              )}
            </div>
            {hasTechDetails && (
              <button
                type="button"
                aria-expanded={techOpen}
                aria-controls={techOpen ? techId : undefined}
                onClick={() => setTechOpen((open) => !open)}
                className="ml-auto inline-flex min-h-[32px] items-center gap-1.5 px-1 text-sm text-secondary transition-colors hover:text-ink"
              >
                <Icon name="chevron-right" className={cx("h-3 w-3 transition-transform", techOpen && "rotate-90")} />
                Technical details
              </button>
            )}
          </div>
        )}

        {hasTechDetails && techOpen && (
          <div id={techId} className="animate-fade-in">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
              {s.runId && (
                <>
                  <dt className="text-secondary">Run</dt>
                  <dd className="break-all font-mono text-body">{s.runId}</dd>
                </>
              )}
              {models.length > 0 && (
                <>
                  <dt className="text-secondary">Models</dt>
                  <dd className="text-body">{models.join(", ")}</dd>
                </>
              )}
              {route && (
                <>
                  <dt className="text-secondary">Path</dt>
                  <dd className="text-body" title={typeof planEvent?.payload.route_reason === "string" ? planEvent.payload.route_reason : undefined}>
                    {ROUTE_LABELS[route] ?? route}
                  </dd>
                </>
              )}
              <dt className="text-secondary">Events</dt>
              <dd className="text-body">{s.events.length}</dd>
            </dl>
            {showSteps.length > 0 && (
              <div className="mt-2 max-h-64 space-y-1 overflow-y-auto">
                {showSteps.map((evt) => (
                  <div key={evt.event_id} className="flex gap-2.5 rounded bg-panel px-2.5 py-[7px] font-mono text-xs leading-relaxed">
                    <span className="shrink-0 text-secondary">#{evt.sequence}</span>
                    <span className="shrink-0 text-ink">{evt.type}</span>
                    <span className="min-w-0 break-all text-secondary">
                      {typeof evt.payload.task_id === "string" ? `${taskName(evt.payload.task_id)} ` : ""}
                      {eventSummary(evt.payload)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {isLatestTurn && s.status === "completed" && s.runId && !datasetRemoved && s.followUps.length > 0 && (
          <section aria-label="Suggested follow-up questions" className="space-y-2 border-t border-border pt-4">
            <p className="text-xs text-secondary">Ask next</p>
            <ul className="flex flex-wrap gap-2">
              {s.followUps.map((suggestion) => (
                <li key={suggestion}>
                  <button
                    type="button"
                    disabled={submitting}
                    onClick={() => onAskSuggestion(suggestion, s)}
                    className={cx(outlineCls, "min-h-[32px] rounded-md px-2.5 py-1.5 text-left text-base text-ink disabled:opacity-50")}
                  >
                    {suggestion}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
      </section>
    </article>
  );
}
