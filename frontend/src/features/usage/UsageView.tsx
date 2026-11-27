"use client";

import React, { useMemo } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { anyText, useUrlState } from "@/lib/url";
import { apiFetch, errorMessage } from "@/lib/api/client";
import { RichSelect, RichOption } from "@/components/RichSelect";
import { PageHeader } from "@/components/PageHeader";
import { HScroll } from "@/components/HScroll";
import { EmptyState, FilterChips, Notice, SearchInput, btn, cardCls, cx } from "@/components/ui";
import { useThemeColor } from "@/lib/theme";
import { providerLabel, relativeTime, useWorkspace } from "@/lib/workspace";
import type { ModelUsageOut, RunUsageOut, ToolUsageOut, UsageOut } from "@/lib/api/types";

type RunUsageItem = RunUsageOut;
type ModelUsageItem = ModelUsageOut;
type UsageResponse = UsageOut;

type WindowKey = "24h" | "7d" | "30d" | "90d" | "all";
type Metric = "tokens" | "cost" | "runs";

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
const WINDOW_HOURS: Record<WindowKey, number | null> = { "24h": 24, "7d": 168, "30d": 720, "90d": 2160, all: null };

const STATUS_OPTIONS: RichOption[] = [
  { value: "all", label: "All statuses" },
  { value: "completed", label: "Completed" },
  { value: "running", label: "Running" },
  { value: "queued", label: "Queued" },
  { value: "failed", label: "Failed" },
  { value: "cancelled", label: "Cancelled" },
];

const MODE_OPTIONS: RichOption[] = [
  { value: "all", label: "All modes" },
  { value: "quick", label: "Quick" },
  { value: "deep", label: "Deep" },
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
      <dd className="mt-1.5 truncate font-display text-[1.6rem] font-semibold leading-tight tracking-[-0.02em] tabular-nums text-ink">
        {loading ? <span className="inline-block h-7 w-16 animate-pulse bg-panel align-middle" /> : value}
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
          <h2 id="usage-models" className="font-display text-lg font-semibold tracking-[-0.01em]">
            By model
          </h2>
          <p className="mt-0.5 text-xs text-secondary">All model calls in the last {windowLabel.toLowerCase()} across all modes and statuses. Bars show {barLabel}.</p>
        </div>
        {providers.length > 1 && (
          <div role="group" aria-label="Provider" className="flex flex-wrap gap-1.5">
            {["all", ...providers].map((p) => (
              <button
                key={p}
                type="button"
                aria-pressed={active === p}
                onClick={() => onProvider(p)}
                className={cx(
                  "min-h-[32px] border px-3 text-[12.5px] transition-colors",
                  active === p ? "border-ink bg-ink text-canvas" : "border-border bg-surface text-body hover:border-ink"
                )}
              >
                {p === "all" ? "All providers" : providerLabel(p)}
              </button>
            ))}
          </div>
        )}
      </div>
      {shown.length === 0 ? (
        <EmptyState title={loading ? "Loading…" : "No model calls in this window"} />
      ) : (
        <HScroll className="border border-border" label="Usage by model">
          <table className="w-full min-w-[720px] border-collapse text-left text-[13px]">
            <thead>
              <tr className="bg-panel text-[11px] uppercase tracking-[0.05em] text-secondary">
                <th className="px-3.5 py-2.5 font-medium">Model</th>
                <th className="w-[28%] px-3.5 py-2.5 font-medium">
                  <span className="sr-only">Share of {barLabel}</span>
                </th>
                <th className="px-3.5 py-2.5 text-right font-medium">Calls</th>
                <th className="px-3.5 py-2.5 text-right font-medium">Tokens</th>
                <th className="px-3.5 py-2.5 text-right font-medium">Cost</th>
                <th className="px-3.5 py-2.5 text-right font-medium">Avg latency</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((m) => (
                <tr key={`${m.provider_id}:${m.model_id}`} className="border-t border-border">
                  <td className="px-3.5 py-3">
                    <span className="block font-medium text-ink">{m.model_id}</span>
                    <span className="block text-xs text-secondary">{providerLabel(m.provider_id)}</span>
                  </td>
                  <td className="px-3.5 py-3">
                    <div className="h-2 overflow-hidden bg-panel" aria-hidden="true">
                      <div className="h-full bg-data-1" style={{ width: `${(value(m) / max) * 100}%` }} />
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
            </tbody>
          </table>
        </HScroll>
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
    <section aria-labelledby="usage-tools" className={cx(cardCls, "space-y-4 p-5 sm:p-6")}>
      <div>
        <h2 id="usage-tools" className="font-display text-lg font-semibold tracking-[-0.01em]">
          Tool calls
        </h2>
        <p className="mt-0.5 text-xs text-secondary">Every data tool the agents called, with how often a call was invalid, repeated, timed out or answered from the query cache.</p>
      </div>
      {tools.length === 0 ? (
        <EmptyState title={loading ? "Loading…" : "No tool calls in this window"} />
      ) : (
        <HScroll className="border border-border" label="Tool calls">
          <table className="w-full min-w-[720px] border-collapse text-left text-[13px]">
            <thead>
              <tr className="bg-panel text-[11px] uppercase tracking-[0.05em] text-secondary">
                <th className="px-3.5 py-2.5 font-medium">Tool</th>
                <th className="px-3.5 py-2.5 text-right font-medium">Calls</th>
                <th className="px-3.5 py-2.5 text-right font-medium">Per step</th>
                <th className="px-3.5 py-2.5 text-right font-medium">Failed</th>
                <th className="px-3.5 py-2.5 text-right font-medium">Invalid</th>
                <th className="px-3.5 py-2.5 text-right font-medium">Repeated</th>
                <th className="px-3.5 py-2.5 text-right font-medium">Timed out</th>
                <th className="px-3.5 py-2.5 text-right font-medium">Cached</th>
                <th className="px-3.5 py-2.5 text-right font-medium">Avg time</th>
              </tr>
            </thead>
            <tbody>
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
            </tbody>
          </table>
        </HScroll>
      )}
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
  const [windowFilter, setWindowFilter] = useUrlState<WindowKey>("window", "7d", isWindowKey);
  const [statusFilter, setStatusFilter] = useUrlState("status", "all", isStatusFilter);
  const [modeFilter, setModeFilter] = useUrlState("mode", "all", isModeFilter);
  const [search, setSearch] = useUrlState("q", "", anyText);
  const [metric, setMetric] = useUrlState<Metric>("metric", "tokens", isMetric);
  const [providerFilter, setProviderFilter] = useUrlState("provider", "all", anyText);

  const gridColor = useThemeColor("border", "#E4E4E7");
  const axisColor = useThemeColor("muted", "#696972");
  const barColor = useThemeColor("data-1", "#1B6E9C");
  const surfaceColor = useThemeColor("surface", "#FFFFFF");
  const inkColor = useThemeColor("ink", "#18181B");

  const windowHours = WINDOW_HOURS[windowFilter];
  const since =
    windowHours === null ? null : new Date((Math.floor(Date.now() / HOUR_MS) - windowHours) * HOUR_MS).toISOString();
  const { data, isLoading, error, dataUpdatedAt } = useQuery({
    queryKey: ["usage", ws.id, since],
    queryFn: () => apiFetch<UsageResponse>(ws.path(since ? `/usage?since=${encodeURIComponent(since)}` : "/usage")),
    refetchInterval: REFRESH_MS,
  });

  const runs = useMemo(() => {
    const hours = WINDOW_HOURS[windowFilter];
    const start = hours === null ? null : Date.now() - hours * 3600 * 1000;
    const q = search.trim().toLowerCase();
    return (data?.runs ?? [])
      .filter((r) => {
        const created = r.created_at ? Date.parse(r.created_at) : NaN;
        const inWindow = start === null || (!Number.isNaN(created) && created >= start);
        const modeOk = modeFilter === "all" || (r.mode ?? "quick") === modeFilter;
        const statusOk = statusFilter === "all" || (r.status ?? "queued") === statusFilter;
        const searchOk = !q || (r.question ?? "").toLowerCase().includes(q) || r.run_id.toLowerCase().includes(q);
        return inWindow && modeOk && statusOk && searchOk;
      })
      .sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
  }, [data, windowFilter, modeFilter, statusFilter, search]);

  const filtered = modeFilter !== "all" || statusFilter !== "all" || search.trim() !== "";
  const totals = useMemo(() => {
    const summary = data?.summary;
    if (!filtered && summary) {
      return {
        runs: summary.runs,
        attempts: summary.attempts,
        tokens: summary.tokens,
        cost: Number(summary.cost_usd) || 0,
        incomplete: summary.incomplete,
        successRate: summary.finished ? Math.round((summary.completed / summary.finished) * 100) : null,
        byMode: summary.by_mode.map((m) => ({ mode: m.mode, runs: m.runs, tokens: m.tokens, cost: Number(m.cost_usd) || 0 })),
      };
    }
    const finished = runs.filter((r) => ["completed", "failed", "cancelled"].includes(r.status ?? ""));
    const completed = runs.filter((r) => r.status === "completed").length;
    return {
      runs: runs.length,
      attempts: runs.reduce((s, r) => s + r.used_attempts, 0),
      tokens: runs.reduce((s, r) => s + r.total_tokens, 0),
      cost: runs.reduce((s, r) => s + (Number(r.total_cost_usd) || 0), 0),
      incomplete: runs.filter((r) => r.incomplete).length,
      successRate: finished.length ? Math.round((completed / finished.length) * 100) : null,
      byMode: Array.from(new Set(["quick", "deep", ...runs.map((r) => r.mode ?? "quick")])).map((mode) => {
        const subset = runs.filter((r) => (r.mode ?? "quick") === mode);
        return {
          mode,
          runs: subset.length,
          tokens: subset.reduce((s, r) => s + r.total_tokens, 0),
          cost: subset.reduce((s, r) => s + (Number(r.total_cost_usd) || 0), 0),
        };
      }),
    };
  }, [runs, data, filtered]);

  const chartData = useMemo(() => bucketRuns(runs, windowFilter), [runs, windowFilter]);
  const modeMetricVal = (m: (typeof totals.byMode)[number]) => (metric === "cost" ? m.cost : metric === "runs" ? m.runs : m.tokens);
  const rawMaxMode = Math.max(...totals.byMode.map(modeMetricVal), 0);
  const maxModeMetric = rawMaxMode > 0 ? rawMaxMode : 1;
  const filtersActive = statusFilter !== "all" || modeFilter !== "all" || windowFilter !== "7d" || Boolean(search.trim());
  const metricLabel = metric === "tokens" ? "Tokens" : metric === "cost" ? "Cost (USD)" : "Runs";
  const hasAnyWorkspaceRuns = (data?.runs?.length ?? 0) > 0;

  function clearFilters() {
    setStatusFilter("all");
    setModeFilter("all");
    setWindowFilter("7d");
    setSearch("");
  }

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

      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-4" role="group" aria-label="Filters">
        <div className="flex flex-wrap items-center gap-2.5">
          <SearchInput
            dense
            className="w-full sm:w-[240px]"
            label="Search questions"
            placeholder="Search questions"
            value={search}
            onChange={setSearch}
          />
          <RichSelect
            dense
            triggerClassName="min-w-[148px] whitespace-nowrap"
            ariaLabel="Run status"
            value={statusFilter}
            options={STATUS_OPTIONS}
            onChange={setStatusFilter}
          />
          <RichSelect
            dense
            triggerClassName="min-w-[132px] whitespace-nowrap"
            ariaLabel="Run mode"
            value={modeFilter}
            options={MODE_OPTIONS}
            onChange={setModeFilter}
          />
          {filtersActive && (
            <button
              type="button"
              onClick={clearFilters}
              className="inline-flex h-9 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap px-3 text-[13px] text-secondary transition-colors hover:bg-sidebar hover:text-ink"
            >
              Reset filters
            </button>
          )}
        </div>
        <FilterChips label="Time window" options={WINDOW_OPTIONS} value={windowFilter} onChange={setWindowFilter} />
      </div>

      {error && <Notice tone="err" title="Couldn't load usage">{errorMessage(error)}</Notice>}

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

      {!isLoading && !error && data && !hasAnyWorkspaceRuns && !filtersActive ? (
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
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
            <section aria-labelledby="usage-chart" className={cx(cardCls, "space-y-4 p-5 sm:p-6")}>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 id="usage-chart" className="font-display text-lg font-semibold tracking-[-0.01em]">
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
              {data?.runs_truncated && (
                <p className="text-xs text-secondary">The chart shows the newest {data.runs.length.toLocaleString()} of {data.run_count.toLocaleString()} runs.</p>
              )}
              {chartData.length === 0 ? (
                <div className="flex h-56 items-center justify-center bg-panel text-[13.5px] text-secondary">
                  {isLoading ? "Loading…" : "No runs in this view."}
                </div>
              ) : (
                <div className="h-56 w-full" role="img" aria-label={`${metricLabel} per ${windowFilter === "24h" ? "hour" : "day"}; the table below lists every run.`}>
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: -8 }}>
                      <CartesianGrid stroke={gridColor} vertical={false} />
                      <XAxis dataKey="label" stroke={axisColor} fontSize={11} tickLine={false} axisLine={false} minTickGap={12} />
                      <YAxis
                        stroke={axisColor}
                        fontSize={11}
                        tickLine={false}
                        axisLine={false}
                        width={48}
                        tickFormatter={(v) => (metric === "cost" ? `$${compact.format(Number(v))}` : compact.format(Number(v)))}
                      />
                      <Tooltip
                        cursor={{ fill: gridColor, fillOpacity: 0.45 }}
                        formatter={(value) => [metric === "cost" ? formatCost(Number(value)) : Number(value).toLocaleString(), metricLabel]}
                        contentStyle={{
                          borderRadius: 0,
                          border: `1px solid ${gridColor}`,
                          background: surfaceColor,
                          color: inkColor,
                          fontSize: 12,
                        }}
                      />
                      <Bar dataKey={metric} fill={barColor} maxBarSize={36} isAnimationActive={false} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              )}
            </section>

            <section aria-labelledby="usage-modes" className={cx(cardCls, "space-y-4 p-5 sm:p-6")}>
              <h2 id="usage-modes" className="font-display text-lg font-semibold tracking-[-0.01em]">
                By mode
              </h2>
              <ul className="space-y-4">
                {totals.byMode.map((m) => (
                  <li key={m.mode} className="space-y-1.5">
                    <div className="flex items-baseline justify-between gap-3 text-[13.5px]">
                      <span className="font-medium capitalize">{m.mode}</span>
                      <span className="tabular-nums text-secondary">
                        {m.runs} run{m.runs === 1 ? "" : "s"} · {formatCost(m.cost)}
                      </span>
                    </div>
                    <div className="h-2 overflow-hidden bg-panel" aria-hidden="true">
                      <div className="h-full bg-data-1" style={{ width: `${(modeMetricVal(m) / maxModeMetric) * 100}%` }} />
                    </div>
                    <div className="text-xs tabular-nums text-secondary">{m.tokens.toLocaleString()} tokens</div>
                  </li>
                ))}
              </ul>
            </section>
          </div>

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
              <h2 id="usage-runs" className="font-display text-lg font-semibold tracking-[-0.01em]">
                Questions
              </h2>
              <span className="text-xs text-secondary">{runs.length.toLocaleString()} in view</span>
            </div>
            {runs.length === 0 ? (
              <EmptyState
                title={isLoading ? "Loading…" : "No runs match these filters"}
                action={
                  filtersActive && !isLoading ? (
                    <button type="button" className={btn.secondary} onClick={clearFilters}>
                      Reset filters
                    </button>
                  ) : undefined
                }
              />
            ) : (
              <HScroll className="border border-border" label="Runs">
                <table className="w-full min-w-[720px] border-collapse text-left text-[13px]">
                  <thead>
                    <tr className="bg-panel text-[11px] uppercase tracking-[0.05em] text-secondary">
                      <th className="px-3.5 py-2.5 font-medium">When</th>
                      <th className="px-3.5 py-2.5 font-medium">Question</th>
                      <th className="px-3.5 py-2.5 font-medium">Mode</th>
                      <th className="px-3.5 py-2.5 font-medium">Status</th>
                      <th className="px-3.5 py-2.5 text-right font-medium">Model calls</th>
                      <th className="px-3.5 py-2.5 text-right font-medium">Tokens</th>
                      <th className="px-3.5 py-2.5 text-right font-medium">Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runs.slice(0, RUN_TABLE_ROWS).map((r) => {
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
                  </tbody>
                </table>
              </HScroll>
            )}
            {(runs.length > RUN_TABLE_ROWS || data?.runs_truncated) && (
              <p className="text-xs text-secondary">
                Showing the {Math.min(runs.length, RUN_TABLE_ROWS).toLocaleString()} most recent of {(filtered ? runs.length : data?.run_count ?? runs.length).toLocaleString()} runs
                {filtered && data?.runs_truncated ? `, filtered from the newest ${data.runs.length.toLocaleString()}` : ""}.
              </p>
            )}
          </section>
        </>
      )}
    </div>
  );
}
