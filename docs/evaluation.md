# Evaluation & Verification Matrix

## Synthetic INR Acceptance Fixture (`fixtures/inr_acceptance/`)
- **Paid orders**: 1,000 + 2,000 + 3,000 + 500 = 6,500.00 INR
- **Cancelled order excluded**: 1,500.00 INR
- **Quarantined duplicate**: 2,000.00 INR (deduplicated via `source_key`)
- **Refunds**: 500 + 3,000 = 3,500.00 INR -> **Net sales**: 3,000.00 INR
- **Fees**: 100.00 INR -> **Eligible settlement**: 2,900.00 INR
- **Confirmed payouts**: 2,600.00 INR + **In-transit**: 300.00 INR -> **Conclusion**: `timing_difference` (not lost money)

## Connector & Provider Verification Status
- **Covered by unit tests (`backend/tests/`)**: CSV, TSV, XLSX, JSON/NDJSON, Parquet and Arrow parsers; SQLite and DuckDB file imports; file-from-link downloads (redirects, token scoping, size cap, HTML rejection); REST pagination modes; LocalArtifactStore; QueryIR + AST validation; Decimal INR reconciliation; provider contract adapters; CircuitBreakerManager; AtomicBudgetLedger; restricted tool manifest; DeterministicSupervisor DAG/reducer; the tool loop; CSV/HTML/PDF/DOCX exports; FastAPI `/api/v1` endpoints; CredentialCipher; persistence across restarts.
- **Not yet executed after the latest changes**: no build, test or migration has been run since the redesign and connector round. See the checklist in `known-issues/12`.
- **Requires Docker Compose / live credentials**: Live PostgreSQL/MySQL/Redis integration, migration 0002 on Postgres, Next.js `npm run build` + Playwright E2E, live connector imports, and live LLM API calls.
