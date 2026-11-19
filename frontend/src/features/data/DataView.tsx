"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch, errorHint, errorMessage } from "@/lib/api/client";
import {
  Badge,
  EmptyState,
  FilterChips,
  Notice,
  SearchInput,
  Spinner,
  btn,
  cardCls,
  cx,
  inputCls,
} from "@/components/ui";
import { ConnectorLogo } from "@/components/ConnectorLogo";
import { RichSelect } from "@/components/RichSelect";
import {
  CATEGORY_LABELS,
  CONNECTORS,
  FILE_ACCEPT,
  bytesToBase64,
  detectFileFormat,
  familyLabel,
  fieldErrors,
  fileToBase64,
  initialValues,
  isFieldVisible,
  missingRequired,
  needsProbe,
  sanitizeDatasetName,
  toPayload,
} from "@/lib/connectors";
import type { ConnectorCategory, ConnectorDef, Values } from "@/lib/connectors";
import {
  datasetLabel,
  formatBytes,
  relativeTime,
  useSnapshots,
  useWorkspace,
} from "@/lib/workspace";
import type { SnapshotSummary } from "@/lib/workspace";

interface DiscoveredTable {
  name: string;
  estimated_rows: number | null;
  columns: { name: string; arrow_type: string; is_leading_zero_id: boolean }[];
}

interface ProbeResult {
  ok: boolean;
  family: string;
  message: string;
  capabilities: { transactional_snapshot: boolean; resumable_cursor: boolean };
  details: Record<string, unknown>;
}

interface MetricDef {
  metric_id: string;
  display_name: string;
  expression: string;
  grain: string;
  unit: string;
  currency: string | null;
}

interface Semantics {
  semantic_version_id: string;
  snapshot_id: string;
  version: number;
  approved: boolean;
  metrics: MetricDef[];
}

interface ProfileColumn {
  column_name: string;
  null_count: number;
  distinct_count: number;
  duplicate_count: number;
  min_value: unknown;
  max_value: unknown;
  is_sampled: boolean;
}

interface PreviewResponse {
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
}

interface DiagnosticsResponse {
  available: boolean;
  diagnostics: {
    extraction_started_at?: string;
    extraction_completed_at?: string;
    is_complete?: boolean;
    is_sampled?: boolean;
    warnings?: string[];
    source_summary?: Record<string, unknown>;
  } | null;
  consistency_level: string | null;
  total_rows: number;
  total_normalized_bytes: number;
  tables: { name: string; row_count: number; normalized_bytes: number }[];
}

const MAX_UPLOAD_BYTES = 40_000_000;

function cellValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

type FlowStep = "form" | "probed" | "discovered" | "imported";

export function DataView() {
  const ws = useWorkspace();
  const queryClient = useQueryClient();
  const searchRef = useRef<HTMLInputElement | null>(null);

  // ---- Datasets -----------------------------------------------------------
  const snapshotsQuery = useSnapshots();
  const snapshots = snapshotsQuery.data?.snapshots ?? [];
  const [selectedSnapshotId, setSelectedSnapshotId] = useState<string | null>(
    null,
  );
  const selectedSnapshot: SnapshotSummary | null =
    snapshots.find((s) => s.snapshot_id === selectedSnapshotId) ??
    snapshots[0] ??
    null;

  // ---- Connector catalog --------------------------------------------------
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<"all" | ConnectorCategory>("all");
  const [selected, setSelected] = useState<ConnectorDef | null>(null);
  const [values, setValues] = useState<Values>({});
  const [step, setStep] = useState<FlowStep>("form");
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [tables, setTables] = useState<DiscoveredTable[]>([]);
  const [checkedTables, setCheckedTables] = useState<string[]>([]);
  const [formError, setFormError] = useState<string | null>(null);
  const [formHint, setFormHint] = useState<string | null>(null);
  const [busy, setBusy] = useState<"probe" | "discover" | "import" | null>(
    null,
  );
  const [importedSnapshotId, setImportedSnapshotId] = useState<string | null>(
    null,
  );

  // File upload (the "file" connector has no form fields of its own).
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [pasteText, setPasteText] = useState("");
  const [datasetName, setDatasetName] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return CONNECTORS.filter((c) => {
      if (category !== "all" && c.category !== category) return false;
      if (!q) return true;
      const hay = [
        c.label,
        c.sublabel,
        c.category,
        CATEGORY_LABELS[c.category],
        ...(c.keywords ?? []),
      ]
        .join(" ")
        .toLowerCase();
      return hay.includes(q);
    });
  }, [query, category]);

  const categoryOptions = useMemo(
    () => [
      { value: "all" as const, label: "All", count: CONNECTORS.length },
      ...(Object.keys(CATEGORY_LABELS) as ConnectorCategory[]).map((c) => ({
        value: c,
        label: CATEGORY_LABELS[c],
        count: CONNECTORS.filter((d) => d.category === c).length,
      })),
    ],
    [],
  );

  function pickConnector(def: ConnectorDef) {
    setSelected(def);
    setValues(initialValues(def));
    setStep("form");
    setProbe(null);
    setTables([]);
    setCheckedTables([]);
    setFormError(null);
    setFormHint(null);
    setImportedSnapshotId(null);
    setUploadFile(null);
    setPasteText("");
    setDatasetName("");
  }

  function changeSource() {
    setSelected(null);
    setStep("form");
    setProbe(null);
    setTables([]);
    setCheckedTables([]);
    setFormError(null);
    setFormHint(null);
    setImportedSnapshotId(null);
  }

  const errors = selected ? fieldErrors(selected, values) : {};
  const missing = selected ? missingRequired(selected, values) : [];
  const formBlocked =
    !selected ||
    missing.length > 0 ||
    Object.keys(errors).length > 0 ||
    busy !== null;

  async function runProbe() {
    if (!selected) return;
    const payload = toPayload(selected, values);
    setBusy("probe");
    setFormError(null);
    setFormHint(null);
    try {
      const res = await apiFetch<ProbeResult>(ws.path("/connectors/probe"), {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setProbe(res);
      if (res.ok) setStep("probed");
      else setFormError(res.message || "The connection test failed.");
    } catch (err) {
      setFormError(errorMessage(err));
      setFormHint(errorHint(err));
    } finally {
      setBusy(null);
    }
  }

  async function runDiscover() {
    if (!selected) return;
    const payload = toPayload(selected, values);
    setBusy("discover");
    setFormError(null);
    setFormHint(null);
    try {
      const res = await apiFetch<{ tables: DiscoveredTable[] }>(
        ws.path("/connectors/discover"),
        {
          method: "POST",
          body: JSON.stringify(payload),
        },
      );
      setTables(res.tables ?? []);
      setCheckedTables((res.tables ?? []).map((t) => t.name));
      setStep("discovered");
      if ((res.tables ?? []).length === 0)
        setFormError("No tables were found for this source.");
    } catch (err) {
      setFormError(errorMessage(err));
      setFormHint(errorHint(err));
    } finally {
      setBusy(null);
    }
  }

  async function runImport() {
    if (!selected) return;
    const payload = toPayload(selected, values);
    setBusy("import");
    setFormError(null);
    setFormHint(null);
    try {
      const res = await apiFetch<{ snapshot_id: string }>(
        ws.path("/connectors/extract"),
        {
          method: "POST",
          body: JSON.stringify({
            ...payload,
            selected_tables: checkedTables,
            selected_columns: {},
            version: 1,
          }),
        },
      );
      await queryClient.invalidateQueries({ queryKey: ["snapshots", ws.id] });
      setImportedSnapshotId(res.snapshot_id);
      setSelectedSnapshotId(res.snapshot_id);
      setStep("imported");
    } catch (err) {
      setFormError(errorMessage(err));
      setFormHint(errorHint(err));
    } finally {
      setBusy(null);
    }
  }

  async function runFileImport() {
    setBusy("import");
    setFormError(null);
    setFormHint(null);
    try {
      let rawBase64: string;
      let format: string;
      let family: string;
      if (uploadFile) {
        if (uploadFile.size > MAX_UPLOAD_BYTES) {
          throw new Error("That file is over the 40 MB upload limit.");
        }
        const detected = detectFileFormat(uploadFile.name);
        if (!detected)
          throw new Error(
            "That file type isn't supported. Use CSV, TSV, Excel, JSON, Parquet, Arrow, SQLite or DuckDB.",
          );
        rawBase64 = await fileToBase64(uploadFile);
        format = detected.format;
        family = detected.family;
      } else if (pasteText.trim()) {
        rawBase64 = bytesToBase64(new TextEncoder().encode(pasteText));
        format = "csv";
        family = "uploaded_file";
      } else {
        throw new Error("Choose a file or paste CSV text first.");
      }
      const tableName = datasetName.trim()
        ? sanitizeDatasetName(datasetName.trim())
        : "uploaded_table";
      const res = await apiFetch<{ snapshot_id: string }>(
        ws.path("/connectors/extract"),
        {
          method: "POST",
          body: JSON.stringify({
            family,
            config: { raw_base64: rawBase64, format, table_name: tableName },
            credentials: {},
            selected_tables: [],
            selected_columns: {},
            version: 1,
          }),
        },
      );
      await queryClient.invalidateQueries({ queryKey: ["snapshots", ws.id] });
      setImportedSnapshotId(res.snapshot_id);
      setSelectedSnapshotId(res.snapshot_id);
      setStep("imported");
    } catch (err) {
      setFormError(errorMessage(err));
      setFormHint(errorHint(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-8">
      <header className="space-y-1.5">
        <h1 className="font-display text-[26px] font-bold leading-tight tracking-[-0.01em]">
          Data
        </h1>
        <p className="max-w-2xl text-[14px] leading-relaxed text-secondary">
          Bring in a dataset, check what was imported, and approve its
          definitions. Only approved datasets can be asked about.
        </p>
      </header>

      {/* ---- Connect a source ---- */}
      <section
        aria-labelledby="connect-heading"
        className={`${cardCls} p-4 sm:p-5`}
      >
        <h2 id="connect-heading" className="font-display text-[17px] font-bold">
          Connect a source
        </h2>

        {!selected ? (
          <div className="mt-4 space-y-4">
            <SearchInput
              value={query}
              onChange={setQuery}
              placeholder="Search connectors"
              label="Search connectors"
              className="max-w-md"
            />
            <FilterChips<"all" | ConnectorCategory>
              label="Connector category"
              options={categoryOptions}
              value={category}
              onChange={setCategory}
            />
            {filtered.length === 0 ? (
              <EmptyState
                title="No connectors match"
                body="Try a different search or category."
                action={
                  <button
                    type="button"
                    className={btn.secondary}
                    onClick={() => {
                      setQuery("");
                      setCategory("all");
                    }}
                  >
                    Clear search
                  </button>
                }
              />
            ) : (
              <ul
                aria-label="Connectors"
                className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3"
              >
                {filtered.map((c) => (
                  <li key={c.id}>
                    <button
                      type="button"
                      onClick={() => pickConnector(c)}
                      className="flex h-full w-full items-start gap-3 rounded-none border border-border bg-canvas px-3.5 py-3 text-left transition-colors hover:border-plum"
                    >
                      <ConnectorLogo id={c.id} />
                      <span className="min-w-0">
                        <span className="block truncate text-[13.5px] font-medium">
                          {c.label}
                        </span>
                        <span className="mt-0.5 block text-[12px] leading-snug text-secondary">
                          {c.sublabel}
                        </span>
                        <span className="mt-1 block text-[11px] text-secondary">
                          {c.authSummary}
                        </span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : (
          <ConnectorForm
            def={selected}
            values={values}
            onChange={setValues}
            errors={errors}
            missing={missing}
            step={step}
            probe={probe}
            tables={tables}
            checkedTables={checkedTables}
            onToggleTable={(name, on) =>
              setCheckedTables((prev) =>
                on ? [...prev, name] : prev.filter((t) => t !== name),
              )
            }
            formError={formError}
            formHint={formHint}
            busy={busy}
            importedSnapshotId={importedSnapshotId}
            uploadFile={uploadFile}
            onPickFile={(f) => {
              setUploadFile(f);
              if (f) setDatasetName(sanitizeDatasetName(f.name));
            }}
            pasteText={pasteText}
            onPasteText={setPasteText}
            datasetName={datasetName}
            onDatasetName={setDatasetName}
            onProbe={runProbe}
            onDiscover={runDiscover}
            onImport={runImport}
            onFileImport={runFileImport}
            onChangeSource={changeSource}
            formBlocked={formBlocked}
          />
        )}
      </section>

      {/* ---- Datasets ---- */}
      <section aria-labelledby="datasets-heading" className="space-y-3">
        <h2
          id="datasets-heading"
          className="font-display text-[17px] font-bold"
        >
          Your datasets
        </h2>
        {snapshotsQuery.isLoading ? (
          <div className="flex items-center gap-2 text-[13px] text-secondary">
            <Spinner /> Loading datasets…
          </div>
        ) : snapshotsQuery.isError ? (
          <Notice tone="err" title="Couldn't load your datasets">
            {errorMessage(snapshotsQuery.error)}
          </Notice>
        ) : snapshots.length === 0 ? (
          <EmptyState
            title="No datasets yet"
            body="Connect a source above to import your first dataset. It will appear here, ready to review and approve."
          />
        ) : (
          <div className="space-y-4">
            <ul
              aria-label="Datasets"
              className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3"
            >
              {snapshots.map((s) => {
                const active = selectedSnapshot?.snapshot_id === s.snapshot_id;
                return (
                  <li key={s.snapshot_id}>
                    <button
                      type="button"
                      onClick={() => setSelectedSnapshotId(s.snapshot_id)}
                      aria-current={active ? "true" : undefined}
                      className={cx(
                        "flex h-full w-full flex-col gap-1 rounded-none border px-3.5 py-3 text-left transition-colors",
                        active
                          ? "border-plum bg-surface ring-[3px] ring-plum-soft"
                          : "border-border bg-surface hover:border-plum",
                      )}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="truncate text-[13.5px] font-medium">
                          {datasetLabel(s)}
                        </span>
                        <Badge tone={s.approved ? "ok" : "warn"}>
                          {s.approved ? "Approved" : "Needs approval"}
                        </Badge>
                      </span>
                      <span className="text-[12px] text-secondary">
                        {familyLabel(s.source_family)} ·{" "}
                        {s.total_rows.toLocaleString()} rows ·{" "}
                        {relativeTime(s.created_at)}
                      </span>
                      <span className="text-[11.5px] text-secondary">
                        {s.tables.length}{" "}
                        {s.tables.length === 1 ? "table" : "tables"} ·{" "}
                        {s.source_id}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
            {selectedSnapshot && (
              <SnapshotDetail
                key={selectedSnapshot.snapshot_id}
                snapshot={selectedSnapshot}
              />
            )}
          </div>
        )}
      </section>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Connector form: fields, probe → discover → import                 */
/* ------------------------------------------------------------------ */

function ConnectorForm(props: {
  def: ConnectorDef;
  values: Values;
  onChange: (v: Values) => void;
  errors: Record<string, string>;
  missing: string[];
  step: FlowStep;
  probe: ProbeResult | null;
  tables: DiscoveredTable[];
  checkedTables: string[];
  onToggleTable: (name: string, on: boolean) => void;
  formError: string | null;
  formHint: string | null;
  busy: "probe" | "discover" | "import" | null;
  importedSnapshotId: string | null;
  uploadFile: File | null;
  onPickFile: (f: File | null) => void;
  pasteText: string;
  onPasteText: (v: string) => void;
  datasetName: string;
  onDatasetName: (v: string) => void;
  onProbe: () => void;
  onDiscover: () => void;
  onImport: () => void;
  onFileImport: () => void;
  onChangeSource: () => void;
  formBlocked: boolean;
}) {
  const { def } = props;
  const isFile = def.id === "file";
  const showProbe = !isFile && needsProbe(def);
  const canDiscover = !isFile && (showProbe ? props.step === "probed" : true);

  return (
    <div className="mt-4 space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <ConnectorLogo id={def.id} size="md" />
          <div>
            <h3 className="font-display text-[16px] font-bold">{def.label}</h3>
            <p className="text-[13px] text-secondary">{def.sublabel}</p>
            {def.basedOn && (
              <p className="text-[12px] text-secondary">
                Based on {def.basedOn}
              </p>
            )}
          </div>
        </div>
        <button
          type="button"
          className={btn.ghost}
          onClick={props.onChangeSource}
        >
          Change source
        </button>
      </div>

      {def.note && (
        <p className="max-w-2xl text-[13px] leading-relaxed text-secondary">
          {def.note}
        </p>
      )}

      {isFile ? (
        <div className="max-w-xl space-y-3">
          <div>
            <label
              htmlFor="data-file"
              className="mb-1.5 block text-[12.5px] font-medium text-body"
            >
              File
            </label>
            <input
              id="data-file"
              type="file"
              accept={FILE_ACCEPT}
              onChange={(e) => props.onPickFile(e.target.files?.[0] ?? null)}
              className="block w-full text-[13px] text-body file:mr-3 file:min-h-[40px] file:border file:border-border-strong file:bg-surface file:px-3 file:text-[13px] file:text-ink"
            />
            <p className="mt-1.5 text-[11px] leading-snug text-secondary">
              CSV, TSV, Excel, JSON, Parquet, Arrow, SQLite or DuckDB. Max 40
              MB. The file is used for this import only.
            </p>
            {props.uploadFile && (
              <p className="mt-1 text-[12.5px] text-body">
                {props.uploadFile.name} · {formatBytes(props.uploadFile.size)}
              </p>
            )}
          </div>
          <div>
            <label
              htmlFor="data-paste"
              className="mb-1.5 block text-[12.5px] font-medium text-body"
            >
              Or paste CSV text
            </label>
            <textarea
              id="data-paste"
              rows={4}
              value={props.pasteText}
              onChange={(e) => props.onPasteText(e.target.value)}
              placeholder={"order_id,amount\n00101,120.50\n00102,84.00"}
              className={inputCls}
            />
          </div>
          <div>
            <label
              htmlFor="data-dataset-name"
              className="mb-1.5 block text-[12.5px] font-medium text-body"
            >
              Dataset name
            </label>
            <input
              id="data-dataset-name"
              type="text"
              value={props.datasetName}
              onChange={(e) => props.onDatasetName(e.target.value)}
              placeholder="orders"
              className={inputCls}
            />
          </div>
        </div>
      ) : (
        <div className="grid max-w-2xl gap-3 sm:grid-cols-2">
          {def.fields
            .filter((f) => isFieldVisible(f, props.values))
            .map((f) => {
              const value = props.values[f.key];
              const err = props.errors[f.key];
              const fieldId = `data-field-${f.key}`;
              const control =
                f.type === "select" && f.options ? (
                  <RichSelect
                    label={f.label}
                    value={typeof value === "string" ? value : ""}
                    options={f.options}
                    onChange={(v) =>
                      props.onChange({ ...props.values, [f.key]: v })
                    }
                    ariaLabel={f.label}
                  />
                ) : f.type === "checkbox" ? (
                  <label className="flex min-h-[44px] cursor-pointer items-center gap-2.5 text-[13.5px]">
                    <input
                      id={fieldId}
                      type="checkbox"
                      checked={value === true}
                      onChange={(e) =>
                        props.onChange({
                          ...props.values,
                          [f.key]: e.target.checked,
                        })
                      }
                      className="h-4 w-4 accent-plum"
                    />
                    {f.label}
                  </label>
                ) : f.type === "textarea" ? (
                  <textarea
                    id={fieldId}
                    rows={4}
                    value={typeof value === "string" ? value : ""}
                    onChange={(e) =>
                      props.onChange({
                        ...props.values,
                        [f.key]: e.target.value,
                      })
                    }
                    placeholder={f.placeholder}
                    className={inputCls}
                  />
                ) : (
                  <input
                    id={fieldId}
                    type={
                      f.type === "password"
                        ? "password"
                        : f.type === "number"
                          ? "number"
                          : f.type === "url"
                            ? "url"
                            : "text"
                    }
                    value={
                      typeof value === "string" || typeof value === "number"
                        ? String(value)
                        : ""
                    }
                    onChange={(e) =>
                      props.onChange({
                        ...props.values,
                        [f.key]: e.target.value,
                      })
                    }
                    placeholder={f.placeholder}
                    readOnly={f.readOnly}
                    autoComplete={f.secret ? "off" : undefined}
                    className={inputCls}
                  />
                );
              return (
                <div key={f.key} className={cx(f.half ? "" : "sm:col-span-2")}>
                  {f.type !== "select" && f.type !== "checkbox" && (
                    <label
                      htmlFor={fieldId}
                      className="mb-1.5 block text-[12.5px] font-medium text-body"
                    >
                      {f.label}
                      {f.required ? " *" : ""}
                    </label>
                  )}
                  {control}
                  {f.help && (
                    <p className="mt-1.5 text-[11px] leading-snug text-secondary">
                      {f.help}
                    </p>
                  )}
                  {err && (
                    <p role="alert" className="mt-1.5 text-[12px] text-err">
                      {err}
                    </p>
                  )}
                </div>
              );
            })}
        </div>
      )}

      {props.missing.length > 0 && props.step === "form" && (
        <p className="text-[12.5px] text-secondary">
          Fill in: {props.missing.join(", ")}.
        </p>
      )}

      {props.formError && (
        <Notice tone="err" title="That didn't work">
          {props.formError}
          {props.formHint && (
            <span className="block opacity-90">{props.formHint}</span>
          )}
        </Notice>
      )}

      {props.probe && props.probe.ok && props.step !== "imported" && (
        <Notice tone="ok" title="Connection looks good">
          {props.probe.message || "The source answered."}
        </Notice>
      )}

      {props.step === "discovered" && (
        <fieldset className="max-w-2xl">
          <legend className="mb-2 text-[12.5px] font-medium text-body">
            Tables to import
          </legend>
          <ul className="space-y-1.5">
            {props.tables.map((t) => (
              <li key={t.name}>
                <label className="flex cursor-pointer items-center gap-2.5 text-[13.5px]">
                  <input
                    type="checkbox"
                    checked={props.checkedTables.includes(t.name)}
                    onChange={(e) =>
                      props.onToggleTable(t.name, e.target.checked)
                    }
                    className="h-4 w-4 accent-plum"
                  />
                  <span className="font-medium">{t.name}</span>
                  <span className="text-secondary">
                    {t.estimated_rows != null
                      ? `${t.estimated_rows.toLocaleString()} rows · `
                      : ""}
                    {t.columns.length} columns
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </fieldset>
      )}

      {props.step === "imported" && props.importedSnapshotId && (
        <Notice tone="ok" title="Imported">
          Your dataset is ready below. Review its definitions and approve them
          to start asking questions.
        </Notice>
      )}

      <div className="flex flex-wrap gap-2">
        {isFile ? (
          <button
            type="button"
            className={btn.primary}
            disabled={props.busy !== null || props.step === "imported"}
            onClick={props.onFileImport}
          >
            {props.busy === "import" && <Spinner />} Import file
          </button>
        ) : (
          <>
            {showProbe && props.step === "form" && (
              <button
                type="button"
                className={btn.secondary}
                disabled={props.formBlocked}
                onClick={props.onProbe}
              >
                {props.busy === "probe" && <Spinner />} Test connection
              </button>
            )}
            {canDiscover &&
              props.step !== "discovered" &&
              props.step !== "imported" && (
                <button
                  type="button"
                  className={btn.secondary}
                  disabled={props.formBlocked || props.busy !== null}
                  onClick={props.onDiscover}
                >
                  {props.busy === "discover" && <Spinner />} Discover datasets
                </button>
              )}
            {props.step === "discovered" && (
              <button
                type="button"
                className={btn.primary}
                disabled={
                  props.checkedTables.length === 0 || props.busy !== null
                }
                onClick={props.onImport}
              >
                {props.busy === "import" && <Spinner />}
                Import {props.checkedTables.length}{" "}
                {props.checkedTables.length === 1 ? "table" : "tables"}
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Snapshot detail: preview, profile, diagnostics, definitions       */
/* ------------------------------------------------------------------ */

function SnapshotDetail({ snapshot }: { snapshot: SnapshotSummary }) {
  const ws = useWorkspace();
  const queryClient = useQueryClient();
  const [table, setTable] = useState<string | null>(null);
  const [approveError, setApproveError] = useState<string | null>(null);

  const activeTable = table ?? snapshot.tables[0] ?? "";

  useEffect(() => {
    setTable(null);
    setApproveError(null);
  }, [snapshot.snapshot_id]);

  const semanticsQuery = useQuery({
    queryKey: ["semantics", snapshot.snapshot_id],
    queryFn: () =>
      apiFetch<Semantics>(
        ws.path(`/semantics/${encodeURIComponent(snapshot.snapshot_id)}`),
      ),
  });

  const previewQuery = useQuery({
    queryKey: ["snapshot-preview", snapshot.snapshot_id, activeTable],
    queryFn: () =>
      apiFetch<PreviewResponse>(
        ws.path(
          `/snapshots/${encodeURIComponent(snapshot.snapshot_id)}/preview?table=${encodeURIComponent(activeTable)}&limit=100`,
        ),
      ),
    enabled: Boolean(activeTable),
  });

  const profileQuery = useQuery({
    queryKey: ["snapshot-profile", snapshot.snapshot_id, activeTable],
    queryFn: () =>
      apiFetch<{ columns: ProfileColumn[] }>(
        ws.path(
          `/snapshots/${encodeURIComponent(snapshot.snapshot_id)}/profile?table=${encodeURIComponent(activeTable)}`,
        ),
      ),
    enabled: Boolean(activeTable),
  });

  const diagnosticsQuery = useQuery({
    queryKey: ["snapshot-diagnostics", snapshot.snapshot_id],
    queryFn: () =>
      apiFetch<DiagnosticsResponse>(
        ws.path(
          `/snapshots/${encodeURIComponent(snapshot.snapshot_id)}/diagnostics`,
        ),
      ),
  });

  const approveMutation = useMutation({
    mutationFn: (expectedVersion: number) =>
      apiFetch<Semantics>(
        ws.path(
          `/semantics/${encodeURIComponent(snapshot.snapshot_id)}/approve`,
        ),
        {
          method: "POST",
          body: JSON.stringify({ expected_version: expectedVersion }),
        },
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["semantics", snapshot.snapshot_id],
      });
      await queryClient.invalidateQueries({ queryKey: ["snapshots", ws.id] });
    },
    onError: (err) => setApproveError(errorMessage(err)),
  });

  const sem = semanticsQuery.data;

  return (
    <div className={`${cardCls} space-y-6 p-4 sm:p-5`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-display text-[16px] font-bold">
            {datasetLabel(snapshot)}
          </h3>
          <p className="mt-0.5 text-[12.5px] text-secondary">
            {familyLabel(snapshot.source_family)} ·{" "}
            {snapshot.total_rows.toLocaleString()} rows ·{" "}
            {formatBytes(snapshot.total_normalized_bytes)} ·{" "}
            {relativeTime(snapshot.created_at)}
          </p>
        </div>
        <Badge tone={snapshot.approved ? "ok" : "warn"}>
          {snapshot.approved ? "Approved" : "Needs approval"}
        </Badge>
      </div>

      {snapshot.tables.length > 1 && (
        <div className="max-w-xs">
          <RichSelect
            label="Table"
            value={activeTable}
            options={snapshot.tables.map((t) => ({ value: t, label: t }))}
            onChange={setTable}
          />
        </div>
      )}

      {/* Preview */}
      <section aria-labelledby="preview-heading">
        <h4 id="preview-heading" className="mb-2 text-[13px] font-medium">
          Preview{activeTable ? ` — ${activeTable}` : ""}{" "}
          <span className="font-normal text-secondary">(first 100 rows)</span>
        </h4>
        {previewQuery.isLoading ? (
          <div className="flex items-center gap-2 text-[13px] text-secondary">
            <Spinner /> Loading preview…
          </div>
        ) : previewQuery.isError ? (
          <Notice tone="err">{errorMessage(previewQuery.error)}</Notice>
        ) : previewQuery.data ? (
          <div className="overflow-x-auto border border-border">
            <table className="w-full min-w-[480px] border-collapse text-[12.5px]">
              <thead>
                <tr className="bg-panel text-left">
                  {previewQuery.data.columns.map((c) => (
                    <th
                      key={c}
                      scope="col"
                      className="border-b border-border px-2.5 py-2 font-medium"
                    >
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {previewQuery.data.rows.map((row, i) => (
                  <tr
                    key={i}
                    className="border-b border-border last:border-b-0"
                  >
                    {previewQuery.data.columns.map((c) => (
                      <td
                        key={c}
                        className="max-w-[240px] truncate px-2.5 py-1.5"
                      >
                        {cellValue(row[c])}
                      </td>
                    ))}
                  </tr>
                ))}
                {previewQuery.data.rows.length === 0 && (
                  <tr>
                    <td
                      colSpan={previewQuery.data.columns.length}
                      className="px-2.5 py-3 text-secondary"
                    >
                      This table has no rows.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      {/* Profile */}
      <section aria-labelledby="profile-heading">
        <h4 id="profile-heading" className="mb-2 text-[13px] font-medium">
          Column profile
        </h4>
        {profileQuery.isLoading ? (
          <div className="flex items-center gap-2 text-[13px] text-secondary">
            <Spinner /> Loading profile…
          </div>
        ) : profileQuery.isError ? (
          <Notice tone="err">{errorMessage(profileQuery.error)}</Notice>
        ) : profileQuery.data ? (
          <div className="overflow-x-auto border border-border">
            <table className="w-full min-w-[480px] border-collapse text-[12.5px]">
              <thead>
                <tr className="bg-panel text-left">
                  <th
                    scope="col"
                    className="border-b border-border px-2.5 py-2 font-medium"
                  >
                    Column
                  </th>
                  <th
                    scope="col"
                    className="border-b border-border px-2.5 py-2 font-medium"
                  >
                    Nulls
                  </th>
                  <th
                    scope="col"
                    className="border-b border-border px-2.5 py-2 font-medium"
                  >
                    Distinct
                  </th>
                  <th
                    scope="col"
                    className="border-b border-border px-2.5 py-2 font-medium"
                  >
                    Duplicates
                  </th>
                  <th
                    scope="col"
                    className="border-b border-border px-2.5 py-2 font-medium"
                  >
                    Min
                  </th>
                  <th
                    scope="col"
                    className="border-b border-border px-2.5 py-2 font-medium"
                  >
                    Max
                  </th>
                </tr>
              </thead>
              <tbody>
                {profileQuery.data.columns.map((c) => (
                  <tr
                    key={c.column_name}
                    className="border-b border-border last:border-b-0"
                  >
                    <td className="px-2.5 py-1.5 font-medium">
                      {c.column_name}
                    </td>
                    <td className="px-2.5 py-1.5">{c.null_count}</td>
                    <td className="px-2.5 py-1.5">{c.distinct_count}</td>
                    <td className="px-2.5 py-1.5">{c.duplicate_count}</td>
                    <td className="max-w-[160px] truncate px-2.5 py-1.5">
                      {cellValue(c.min_value)}
                    </td>
                    <td className="max-w-[160px] truncate px-2.5 py-1.5">
                      {cellValue(c.max_value)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      {/* Diagnostics */}
      <section aria-labelledby="diagnostics-heading">
        <h4 id="diagnostics-heading" className="mb-2 text-[13px] font-medium">
          Import diagnostics
        </h4>
        {diagnosticsQuery.isLoading ? (
          <div className="flex items-center gap-2 text-[13px] text-secondary">
            <Spinner /> Loading diagnostics…
          </div>
        ) : diagnosticsQuery.isError ? (
          <Notice tone="err">{errorMessage(diagnosticsQuery.error)}</Notice>
        ) : diagnosticsQuery.data ? (
          <div className="space-y-1 text-[13px] leading-relaxed">
            <p>
              Consistency:{" "}
              <strong>{diagnosticsQuery.data.consistency_level ?? "—"}</strong>{" "}
              · {diagnosticsQuery.data.total_rows.toLocaleString()} rows ·{" "}
              {formatBytes(diagnosticsQuery.data.total_normalized_bytes)}
              {diagnosticsQuery.data.diagnostics?.is_sampled
                ? " · sampled"
                : ""}
            </p>
            {(diagnosticsQuery.data.diagnostics?.warnings ?? []).length > 0 && (
              <Notice tone="warn" title="Warnings">
                <ul className="list-disc pl-4">
                  {(diagnosticsQuery.data.diagnostics?.warnings ?? []).map(
                    (w, i) => (
                      <li key={i}>{w}</li>
                    ),
                  )}
                </ul>
              </Notice>
            )}
          </div>
        ) : null}
      </section>

      {/* Definitions */}
      <section aria-labelledby="definitions-heading">
        <h4 id="definitions-heading" className="mb-2 text-[13px] font-medium">
          Metric definitions
        </h4>
        {semanticsQuery.isLoading ? (
          <div className="flex items-center gap-2 text-[13px] text-secondary">
            <Spinner /> Loading definitions…
          </div>
        ) : semanticsQuery.isError ? (
          <Notice tone="err">{errorMessage(semanticsQuery.error)}</Notice>
        ) : sem ? (
          <div className="space-y-3">
            {sem.metrics.length === 0 ? (
              <p className="text-[13px] text-secondary">
                No metrics were proposed for this dataset.
              </p>
            ) : (
              <ul className="space-y-2">
                {sem.metrics.map((m) => (
                  <li
                    key={m.metric_id}
                    className="border border-border bg-canvas px-3 py-2.5"
                  >
                    <p className="text-[13.5px] font-medium">
                      {m.display_name}
                    </p>
                    <p className="mt-0.5 break-words font-mono text-[12px] text-secondary">
                      {m.expression}
                    </p>
                    <p className="mt-0.5 text-[12px] text-secondary">
                      Grain: {m.grain || "—"} · Unit: {m.unit || "—"}
                      {m.currency ? ` · Currency: ${m.currency}` : ""}
                    </p>
                  </li>
                ))}
              </ul>
            )}
            {approveError && (
              <Notice tone="err" title="Approval failed">
                {approveError}
              </Notice>
            )}
            {sem.approved ? (
              <Notice tone="ok" title="Approved">
                These definitions are approved. You can ask questions about this
                dataset.
              </Notice>
            ) : (
              <button
                type="button"
                className={btn.primary}
                disabled={approveMutation.isPending}
                onClick={() => approveMutation.mutate(sem.version)}
              >
                {approveMutation.isPending && <Spinner />}
                Approve definitions
              </button>
            )}
          </div>
        ) : null}
      </section>
    </div>
  );
}
