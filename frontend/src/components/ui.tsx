"use client";

import React, { useId } from "react";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export const btn = {
  primary:
    "inline-flex min-h-[40px] items-center justify-center gap-2 bg-plum px-[18px] text-[13.5px] font-medium text-on-accent transition-colors hover:bg-plum-deep disabled:cursor-not-allowed disabled:opacity-45",
  secondary:
    "inline-flex min-h-[40px] items-center justify-center gap-2 border border-border-strong bg-surface px-[18px] text-[13.5px] text-ink transition-colors hover:border-plum disabled:cursor-not-allowed disabled:opacity-60",
  ghost:
    "inline-flex min-h-[40px] items-center justify-center gap-1.5 px-3 text-[13px] text-secondary transition-colors hover:bg-sidebar hover:text-ink disabled:cursor-not-allowed disabled:opacity-60",
  link: "inline-flex min-h-[40px] items-center gap-1.5 px-1 text-[13px] font-medium text-ink underline decoration-border-strong underline-offset-4 hover:decoration-ink",
};

export const btnSm = {
  primary:
    "inline-flex min-h-[34px] items-center justify-center gap-1.5 bg-plum px-3.5 text-[13px] font-medium text-on-accent transition-colors hover:bg-plum-deep disabled:cursor-not-allowed disabled:opacity-45",
  secondary:
    "inline-flex min-h-[34px] items-center justify-center gap-1.5 border border-border-strong bg-surface px-3.5 text-[13px] text-ink transition-colors hover:border-plum disabled:cursor-not-allowed disabled:opacity-60",
};

export const inputBaseCls =
  "w-full border border-border-strong bg-surface px-3 text-sm text-ink placeholder:text-secondary transition-colors focus:border-plum focus:outline-none focus:ring-[3px] focus:ring-plum-soft disabled:opacity-60";

export const inputCls = `min-h-[44px] ${inputBaseCls}`;

export const cardCls = " border border-border bg-surface shadow-card";

export function Spinner({ className = "h-3.5 w-3.5" }: { className?: string }) {
  return (
    <span aria-hidden="true" className={`${className} inline-block animate-spin rounded-full border-2 border-current border-t-transparent`} />
  );
}

export function Skeleton({ className }: { className: string }) {
  return <div aria-hidden="true" className={`animate-pulse bg-panel ${className}`} />;
}

type Tone = "neutral" | "ok" | "warn" | "err" | "info";

const NOTICE_TONES: Record<Tone, string> = {
  neutral: "border border-border bg-panel text-body",
  info: "border border-border bg-panel text-body",
  ok: "border border-border bg-ok-soft text-ok",
  warn: "border border-warn/25 bg-warn-soft text-warn",
  err: "border border-err/25 bg-err-soft text-err",
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
      className={cx("flex flex-wrap items-start justify-between gap-x-4 gap-y-2 px-3.5 py-2.5 text-[13px] leading-relaxed", NOTICE_TONES[tone], className)}
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
    <span className={`inline-flex min-h-[24px] shrink-0 items-center gap-1.5 px-2 text-[11.5px] font-medium ${t.pill}`}>
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
      <svg
        aria-hidden="true"
        className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-secondary"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      >
        <circle cx="11" cy="11" r="7" />
        <path d="m20 20-3.5-3.5" />
      </svg>
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
          inputBaseCls,
          dense ? "h-9 py-1.5 pl-9 pr-9" : "min-h-[40px] pl-9 pr-9",
          "[&::-webkit-search-cancel-button]:hidden"
        )}
      />
      {value && (
        <button
          type="button"
          onClick={() => onChange("")}
          aria-label="Clear search"
          className="absolute right-1 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center text-secondary hover:text-ink"
        >
          <svg aria-hidden="true" className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
            <path d="M18 6 6 18M6 6l12 12" />
          </svg>
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
}: {
  label: string;
  options: ChipOption<T>[];
  value: T;
  onChange: (value: T) => void;
  className?: string;
}) {
  return (
    <div role="group" aria-label={label} className={cx("flex flex-wrap gap-1.5", className)}>
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(o.value)}
            className={cx(
              "inline-flex min-h-[34px] items-center gap-1.5 border px-3 text-[12.5px] transition-colors",
              active ? "border-ink bg-ink font-medium text-canvas" : "border-border bg-surface text-body hover:border-border-strong hover:text-ink"
            )}
          >
            {o.label}
            {o.count !== undefined && (
              <span className={cx("tabular-nums text-[11px]", active ? "text-canvas/70" : "text-secondary")}>{o.count}</span>
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
    <div className={cx("flex flex-col items-center gap-2 bg-panel px-6 py-10 text-center", className)}>
      {icon && <div className="mb-1 flex h-10 w-10 items-center justify-center bg-surface text-ink ring-1 ring-border">{icon}</div>}
      <p className="text-[14.5px] font-medium text-ink">{title}</p>
      {body && <div className="max-w-md text-[13px] leading-relaxed text-secondary">{body}</div>}
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
        className="inline-flex min-h-[32px] cursor-pointer list-none items-center gap-1.5 px-1 text-[12.5px] text-secondary transition-colors hover:text-ink [&::-webkit-details-marker]:hidden"
      >
        <svg aria-hidden="true" className="h-3 w-3 transition-transform group-open:rotate-90" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2">
          <path d="m9 18 6-6-6-6" />
        </svg>
        {summary}
      </summary>
      <div className="mt-2">{children}</div>
    </details>
  );
}
