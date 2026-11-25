import type { ClarifyRunRequest, CreateRunRequest } from "@/lib/api/types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api/v1";

/** Error thrown by apiFetch. `status` is 0 when the API could not be reached at all. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string | null;
  readonly remediation: string | null;
  readonly fieldErrors: Record<string, string>;
  readonly requestId: string | null;

  constructor(
    message: string,
    opts: {
      status: number;
      code?: string | null;
      remediation?: string | null;
      fieldErrors?: Record<string, string>;
      requestId?: string | null;
    }
  ) {
    super(message);
    this.name = "ApiError";
    this.status = opts.status;
    this.code = opts.code ?? null;
    this.remediation = opts.remediation ?? null;
    this.fieldErrors = opts.fieldErrors ?? {};
    this.requestId = opts.requestId ?? null;
  }

  get isNetwork(): boolean {
    return this.status === 0;
  }
}

function str(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function toApiError(body: unknown, status: number, statusText: string): ApiError {
  const b = body && typeof body === "object" ? (body as Record<string, unknown>) : {};
  let message = str(b.message) ?? str(b.detail);
  if (!message && Array.isArray(b.detail)) {
    message = b.detail
      .map((d) => (d && typeof d === "object" ? str((d as Record<string, unknown>).msg) : null))
      .filter(Boolean)
      .join("; ");
  }
  const fieldErrors: Record<string, string> = {};
  if (b.field_errors && typeof b.field_errors === "object") {
    for (const [k, v] of Object.entries(b.field_errors as Record<string, unknown>)) {
      if (typeof v === "string") fieldErrors[k.replace(/^body\./, "").replace(/^config\./, "")] = v;
    }
  }
  return new ApiError(message || statusText || `Request failed (HTTP ${status})`, {
    status,
    code: str(b.code),
    remediation: str(b.remediation),
    fieldErrors,
    requestId: str(b.request_id),
  });
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers || {}),
      },
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError("Can't reach the Ledgerline API. Check that it is running, then try again.", { status: 0 });
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw toApiError(body, res.status, res.statusText);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** A short, user-facing message for any thrown value. */
export function errorMessage(err: unknown, fallback = "Something went wrong."): string {
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

/** The remediation hint the API attached to an error, if any. */
export function errorHint(err: unknown): string | null {
  return err instanceof ApiError ? err.remediation : null;
}

/** React Query retry policy: retry network and server errors only, never 4xx answers. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError) return (error.isNetwork || error.status >= 500) && failureCount < 2;
  return failureCount < 2;
}

export interface CreateRunResponse {
  run_id: string;
  status: string;
  status_url: string;
  events_url: string;
}

export function createRun(
  workspaceId: string,
  payload: Omit<CreateRunRequest, "background_async" | "execute_inline">,
  idempotencyKey: string
): Promise<CreateRunResponse> {
  return apiFetch<CreateRunResponse>(`/workspaces/${encodeURIComponent(workspaceId)}/runs`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ ...payload, background_async: true } satisfies CreateRunRequest),
  });
}

export function cancelRun(workspaceId: string, runId: string): Promise<{ run_id: string; status: string }> {
  return apiFetch(`/workspaces/${encodeURIComponent(workspaceId)}/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
  });
}

export function clarifyRun(
  workspaceId: string,
  runId: string,
  revision: number,
  answer: string
): Promise<{ run_id: string; status: string; revision: number }> {
  return apiFetch(`/workspaces/${encodeURIComponent(workspaceId)}/runs/${encodeURIComponent(runId)}/clarify`, {
    method: "POST",
    body: JSON.stringify({ revision, answer } satisfies ClarifyRunRequest),
  });
}

export function deleteRun(workspaceId: string, runId: string): Promise<{ deleted: boolean }> {
  return apiFetch(`/workspaces/${encodeURIComponent(workspaceId)}/runs/${encodeURIComponent(runId)}`, { method: "DELETE" });
}

/** crypto.randomUUID needs a secure context; fall back for plain-HTTP LAN access. */
export function newId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `id-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}
