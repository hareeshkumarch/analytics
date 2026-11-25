"use client";

import React, { useEffect, useRef, useState } from "react";
import { API_BASE, apiFetch, errorMessage } from "@/lib/api/client";
import { btn } from "@/components/ui";
import { workspacePath } from "@/lib/workspace";

const FORMATS = [
  { id: "pdf", label: "PDF", ext: "pdf" },
  { id: "docx", label: "Word document", ext: "docx" },
  { id: "html", label: "Web page (HTML)", ext: "html" },
] as const;

type Format = (typeof FORMATS)[number];

function fileName(question: string, ext: string): string {
  const slug = question.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60);
  return `${slug || "answer"}.${ext}`;
}

export function ExportMenu({ workspaceId, runId, question }: { workspaceId: string; runId: string; question: string }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<Format["id"] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      trigger.current?.focus();
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    root.current?.querySelector<HTMLButtonElement>("[role=menuitem]")?.focus();
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => setError(null), [runId]);

  async function download(format: Format) {
    setOpen(false);
    setBusy(format.id);
    setError(null);
    try {
      const created = await apiFetch<{ artifact_id: string }>(workspacePath(workspaceId, "/exports"), {
        method: "POST",
        body: JSON.stringify({ run_id: runId, format: format.id }),
      });
      const res = await fetch(`${API_BASE}${workspacePath(workspaceId, `/exports/${encodeURIComponent(created.artifact_id)}`)}`);
      if (!res.ok) throw new Error(`The export couldn't be downloaded (HTTP ${res.status}).`);
      const url = URL.createObjectURL(await res.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = fileName(question, format.ext);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setError(errorMessage(err, "Couldn't export this answer."));
    } finally {
      setBusy(null);
    }
  }

  function onMenuKey(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const items = Array.from(e.currentTarget.querySelectorAll<HTMLButtonElement>("[role=menuitem]"));
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    const next = (index + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items[next]?.focus();
  }

  return (
    <div ref={root} className="relative">
      <button
        ref={trigger}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={busy !== null}
        onClick={() => {
          setError(null);
          setOpen((v) => !v);
        }}
        className={btn.ghost}
      >
        {busy ? "Exporting…" : "Export"}
      </button>
      {open && (
        <div
          role="menu"
          tabIndex={-1}
          aria-label="Export answer"
          onKeyDown={onMenuKey}
          className="absolute right-0 top-full z-30 mt-1 min-w-[200px] border border-border bg-surface py-1 shadow-pop"
        >
          {FORMATS.map((format) => (
            <button
              key={format.id}
              type="button"
              role="menuitem"
              onClick={() => void download(format)}
              className="block w-full px-3.5 py-2 text-left text-[13px] text-body hover:bg-sidebar hover:text-ink focus:bg-sidebar focus:text-ink focus:outline-none"
            >
              {format.label}
            </button>
          ))}
        </div>
      )}
      {error && (
        <p role="alert" className="absolute right-0 top-full z-20 mt-1 w-max max-w-[280px] border border-border bg-surface px-3 py-2 text-[12px] text-err shadow-pop">
          {error}
        </p>
      )}
    </div>
  );
}
