"use client";

import React, { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { apiFetch, errorMessage } from "@/lib/api/client";
import { SAMPLE_NAME } from "@/lib/sample";
import { SnapshotSummary, useWorkspace } from "@/lib/workspace";
import { Spinner, btn } from "@/components/ui";

export function SampleDataButton({
  onImported,
  className = btn.secondary,
}: {
  onImported: (snapshot: SnapshotSummary) => void;
  className?: string;
}) {
  const ws = useWorkspace();
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function importSample() {
    setBusy(true);
    setError(null);
    try {
      const res = await apiFetch<{ snapshot: SnapshotSummary }>(ws.path(`/samples/${SAMPLE_NAME}/import`), { method: "POST" });
      await queryClient.invalidateQueries({ queryKey: ["snapshots", ws.id] });
      onImported(res.snapshot);
    } catch (err) {
      setError(errorMessage(err, "Couldn't import the sample data."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="inline-flex flex-col items-start gap-1">
      <button type="button" className={className} onClick={() => void importSample()} disabled={busy}>
        {busy && <Spinner />}
        {busy ? "Importing sample…" : "Try with sample data"}
      </button>
      {error && (
        <span role="alert" className="text-xs text-err">
          {error}
        </span>
      )}
    </span>
  );
}
