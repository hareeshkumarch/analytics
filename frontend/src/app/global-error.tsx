"use client";

import { useEffect } from "react";

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <html lang="en">
      <head>
        <meta name="color-scheme" content="light dark" />
        <style>{`
          :root { --bg: #FAFAFA; --fg: #18181B; --muted: #696972; --btn-bg: #18181B; --btn-fg: #FFFFFF; --bd: #8C8C96; }
          @media (prefers-color-scheme: dark) {
            :root { --bg: #121214; --fg: #F4F4F5; --muted: #A1A1AA; --btn-bg: #F4F4F5; --btn-fg: #18181B; --bd: #71717A; }
          }
          body { background: var(--bg); color: var(--fg); font-family: system-ui, sans-serif; margin: 0; padding: 4rem 1rem; }
          main { margin: 0 auto; max-width: 32rem; }
          .actions { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 1rem; }
          .btn { display: inline-flex; min-height: 40px; align-items: center; padding: 0 1.125rem; font-size: 0.875rem; font-weight: 500; text-decoration: none; cursor: pointer; border-radius: 0; }
          .btn-primary { background: var(--btn-bg); color: var(--btn-fg); border: 1px solid var(--btn-bg); }
          .btn-secondary { background: transparent; color: var(--fg); border: 1px solid var(--bd); }
        `}</style>
      </head>
      <body>
        <main role="alert">
          <h1 style={{ fontSize: "1.5rem", margin: 0 }}>Ledgerline couldn&apos;t load</h1>
          <p style={{ color: "var(--muted)", lineHeight: 1.6 }}>Something went wrong before the page could start. Your data and runs are safe.</p>
          <div className="actions">
            <button type="button" onClick={reset} className="btn btn-primary">
              Try again
            </button>
            <a href="/ask" className="btn btn-secondary">
              Go to Ask
            </a>
          </div>
          {error.digest && <p style={{ fontFamily: "monospace", fontSize: "0.75rem", color: "var(--muted)", marginTop: "1rem" }}>Reference: {error.digest}</p>}
        </main>
      </body>
    </html>
  );
}
