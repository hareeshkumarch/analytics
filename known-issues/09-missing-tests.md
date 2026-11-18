# 09 - Missing asynchronous run and frontend coverage

The suite includes provider/tool-loop tests, an inline end-to-end API slice, run idempotency and
basic persistence checks. It still does not exercise the background run lifecycle that the UI uses,
or verify the UI submitting a run and rendering its terminal answer. Add the cases below with
FastAPI `TestClient` and a fake gateway, building on `backend/tests/test_end_to_end_slice.py`.

## Already added
`backend/tests/test_tool_loop.py` covers the tool loop, budgets, wire formats, evidence and the
query compiler changes (see 02). `test_end_to_end_slice.py` exercises an inline run, and
`test_api_and_infra.py` covers basic idempotency and event replay. The asynchronous lifecycle,
reconnect, cancellation, and rendered frontend answer cases below remain open.

## Backend — API run lifecycle (`backend/tests/test_run_lifecycle.py`, new)
1. **Background dispatch**: `POST /runs {background_async: true}` → poll `GET /runs/{id}` until
   terminal → `completed`. The events list contains `run.status running` and `run.finished completed`.
2. **Live SSE**: open `GET /runs/{id}/events` with `client.stream(...)` *before* the run finishes.
   You receive `run.finished` and the stream closes by itself. Sequences are strictly increasing
   with no duplicates.
3. **Resume**: reconnect with `Last-Event-ID: 3` → only events with sequence > 3 are sent.
4. **No key**: no provider keys → run ends `failed`, the `run.finished` payload has `error`, and there
   is no `answer.delta` containing an error message.
5. **Exception in orchestrator**: monkeypatch `execute_local` to raise → `run.finished {failed}`,
   and `GET /runs/{id}` says `failed`.
6. **Cancel while running**: fake gateway that sleeps 1 s. Cancel after the first `task.status running`.
   Final status is `cancelled`, no events follow the cancelled `run.finished`, and a second
   queued run only starts after the first worker thread returns.
7. **Cancel while queued**: two runs with the semaphore at 1. Cancel the second → it never emits
   `run.status running`.
8. **Saved key is used**: `PUT /settings/provider-key` then `POST /providers/openai/test` → `ok`
   (with a fake gateway).
9. **Routing GET/PUT**: GET returns v1; PUT `{expected_version: 1, preset: "cost"}` → GET returns v2/cost.

## Backend — routing service
10. Transport error (gateway raises `httpx.ReadTimeout`) → falls back to the next model, and the
    semaphore count is back to 0 afterwards.
11. `ledger.reserve` raises `BudgetExhaustedError` → semaphore released, and a half-open probe is released.
12. `ledger.settle` raises after a successful call → the provider is called **once** (no fallback).

## Frontend
13. Unit (Vitest + Testing Library; add as a dev dependency): `RichSelect` keyboard (↓↓ Enter
    selects the 3rd option; Esc closes and keeps the value; typing "q" jumps to "Quality-oriented";
    disabled options are skipped).
14. Playwright (`frontend/tests/workspace.spec.ts` covers only navigation today):
    - Ask: submit a quick run against a stubbed API (`page.route`), see the status go to Completed
      and the answer rendered.
    - Settings: change the preset → `PUT /routing` sent with the right `expected_version`.
    - Mobile project (`devices['iPhone 13']`): nav row scrolls, and the selector dropdown is fully in viewport.
