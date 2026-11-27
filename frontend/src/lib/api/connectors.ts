import { apiFetch } from "@/lib/api/client";
import type { ConnectorExtractRequest, ConnectorProbeRequest } from "@/lib/api/types";
import type { SemanticVersion } from "@/lib/api/semantics";
import type { Family, SourcePayload } from "@/lib/connectors";
import { SnapshotSummary, workspacePath } from "@/lib/workspace";

interface ProbeResult {
  ok: boolean;
  family: Family;
  message: string;
  remediation?: string | null;
}

export interface DiscoveredTable {
  name: string;
  estimated_rows: number | null;
  columns: { name: string; arrow_type: string; is_leading_zero_id: boolean }[];
}

export interface ExtractResult {
  snapshot: SnapshotSummary;
  semantics: SemanticVersion;
}

export type ImportLineage = Pick<ConnectorExtractRequest, "source_id" | "form" | "display_name">;

type ExtractRequest = ConnectorExtractRequest & SourcePayload & { selected_tables: string[] };

function post<T>(
  workspaceId: string,
  step: "probe" | "discover" | "extract",
  body: ConnectorProbeRequest | ConnectorExtractRequest,
  signal: AbortSignal
): Promise<T> {
  return apiFetch<T>(workspacePath(workspaceId, `/connectors/${step}`), { method: "POST", body: JSON.stringify(body), signal });
}

export function probeSource(workspaceId: string, payload: SourcePayload, signal: AbortSignal): Promise<ProbeResult> {
  return post<ProbeResult>(workspaceId, "probe", payload, signal);
}

export async function discoverSource(workspaceId: string, payload: SourcePayload, signal: AbortSignal): Promise<DiscoveredTable[]> {
  const res = await post<{ tables: DiscoveredTable[] }>(workspaceId, "discover", payload, signal);
  return res.tables ?? [];
}

export function extractSource(workspaceId: string, request: ExtractRequest, signal: AbortSignal): Promise<ExtractResult> {
  return post<ExtractResult>(workspaceId, "extract", request, signal);
}

export function uploadFile(workspaceId: string, data: Blob): Promise<{ upload_id: string; byte_size: number }> {
  return apiFetch(workspacePath(workspaceId, "/uploads"), {
    method: "POST",
    headers: { "Content-Type": "application/octet-stream" },
    body: data,
  });
}
