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
  const gridColor = useThemeColor("border", "#E4E4E7");
  const axisColor = useThemeColor("muted", "#696972");
  const surfaceColor = useThemeColor("surface", "#FFFFFF");
  const inkColor = useThemeColor("ink", "#18181B");
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
      width={48}
      tickFormatter={(v) => formats.axis.format(Number(v))}
    />,
    <Tooltip
      key="tip"
      cursor={chart.chart_type === "line" ? { stroke: gridColor } : { fill: gridColor, fillOpacity: 0.45 }}
      formatter={(value, key) => [value === null ? "—" : formats.value.format(Number(value)), names[String(key)] ?? String(key)]}
      contentStyle={{ borderRadius: 0, border: `1px solid ${gridColor}`, background: surfaceColor, color: inkColor, fontSize: 12 }}
    />
  ];

  return (
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
  );
}
