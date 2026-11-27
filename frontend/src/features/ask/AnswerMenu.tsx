"use client";

import React, { useEffect, useState } from "react";
import { API_BASE, apiFetch, errorMessage } from "@/lib/api/client";
import { ActionMenu, type MenuAction } from "@/components/ActionMenu";
import { workspacePath } from "@/lib/workspace";
import type { CreateExportRequest } from "@/lib/api/types";

const FORMATS = [
  { id: "pdf", label: "Export as PDF", ext: "pdf" },
  { id: "docx", label: "Export as Word document", ext: "docx" },
  { id: "html", label: "Export as web page (HTML)", ext: "html" },
] as const;

type Format = (typeof FORMATS)[number];

function fileName(question: string, ext: string): string {
  const slug = question.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60);
  return `${slug || "answer"}.${ext}`;
}

export function AnswerMenu({
  workspaceId,
  runId,
  question,
  copied,
  onCopy,
  onDelete,
}: {
  workspaceId: string;
  runId: string | null;
  question: string;
  copied: boolean;
  onCopy?: () => void;
  onDelete?: () => void;
}) {
  const [busy, setBusy] = useState<Format["id"] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setError(null), [runId]);

  async function download(format: Format) {
    if (!runId) return;
    setBusy(format.id);
    setError(null);
    try {
      const created = await apiFetch<{ artifact_id: string }>(workspacePath(workspaceId, "/exports"), {
        method: "POST",
        body: JSON.stringify({ run_id: runId, format: format.id } satisfies CreateExportRequest),
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

  const actions: MenuAction[] = [
    ...(onCopy ? [{ label: "Copy answer", onSelect: onCopy }] : []),
    ...(runId ? FORMATS.map((format) => ({ label: format.label, onSelect: () => void download(format) })) : []),
    ...(onDelete ? [{ label: "Delete answer…", onSelect: onDelete, danger: true }] : []),
  ];

  return (
    <ActionMenu
      menuLabel="Answer actions"
      triggerText={busy ? "Exporting…" : copied ? "Copied" : "More"}
      actions={actions}
      disabled={busy !== null}
      notice={
        error && (
          <>
            <span className="flex-1">{error}</span>
            <button type="button" onClick={() => setError(null)} aria-label="Dismiss export error" className="shrink-0 rounded-full text-secondary hover:text-ink">
              ×
            </button>
          </>
        )
      }
    />
  );
}
