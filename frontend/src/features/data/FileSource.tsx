"use client";

import React, { useId, useMemo, useState } from "react";
import { btn, cx, inputCls } from "@/components/ui";
import {
  ConnectorDef,
  DetectedFormat,
  FILE_ACCEPT,
  SINGLE_TABLE_FORMATS,
  SourcePayload,
  datasetName,
  detectFileFormat,
  sanitizeDatasetName,
  toPayload,
} from "@/lib/connectors";
import { uploadFile } from "@/lib/api/connectors";
import { formatBytes, useWorkspace } from "@/lib/workspace";

interface FileDraft {
  mode: "file" | "paste";
  file: File | null;
  text: string;
  name: string;
  nameEdited: boolean;
}

const EMPTY: FileDraft = { mode: "file", file: null, text: "", name: "", nameEdited: false };
const PASTED_CSV: DetectedFormat = { family: "uploaded_file", format: "csv", label: "CSV" };
const PASTED_NAME = "pasted_data";

function defaultNameForDraft(mode: "file" | "paste", file: File | null, text: string): string {
  if (mode === "file") return file ? sanitizeDatasetName(file.name) : "";
  return text.trim() ? PASTED_NAME : "";
}

export function useFileSource(maxBytes: number | undefined) {
  const ws = useWorkspace();
  const [draft, setDraft] = useState<FileDraft>(EMPTY);
  const activeFile = draft.mode === "file" ? draft.file : null;
  const activeText = draft.mode === "paste" ? draft.text : "";
  const detected = activeFile ? detectFileFormat(activeFile.name) : activeText.trim() ? PASTED_CSV : null;
  const size = useMemo(
    () => (activeFile ? activeFile.size : new TextEncoder().encode(activeText).length),
    [activeFile, activeText],
  );

  const errors: { file?: string; name?: string } = {};
  if (draft.mode === "file" && !draft.file) errors.file = "Choose a file to upload.";
  else if (draft.mode === "paste" && !draft.text.trim()) errors.file = "Paste CSV text to import.";
  else if (!detected) errors.file = "This file type isn't supported. Use CSV, TSV, Excel, JSON, NDJSON, Parquet, Arrow, SQLite or DuckDB.";
  else if (maxBytes && size > maxBytes) errors.file = `This file is ${formatBytes(size)}. The limit is ${formatBytes(maxBytes)}.`;
  const nameError = datasetName(draft.name.trim());
  if (nameError) errors.name = nameError;

  function setMode(mode: "file" | "paste") {
    setDraft((d) => ({
      ...d,
      mode,
      name: d.nameEdited ? d.name : defaultNameForDraft(mode, d.file, d.text),
    }));
  }

  function chooseFile(file: File | null) {
    setDraft((d) => ({
      ...d,
      mode: "file",
      file,
      name: d.nameEdited ? d.name : defaultNameForDraft("file", file, d.text),
    }));
  }

  function setText(text: string) {
    setDraft((d) => ({
      ...d,
      mode: "paste",
      text,
      name: d.nameEdited ? d.name : defaultNameForDraft("paste", d.file, text),
    }));
  }

  function setName(name: string) {
    setDraft((d) => ({ ...d, name, nameEdited: true }));
  }

  async function build(def: ConnectorDef): Promise<SourcePayload> {
    if (!detected) throw new Error(draft.mode === "paste" ? "Paste CSV text first." : "Choose a supported file first.");
    const blob = activeFile ?? new Blob([activeText], { type: "text/csv" });
    const { upload_id } = await uploadFile(ws.id, blob);
    const base = toPayload(def, {});
    const format = detected.family === "uploaded_file" ? { format: detected.format } : {};
    return { ...base, family: detected.family, config: { ...base.config, ...format, upload_id, table_name: draft.name.trim() } };
  }

  return {
    draft,
    detected,
    errors,
    singleTable: Boolean(detected && SINGLE_TABLE_FORMATS.has(detected.format)),
    setMode,
    chooseFile,
    setText,
    setName,
    clear: () => setDraft(EMPTY),
    build,
  };
}

type FileSourceState = ReturnType<typeof useFileSource>;

function nameHelp(source: FileSourceState): string {
  if (!source.detected || source.singleTable) return "Becomes the table name.";
  if (source.detected.family === "uploaded_file") return "Becomes the table name; each sheet or nested list adds a suffix.";
  return "Names this source. Its tables keep their own names.";
}

export function FileSource({
  source,
  showErrors,
  disabled,
  maxBytes,
}: {
  source: FileSourceState;
  showErrors: boolean;
  disabled: boolean;
  maxBytes: number | undefined;
}) {
  const id = useId();
  const [dragging, setDragging] = useState(false);
  const { draft, detected, errors } = source;
  const hasPaste = Boolean(draft.text.trim());
  const pasteSizeError = draft.mode === "paste" && (showErrors || hasPaste) ? errors.file : undefined;
  const fileError = draft.mode === "file" && (showErrors || draft.file) ? errors.file : undefined;
  const nameError = showErrors || draft.nameEdited ? errors.name : undefined;

  return (
    <div className="space-y-4">
      <div role="tablist" aria-label="Import method" className="inline-flex rounded-lg border border-border bg-panel p-0.5">
        {(
          [
            { key: "file", label: draft.file ? "Upload file · ready" : "Upload file" },
            { key: "paste", label: hasPaste ? "Paste CSV · ready" : "Paste CSV" },
          ] as const
        ).map((tab) => {
          const active = draft.mode === tab.key;
          return (
            <button
              key={tab.key}
              type="button"
              role="tab"
              aria-selected={active}
              disabled={disabled}
              onClick={() => source.setMode(tab.key)}
              className={cx(
                "min-h-[30px] rounded-md px-3 text-xs font-medium transition-colors",
                active ? "bg-surface text-ink shadow-sm" : "text-secondary hover:text-ink",
              )}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      {draft.mode === "file" ? (
        <div role="tabpanel" aria-label="Upload file" className="space-y-1.5">
          <input
            id={`${id}-file`}
            type="file"
            accept={FILE_ACCEPT}
            disabled={disabled}
            className="peer sr-only"
            aria-invalid={Boolean(fileError)}
            aria-describedby={cx(`${id}-file-help`, fileError && `${id}-file-err`)}
            onChange={(e) => {
              source.chooseFile(e.target.files?.[0] ?? null);
              e.target.value = "";
            }}
          />
          <label
            htmlFor={`${id}-file`}
            onDragOver={(e) => {
              e.preventDefault();
              if (!disabled) setDragging(true);
            }}
            onDragLeave={(e) => {
              if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false);
            }}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              if (!disabled && e.dataTransfer.files[0]) source.chooseFile(e.dataTransfer.files[0]);
            }}
            className={cx(
              "flex min-h-[128px] flex-col items-center justify-center gap-1 rounded-lg border border-dashed bg-panel px-4 py-6 text-center transition-colors peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-accent",
              dragging ? "border-accent" : fileError ? "border-err" : "border-border-strong hover:border-accent",
              disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer",
            )}
          >
            <span className="break-all text-base font-medium text-ink">{draft.file ? draft.file.name : "Choose a file or drop it here"}</span>
            <span id={`${id}-file-help`} className="text-xs text-secondary">
              {draft.file
                ? `${detected?.label ?? "Unknown type"} · ${formatBytes(draft.file.size)}`
                : maxBytes
                  ? `Up to ${formatBytes(maxBytes)}`
                  : "One file per import"}
            </span>
          </label>
          {fileError && (
            <p id={`${id}-file-err`} role="alert" className="text-xs text-err">
              {fileError}
            </p>
          )}
          {draft.file && (
            <button type="button" className={btn.ghost} disabled={disabled} onClick={() => source.chooseFile(null)}>
              Remove file
            </button>
          )}
        </div>
      ) : (
        <div role="tabpanel" aria-label="Paste CSV" className="space-y-1.5">
          <div className="flex items-center justify-between gap-2">
            <label htmlFor={`${id}-paste`} className="block text-sm font-medium text-body">
              Paste CSV text
            </label>
            {hasPaste && (
              <button type="button" disabled={disabled} onClick={() => source.setText("")} className="text-xs text-secondary underline-offset-4 hover:text-ink hover:underline">
                Clear text
              </button>
            )}
          </div>
          <textarea
            id={`${id}-paste`}
            rows={5}
            value={draft.text}
            disabled={disabled}
            spellCheck={false}
            placeholder={"order_id,amount\nA-001,19.90"}
            aria-invalid={Boolean(pasteSizeError)}
            aria-describedby={pasteSizeError ? `${id}-paste-err` : undefined}
            onChange={(e) => source.setText(e.target.value)}
            className={cx(inputCls, "min-h-[112px] py-2.5 font-mono text-[16px] sm:text-xs", pasteSizeError && "border-err")}
          />
          {pasteSizeError && (
            <p id={`${id}-paste-err`} role="alert" className="text-xs text-err">
              {pasteSizeError}
            </p>
          )}
        </div>
      )}

      <div className="space-y-1.5">
        <label htmlFor={`${id}-name`} className="block text-sm font-medium text-body">
          Dataset name
        </label>
        <input
          id={`${id}-name`}
          value={draft.name}
          disabled={disabled}
          spellCheck={false}
          autoComplete="off"
          placeholder="orders"
          aria-invalid={Boolean(nameError)}
          aria-describedby={cx(`${id}-name-help`, nameError && `${id}-name-err`)}
          onChange={(e) => source.setName(e.target.value)}
          className={cx(inputCls, "font-mono text-[16px] sm:text-sm", nameError && "border-err")}
        />
        <p id={`${id}-name-help`} className="text-xs text-secondary">
          {nameHelp(source)}
        </p>
        {nameError && (
          <p id={`${id}-name-err`} className="text-xs text-err">
            {nameError}
          </p>
        )}
      </div>
    </div>
  );
}
