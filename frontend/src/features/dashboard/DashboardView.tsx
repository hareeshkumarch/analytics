"use client";

import React, { useCallback, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, errorMessage } from "@/lib/api/client";
import { anyText, useUrlState } from "@/lib/url";
import { AnswerCharts } from "@/components/AnswerCharts";
import { PageHeader } from "@/components/PageHeader";
import { RichSelect, RichOption } from "@/components/RichSelect";
import { Badge, EmptyState, FilterChips, Notice, SearchInput, Skeleton, Spinner, btn, cardCls, cx } from "@/components/ui";
import { SnapshotSummary, datasetLabel, relativeTime, useSnapshots, useWorkspace } from "@/lib/workspace";
import type { PinOut, RunOut, UpdatePinRequest } from "@/lib/api/types";
import { headlineFigure, stripEvidenceIds } from "@/lib/text";

type PinItem = PinOut;

const ACTIVE_RUN = new Set(["queued", "running", "awaiting_input"]);

type RunSummary = RunOut;

type WindowFilter = "any" | "7d" | "30d";
const isWindowFilter = (value: string): value is WindowFilter => value === "any" || value === "7d" || value === "30d";

const isSort = (value: string): value is string => SORT_OPTIONS.some((o) => o.value === value);

const SORT_OPTIONS: RichOption[] = [
  { value: "newest", label: "Newest first" },
  { value: "oldest", label: "Oldest first" },
  { value: "title", label: "Title A–Z" },
];

function excerpt(markdown: string | undefined, max = 240): string {
  if (!markdown) return "";
  const text = stripEvidenceIds(markdown)
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/[*_`>|]/g, "")
    .replace(/-{3,}/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .split(/(?<=[.!?])\s/)[0];
  return text.length > max ? `${text.slice(0, max).trimEnd()}…` : text;
}

export function DashboardView() {
  const ws = useWorkspace();
  const qc = useQueryClient();
  const router = useRouter();
  const pinsQuery = useQuery({
    queryKey: ["pins", ws.id],
    queryFn: () => apiFetch<{ pins: PinItem[] }>(ws.path("/pins")),
  });
  const runsQuery = useQuery({
    queryKey: ["runs", ws.id],
    queryFn: () => apiFetch<{ runs: RunSummary[] }>(ws.path("/runs")),
    refetchInterval: (query) => {
      const runs = query.state.data?.runs ?? [];
      const latest = new Set((pinsQuery.data?.pins ?? []).map((p) => p.latest_run_id).filter(Boolean));
      return runs.some((r) => latest.has(r.run_id) && ACTIVE_RUN.has(r.status)) ? 5000 : false;
    },
  });
  const snapshotsQuery = useSnapshots();

  const [sort, setSort] = useUrlState("sort", "newest", isSort);
  const [query, setQuery] = useUrlState("q", "", anyText);
  const [windowFilter, setWindowFilter] = useUrlState<WindowFilter>("window", "any", isWindowFilter);
  const [dataset, setDataset] = useUrlState("dataset", "all", anyText);
  const [busyPin, setBusyPin] = useState<string | null>(null);
  const [autoRefreshPin, setAutoRefreshPin] = useState<string | null>(null);
  const [confirmUnpin, setConfirmUnpin] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const pins = useMemo(() => pinsQuery.data?.pins ?? [], [pinsQuery.data]);
  const pageRunsById = useMemo(() => new Map((runsQuery.data?.runs ?? []).map((r) => [r.run_id, r])), [runsQuery.data]);

  const missingRunIds = useMemo(() => {
    if (!runsQuery.isSuccess) return [];
    const needed = new Set<string>();
    for (const p of pins) {
      if (p.run_id && !pageRunsById.has(p.run_id)) needed.add(p.run_id);
      if (p.latest_run_id && !pageRunsById.has(p.latest_run_id)) needed.add(p.latest_run_id);
    }
    return Array.from(needed);
  }, [pins, pageRunsById, runsQuery.isSuccess]);

  const extraRunQueries = useQueries({
    queries: missingRunIds.map((runId) => ({
      queryKey: ["run", ws.id, runId],
      queryFn: () => apiFetch<RunSummary>(ws.path(`/runs/${encodeURIComponent(runId)}`)),
      staleTime: 60_000,
    })),
  });

  const runsById = useMemo(() => {
    const map = new Map(pageRunsById);
    for (const q of extraRunQueries) {
      if (q.data) map.set(q.data.run_id, q.data);
    }
    return map;
  }, [pageRunsById, extraRunQueries]);

  const snapshots = useMemo(() => snapshotsQuery.data?.snapshots ?? [], [snapshotsQuery.data]);
  const snapshotById = useMemo(() => new Map(snapshots.map((s) => [s.snapshot_id, s])), [snapshots]);

  /** Newest approved snapshot of the same source, if it is newer than the one the pin was answered from. */
  function newerVersion(snap: SnapshotSummary | undefined): SnapshotSummary | null {
    if (!snap) return null;
    const newer = snapshots.find((s) => s.source_id === snap.source_id && s.approved && s.version > snap.version);
    return newer ?? null;
  }

  const shownRun = useCallback(
    (pin: PinItem): RunSummary | undefined => {
      const latest = pin.latest_run_id ? runsById.get(pin.latest_run_id) : undefined;
      return latest?.status === "completed" ? latest : runsById.get(pin.run_id);
    },
    [runsById]
  );

  async function setAutoRefresh(pin: PinItem, enabled: boolean) {
    setActionError(null);
    setAutoRefreshPin(pin.pin_id);
    try {
      await apiFetch(ws.path(`/pins/${encodeURIComponent(pin.pin_id)}`), { method: "PATCH", body: JSON.stringify({ auto_refresh: enabled } satisfies UpdatePinRequest) });
      await qc.invalidateQueries({ queryKey: ["pins", ws.id] });
    } catch (err) {
      setActionError(errorMessage(err, "Couldn't change this pin."));
    } finally {
      setAutoRefreshPin(null);
    }
  }
  const datasetOptions: RichOption[] = useMemo(() => {
    const ids = Array.from(new Set(pins.map((p) => shownRun(p)?.snapshot_id).filter((id): id is string => Boolean(id))));
    return [
      { value: "all", label: "All datasets" },
      ...ids.map((id) => {
        const s = snapshotById.get(id);
        return { value: id, label: s ? `${datasetLabel(s)} · v${s.version}` : "Removed dataset" };
      }),
    ];
  }, [pins, shownRun, snapshotById]);

  const q = query.trim().toLowerCase();
  const cutoff = windowFilter === "7d" ? Date.now() - 7 * 864e5 : windowFilter === "30d" ? Date.now() - 30 * 864e5 : null;
  const visible = pins
    .filter((p) => !q || `${p.title} ${p.question_text}`.toLowerCase().includes(q))
    .filter((p) => cutoff === null || Date.parse(p.created_at) >= cutoff)
    .filter((p) => dataset === "all" || shownRun(p)?.snapshot_id === dataset)
    .sort((a, b) =>
      sort === "title"
        ? a.title.localeCompare(b.title)
        : sort === "oldest"
          ? a.created_at.localeCompare(b.created_at)
          : b.created_at.localeCompare(a.created_at)
    );
  const filtersActive = Boolean(q) || windowFilter !== "any" || dataset !== "all";

  async function rerun(pin: PinItem) {
    setBusyPin(pin.pin_id);
    setActionError(null);
    try {
      const res = await apiFetch<{ refresh_run_id: string }>(ws.path(`/pins/${encodeURIComponent(pin.pin_id)}/refresh`), { method: "POST" });
      await qc.invalidateQueries({ queryKey: ["runs", ws.id] });
      router.push(`/ask?run=${encodeURIComponent(res.refresh_run_id)}`);
    } catch (err) {
      setActionError(errorMessage(err, "Couldn't re-run this question."));
      setBusyPin(null);
    }
  }

  async function unpin(pin: PinItem) {
    if (confirmUnpin !== pin.pin_id) {
      setConfirmUnpin(pin.pin_id);
      return;
    }
    setBusyPin(pin.pin_id);
    setActionError(null);
    try {
      await apiFetch(ws.path(`/pins/${encodeURIComponent(pin.pin_id)}`), { method: "DELETE" });
      qc.setQueryData<{ pins: PinItem[] }>(["pins", ws.id], (old) => (old ? { pins: old.pins.filter((p) => p.pin_id !== pin.pin_id) } : old));
    } catch (err) {
      setActionError(errorMessage(err, "Couldn't unpin this answer."));
    } finally {
      setBusyPin(null);
      setConfirmUnpin(null);
    }
  }

  const nowMs = Date.now();
  const windowCounts = {
    any: pins.length,
    d7: pins.filter((p) => Date.parse(p.created_at) >= nowMs - 7 * 864e5).length,
    d30: pins.filter((p) => Date.parse(p.created_at) >= nowMs - 30 * 864e5).length,
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Dashboard"
        description="Answers you pinned, the dataset each one used, and whether newer data has arrived since."
        action={
          pins.length > 0 ? (
            <Link href="/ask" className={btn.primary}>
              Ask a question
            </Link>
          ) : undefined
        }
      />

      {pins.length > 1 && (
        <div className="flex flex-col gap-3 lg:flex-row lg:flex-wrap lg:items-center">
          <SearchInput className="lg:w-[280px]" label="Search pinned answers" placeholder="Search pinned answers" value={query} onChange={setQuery} />
          <FilterChips
            label="Pinned within"
            value={windowFilter}
            onChange={setWindowFilter}
            options={[
              { value: "any", label: "Any time", count: windowCounts.any },
              { value: "7d", label: "Last 7 days", count: windowCounts.d7 },
              { value: "30d", label: "Last 30 days", count: windowCounts.d30 },
            ]}
          />
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:flex">
            <div className="min-w-0 lg:w-[220px]">
              <RichSelect compact ariaLabel="Dataset" value={dataset} options={datasetOptions} onChange={setDataset} />
            </div>
            <div className="min-w-0 lg:w-[170px]">
              <RichSelect compact ariaLabel="Sort pins" value={sort} options={SORT_OPTIONS} onChange={setSort} />
            </div>
          </div>
        </div>
      )}

      {actionError && <Notice tone="err" title={actionError} />}

      {runsQuery.error && (
        <Notice tone="err" title="Couldn't load saved answers" action={<button type="button" className={btn.secondary} onClick={() => void runsQuery.refetch()}>Try again</button>}>
          {errorMessage(runsQuery.error)}
        </Notice>
      )}
      {snapshotsQuery.error && (
        <Notice tone="err" title="Couldn't load datasets" action={<button type="button" className={btn.secondary} onClick={() => void snapshotsQuery.refetch()}>Try again</button>}>
          {errorMessage(snapshotsQuery.error)}
        </Notice>
      )}

      {pinsQuery.isLoading && (
        <div className="space-y-3" aria-live="polite">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
      )}

      {pinsQuery.error && (
        <Notice tone="err" title="Couldn't load pinned answers" action={<button type="button" className={btn.secondary} onClick={() => void pinsQuery.refetch()}>Try again</button>}>
          {errorMessage(pinsQuery.error)}
        </Notice>
      )}

      {!pinsQuery.isLoading && !pinsQuery.error && pins.length === 0 && (
        <EmptyState
          title="Nothing pinned yet"
          body="Ask a question, then use Pin to Dashboard on an answer worth keeping. Each pin remembers the dataset version it used."
          action={
            <Link href="/ask" className={btn.primary}>
              Ask a question
            </Link>
          }
        />
      )}

      {pins.length > 0 && visible.length === 0 && (
        <EmptyState
          title="No pinned answers match"
          body="No pinned answers match the current search or filter settings."
          action={
            filtersActive ? (
              <button
                type="button"
                className={btn.secondary}
                onClick={() => {
                  setQuery("");
                  setWindowFilter("any");
                  setDataset("all");
                }}
              >
                Clear filters
              </button>
            ) : undefined
          }
        />
      )}

      {visible.length > 0 && (
        <ul className="grid gap-3 xl:grid-cols-2">
          {visible.map((pin) => {
            const run = shownRun(pin);
            const latest = pin.latest_run_id ? runsById.get(pin.latest_run_id) : undefined;
            const updating = Boolean(latest && ACTIVE_RUN.has(latest.status));
            const latestFailed = latest?.status === "failed";
            const snap = run?.snapshot_id ? snapshotById.get(run.snapshot_id) : undefined;
            const newer = newerVersion(snap);
            const text = excerpt(run?.answer?.summary_markdown ?? undefined);
            const headline = run?.answer?.summary_markdown ? headlineFigure(run.answer.summary_markdown) : null;
            const busy = busyPin === pin.pin_id;
            const missingIdx = missingRunIds.indexOf(pin.run_id);
            const loadingRun = runsQuery.isLoading || (missingIdx >= 0 && Boolean(extraRunQueries[missingIdx]?.isLoading));
            const canRefresh = Boolean(snap);
            return (
              <li key={pin.pin_id} className={cx(cardCls, "flex flex-col gap-3 p-5")}>
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <h2 className="min-w-0 flex-1 font-display text-[15.5px] font-semibold leading-snug text-ink">{pin.title}</h2>
                  {updating ? (
                    <Badge tone="info" dot pulse>
                      Updating
                    </Badge>
                  ) : latestFailed ? (
                    <Badge tone="warn" dot>
                      Re-run failed
                    </Badge>
                  ) : !snap && run && snapshotsQuery.isSuccess ? (
                    <Badge tone="warn" dot>
                      Dataset removed
                    </Badge>
                  ) : newer ? (
                    <Badge tone="warn" dot>
                      Newer data: v{newer.version}
                    </Badge>
                  ) : snap ? (
                    <Badge tone="ok" dot>
                      Latest data
                    </Badge>
                  ) : null}
                </div>
                {pin.question_text.trim() !== pin.title.trim() && <p className="text-[13px] leading-relaxed text-secondary">{pin.question_text}</p>}
                {headline && <p className="font-display text-[26px] font-semibold leading-tight tracking-tight text-ink tabular-nums">{headline}</p>}
                <p className={cx("text-[13.5px] leading-relaxed", text ? "text-body" : "text-secondary")}>
                  {text || (loadingRun ? "Loading the answer…" : "The saved answer isn't available.")}
                </p>
                {latestFailed && <p className="text-xs text-err">The latest re-run failed; showing the earlier answer.</p>}
                <AnswerCharts charts={run?.answer?.charts} small contextTitle={pin.title} />
                <label className={cx("flex min-h-[40px] cursor-pointer items-center gap-2 text-[12.5px]", canRefresh ? "text-body" : "cursor-not-allowed text-secondary opacity-60")}>
                  <input
                    type="checkbox"
                    className="h-4 w-4 accent-plum"
                    checked={Boolean(pin.auto_refresh)}
                    disabled={!canRefresh || autoRefreshPin === pin.pin_id}
                    onChange={(e) => void setAutoRefresh(pin, e.target.checked)}
                  />
                  Re-run automatically when newer data is approved
                </label>
                <div className="mt-auto flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3">
                  <span className="text-[12px] text-secondary">
                    {snap ? `${datasetLabel(snap)} v${snap.version} · ` : run && snapshotsQuery.isSuccess ? "Removed dataset · " : ""}
                    {run && run.run_id !== pin.run_id && run.created_at ? `updated ${relativeTime(run.created_at)} · ` : ""}
                    pinned {relativeTime(pin.created_at)}
                  </span>
                  <span className="flex flex-wrap gap-1.5">
                    <Link
                      href={`/ask?run=${encodeURIComponent(run?.run_id ?? pin.run_id)}`}
                      aria-label={`Open ${pin.title}`}
                      className={cx(btn.secondary, "px-3")}
                    >
                      Open
                    </Link>
                    <button
                      type="button"
                      className={cx(btn.secondary, "px-3")}
                      onClick={() => void rerun(pin)}
                      disabled={busy || !canRefresh}
                      aria-label={`${newer ? "Re-run on new data" : "Re-run"} ${pin.title}`}
                      title={!canRefresh ? "Dataset was deleted" : newer ? `Ask again using v${newer.version}` : "Ask the same question again"}
                    >
                      {busy && confirmUnpin !== pin.pin_id && <Spinner />}
                      {newer ? "Re-run on new data" : "Re-run"}
                    </button>
                    <button
                      type="button"
                      className={cx(btn.ghost, confirmUnpin === pin.pin_id && "text-err hover:text-err")}
                      onClick={() => void unpin(pin)}
                      disabled={busy}
                      aria-label={confirmUnpin === pin.pin_id ? `Confirm unpin ${pin.title}` : `Unpin ${pin.title}`}
                    >
                      {busy && confirmUnpin === pin.pin_id && <Spinner />}
                      {confirmUnpin === pin.pin_id ? "Confirm unpin" : "Unpin"}
                    </button>
                    {confirmUnpin === pin.pin_id && !busy && (
                      <button
                        type="button"
                        className={btn.ghost}
                        onClick={() => setConfirmUnpin(null)}
                      >
                        Cancel
                      </button>
                    )}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
