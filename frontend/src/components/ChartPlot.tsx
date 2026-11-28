"use client";

import React from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { AnswerChart, NumberFormats } from "@/components/AnswerCharts";
import { useThemeColor } from "@/lib/theme";

const AXIS_LABEL_CHARS = 24;

const shortLabel = (value: unknown) => {
  const text = String(value ?? "");
  return text.length > AXIS_LABEL_CHARS ? `${text.slice(0, AXIS_LABEL_CHARS - 1)}…` : text;
};

export function ChartPlot({
  chart,
  data,
  keys,
  names,
  colors,
  formats,
}: {
  chart: AnswerChart;
  data: Record<string, string | number | null>[];
  keys: string[];
  names: Record<string, string>;
  colors: string[];
  formats: NumberFormats;
}) {
  const gridColor = useThemeColor("border", "#E6E4DF");
  const axisColor = useThemeColor("muted", "#787671");
  const popoverColor = useThemeColor("popover", "#FFFFFF");
  const inkColor = useThemeColor("ink", "#111111");
  const colorFor = (idx: number) => colors[Math.min(idx, colors.length - 1)];
  const axes = [
    <CartesianGrid key="grid" stroke={gridColor} vertical={false} />,
    <XAxis key="x" dataKey="label" stroke={axisColor} fontSize={11} tickLine={false} axisLine={false} minTickGap={12} tickFormatter={shortLabel} />,
    <YAxis
      key="y"
      stroke={axisColor}
      fontSize={11}
      tickLine={false}
      axisLine={false}
      width={56}
      tickFormatter={(v) => formats.axis.format(Number(v))}
    />,
    <Tooltip
      key="tip"
      cursor={chart.chart_type === "line" ? { stroke: gridColor } : { fill: gridColor, fillOpacity: 0.45 }}
      formatter={(value, key) => [value === null ? "—" : formats.value.format(Number(value)), names[String(key)] ?? String(key)]}
      contentStyle={{ borderRadius: 8, border: `1px solid ${gridColor}`, background: popoverColor, color: inkColor, fontSize: 12, boxShadow: "0 8px 24px rgb(0 0 0 / 0.12)" }}
    />
  ];

  return (
    <ResponsiveContainer width="100%" height="100%">
      {chart.chart_type === "line" ? (
        <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          {axes}
          {keys.map((key, i) => (
            <Line key={key} dataKey={key} stroke={colorFor(i)} strokeWidth={2} dot={data.length <= 24} isAnimationActive={false} />
          ))}
        </LineChart>
      ) : (
        <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }} barCategoryGap={chart.chart_type === "histogram" ? 1 : "20%"}>
          {axes}
          {keys.map((key, i) => (
            <Bar key={key} dataKey={key} fill={colorFor(i)} radius={[3, 3, 0, 0]} maxBarSize={chart.chart_type === "histogram" ? undefined : 36} isAnimationActive={false} />
          ))}
        </BarChart>
      )}
    </ResponsiveContainer>
  );
}
