import { API_BASE, apiFetch } from "@/lib/api/client";
import type { CreateExportRequest } from "@/lib/api/types";
import { workspacePath } from "@/lib/workspace";

export const EXPORT_FORMATS = [
  { id: "pdf", label: "Export as PDF", ext: "pdf" },
  { id: "docx", label: "Export as Word document", ext: "docx" },
  { id: "html", label: "Export as web page (HTML)", ext: "html" },
] as const;

export type ExportFormat = (typeof EXPORT_FORMATS)[number];

export async function downloadAnswerExport({
  workspaceId,
  runId,
  format,
  title,
}: {
  workspaceId: string;
  runId: string;
  format: ExportFormat;
  title: string;
}): Promise<void> {
  const created = await apiFetch<{ artifact_id: string }>(workspacePath(workspaceId, "/exports"), {
    method: "POST",
    body: JSON.stringify({ run_id: runId, format: format.id } satisfies CreateExportRequest),
  });
  const res = await fetch(`${API_BASE}${workspacePath(workspaceId, `/exports/${encodeURIComponent(created.artifact_id)}`)}`);
  if (!res.ok) throw new Error(`The export couldn't be downloaded (HTTP ${res.status}).`);
  const url = URL.createObjectURL(await res.blob());
  const link = document.createElement("a");
  link.href = url;
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "answer";
  link.download = `${slug}.${format.ext}`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
