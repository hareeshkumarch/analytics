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
  const gridColor = useThemeColor("border", "#E6E4DF");
  const axisColor = useThemeColor("muted", "#787671");
  const barColor = useThemeColor("data-1", "#257DE0");
  const popoverColor = useThemeColor("popover", "#FFFFFF");
  const inkColor = useThemeColor("ink", "#111111");
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
        <CartesianGrid stroke={gridColor} vertical={false} />
        <XAxis dataKey="label" stroke={axisColor} fontSize={11} tickLine={false} axisLine={false} minTickGap={12} />
        <YAxis stroke={axisColor} fontSize={11} tickLine={false} axisLine={false} width={56} tickFormatter={(v) => formatAxis(Number(v))} />
        <Tooltip
          cursor={{ fill: gridColor, fillOpacity: 0.45 }}
          formatter={(value) => [formatValue(Number(value)), seriesLabel]}
          contentStyle={{ borderRadius: 8, border: `1px solid ${gridColor}`, background: popoverColor, color: inkColor, fontSize: 12, boxShadow: "0 8px 24px rgb(0 0 0 / 0.12)" }}
        />
        <Bar dataKey={dataKey} fill={barColor} maxBarSize={36} radius={[4, 4, 0, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}
