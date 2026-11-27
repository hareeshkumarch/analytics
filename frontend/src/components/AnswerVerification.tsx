"use client";

import React from "react";
import { Icon } from "@/components/Icon";
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
  unreviewed: "Not reviewed",
  not_applicable: "Not applicable",
};

export function AnswerVerification({ outcome, criteria }: { outcome: string | null; criteria: Criterion[] }) {
  const shown = outcome ? OUTCOMES[outcome] : undefined;
  if (!shown) return null;
  const passed = criteria.filter((c) => c.status === "verified").length;
  // Criteria nobody reviewed (Quick mode has no evidence review) are reported apart from the checks that ran.
  const unreviewed = criteria.filter((c) => c.status === "unreviewed").length;
  const checked = criteria.length - unreviewed;
  return (
    <section aria-label="Answer verification" className="mt-4">
      <details open={passed < checked || undefined} className="group text-sm">
        <summary className="flex min-h-[32px] cursor-pointer list-none items-center gap-2 text-secondary hover:text-ink [&::-webkit-details-marker]:hidden">
          <Badge tone={shown.tone} dot>
            {shown.label}
          </Badge>
          <span>
            {passed} of {checked} checks passed
            {unreviewed > 0 ? `, ${unreviewed} not reviewed` : ""}
          </span>
          <Icon name="chevron-down" className="h-3.5 w-3.5 transition-transform group-open:rotate-180" />
        </summary>
        <ul className="mt-1.5 space-y-1">
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
      </details>
    </section>
  );
}
