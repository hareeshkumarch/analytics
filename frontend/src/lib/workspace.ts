"use client";

import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api/client";
import { useUiStore } from "@/lib/store";

export function workspacePath(workspaceId: string, path: string): string {
  return `/workspaces/${encodeURIComponent(workspaceId)}${path}`;
}

export function useWorkspace() {
  const id = useUiStore((s) => s.activeWorkspaceId);
  return { id, path: (p: string) => workspacePath(id, p) };
}

export interface RunLimits {
  total_model_attempts: number;
  max_replans: number;
  active_seconds: number;
}

export interface HealthResponse {
  status: string;
  connectors: string[];
  providers: string[];
  limits?: Record<string, number>;
  run_limits?: Record<string, RunLimits>;
}

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: () => apiFetch<HealthResponse>("/health"),
    staleTime: 5 * 60 * 1000,
  });
}

export interface ColumnDetail {
  name: string;
  type: string;
  nullable: boolean;
  leading_zero_id: boolean;
  currency: string | null;
}

export interface SnapshotSummary {
  snapshot_id: string;
  source_id: string;
  version: number;
  total_rows: number;
  tables: string[];
  semantic_version_id?: string | null;
  approved?: boolean;
  created_at?: string | null;
  source_family?: string | null;
  total_normalized_bytes?: number;
  consistency_level?: string | null;
  sha256?: string;
  table_details?: { name: string; row_count: number; normalized_bytes?: number; columns: ColumnDetail[] }[];
}

function createdTime(s: SnapshotSummary): number {
  const t = s.created_at ? Date.parse(s.created_at) : NaN;
  return Number.isNaN(t) ? -Infinity : t;
}

/**
 * Newest first. The API returns newest first today, but older in-memory fallbacks did not, so the
 * order is made explicit here. Items without created_at keep their relative API order (stable sort).
 */
export function sortSnapshotsNewestFirst<T extends SnapshotSummary>(list: T[]): T[] {
  return list
    .map((item, index) => ({ item, index }))
    .sort((a, b) => {
      const diff = createdTime(b.item) - createdTime(a.item);
      if (diff !== 0 && Number.isFinite(diff)) return diff;
      if (createdTime(a.item) === -Infinity && createdTime(b.item) !== -Infinity) return 1;
      if (createdTime(b.item) === -Infinity && createdTime(a.item) !== -Infinity) return -1;
      return a.index - b.index;
    })
    .map(({ item }) => item);
}

/** Human name for a dataset: its first table, plus how many more. */
export function datasetLabel(s: Pick<SnapshotSummary, "tables" | "source_id">): string {
  if (!s.tables.length) return s.source_id;
  return s.tables.length === 1 ? s.tables[0] : `${s.tables[0]} +${s.tables.length - 1} more`;
}

function selectSortedSnapshots<T extends SnapshotSummary>(data: { snapshots: T[] }): { snapshots: T[] } {
  return { snapshots: sortSnapshotsNewestFirst(data.snapshots ?? []) };
}

export function useSnapshots<T extends SnapshotSummary = SnapshotSummary>() {
  const ws = useWorkspace();
  return useQuery({
    queryKey: ["snapshots", ws.id],
    queryFn: () => apiFetch<{ snapshots: T[] }>(ws.path("/snapshots")),
    select: selectSortedSnapshots<T>,
  });
}

export interface ProviderStatus {
  provider_id: string;
  api_profile: string;
  endpoint_profile_id: string;
  configured: boolean;
}

export interface ModelStatus {
  model_id: string;
  provider_id: string;
  roles: string[];
  enabled: boolean;
  verification_date: string;
}

export interface ProvidersResponse {
  workspace_id: string;
  providers: ProviderStatus[];
  models: ModelStatus[];
}

export function useProviders() {
  const ws = useWorkspace();
  return useQuery({
    queryKey: ["providers", ws.id],
    queryFn: () => apiFetch<ProvidersResponse>(ws.path("/providers")),
  });
}

export function relativeTime(iso?: string | null): string {
  if (!iso) return "—";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "—";
  const minutes = Math.round((Date.now() - then) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days} d ago`;
  return new Date(then).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes)) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(bytes < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

export function humanize(id: string): string {
  return id.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
