"use client";

import React, { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, errorMessage } from "@/lib/api/client";
import { Badge, Notice, btnSm, cardCls, cx, inputCls } from "@/components/ui";
import { providerLabel } from "@/lib/workspace";
import type { OpsSwitchListOut, OpsSwitchOut, SetOpsSwitchRequest } from "@/lib/api/types";

const KEY = ["ops-switches"];

function switchLabel(id: string): string {
  return id === "runs" ? "New questions" : providerLabel(id.replace(/^provider:/, ""));
}

export function OperatorSwitches() {
  const qc = useQueryClient();
  const id = useId();
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const query = useQuery({ queryKey: KEY, queryFn: () => apiFetch<OpsSwitchListOut>("/ops/switches") });
  const mutation = useMutation({
    mutationFn: ({ switchId, body }: { switchId: string; body: SetOpsSwitchRequest }) =>
      apiFetch<OpsSwitchOut>(`/ops/switches/${encodeURIComponent(switchId)}`, { method: "PUT", body: JSON.stringify(body) }),
    onSuccess: () => {
      setReason("");
      qc.invalidateQueries({ queryKey: KEY });
    },
    onError: (err) => setError(errorMessage(err, "Couldn't change the switch.")),
  });
  const paused = new Map((query.data?.switches ?? []).filter((s) => s.paused).map((s) => [s.switch_id, s]));
  const toggle = (switchId: string) => {
    setError(null);
    if (!reason.trim()) {
      setError("Give a reason first; it is kept in the change history.");
      return;
    }
    mutation.mutate({ switchId, body: { paused: !paused.has(switchId), reason: reason.trim() } });
  };

  return (
    <section aria-labelledby={`${id}-title`} className={cx(cardCls, "space-y-4 p-5 sm:p-6")}>
      <div>
        <h2 id={`${id}-title`} className="font-display text-lg font-semibold leading-snug tracking-[-0.01em] text-ink">
          Operator controls
        </h2>
        <p className="mt-1 text-xs text-secondary">
          Pause new questions, or stop routing to a provider during an incident. Runs in progress continue, nothing is deleted, and every
          change is recorded.
        </p>
      </div>
      <label htmlFor={`${id}-reason`} className="block text-[13px] font-medium text-ink">
        Reason for the change
      </label>
      <input
        id={`${id}-reason`}
        className={inputCls}
        value={reason}
        maxLength={500}
        placeholder="e.g. Provider outage, incident 42"
        onChange={(e) => setReason(e.target.value)}
      />
      {query.isLoading && <div className="h-24 animate-pulse bg-panel" aria-hidden="true" />}
      {query.data && (
        <ul className="divide-y divide-border border border-border">
          {query.data.available.map((switchId) => {
            const current = paused.get(switchId);
            return (
              <li key={switchId} className="flex flex-wrap items-center justify-between gap-3 px-3.5 py-2.5 text-[13px]">
                <div className="min-w-0">
                  <span className="font-medium text-ink">{switchLabel(switchId)}</span>
                  {current && <p className="mt-0.5 text-xs text-secondary">{current.reason}</p>}
                </div>
                <div className="flex items-center gap-2">
                  <Badge tone={current ? "warn" : "ok"} dot>
                    {current ? "Paused" : "Active"}
                  </Badge>
                  <button type="button" className={btnSm.secondary} disabled={mutation.isPending} onClick={() => toggle(switchId)}>
                    {current ? "Resume" : "Pause"}
                    <span className="sr-only"> {switchLabel(switchId)}</span>
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {query.data && query.data.history.length > 0 && (
        <details className="text-xs text-secondary">
          <summary className="cursor-pointer text-[13px] text-ink">Recent changes</summary>
          <ul className="mt-2 space-y-1">
            {query.data.history.slice(0, 10).map((h, i) => (
              <li key={`${h.switch_id}-${h.changed_at}-${i}`}>
                {new Date(h.changed_at).toLocaleString()} · {switchLabel(h.switch_id)} {h.paused ? "paused" : "resumed"}: {h.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
      {error && <Notice tone="err" title={error} />}
      {query.error && <Notice tone="err" title="Couldn't load operator controls">{errorMessage(query.error)}</Notice>}
    </section>
  );
}
