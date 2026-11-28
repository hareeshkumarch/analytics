"use client";

import React, { useRef } from "react";
import { Icon } from "@/components/Icon";
import { ThemePreference, useTheme } from "@/lib/theme";

const OPTIONS: { value: ThemePreference; label: string }[] = [
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

function ThemeIcon({ name, className = "h-[16px] w-[16px]" }: { name: ThemePreference; className?: string }) {
  return <Icon name={name === "light" ? "sun" : name === "dark" ? "moon" : "monitor"} className={className} />;
}

const NEXT: Record<ThemePreference, ThemePreference> = { system: "light", light: "dark", dark: "system" };

export function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const { preference, choose } = useTheme();
  const radios = useRef<(HTMLButtonElement | null)[]>([]);

  function onRadioKey(e: React.KeyboardEvent, index: number) {
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = (index + step + OPTIONS.length) % OPTIONS.length;
    choose(OPTIONS[next].value);
    radios.current[next]?.focus();
  }

  if (compact) {
    const current = OPTIONS.find((o) => o.value === preference)?.label ?? "System";
    return (
      <button
        type="button"
        onClick={() => choose(NEXT[preference])}
        aria-label={`Theme: ${current}. Switch to ${NEXT[preference]}`}
        title={`Theme: ${current}`}
        className="flex h-8 w-8 items-center justify-center rounded-md text-secondary transition-colors hover:bg-ink/[0.05] hover:text-ink dark:hover:bg-ink/[0.08]"
      >
        <ThemeIcon name={preference} />
      </button>
    );
  }

  return (
    <div role="radiogroup" aria-label="Theme" className="grid grid-cols-3 rounded-md bg-ink/[0.05] p-0.5">
      {OPTIONS.map((o, index) => {
        const active = preference === o.value;
        return (
          <button
            key={o.value}
            ref={(el) => {
              radios.current[index] = el;
            }}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={active ? 0 : -1}
            onKeyDown={(e) => onRadioKey(e, index)}
            onClick={() => choose(o.value)}
            className={`flex h-7 items-center rounded justify-center gap-1.5 text-xs transition-colors ${
              active ? "bg-popover font-medium text-ink shadow-[0_0_0_1px_rgb(var(--ink)/0.08)]" : "text-body hover:text-ink"
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
