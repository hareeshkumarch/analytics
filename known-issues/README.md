# Known issues and remaining work

Current source status, last updated 2026-11-18. The recent worker integration has not yet been exercised against a live PostgreSQL/Redis Compose stack.

## Start here

**[12](12-open-issues-reasons-and-fixes.md) is the current list** of every fix and improvement still needed, each with its reason and how to fix it, plus a suggested order and the verification checklist. [11](11-open-fixes-and-audit.md) keeps the audit history and the status of each original audit item.

Top priorities from 12:

1. **Commit the frontend lockfile (#1).** CI's `npm ci` fails without it.
2. **Run the verification checklist (#4).** Nothing recent has been executed.
3. **Add lint, type-check and Playwright to CI (#43).**
4. **Load the saved routing policy at run start (#10)** so the cost limit applies after a restart.
5. **Stop silent memory fallbacks on database errors (#9).** Writes are lost on restart.

## Topic files

| # | Current status | Area |
|---|---|---|
| [02](02-specialists-do-not-query-data.md) | Implemented in source. Before the latest round every specialist step failed on an undefined `bridge`; that is fixed but not yet run against real providers | Backend / UI |
| [03](03-replanning-never-triggers.md) | Structured review and re-review are implemented; runtime verification remains | Backend |
| [05](05-python-dependency-lock.md) | Open: no reproducible Python lock file (unused packages have been removed, so the lock is now smaller) | Build |
| [06](06-celery-worker-and-dispatcher.md) | Implemented: Compose uses Celery for persisted background runs; outbox and live-stack validation remain | Backend / Infra |
| [07](07-in-memory-state.md) | Partly fixed: repositories are wired; evidence, budget totals, idempotency and live fan-out are still process-local | Backend |
| [08](08-frontend-gaps.md) | Out of date (12 #46): describes the UI before the redesign. Connector warnings and runtime checks remain | Frontend / Backend |
| [09](09-missing-tests.md) | Open: no end-to-end run test; CI has no lint or Playwright (12 #43) | Tests |
| [10](10-solid-refactors.md) | Open: structural refactors, notably splitting `main.py` (12 I8) | All |
| [11](11-open-fixes-and-audit.md) | Audit history and status table | All |
| [12](12-open-issues-reasons-and-fixes.md) | **Current list of fixes and improvements** | All |
| [13](13-deep-agent-guidelines-gap-analysis.md) | Gap analysis against the Deep Agent Engineering Guidelines v2 (tracker #15) | All |
| [14](14-failure-matrix.md) | Failure-mode test matrix for guidelines §34 (#28) | Tests |

## Verification still required

Run these in an environment with the project dependencies installed, then bring up the stack and do a real provider-backed run. Source inspection is not runtime verification.

```bash
cd frontend && npm install && npm run typecheck && npm run build && npx playwright test
```

```bash
cd backend && pip install -e ".[dev]" && pytest -v
```
