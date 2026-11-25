"use client";

import { useEffect } from "react";

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", margin: "4rem auto", maxWidth: "32rem", padding: "0 1rem" }}>
        <h1 style={{ fontSize: "1.5rem" }}>Ledgerline couldn&apos;t load</h1>
        <p>Something went wrong before the page could start. Your data and runs are safe.</p>
        <button type="button" onClick={reset} style={{ padding: "0.6rem 1.1rem" }}>
          Try again
        </button>
        {error.digest && <p style={{ fontFamily: "monospace", fontSize: "0.75rem" }}>Reference: {error.digest}</p>}
      </body>
    </html>
  );
}
