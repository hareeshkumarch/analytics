"use client";

import Link from "next/link";
import { useEffect } from "react";

export default function RouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="mx-auto flex max-w-lg flex-col items-start gap-4 py-16" role="alert">
      <h1 className="font-display text-[1.6rem] font-semibold tracking-[-0.02em] text-ink">This page hit a problem</h1>
      <p className="text-[14.5px] leading-relaxed text-secondary">
        Something on this page couldn&apos;t be shown. Your data and runs are safe. Try again, or go back to Ask.
      </p>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={reset}
          className="inline-flex min-h-[40px] items-center bg-plum px-[18px] text-[13.5px] font-medium text-on-accent hover:bg-plum-deep"
        >
          Try again
        </button>
        <Link href="/ask" className="inline-flex min-h-[40px] items-center border border-border-strong bg-surface px-[18px] text-[13.5px] text-ink hover:border-plum">
          Go to Ask
        </Link>
      </div>
      {error.digest && <p className="font-mono text-[11.5px] text-secondary">Reference: {error.digest}</p>}
    </div>
  );
}
