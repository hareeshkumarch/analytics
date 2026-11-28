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
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){var t=null;try{t=localStorage.getItem("ledgerline.theme");}catch(e){}if(t!=="light"&&t!=="dark"){t=window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";}document.documentElement.setAttribute("data-theme",t);})();`,
          }}
        />
        <style>{`
          :root { --bg: #F7F6F2; --fg: #111111; --muted: #787671; --btn-bg: #111111; --btn-fg: #FFFFFF; --bd: #8E8D88; --sec-bg: rgba(17,17,17,0.05); }
          :root[data-theme="dark"] { --bg: #191816; --fg: #EFEEEA; --muted: #B0AEA8; --btn-bg: #EFEEEA; --btn-fg: #191816; --bd: #716F6A; --sec-bg: rgba(239,238,234,0.15); }
          body { background: var(--bg); color: var(--fg); font-family: Inter, system-ui, sans-serif; font-size: 14px; margin: 0; padding: 4rem 1rem; }
          main { margin: 0 auto; max-width: 32rem; }
          .actions { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 1rem; }
          .btn { display: inline-flex; min-height: 32px; align-items: center; padding: 0 0.75rem; font-size: 0.875rem; font-weight: 450; text-decoration: none; cursor: pointer; border-radius: 6px; }
          .btn-primary { background: var(--btn-bg); color: var(--btn-fg); border: 1px solid var(--btn-bg); }
          .btn-secondary { background: var(--sec-bg); color: var(--fg); border: 1px solid var(--bd); }
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
