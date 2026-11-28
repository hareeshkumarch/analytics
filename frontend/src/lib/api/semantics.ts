"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, apiFetch } from "@/lib/api/client";
import { useWorkspace } from "@/lib/workspace";
import type { ApproveSemanticsRequest } from "@/lib/api/types";

interface MetricDefinition {
  metric_id: string;
  display_name: string;
  expression: string;
  grain: string;
  unit: string;
  currency?: string | null;
}

export interface SemanticVersion {
  semantic_version_id: string;
  snapshot_id: string;
  version: number;
  approved: boolean;
  metrics: MetricDefinition[];
}

export function semanticsKey(workspaceId: string, snapshotId: string) {
  return ["semantics", workspaceId, snapshotId];
}

export function isVersionConflict(err: unknown): boolean {
  return err instanceof ApiError && err.status === 409;
}

export function useSemantics(snapshotId: string) {
  const ws = useWorkspace();
  return useQuery({
    queryKey: semanticsKey(ws.id, snapshotId),
    queryFn: () => apiFetch<SemanticVersion>(ws.path(`/semantics/${encodeURIComponent(snapshotId)}`)),
  });
}

export function useUpdateSemantics(snapshotId: string) {
  const ws = useWorkspace();
  const qc = useQueryClient();
  const key = semanticsKey(ws.id, snapshotId);
  return useMutation({
    mutationFn: (payload: {
      expected_version: number;
      metrics: { metric_id: string; display_name: string; unit?: string | null; currency?: string | null }[];
      remove_metric_ids?: string[];
    }) =>
      apiFetch<SemanticVersion>(ws.path(`/semantics/${encodeURIComponent(snapshotId)}`), {
        method: "PATCH",
        body: JSON.stringify(payload),
      }),
    onSuccess: (next) => qc.setQueryData(key, next),
    onError: (err) => {
      if (isVersionConflict(err)) void qc.invalidateQueries({ queryKey: key });
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["snapshots", ws.id] }),
  });
}

export function useApproveSemantics(snapshotId: string) {
  const ws = useWorkspace();
  const qc = useQueryClient();
  const key = semanticsKey(ws.id, snapshotId);
  return useMutation({
    mutationFn: (expectedVersion: number) =>
      apiFetch<SemanticVersion>(ws.path(`/semantics/${encodeURIComponent(snapshotId)}/approve`), {
        method: "POST",
        body: JSON.stringify({ expected_version: expectedVersion } satisfies ApproveSemanticsRequest),
      }),
    onSuccess: (next) => qc.setQueryData(key, next),
    onError: (err) => {
      if (isVersionConflict(err)) void qc.invalidateQueries({ queryKey: key });
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["snapshots", ws.id] }),
  });
}
