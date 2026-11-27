"use client";

import React, { useId } from "react";
import { Icon } from "@/components/Icon";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

/*
 * Control rules (keep every control on these, never restyle one at the call site):
 *   Height  32px default, 28px small. Controls that share a row share a height.
 *   Shape   8px corners on buttons, fields and chips; badges are pills.
 *   Fill    primary is near-black (near-white in dark); secondary is a 5% ink wash with a hairline ring;
 *           ghost has no fill until hovered. No coloured borders on hover.
 *   Text    14px at weight 450; small controls 13px.
 */
const btnBase = "inline-flex shrink-0 items-center justify-center rounded-md font-medium transition-colors disabled:cursor-not-allowed";
const btnMd = "min-h-[32px] gap-1.5 px-3 text-base";
const btnSmSize = "min-h-[28px] gap-1.5 px-2.5 text-sm";
const solid = "bg-accent text-on-accent hover:bg-accent-hover disabled:opacity-45";
/** Border, fill and hover shared by every outlined control: secondary buttons, chips, fields. */
export const outlineCls =
  "bg-ink/[0.05] ring-1 ring-inset ring-ink/10 transition-colors hover:bg-ink/[0.08] dark:bg-ink/[0.15] dark:ring-0 dark:hover:bg-ink/[0.22]";
const outlined = `${outlineCls} text-ink disabled:opacity-50`;
const quiet = "text-ink hover:bg-ink/[0.05] disabled:opacity-50 dark:hover:bg-ink/[0.08]";

export const btn = {
  primary: `${btnBase} ${btnMd} ${solid}`,
  secondary: `${btnBase} ${btnMd} ${outlined}`,
  ghost: `${btnBase} ${btnMd} ${quiet}`,
  /** Square icon-only button at the default height. */
  icon: `${btnBase} h-8 w-8 ${outlined}`,
  link: "inline-flex min-h-[32px] items-center gap-1.5 px-1 text-sm font-medium text-ink underline decoration-border-strong underline-offset-4 hover:decoration-ink",
};

export const btnSm = {
  primary: `${btnBase} ${btnSmSize} ${solid}`,
  secondary: `${btnBase} ${btnSmSize} ${outlined}`,
  ghost: `${btnBase} ${btnSmSize} ${quiet}`,
};

export const inputBaseCls =
  "w-full rounded-md border border-ink/[0.12] bg-surface px-2.5 text-base text-ink placeholder:text-secondary/80 transition-shadow focus:border-focus focus:outline-none focus:ring-[3px] focus:ring-focus/15 disabled:opacity-60 dark:bg-ink/[0.05]";

/** Default 32px field. */
export const inputCls = `min-h-[32px] ${inputBaseCls}`;
/** 28px field for filter bars, next to small buttons and chips. */
export const inputSmCls = `h-7 ${inputBaseCls}`;

export const cardCls = "rounded-xl border border-border bg-surface";

/** Heading for a card or page section. */
export const sectionTitleCls = "text-md font-semibold leading-snug text-ink";

/** Header row shared by every data table. */
export const tableHeadCls = "bg-panel text-xs text-secondary";

export function Spinner({ className = "h-3.5 w-3.5" }: { className?: string }) {
  return (
    <span aria-hidden="true" className={`${className} inline-block animate-spin rounded-full border-2 border-current border-t-transparent`} />
  );
}

export function Skeleton({ className }: { className: string }) {
  return <div aria-hidden="true" className={`animate-pulse rounded-lg bg-panel ${className}`} />;
}

/** Placeholder for a list or table while it loads. */
export function SkeletonRows({ rows = 4, rowClassName = "h-10" }: { rows?: number; rowClassName?: string }) {
  return (
    <div className="space-y-2" role="status">
      <span className="sr-only">Loading…</span>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className={rowClassName} />
      ))}
    </div>
  );
}

type Tone = "neutral" | "ok" | "warn" | "err" | "info";

const NOTICE_TONES: Record<Tone, string> = {
  neutral: "rounded-lg border border-border bg-panel text-body",
  info: "rounded-lg border border-border bg-panel text-body",
  ok: "rounded-lg border border-ok/25 bg-ok-soft text-ok",
  warn: "rounded-lg border border-warn/25 bg-warn-soft text-warn",
  err: "rounded-lg border border-err/25 bg-err-soft text-err",
};

export function Notice({
  tone,
  title,
  children,
  action,
  className = "",
}: {
  tone: Tone;
  title?: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      role={tone === "err" ? "alert" : "status"}
      className={cx("flex flex-wrap items-start justify-between gap-x-4 gap-y-2 px-3.5 py-2.5 text-sm leading-relaxed", NOTICE_TONES[tone], className)}
    >
      <div className="min-w-0 flex-1">
        {title && <p className="font-medium">{title}</p>}
        {children && <div className={title ? "mt-0.5 opacity-90" : ""}>{children}</div>}
      </div>
      {action && <div className="flex shrink-0 flex-wrap items-center gap-2">{action}</div>}
    </div>
  );
}

const BADGE_TONES: Record<Tone, { pill: string; dot: string }> = {
  neutral: { pill: "bg-panel text-body", dot: "bg-secondary" },
  info: { pill: "bg-panel text-body", dot: "bg-secondary" },
  ok: { pill: "bg-ok-soft text-ok", dot: "bg-ok-dot" },
  warn: { pill: "bg-warn-soft text-warn", dot: "bg-warn-dot" },
  err: { pill: "bg-err-soft text-err", dot: "bg-err-dot" },
};

export function Badge({
  tone = "neutral",
  dot = false,
  pulse = false,
  children,
}: {
  tone?: Tone;
  dot?: boolean;
  pulse?: boolean;
  children: React.ReactNode;
}) {
  const t = BADGE_TONES[tone];
  return (
    <span className={`inline-flex min-h-[24px] shrink-0 items-center gap-1.5 rounded-full px-2.5 text-xs font-medium ${t.pill}`}>
      {dot && <span aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${t.dot} ${pulse ? "animate-pulse" : ""}`} />}
      {children}
    </span>
  );
}

export function SearchInput({
  value,
  onChange,
  placeholder,
  label,
  className = "",
  dense = false,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  label: string;
  className?: string;
  dense?: boolean;
}) {
  return (
    <div className={cx("relative min-w-0", className)}>
      <Icon name="search" className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-secondary" />
      <input
        type="search"
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape" && value) {
            e.preventDefault();
            onChange("");
          }
        }}
        placeholder={placeholder}
        className={cx(
          dense ? inputSmCls : inputCls,
          "pl-8 pr-8",
          "[&::-webkit-search-cancel-button]:hidden"
        )}
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange("")}
          aria-label="Clear search"
          className="absolute right-1 top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-full text-secondary hover:text-ink"
        >
          <Icon name="x" className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}

export interface ChipOption<T extends string> {
  value: T;
  label: string;
  count?: number;
}

/** A row of mutually exclusive filter buttons. Wraps on small screens. */
export function FilterChips<T extends string>({
  label,
  options,
  value,
  onChange,
  className = "",
  fill = false,
}: {
  label: string;
  options: readonly ChipOption<T>[];
  value: T;
  onChange: (value: T) => void;
  className?: string;
  /** Tighter padding and equal-width chips for narrow columns. Height, shape and border stay the same. */
  fill?: boolean;
}) {
  return (
    <div role="group" aria-label={label} className={cx("flex", fill ? "flex-nowrap gap-1" : "flex-wrap gap-1.5", className)}>
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(o.value)}
            className={cx(
              "inline-flex min-h-[28px] items-center gap-1.5 whitespace-nowrap rounded-md transition-colors",
              fill ? "flex-auto justify-center px-1.5 text-xs" : "px-2.5 text-sm",
              active ? "bg-ink/[0.07] font-medium text-ink dark:bg-ink/[0.15]" : "text-secondary hover:bg-ink/[0.05] hover:text-ink"
            )}
          >
            {o.label}
            {o.count !== undefined && (
              <span className={cx("tabular-nums text-2xs", active ? "text-accent" : "text-secondary")}>{o.count}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}

export function EmptyState({
  title,
  body,
  action,
  icon,
  className = "",
}: {
  title: string;
  body?: React.ReactNode;
  action?: React.ReactNode;
  icon?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cx("flex flex-col items-center gap-2 rounded-lg bg-panel px-6 py-10 text-center", className)}>
      {icon && <div className="mb-1 flex h-10 w-10 items-center justify-center rounded-full bg-surface text-ink ring-1 ring-border">{icon}</div>}
      <p className="text-base font-medium text-ink">{title}</p>
      {body && <div className="max-w-md text-sm leading-relaxed text-secondary">{body}</div>}
      {action && <div className="mt-2 flex flex-wrap justify-center gap-2">{action}</div>}
    </div>
  );
}

/** Collapsible "technical details" block for information most users don't need. */
export function Details({ summary, children, className = "" }: { summary: string; children: React.ReactNode; className?: string }) {
  const id = useId();
  return (
    <details className={cx("group", className)}>
      <summary
        id={id}
        className="inline-flex min-h-[32px] cursor-pointer list-none items-center gap-1.5 px-1 text-sm text-secondary transition-colors hover:text-ink [&::-webkit-details-marker]:hidden"
      >
        <Icon name="chevron-right" className="h-3 w-3 transition-transform group-open:rotate-90" />
        {summary}
      </summary>
      <div className="mt-2">{children}</div>
    </details>
  );
}
