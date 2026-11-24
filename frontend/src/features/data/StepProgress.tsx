import React from "react";
import { Spinner, cx } from "@/components/ui";

interface Step {
  key: string;
  label: string;
}

export function StepProgress({ steps, current, busy, status }: { steps: Step[]; current: number; busy: boolean; status: string }) {
  return (
    <div className="space-y-2">
      <ol aria-label="Import steps" className="flex flex-wrap gap-x-4 gap-y-2">
        {steps.map((s, i) => {
          const done = i < current;
          const active = i === current;
          return (
            <li
              key={s.key}
              aria-current={active ? "step" : undefined}
              className={cx("flex items-center gap-2 text-[12.5px]", active ? "font-medium text-ink" : done ? "text-body" : "text-secondary")}
            >
              <span
                aria-hidden="true"
                className={cx(
                  "flex h-5 w-5 shrink-0 items-center justify-center text-[11px] tabular-nums",
                  done ? "bg-ink text-canvas" : active ? "border border-ink" : "border border-border"
                )}
              >
                {active && busy ? (
                  <Spinner className="h-2.5 w-2.5" />
                ) : done ? (
                  <svg className="h-3 w-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
                    <path d="M20 6 9 17l-5-5" />
                  </svg>
                ) : (
                  i + 1
                )}
              </span>
              {s.label}
              {done && <span className="sr-only">(done)</span>}
            </li>
          );
        })}
      </ol>
      <p aria-live="polite" className="min-h-[18px] text-xs text-secondary">
        {status}
      </p>
    </div>
  );
}
