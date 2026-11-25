import type { SnapshotSummary } from "@/lib/workspace";

export const SAMPLE_NAME = "inr_acceptance";

const SAMPLE_TABLES = ["fees", "orders", "payouts", "refunds"];

export const SAMPLE_QUESTIONS = [
  "What were net sales in October after refunds?",
  "How much did we pay in fees, by fee type?",
  "Do confirmed and in-transit payouts reconcile with eligible settlement?",
];

export function isSampleDataset(snapshot: SnapshotSummary | null | undefined): boolean {
  if (!snapshot) return false;
  const tables = [...snapshot.tables].sort();
  return tables.length === SAMPLE_TABLES.length && tables.every((t, i) => t === SAMPLE_TABLES[i]);
}
