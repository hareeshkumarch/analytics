"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api/client";
import { workspacePath } from "@/lib/workspace";
import type { RichOption } from "@/components/RichSelect";

export type RoutingPreset = "balanced" | "cost" | "latency" | "quality";

export interface RoutingPolicy {
  workspace_id: string;
  policy_version: number;
  preset: RoutingPreset;
  fallback_enabled: boolean;
  role_routes: Record<string, string[]>;
  hard_cost_ceiling_usd: string | null;
}

export const PRESET_OPTIONS: RichOption[] = [
  { value: "balanced", label: "Balanced", sublabel: "Default role-ordered routes", badge: "Default" },
  { value: "cost", label: "Cost-oriented", sublabel: "Ranks eligible models by $/1M tokens", badge: "Cost" },
  { value: "latency", label: "Latency-oriented", sublabel: "Prefers the fastest measured response", badge: "Fast" },
  { value: "quality", label: "Quality-oriented", sublabel: "Prefers frontier reasoning models", badge: "Quality" },
];

export function useRoutingPolicy(workspaceId: string) {
  const qc = useQueryClient();
  const key = ["routing", workspaceId];

  const query = useQuery({
    queryKey: key,
    queryFn: () => apiFetch<RoutingPolicy>(workspacePath(workspaceId, "/routing")),
  });

  const mutation = useMutation({
    mutationFn: (
      patch: Partial<Pick<RoutingPolicy, "preset" | "fallback_enabled" | "role_routes" | "hard_cost_ceiling_usd">>
    ) => {
      const current = qc.getQueryData<RoutingPolicy>(key) ?? query.data;
      if (!current) throw new Error("Routing policy not loaded yet");
      const body = {
        expected_version: current.policy_version,
        preset: patch.preset ?? current.preset,
        fallback_enabled: patch.fallback_enabled ?? current.fallback_enabled,
        ...(patch.role_routes !== undefined ? { role_routes: patch.role_routes } : {}),
        ...(patch.hard_cost_ceiling_usd !== undefined
          ? { hard_cost_ceiling_usd: patch.hard_cost_ceiling_usd }
          : {}),
      };
      return apiFetch<RoutingPolicy>(workspacePath(workspaceId, "/routing"), {
        method: "PUT",
        body: JSON.stringify(body),
      });
    },
    onSuccess: (next) => qc.setQueryData(key, next),
    onError: () => qc.invalidateQueries({ queryKey: key }),
  });

  return { ...query, update: mutation.mutate, updating: mutation.isPending, updateError: mutation.error };
}
