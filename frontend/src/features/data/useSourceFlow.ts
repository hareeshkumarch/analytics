"use client";

import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError, errorHint, errorMessage } from "@/lib/api/client";
import { DiscoveredTable, ExtractResult, discoverSource, extractSource, probeSource } from "@/lib/api/connectors";
import { semanticsKey } from "@/lib/api/semantics";
import type { SourcePayload } from "@/lib/connectors";
import { useWorkspace } from "@/lib/workspace";

export type FlowPhase = "details" | "probing" | "discovering" | "tables" | "importing" | "done";

export interface FlowError {
  title: string;
  hint: string | null;
  fields: Record<string, string>;
  code: string | null;
  requestId: string | null;
}

function flowError(title: string, hint: string | null = null): FlowError {
  return { title, hint, fields: {}, code: null, requestId: null };
}

function toFlowError(err: unknown, fallback: string): FlowError {
  if (!(err instanceof ApiError)) return flowError(errorMessage(err, fallback), errorHint(err));
  const fields = Object.fromEntries(Object.entries(err.fieldErrors).map(([key, msg]) => [key.replace(/^credentials\./, ""), msg]));
  return { title: errorMessage(err, fallback), hint: errorHint(err), fields, code: err.code, requestId: err.requestId };
}

export function useSourceFlow(onImported: (result: ExtractResult) => void) {
  const ws = useWorkspace();
  const qc = useQueryClient();
  const [phase, setPhase] = useState<FlowPhase>("details");
  const [tables, setTables] = useState<DiscoveredTable[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState<FlowError | null>(null);
  const [result, setResult] = useState<ExtractResult | null>(null);
  const payloadRef = useRef<SourcePayload | null>(null);
  const requestRef = useRef<AbortController | null>(null);

  useEffect(() => () => requestRef.current?.abort(), []);

  function start(next: FlowPhase): AbortController | null {
    if (requestRef.current) return null;
    const ctrl = new AbortController();
    requestRef.current = ctrl;
    setError(null);
    setPhase(next);
    return ctrl;
  }

  function finish(ctrl: AbortController) {
    if (requestRef.current === ctrl) requestRef.current = null;
  }

  async function connect(build: () => Promise<SourcePayload>, probe: boolean) {
    const ctrl = start(probe ? "probing" : "discovering");
    if (!ctrl) return;
    try {
      const payload = await build();
      if (probe) {
        const res = await probeSource(ws.id, payload, ctrl.signal);
        if (ctrl.signal.aborted) return;
        if (!res.ok) {
          setError(flowError("The connection test failed.", [res.message, res.remediation].filter(Boolean).join(" ") || null));
          setPhase("details");
          return;
        }
        setPhase("discovering");
      }
      const found = await discoverSource(ws.id, payload, ctrl.signal);
      if (ctrl.signal.aborted) return;
      if (!found.length) {
        setError(flowError("No tables were found.", "Check that this source has data the user or token can read."));
        setPhase("details");
        return;
      }
      payloadRef.current = payload;
      setTables(found);
      setSelected(found.map((t) => t.name));
      setPhase("tables");
    } catch (err) {
      if (ctrl.signal.aborted) return;
      setError(toFlowError(err, "Couldn't read this source."));
      setPhase("details");
    } finally {
      finish(ctrl);
    }
  }

  async function importTables(tableName?: string) {
    const base = payloadRef.current;
    if (!base || !selected.length) return;
    const ctrl = start("importing");
    if (!ctrl) return;
    const payload = tableName ? { ...base, config: { ...base.config, table_name: tableName } } : base;
    try {
      const selectedTables = tableName ? [tableName] : selected;
      const res = await extractSource(ws.id, { ...payload, selected_tables: selectedTables }, ctrl.signal);
      payloadRef.current = null;
      qc.setQueryData(semanticsKey(ws.id, res.snapshot.snapshot_id), res.semantics);
      await qc.invalidateQueries({ queryKey: ["snapshots", ws.id] });
      setTables([]);
      setSelected([]);
      setResult(res);
      setPhase("done");
      onImported(res);
    } catch (err) {
      if (ctrl.signal.aborted) return;
      setError(toFlowError(err, "The import failed."));
      setPhase("tables");
    } finally {
      finish(ctrl);
    }
  }

  function back() {
    requestRef.current?.abort();
    requestRef.current = null;
    payloadRef.current = null;
    setTables([]);
    setSelected([]);
    setError(null);
    setResult(null);
    setPhase("details");
  }

  const busy = phase === "probing" || phase === "discovering" || phase === "importing";
  return { phase, busy, tables, selected, setSelected, error, result, connect, importTables, back };
}
