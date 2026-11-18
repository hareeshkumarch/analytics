# Architecture Overview

## Layered design
- **Domain (`backend/app/domain/`)**: Pure Python immutable value objects (`DataLimits`, `RunBudgetLimits`, `QueryIR`, `PlanDAG`, `SnapshotManifest`, `ModelProfile`, `RunEvent`). No imports from FastAPI, SQLAlchemy or provider SDKs.
- **Application (`backend/app/application/`)**: Narrow ports (`ConnectorDiscovery`, `SnapshotExtractor`, `ArtifactStore`, `QueryCompiler`, `QueryExecutor`, `ModelGateway`, `BudgetLedger`, `RunRepository`, `EventLog`, `Clock`) and services (ingestion helpers, profiling, reconciliation, `RoutingService`, `AtomicBudgetLedger`, `ExportService`, the analytics toolbox and `InvestigationOrchestrator`).
- **Adapters (`backend/app/adapters/`)**:
  - Registries for 7 file parsers (CSV, TSV, XLSX, JSON, NDJSON, Parquet, Arrow), 10 connector families (see `docs/connectors.md`) and 7 LLM providers.
  - A deterministic QueryIR-to-DuckDB compiler.
  - The in-process `DeterministicSupervisor` and the restricted tool-manifest helper.
  - SQLAlchemy 2.0 / Alembic persistence.
- **Composition root (`backend/app/bootstrap/container.py`)**: Wires the YAML configs (`providers.yaml`, `models.yaml`, `routing.yaml`) and the concrete adapters.
- **Frontend (`frontend/`)**: Next.js App Router. Thin route files render feature views from `src/features/`; shared UI primitives live in `src/components/ui.tsx`; the API client, SSE stream, connector catalog and workspace hooks live in `src/lib/`. See `docs/design.md`.

Runs execute inside the API process (see `docs/orchestration.md` and `known-issues/06`). Current open work is tracked in `known-issues/11` and `known-issues/12`.
