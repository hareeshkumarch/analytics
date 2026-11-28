import { describe, expect, it } from "vitest";
import { isSampleDataset } from "@/lib/sample";
import { ApiError } from "@/lib/api/client";
import { datasetLabel, datasetTables, isApiUnreachable, providerLabel, sortSnapshotsNewestFirst, type SnapshotSummary } from "@/lib/workspace";

const snap = (id: string, created_at: string | null, tables = ["t"]): SnapshotSummary => ({
  snapshot_id: id,
  source_id: "s",
  version: 1,
  total_rows: 0,
  tables,
  created_at,
});

describe("sortSnapshotsNewestFirst", () => {
  it("orders by creation time and keeps undated items in API order", () => {
    const sorted = sortSnapshotsNewestFirst([
      snap("old", "2026-01-01T00:00:00Z"),
      snap("undated-a", null),
      snap("new", "2026-06-01T00:00:00Z"),
      snap("undated-b", null),
    ]);
    expect(sorted.map((s) => s.snapshot_id)).toEqual(["new", "old", "undated-a", "undated-b"]);
  });
});

describe("isSampleDataset", () => {
  it("recognises the four sample tables in any order", () => {
    expect(isSampleDataset(snap("x", null, ["refunds", "orders", "payouts", "fees"]))).toBe(true);
    expect(isSampleDataset(snap("x", null, ["orders"]))).toBe(false);
    expect(isSampleDataset(null)).toBe(false);
  });
});

describe("providerLabel", () => {
  it("formats brand capitalization accurately", () => {
    expect(providerLabel("openai")).toBe("OpenAI");
    expect(providerLabel("xai")).toBe("xAI");
    expect(providerLabel("nvidia_nim")).toBe("NVIDIA NIM");
    expect(providerLabel("anthropic")).toBe("Anthropic");
  });
});

describe("datasetLabel", () => {
  it("prefers the display name and keeps the table list as secondary text", () => {
    const named = { ...snap("s1", null, ["fees", "orders", "payouts"]), display_name: "Sample shop payments (INR)" };
    expect(datasetLabel(named)).toBe("Sample shop payments (INR)");
    expect(datasetTables(named)).toBe("fees +2 more");
    expect(datasetLabel({ ...named, display_name: null })).toBe("fees +2 more");
  });
});

describe("isApiUnreachable", () => {
  it("treats a failed fetch, a server error or a non-JSON 404 page as unreachable", () => {
    expect(isApiUnreachable(new ApiError("Failed to fetch", { status: 0 }))).toBe(true);
    expect(isApiUnreachable(new ApiError("Bad gateway", { status: 502 }))).toBe(true);
    expect(isApiUnreachable(new ApiError("Not Found", { status: 404, jsonBody: false }))).toBe(true);
  });

  it("does not treat JSON answers from the API or non-API errors as unreachable", () => {
    expect(isApiUnreachable(new ApiError("Not found", { status: 404 }))).toBe(false);
    expect(isApiUnreachable(new ApiError("Unauthorized", { status: 401 }))).toBe(false);
    expect(isApiUnreachable(new Error("boom"))).toBe(false);
    expect(isApiUnreachable(null)).toBe(false);
  });
});
