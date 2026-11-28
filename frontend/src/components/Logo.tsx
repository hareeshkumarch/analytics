import React from "react";

/**
 * Ledgerline mark: an "L" drawn as chart axes with a rising line inside it.
 * The tile uses the CTA colour (near-black in light, near-white in dark).
 */
export function LogoMark({ className = "h-8 w-8" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={`${className} shrink-0`} aria-hidden="true">
      <rect width="32" height="32" rx="8" className="fill-brand" />
      <g fill="none" className="stroke-on-accent" strokeLinecap="round" strokeLinejoin="round">
        <path d="M9.5 8v14.5H23" strokeWidth="2.6" />
        <path d="m14 17.5 3.4-4 3 2.4 3.6-5" strokeWidth="2" opacity="0.8" />
      </g>
      <circle cx="24" cy="10.9" r="1.7" className="fill-on-accent" />
    </svg>
  );
}
