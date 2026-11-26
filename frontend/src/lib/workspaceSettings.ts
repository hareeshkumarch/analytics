"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api/client";
import { workspacePath } from "@/lib/workspace";
import type { UpdateWorkspaceSettingsRequest, WorkspaceSettingsOut } from "@/lib/api/types";

export function timeZoneOptions(current: string | undefined): string[] {
  const zones = typeof Intl.supportedValuesOf === "function" ? Intl.supportedValuesOf("timeZone") : [];
  return Array.from(new Set(["UTC", ...(current ? [current] : []), ...zones]));
}

export function useWorkspaceSettings(workspaceId: string) {
  const qc = useQueryClient();
  const key = ["workspace-settings", workspaceId];
  const query = useQuery({
    queryKey: key,
    queryFn: () => apiFetch<WorkspaceSettingsOut>(workspacePath(workspaceId, "/settings")),
  });
  const mutation = useMutation({
    mutationFn: (body: UpdateWorkspaceSettingsRequest) =>
      apiFetch<WorkspaceSettingsOut>(workspacePath(workspaceId, "/settings"), { method: "PUT", body: JSON.stringify(body) }),
    onSuccess: (next) => qc.setQueryData(key, next),
  });
  return { ...query, save: mutation.mutate, saving: mutation.isPending };
}
