"use client";

import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api/client";
import { useWorkspace } from "@/lib/workspace";

interface ColumnProfile {
  column_name: string;
  null_count: number;
  distinct_count: number;
  duplicate_count: number;
  min_value: string | null;
  max_value: string | null;
  is_sampled: boolean;
}

interface SnapshotPreview {
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
}

interface SnapshotDiagnostics {
  available: boolean;
  diagnostics: {
    extraction_started_at?: string;
    extraction_completed_at?: string;
    is_complete?: boolean;
    is_sampled?: boolean;
    warnings?: unknown[];
    source_summary?: Record<string, string>;
  } | null;
  consistency_level: string | null;
  total_rows: number;
  total_normalized_bytes: number;
}

const PREVIEW_ROWS = 100;

function tableQuery(table: string, extra = ""): string {
  return `?table=${encodeURIComponent(table)}${extra}`;
}

export function useSnapshotProfile(snapshotId: string, table: string) {
  const ws = useWorkspace();
  return useQuery({
    queryKey: ["snapshot-profile", ws.id, snapshotId, table],
    queryFn: () =>
      apiFetch<{ columns: ColumnProfile[] }>(ws.path(`/snapshots/${encodeURIComponent(snapshotId)}/profile${tableQuery(table)}`)),
    enabled: Boolean(table),
    staleTime: Infinity,
  });
}

export function useSnapshotPreview(snapshotId: string, table: string) {
  const ws = useWorkspace();
  return useQuery({
    queryKey: ["snapshot-preview", ws.id, snapshotId, table, PREVIEW_ROWS],
    queryFn: () =>
      apiFetch<SnapshotPreview>(
        ws.path(`/snapshots/${encodeURIComponent(snapshotId)}/preview${tableQuery(table, `&limit=${PREVIEW_ROWS}`)}`)
      ),
    enabled: Boolean(table),
    staleTime: Infinity,
  });
}

export function useSnapshotDiagnostics(snapshotId: string) {
  const ws = useWorkspace();
  return useQuery({
    queryKey: ["snapshot-diagnostics", ws.id, snapshotId],
    queryFn: () => apiFetch<SnapshotDiagnostics>(ws.path(`/snapshots/${encodeURIComponent(snapshotId)}/diagnostics`)),
    staleTime: Infinity,
  });
}
