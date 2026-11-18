# 03 - Deep-mode review and replanning

## Current status

Implemented in the orchestration path. The evidence reviewer now returns a structured verdict with approval, bounded gaps, and contradictions. Rejected or failed reviews can trigger bounded remediation followed by a new reviewer step; the evidence check remains an independent guard. Runtime verification is pending.

## Remaining problems

- Structured reviewer decisions and remediation behavior still need end-to-end runtime coverage.

## Fix

1. Exercise structured reviewer approval, rejection, malformed output, and failure.
2. Verify reviewer-driven remediation, re-review, and maximum-replan behavior against the task budget.

## What to cover

- A reviewer that rejects once and then approves produces exactly one remediation plan and runs the new task once.
- Repeated rejection stops at `max_replans` without raising or looping forever.
- Replan accounting increases only for new tasks; already completed or superseded tasks are not rerun.
- A failed `t1_analyze` marks dependent tasks failed instead of running `t2_review` with missing findings; the run ends failed or partial.
- An approved review does not replan; a review with bounded gaps does.
