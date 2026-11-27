"use client";

import React from "react";
import { Icon } from "@/components/Icon";
import { useToastStore } from "@/lib/toast";

/** Corner stack of short confirmations. Sits above the phone tab bar. */
export function Toaster() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);
  return (
    <div
      role="status"
      aria-live="polite"
      className="pointer-events-none fixed inset-x-4 bottom-[calc(4.75rem+env(safe-area-inset-bottom))] z-[70] flex flex-col items-center gap-2 sm:inset-x-auto sm:bottom-5 sm:right-5 sm:items-end"
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          className="pointer-events-auto flex max-w-[min(420px,100%)] items-center gap-2.5 rounded-xl border border-ink/[0.08] bg-popover py-2 pl-3.5 pr-1.5 text-sm text-ink shadow-pop"
        >
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-ok-soft text-ok">
            <Icon name="check" className="h-3 w-3" strokeWidth={2.5} />
          </span>
          <span className="min-w-0 flex-1 leading-snug">{t.text}</span>
          <button
            type="button"
            onClick={() => dismiss(t.id)}
            aria-label="Dismiss"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-secondary transition-colors hover:bg-sidebar hover:text-ink"
          >
            <Icon name="x" className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}
