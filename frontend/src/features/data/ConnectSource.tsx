"use client";

import React, { useEffect, useId, useRef, useState } from "react";
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
  sourceDisplayName,
  toPayload,
} from "@/lib/connectors";
import { datasetLabel, useHealth } from "@/lib/workspace";
import { ConnectorCatalog } from "@/features/data/ConnectorCatalog";
import { SampleDataButton } from "@/components/SampleDataButton";
import { FileSource, useFileSource } from "@/features/data/FileSource";
import { SourceForm } from "@/features/data/SourceForm";
import { StepProgress } from "@/features/data/StepProgress";
import { TableSelector, tableMeta } from "@/features/data/TableSelector";
import { FlowError, FlowPhase, useSourceFlow } from "@/features/data/useSourceFlow";

const PASTED_DISPLAY_NAME = "Pasted data";

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

export interface ReimportTarget {
  connectorId: ConnectorId;
  sourceId: string;
  label: string;
  values: Record<string, FieldValue>;
  selectedTables: string[];
  nonce: number;
}

function prefilledValues(def: ConnectorDef, saved: Record<string, FieldValue> | undefined): Values {
  const values = initialValues(def);
  if (!saved) return values;
  for (const f of def.fields) {
    const v = saved[f.key];
    if (!f.secret && v !== undefined && typeof v === typeof values[f.key]) values[f.key] = v;
  }
  return values;
}

function formValues(def: ConnectorDef, values: Values): Record<string, FieldValue> {
  return Object.fromEntries(
    def.fields.filter((f) => !f.secret && isFieldVisible(f, values) && values[f.key] !== "").map((f) => [f.key, values[f.key]])
  );
}

function SourceSetup({
  def,
  reimport,
  onBack,
  onImported,
}: {
  def: ConnectorDef;
  reimport?: ReimportTarget;
  onBack: () => void;
  onImported: (snapshotId: string) => void;
}) {
  const isFile = def.family === "uploaded_file";
  const probe = needsProbe(def);
  const nameId = useId();
  const maxBytes = useHealth().data?.limits?.max_uploaded_file_bytes;
  const file = useFileSource(maxBytes);
  const [values, setValues] = useState<Values>(() => prefilledValues(def, reimport?.values));
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
    if (isFile) {
      setTableName(!file.draft.nameEdited && reimport?.selectedTables.length === 1 ? reimport.selectedTables[0] : file.draft.name.trim());
    }
    void flow.connect(isFile ? () => file.build(def) : () => Promise.resolve(toPayload(def, values)), probe, reimport?.selectedTables);
  }

  function submitImport(e: React.FormEvent) {
    e.preventDefault();
    if (flow.busy || importCount === 0 || renameError) return;
    void flow.importTables(single ? tableName.trim() : undefined, {
      source_id: reimport?.sourceId,
      form: { connector_id: def.id, values: isFile ? {} : formValues(def, values) },
      display_name: isFile ? (file.draft.file?.name ?? PASTED_DISPLAY_NAME) : sourceDisplayName(def, values),
    });
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

      {reimport && flow.phase !== "done" && (
        <Notice tone="info" title={`New version of ${reimport.label}`}>
          {isFile
            ? "Choose the updated file. It's saved as the next version of this dataset."
            : def.fields.some((f) => f.secret)
              ? "The saved settings are filled in. Enter the credentials again; they're never stored."
              : "The saved settings are filled in. Check them, then read the tables again."}
        </Notice>
      )}

      <StepProgress steps={steps} current={current} busy={flow.busy} status={statusText(flow.phase, flow.error, probe, flow.tables.length)} />

      {flow.error && (
        <FlowErrorNotice
          error={flow.error}
          fieldKeys={new Set(inDetails && !isFile ? def.fields.filter((f) => isFieldVisible(f, values)).map((f) => f.key) : [])}
        />
      )}

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
            <button type="button" className={btn.ghost} onClick={flow.back}>
              {flow.phase === "importing" ? "Cancel import" : "Edit details"}
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
    </div>
  );
}

export function ConnectSource({
  reimport,
  onImported,
  onCancelReimport,
  offerSample,
  onClose,
}: {
  reimport: ReimportTarget | null;
  onImported: (snapshotId: string) => void;
  onCancelReimport: () => void;
  offerSample: boolean;
  onClose?: () => void;
}) {
  const [selectedId, setSelectedId] = useState<ConnectorId | null>(null);
  const sectionRef = useRef<HTMLElement>(null);
  const activeId = reimport?.connectorId ?? selectedId;
  const def = activeId ? CONNECTOR_BY_ID.get(activeId) : undefined;

  useEffect(() => {
    if (!reimport) return;
    sectionRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
    sectionRef.current?.querySelector<HTMLElement>("form input:not([type=hidden]), form textarea, input:not([type=hidden]), button")?.focus({ preventScroll: true });
  }, [reimport]);

  return (
    <section ref={sectionRef} aria-label="Add data" className={cx(cardCls, "min-w-0 scroll-mt-4 p-5 sm:p-6")}>
      {def ? (
        <SourceSetup
          key={reimport ? `re-${reimport.nonce}` : def.id}
          def={def}
          reimport={reimport ?? undefined}
          onBack={() => {
            setSelectedId(null);
            onCancelReimport();
          }}
          onImported={onImported}
        />
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <h2 className="font-display text-lg font-semibold leading-snug tracking-[-0.01em] text-ink">Add data</h2>
              <p className="mt-1 text-xs text-secondary">
                Pick a source. Each import becomes a read-only snapshot, and credentials are only used for that import.
              </p>
            </div>
            {onClose && (
              <button type="button" className={btn.ghost} onClick={onClose}>
                Close
              </button>
            )}
          </div>
          {offerSample && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 bg-panel px-3.5 py-3">
              <span className="min-w-0 flex-1 text-[13px] text-body">No data handy? Import a small shop dataset with orders, refunds, fees and payouts.</span>
              <SampleDataButton onImported={(snap) => onImported(snap.snapshot_id)} />
            </div>
          )}
          <ConnectorCatalog onSelect={setSelectedId} />
        </div>
      )}
    </section>
  );
}
