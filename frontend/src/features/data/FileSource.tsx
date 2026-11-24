"use client";

import React, { useId, useMemo, useState } from "react";
import { btn, cx, inputCls } from "@/components/ui";
import {
  ConnectorDef,
  DetectedFormat,
  FILE_ACCEPT,
  SINGLE_TABLE_FORMATS,
  SourcePayload,
  bytesToBase64,
  datasetName,
  detectFileFormat,
  fileToBase64,
  sanitizeDatasetName,
  toPayload,
} from "@/lib/connectors";
import { formatBytes } from "@/lib/workspace";

interface FileDraft {
  file: File | null;
  text: string;
  name: string;
  nameEdited: boolean;
}

const EMPTY: FileDraft = { file: null, text: "", name: "", nameEdited: false };
const PASTED_CSV: DetectedFormat = { family: "uploaded_file", format: "csv", label: "CSV" };
const PASTED_NAME = "pasted_data";

export function useFileSource(maxBytes: number | undefined) {
  const [draft, setDraft] = useState<FileDraft>(EMPTY);
  const detected = draft.file ? detectFileFormat(draft.file.name) : draft.text.trim() ? PASTED_CSV : null;
  const size = useMemo(() => (draft.file ? draft.file.size : new TextEncoder().encode(draft.text).length), [draft.file, draft.text]);

  const errors: { file?: string; name?: string } = {};
  if (!draft.file && !draft.text.trim()) errors.file = "Choose a file or paste CSV text.";
  else if (!detected) errors.file = "This file type isn't supported. Use CSV, TSV, Excel, JSON, NDJSON, Parquet, Arrow, SQLite or DuckDB.";
  else if (maxBytes && size > maxBytes) errors.file = `This file is ${formatBytes(size)}. The limit is ${formatBytes(maxBytes)}.`;
  const nameError = datasetName(draft.name.trim());
  if (nameError) errors.name = nameError;

  function chooseFile(file: File | null) {
    setDraft((d) => ({ ...d, file, text: "", name: d.nameEdited ? d.name : file ? sanitizeDatasetName(file.name) : "" }));
  }

  function setText(text: string) {
    setDraft((d) => ({ ...d, file: null, text, name: d.nameEdited ? d.name : text.trim() ? PASTED_NAME : "" }));
  }

  function setName(name: string) {
    setDraft((d) => ({ ...d, name, nameEdited: true }));
  }

  async function build(def: ConnectorDef): Promise<SourcePayload> {
    if (!detected) throw new Error("Choose a supported file first.");
    const raw = draft.file ? await fileToBase64(draft.file) : bytesToBase64(new TextEncoder().encode(draft.text));
    const base = toPayload(def, {});
    const format = detected.family === "uploaded_file" ? { format: detected.format } : {};
    return { ...base, family: detected.family, config: { ...base.config, ...format, raw_base64: raw, table_name: draft.name.trim() } };
  }

  return {
    draft,
    detected,
    errors,
    singleTable: Boolean(detected && SINGLE_TABLE_FORMATS.has(detected.format)),
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
  const fileError = showErrors || draft.file ? errors.file : undefined;
  const nameError = showErrors || draft.nameEdited ? errors.name : undefined;

  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
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
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            if (!disabled && e.dataTransfer.files[0]) source.chooseFile(e.dataTransfer.files[0]);
          }}
          className={cx(
            "flex min-h-[128px] flex-col items-center justify-center gap-1 border border-dashed bg-panel px-4 py-6 text-center transition-colors peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-plum",
            dragging ? "border-plum" : fileError ? "border-err" : "border-border-strong hover:border-plum",
            disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer"
          )}
        >
          <span className="break-all text-[13.5px] font-medium text-ink">{draft.file ? draft.file.name : "Choose a file or drop it here"}</span>
          <span id={`${id}-file-help`} className="text-xs text-secondary">
            {draft.file
              ? `${detected?.label ?? "Unknown type"} · ${formatBytes(draft.file.size)}`
              : maxBytes
                ? `Up to ${formatBytes(maxBytes)}`
                : "One file per import"}
          </span>
        </label>
        {fileError && (
          <p id={`${id}-file-err`} className="text-xs text-err">
            {fileError}
          </p>
        )}
        {draft.file && (
          <button type="button" className={btn.ghost} disabled={disabled} onClick={() => source.chooseFile(null)}>
            Remove file
          </button>
        )}
      </div>

      <div className="space-y-1.5">
        <label htmlFor={`${id}-paste`} className="block text-[12.5px] font-medium text-body">
          Or paste CSV text
        </label>
        <textarea
          id={`${id}-paste`}
          rows={4}
          value={draft.text}
          disabled={disabled}
          spellCheck={false}
          placeholder={"order_id,amount\nA-001,19.90"}
          onChange={(e) => source.setText(e.target.value)}
          className={cx(inputCls, "min-h-[96px] py-2.5 font-mono text-xs")}
        />
      </div>

      <div className="space-y-1.5">
        <label htmlFor={`${id}-name`} className="block text-[12.5px] font-medium text-body">
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
          className={cx(inputCls, "font-mono text-[13px]", nameError && "border-err")}
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
