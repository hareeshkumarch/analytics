import { afterEach, describe, expect, it, vi } from "vitest";
import { subscribeRunEvents, type SseEnvelope } from "@/lib/sse";

class FakeEventSource {
  static CLOSED = 2;
  static last: FakeEventSource;
  readyState = 1;
  closed = false;
  onerror: (() => void) | null = null;
  listeners = new Map<string, (e: { data: string }) => void>();
  constructor(public url: string) {
    FakeEventSource.last = this;
  }
  addEventListener(type: string, fn: (e: { data: string }) => void) {
    this.listeners.set(type, fn);
  }
  close() {
    this.closed = true;
  }
  emit(evt: Partial<SseEnvelope>) {
    this.listeners.get(evt.type ?? "run.status")?.({ data: JSON.stringify({ payload: {}, ...evt }) });
  }
}

afterEach(() => vi.unstubAllGlobals());

function subscribe(after = 0) {
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal("window", { location: { href: "http://app.test/ask" } });
  const handlers = { onEvent: vi.fn(), onResetDraft: vi.fn(), onDisconnected: vi.fn() };
  const close = subscribeRunEvents("http://api.test/api/v1/workspaces/ws/runs/r1/events", after, handlers);
  return { handlers, close, es: FakeEventSource.last };
}

describe("subscribeRunEvents", () => {
  it("resumes after the given sequence and drops duplicates", () => {
    const { handlers, es } = subscribe(3);
    expect(new URL(es.url).searchParams.get("after")).toBe("3");
    es.emit({ type: "run.status", sequence: 3, event_id: "e3" });
    es.emit({ type: "run.status", sequence: 4, event_id: "e4" });
    es.emit({ type: "run.status", sequence: 4, event_id: "e4" });
    es.emit({ type: "answer.delta", sequence: 5, event_id: "e5" });
    expect(handlers.onEvent.mock.calls.map(([e]) => e.sequence)).toEqual([4, 5]);
  });

  it("reports answer resets and ignores malformed frames", () => {
    const { handlers, es } = subscribe();
    es.listeners.get("answer.delta")?.({ data: "{not json" });
    es.emit({ type: "answer.reset", sequence: 1, event_id: "e1", payload: { replacement_draft_id: "d2" } });
    expect(handlers.onResetDraft).toHaveBeenCalledWith("", "d2");
    expect(handlers.onEvent).toHaveBeenCalledTimes(1);
  });

  it("gives up after repeated reconnect failures", () => {
    const { handlers, es, close } = subscribe();
    for (let i = 0; i < 4; i++) es.onerror?.();
    expect(handlers.onDisconnected).not.toHaveBeenCalled();
    es.onerror?.();
    expect(handlers.onDisconnected).toHaveBeenCalledTimes(1);
    expect(es.closed).toBe(true);
    close();
  });
});
