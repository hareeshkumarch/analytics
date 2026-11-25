import { describe, expect, it } from "vitest";
import { isSampleDataset } from "@/lib/sample";
import { sortSnapshotsNewestFirst, type SnapshotSummary } from "@/lib/workspace";

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
