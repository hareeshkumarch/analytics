"use client";

import React, { useContext, useMemo } from "react";
import dynamic from "next/dynamic";
import type { ChartOut } from "@/lib/api/types";
import { EvidenceContext } from "@/components/SafeMarkdown";
import { useThemeColor } from "@/lib/theme";

export type AnswerChart = ChartOut;

const ChartPlot = dynamic(() => import("@/components/ChartPlot").then((m) => m.ChartPlot), { ssr: false });

const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
const full = new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 });
const humanize = (field: string) => field.replace(/_/g, " ");

export interface NumberFormats {
  axis: Intl.NumberFormat;
  value: Intl.NumberFormat;
}

function numberFormats(currency: string | null | undefined): NumberFormats {
  if (!currency) return { axis: compact, value: full };
  try {
    return {
      axis: new Intl.NumberFormat(undefined, { style: "currency", currency, notation: "compact", maximumFractionDigits: 1 }),
      value: new Intl.NumberFormat(undefined, { style: "currency", currency }),
    };
  } catch {
    return { axis: compact, value: full };
  }
}

function ChartCard({ chart, small, showTitle }: { chart: AnswerChart; small: boolean; showTitle: boolean }) {
  const openEvidence = useContext(EvidenceContext);
  const formats = useMemo(() => numberFormats(chart.currency), [chart.currency]);
  const palette = [useThemeColor("data-1", "#1B6E9C"), useThemeColor("data-2", "#C2652A"), useThemeColor("data-3", "#B0407A")];
  const colorFor = (idx: number) => palette[Math.min(idx, palette.length - 1)];
  const keys = chart.y_fields.map((_, i) => `s${i}`);
  const data = chart.rows.map((row) => {
    const point: Record<string, string | number | null> = { label: String(row[0] ?? "") };
    keys.forEach((key, i) => {
      point[key] = typeof row[i + 1] === "number" ? (row[i + 1] as number) : null;
    });
    return point;
  });
  const names = Object.fromEntries(keys.map((key, i) => [key, humanize(chart.y_fields[i])]));
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
        <ChartPlot chart={chart} data={data} keys={keys} names={names} colors={palette} formats={formats} />
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
                        {typeof cell === "number" ? (c > 0 ? formats.value : full).format(cell) : cell ?? "—"}
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
