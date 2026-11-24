import { apiFetch } from "@/lib/api/client";
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

interface ExtractRequest extends SourcePayload {
  selected_tables: string[];
  source_id?: string;
  version: number;
}

function post<T>(workspaceId: string, step: "probe" | "discover" | "extract", body: object, signal: AbortSignal): Promise<T> {
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

function identityConfig(config: Record<string, unknown>): [string, unknown][] {
  return Object.keys(config)
    .filter((key) => key !== "raw_base64")
    .sort()
    .map((key) => {
      const value = config[key];
      if (key !== "url" || typeof value !== "string") return [key, value];
      try {
        const u = new URL(value);
        return [key, `${u.host}${u.pathname}`];
      } catch {
        return [key, value];
      }
    });
}

async function sourceKey(payload: SourcePayload): Promise<string | null> {
  if (!globalThis.crypto?.subtle) return null;
  const text = JSON.stringify([payload.family, identityConfig(payload.config)]);
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
  return Array.from(digest.subarray(0, 12), (b) => b.toString(16).padStart(2, "0")).join("");
}

export async function nextSourceVersion(
  payload: SourcePayload,
  snapshots: SnapshotSummary[]
): Promise<Pick<ExtractRequest, "source_id" | "version">> {
  const key = await sourceKey(payload);
  if (!key) return { version: 1 };
  const versions = snapshots.filter((s) => s.source_id.endsWith(`_${key}`)).map((s) => s.version);
  return { source_id: `src_${key}`, version: Math.max(0, ...versions) + 1 };
}
