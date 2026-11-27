"use client";

import React, { useId, useState } from "react";
import { Icon } from "@/components/Icon";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, errorMessage } from "@/lib/api/client";
import { Badge, Notice, btnSm, cardCls, cx, inputCls, sectionTitleCls } from "@/components/ui";
import { providerLabel } from "@/lib/workspace";
import type { OpsSwitchListOut, OpsSwitchOut, SetOpsSwitchRequest } from "@/lib/api/types";

const KEY = ["ops-switches"];
const REASON_CHARS = 500;
const RUNS_SWITCH = "runs";

export const providerSwitchId = (providerId: string) => `provider:${providerId}`;

function switchLabel(id: string): string {
  return id === RUNS_SWITCH ? "New questions" : providerLabel(id.replace(/^provider:/, ""));
}

export function useOpsSwitches() {
  return useQuery({ queryKey: KEY, queryFn: () => apiFetch<OpsSwitchListOut>("/ops/switches") });
}

export function PauseControl({ switchId, data }: { switchId: string; data: OpsSwitchListOut }) {
  const qc = useQueryClient();
  const id = useId();
  const [editing, setEditing] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const current = data.switches.find((s) => s.switch_id === switchId && s.paused);
  const label = switchLabel(switchId);
  const mutation = useMutation({
    mutationFn: (body: SetOpsSwitchRequest) =>
      apiFetch<OpsSwitchOut>(`/ops/switches/${encodeURIComponent(switchId)}`, { method: "PUT", body: JSON.stringify(body) }),
    onSuccess: () => {
      setReason("");
      setEditing(false);
      qc.invalidateQueries({ queryKey: KEY });
    },
    onError: (err) => setError(errorMessage(err, "Couldn't change the switch.")),
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!reason.trim()) {
      setError("Give a reason; it is kept in the change history.");
      return;
    }
    mutation.mutate({ paused: !current, reason: reason.trim() });
  }

  if (!editing) {
    return (
      <span className="inline-flex flex-wrap items-center justify-end gap-2">
        {current && (
          <Badge tone="warn" dot>
            Paused
          </Badge>
        )}
        <button type="button" className={btnSm.secondary} onClick={() => setEditing(true)}>
          {current ? "Resume" : "Pause"}
          <span className="sr-only"> {label}</span>
        </button>
      </span>
    );
  }

  return (
    <form onSubmit={submit} className="flex w-full min-w-[240px] flex-col gap-2 text-left">
      <label htmlFor={`${id}-reason`} className="text-sm font-medium text-ink">
        {current ? `Why resume ${label.toLowerCase()}?` : `Why pause ${label.toLowerCase()}?`}
      </label>
      <input
        id={`${id}-reason`}
        className={inputCls}
        value={reason}
        maxLength={REASON_CHARS}
        placeholder="e.g. Provider outage, incident 42"
        onChange={(e) => setReason(e.target.value)}
        // eslint-disable-next-line jsx-a11y/no-autofocus -- the field appears in response to the user's click
        autoFocus
      />
      <span className="flex flex-wrap gap-2">
        <button type="submit" className={btnSm.primary} disabled={mutation.isPending}>
          {current ? "Resume" : "Pause"}
        </button>
        <button
          type="button"
          className={btnSm.secondary}
          onClick={() => {
            setEditing(false);
            setError(null);
          }}
        >
          Cancel
        </button>
      </span>
      {error && <p className="text-xs text-err">{error}</p>}
    </form>
  );
}

export function IncidentControls() {
  const id = useId();
  const query = useOpsSwitches();
  const runsPaused = query.data?.switches.find((s) => s.switch_id === RUNS_SWITCH && s.paused);

  return (
    <section aria-labelledby={`${id}-title`} className={cx(cardCls, "p-5 sm:p-6")}>
      <details open={Boolean(runsPaused) || undefined} className="group">
        <summary className="flex min-h-[40px] cursor-pointer list-none items-center justify-between gap-3">
          <span>
            <h2 id={`${id}-title`} className={sectionTitleCls}>
              Incident controls
            </h2>
            <span className="mt-1 block text-xs text-secondary">
              Pause new questions during an incident. Runs in progress continue and every change is recorded.
            </span>
          </span>
          <span className="flex shrink-0 items-center gap-2">
            {runsPaused && (
              <Badge tone="warn" dot>
                Questions paused
              </Badge>
            )}
            <Icon name="chevron-down" className="h-4 w-4 text-secondary transition-transform group-open:rotate-180" />
          </span>
        </summary>
        <div className="mt-4 space-y-4">
          {query.isLoading && <div className="h-16 animate-pulse rounded bg-panel" aria-hidden="true" />}
          {query.data?.available.includes(RUNS_SWITCH) && (
            <div className="flex flex-wrap items-start justify-between gap-3 rounded-lg border border-border px-3.5 py-3 text-sm">
              <div className="min-w-0">
                <span className="font-medium text-ink">Pause new questions</span>
                <p className="mt-0.5 text-xs text-secondary">
                  {runsPaused ? runsPaused.reason : "New questions and pin refreshes are refused with your reason until you resume."}
                </p>
              </div>
              <PauseControl switchId={RUNS_SWITCH} data={query.data} />
            </div>
          )}
          {query.data && query.data.history.length > 0 && (
            <details className="text-xs text-secondary">
              <summary className="cursor-pointer text-sm text-ink">Recent changes</summary>
              <ul className="mt-2 space-y-1">
                {query.data.history.slice(0, 10).map((h, i) => (
                  <li key={`${h.switch_id}-${h.changed_at}-${i}`}>
                    {new Date(h.changed_at).toLocaleString()} · {switchLabel(h.switch_id)} {h.paused ? "paused" : "resumed"}: {h.reason}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {query.error && (
            <Notice tone="err" title="Couldn't load incident controls">
              {errorMessage(query.error)}
            </Notice>
          )}
        </div>
      </details>
    </section>
  );
}
