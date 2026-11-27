"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { API_BASE, apiFetch, cancelRun, clarifyRun, createRun, deleteRun, errorHint, errorMessage, newId } from "@/lib/api/client";
import { subscribeRunEvents, SseEnvelope } from "@/lib/sse";
import { useUiStore } from "@/lib/store";
import { PRESET_OPTIONS, useRoutingPolicy } from "@/lib/routing";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { EvidenceContext, SafeMarkdown } from "@/components/SafeMarkdown";
import { EvidencePanel } from "@/features/ask/EvidencePanel";
import { RichSelect } from "@/components/RichSelect";
import { Badge, Details, Notice, SearchInput, Spinner, btn, btnSm, cx, inputBaseCls, inputCls } from "@/components/ui";
import {
  RunLimits,
  datasetLabel,
  humanize,
  relativeTime,
  useHealth,
  useProviders,
  useSnapshots,
  workspacePath,
} from "@/lib/workspace";
import type { RunMode } from "@/lib/types";
import { setUrlParam } from "@/lib/url";
import { AnswerCharts, type AnswerChart } from "@/components/AnswerCharts";
import { AnswerVerification, type Criterion } from "@/components/AnswerVerification";
import { ExportMenu } from "@/features/ask/ExportMenu";
import { SampleDataButton } from "@/components/SampleDataButton";
import { SAMPLE_QUESTIONS, isSampleDataset } from "@/lib/sample";
import type { CreatePinRequest, RunOut, RunPageOut, TaskRecordListOut } from "@/lib/api/types";
import { progressSteps } from "@/lib/progress";
import { stripEvidenceIds } from "@/lib/text";

interface Session {
  id: string;
  question: string;
  mode: RunMode;
  runId: string | null;
  status: string;
  answer: string;
  charts: AnswerChart[];
  outcome: string | null;
  criteria: Criterion[];
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

type StoredRun = RunOut;
type RunPage = RunPageOut;

type HistoryFilter = "all" | "done" | "running" | "failed";

const TERMINAL = new Set(["completed", "failed", "cancelled"]);
const FINISHED_MESSAGES: Record<string, string> = {
  completed: "Answer ready.",
  failed: "The run failed.",
  cancelled: "The run was cancelled.",
};
const isTerminal = (status: string) => TERMINAL.has(status);
// Browsers allow about six HTTP/1.1 connections per host; the rest stay free for API calls.
const MAX_LIVE_STREAMS = 3;

const OPERATION_LABELS: Record<string, string> = {
  aggregate: "Summary",
  detail: "Rows",
  compare_periods: "Period comparison",
  contribution_breakdown: "Breakdown",
  distribution: "Distribution",
  reconciliation_lookup: "Reconciliation",
};

function budgetCopy(limits: RunLimits | undefined): string {
  if (!limits) return "";
  const minutes = Math.round(limits.active_seconds / 60);
  return `Up to ${limits.total_model_attempts} model calls · ${minutes} min`;
}

function fromStored(run: StoredRun): Session {
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

function applyEvent(s: Session, evt: SseEnvelope): Partial<Session> {
  const next: Partial<Session> = { events: [...s.events, evt], lastSeq: Math.max(s.lastSeq, evt.sequence) };
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
    const failedTasks = Number(evt.payload.failed_tasks ?? 0);
    if (evt.payload.error) next.error = String(evt.payload.error);
    else if (failedTasks > 0) {
      next.error = `${failedTasks} step${failedTasks === 1 ? "" : "s"} failed, so the answer may be partial.`;
    }
  }
  return next;
}

/** Turn raw backend/provider errors into plain words; the raw text stays available under "Details". */
function friendlyError(raw: string): { title: string; hint: string | null } {
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

function statusTone(status: string): "ok" | "err" | "neutral" | "warn" {
  if (status === "completed") return "ok";
  if (status === "failed") return "err";
  if (status === "cancelled") return "neutral";
  return "warn";
}

function statusLabel(status: string): string {
  if (status === "completed") return "Done";
  if (status === "queued") return "Queued";
  if (status === "running") return "Working";
  if (status === "awaiting_input") return "Needs your input";
  return humanize(status);
}

const BUDGET_RESOURCES: Record<string, string> = { model_calls: "Model calls", tokens: "Tokens" };

const ROUTE_LABELS: Record<string, string> = {
  direct_query: "Direct query (no planning step)",
  standard: "Planned",
  deep: "Deep investigation",
};

const SUMMARY_FIELDS = ["status", "task_id", "tool", "provider_id", "model_id", "revision", "row_count", "truncated", "code"];

function eventSummary(payload: Record<string, unknown>): string {
  return SUMMARY_FIELDS.filter((k) => ["string", "number", "boolean"].includes(typeof payload[k]))
    .map((k) => `${k}=${String(payload[k])}`)
    .join(" ");
}

function taskName(taskId: string): string {
  return humanize(taskId.replace(/^t\d+_/, ""));
}

function RunProgress({ events, workspaceId, runId }: { events: SseEnvelope[]; workspaceId: string; runId: string | null }) {
  const changes = events.filter((e) => e.type === "task.status" || e.type === "plan.updated").length;
  const records = useQuery({
    queryKey: ["run-tasks", workspaceId, runId, changes],
    queryFn: () => apiFetch<TaskRecordListOut>(workspacePath(workspaceId, `/runs/${encodeURIComponent(runId!)}/tasks`)),
    enabled: Boolean(runId) && changes > 0,
    placeholderData: (previous) => previous,
  });
  const steps = progressSteps(events, records.data?.tasks);
  if (!steps.length) return null;
  return (
    <ol className="space-y-1.5" aria-label="Progress">
      {steps.map(({ taskId: t, status: st, queries }) => {
        return (
          <li key={t} className="flex items-center gap-2.5 text-[13px]">
            <span
              aria-hidden="true"
              className={cx(
                "h-2 w-2 shrink-0 rounded-full",
                st === "completed" ? "bg-ok-dot" : st === "failed" ? "bg-err-dot" : st === "running" ? "animate-pulse bg-warn-dot" : "bg-border-strong"
              )}
            />
            <span className={st === "pending" ? "text-secondary" : "text-ink"}>{taskName(t)}</span>
            <span className="text-xs text-secondary">
              {st === "running" ? "working" : st === "completed" ? "done" : st === "failed" ? "failed" : st === "superseded" ? "replaced" : st === "cancelled" ? "skipped" : "waiting"}
              {queries ? ` · ${queries} quer${queries === 1 ? "y" : "ies"}` : ""}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

export function AskView() {
  const queryClient = useQueryClient();
  const activeWorkspaceId = useUiStore((s) => s.activeWorkspaceId);
  const draftQuestion = useUiStore((s) => s.draftQuestion);
  const setDraftQuestion = useUiStore((s) => s.setDraftQuestion);
  const runMode = useUiStore((s) => s.runMode);
  const setRunMode = useUiStore((s) => s.setRunMode);

  const routing = useRoutingPolicy(activeWorkspaceId);
  const health = useHealth();
  const providers = useProviders();
  const snapshotsQuery = useSnapshots();
  const runsQuery = useQuery({
    queryKey: ["runs", activeWorkspaceId],
    queryFn: () => apiFetch<RunPage>(workspacePath(activeWorkspaceId, "/runs")),
    // Poll only while a recent run is unfinished; an old "running" record is stale and must not poll forever.
    refetchInterval: (q) => {
      const now = Date.now();
      const ages = (q.state.data?.runs ?? [])
        .filter((r) => !isTerminal(r.status))
        .map((r) => now - (r.created_at ? Date.parse(r.created_at) : now));
      const youngest = ages.filter((a) => !Number.isNaN(a) && a < 60 * 60 * 1000);
      if (!youngest.length) return false;
      return Math.min(...youngest) < 5 * 60 * 1000 ? 8000 : 30000;
    },
  });
  const pinsQuery = useQuery({
    queryKey: ["pins", activeWorkspaceId],
    queryFn: () => apiFetch<{ pins: { run_id: string }[] }>(workspacePath(activeWorkspaceId, "/pins")),
  });

  const snapshots = useMemo(() => snapshotsQuery.data?.snapshots ?? [], [snapshotsQuery.data]);
  const approvedSnapshots = snapshots.filter((s) => s.approved);
  const [chosenSnapshot, setChosenSnapshot] = useState<string | null>(null);
  const snapshot = approvedSnapshots.find((s) => s.snapshot_id === chosenSnapshot) ?? approvedSnapshots[0] ?? null;
  const snapshotById = useMemo(() => new Map(snapshots.map((s) => [s.snapshot_id, s])), [snapshots]);

  const budget = budgetCopy(health.data?.run_limits?.[runMode]);
  const inputLimits = health.data?.input_limits;
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [pendingRunParam, setPendingRunParam] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<{ message: string; hint: string | null } | null>(null);
  const [pinningRunId, setPinningRunId] = useState<string | null>(null);
  const [pinnedRunIds, setPinnedRunIds] = useState<Set<string>>(() => new Set());
  const [pinError, setPinError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [clarificationSubmitting, setClarificationSubmitting] = useState(false);
  const [openEvidence, setOpenEvidence] = useState<string | null>(null);
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyFilter, setHistoryFilter] = useState<HistoryFilter>("all");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [isMac, setIsMac] = useState(false);
  const closeEvidence = useCallback(() => setOpenEvidence(null), []);
  const closers = useRef(new Map<string, () => void>());
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const unmounted = useRef(false);

  // Read deep links once: ?run=<run_id> opens a saved run, ?dataset=<snapshot_id> preselects a dataset.
  useEffect(() => {
    setIsMac(/Mac|iPhone|iPad/i.test(navigator.platform || navigator.userAgent));
    const params = new URLSearchParams(window.location.search);
    setPendingRunParam(params.get("run"));
    const dataset = params.get("dataset");
    if (dataset) setChosenSnapshot(dataset);
  }, []);

  useEffect(() => {
    unmounted.current = false;
    const map = closers.current;
    return () => {
      unmounted.current = true;
      map.forEach((close) => close());
      map.clear();
    };
  }, []);

  const [olderRuns, setOlderRuns] = useState<{ runs: StoredRun[]; next: string | null | undefined }>({ runs: [], next: undefined });
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [olderError, setOlderError] = useState<string | null>(null);
  const [followUpDraft, setFollowUpDraft] = useState("");
  const [liveMessage, setLiveMessage] = useState("");
  const olderCursor = olderRuns.next === undefined ? runsQuery.data?.next_before ?? null : olderRuns.next;

  async function loadOlderRuns() {
    if (!olderCursor || loadingOlder) return;
    setLoadingOlder(true);
    setOlderError(null);
    try {
      const page = await apiFetch<RunPage>(
        workspacePath(activeWorkspaceId, `/runs?before=${encodeURIComponent(olderCursor)}`)
      );
      setOlderRuns((prev) => ({ runs: [...prev.runs, ...page.runs], next: page.next_before }));
    } catch (err) {
      setOlderError(errorMessage(err, "Couldn't load older questions."));
    } finally {
      setLoadingOlder(false);
    }
  }

  // Merge saved runs: add unknown ones, and refresh status/answer of runs that aren't streaming here.
  useEffect(() => {
    const saved = runsQuery.data ? [...runsQuery.data.runs, ...olderRuns.runs] : undefined;
    if (!saved) return;
    setSessions((prev) => {
      const byRun = new Map(saved.map((r) => [r.run_id, r]));
      const known = new Set(prev.map((s) => s.runId).filter((id): id is string => Boolean(id)));
      const updated = prev.map((s) => {
        if (!s.runId || s.streaming) return s;
        const r = byRun.get(s.runId);
        if (!r) return s;
        const serverAnswer = r.answer?.summary_markdown ?? "";
        const serverCharts = r.answer?.charts ?? s.charts;
        const sameCharts = serverCharts.map((c) => c.chart_id).join() === s.charts.map((c) => c.chart_id).join();
        const nextError = r.error ?? s.error;
        const outcome = r.answer?.outcome ?? s.outcome;
        const prompt = r.clarification_prompt ?? null;
        const revision = r.clarification_revision ?? s.clarificationRevision;
        if (
          r.status === s.status &&
          (!serverAnswer || serverAnswer === s.answer) &&
          sameCharts &&
          (r.conversation_id ?? null) === s.conversationId &&
          nextError === s.error &&
          outcome === s.outcome &&
          prompt === s.clarificationPrompt &&
          revision === s.clarificationRevision
        ) return s;
        return {
          ...s,
          status: r.status,
          answer: serverAnswer || s.answer,
          charts: serverCharts,
          outcome,
          criteria: r.answer?.criteria ?? s.criteria,
          conversationId: r.conversation_id ?? s.conversationId,
          answerFromServer: Boolean(serverAnswer) || s.answerFromServer,
          error: nextError,
          connectionLost: s.connectionLost && !isTerminal(r.status),
          clarificationPrompt: prompt,
          clarificationRevision: revision,
        };
      });
      const added = saved.filter((r) => !known.has(r.run_id)).map(fromStored);
      return added.length || updated.some((s, i) => s !== prev[i]) ? [...updated, ...added] : prev;
    });
  }, [runsQuery.data, olderRuns.runs]);

  useEffect(() => {
    setPinnedRunIds(new Set((pinsQuery.data?.pins ?? []).map((pin) => pin.run_id)));
  }, [pinsQuery.data]);

  // Resolve ?run= once its session exists.
  useEffect(() => {
    if (!pendingRunParam) return;
    const match = sessions.find((s) => s.runId === pendingRunParam);
    if (match) {
      setActiveId(match.id);
      setPendingRunParam(null);
    } else if (runsQuery.isSuccess && !runsQuery.data.runs.some((r) => r.run_id === pendingRunParam)) {
      setPendingRunParam(null);
    }
  }, [pendingRunParam, sessions, runsQuery.isSuccess, runsQuery.data]);

  const patch = useCallback(
    (id: string, fn: (s: Session) => Partial<Session>) =>
      setSessions((prev) => prev.map((s) => (s.id === id ? { ...s, ...fn(s) } : s))),
    []
  );

  const closeStream = useCallback((id: string) => {
    closers.current.get(id)?.();
    closers.current.delete(id);
  }, []);

  /** Subscribe a session to its run's events after `afterSeq`. One code path for live, resumed and replayed runs. */
  const attach = useCallback(
    (sessionId: string, runId: string, afterSeq: number) => {
      closeStream(sessionId);
      // Absolute URL: a path starting with "/" would resolve against the origin and drop the /api/v1 prefix.
      const url = `${API_BASE}${workspacePath(activeWorkspaceId, `/runs/${encodeURIComponent(runId)}/events`)}`;
      patch(sessionId, (s) => ({
        streaming: true,
        connectionLost: false,
        traceLoaded: true,
        answerFromServer: afterSeq === 0 ? s.answerFromServer || Boolean(s.answer) : s.answerFromServer,
      }));
      const close = subscribeRunEvents(url, afterSeq, {
        onEvent: (evt) => {
          patch(sessionId, (s) => applyEvent(s, evt));
          if (evt.type === "run.finished") {
            closeStream(sessionId);
            void queryClient.invalidateQueries({ queryKey: ["runs", activeWorkspaceId] });
            void queryClient.invalidateQueries({ queryKey: ["usage", activeWorkspaceId] });
          }
        },
        onResetDraft: () => patch(sessionId, () => ({ answer: "" })),
        onDisconnected: () => {
          closers.current.delete(sessionId);
          patch(sessionId, (s) => (isTerminal(s.status) ? { streaming: false } : { streaming: false, connectionLost: true }));
        },
      });
      closers.current.set(sessionId, close);
      for (const [otherId] of closers.current) {
        if (closers.current.size <= MAX_LIVE_STREAMS) break;
        if (otherId === sessionId) continue;
        closeStream(otherId);
        patch(otherId, (s) =>
          isTerminal(s.status)
            ? { streaming: false }
            : { streaming: false, traceLoaded: false, events: [], lastSeq: 0, answer: "", answerFromServer: false }
        );
      }
    },
    [activeWorkspaceId, closeStream, patch, queryClient]
  );

  const active = sessions.find((s) => s.id === activeId) ?? null;
  const watched = useRef<{ id: string | null; status: string | null }>({ id: null, status: null });
  const activeKey = active?.id ?? null;
  const activeStatus = active?.status ?? null;

  useEffect(() => {
    const previous = watched.current;
    watched.current = { id: activeKey, status: activeStatus };
    if (previous.id !== activeKey) {
      setLiveMessage("");
      return;
    }
    const finished = activeStatus !== null && isTerminal(activeStatus);
    const wasRunning = previous.status !== null && !isTerminal(previous.status);
    if (finished && wasRunning) setLiveMessage(FINISHED_MESSAGES[activeStatus] ?? "");
  }, [activeKey, activeStatus]);

  // Opening a saved run loads its trace once; a run that is still working resumes live updates.
  useEffect(() => {
    if (!active?.runId || active.streaming || closers.current.has(active.id)) return;
    if (active.traceLoaded) return;
    attach(active.id, active.runId, 0);
  }, [active?.id, active?.runId, active?.streaming, active?.traceLoaded, attach]);

  const isBusy = active ? !isTerminal(active.status) : false;
  const isAwaitingInput = active?.status === "awaiting_input";
  const isWorking = isBusy && !isAwaitingInput;
  const toolEvents = active ? active.events.filter((e) => e.type === "tool.result") : [];
  const nonSchemaTools = toolEvents.filter((e) => e.payload.tool !== "inspect_schema");
  const finishedEvent = active ? [...active.events].reverse().find((e) => e.type === "run.finished") : undefined;
  const timeLimited = Boolean(finishedEvent?.payload.time_budget_exceeded);
  const budgetWarnings = active ? active.events.filter((e) => e.type === "budget.warning") : [];
  const planEvent = active ? [...active.events].reverse().find((e) => e.type === "plan.updated") : undefined;
  const route = typeof planEvent?.payload.route === "string" ? planEvent.payload.route : null;
  const models = active
    ? Array.from(
        new Set(
          active.events
            .filter((e) => e.type === "route.selected")
            .map((e) => `${String(e.payload.provider ?? "")} · ${String(e.payload.model ?? "")}`)
        )
      )
    : [];

  // Readiness: a provider key and an approved dataset are both required before asking.
  const anyProvider = providers.data ? providers.data.providers.some((p) => p.configured) : null;
  const hasApproved = snapshotsQuery.data ? approvedSnapshots.length > 0 : null;
  const blockers: { key: string; title: string; body: string; href: string; cta: string; sample?: boolean }[] = [];
  if (anyProvider === false) {
    blockers.push({
      key: "provider",
      title: "Add an AI provider key",
      body: "Answers are written by a model provider such as OpenAI, Anthropic or Gemini.",
      href: "/settings",
      cta: "Open Settings",
    });
  }
  if (hasApproved === false) {
    blockers.push({
      key: "data",
      title: snapshots.length ? "Approve a dataset" : "Connect your data",
      body: snapshots.length
        ? "Your imported data needs its definitions approved before questions can use it."
        : "Import a file, a database or an app on the Data page, or start with sample shop data.",
      href: "/data",
      cta: snapshots.length ? "Review datasets" : "Add data",
      sample: !snapshots.length,
    });
  }
  const canAsk = blockers.length === 0 && Boolean(snapshot);

  function selectSession(s: Session) {
    setOpenEvidence(null);
    setPinError(null);
    setConfirmDeleteId(null);
    setActiveId(s.id);
    setHistoryOpen(false);
    setUrlParam("run", s.runId);
  }

  async function startRun(
    question: string,
    target: { snapshotId: string; semanticVersionId?: string; followUpOf?: string },
    restore: (question: string) => void
  ) {
    const id = newId();
    const previousActiveId = activeId;
    const mode = runMode;
    setSubmitError(null);
    setConfirmDeleteId(null);
    setSessions((prev) => [
      {
        id,
        question,
        mode,
        runId: null,
        status: "queued",
        answer: "",
        charts: [],
        outcome: null,
        criteria: [],
        conversationId: target.followUpOf
          ? (sessions.find((s) => s.runId === target.followUpOf)?.conversationId ?? null)
          : null,
        answerFromServer: false,
        events: [],
        error: null,
        createdAt: new Date().toISOString(),
        snapshotId: target.snapshotId,
        streaming: false,
        connectionLost: false,
        traceLoaded: true,
        lastSeq: 0,
        clarificationPrompt: null,
        clarificationRevision: 0,
        clarificationDraft: "",
      },
      ...prev,
    ]);
    setActiveId(id);
    setOpenEvidence(null);
    setSubmitting(true);

    try {
      const res = await createRun(
        activeWorkspaceId,
        {
          question,
          mode,
          snapshot_id: target.snapshotId,
          semantic_version_id: target.semanticVersionId,
          follow_up_of: target.followUpOf,
        },
        newId()
      );
      if (unmounted.current) return;
      patch(id, () => ({ runId: res.run_id, status: res.status || "queued" }));
      attach(id, res.run_id, 0);
      setUrlParam("run", res.run_id);
    } catch (err) {
      // Put the question back and stay on the active run for failed follow-ups so nothing typed is lost.
      setSessions((prev) => prev.filter((s) => s.id !== id));
      setActiveId(target.followUpOf ? previousActiveId : null);
      restore(question);
      setSubmitError({ message: errorMessage(err, "Couldn't start the run."), hint: errorHint(err) });
    } finally {
      setSubmitting(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const question = draftQuestion.trim();
    if (!question || submitting || !canAsk || !snapshot) return;
    setDraftQuestion("");
    await startRun(
      question,
      { snapshotId: snapshot.snapshot_id, semanticVersionId: snapshot.semantic_version_id ?? undefined },
      setDraftQuestion
    );
  }

  async function handleFollowUp(e: React.FormEvent) {
    e.preventDefault();
    const question = followUpDraft.trim();
    if (!question || submitting || !active?.runId || !active.snapshotId) return;
    setFollowUpDraft("");
    await startRun(question, { snapshotId: active.snapshotId, followUpOf: active.runId }, setFollowUpDraft);
  }

  async function handleCancel() {
    if (!active?.runId) return;
    const id = active.id;
    try {
      const r = await cancelRun(activeWorkspaceId, active.runId);
      patch(id, () => ({ status: r.status }));
      void queryClient.invalidateQueries({ queryKey: ["runs", activeWorkspaceId] });
      if (isTerminal(r.status)) closeStream(id);
    } catch (err) {
      patch(id, () => ({ error: errorMessage(err, "Couldn't cancel the run.") }));
    }
  }

  async function handleDelete() {
    if (!active?.runId) return;
    if (confirmDeleteId !== active.id) {
      setConfirmDeleteId(active.id);
      return;
    }
    const { id, runId } = active;
    setConfirmDeleteId(null);
    try {
      await deleteRun(activeWorkspaceId, runId);
      closeStream(id);
      setOlderRuns((prev) => ({ ...prev, runs: prev.runs.filter((r) => r.run_id !== runId) }));
      queryClient.setQueryData<RunPage>(["runs", activeWorkspaceId], (page) =>
        page ? { ...page, runs: page.runs.filter((r) => r.run_id !== runId) } : page
      );
      setSessions((prev) => prev.filter((s) => s.id !== id));
      setActiveId(null);
      setUrlParam("run", null);
      void queryClient.invalidateQueries({ queryKey: ["pins", activeWorkspaceId] });
    } catch (err) {
      patch(id, () => ({ error: errorMessage(err, "Couldn't delete this question.") }));
    }
  }

  async function handleClarificationSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!active?.runId || !active.clarificationPrompt || clarificationSubmitting) return;
    const answer = active.clarificationDraft.trim();
    if (!answer) return;
    const id = active.id;
    setClarificationSubmitting(true);
    patch(id, () => ({ error: null }));
    try {
      const result = await clarifyRun(
        activeWorkspaceId,
        active.runId,
        active.clarificationRevision,
        answer
      );
      patch(id, () => ({
        status: result.status,
        clarificationRevision: result.revision,
        clarificationPrompt: null,
        clarificationDraft: "",
      }));
      void queryClient.invalidateQueries({ queryKey: ["runs", activeWorkspaceId] });
      if (!closers.current.has(id)) attach(id, active.runId, active.lastSeq);
    } catch (err) {
      patch(id, () => ({ error: errorMessage(err, "Couldn't send your clarification.") }));
      void queryClient.invalidateQueries({ queryKey: ["runs", activeWorkspaceId] });
    } finally {
      setClarificationSubmitting(false);
    }
  }

  async function handlePin() {
    if (!active?.runId || active.status !== "completed" || pinnedRunIds.has(active.runId)) return;
    const runId = active.runId;
    setPinningRunId(runId);
    setPinError(null);
    try {
      await apiFetch(workspacePath(activeWorkspaceId, "/pins"), {
        method: "POST",
        body: JSON.stringify({
          title: active.question.trim().slice(0, inputLimits?.max_pin_title_chars) || "Pinned answer",
          question_text: active.question,
          run_id: runId,
        } satisfies CreatePinRequest),
      });
      setPinnedRunIds((previous) => new Set(previous).add(runId));
      await queryClient.invalidateQueries({ queryKey: ["pins", activeWorkspaceId] });
    } catch (err) {
      setPinError(errorMessage(err, "Couldn't pin this answer."));
    } finally {
      setPinningRunId(null);
    }
  }

  async function copyAnswer() {
    if (!active?.answer) return;
    try {
      await navigator.clipboard.writeText(stripEvidenceIds(active.answer));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
      setPinError("Couldn't copy automatically; select and copy the answer text below.");
    }
  }

  function startNew() {
    setActiveId(null);
    setOpenEvidence(null);
    setConfirmDeleteId(null);
    setPinError(null);
    setUrlParam("run", null);
    textareaRef.current?.focus();
  }

  const sortedSessions = useMemo(
    () => [...sessions].sort((a, b) => (b.createdAt ?? "").localeCompare(a.createdAt ?? "")),
    [sessions]
  );
  const hq = historyQuery.trim().toLowerCase();
  const historyCounts = {
    all: sortedSessions.length,
    done: sortedSessions.filter((s) => s.status === "completed").length,
    running: sortedSessions.filter((s) => !isTerminal(s.status)).length,
    failed: sortedSessions.filter((s) => s.status === "failed" || s.status === "cancelled").length,
  };
  const visibleHistory = sortedSessions.filter((s) => {
    if (hq && !s.question.toLowerCase().includes(hq)) return false;
    if (historyFilter === "done") return s.status === "completed";
    if (historyFilter === "running") return !isTerminal(s.status);
    if (historyFilter === "failed") return s.status === "failed" || s.status === "cancelled";
    return true;
  });

  const threads: Session[][] = [];
  const threadByKey = new Map<string, Session[]>();
  for (const s of visibleHistory) {
    const key = s.conversationId ?? s.id;
    let thread = threadByKey.get(key);
    if (!thread) {
      thread = [];
      threadByKey.set(key, thread);
      threads.push(thread);
    }
    thread.unshift(s);
  }

  const presetLabel = PRESET_OPTIONS.find((o) => o.value === routing.data?.preset)?.label;
  const activeDataset = active?.snapshotId ? snapshotById.get(active.snapshotId) : undefined;
  const errorInfo = active?.error ? friendlyError(active.error) : null;

  const composer = (
    <div className="space-y-1.5">
      <form
        onSubmit={handleSubmit}
        className={cx(
          "flex flex-col gap-2.5 border p-3 transition-shadow sm:px-4 sm:py-3",
          canAsk
            ? "border-border-strong bg-surface shadow-composer focus-within:border-ink focus-within:ring-[3px] focus-within:ring-plum-soft"
            : "border-border bg-panel/60"
        )}
      >
        <textarea
          ref={textareaRef}
          aria-label="Your question"
          rows={2}
          maxLength={inputLimits?.max_question_chars}
          value={draftQuestion}
          onChange={(e) => setDraftQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              e.currentTarget.form?.requestSubmit();
            }
          }}
          placeholder={canAsk ? "Ask about your approved data…" : "Finish setup to start asking questions"}
          className={cx(
            "min-h-[48px] w-full resize-none bg-transparent px-1 py-0.5 text-[15px] leading-relaxed placeholder:text-secondary focus:outline-none",
            canAsk ? "text-ink" : "text-secondary"
          )}
        />

        <div className="flex flex-wrap items-center justify-between gap-2.5">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <div role="group" aria-label="Run mode" className="inline-flex border border-border bg-sidebar p-[2px]">
              {(["quick", "deep"] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  aria-pressed={runMode === mode}
                  onClick={() => setRunMode(mode)}
                  title={mode === "quick" ? "One focused analysis step" : "Plans several steps and reviews the evidence"}
                  className={cx(
                    "min-h-[30px] px-3.5 text-[13px] capitalize transition-colors",
                    runMode === mode ? "bg-surface font-medium text-ink shadow-card" : "text-body hover:text-ink"
                  )}
                >
                  {mode}
                </button>
              ))}
            </div>

            <div className="min-w-[140px] flex-1 basis-[140px] sm:flex-none sm:basis-auto">
              <RichSelect
                dense
                triggerClassName="w-full sm:w-auto sm:min-w-[168px]"
                ariaLabel="Dataset"
                value={snapshot?.snapshot_id ?? ""}
                placeholder={snapshotsQuery.isLoading ? "Loading datasets…" : "No approved dataset"}
                options={snapshots.map((s) => ({
                  value: s.snapshot_id,
                  label: `${datasetLabel(s)} · v${s.version}`,
                  sublabel: s.approved ? `${s.total_rows.toLocaleString()} rows` : "Needs approval on the Data page",
                  badge: s.approved ? undefined : "Draft",
                  disabled: !s.approved,
                }))}
                disabled={approvedSnapshots.length === 0}
                onChange={setChosenSnapshot}
              />
            </div>

            <Link
              href="/settings"
              className="inline-flex h-9 shrink-0 items-center whitespace-nowrap px-1.5 text-[12.5px] text-secondary underline decoration-border underline-offset-4 hover:text-ink"
              title="Routing applies to every run in this workspace. Change it in Settings."
            >
              Routing: {presetLabel ?? (routing.isLoading ? "…" : "default")}
            </Link>
          </div>

          <div className="ml-auto flex shrink-0 items-center gap-2.5">
            <span className="hidden text-xs text-secondary sm:inline">{isMac ? "⌘ + Enter" : "Ctrl + Enter"}</span>
            <button
              type="submit"
              aria-label={submitting ? "Starting" : runMode === "quick" ? "Ask (quick)" : "Ask (deep)"}
              disabled={!draftQuestion.trim() || submitting || !canAsk}
              className="inline-flex h-9 w-9 shrink-0 items-center justify-center bg-plum text-on-accent transition-colors hover:bg-plum-deep disabled:cursor-not-allowed disabled:bg-plum/35"
            >
              {submitting ? (
                <Spinner className="h-4 w-4" />
              ) : (
                <svg className="h-[17px] w-[17px]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
                  <path d="M12 19V5M5 12l7-7 7 7" />
                </svg>
              )}
            </button>
          </div>
        </div>
      </form>
      {budget && <p className="px-0.5 text-[11.5px] text-secondary">{budget}</p>}
      {health.isError && (
        <p className="px-0.5 text-[11.5px] text-secondary">Run limits are unavailable right now; the API health check failed.</p>
      )}
    </div>
  );

  function historyItem(s: Session, followUp = false) {
    const isActive = s.id === activeId;
    return (
      <button
        type="button"
        aria-current={isActive ? "true" : undefined}
        onClick={() => selectSession(s)}
        className={cx("block w-full px-2.5 py-2 text-left transition-colors", isActive ? "bg-bubble text-ink" : "text-body hover:bg-sidebar")}
      >
        {followUp && <span className="sr-only">Follow-up: </span>}
        <span className="line-clamp-2 text-[13px] leading-snug">{s.question}</span>
        <span className="mt-1 flex items-center gap-1.5 text-[11px] text-secondary">
          <span
            aria-hidden="true"
            className={cx(
              "h-1.5 w-1.5 rounded-full",
              s.status === "completed" ? "bg-ok-dot" : s.status === "failed" ? "bg-err-dot" : isTerminal(s.status) ? "bg-secondary" : "animate-pulse bg-warn-dot"
            )}
          />
          {statusLabel(s.status)} · {s.mode} · {relativeTime(s.createdAt)}
        </span>
      </button>
    );
  }

  const showHistoryControls = sortedSessions.length > 0 || Boolean(hq) || historyFilter !== "all";
  const history = (
    <nav aria-label="Question history" className="space-y-2">
      {showHistoryControls && (
        <>
          <SearchInput dense label="Search history" placeholder="Search questions" value={historyQuery} onChange={setHistoryQuery} />
          <div role="group" aria-label="Filter history" className="flex items-center gap-1">
            {(
              [
                { value: "all", label: "All", count: historyCounts.all },
                { value: "done", label: "Done", count: historyCounts.done },
                { value: "running", label: "Running", count: historyCounts.running },
                { value: "failed", label: "Failed", count: historyCounts.failed },
              ] as const
            ).map((o) => {
              const activeFilter = o.value === historyFilter;
              return (
                <button
                  key={o.value}
                  type="button"
                  aria-pressed={activeFilter}
                  onClick={() => setHistoryFilter(o.value)}
                  className={cx(
                    "inline-flex h-7 flex-auto items-center justify-center gap-1 whitespace-nowrap border px-1.5 text-[11.5px] transition-colors",
                    activeFilter
                      ? "border-ink bg-ink font-medium text-canvas"
                      : "border-border bg-surface text-body hover:border-border-strong hover:text-ink"
                  )}
                >
                  <span>{o.label}</span>
                  <span className={cx("tabular-nums text-[10.5px]", activeFilter ? "text-canvas/70" : "text-secondary")}>
                    {o.count}
                  </span>
                </button>
              );
            })}
          </div>
        </>
      )}
      {sortedSessions.length === 0 ? (
        <p className="px-1 py-2 text-xs leading-relaxed text-secondary" role={runsQuery.error ? "alert" : undefined}>
          {runsQuery.error
            ? `Couldn't load history: ${errorMessage(runsQuery.error)}`
            : runsQuery.isLoading
              ? "Loading history…"
              : "Your questions will appear here."}
        </p>
      ) : visibleHistory.length === 0 ? (
        <p className="px-1 py-2 text-xs text-secondary">No questions match.</p>
      ) : (
        // On desktop the sidebar is sticky, so the list must end above the viewport bottom.
        <ul className="max-h-[50vh] space-y-0.5 overflow-y-auto lg:max-h-[calc(100vh-17rem)]">
          {threads.map((thread) => (
            <li key={thread[0].id}>
              {thread.length === 1 ? (
                historyItem(thread[0])
              ) : (
                <ul aria-label={`Conversation: ${thread[0].question}`} className="space-y-0.5">
                  {thread.map((s, i) => (
                    <li key={s.id} className={i > 0 ? "ml-2.5 border-l border-border pl-1.5" : undefined}>
                      {historyItem(s, i > 0)}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}
      {olderError && (
        <p role="alert" className="px-1 text-xs text-err">
          {olderError}
        </p>
      )}
      {olderCursor && (
        <button type="button" onClick={() => void loadOlderRuns()} disabled={loadingOlder} className={cx(btn.ghost, "mt-1 w-full")}>
          {loadingOlder ? "Loading…" : "Load older questions"}
        </button>
      )}
    </nav>
  );

  return (
    <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_260px]">
      <p className="sr-only" aria-live="polite">
        {liveMessage}
      </p>
      <div className={cx("min-w-0", active ? "space-y-6" : "space-y-4")}>
        {active ? (
          <h1 className="sr-only">Ask Ledgerline</h1>
        ) : (
          <header className="flex flex-col items-center gap-2 pt-0 text-center sm:pt-2">
            <h1 className="font-display text-[1.75rem] font-semibold leading-tight tracking-[-0.025em] sm:text-[2rem]">What would you like to know?</h1>
            <p className="max-w-[560px] text-[14px] leading-relaxed text-secondary">
              <strong className="font-medium text-ink">Quick</strong> answers one focused question.{" "}
              <strong className="font-medium text-ink">Deep</strong> plans several steps and checks the evidence. Every figure links to the
              query behind it.
            </p>
          </header>
        )}

        {blockers.length > 0 && (
          <section aria-label="Setup" className="border border-border bg-surface p-3.5 sm:p-4">
            <h2 className="font-display text-[15px] font-semibold">Before your first question</h2>
            <ol className="mt-2.5 space-y-1.5">
              {blockers.map((b, i) => (
                <li key={b.key} className="flex flex-wrap items-center gap-x-3 gap-y-2.5 bg-panel px-3 py-2.5">
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center bg-ink text-[12px] font-semibold text-canvas" aria-hidden="true">
                    {i + 1}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-[13.5px] font-medium text-ink">{b.title}</span>
                    <span className="block text-[12px] text-secondary">{b.body}</span>
                  </span>
                  <span className="flex basis-full flex-wrap items-center gap-2 pl-9 sm:basis-auto sm:pl-0">
                    {b.sample && (
                      <SampleDataButton
                        className={btnSm.secondary}
                        onImported={(snap) => setChosenSnapshot(snap.snapshot_id)}
                      />
                    )}
                    <Link href={b.href} className={i === 0 ? btnSm.primary : btnSm.secondary}>
                      {b.cta}
                    </Link>
                  </span>
                </li>
              ))}
            </ol>
          </section>
        )}

        {composer}
        {!active && isSampleDataset(snapshot) && (
          <section aria-label="Example questions" className="space-y-2">
            <p className="text-xs text-secondary">Questions to try on the sample data:</p>
            <ul className="flex flex-wrap gap-2">
              {SAMPLE_QUESTIONS.map((q) => (
                <li key={q}>
                  <button
                    type="button"
                    className="min-h-[36px] border border-border bg-surface px-3 text-left text-[13px] text-body transition-colors hover:border-ink hover:text-ink"
                    onClick={() => {
                      setDraftQuestion(q);
                      textareaRef.current?.focus();
                    }}
                  >
                    {q}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
        {submitError && (
          <Notice tone="err" title={submitError.message}>
            {submitError.hint}
          </Notice>
        )}

        {active && (
          <article aria-label="Current question" className="space-y-4">
            <div className="flex justify-end">
              <p className="max-w-[560px] whitespace-pre-wrap break-words bg-bubble px-4 py-3 text-[14.5px] leading-relaxed">{active.question}</p>
            </div>

            <section aria-label="Answer" aria-busy={isWorking} className="space-y-4 border border-border bg-surface p-5 shadow-card sm:px-6 sm:py-[22px]">
              <div className="flex flex-wrap items-center justify-between gap-2.5">
                <span className="min-w-0 truncate text-xs text-secondary">
                  {active.mode === "deep" ? "Deep investigation" : "Quick answer"}
                  {activeDataset ? ` · ${datasetLabel(activeDataset)} v${activeDataset.version}` : ""}
                  {active.createdAt ? ` · ${relativeTime(active.createdAt)}` : ""}
                </span>
                <div className="flex shrink-0 flex-wrap items-center gap-2">
                  <Badge tone={statusTone(active.status)} dot pulse={isWorking}>
                    {statusLabel(active.status)}
                  </Badge>
                  {active.status === "completed" && active.answer && (
                    <button type="button" onClick={() => void copyAnswer()} className={btn.ghost}>
                      {copied ? "Copied" : "Copy"}
                    </button>
                  )}
                  {active.status === "completed" && active.runId && active.answer && (
                    <ExportMenu workspaceId={activeWorkspaceId} runId={active.runId} question={active.question} />
                  )}
                  {active.status === "completed" && active.runId && (
                    pinnedRunIds.has(active.runId) ? (
                      <Link href="/dashboard" className={btn.secondary}>
                        Pinned · open Dashboard
                      </Link>
                    ) : (
                      <button type="button" onClick={() => void handlePin()} disabled={pinningRunId === active.runId} className={btn.secondary}>
                        {pinningRunId === active.runId ? "Pinning…" : "Pin to Dashboard"}
                      </button>
                    )
                  )}
                  {isBusy && active.runId && (
                    <button type="button" onClick={() => void handleCancel()} className={btn.secondary}>
                      Cancel
                    </button>
                  )}
                  {!isBusy && active.runId && isTerminal(active.status) && (
                    confirmDeleteId === active.id ? (
                      <>
                        <button type="button" onClick={() => void handleDelete()} className={cx(btn.secondary, "border-err text-err hover:border-err")}>
                          Confirm delete
                        </button>
                        <button type="button" onClick={() => setConfirmDeleteId(null)} className={btn.ghost}>
                          Cancel
                        </button>
                      </>
                    ) : (
                      <button type="button" onClick={() => void handleDelete()} className={btn.ghost}>
                        Delete
                      </button>
                    )
                  )}
                </div>
              </div>

              {active.connectionLost && isBusy && (
                <Notice
                  tone="warn"
                  title="Live updates stopped"
                  action={
                    <button type="button" className={btn.secondary} onClick={() => active.runId && attach(active.id, active.runId, active.lastSeq)}>
                      Reconnect
                    </button>
                  }
                >
                  The run may still be working. This page also checks its status every few seconds.
                </Notice>
              )}
              {errorInfo && (
                <Notice
                  tone={active.status === "failed" ? "err" : "warn"}
                  title={errorInfo.title}
                  action={
                    active.status === "failed" && canAsk && (snapshot || active.snapshotId) ? (
                      <button
                        type="button"
                        className={btn.secondary}
                        disabled={submitting}
                        onClick={() => {
                          const targetId = active.snapshotId && snapshotById.get(active.snapshotId)?.approved ? active.snapshotId : snapshot?.snapshot_id;
                          if (!targetId) return;
                          void startRun(active.question, { snapshotId: targetId }, setDraftQuestion);
                        }}
                      >
                        Retry question
                      </button>
                    ) : undefined
                  }
                >
                  {errorInfo.hint}
                  {errorInfo.title !== active.error && (
                    <Details summary="Details" className="mt-1">
                      <p className="break-words font-mono text-[11.5px] opacity-80">{active.error}</p>
                    </Details>
                  )}
                </Notice>
              )}
              {timeLimited && (
                <Notice tone="warn" title="Stopped at the time limit">
                  The answer may be incomplete. Try Quick mode or a narrower question.
                </Notice>
              )}
              {budgetWarnings.length > 0 && (
                <Notice tone="warn" title="This run is close to its budget">
                  {budgetWarnings
                    .map((e) => `${BUDGET_RESOURCES[String(e.payload.resource)] ?? String(e.payload.resource)}: ${Number(e.payload.used).toLocaleString()} of ${Number(e.payload.limit).toLocaleString()} used`)
                    .join(" · ")}
                  . Later steps may be skipped and the answer may be partial.
                </Notice>
              )}
              {pinError && <Notice tone="err" title={pinError} />}

              {isAwaitingInput && active.clarificationPrompt && (
                <form onSubmit={handleClarificationSubmit} className="space-y-3 border border-border-strong bg-canvas p-4">
                  <Notice tone="warn" title="A detail is needed to continue">
                    {active.clarificationPrompt}
                  </Notice>
                  <textarea
                    aria-label="Clarification answer"
                    value={active.clarificationDraft}
                    onChange={(event) => patch(active.id, () => ({ clarificationDraft: event.target.value }))}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                        e.preventDefault();
                        e.currentTarget.form?.requestSubmit();
                      }
                    }}
                    rows={3}
                    maxLength={inputLimits?.max_clarification_chars}
                    className={cx(inputBaseCls, "min-h-[84px] resize-y py-2.5")}
                    placeholder="Add the missing detail"
                  />
                  <div className="flex items-center justify-end gap-3">
                    <span className="hidden text-xs text-secondary sm:inline">{isMac ? "⌘ + Enter" : "Ctrl + Enter"}</span>
                    <button
                      type="submit"
                      disabled={!active.clarificationDraft.trim() || clarificationSubmitting}
                      className={btn.primary}
                    >
                      {clarificationSubmitting ? "Sending…" : "Continue"}
                    </button>
                  </div>
                </form>
              )}

              {isWorking && <RunProgress events={active.events} workspaceId={activeWorkspaceId} runId={active.runId ?? null} />}

              {active.answer ? (
                <ErrorBoundary key={active.id} title="This answer couldn't be shown">
                  <EvidenceContext.Provider value={active.runId ? setOpenEvidence : null}>
                    <SafeMarkdown content={active.answer} />
                  </EvidenceContext.Provider>
                  {active.status === "completed" && <AnswerCharts charts={active.charts} />}
                  {active.status === "completed" && <AnswerVerification outcome={active.outcome} criteria={active.criteria} />}
                </ErrorBoundary>
              ) : (
                isWorking && (
                  <div className="space-y-2.5" aria-hidden="true">
                    <div className="h-3 w-[86%] animate-pulse bg-bubble" />
                    <div className="h-3 w-[64%] animate-pulse bg-bubble" />
                    <div className="h-3 w-[72%] animate-pulse bg-bubble" />
                  </div>
                )
              )}

              {active.status === "completed" && active.runId && (
                <form onSubmit={(e) => void handleFollowUp(e)} className="flex flex-col gap-2 border-t border-border pt-3 sm:flex-row">
                  <label htmlFor="follow-up" className="sr-only">
                    Ask a follow-up question
                  </label>
                  <input
                    id="follow-up"
                    value={followUpDraft}
                    onChange={(e) => setFollowUpDraft(e.target.value)}
                    placeholder="Ask a follow-up about this answer"
                    maxLength={inputLimits?.max_question_chars}
                    className={cx(inputCls, "min-h-[40px] flex-1")}
                  />
                  <button type="submit" disabled={submitting || !followUpDraft.trim()} className={btn.secondary}>
                    Follow up
                  </button>
                </form>
              )}

              {nonSchemaTools.length > 0 && (
                <div className="space-y-2 border-t border-border pt-3">
                  <p className="text-[12px] font-semibold uppercase tracking-wide text-secondary">Evidence · {nonSchemaTools.length} quer{nonSchemaTools.length === 1 ? "y" : "ies"}</p>
                  <ul className="flex flex-wrap gap-1.5">
                    {nonSchemaTools.map((evt) => {
                        const p = evt.payload;
                        const evId = typeof p.evidence_id === "string" ? p.evidence_id : null;
                        const label = `${OPERATION_LABELS[String(p.operation ?? "")] ?? "Query"}${
                          p.row_count !== undefined ? ` · ${Number(p.row_count).toLocaleString()} rows` : ""
                        }`;
                        return (
                          <li key={evt.event_id}>
                            {evId && active.runId ? (
                              <button
                                type="button"
                                onClick={() => setOpenEvidence(evId)}
                                className="inline-flex min-h-[30px] items-center gap-1.5 border border-border bg-panel px-2 text-[12px] text-body transition-colors hover:border-ink hover:text-ink"
                              >
                                <span className="h-1.5 w-1.5 rounded-full bg-ok-dot" aria-hidden="true" />
                                {label}
                              </button>
                            ) : (
                              <span
                                title={p.ok ? undefined : "This query failed; the model was told and could retry."}
                                className="inline-flex min-h-[30px] items-center gap-1.5 border border-border px-2 text-[12px] text-secondary"
                              >
                                <span className={cx("h-1.5 w-1.5 rounded-full", p.ok ? "bg-secondary" : "bg-err-dot")} aria-hidden="true" />
                                {p.ok ? label : `${label} · failed`}
                              </span>
                            )}
                          </li>
                        );
                      })}
                  </ul>
                </div>
              )}

              {(active.runId || active.events.length > 0) && (
                <div className="border-t border-border pt-2">
                  <Details summary="Technical details">
                    <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[12px]">
                      {active.runId && (
                        <>
                          <dt className="text-secondary">Run</dt>
                          <dd className="break-all font-mono text-body">{active.runId}</dd>
                        </>
                      )}
                      {models.length > 0 && (
                        <>
                          <dt className="text-secondary">Models</dt>
                          <dd className="text-body">{models.join(", ")}</dd>
                        </>
                      )}
                      {route && (
                        <>
                          <dt className="text-secondary">Path</dt>
                          <dd className="text-body" title={typeof planEvent?.payload.route_reason === "string" ? planEvent.payload.route_reason : undefined}>
                            {ROUTE_LABELS[route] ?? humanize(route)}
                          </dd>
                        </>
                      )}
                      <dt className="text-secondary">Events</dt>
                      <dd className="text-body">{active.events.length}</dd>
                    </dl>
                    {active.events.length > 0 && (
                      <div className="mt-2 max-h-64 space-y-1 overflow-y-auto">
                        {active.events.map((evt) => (
                          <div key={evt.event_id} className="flex gap-2.5 bg-panel px-2.5 py-[7px] font-mono text-[11.5px] leading-relaxed">
                            <span className="shrink-0 text-secondary">#{evt.sequence}</span>
                            <span className="shrink-0 text-ink">{evt.type}</span>
                            <span className="min-w-0 break-all text-secondary">{eventSummary(evt.payload)}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </Details>
                </div>
              )}
            </section>
          </article>
        )}
      </div>

      <aside className="order-first space-y-3 lg:sticky lg:top-8 lg:order-none">
        <div className="flex gap-2">
          <button type="button" onClick={startNew} className={cx(btn.secondary, "flex-1 px-3 font-medium")}>
            New question
          </button>
          <button
            type="button"
            className={cx(btn.secondary, "px-3 lg:hidden")}
            aria-expanded={historyOpen}
            aria-controls="ask-history"
            onClick={() => setHistoryOpen((o) => !o)}
          >
            History{historyCounts.all ? ` (${historyCounts.all})` : ""}
          </button>
        </div>
        <div id="ask-history" className={cx(historyOpen ? "block" : "hidden", "lg:block")}>
          {history}
        </div>
      </aside>

      {openEvidence && active?.runId && (
        <ErrorBoundary key={openEvidence} title="This evidence couldn't be shown">
          <EvidencePanel workspaceId={activeWorkspaceId} runId={active.runId} evidenceId={openEvidence} onClose={closeEvidence} />
        </ErrorBoundary>
      )}
    </div>
  );
}
