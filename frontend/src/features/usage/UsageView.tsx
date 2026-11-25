"use client";

import React, { useMemo, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { apiFetch, errorMessage } from "@/lib/api/client";
import { RichSelect, RichOption } from "@/components/RichSelect";
import { PageHeader } from "@/components/PageHeader";
import { HScroll } from "@/components/HScroll";
import { EmptyState, FilterChips, Notice, SearchInput, btn, cardCls, cx } from "@/components/ui";
import { useThemeColor } from "@/lib/theme";
import { relativeTime, useWorkspace } from "@/lib/workspace";

interface RunUsageItem {
  run_id: string;
  mode?: string;
  status?: string;
  used_attempts: number;
  total_tokens: number;
  total_cost_usd: string;
  incomplete: boolean;
  created_at?: string;
  question?: string;
  error?: string | null;
}

interface UsageResponse {
  workspace_id: string;
  run_count: number;
  runs: RunUsageItem[];
}

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
  const [windowFilter, setWindowFilter] = useState<WindowKey>("7d");
  const [statusFilter, setStatusFilter] = useState("all");
  const [modeFilter, setModeFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [metric, setMetric] = useState<Metric>("tokens");

  const gridColor = useThemeColor("border", "#E4E4E7");
  const axisColor = useThemeColor("muted", "#696972");
  const barColor = useThemeColor("accent", "#18181B");
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

  const totals = useMemo(() => {
    const finished = runs.filter((r) => ["completed", "failed", "cancelled"].includes(r.status ?? ""));
    const completed = runs.filter((r) => r.status === "completed").length;
    return {
      runs: runs.length,
      attempts: runs.reduce((s, r) => s + r.used_attempts, 0),
      tokens: runs.reduce((s, r) => s + r.total_tokens, 0),
      cost: runs.reduce((s, r) => s + (Number(r.total_cost_usd) || 0), 0),
      incomplete: runs.filter((r) => r.incomplete).length,
      successRate: finished.length ? Math.round((completed / finished.length) * 100) : null,
      byMode: (["quick", "deep"] as const).map((mode) => {
        const subset = runs.filter((r) => (r.mode ?? "quick") === mode);
        return {
          mode,
          runs: subset.length,
          tokens: subset.reduce((s, r) => s + r.total_tokens, 0),
          cost: subset.reduce((s, r) => s + (Number(r.total_cost_usd) || 0), 0),
        };
      }),
    };
  }, [runs]);

  const chartData = useMemo(() => bucketRuns(runs, windowFilter), [runs, windowFilter]);
  const maxModeTokens = Math.max(1, ...totals.byMode.map((m) => m.tokens));
  const filtersActive = statusFilter !== "all" || modeFilter !== "all" || windowFilter !== "7d" || Boolean(search.trim());
  const metricLabel = metric === "tokens" ? "Tokens" : metric === "cost" ? "Cost (USD)" : "Runs";

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
          <span className="inline-flex items-center gap-2 text-xs text-secondary" aria-live="polite">
            <span aria-hidden="true" className={cx("h-1.5 w-1.5 rounded-full", error ? "bg-err-dot" : "animate-pulse bg-ok-dot")} />
            {error ? "Not updating" : dataUpdatedAt ? `Updated ${new Date(dataUpdatedAt).toLocaleTimeString()}` : "Live"}
          </span>
        }
      />

      <div className="flex flex-col gap-3 xl:flex-row xl:flex-wrap xl:items-center" role="group" aria-label="Filters">
        <FilterChips label="Time window" options={WINDOW_OPTIONS} value={windowFilter} onChange={setWindowFilter} />
        <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap sm:items-center">
          <SearchInput className="col-span-2 sm:w-[240px]" label="Search questions" placeholder="Search questions" value={search} onChange={setSearch} />
          <div className="min-w-0 sm:w-[160px]">
            <RichSelect compact ariaLabel="Run status" value={statusFilter} options={STATUS_OPTIONS} onChange={setStatusFilter} />
          </div>
          <div className="min-w-0 sm:w-[140px]">
            <RichSelect compact ariaLabel="Run mode" value={modeFilter} options={MODE_OPTIONS} onChange={setModeFilter} />
          </div>
          {filtersActive && (
            <button type="button" onClick={clearFilters} className={cx(btn.ghost, "col-span-2 sm:col-span-1")}>
              Reset filters
            </button>
          )}
        </div>
      </div>

      {error && <Notice tone="err" title="Couldn't load usage">{errorMessage(error)}</Notice>}

      <dl
        className={cx(
          cardCls,
          "grid grid-cols-2 divide-border overflow-hidden max-lg:[&>*:nth-child(-n+4)]:border-b max-lg:[&>*:nth-child(odd)]:border-r lg:grid-cols-5 lg:divide-x"
        )}
      >
        <Stat label="Questions" value={totals.runs.toLocaleString()} hint={`${totals.byMode[0].runs} quick · ${totals.byMode[1].runs} deep`} loading={isLoading} />
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
                  <Bar dataKey={metric} fill={barColor} maxBarSize={36} />
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
                  <div className="h-full bg-plum" style={{ width: `${(m.tokens / maxModeTokens) * 100}%` }} />
                </div>
                <div className="text-xs tabular-nums text-secondary">{m.tokens.toLocaleString()} tokens</div>
              </li>
            ))}
          </ul>
        </section>
      </div>

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
                {runs.slice(0, 100).map((r) => {
                  const status = r.status ?? "queued";
                  return (
                    <tr key={r.run_id} className="border-t border-border align-top">
                      <td className="whitespace-nowrap px-3.5 py-3 text-secondary" title={r.created_at}>
                        {relativeTime(r.created_at)}
                      </td>
                      <td className="max-w-[320px] px-3.5 py-3">
                        <Link href={`/ask?run=${encodeURIComponent(r.run_id)}`} className="line-clamp-2 text-ink underline decoration-border underline-offset-4 hover:decoration-ink" title={r.question}>
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
                          <span className="text-secondary" title="Some model calls have no settled price">
                            —
                          </span>
                        ) : (
                          formatCost(Number(r.total_cost_usd) || 0)
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </HScroll>
        )}
        {runs.length > 100 && <p className="text-xs text-secondary">Showing the 100 most recent of {runs.length.toLocaleString()} runs.</p>}
      </section>
    </div>
  );
}
