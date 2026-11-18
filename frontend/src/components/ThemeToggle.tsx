"use client";

import React from "react";
import { ThemePreference, useTheme } from "@/lib/theme";

const OPTIONS: { value: ThemePreference; label: string }[] = [
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

function ThemeIcon({ name, className = "h-[16px] w-[16px]" }: { name: ThemePreference; className?: string }) {
  const props = {
    className: `${className} shrink-0`,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.7,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  if (name === "light") {
    return (
      <svg {...props}>
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
      </svg>
    );
  }
  if (name === "dark") {
    return (
      <svg {...props}>
        <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
      </svg>
    );
  }
  return (
    <svg {...props}>
      <rect x="3" y="4" width="18" height="12" />
      <path d="M8 20h8M12 16v4" />
    </svg>
  );
}

const NEXT: Record<ThemePreference, ThemePreference> = { system: "light", light: "dark", dark: "system" };

export function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const { preference, choose } = useTheme();

  if (compact) {
    const current = OPTIONS.find((o) => o.value === preference)?.label ?? "System";
    return (
      <button
        type="button"
        onClick={() => choose(NEXT[preference])}
        aria-label={`Theme: ${current}. Switch to ${NEXT[preference]}`}
        title={`Theme: ${current}`}
        className="flex min-h-[40px] min-w-[40px] items-center justify-center border border-border bg-canvas text-secondary transition-colors hover:border-border-strong hover:text-ink"
      >
        <ThemeIcon name={preference} />
      </button>
    );
  }

  return (
    <div role="radiogroup" aria-label="Theme" className="grid grid-cols-3 border border-border bg-canvas p-0.5">
      {OPTIONS.map((o) => {
        const active = preference === o.value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => choose(o.value)}
            className={`flex min-h-[32px] items-center justify-center gap-1.5 text-[12px] transition-colors ${
              active ? "bg-surface font-medium text-ink shadow-card ring-1 ring-border" : "text-secondary hover:text-ink"
            }`}
          >
            <ThemeIcon name={o.value} className="h-[14px] w-[14px]" />
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
