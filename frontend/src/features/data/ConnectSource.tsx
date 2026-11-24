"use client";

import React, { useId, useRef, useState } from "react";
import { ConnectorLogo } from "@/components/ConnectorLogo";
import { Badge, Details, Notice, Spinner, btn, cardCls, cx, inputCls } from "@/components/ui";
import {
  CONNECTOR_BY_ID,
  ConnectorDef,
  ConnectorId,
  FieldValue,
  Values,
  datasetName,
  fieldErrors,
  initialValues,
  isFieldVisible,
  missingRequired,
  needsProbe,
  toPayload,
} from "@/lib/connectors";
import { datasetLabel, useHealth } from "@/lib/workspace";
import { ConnectorCatalog } from "@/features/data/ConnectorCatalog";
import { FileSource, useFileSource } from "@/features/data/FileSource";
import { SourceForm } from "@/features/data/SourceForm";
import { StepProgress } from "@/features/data/StepProgress";
import { TableSelector, tableMeta } from "@/features/data/TableSelector";
import { FlowError, FlowPhase, useSourceFlow } from "@/features/data/useSourceFlow";

const STEP_BY_PHASE: Record<FlowPhase, string> = {
  details: "details",
  probing: "probe",
  discovering: "tables",
  tables: "tables",
  importing: "import",
  done: "approve",
};

function clearSecrets(def: ConnectorDef, values: Values): Values {
  const initial = initialValues(def);
  const next = { ...values };
  for (const f of def.fields) if (f.secret) next[f.key] = initial[f.key];
  return next;
}

function statusText(phase: FlowPhase, error: FlowError | null, probe: boolean, tableCount: number): string {
  if (error) return phase === "tables" ? "The import didn't finish." : "Couldn't read this source.";
  switch (phase) {
    case "details":
      return "";
    case "probing":
      return "Testing the connection…";
    case "discovering":
      return probe ? "Connected. Reading the list of tables…" : "Reading tables…";
    case "tables":
      return `Found ${tableCount} table${tableCount === 1 ? "" : "s"}. Choose what to import.`;
    case "importing":
      return "Importing a read-only snapshot. Large sources can take a minute.";
    case "done":
      return "Imported. Approve the definitions so Ask can use this data.";
  }
}

function FlowErrorNotice({ error, fieldKeys }: { error: FlowError; fieldKeys: Set<string> }) {
  const other = Object.entries(error.fields).filter(([key]) => !fieldKeys.has(key));
  const technical = [error.code && `code ${error.code}`, error.requestId && `request ${error.requestId}`].filter(Boolean).join(" · ");
  return (
    <Notice tone="err" title={error.title}>
      {error.hint && <p>{error.hint}</p>}
      {other.length > 0 && (
        <ul className="mt-1 list-disc pl-4">
          {other.map(([key, message]) => (
            <li key={key}>
              <span className="font-mono">{key}</span>: {message}
            </li>
          ))}
        </ul>
      )}
      {technical && (
        <Details summary="Technical details" className="mt-1">
          <p className="font-mono text-xs">{technical}</p>
        </Details>
      )}
    </Notice>
  );
}

function SourceSetup({ def, onBack, onImported }: { def: ConnectorDef; onBack: () => void; onImported: (snapshotId: string) => void }) {
  const isFile = def.family === "uploaded_file";
  const probe = needsProbe(def);
  const nameId = useId();
  const maxBytes = useHealth().data?.limits?.max_uploaded_file_bytes;
  const file = useFileSource(maxBytes);
  const [values, setValues] = useState<Values>(() => initialValues(def));
  const [showErrors, setShowErrors] = useState(false);
  const [tableName, setTableName] = useState("");
  const formRef = useRef<HTMLFormElement>(null);
  const flow = useSourceFlow((res) => {
    setValues((v) => clearSecrets(def, v));
    file.clear();
    setShowErrors(false);
    onImported(res.snapshot.snapshot_id);
  });

  const missing = isFile ? [] : missingRequired(def, values);
  const clientErrors: Record<string, string> = isFile
    ? {}
    : {
        ...Object.fromEntries(
          def.fields.filter((f) => missing.includes(f.label)).map((f) => [f.key, f.type === "checkbox" ? "Confirm this to continue." : "Required."])
        ),
        ...fieldErrors(def, values),
      };
  const shownErrors = { ...(flow.error?.fields ?? {}), ...(showErrors ? clientErrors : {}) };
  const blocked = isFile ? Object.keys(file.errors).length > 0 : Object.keys(clientErrors).length > 0;

  const single = isFile && file.singleTable;
  const renameError = single ? datasetName(tableName.trim()) : null;
  const importCount = single ? 1 : flow.selected.length;
  const inTables = flow.phase === "tables" || flow.phase === "importing";
  const inDetails = flow.phase === "details" || flow.phase === "probing" || flow.phase === "discovering";

  const steps = [
    { key: "details", label: isFile ? "Choose a file" : "Enter details" },
    ...(probe ? [{ key: "probe", label: "Test connection" }] : []),
    { key: "tables", label: "Choose tables" },
    { key: "import", label: "Import" },
    { key: "approve", label: "Approve definitions" },
  ];
  const current = steps.findIndex((s) => s.key === STEP_BY_PHASE[flow.phase]);

  function handleChange(key: string, value: FieldValue) {
    setValues((v) => ({ ...v, [key]: value }));
  }

  function submitDetails(e: React.FormEvent) {
    e.preventDefault();
    if (flow.busy) return;
    setShowErrors(true);
    if (blocked) {
      requestAnimationFrame(() => formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus());
      return;
    }
    if (isFile) setTableName(file.draft.name.trim());
    void flow.connect(isFile ? () => file.build(def) : () => Promise.resolve(toPayload(def, values)), probe);
  }

  function submitImport(e: React.FormEvent) {
    e.preventDefault();
    if (flow.busy || importCount === 0 || renameError) return;
    void flow.importTables(single ? tableName.trim() : undefined);
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <ConnectorLogo id={def.id} size="md" />
          <div className="min-w-0">
            <h2 className="font-display text-lg font-semibold leading-snug tracking-[-0.01em] text-ink">{def.label}</h2>
            <p className="mt-0.5 text-xs text-secondary">{def.sublabel}</p>
          </div>
        </div>
        <button type="button" className={btn.secondary} onClick={onBack} disabled={flow.phase === "importing"}>
          Change source
        </button>
      </div>

      <div className="space-y-2">
        <div className="flex flex-wrap gap-1.5">
          <Badge>{def.authSummary}</Badge>
          {def.basedOn && <Badge>via {def.basedOn}</Badge>}
        </div>
        {def.note && <p className="text-[13px] leading-relaxed text-body">{def.note}</p>}
      </div>

      <StepProgress steps={steps} current={current} busy={flow.busy} status={statusText(flow.phase, flow.error, probe, flow.tables.length)} />

      {inDetails && (
        <form ref={formRef} noValidate onSubmit={submitDetails} className="space-y-4">
          {isFile ? (
            <FileSource source={file} showErrors={showErrors} disabled={flow.busy} maxBytes={maxBytes} />
          ) : (
            <SourceForm def={def} values={values} errors={shownErrors} disabled={flow.busy} onChange={handleChange} />
          )}
          {showErrors && missing.length > 0 && <p className="text-xs text-err">Still needed: {missing.join(", ")}.</p>}
          <div className="flex flex-wrap items-center gap-2">
            <button type="submit" className={btn.primary} disabled={flow.busy}>
              {flow.busy && <Spinner />}
              {flow.phase === "probing" ? "Testing…" : flow.phase === "discovering" ? "Reading…" : probe ? "Test connection" : isFile ? "Read file" : "Read tables"}
            </button>
            {flow.busy && (
              <button type="button" className={btn.ghost} onClick={flow.back}>
                Cancel
              </button>
            )}
          </div>
        </form>
      )}

      {inTables && (
        <form noValidate onSubmit={submitImport} className="space-y-4">
          {single ? (
            <div className="space-y-1.5">
              <label htmlFor={nameId} className="block text-[12.5px] font-medium text-body">
                Table name
              </label>
              <input
                id={nameId}
                value={tableName}
                disabled={flow.busy}
                spellCheck={false}
                autoComplete="off"
                aria-invalid={Boolean(renameError)}
                aria-describedby={cx(`${nameId}-help`, renameError && `${nameId}-err`)}
                onChange={(e) => setTableName(e.target.value)}
                className={cx(inputCls, "font-mono text-[13px]", renameError && "border-err")}
              />
              <p id={`${nameId}-help`} className="text-xs text-secondary">
                {flow.tables[0] ? `${tableMeta(flow.tables[0])}. ` : ""}Renaming here doesn&apos;t upload the file again.
              </p>
              {renameError && (
                <p id={`${nameId}-err`} className="text-xs text-err">
                  {renameError}
                </p>
              )}
            </div>
          ) : (
            <TableSelector tables={flow.tables} selected={flow.selected} onChange={flow.setSelected} disabled={flow.busy} />
          )}
          <div className="flex flex-wrap items-center gap-2">
            <button type="submit" className={btn.primary} disabled={flow.busy || importCount === 0 || Boolean(renameError)}>
              {flow.phase === "importing" && <Spinner />}
              {flow.phase === "importing" ? "Importing…" : `Import ${importCount} table${importCount === 1 ? "" : "s"}`}
            </button>
            <button type="button" className={btn.ghost} onClick={flow.back} disabled={flow.busy}>
              Edit details
            </button>
          </div>
        </form>
      )}

      {flow.phase === "done" && flow.result && (
        <Notice
          tone="ok"
          title={`Imported ${datasetLabel(flow.result.snapshot)} as version ${flow.result.snapshot.version}`}
          action={
            <button type="button" className={btn.secondary} onClick={flow.back}>
              Import again
            </button>
          }
        >
          {flow.result.snapshot.total_rows.toLocaleString()} rows in {flow.result.snapshot.tables.length} table
          {flow.result.snapshot.tables.length === 1 ? "" : "s"}. {def.fields.some((f) => f.secret) ? "Credentials were cleared from this form. " : ""}
          Approve the proposed definitions under Datasets so Ask can use this data.
        </Notice>
      )}

      {flow.error && (
        <FlowErrorNotice
          error={flow.error}
          fieldKeys={new Set(inDetails && !isFile ? def.fields.filter((f) => isFieldVisible(f, values)).map((f) => f.key) : [])}
        />
      )}
    </div>
  );
}

export function ConnectSource({ onImported }: { onImported: (snapshotId: string) => void }) {
  const [selectedId, setSelectedId] = useState<ConnectorId | null>(null);
  const def = selectedId ? CONNECTOR_BY_ID.get(selectedId) : undefined;
  return (
    <section aria-label="Add data" className={cx(cardCls, "min-w-0 p-5 sm:p-6")}>
      {def ? (
        <SourceSetup key={def.id} def={def} onBack={() => setSelectedId(null)} onImported={onImported} />
      ) : (
        <div className="space-y-4">
          <div>
            <h2 className="font-display text-lg font-semibold leading-snug tracking-[-0.01em] text-ink">Add data</h2>
            <p className="mt-1 text-xs text-secondary">
              Pick a source. Each import becomes a read-only snapshot, and credentials are only used for that import.
            </p>
          </div>
          <ConnectorCatalog onSelect={setSelectedId} />
        </div>
      )}
    </section>
  );
}
