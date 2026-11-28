import type { AnswerChart } from "@/components/AnswerCharts";
import type { Criterion } from "@/components/AnswerVerification";
import type { RunOut, RunPageOut } from "@/lib/api/types";
import type { SseEnvelope } from "@/lib/sse";
import type { RunMode } from "@/lib/types";
import { humanize, type RunLimits } from "@/lib/workspace";

export interface Session {
  id: string;
  question: string;
  mode: RunMode;
  runId: string | null;
  status: string;
  answer: string;
  charts: AnswerChart[];
  outcome: string | null;
  criteria: Criterion[];
  limitations: string[];
  /** Suggested next questions, sent with the finished answer. */
  followUps: string[];
  conversationId: string | null;
  /** The answer text came from the saved run; replayed answer.delta events must not be appended again. */
  answerFromServer: boolean;
  events: SseEnvelope[];
  error: string | null;
  createdAt: string | null;
  snapshotId: string | null;
  streaming: boolean;
  connectionLost: boolean;
  traceLoaded: boolean;
  lastSeq: number;
  clarificationPrompt: string | null;
  clarificationRevision: number;
  clarificationDraft: string;
}

export type StoredRun = RunOut;
export type RunPage = RunPageOut;
export type HistoryFilter = "all" | "done" | "running" | "failed";

export const TERMINAL = new Set(["completed", "failed", "cancelled"]);
export const STREAM_FLUSH_MS = 80;
export const FINISHED_MESSAGES: Record<string, string> = {
  completed: "Answer ready.",
  failed: "The run failed.",
  cancelled: "The run was cancelled.",
};
export const isTerminal = (status: string) => TERMINAL.has(status);
// Browsers allow about six HTTP/1.1 connections per host; the rest stay free for API calls.
export const MAX_LIVE_STREAMS = 3;

export const OPERATION_LABELS: Record<string, string> = {
  aggregate: "Summary",
  detail: "Rows",
  compare_periods: "Period comparison",
  contribution_breakdown: "Breakdown",
  distribution: "Distribution",
  reconciliation_lookup: "Reconciliation",
  trend: "Trend",
  percentiles: "Percentiles",
  outliers: "Outliers",
};

export const BUDGET_RESOURCES: Record<string, string> = {
  model_calls: "Model calls",
  tokens: "Tokens",
};

export const ROUTE_LABELS: Record<string, string> = {
  direct_query: "Direct query (no planning step)",
  standard: "Planned",
  deep: "Deep investigation",
};

export function budgetCopy(limits: RunLimits | undefined): string {
  if (!limits) return "";
  const minutes = Math.round(limits.active_seconds / 60);
  return `Up to ${limits.total_model_attempts} model calls · ${minutes} min`;
}

export function fromStored(run: StoredRun): Session {
  const answer = run.answer?.summary_markdown ?? "";
  return {
    id: run.run_id,
    question: run.question || "Untitled question",
    mode: run.mode ?? "quick",
    runId: run.run_id,
    status: run.status,
    answer,
    charts: run.answer?.charts ?? [],
    outcome: run.answer?.outcome ?? null,
    criteria: run.answer?.criteria ?? [],
    limitations: run.answer?.limitations ?? [],
    followUps: run.answer?.follow_ups ?? [],
    conversationId: run.conversation_id ?? null,
    answerFromServer: Boolean(answer),
    events: [],
    error: run.error ?? null,
    createdAt: run.created_at ?? null,
    snapshotId: run.snapshot_id ?? null,
    streaming: false,
    connectionLost: false,
    traceLoaded: false,
    lastSeq: 0,
    clarificationPrompt: run.clarification_prompt ?? null,
    clarificationRevision: run.clarification_revision ?? 0,
    clarificationDraft: "",
  };
}

export function applyEvents(s: Session, evts: SseEnvelope[]): Partial<Session> {
  let state = s;
  for (const evt of evts) state = { ...state, ...applyEvent(state, evt) };
  const kept = evts.filter((e) => e.type !== "answer.delta");
  return {
    ...state,
    events: kept.length ? [...s.events, ...kept] : s.events,
    lastSeq: Math.max(s.lastSeq, ...evts.map((e) => e.sequence)),
  };
}

export function applyEvent(s: Session, evt: SseEnvelope): Partial<Session> {
  const next: Partial<Session> = {};
  if (evt.type === "answer.reset") {
    next.answer = "";
    next.answerFromServer = false;
  }
  if (evt.type === "answer.delta" && !s.answerFromServer) next.answer = s.answer + String(evt.payload.text ?? "");
  if (evt.type === "run.status" || evt.type === "run.finished") {
    next.status = String(evt.payload.status ?? (evt.type === "run.finished" ? "completed" : s.status));
    if (evt.payload.status === "queued" || evt.payload.status === "running") {
      next.clarificationPrompt = null;
      next.clarificationDraft = "";
    }
    if (evt.payload.revision !== undefined) {
      next.clarificationRevision = Number(evt.payload.revision);
    }
  }
  if (evt.type === "clarification.required") {
    next.status = "awaiting_input";
    next.clarificationPrompt = String(evt.payload.prompt ?? "Please clarify your question.");
    next.clarificationRevision = Number(evt.payload.revision ?? s.clarificationRevision);
  }
  if (evt.type === "run.finished") {
    next.streaming = false;
    next.connectionLost = false;
    if (typeof evt.payload.outcome === "string") next.outcome = evt.payload.outcome;
    if (Array.isArray(evt.payload.criteria)) next.criteria = evt.payload.criteria as Criterion[];
    if (Array.isArray(evt.payload.limitations)) next.limitations = evt.payload.limitations.map(String);
    const failedTasks = Number(evt.payload.failed_tasks ?? 0);
    if (evt.payload.error) next.error = String(evt.payload.error);
    else if (failedTasks > 0) {
      next.error = `${failedTasks} step${failedTasks === 1 ? "" : "s"} failed, so the answer may be partial.`;
    }
  }
  return next;
}

/** Turn raw backend/provider errors into plain words; the raw text stays available under "Details". */
export function friendlyError(raw: string): { title: string; hint: string | null } {
  const r = raw.toLowerCase();
  if (r.includes("may be partial")) return { title: raw, hint: null };
  if (r.includes("setup_required") || r.includes("api key") || r.includes("not configured") || r.includes("no eligible"))
    return { title: "No AI provider is ready", hint: "Add an API key in Settings, then ask again." };
  if (r.includes("budget") || r.includes("ceiling"))
    return { title: "The run hit its budget limit", hint: "Try Quick mode or a narrower question, or raise the cost ceiling in Settings." };
  if (r.includes("timeout") || r.includes("timed out"))
    return { title: "The model took too long to respond", hint: "Try again, or pick a faster routing preset in Settings." };
  if (r.includes("not approved")) return { title: "This dataset isn't approved yet", hint: "Approve its definitions on the Data page." };
  if (r.includes("reach") && r.includes("api")) return { title: raw, hint: null };
  return { title: "The run didn't finish", hint: "Try again. The technical reason is under Details." };
}

export function statusTone(status: string): "ok" | "err" | "neutral" | "warn" {
  if (status === "completed") return "ok";
  if (status === "failed") return "err";
  if (status === "cancelled") return "neutral";
  return "warn";
}

export function statusLabel(status: string): string {
  if (status === "completed") return "Done";
  if (status === "queued") return "Queued";
  if (status === "running") return "Working";
  if (status === "awaiting_input") return "Needs your input";
  return humanize(status);
}

const SUMMARY_FIELDS = ["status", "task_id", "tool", "provider_id", "model_id", "revision", "row_count", "truncated", "code"];

export function eventSummary(payload: Record<string, unknown>): string {
  return SUMMARY_FIELDS.filter((k) => ["string", "number", "boolean"].includes(typeof payload[k]))
    .map((k) => `${k}=${String(payload[k])}`)
    .join(" ");
}

export function taskName(taskId: string): string {
  return humanize(taskId.replace(/^t\d+_/, ""));
}
