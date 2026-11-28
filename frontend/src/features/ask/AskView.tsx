"use client";

import React, { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "@/lib/toast";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { API_BASE, apiFetch, cancelRun, clarifyRun, createRun, deleteRun, errorHint, errorMessage, newId } from "@/lib/api/client";
import { subscribeRunEvents, SseEnvelope } from "@/lib/sse";
import { useUiStore } from "@/lib/store";
import { PRESET_SUMMARY, useRoutingPolicy } from "@/lib/routing";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { EvidencePanel } from "@/features/ask/EvidencePanel";
import { Badge, FilterChips, Notice, SearchInput, SkeletonRows, btn, btnSm, cx, outlineCls } from "@/components/ui";
import {
  datasetLabel,
  relativeTime,
  useHealth,
  useServerDown,
  useProviders,
  useSnapshots,
  workspacePath,
} from "@/lib/workspace";
import { setUrlParam } from "@/lib/url";
import { SampleDataButton } from "@/components/SampleDataButton";
import { SAMPLE_QUESTIONS, isSampleDataset } from "@/lib/sample";
import type { CreatePinRequest } from "@/lib/api/types";
import { AskComposer } from "@/features/ask/AskComposer";
import { AskThreadTurn } from "@/features/ask/AskThreadTurn";
import {
  FINISHED_MESSAGES,
  HistoryFilter,
  MAX_LIVE_STREAMS,
  RunPage,
  STREAM_FLUSH_MS,
  Session,
  StoredRun,
  applyEvents,
  budgetCopy,
  fromStored,
  isTerminal,
  statusLabel,
} from "@/features/ask/askTypes";

const HistoryItem = React.memo(function HistoryItem({
  id,
  runId,
  question,
  status,
  mode,
  createdAt,
  active,
  followUp,
  onSelect,
}: {
  id: string;
  runId: string | null;
  question: string;
  status: string;
  mode: string;
  createdAt: string | null;
  active: boolean;
  followUp: boolean;
  onSelect: (id: string, runId: string | null) => void;
}) {
  return (
    <button
      type="button"
      aria-current={active ? "true" : undefined}
      onClick={() => onSelect(id, runId)}
      className={cx("block w-full rounded-md px-2 py-1.5 text-left transition-colors", active ? "bg-ink/[0.05] text-ink dark:bg-ink/[0.15]" : "text-ink hover:bg-ink/[0.05] dark:hover:bg-ink/[0.08]")}
    >
      {followUp && <span className="sr-only">Follow-up: </span>}
      <span className="line-clamp-2 text-sm leading-snug">{question}</span>
      <span className="mt-1 flex items-center gap-1.5 text-2xs text-secondary">
        <span
          aria-hidden="true"
          className={cx(
            "h-1.5 w-1.5 rounded-full",
            status === "completed" ? "bg-ok-dot" : status === "failed" ? "bg-err-dot" : isTerminal(status) ? "bg-secondary" : "animate-pulse bg-warn-dot"
          )}
        />
        {statusLabel(status)} · {mode} · {relativeTime(createdAt)}
      </span>
    </button>
  );
});

function AskUrlSync({
  onRunParam,
  onDatasetParam,
}: {
  onRunParam: (runId: string | null) => void;
  onDatasetParam: (snapshotId: string) => void;
}) {
  const searchParams = useSearchParams();
  const run = searchParams.get("run");
  const dataset = searchParams.get("dataset");
  const prevRun = useRef<string | null | undefined>(undefined);
  const prevDataset = useRef<string | null>(null);

  useEffect(() => {
    if (prevRun.current !== run) {
      const first = prevRun.current === undefined;
      prevRun.current = run;
      if (!first || run) onRunParam(run);
    }
  }, [run, onRunParam]);

  useEffect(() => {
    if (dataset && dataset !== prevDataset.current) {
      prevDataset.current = dataset;
      onDatasetParam(dataset);
    }
  }, [dataset, onDatasetParam]);

  return null;
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
  const serverDown = useServerDown();
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
  const [clarificationSubmitting, setClarificationSubmitting] = useState(false);
  const [openEvidence, setOpenEvidence] = useState<{ runId: string; evidenceId: string } | null>(null);
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyFilter, setHistoryFilter] = useState<HistoryFilter>("all");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [isMac, setIsMac] = useState(false);
  const closeEvidence = useCallback(() => setOpenEvidence(null), []);
  const handleOpenEvidence = useCallback((runId: string, evidenceId: string) => setOpenEvidence({ runId, evidenceId }), []);
  const closers = useRef(new Map<string, () => void>());
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const turnRefs = useRef(new Map<string, HTMLElement | null>());
  const fetchedDetailsRef = useRef(new Set<string>());
  const unmounted = useRef(false);

  useEffect(() => {
    setIsMac(/Mac|iPhone|iPad/i.test(navigator.platform || navigator.userAgent));
  }, []);

  const handleUrlRun = useCallback((runId: string | null) => {
    if (runId) {
      setPendingRunParam(runId);
    } else {
      setPendingRunParam(null);
      setActiveId(null);
      setOpenEvidence(null);
      setConfirmDeleteId(null);
      setPinError(null);
    }
  }, []);

  const handleUrlDataset = useCallback((datasetId: string) => {
    setChosenSnapshot(datasetId);
  }, []);

  useEffect(() => {
    closers.current.forEach((close) => close());
    closers.current.clear();
    pending.current.forEach((p) => window.clearTimeout(p.timer));
    pending.current.clear();
    fetchedDetailsRef.current.clear();
    setSessions([]);
    setActiveId(null);
    setPendingRunParam(null);
    setOpenEvidence(null);
    setConfirmDeleteId(null);
    setPinError(null);
    setOlderRuns({ runs: [], next: undefined });
  }, [activeWorkspaceId]);

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
        const followUps = r.answer?.follow_ups ?? s.followUps;
        const limitations = r.answer?.limitations ?? s.limitations;
        const prompt = r.clarification_prompt ?? null;
        const revision = r.clarification_revision ?? s.clarificationRevision;
        if (
          r.status === s.status &&
          (!serverAnswer || serverAnswer === s.answer) &&
          sameCharts &&
          (r.conversation_id ?? null) === s.conversationId &&
          nextError === s.error &&
          outcome === s.outcome &&
          followUps.join("\n") === s.followUps.join("\n") &&
          limitations.join("\n") === s.limitations.join("\n") &&
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
          limitations,
          followUps,
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

  const pending = useRef(new Map<string, { events: SseEnvelope[]; timer: number }>());

  const flush = useCallback(
    (id: string) => {
      const queued = pending.current.get(id);
      if (!queued) return;
      pending.current.delete(id);
      window.clearTimeout(queued.timer);
      patch(id, (s) => applyEvents(s, queued.events));
    },
    [patch]
  );

  const enqueue = useCallback(
    (id: string, evt: SseEnvelope) => {
      const queued = pending.current.get(id);
      if (queued) queued.events.push(evt);
      else pending.current.set(id, { events: [evt], timer: window.setTimeout(() => flush(id), STREAM_FLUSH_MS) });
    },
    [flush]
  );

  const closeStream = useCallback(
    (id: string) => {
      flush(id);
      closers.current.get(id)?.();
      closers.current.delete(id);
    },
    [flush]
  );

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
          enqueue(sessionId, evt);
          if (evt.type === "run.finished") {
            closeStream(sessionId);
            void queryClient.invalidateQueries({ queryKey: ["runs", activeWorkspaceId] });
            void queryClient.invalidateQueries({ queryKey: ["usage", activeWorkspaceId] });
            if (!fetchedDetailsRef.current.has(runId)) {
              fetchedDetailsRef.current.add(runId);
              void apiFetch<StoredRun>(workspacePath(activeWorkspaceId, `/runs/${encodeURIComponent(runId)}`))
                .then((detail) => {
                  if (unmounted.current) return;
                  const lims = detail.answer?.limitations;
                  if (Array.isArray(lims) && lims.length > 0) {
                    patch(sessionId, () => ({ limitations: lims }));
                  }
                })
                .catch(() => {});
            }
          }
        },
        onResetDraft: () => flush(sessionId),
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
    [activeWorkspaceId, closeStream, enqueue, flush, patch, queryClient]
  );

  const active = sessions.find((s) => s.id === activeId) ?? null;
  const activeThread = useMemo(() => {
    if (!active) return [];
    if (!active.conversationId) return [active];
    return sessions
      .filter((s) => s.conversationId === active.conversationId)
      .sort((a, b) => (a.createdAt ?? "").localeCompare(b.createdAt ?? "") || a.id.localeCompare(b.id));
  }, [active, sessions]);
  const latestTurn = activeThread.length ? activeThread[activeThread.length - 1] : active;

  const watched = useRef<{ id: string | null; status: string | null }>({ id: null, status: null });
  const activeKey = active?.id ?? null;
  const activeStatus = active?.status ?? null;

  useEffect(() => {
    const previous = watched.current;
    watched.current = { id: activeKey, status: activeStatus };
    if (previous.id !== activeKey) {
      setLiveMessage("");
      if (activeKey && activeThread.length > 1) {
        turnRefs.current.get(activeKey)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
      return;
    }
    const finished = activeStatus !== null && isTerminal(activeStatus);
    const wasRunning = previous.status !== null && !isTerminal(previous.status);
    if (finished && wasRunning) setLiveMessage(FINISHED_MESSAGES[activeStatus] ?? "");
  }, [activeKey, activeStatus, activeThread.length]);

  // Opening a conversation loads traces and single-run details (such as limitations) for its turns.
  useEffect(() => {
    for (const s of activeThread) {
      if (!s.runId) continue;
      if (s.status === "completed" && s.limitations.length === 0 && !fetchedDetailsRef.current.has(s.runId)) {
        const rid = s.runId;
        const sid = s.id;
        fetchedDetailsRef.current.add(rid);
        void apiFetch<StoredRun>(workspacePath(activeWorkspaceId, `/runs/${encodeURIComponent(rid)}`))
          .then((detail) => {
            if (unmounted.current) return;
            const lims = detail.answer?.limitations;
            if (Array.isArray(lims) && lims.length > 0) {
              patch(sid, () => ({ limitations: lims }));
            }
          })
          .catch(() => {});
      }
      if (!s.streaming && !closers.current.has(s.id) && !s.traceLoaded) {
        attach(s.id, s.runId, 0);
      }
    }
  }, [activeThread, activeWorkspaceId, attach, patch]);

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

  const selectSession = useCallback((id: string, runId: string | null) => {
    setOpenEvidence(null);
    setPinError(null);
    setConfirmDeleteId(null);
    setActiveId(id);
    setHistoryOpen(false);
    setUrlParam("run", runId);
  }, []);

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
        limitations: [],
        followUps: [],
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
    const anchor = latestTurn ?? active;
    if (!question || submitting || !anchor?.runId) return;
    const targetSnapshotId =
      anchor.snapshotId && snapshotById.get(anchor.snapshotId)?.approved ? anchor.snapshotId : snapshot?.snapshot_id;
    if (!targetSnapshotId) return;
    setFollowUpDraft("");
    await startRun(question, { snapshotId: targetSnapshotId, followUpOf: anchor.runId }, setFollowUpDraft);
  }

  async function handleCancel(target: Session) {
    if (!target.runId) return;
    const id = target.id;
    try {
      const r = await cancelRun(activeWorkspaceId, target.runId);
      patch(id, () => ({ status: r.status }));
      void queryClient.invalidateQueries({ queryKey: ["runs", activeWorkspaceId] });
      if (isTerminal(r.status)) closeStream(id);
    } catch (err) {
      patch(id, () => ({ error: errorMessage(err, "Couldn't cancel the run.") }));
    }
  }

  async function handleConfirmDelete(target: Session) {
    if (!target.runId) return;
    const { id, runId } = target;
    setConfirmDeleteId(null);
    try {
      await deleteRun(activeWorkspaceId, runId);
      closeStream(id);
      setOlderRuns((prev) => ({ ...prev, runs: prev.runs.filter((r) => r.run_id !== runId) }));
      queryClient.setQueryData<RunPage>(["runs", activeWorkspaceId], (page) =>
        page ? { ...page, runs: page.runs.filter((r) => r.run_id !== runId) } : page
      );
      setSessions((prev) => prev.filter((s) => s.id !== id));
      if (activeId === id) {
        const nextTurn = activeThread.find((t) => t.id !== id) ?? null;
        setActiveId(nextTurn ? nextTurn.id : null);
        setUrlParam("run", nextTurn?.runId ?? null);
      }
      if (openEvidence?.runId === runId) setOpenEvidence(null);
      void queryClient.invalidateQueries({ queryKey: ["pins", activeWorkspaceId] });
    } catch (err) {
      patch(id, () => ({ error: errorMessage(err, "Couldn't delete this question.") }));
    }
  }

  async function handleClarificationSubmit(target: Session, e: React.FormEvent) {
    e.preventDefault();
    if (!target.runId || !target.clarificationPrompt || clarificationSubmitting) return;
    const answer = target.clarificationDraft.trim();
    if (!answer) return;
    const id = target.id;
    setClarificationSubmitting(true);
    patch(id, () => ({ error: null }));
    try {
      const result = await clarifyRun(
        activeWorkspaceId,
        target.runId,
        target.clarificationRevision,
        answer
      );
      patch(id, () => ({
        status: result.status,
        clarificationRevision: result.revision,
        clarificationPrompt: null,
        clarificationDraft: "",
      }));
      void queryClient.invalidateQueries({ queryKey: ["runs", activeWorkspaceId] });
      if (!closers.current.has(id)) attach(id, target.runId, target.lastSeq);
    } catch (err) {
      patch(id, () => ({ error: errorMessage(err, "Couldn't send your clarification.") }));
      void queryClient.invalidateQueries({ queryKey: ["runs", activeWorkspaceId] });
    } finally {
      setClarificationSubmitting(false);
    }
  }

  async function handlePin(target: Session) {
    if (!target.runId || target.status !== "completed" || pinnedRunIds.has(target.runId)) return;
    const runId = target.runId;
    setPinningRunId(runId);
    setPinError(null);
    try {
      await apiFetch(workspacePath(activeWorkspaceId, "/pins"), {
        method: "POST",
        body: JSON.stringify({
          title: target.question.trim().slice(0, inputLimits?.max_pin_title_chars) || "Pinned answer",
          question_text: target.question,
          run_id: runId,
        } satisfies CreatePinRequest),
      });
      setPinnedRunIds((previous) => new Set(previous).add(runId));
      toast("Pinned to Dashboard.");
      await queryClient.invalidateQueries({ queryKey: ["pins", activeWorkspaceId] });
    } catch (err) {
      setPinError(errorMessage(err, "Couldn't pin this answer."));
    } finally {
      setPinningRunId(null);
    }
  }

  function handleRetry(target: Session) {
    const targetId = target.snapshotId && snapshotById.get(target.snapshotId)?.approved ? target.snapshotId : snapshot?.snapshot_id;
    if (!targetId) return;
    void startRun(target.question, { snapshotId: targetId }, setDraftQuestion);
  }

  function startNew() {
    setActiveId(null);
    setOpenEvidence(null);
    setConfirmDeleteId(null);
    setPinError(null);
    setUrlParam("run", null);
    requestAnimationFrame(() => textareaRef.current?.focus());
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

  const presetSummary = routing.data ? PRESET_SUMMARY[routing.data.preset] : undefined;
  const activeDataset = active?.snapshotId ? snapshotById.get(active.snapshotId) : undefined;
  const composer = (
    <AskComposer
      variant="new"
      value={draftQuestion}
      onChange={setDraftQuestion}
      onSubmit={handleSubmit}
      textareaRef={textareaRef}
      runMode={runMode}
      onRunModeChange={setRunMode}
      snapshots={snapshots}
      approvedCount={approvedSnapshots.length}
      selectedSnapshot={snapshot ?? undefined}
      onSnapshotChange={setChosenSnapshot}
      snapshotsLoading={snapshotsQuery.isLoading}
      canAsk={canAsk}
      submitting={submitting}
      presetSummary={presetSummary ?? null}
      routingLoading={routing.isLoading}
      isMac={isMac}
      maxLength={inputLimits?.max_question_chars}
      budget={budget}
      healthError={health.isError}
      serverDown={serverDown}
    />
  );

  const historyItem = (s: Session, followUp = false) => (
    <HistoryItem
      id={s.id}
      runId={s.runId}
      question={s.question}
      status={s.status}
      mode={s.mode}
      createdAt={s.createdAt}
      active={s.id === activeId}
      followUp={followUp}
      onSelect={selectSession}
    />
  );

  const showHistoryControls = sortedSessions.length > 0 || Boolean(hq) || historyFilter !== "all";
  const history = (
    <nav aria-label="Question history" className="space-y-2">
      {showHistoryControls && (
        <>
          <SearchInput dense label="Search history" placeholder="Search questions" value={historyQuery} onChange={setHistoryQuery} />
          <FilterChips
            fill
            label="Filter history"
            value={historyFilter}
            onChange={setHistoryFilter}
            options={[
              { value: "all", label: "All", count: historyCounts.all },
              { value: "done", label: "Done", count: historyCounts.done },
              { value: "running", label: "Running", count: historyCounts.running },
              { value: "failed", label: "Failed", count: historyCounts.failed },
            ]}
          />
        </>
      )}
      {sortedSessions.length === 0 && runsQuery.isLoading && !serverDown ? (
        <SkeletonRows rows={5} rowClassName="h-11" />
      ) : sortedSessions.length === 0 ? (
        <p className="px-1 py-2 text-xs leading-relaxed text-secondary" role={runsQuery.error && !serverDown ? "alert" : undefined}>
          {serverDown
            ? "History will appear once the server is back."
            : runsQuery.error
            ? `Couldn't load history: ${errorMessage(runsQuery.error)}`
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
    // History sits in a left column (DOM order keeps the question first for keyboard users); the conversation
    // is one centred reading column.
    <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[248px_minmax(0,1fr)] lg:gap-10">
      <Suspense fallback={null}>
        <AskUrlSync onRunParam={handleUrlRun} onDatasetParam={handleUrlDataset} />
      </Suspense>
      <p className="sr-only" aria-live="polite">
        {liveMessage}
      </p>
      {active && <h1 className="sr-only">Ask Ledgerline</h1>}
      <div
        className={cx(
          "mx-auto w-full min-w-0 max-w-[760px]",
          // Before the first question the start screen fills the viewport left after the shell's chrome: centred
          // from tablet up, and on phones the question box sits at the bottom within thumb reach.
          active
            ? "space-y-6"
            : "flex min-h-[calc(100dvh-14rem)] flex-col gap-4 sm:min-h-[calc(100dvh-13rem)] sm:justify-center sm:pb-[6vh] md:min-h-[calc(100dvh-8rem)] lg:min-h-[calc(100dvh-4rem)]"
        )}
      >
        {!active && (
          <header className="relative isolate flex flex-1 flex-col items-center justify-center gap-3 py-4 text-center sm:flex-none sm:pb-2 sm:pt-0">
            <div aria-hidden="true" className="bg-dot-grid pointer-events-none absolute -inset-x-6 -bottom-4 top-0 -z-10" />
            <h1 className="font-display text-[28px] font-[360] leading-[1.2] sm:text-[32px]">What would you like to know?</h1>
            <p className="max-w-[560px] text-base leading-relaxed text-secondary">
              <strong className="font-medium text-ink">Quick</strong> answers one focused question.{" "}
              <strong className="font-medium text-ink">Deep</strong> plans several steps and checks the evidence. Every figure links to the
              query behind it.
            </p>
          </header>
        )}

        {!active && blockers.length > 0 && !serverDown && (
          <section aria-label="Setup" className="rounded-lg border border-border bg-surface p-3.5 sm:p-4">
            <h2 className="text-md font-semibold">Before your first question</h2>
            <ol className="mt-2.5 space-y-1.5">
              {blockers.map((b, i) => (
                <li key={b.key} className="flex flex-wrap items-center gap-x-3 gap-y-2.5 rounded-lg bg-panel px-3 py-2.5">
                  <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent text-xs font-semibold text-on-accent" aria-hidden="true">
                    {i + 1}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-base font-medium text-ink">{b.title}</span>
                    <span className="block text-xs text-secondary">{b.body}</span>
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

        {!active && composer}
        {!active && isSampleDataset(snapshot) && (
          <section aria-label="Example questions" className="space-y-2">
            <p className="text-xs text-secondary">Questions to try on the sample data:</p>
            <ul className="flex flex-wrap gap-2">
              {SAMPLE_QUESTIONS.map((q) => (
                <li key={q}>
                  <button
                    type="button"
                    className={cx(outlineCls, "min-h-[40px] rounded-lg px-3.5 py-2 text-left text-base text-body hover:text-ink")}
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
          <div className="space-y-6">
            {/* One quiet line of context; "New question" lives at the top of the history column. */}
            <div className="flex min-w-0 flex-wrap items-center gap-2 border-b border-border pb-3 text-xs text-secondary">
              {activeDataset ? (
                <span className="truncate font-medium text-ink">
                  {datasetLabel(activeDataset)} · v{activeDataset.version}
                </span>
              ) : latestTurn?.snapshotId && !snapshotsQuery.isLoading ? (
                <Badge tone="neutral">Removed dataset</Badge>
              ) : null}
              <span>
                {activeThread.length} {activeThread.length === 1 ? "turn" : "turns"}
              </span>
            </div>

            {pinError && <Notice tone="err" title={pinError} />}

            <div className="space-y-10">
              {activeThread.map((turn, idx) => (
                <AskThreadTurn
                  key={turn.id}
                  session={turn}
                  turnIndex={idx}
                  isFocusedTurn={turn.id === active.id}
                  isLatestTurn={idx === activeThread.length - 1}
                  snapshotsLoading={snapshotsQuery.isLoading}
                  snapshotById={snapshotById}
                  fallbackSnapshot={snapshot ?? undefined}
                  workspaceId={activeWorkspaceId}
                  canAsk={canAsk}
                  submitting={submitting}
                  pinningRunId={pinningRunId}
                  pinnedRunIds={pinnedRunIds}
                  confirmDeleteId={confirmDeleteId}
                  clarificationSubmitting={clarificationSubmitting}
                  maxClarificationChars={inputLimits?.max_clarification_chars}
                  registerRef={(el) => {
                    if (el) turnRefs.current.set(turn.id, el);
                    else turnRefs.current.delete(turn.id);
                  }}
                  onPin={(s) => void handlePin(s)}
                  onCancel={(s) => void handleCancel(s)}
                  onRequestDelete={(s) => setConfirmDeleteId(s.id)}
                  onConfirmDelete={(s) => void handleConfirmDelete(s)}
                  onCancelDelete={() => setConfirmDeleteId(null)}
                  onRetry={handleRetry}
                  onClarificationChange={(sid, draft) => patch(sid, () => ({ clarificationDraft: draft }))}
                  onClarificationSubmit={(s, e) => void handleClarificationSubmit(s, e)}
                  onReconnect={(s) => s.runId && attach(s.id, s.runId, s.lastSeq)}
                  onOpenEvidence={handleOpenEvidence}
                  onAskSuggestion={(suggestion, s) => {
                    const targetId =
                      s.snapshotId && snapshotById.get(s.snapshotId)?.approved ? s.snapshotId : snapshot?.snapshot_id;
                    if (!targetId || !s.runId) return;
                    void startRun(suggestion, { snapshotId: targetId, followUpOf: s.runId }, setFollowUpDraft);
                  }}
                />
              ))}
            </div>

            {latestTurn && (
              // Pinned to the bottom of the viewport (above the phone tab bar) so the next question is always in
              // reach; the canvas-coloured fade lets the thread scroll underneath it.
              <div className="sticky bottom-[calc(3.5rem+env(safe-area-inset-bottom))] z-10 -mx-2 bg-gradient-to-t from-canvas from-80% to-canvas/0 px-2 pb-3 pt-5 sm:bottom-0 sm:pb-5">
                {latestTurn.status === "completed" && latestTurn.runId ? (
                  <AskComposer
                    variant="followup"
                    value={followUpDraft}
                    onChange={setFollowUpDraft}
                    onSubmit={(e) => void handleFollowUp(e)}
                    runMode={runMode}
                    onRunModeChange={setRunMode}
                    snapshots={snapshots}
                    approvedCount={approvedSnapshots.length}
                    selectedSnapshot={
                      (latestTurn.snapshotId ? snapshotById.get(latestTurn.snapshotId) : undefined) ?? snapshot ?? undefined
                    }
                    onSnapshotChange={setChosenSnapshot}
                    snapshotsLoading={snapshotsQuery.isLoading}
                    canAsk={canAsk || approvedSnapshots.length > 0}
                    submitting={submitting}
                    isWorking={false}
                    datasetRemoved={
                      Boolean(latestTurn.snapshotId) &&
                      !snapshotsQuery.isLoading &&
                      !snapshotById.get(latestTurn.snapshotId ?? "")
                    }
                    presetSummary={presetSummary ?? null}
                    routingLoading={routing.isLoading}
                    isMac={isMac}
                    maxLength={inputLimits?.max_question_chars}
                    serverDown={serverDown}
                  />
                ) : (
                  composer
                )}
              </div>
            )}
          </div>
        )}
      </div>

      <aside className="order-first space-y-3 lg:sticky lg:top-8">
        <div className="flex justify-end gap-2">
          <button type="button" onClick={startNew} className={cx(btn.secondary, "flex-1", !active && "max-lg:hidden")}>
            New question
          </button>
          <button
            type="button"
            className={cx(btn.secondary, "lg:hidden")}
            aria-expanded={historyOpen}
            aria-controls="ask-history"
            onClick={() => setHistoryOpen((o) => !o)}
          >
            History{historyCounts.all ? ` (${historyCounts.all})` : ""}
          </button>
        </div>
        <div id="ask-history" className={cx(historyOpen ? "block animate-fade-in" : "hidden", "lg:block lg:animate-none")}>
          {history}
        </div>
      </aside>

      {openEvidence && (
        <ErrorBoundary key={`${openEvidence.runId}:${openEvidence.evidenceId}`} title="This evidence couldn't be shown">
          <EvidencePanel
            workspaceId={activeWorkspaceId}
            runId={openEvidence.runId}
            evidenceId={openEvidence.evidenceId}
            onClose={closeEvidence}
          />
        </ErrorBoundary>
      )}
    </div>
  );
}
