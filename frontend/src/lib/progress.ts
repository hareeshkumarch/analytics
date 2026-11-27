import type { SseEnvelope } from "@/lib/sse";
import type { TaskRecordOut } from "@/lib/api/types";

export type StepProgress = { taskId: string; status: string; queries: number };

const RANK: Record<string, number> = { pending: 0, running: 1, completed: 2, failed: 2, cancelled: 2, superseded: 2 };

function rank(status: string | undefined): number {
  return status === undefined ? -1 : (RANK[status] ?? 0);
}

export function progressSteps(events: SseEnvelope[], records: TaskRecordOut[] | undefined): StepProgress[] {
  const plan = [...events].reverse().find((e) => e.type === "plan.updated");
  const planned = Array.isArray(plan?.payload.tasks) ? (plan!.payload.tasks as unknown[]).map(String) : [];
  const fromEvents = new Map<string, string>();
  const queries = new Map<string, number>();
  for (const e of events) {
    const taskId = typeof e.payload.task_id === "string" ? e.payload.task_id : null;
    if (!taskId) continue;
    if (e.type === "task.status") fromEvents.set(taskId, String(e.payload.status ?? ""));
    if (e.type === "tool.result" && e.payload.tool !== "inspect_schema") queries.set(taskId, (queries.get(taskId) ?? 0) + 1);
  }
  const recorded = new Map((records ?? []).map((r) => [r.task_id, r.status]));
  const order = records?.length ? [...records].sort((a, b) => a.plan_revision - b.plan_revision).map((r) => r.task_id) : planned;
  const ids = [...order, ...planned.filter((t) => !order.includes(t))];
  return ids.map((taskId) => {
    const stored = recorded.get(taskId);
    const live = fromEvents.get(taskId);
    const status = rank(live) > rank(stored) ? live! : (stored ?? live ?? "pending");
    return { taskId, status, queries: queries.get(taskId) ?? 0 };
  });
}
