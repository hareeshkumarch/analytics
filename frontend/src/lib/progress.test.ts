import { describe, expect, it } from "vitest";
import { progressSteps } from "./progress";
import type { SseEnvelope } from "./sse";
import type { TaskRecordOut } from "./api/types";

const ev = (type: string, payload: Record<string, unknown>, sequence = 1): SseEnvelope =>
  ({ type, payload, sequence }) as unknown as SseEnvelope;

const rec = (task_id: string, status: string, plan_revision = 1): TaskRecordOut =>
  ({ task_id, status, plan_revision }) as TaskRecordOut;

describe("progressSteps", () => {
  it("falls back to events before any task record exists", () => {
    const steps = progressSteps(
      [ev("plan.updated", { tasks: ["t1_analyze", "t2_review"] }), ev("task.status", { task_id: "t1_analyze", status: "running" }), ev("tool.result", { task_id: "t1_analyze", tool: "run_query_ir" })],
      undefined,
    );
    expect(steps).toEqual([
      { taskId: "t1_analyze", status: "running", queries: 1 },
      { taskId: "t2_review", status: "pending", queries: 0 },
    ]);
  });

  it("uses records as the authority, including restored and replanned tasks", () => {
    const steps = progressSteps(
      [ev("plan.updated", { tasks: ["t1_analyze", "t2_review"] })],
      [rec("t1_analyze", "completed"), rec("t2_review", "failed"), rec("t3_remediate", "running", 2)],
    );
    expect(steps.map((s) => [s.taskId, s.status])).toEqual([
      ["t1_analyze", "completed"],
      ["t2_review", "failed"],
      ["t3_remediate", "running"],
    ]);
  });

  it("lets a newer live event win until the records are refetched", () => {
    const steps = progressSteps([ev("task.status", { task_id: "t1_analyze", status: "completed" })], [rec("t1_analyze", "running")]);
    expect(steps[0].status).toBe("completed");
  });
});
