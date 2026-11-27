"use client";

import React from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useThemeColor } from "@/lib/theme";

export function UsageBarChart({
  data,
  dataKey,
  seriesLabel,
  formatAxis,
  formatValue,
}: {
  data: Record<string, string | number>[];
  dataKey: string;
  seriesLabel: string;
  formatAxis: (value: number) => string;
  formatValue: (value: number) => string;
}) {
  const gridColor = useThemeColor("border", "#E4E4E7");
  const axisColor = useThemeColor("muted", "#696972");
  const barColor = useThemeColor("data-1", "#1B6E9C");
  const surfaceColor = useThemeColor("surface", "#FFFFFF");
  const inkColor = useThemeColor("ink", "#18181B");
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -8 }}>
        <CartesianGrid stroke={gridColor} vertical={false} />
        <XAxis dataKey="label" stroke={axisColor} fontSize={11} tickLine={false} axisLine={false} minTickGap={12} />
        <YAxis stroke={axisColor} fontSize={11} tickLine={false} axisLine={false} width={48} tickFormatter={(v) => formatAxis(Number(v))} />
        <Tooltip
          cursor={{ fill: gridColor, fillOpacity: 0.45 }}
          formatter={(value) => [formatValue(Number(value)), seriesLabel]}
          contentStyle={{ borderRadius: 0, border: `1px solid ${gridColor}`, background: surfaceColor, color: inkColor, fontSize: 12 }}
        />
        <Bar dataKey={dataKey} fill={barColor} maxBarSize={36} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}
