"use client";

import React from "react";
import { Badge } from "@/components/ui";
import type { CriterionOut } from "@/lib/api/types";

export type Criterion = Pick<CriterionOut, "criterion" | "status"> & Partial<Pick<CriterionOut, "detail">>;

const OUTCOMES: Record<string, { label: string; tone: "ok" | "warn" | "err" }> = {
  success: { label: "Verified", tone: "ok" },
  partial_success: { label: "Partly verified", tone: "warn" },
  needs_review: { label: "Needs review", tone: "err" },
};

const STATUS_LABELS: Record<string, string> = {
  verified: "Verified",
  failed: "Failed",
  blocked: "Not checked",
  not_applicable: "Not applicable",
};

export function AnswerVerification({ outcome, criteria }: { outcome: string | null; criteria: Criterion[] }) {
  const shown = outcome ? OUTCOMES[outcome] : undefined;
  if (!shown) return null;
  return (
    <section aria-label="Answer verification" className="mt-4 border-t border-border pt-3">
      <div className="flex items-center gap-2 text-[12.5px] text-secondary">
        <Badge tone={shown.tone} dot>
          {shown.label}
        </Badge>
        <span>{criteria.filter((c) => c.status === "verified").length} of {criteria.length} checks passed</span>
      </div>
      <ul className="mt-2 space-y-1 text-[12.5px]">
        {criteria.map((c) => (
          <li key={c.criterion} className="flex gap-2">
            <span className={c.status === "verified" ? "text-ok" : c.status === "failed" ? "text-err" : "text-secondary"}>
              {STATUS_LABELS[c.status] ?? c.status}
            </span>
            <span className="text-body">
              {c.criterion}
              {c.detail ? <span className="text-secondary">. {c.detail}</span> : null}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
