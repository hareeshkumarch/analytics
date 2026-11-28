"use client";

import React, { useEffect, useState } from "react";
import { Icon } from "@/components/Icon";
import { errorMessage } from "@/lib/api/client";
import { EXPORT_FORMATS, downloadAnswerExport, type ExportFormat } from "@/lib/exportAnswer";
import { ActionMenu, type MenuAction } from "@/components/ActionMenu";

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
  const [busy, setBusy] = useState<ExportFormat["id"] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => setError(null), [runId]);

  async function download(format: ExportFormat) {
    if (!runId) return;
    setBusy(format.id);
    setError(null);
    try {
      await downloadAnswerExport({ workspaceId, runId, format, title: question });
    } catch (err) {
      setError(errorMessage(err, "Couldn't export this answer."));
    } finally {
      setBusy(null);
    }
  }

  const actions: MenuAction[] = [
    ...(onCopy ? [{ label: "Copy answer", onSelect: onCopy }] : []),
    ...(runId ? EXPORT_FORMATS.map((format) => ({ label: format.label, onSelect: () => void download(format) })) : []),
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
            <button
              type="button"
              onClick={() => setError(null)}
              aria-label="Dismiss export error"
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-secondary transition-colors hover:bg-panel hover:text-ink"
            >
              <Icon name="x" className="h-3.5 w-3.5" />
            </button>
          </>
        )
      }
    />
  );
}
