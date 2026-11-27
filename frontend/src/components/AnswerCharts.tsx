"use client";

import React, { useContext } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { ChartOut } from "@/lib/api/types";
import { EvidenceContext } from "@/components/SafeMarkdown";
import { useThemeColor } from "@/lib/theme";

const AXIS_LABEL_CHARS = 24;

export type AnswerChart = ChartOut;

const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
const full = new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 });
const humanize = (field: string) => field.replace(/_/g, " ");

function ChartCard({ chart, small, showTitle }: { chart: AnswerChart; small: boolean; showTitle: boolean }) {
  const openEvidence = useContext(EvidenceContext);
  const gridColor = useThemeColor("border", "#E4E4E7");
  const axisColor = useThemeColor("muted", "#696972");
  const surfaceColor = useThemeColor("surface", "#FFFFFF");
  const inkColor = useThemeColor("ink", "#18181B");
  const palette = [useThemeColor("data-1", "#1B6E9C"), useThemeColor("data-2", "#C2652A"), useThemeColor("data-3", "#B0407A")];
  const colorFor = (idx: number) => palette[Math.min(idx, palette.length - 1)];
  const shortLabel = (value: unknown) => {
    const text = String(value ?? "");
    return text.length > AXIS_LABEL_CHARS ? `${text.slice(0, AXIS_LABEL_CHARS - 1)}…` : text;
  };
  const keys = chart.y_fields.map((_, i) => `s${i}`);
  const data = chart.rows.map((row) => {
    const point: Record<string, string | number | null> = { label: String(row[0] ?? "") };
    keys.forEach((key, i) => {
      point[key] = typeof row[i + 1] === "number" ? (row[i + 1] as number) : null;
    });
    return point;
  });
  const names = Object.fromEntries(keys.map((key, i) => [key, humanize(chart.y_fields[i])]));
  const axes = [
    <CartesianGrid key="grid" stroke={gridColor} vertical={false} />,
    <XAxis key="x" dataKey="label" stroke={axisColor} fontSize={11} tickLine={false} axisLine={false} minTickGap={12} tickFormatter={shortLabel} />,
    <YAxis
      key="y"
      stroke={axisColor}
      fontSize={11}
      tickLine={false}
      axisLine={false}
      width={48}
      tickFormatter={(v) => compact.format(Number(v))}
    />,
    <Tooltip
      key="tip"
      cursor={chart.chart_type === "line" ? { stroke: gridColor } : { fill: gridColor, fillOpacity: 0.45 }}
      formatter={(value, key) => [value === null ? "—" : full.format(Number(value)), names[String(key)] ?? String(key)]}
      contentStyle={{ borderRadius: 0, border: `1px solid ${gridColor}`, background: surfaceColor, color: inkColor, fontSize: 12 }}
    />
  ];
  const label = `${chart.title}: ${chart.y_fields.map(humanize).join(", ")} by ${humanize(chart.x_field)}, ${chart.rows.length} points.`;

  return (
    <figure className="border border-border bg-surface p-3.5">
      {(showTitle || keys.length > 1) && (
        <figcaption className="mb-2 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          {showTitle && <span className="text-[13px] font-medium text-ink">{chart.title}</span>}
          {keys.length > 1 && (
            <span className="flex flex-wrap gap-3 text-[11.5px] text-secondary">
              {keys.map((key, i) => (
                <span key={key} className="inline-flex items-center gap-1.5">
                  <span aria-hidden="true" className="inline-block h-2 w-2" style={{ background: colorFor(i) }} />
                  {names[key]}
                </span>
              ))}
            </span>
          )}
        </figcaption>
      )}
      <div className={small ? "h-36 w-full" : "h-52 w-full"} role="img" aria-label={label}>
        <ResponsiveContainer width="100%" height="100%">
          {chart.chart_type === "line" ? (
            <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -8 }}>
              {axes}
              {keys.map((key, i) => (
                <Line key={key} dataKey={key} stroke={colorFor(i)} strokeWidth={2} dot={data.length <= 24} isAnimationActive={false} />
              ))}
            </LineChart>
          ) : (
            <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -8 }} barCategoryGap={chart.chart_type === "histogram" ? 1 : "20%"}>
              {axes}
              {keys.map((key, i) => (
                <Bar key={key} dataKey={key} fill={colorFor(i)} maxBarSize={chart.chart_type === "histogram" ? undefined : 36} isAnimationActive={false} />
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
      {!small && (
        <details className="mt-2 text-[12px] text-secondary">
          <summary className="cursor-pointer select-none hover:text-ink">Show data</summary>
          <div className="mt-2 max-h-56 overflow-auto">
            <table className="w-full border-collapse text-left">
              <thead>
                <tr>
                  {[chart.x_field, ...chart.y_fields].map((h) => (
                    <th key={h} scope="col" className="border-b border-border py-1 pr-3 font-medium text-body">
                      {humanize(h)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {chart.rows.map((row, r) => (
                  <tr key={r}>
                    {row.map((cell, c) => (
                      <td key={c} className="border-b border-border py-1 pr-3 tabular-nums">
                        {typeof cell === "number" ? full.format(cell) : cell ?? "—"}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
      {(openEvidence || chart.truncated) && (
        <p className="mt-1.5 flex flex-wrap items-center gap-x-2 text-[11px] text-secondary">
          {openEvidence && (
            <button
              type="button"
              onClick={() => openEvidence(chart.evidence_id)}
              className="underline decoration-border-strong underline-offset-4 hover:text-ink"
            >
              View source query
            </button>
          )}
          {chart.truncated && <span>Partial data: only the first rows are shown</span>}
        </p>
      )}
    </figure>
  );
}

const sameTitle = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

export function AnswerCharts({ charts, small = false, contextTitle }: { charts?: AnswerChart[] | null; small?: boolean; contextTitle?: string }) {
  if (!charts?.length) return null;
  const shown = small ? charts.slice(0, 1) : charts;
  return (
    <div className={small ? undefined : "mt-4 grid gap-3 lg:grid-cols-2"}>
      {shown.map((chart) => (
        <ChartCard key={chart.chart_id} chart={chart} small={small} showTitle={!contextTitle || !sameTitle(chart.title, contextTitle)} />
      ))}
    </div>
  );
}
