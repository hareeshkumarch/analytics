"use client";

import React, { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { anyText, useUrlState } from "@/lib/url";
import { apiFetch, errorMessage } from "@/lib/api/client";
import { RichSelect, RichOption } from "@/components/RichSelect";
import { PageHeader } from "@/components/PageHeader";
import { DataTable, SortDir } from "@/features/data/DataTable";
import { Details, EmptyState, Skeleton, SkeletonRows, FilterChips, Notice, SearchInput, btn, btnSm, cardCls, cx, sectionTitleCls } from "@/components/ui";
import { providerLabel, relativeTime, useServerDown, useWorkspace } from "@/lib/workspace";
import type { ModelUsageOut, RunUsageOut, ToolUsageOut, UsageOut } from "@/lib/api/types";

type RunUsageItem = RunUsageOut;
type ModelUsageItem = ModelUsageOut;
type UsageResponse = UsageOut;

type WindowKey = "24h" | "7d" | "30d" | "90d" | "all";
type Metric = "tokens" | "cost" | "runs";
type RunSortKey = "when" | "calls" | "tokens" | "cost";


const UsageBarChart = dynamic(() => import("@/features/usage/UsageBarChart").then((m) => m.UsageBarChart), { ssr: false });
const REFRESH_MS = 15000;

const WINDOW_OPTIONS: { value: WindowKey; label: string }[] = [
  { value: "24h", label: "24 hours" },
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "90d", label: "90 days" },
  { value: "all", label: "All time" },
];

const HOUR_MS = 3600 * 1000;
const RUN_TABLE_ROWS = 100;
// The API returns at most this many questions per request.
const RUN_TABLE_MAX = 500;
const WINDOW_HOURS: Record<WindowKey, number | null> = { "24h": 24, "7d": 168, "30d": 720, "90d": 2160, all: null };

const statusDot = (tone: string) => <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${tone}`} />;

const STATUS_OPTIONS: RichOption[] = [
  { value: "all", label: "All statuses", sublabel: "Every question in the time window" },
  { value: "completed", label: "Completed", sublabel: "Finished with an answer", icon: statusDot("bg-ok-dot") },
  { value: "running", label: "Running", sublabel: "Being answered now", icon: statusDot("bg-warn-dot") },
  { value: "queued", label: "Queued", sublabel: "Waiting for a free slot", icon: statusDot("bg-warn-dot") },
  { value: "failed", label: "Failed", sublabel: "Stopped with an error", icon: statusDot("bg-err-dot") },
  { value: "cancelled", label: "Cancelled", sublabel: "Stopped by someone", icon: statusDot("bg-secondary") },
];

const MODE_OPTIONS: RichOption[] = [
  { value: "all", label: "All modes", sublabel: "Quick and Deep questions" },
  { value: "quick", label: "Quick", sublabel: "One focused analysis step" },
  { value: "deep", label: "Deep", sublabel: "Several steps with an evidence review" },
];

const isWindowKey = (value: string): value is WindowKey => WINDOW_OPTIONS.some((o) => o.value === value);
const isStatusFilter = (value: string): value is string => STATUS_OPTIONS.some((o) => o.value === value);
const isModeFilter = (value: string): value is string => MODE_OPTIONS.some((o) => o.value === value);
const isMetric = (value: string): value is Metric => value === "tokens" || value === "cost" || value === "runs";

const STATUS_DOT: Record<string, string> = {
  completed: "bg-ok-dot",
  failed: "bg-err-dot",
  running: "bg-warn-dot",
  queued: "bg-warn-dot",
  cancelled: "bg-secondary",
};

const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });

function formatCost(value: number): string {
  if (value === 0) return "$0";
  if (value < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(2)}`;
}

function Stat({ label, value, hint, loading }: { label: string; value: string; hint: string; loading: boolean }) {
  return (
    <div className="min-w-0 px-5 py-4">
      <dt className="text-xs font-medium text-secondary">{label}</dt>
      <dd className="mt-1.5 truncate text-[1.6rem] font-semibold leading-tight tracking-[-0.02em] tabular-nums text-ink">
        {loading ? <span className="inline-block h-7 w-16 animate-pulse rounded bg-panel align-middle" /> : value}
      </dd>
      <dd className="mt-1 truncate text-xs text-secondary">{hint}</dd>
    </div>
  );
}

function ModelUsage({
  models,
  metric,
  provider,
  onProvider,
  windowLabel,
  loading,
}: {
  models: ModelUsageItem[];
  metric: Metric;
  provider: string;
  onProvider: (value: string) => void;
  windowLabel: string;
  loading: boolean;
}) {
  const providers = Array.from(new Set(models.map((m) => m.provider_id))).sort();
  const active = providers.includes(provider) ? provider : "all";
  const shown = active === "all" ? models : models.filter((m) => m.provider_id === active);
  const value = (m: ModelUsageItem) => (metric === "cost" ? Number(m.total_cost_usd) || 0 : metric === "runs" ? m.calls : m.total_tokens);
  const rawMax = Math.max(...shown.map(value), 0);
  const max = rawMax > 0 ? rawMax : 1;
  const barLabel = metric === "cost" ? "cost" : metric === "runs" ? "model calls" : "tokens";
  return (
    <section aria-labelledby="usage-models" className={cx(cardCls, "space-y-4 p-5 sm:p-6")}>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h2 id="usage-models" className={sectionTitleCls}>
            By model
          </h2>
          <p className="mt-0.5 text-xs text-secondary">All model calls in the last {windowLabel.toLowerCase()} across all modes and statuses. Bars show {barLabel}.</p>
        </div>
        {providers.length > 1 && (
          <FilterChips
            label="Provider"
            value={active}
            onChange={onProvider}
            options={[{ value: "all", label: "All providers" }, ...providers.map((p) => ({ value: p, label: providerLabel(p) }))]}
          />
        )}
      </div>
      {loading ? (
        <SkeletonRows rows={3} />
      ) : shown.length === 0 ? (
        <EmptyState title="No model calls in this window" />
      ) : (
        <DataTable label="Usage by model" minWidth="min-w-[720px]" head={[
            "Model",
            { label: `Share of ${barLabel}`, srOnly: true, className: "w-[28%]" },
            { label: "Calls", align: "right" },
            { label: "Tokens", align: "right" },
            { label: "Cost", align: "right" },
            { label: "Avg latency", align: "right" },
          ]}>
              {shown.map((m) => (
                <tr key={`${m.provider_id}:${m.model_id}`} className="border-t border-border">
                  <td className="px-3.5 py-3">
                    <span className="block font-medium text-ink">{m.model_id}</span>
                    <span className="block text-xs text-secondary">{providerLabel(m.provider_id)}</span>
                  </td>
                  <td className="px-3.5 py-3">
                    <div className="h-2 overflow-hidden rounded-full bg-panel" aria-hidden="true">
                      <div className="h-full rounded-full bg-data-1" style={{ width: `${(value(m) / max) * 100}%` }} />
                    </div>
                  </td>
                  <td className="px-3.5 py-3 text-right tabular-nums">
                    {m.calls.toLocaleString()}
                    {m.unsettled_calls > 0 && (
                      <span className="block text-xs text-secondary" title="Calls that failed or are still running">
                        {m.unsettled_calls} unfinished
                      </span>
                    )}
                  </td>
                  <td className="px-3.5 py-3 text-right tabular-nums">{m.total_tokens.toLocaleString()}</td>
                  <td className="px-3.5 py-3 text-right tabular-nums">
                    {m.cost_unknown ? (
                      <span className="text-secondary" title="This model has no declared price">
                        Unknown
                      </span>
                    ) : (
                      formatCost(Number(m.total_cost_usd) || 0)
                    )}
                  </td>
                  <td className="px-3.5 py-3 text-right tabular-nums text-body">
                    {m.avg_latency_ms === null ? "—" : `${(m.avg_latency_ms / 1000).toFixed(1)} s`}
                  </td>
                </tr>
              ))}
            </DataTable>
      )}
    </section>
  );
}

const TOOL_LABELS: Record<string, string> = {
  inspect_schema: "Inspect schema",
  run_query_ir: "Run query",
  calculate: "Calculate",
};

function ToolUsage({ tools, loading }: { tools: ToolUsageOut[]; loading: boolean }) {
  return (
    <section aria-label="Tool calls" className={cx(cardCls, "px-5 py-3 sm:px-6")}>
      <Details summary="Technical details: tool calls">
        <p className="mb-3 text-xs text-secondary">Every data tool the agents called, with how often a call was invalid, repeated, timed out or answered from the query cache.</p>
      {loading ? (
        <SkeletonRows rows={3} />
      ) : tools.length === 0 ? (
        <EmptyState title="No tool calls in this window" />
      ) : (
        <DataTable label="Tool calls" minWidth="min-w-[720px]" head={[
            "Tool",
            ...["Calls", "Per step", "Failed", "Invalid", "Repeated", "Timed out", "Cached", "Avg time"].map((label) => ({ label, align: "right" as const })),
          ]}>
              {tools.map((t) => (
                <tr key={t.tool} className="border-t border-border">
                  <td className="px-3.5 py-3 font-medium text-ink">{TOOL_LABELS[t.tool] ?? t.tool}</td>
                  <td className="px-3.5 py-3 text-right tabular-nums">{t.calls.toLocaleString()}</td>
                  <td className="px-3.5 py-3 text-right tabular-nums">{t.calls_per_task}</td>
                  <td className="px-3.5 py-3 text-right tabular-nums">{t.failed_calls}</td>
                  <td className="px-3.5 py-3 text-right tabular-nums">{t.invalid_calls}</td>
                  <td className="px-3.5 py-3 text-right tabular-nums">{t.duplicate_calls}</td>
                  <td className="px-3.5 py-3 text-right tabular-nums">{t.timeouts}</td>
                  <td className="px-3.5 py-3 text-right tabular-nums">{t.cache_hits ?? 0}</td>
                  <td className="px-3.5 py-3 text-right tabular-nums text-body">{t.avg_duration_ms.toLocaleString()} ms</td>
                </tr>
              ))}
            </DataTable>
      )}
      </Details>
    </section>
  );
}

/** Bucket runs by hour (24 h view) or by day (longer views) so the chart stays readable at any volume. */
function bucketRuns(runs: RunUsageItem[], windowKey: WindowKey) {
  const hourly = windowKey === "24h";
  const buckets = new Map<string, { key: string; label: string; tokens: number; cost: number; runs: number }>();
  for (const r of runs) {
    const t = r.created_at ? new Date(r.created_at) : null;
    if (!t || Number.isNaN(t.getTime())) continue;
    const start = new Date(t);
    if (hourly) start.setMinutes(0, 0, 0);
    else start.setHours(0, 0, 0, 0);
    const key = start.toISOString();
    const label = hourly
      ? start.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
      : start.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    const b = buckets.get(key) ?? { key, label, tokens: 0, cost: 0, runs: 0 };
    b.tokens += r.total_tokens;
    b.cost += Number(r.total_cost_usd) || 0;
    b.runs += 1;
    buckets.set(key, b);
  }
  return Array.from(buckets.values()).sort((a, b) => a.key.localeCompare(b.key));
}

export function UsageView() {
  const ws = useWorkspace();
  const serverDown = useServerDown();
  const [windowFilter, setWindowFilter] = useUrlState<WindowKey>("window", "7d", isWindowKey);
  const [statusFilter, setStatusFilter] = useUrlState("status", "all", isStatusFilter);
  const [modeFilter, setModeFilter] = useUrlState("mode", "all", isModeFilter);
  const [search, setSearch] = useUrlState("q", "", anyText);
  const [metric, setMetric] = useUrlState<Metric>("metric", "tokens", isMetric);
  const [providerFilter, setProviderFilter] = useUrlState("provider", "all", anyText);


  const [sort, setSort] = useState<{ key: RunSortKey; dir: SortDir }>({ key: "when", dir: "desc" });
  const [rowLimit, setRowLimit] = useState(RUN_TABLE_ROWS);
  // Wait for a pause in typing before asking the server to search.
  const [searchTerm, setSearchTerm] = useState(search);
  useEffect(() => {
    const id = window.setTimeout(() => setSearchTerm(search.trim()), 300);
    return () => window.clearTimeout(id);
  }, [search]);

  const windowHours = WINDOW_HOURS[windowFilter];
  const since =
    windowHours === null ? null : new Date((Math.floor(Date.now() / HOUR_MS) - windowHours) * HOUR_MS).toISOString();
  // The server filters, sorts and pages over every question in the window, and totals the chart.
  const params = new URLSearchParams({
    sort: sort.key,
    order: sort.dir,
    limit: String(rowLimit),
    bucket: windowFilter === "24h" ? "hour" : "day",
    tz_offset_minutes: String(-new Date().getTimezoneOffset()),
  });
  if (since) params.set("since", since);
  if (statusFilter !== "all") params.set("status", statusFilter);
  if (modeFilter !== "all") params.set("mode", modeFilter);
  if (searchTerm) params.set("q", searchTerm);
  const queryString = params.toString();
  const { data, isLoading, error, dataUpdatedAt, refetch } = useQuery({
    queryKey: ["usage", ws.id, queryString],
    queryFn: () => apiFetch<UsageResponse>(ws.path(`/usage?${queryString}`)),
    refetchInterval: REFRESH_MS,
    // Keep the current rows on screen while a new sort or filter loads.
    placeholderData: keepPreviousData,
  });

  const runs = useMemo(() => data?.runs ?? [], [data]);
  const matchedCount = data?.matched_count ?? runs.length;
  const sortable = (label: string, key: RunSortKey) => ({
    label,
    align: key === "when" ? undefined : ("right" as const),
    sort: {
      dir: sort.key === key ? sort.dir : null,
      // A new column starts with its largest values; clicking again flips it.
      onToggle: () => setSort((s) => ({ key, dir: s.key === key && s.dir === "desc" ? "asc" : "desc" })),
    },
  });

  const filtersActive = modeFilter !== "all" || statusFilter !== "all" || search.trim() !== "";
  const totals = useMemo(() => {
    const summary = data?.summary;
    if (summary) {
      return {
        runs: summary.runs,
        attempts: summary.attempts,
        tokens: summary.tokens,
        cost: Number(summary.cost_usd) || 0,
        incomplete: summary.incomplete,
        successRate: summary.finished ? Math.round((summary.completed / summary.finished) * 100) : null,
        byMode: summary.by_mode.map((m) => ({ mode: m.mode, runs: m.runs })),
      };
    }
    // Older API responses carry no summary: fall back to the rows we were given.
    const finished = runs.filter((r) => ["completed", "failed", "cancelled"].includes(r.status ?? ""));
    const completed = runs.filter((r) => r.status === "completed").length;
    return {
      runs: runs.length,
      attempts: runs.reduce((sum, r) => sum + r.used_attempts, 0),
      tokens: runs.reduce((sum, r) => sum + r.total_tokens, 0),
      cost: runs.reduce((sum, r) => sum + (Number(r.total_cost_usd) || 0), 0),
      incomplete: runs.filter((r) => r.incomplete).length,
      successRate: finished.length ? Math.round((completed / finished.length) * 100) : null,
      byMode: ["quick", "deep"].map((mode) => ({ mode, runs: runs.filter((r) => (r.mode ?? "quick") === mode).length })),
    };
  }, [runs, data]);

  const chartData = useMemo(() => {
    const buckets = data?.buckets;
    if (!buckets?.length) return bucketRuns(runs, windowFilter);
    const hourly = windowFilter === "24h";
    return buckets.map((b) => {
      const start = new Date(b.start);
      return {
        key: b.start,
        label: hourly
          ? start.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
          : start.toLocaleDateString(undefined, { month: "short", day: "numeric" }),
        tokens: b.tokens,
        cost: Number(b.cost_usd) || 0,
        runs: b.runs,
      };
    });
  }, [data, runs, windowFilter]);
  const metricLabel = metric === "tokens" ? "Tokens" : metric === "cost" ? "Cost (USD)" : "Runs";
  const hasAnyWorkspaceRuns = (data?.run_count ?? 0) > 0;

  function clearFilters() {
    setStatusFilter("all");
    setModeFilter("all");
    setSearch("");
  }

  if (serverDown) {
    return <PageHeader title="Usage" description="Model calls, tokens and cost for each question, from the budget ledger. Costs we couldn't price are marked." />;
  }

  const failed = Boolean(error) && !data;

  const runFilters = (
    <div className="flex flex-wrap items-center gap-2.5" role="group" aria-label="Filter questions">
      <SearchInput dense className="w-full sm:w-[240px]" label="Search questions" placeholder="Search questions" value={search} onChange={setSearch} />
      <RichSelect dense triggerClassName="min-w-[148px] whitespace-nowrap" ariaLabel="Run status" value={statusFilter} options={STATUS_OPTIONS} onChange={setStatusFilter} />
      <RichSelect dense triggerClassName="min-w-[132px] whitespace-nowrap" ariaLabel="Run mode" value={modeFilter} options={MODE_OPTIONS} onChange={setModeFilter} />
      {filtersActive && (
        <button
          type="button"
          onClick={clearFilters}
          className={btnSm.ghost}
        >
          Reset filters
        </button>
      )}
    </div>
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="Usage"
        description="Model calls, tokens and cost for each question, from the budget ledger. Costs we couldn't price are marked."
        action={
          <span className="inline-flex items-center gap-2 text-xs text-secondary" aria-live={error ? "polite" : undefined}>
            <span aria-hidden="true" className={cx("h-1.5 w-1.5 rounded-full", error ? "bg-err-dot" : "animate-pulse bg-ok-dot")} />
            {error ? "Not updating" : dataUpdatedAt ? `Updated ${new Date(dataUpdatedAt).toLocaleTimeString()}` : "Live"}
          </span>
        }
      />

      <div>
        <div className="hidden sm:block">
          <FilterChips label="Time window" options={WINDOW_OPTIONS} value={windowFilter} onChange={setWindowFilter} />
        </div>
        <div className="w-full sm:hidden">
          <RichSelect dense ariaLabel="Time window" value={windowFilter} options={WINDOW_OPTIONS} onChange={(v) => setWindowFilter(v as WindowKey)} />
        </div>
      </div>

      {error && (
        <Notice
          tone="err"
          title="Couldn't load usage"
          action={
            <button type="button" className={btn.secondary} onClick={() => void refetch()}>
              Try again
            </button>
          }
        >
          {errorMessage(error)}
        </Notice>
      )}

      {!failed && (
      <dl
        className={cx(
          cardCls,
          "grid grid-cols-2 divide-border overflow-hidden max-lg:last:col-span-2 max-lg:[&>*:last-child]:col-span-2 max-lg:[&>*:nth-child(-n+4)]:border-b max-lg:[&>*:nth-child(odd):not(:last-child)]:border-r lg:grid-cols-5 lg:divide-x"
        )}
      >
        <Stat label="Questions" value={totals.runs.toLocaleString()} hint={totals.byMode.map((m) => `${m.runs} ${m.mode}`).join(" · ")} loading={isLoading} />
        <Stat label="Success rate" value={totals.successRate === null ? "—" : `${totals.successRate}%`} hint="Of finished runs" loading={isLoading} />
        <Stat label="Model calls" value={totals.attempts.toLocaleString()} hint="Including retries" loading={isLoading} />
        <Stat label="Tokens" value={compact.format(totals.tokens)} hint={`${totals.tokens.toLocaleString()} total`} loading={isLoading} />
        <Stat
          label="Cost"
          value={totals.runs === 0 ? "—" : totals.incomplete > 0 ? `≥ ${formatCost(totals.cost)}` : formatCost(totals.cost)}
          hint={totals.incomplete > 0 ? `${totals.incomplete} run${totals.incomplete === 1 ? "" : "s"} not fully priced` : "From the price table"}
          loading={isLoading}
        />
      </dl>
      )}

      {failed ? null : !isLoading && !error && data && !hasAnyWorkspaceRuns ? (
        <div className={cardCls}>
          <EmptyState
            title="No usage recorded yet"
            body="Ask your first question to start tracking token spend, model calls, and tool latency here."
            action={
              <Link href="/ask" className={btn.primary}>
                Go to Ask
              </Link>
            }
          />
        </div>
      ) : (
        <>
            <section aria-labelledby="usage-chart" className={cx(cardCls, "space-y-4 p-5 sm:p-6")}>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 id="usage-chart" className={sectionTitleCls}>
                  {metricLabel} per {windowFilter === "24h" ? "hour" : "day"}
                </h2>
                <FilterChips
                  label="Chart metric"
                  value={metric}
                  onChange={setMetric}
                  options={[
                    { value: "tokens", label: "Tokens" },
                    { value: "cost", label: "Cost" },
                    { value: "runs", label: "Questions" },
                  ]}
                />
              </div>
              {isLoading ? (
                <Skeleton className="h-56" />
              ) : chartData.length === 0 ? (
                <div className="flex h-56 items-center justify-center rounded-lg bg-panel text-base text-secondary">No questions in this time window.</div>
              ) : (
                <div className="h-56 w-full" role="img" aria-label={`${metricLabel} per ${windowFilter === "24h" ? "hour" : "day"}; the table below lists every run.`}>
                  <UsageBarChart
                    data={chartData}
                    dataKey={metric}
                    seriesLabel={metricLabel}
                    formatAxis={(v) => (metric === "cost" ? `$${compact.format(v)}` : compact.format(v))}
                    formatValue={(v) => (metric === "cost" ? formatCost(v) : v.toLocaleString())}
                  />
                </div>
              )}
            </section>

          <ModelUsage
            models={data?.models ?? []}
            metric={metric}
            provider={providerFilter}
            onProvider={setProviderFilter}
            windowLabel={WINDOW_OPTIONS.find((o) => o.value === windowFilter)?.label ?? ""}
            loading={isLoading}
          />

          <ToolUsage tools={data?.tools ?? []} loading={isLoading} />

          <section aria-labelledby="usage-runs" className={cx(cardCls, "space-y-4 p-5 sm:p-6")}>
            <div className="flex items-baseline justify-between gap-3">
              <h2 id="usage-runs" className={sectionTitleCls}>
                Questions
              </h2>
              <span className="text-xs text-secondary">{matchedCount.toLocaleString()} in view</span>
            </div>
            {runFilters}
            {isLoading ? (
              <SkeletonRows rows={5} />
            ) : runs.length === 0 ? (
              <EmptyState
                title="No questions match these filters"
                action={
                  filtersActive ? (
                    <button type="button" className={btn.secondary} onClick={clearFilters}>
                      Reset filters
                    </button>
                  ) : undefined
                }
              />
            ) : (
              <DataTable label="Questions asked" minWidth="min-w-[720px]" head={[
                  sortable("When", "when"),
                  "Question",
                  "Mode",
                  "Status",
                  sortable("Model calls", "calls"),
                  sortable("Tokens", "tokens"),
                  sortable("Cost", "cost"),
                ]}>
                    {runs.map((r) => {
                      const status = r.status ?? "queued";
                      const cost = formatCost(Number(r.total_cost_usd) || 0);
                      return (
                        <tr key={r.run_id} className="border-t border-border align-top">
                          <td className="whitespace-nowrap px-3.5 py-3 text-secondary" title={r.created_at ?? undefined}>
                            {relativeTime(r.created_at)}
                          </td>
                          <td className="max-w-[320px] px-3.5 py-3">
                            <Link href={`/ask?run=${encodeURIComponent(r.run_id)}`} className="line-clamp-2 text-ink underline decoration-border underline-offset-4 hover:decoration-ink" title={r.question ?? undefined}>
                              {r.question || "Untitled question"}
                            </Link>
                            {r.error && <span className="mt-0.5 line-clamp-1 block text-xs text-err">Failed: see the run for details</span>}
                          </td>
                          <td className="px-3.5 py-3 capitalize text-body">{r.mode ?? "quick"}</td>
                          <td className="whitespace-nowrap px-3.5 py-3">
                            <span className="inline-flex items-center gap-1.5 capitalize text-body">
                              <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${STATUS_DOT[status] ?? "bg-secondary"}`} />
                              {status.replaceAll("_", " ")}
                            </span>
                          </td>
                          <td className="px-3.5 py-3 text-right tabular-nums">{r.used_attempts}</td>
                          <td className="px-3.5 py-3 text-right tabular-nums">{r.total_tokens.toLocaleString()}</td>
                          <td className="px-3.5 py-3 text-right tabular-nums">
                            {r.incomplete ? (
                              <span className="text-secondary" title="Some model calls had no declared price">
                                ≥ {cost}
                              </span>
                            ) : (
                              cost
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </DataTable>
            )}
            {matchedCount > runs.length && (
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-xs text-secondary">
                  Showing {runs.length.toLocaleString()} of {matchedCount.toLocaleString()} questions
                  {rowLimit >= RUN_TABLE_MAX ? ". Narrow the filters or time window to see the rest." : "."}
                </p>
                {rowLimit < RUN_TABLE_MAX && (
                  <button
                    type="button"
                    className={btn.secondary}
                    onClick={() => setRowLimit((n) => Math.min(n + RUN_TABLE_ROWS, RUN_TABLE_MAX))}
                  >
                    Show {Math.min(RUN_TABLE_ROWS, matchedCount - runs.length)} more
                  </button>
                )}
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
