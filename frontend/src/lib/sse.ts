import { API_BASE } from "@/lib/api/client";

export interface SseEnvelope {
  schema_version: number;
  event_id: string;
  sequence: number;
  workspace_id: string;
  run_id: string;
  type: string;
  occurred_at: string;
  payload: Record<string, unknown>;
}

export interface RunStreamHandlers {
  onEvent: (evt: SseEnvelope) => void;
  onResetDraft: (replacementAttemptId: string, replacementDraftId: string) => void;
  /** The stream gave up after repeated failures. The run itself may still be working on the server. */
  onDisconnected: () => void;
}

export const RUN_EVENT_TYPES = [
  "run.status",
  "run.progress",
  "plan.updated",
  "task.status",
  "route.selected",
  "tool.result",
  "model.attempt",
  "clarification.required",
  "artifact.ready",
  "answer.delta",
  "answer.reset",
  "run.finished",
] as const;

const MAX_FAILED_RECONNECTS = 5;

/**
 * Subscribe to a run's events after `afterSequence`. The browser reconnects on its own and the
 * server resumes from Last-Event-ID; duplicates are dropped by sequence and event id.
 */
export function subscribeRunEvents(
  eventsUrl: string,
  afterSequence: number,
  handlers: RunStreamHandlers
): () => void {
  let lastSeq = afterSequence;
  const seenIds = new Set<string>();
  const url = new URL(eventsUrl, new URL(API_BASE, typeof window !== "undefined" ? window.location.href : undefined));
  url.searchParams.set("after", String(lastSeq));

  const es = new EventSource(url.toString());
  let failedReconnects = 0;

  const handleMessage = (raw: MessageEvent<string>) => {
    try {
      const evt = JSON.parse(raw.data) as SseEnvelope;
      if (evt.sequence <= lastSeq || seenIds.has(evt.event_id)) return;
      lastSeq = evt.sequence;
      seenIds.add(evt.event_id);
      failedReconnects = 0;
      if (evt.type === "answer.reset") {
        handlers.onResetDraft(
          String(evt.payload.replacement_attempt_id ?? ""),
          String(evt.payload.replacement_draft_id ?? "")
        );
      }
      handlers.onEvent(evt);
    } catch {
      // Ignore malformed frames; the next valid frame continues the stream.
    }
  };

  for (const evtType of RUN_EVENT_TYPES) {
    es.addEventListener(evtType, handleMessage as EventListener);
  }

  es.onerror = () => {
    failedReconnects += 1;
    if (es.readyState === EventSource.CLOSED || failedReconnects >= MAX_FAILED_RECONNECTS) {
      es.close();
      handlers.onDisconnected();
    }
  };

  return () => es.close();
}
