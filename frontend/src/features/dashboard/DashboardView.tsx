"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "@/lib/toast";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, errorMessage } from "@/lib/api/client";
import { EXPORT_FORMATS, downloadAnswerExport, type ExportFormat } from "@/lib/exportAnswer";
import { anyText, useUrlState } from "@/lib/url";
import { ActionMenu } from "@/components/ActionMenu";
import { AnswerCharts } from "@/components/AnswerCharts";
import { ConnectorLogo, logoIdForFamily } from "@/components/ConnectorLogo";
import { Icon } from "@/components/Icon";
import { PageHeader } from "@/components/PageHeader";
import { RichSelect, RichOption } from "@/components/RichSelect";
import { Badge, EmptyState, FilterChips, Notice, SearchInput, Skeleton, Spinner, btn, btnSm, cardCls, cx, dangerConfirmBtnCls, inputCls } from "@/components/ui";
import { datasetLabel, relativeTime, useHealth, useServerDown, useWorkspace } from "@/lib/workspace";
import type { PinOut, UpdatePinRequest } from "@/lib/api/types";
import { excerptMarkdown, headlineFigure } from "@/lib/text";

type PinItem = PinOut;

const ACTIVE_RUN = new Set(["queued", "running", "awaiting_input"]);
const isUpdating = (pin: PinItem): boolean => Boolean(pin.latest_run_status && ACTIVE_RUN.has(pin.latest_run_status));

interface PinRefreshBatch {
  refreshed: { pin_id: string; refresh_run_id: string }[];
  failed: { pin_id: string; detail: string }[];
}


const DIRECTION_OPTIONS: RichOption[] = [
  { value: "above", label: "Goes above", sublabel: "Flag the card when the headline figure is higher" },
  { value: "below", label: "Goes below", sublabel: "Flag the card when the headline figure is lower" },
];

const formatNumber = (value: number): string => new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(value);

/** "in 5 h" for a time that has not come yet; "soon" once it is due. */
function timeUntil(iso: string): string {
  const minutes = Math.round((Date.parse(iso) - Date.now()) / 60000);
  if (!Number.isFinite(minutes) || minutes < 1) return "soon";
  if (minutes < 60) return `in ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `in ${hours} h`;
  return `in ${Math.round(hours / 24)} d`;
}

type WindowFilter = "any" | "7d" | "30d";
const isWindowFilter = (value: string): value is WindowFilter => value === "any" || value === "7d" || value === "30d";

const isSort = (value: string): value is string => SORT_OPTIONS.some((o) => o.value === value);

const SORT_OPTIONS: RichOption[] = [
  { value: "newest", label: "Newest first", sublabel: "Most recently pinned at the top" },
  { value: "oldest", label: "Oldest first", sublabel: "Longest-standing pins at the top" },
  { value: "title", label: "Title A–Z", sublabel: "Alphabetical by pin title" },
];

export function DashboardView() {
  const ws = useWorkspace();
  const qc = useQueryClient();
  const health = useHealth();
  const maxPinTitleChars = health.data?.input_limits?.max_pin_title_chars;
  const pinsQuery = useQuery({
    queryKey: ["pins", ws.id],
    queryFn: () => apiFetch<{ pins: PinItem[] }>(ws.path("/pins")),
    // Poll only while a re-run is in flight, so its answer appears when it finishes.
    refetchInterval: (query) => ((query.state.data?.pins ?? []).some(isUpdating) ? 5000 : false),
  });
  const serverDown = useServerDown();

  const [sort, setSort] = useUrlState("sort", "newest", isSort);
  const [query, setQuery] = useUrlState("q", "", anyText);
  const [windowFilter, setWindowFilter] = useUrlState<WindowFilter>("window", "any", isWindowFilter);
  const [dataset, setDataset] = useUrlState("dataset", "all", anyText);
  const [busyPin, setBusyPin] = useState<string | null>(null);
  const [autoRefreshPin, setAutoRefreshPin] = useState<string | null>(null);
  const [exportingPin, setExportingPin] = useState<string | null>(null);
  const [renamingPin, setRenamingPin] = useState<string | null>(null);
  const [renameTitle, setRenameTitle] = useState("");
  const [confirmUnpin, setConfirmUnpin] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [rerunningAll, setRerunningAll] = useState(false);
  const [alertPin, setAlertPin] = useState<string | null>(null);
  const [alertDirection, setAlertDirection] = useState<"above" | "below">("above");
  const [alertValue, setAlertValue] = useState("");
  const [alertError, setAlertError] = useState<string | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const cancelUnpinRef = useRef<HTMLButtonElement>(null);
  const alertValueRef = useRef<HTMLInputElement>(null);
  const renameInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (renamingPin) {
      const id = window.requestAnimationFrame(() => renameInputRef.current?.focus());
      return () => window.cancelAnimationFrame(id);
    }
  }, [renamingPin]);

  useEffect(() => {
    if (confirmUnpin) cancelUnpinRef.current?.focus();
  }, [confirmUnpin]);

  const pins = useMemo(() => pinsQuery.data?.pins ?? [], [pinsQuery.data]);
  async function setAutoRefresh(pin: PinItem, enabled: boolean) {
    setActionError(null);
    setAutoRefreshPin(pin.pin_id);
    try {
      await apiFetch(ws.path(`/pins/${encodeURIComponent(pin.pin_id)}`), { method: "PATCH", body: JSON.stringify({ auto_refresh: enabled } satisfies UpdatePinRequest) });
      await qc.invalidateQueries({ queryKey: ["pins", ws.id] });
      toast(enabled ? "This answer will re-run when newer data is approved." : "Automatic re-runs turned off.");
    } catch (err) {
      setActionError(errorMessage(err, "Couldn't change this pin."));
    } finally {
      setAutoRefreshPin(null);
    }
  }
  async function updatePin(pin: PinItem, body: UpdatePinRequest, message: string): Promise<boolean> {
    setActionError(null);
    setAutoRefreshPin(pin.pin_id);
    try {
      await apiFetch(ws.path(`/pins/${encodeURIComponent(pin.pin_id)}`), { method: "PATCH", body: JSON.stringify(body) });
      await qc.invalidateQueries({ queryKey: ["pins", ws.id] });
      toast(message);
      return true;
    } catch (err) {
      setActionError(errorMessage(err, "Couldn't change this pin."));
      return false;
    } finally {
      setAutoRefreshPin(null);
    }
  }
  function openAlertForm(pin: PinItem) {
    setAlertDirection(pin.alert?.direction === "below" ? "below" : "above");
    setAlertValue(pin.alert ? String(pin.alert.value) : "");
    setAlertError(null);
    setAlertPin(pin.pin_id);
    window.requestAnimationFrame(() => alertValueRef.current?.focus());
  }
  async function saveAlert(pin: PinItem) {
    const value = Number(alertValue);
    if (alertValue.trim() === "" || !Number.isFinite(value)) {
      setAlertError("Enter a numeric threshold value.");
      alertValueRef.current?.focus();
      return;
    }
    setAlertError(null);
    const saved = await updatePin(pin, { alert: { direction: alertDirection, value } }, "Alert saved. The card is flagged whenever the figure crosses it.");
    if (saved) setAlertPin(null);
  }
  const datasetOptions: RichOption[] = useMemo(() => {
    const seen = new Map<string, RichOption>();
    for (const p of pins) {
      const id = p.shown_run?.snapshot_id;
      if (!id || seen.has(id)) continue;
      const snap = p.snapshot;
      seen.set(
        id,
        snap
          ? {
              value: id,
              label: `${datasetLabel(snap)} · v${snap.version}`,
              sublabel: `${snap.total_rows.toLocaleString()} rows · ${snap.tables.length} table${snap.tables.length === 1 ? "" : "s"}`,
              icon: <ConnectorLogo id={logoIdForFamily(snap.source_family) ?? "file"} />,
              badge: p.newer_version != null ? "Older" : undefined,
            }
          : { value: id, label: "Removed dataset", sublabel: "The dataset this answer used was deleted" }
      );
    }
    return [{ value: "all", label: "All datasets", sublabel: "Pins from every dataset" }, ...seen.values()];
  }, [pins]);

  const q = query.trim().toLowerCase();
  const cutoff = windowFilter === "7d" ? Date.now() - 7 * 864e5 : windowFilter === "30d" ? Date.now() - 30 * 864e5 : null;
  const visible = pins
    .filter((p) => !q || `${p.title} ${p.question_text}`.toLowerCase().includes(q))
    .filter((p) => cutoff === null || Date.parse(p.created_at) >= cutoff)
    .filter((p) => dataset === "all" || p.shown_run?.snapshot_id === dataset)
    .sort((a, b) =>
      sort === "title"
        ? a.title.localeCompare(b.title)
        : sort === "oldest"
          ? a.created_at.localeCompare(b.created_at)
          : b.created_at.localeCompare(a.created_at)
    );
  /** A pin is stale when its dataset has a newer approved version and no re-run is already in flight. */
  const isStale = (pin: PinItem): boolean => pin.newer_version != null && !isUpdating(pin);
  const stalePins = pins.filter(isStale);
  const staleIds = new Set(stalePins.map((p) => p.pin_id));
  // Answers that need attention lead; the chosen sort still applies within each group.
  visible.sort((a, b) => Number(staleIds.has(b.pin_id)) - Number(staleIds.has(a.pin_id)));

  async function rerunAll() {
    setRerunningAll(true);
    setActionError(null);
    try {
      const res = await apiFetch<PinRefreshBatch>(ws.path("/pins/refresh"), { method: "POST", body: JSON.stringify({}) });
      if (res.failed.length > 0) setActionError(`Couldn't re-run ${res.failed.length} of ${res.refreshed.length + res.failed.length} pinned answers.`);
      else toast(`Re-running ${res.refreshed.length} pinned answer${res.refreshed.length === 1 ? "" : "s"} on the newest data.`);
    } catch (err) {
      setActionError(errorMessage(err, "Couldn't re-run the pinned answers."));
    }
    await qc.invalidateQueries({ queryKey: ["pins", ws.id] });
    setRerunningAll(false);
  }

  const filtersActive = Boolean(q) || windowFilter !== "any" || dataset !== "all";
  const activeFilterCount = Number(windowFilter !== "any") + Number(dataset !== "all") + Number(sort !== "newest");

  async function rerun(pin: PinItem) {
    setBusyPin(pin.pin_id);
    setActionError(null);
    try {
      const res = await apiFetch<{ pin_id: string; refresh_run_id: string; status?: string }>(
        ws.path(`/pins/${encodeURIComponent(pin.pin_id)}/refresh`),
        { method: "POST" },
      );
      qc.setQueryData<{ pins: PinItem[] }>(["pins", ws.id], (old) =>
        old
          ? {
              pins: old.pins.map((p) =>
                p.pin_id === pin.pin_id
                  ? { ...p, latest_run_id: res.refresh_run_id, latest_run_status: res.status || "queued" }
                  : p,
              ),
            }
          : old,
      );
      toast(`Re-running "${pin.title}" against the latest imported snapshot.`);
      await qc.invalidateQueries({ queryKey: ["pins", ws.id] });
      await qc.invalidateQueries({ queryKey: ["runs", ws.id] });
    } catch (err) {
      setActionError(errorMessage(err, "Couldn't re-run this question."));
    } finally {
      setBusyPin(null);
    }
  }

  function openRename(pin: PinItem) {
    setRenamingPin(pin.pin_id);
    setRenameTitle(pin.title);
  }

  async function saveRename(pin: PinItem) {
    const clean = renameTitle.trim();
    if (!clean) {
      setActionError("Enter a title for this pin.");
      return;
    }
    const ok = await updatePin(pin, { title: clean }, "Pin renamed.");
    if (ok) setRenamingPin(null);
  }

  async function exportPin(pin: PinItem, runId: string, format: ExportFormat) {
    setExportingPin(pin.pin_id);
    setActionError(null);
    try {
      await downloadAnswerExport({ workspaceId: ws.id, runId, format, title: pin.title });
    } catch (err) {
      setActionError(errorMessage(err, "Couldn't export this pinned answer."));
    } finally {
      setExportingPin(null);
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
      toast("Answer unpinned.");
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

  const loadError = pinsQuery.error;

  if (serverDown) {
    return <PageHeader title="Dashboard" description="Answers you pinned, the dataset each one used, and whether newer data has arrived since." />;
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Dashboard"
        description="Answers you pinned, the dataset each one used, and whether newer data has arrived since."
        action={
          pins.length > 0 ? (
            // Phones reach Ask from the tab bar, so the button would only push the pins further down.
            <Link href="/ask" className={cx(btn.primary, "max-sm:hidden")}>
              Ask a question
            </Link>
          ) : undefined
        }
      />

      {pins.length > 1 && (
        <div className="flex flex-col gap-3 lg:flex-row lg:flex-wrap lg:items-center">
          <div className="flex gap-2 lg:contents">
            <SearchInput dense className="flex-1 lg:w-[280px] lg:flex-none" label="Search pinned answers" placeholder="Search pinned answers" value={query} onChange={setQuery} />
            {/* Below desktop width the filters fold behind one button so the pins start near the top. */}
            <button
              type="button"
              className={cx(btn.secondary, "lg:hidden")}
              aria-expanded={filtersOpen}
              aria-controls="pin-filters"
              onClick={() => setFiltersOpen((open) => !open)}
            >
              Filters
              {activeFilterCount > 0 && <span className="tabular-nums text-secondary">· {activeFilterCount}</span>}
              <Icon name="chevron-down" className={cx("h-3.5 w-3.5 transition-transform", filtersOpen && "rotate-180")} />
            </button>
          </div>
          <div id="pin-filters" className={cx(filtersOpen ? "flex animate-fade-in" : "hidden", "flex-col gap-3 lg:contents")}>
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
              <RichSelect dense ariaLabel="Dataset" value={dataset} options={datasetOptions} onChange={setDataset} />
            </div>
            <div className="min-w-0 lg:w-[170px]">
              <RichSelect dense ariaLabel="Sort pins" value={sort} options={SORT_OPTIONS} onChange={setSort} />
            </div>
          </div>
          </div>
        </div>
      )}

      {actionError && <Notice tone="err" title={actionError} />}

      {stalePins.length > 1 && (
        <Notice
          tone="warn"
          title={`${stalePins.length} pinned answers have newer data`}
          action={
            <button type="button" className={btn.secondary} disabled={rerunningAll} onClick={() => void rerunAll()}>
              {rerunningAll && <Spinner />}
              Re-run all
            </button>
          }
        >
          They still show figures from an older dataset version.
        </Notice>
      )}

      {loadError && (
        <Notice
          tone="err"
          title="Couldn't load the dashboard"
          action={
            <button
              type="button"
              className={btn.secondary}
              onClick={() => void pinsQuery.refetch()}
            >
              Try again
            </button>
          }
        >
          {errorMessage(loadError)}
        </Notice>
      )}

      {pinsQuery.isLoading && (
        <div className="space-y-3" aria-live="polite">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
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
            const run = pin.shown_run ?? undefined;
            const updating = isUpdating(pin);
            const latestFailed = pin.latest_run_status === "failed";
            const snap = pin.snapshot ?? undefined;
            const newer = pin.newer_version ?? null;
            const text = excerptMarkdown(run?.answer?.summary_markdown ?? undefined);
            const headline = run?.answer?.summary_markdown ? headlineFigure(run.answer.summary_markdown) : null;
            const busy = busyPin === pin.pin_id;
            const canRefresh = Boolean(snap);
            const schedule = pin.schedule === "daily" || pin.schedule === "weekly" ? pin.schedule : null;
            const saving = autoRefreshPin === pin.pin_id;
            const exporting = exportingPin === pin.pin_id;
            const exportRunId = run?.status === "completed" ? run.run_id : null;
            const menuActions = [
              { label: "Rename title…", onSelect: () => openRename(pin) },
              // Kept while the dataset is gone only so an existing setting can still be switched off.
              ...(canRefresh || pin.auto_refresh
                ? [
                    {
                      label: "Auto re-run on new data",
                      hint: "Uses your newest imported snapshot, not live sources",
                      checked: Boolean(pin.auto_refresh),
                      separatorAbove: true,
                      onSelect: () => void setAutoRefresh(pin, !pin.auto_refresh),
                    },
                  ]
                : []),
              ...(canRefresh && schedule !== "daily"
                ? [{ label: "Re-run every day", onSelect: () => void updatePin(pin, { schedule: "daily" }, "Scheduled daily against latest imported data.") }]
                : []),
              ...(canRefresh && schedule !== "weekly"
                ? [{ label: "Re-run every week", onSelect: () => void updatePin(pin, { schedule: "weekly" }, "Scheduled weekly against latest imported data.") }]
                : []),
              ...(schedule ? [{ label: "Stop scheduled re-runs", onSelect: () => void updatePin(pin, { schedule: "off" }, "Scheduled re-runs turned off.") }] : []),
              { label: pin.alert ? "Change alert…" : "Set alert…", separatorAbove: true, onSelect: () => openAlertForm(pin) },
              ...(pin.alert ? [{ label: "Remove alert", onSelect: () => void updatePin(pin, { clear_alert: true }, "Alert removed.") }] : []),
              ...(exportRunId
                ? EXPORT_FORMATS.map((fmt, idx) => ({
                    label: fmt.label,
                    separatorAbove: idx === 0,
                    onSelect: () => void exportPin(pin, exportRunId, fmt),
                  }))
                : []),
              { label: "Unpin", danger: true, separatorAbove: true, onSelect: () => setConfirmUnpin(pin.pin_id) },
            ];
            return (
              <li key={pin.pin_id} className={cx(cardCls, "flex flex-col gap-3 p-5")}>
                <div className="flex flex-wrap items-start justify-between gap-2">
                  {renamingPin === pin.pin_id ? (
                    <form
                      className="flex min-w-0 flex-1 flex-wrap items-center gap-2"
                      onSubmit={(e) => {
                        e.preventDefault();
                        void saveRename(pin);
                      }}
                    >
                      <input
                        ref={renameInputRef}
                        aria-label={`Rename ${pin.title}`}
                        value={renameTitle}
                        onChange={(e) => setRenameTitle(e.target.value)}
                        maxLength={maxPinTitleChars}
                        className={cx(inputCls, "min-w-[160px] flex-1")}
                      />
                      <button type="submit" disabled={!renameTitle.trim() || saving} className={btnSm.primary}>
                        Save
                      </button>
                      <button type="button" onClick={() => setRenamingPin(null)} disabled={saving} className={btnSm.ghost}>
                        Cancel
                      </button>
                    </form>
                  ) : (
                    // The title opens the answer, so the card needs no separate Open button.
                    <h2 className="min-w-0 flex-1 text-md font-semibold leading-snug text-ink">
                      <Link
                        href={`/ask?run=${encodeURIComponent(run?.run_id ?? pin.run_id)}`}
                        className="rounded-sm decoration-border-strong underline-offset-4 hover:underline"
                      >
                        {pin.title}
                      </Link>
                    </h2>
                  )}
                  {updating ? (
                    <Badge tone="info" dot pulse>
                      Updating
                    </Badge>
                  ) : latestFailed ? (
                    <Badge tone="warn" dot>
                      Re-run failed
                    </Badge>
                  ) : !snap && run ? (
                    <Badge tone="warn" dot>
                      Dataset removed
                    </Badge>
                  ) : newer ? (
                    <Badge tone="warn" dot>
                      Newer data: v{newer}
                    </Badge>
                  ) : null}
                </div>
                {pin.question_text.trim() !== pin.title.trim() && <p className="text-sm leading-relaxed text-secondary">{pin.question_text}</p>}
                {headline && <p className="text-[26px] font-semibold leading-tight tracking-tight text-ink tabular-nums">{headline}</p>}
                <AnswerCharts charts={run?.answer?.charts} small contextTitle={pin.title} />
                <p className={cx("text-base leading-relaxed", text ? "text-body" : "text-secondary")}>
                  {text || "The saved answer isn't available."}
                </p>
                {latestFailed && <p className="text-xs text-err">The latest re-run failed; showing the earlier answer.</p>}
                {(schedule || pin.auto_refresh || pin.alert) && (
                  <div
                    className="flex flex-wrap items-center gap-1.5"
                    title={
                      schedule || pin.auto_refresh
                        ? "Re-runs query your latest imported snapshot; they don't pull from external sources."
                        : undefined
                    }
                  >
                    {schedule && (
                      <Badge tone="neutral">
                        Re-runs {schedule}
                        {pin.next_run_at ? ` · next ${timeUntil(pin.next_run_at)}` : ""}
                      </Badge>
                    )}
                    {pin.auto_refresh && <Badge tone="neutral">Re-runs on new data</Badge>}
                    {pin.alert && (
                      <Badge tone={pin.alert_triggered ? "err" : "neutral"} dot={Boolean(pin.alert_triggered)}>
                        {pin.alert_triggered ? "Alert: " : "Alert when "}
                        {pin.alert.direction} {formatNumber(pin.alert.value)}
                        {pin.alert_triggered == null ? " · no figure to compare" : ""}
                      </Badge>
                    )}
                  </div>
                )}
                {/* One footer row: where the figures came from on the left, the two actions on the right. */}
                <div className="mt-auto flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-t border-border pt-3">
                  {/* On phones the source line always takes its own row, so every card wraps the same way. */}
                  <p className="min-w-0 text-xs text-secondary max-sm:basis-full">
                    {snap ? `${datasetLabel(snap)} v${snap.version} · ` : run ? "Removed dataset · " : ""}
                    {run && run.run_id !== pin.run_id && run.created_at ? `updated ${relativeTime(run.created_at)} · ` : ""}
                    pinned {relativeTime(pin.created_at)}
                  </p>
                  {confirmUnpin === pin.pin_id ? (
                    <span className="flex flex-wrap items-center gap-1.5">
                      <span className="text-sm text-body">Unpin this answer?</span>
                      <button
                        type="button"
                        className={dangerConfirmBtnCls}
                        onClick={() => void unpin(pin)}
                        disabled={busy}
                        aria-label={`Confirm unpin ${pin.title}`}
                      >
                        {busy && <Spinner />}
                        Unpin
                      </button>
                      <button
                        ref={cancelUnpinRef}
                        type="button"
                        className={btn.ghost}
                        onClick={() => {
                          setConfirmUnpin(null);
                          window.requestAnimationFrame(() =>
                            document.getElementById(`pin-actions-${pin.pin_id}`)?.querySelector<HTMLButtonElement>("[aria-haspopup='menu']")?.focus()
                          );
                        }}
                        disabled={busy}
                      >
                        Cancel
                      </button>
                    </span>
                  ) : (
                    <span id={`pin-actions-${pin.pin_id}`} className="ml-auto flex flex-wrap items-center gap-1.5">
                      <button
                        type="button"
                        className={newer ? btn.primary : btn.ghost}
                        onClick={() => void rerun(pin)}
                        disabled={busy || !canRefresh}
                        aria-label={`${newer ? "Re-run on new data" : "Re-run"} ${pin.title}`}
                        title={!canRefresh ? "Dataset was deleted" : newer ? `Ask again using v${newer}` : "Ask the same question again"}
                      >
                        {busy && <Spinner />}
                        {newer ? "Re-run on new data" : "Re-run"}
                      </button>
                      <ActionMenu
                        menuLabel={`More actions for ${pin.title}`}
                        triggerText={exporting ? "Exporting…" : "More"}
                        disabled={busy || saving || exporting}
                        actions={menuActions}
                      />
                    </span>
                  )}
                </div>
                {alertPin === pin.pin_id && (
                  <form
                    noValidate
                    className="flex flex-wrap items-end gap-2 border-t border-border pt-3"
                    aria-label={`Alert for ${pin.title}`}
                    onSubmit={(e) => {
                      e.preventDefault();
                      void saveAlert(pin);
                    }}
                  >
                    <div className="w-44">
                      <RichSelect
                        label="Flag this card when it"
                        value={alertDirection}
                        options={DIRECTION_OPTIONS}
                        onChange={(v) => setAlertDirection(v === "below" ? "below" : "above")}
                      />
                    </div>
                    <label className="flex min-w-[8rem] flex-1 flex-col gap-1 text-sm text-body">
                      Value
                      <input
                        ref={alertValueRef}
                        type="number"
                        step="any"
                        inputMode="decimal"
                        required
                        aria-invalid={Boolean(alertError)}
                        value={alertValue}
                        onChange={(e) => {
                          setAlertValue(e.target.value);
                          if (alertError) setAlertError(null);
                        }}
                        placeholder={pin.headline_value != null ? `Now ${formatNumber(pin.headline_value)}` : "For example 5000"}
                        className={cx(inputCls, "w-full min-w-0", alertError && "border-err")}
                      />
                    </label>
                    <button type="submit" className={btn.primary} disabled={saving || alertValue.trim() === ""}>
                      {saving && <Spinner />}
                      Save alert
                    </button>
                    <button type="button" className={btn.ghost} onClick={() => setAlertPin(null)}>
                      Cancel
                    </button>
                    {alertError && (
                      <p role="alert" className="basis-full text-xs text-err">
                        {alertError}
                      </p>
                    )}
                    <p className="basis-full text-xs text-secondary">
                      {pin.headline_value != null
                        ? "Compared with the headline figure each time this answer re-runs."
                        : "This answer has no headline figure yet, so the alert can't be checked until a re-run produces one."}
                    </p>
                  </form>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
