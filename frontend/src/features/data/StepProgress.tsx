import React from "react";
import { Icon } from "@/components/Icon";
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
              className={cx("flex items-center gap-2 text-sm", active ? "font-medium text-ink" : done ? "text-body" : "text-secondary")}
            >
              <span
                aria-hidden="true"
                className={cx(
                  "flex h-5 w-5 shrink-0 rounded-full items-center justify-center text-2xs tabular-nums",
                  done ? "bg-accent text-on-accent" : active ? "border border-accent text-accent" : "border border-border"
                )}
              >
                {active && busy ? (
                  <Spinner className="h-2.5 w-2.5" />
                ) : done ? (
                  <Icon name="check" className="h-3 w-3" />
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
