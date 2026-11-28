"use client";

import React, { useId, useState } from "react";
import { RichSelect } from "@/components/RichSelect";
import { btn, cx, inputCls } from "@/components/ui";
import { ConnectorDef, FieldDef, FieldValue, Values, isFieldVisible, sheetsLinkLacksTab, toSheetsCsvUrl } from "@/lib/connectors";

interface FieldProps {
  field: FieldDef;
  id: string;
  value: FieldValue;
  error?: string;
  disabled: boolean;
  onChange: (key: string, value: FieldValue) => void;
}

function FieldMessages({ id, help, error }: { id: string; help?: string; error?: string }) {
  return (
    <>
      {help && (
        <p id={`${id}-help`} className="text-xs leading-snug text-secondary">
          {help}
        </p>
      )}
      {error && (
        <p id={`${id}-err`} className="text-xs text-err">
          {error}
        </p>
      )}
    </>
  );
}

function describedBy(id: string, help?: string, error?: string): string | undefined {
  return [help && `${id}-help`, error && `${id}-err`].filter(Boolean).join(" ") || undefined;
}

function SheetsLinkHint({ field, value, onChange }: { field: FieldDef; value: string; onChange: FieldProps["onChange"] }) {
  const csv = toSheetsCsvUrl(value);
  if (!csv || csv === value.trim()) return null;
  return (
    <p className="flex flex-wrap items-center gap-x-2 text-xs text-secondary">
      This is a Google Sheets page, not a file.
      <button type="button" className={cx(btn.link, "min-h-[32px] text-xs")} onClick={() => onChange(field.key, csv)}>
        Use its CSV export link
      </button>
    </p>
  );
}

function Field({ field, id, value, error, disabled, onChange, sheetsHint }: FieldProps & { sheetsHint: boolean }) {
  const [revealed, setRevealed] = useState(false);
  const describe = describedBy(id, field.help, error);

  if (field.type === "select") {
    return (
      <RichSelect
        label={field.label}
        value={String(value)}
        options={field.options ?? []}
        disabled={disabled}
        hint={field.help}
        onChange={(v) => onChange(field.key, v)}
      />
    );
  }

  if (field.type === "checkbox") {
    return (
      <div className="space-y-1">
        <label htmlFor={id} className="flex min-h-[40px] cursor-pointer items-center gap-2.5 text-sm text-body">
          <input
            id={id}
            type="checkbox"
            checked={value === true}
            disabled={disabled}
            aria-invalid={Boolean(error)}
            aria-describedby={describe}
            onChange={(e) => onChange(field.key, e.target.checked)}
            className="h-4 w-4 shrink-0 accent-focus"
          />
          {field.label}
        </label>
        <FieldMessages id={id} help={field.help} error={error} />
      </div>
    );
  }

  const text = typeof value === "string" ? value : "";
  const shared = {
    id,
    value: text,
    disabled,
    readOnly: field.readOnly,
    placeholder: field.placeholder,
    spellCheck: false,
    autoComplete: "off",
    "aria-invalid": Boolean(error),
    "aria-required": field.required || undefined,
    "aria-describedby": describe,
  };

  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm font-medium text-body">
        {field.label}
      </label>
      {field.type === "textarea" ? (
        <textarea
          {...shared}
          rows={5}
          onChange={(e) => onChange(field.key, e.target.value)}
          className={cx(inputCls, "min-h-[120px] py-2.5 font-mono text-[16px] sm:text-xs", error && "border-err")}
        />
      ) : field.type === "password" ? (
        <div className="relative">
          <input
            {...shared}
            type={revealed ? "text" : "password"}
            onChange={(e) => onChange(field.key, e.target.value)}
            className={cx(inputCls, "pr-16", field.readOnly && "bg-panel text-secondary", error && "border-err")}
          />
          <button
            type="button"
            onClick={() => setRevealed((r) => !r)}
            aria-label={revealed ? `Hide ${field.label}` : `Show ${field.label}`}
            aria-pressed={revealed}
            className="absolute right-1 top-1/2 flex h-7 min-w-[44px] -translate-y-1/2 items-center justify-center rounded-md px-2 text-xs text-secondary transition-colors hover:text-ink"
          >
            {revealed ? "Hide" : "Show"}
          </button>
        </div>
      ) : (
        <input
          {...shared}
          type={field.type === "number" ? "number" : field.type === "url" ? "url" : "text"}
          inputMode={field.type === "number" ? "numeric" : field.type === "url" ? "url" : undefined}
          onChange={(e) => onChange(field.key, e.target.value)}
          className={cx(inputCls, field.readOnly && "bg-panel text-secondary", error && "border-err")}
        />
      )}
      {sheetsHint && <SheetsLinkHint field={field} value={text} onChange={onChange} />}
      {field.key === "sheet_url" && sheetsLinkLacksTab(text) && (
        <p className="text-xs text-secondary">
          This link doesn&apos;t name a tab, so the first tab will be imported. To import another tab, open it and copy the address again.
        </p>
      )}
      <FieldMessages id={id} help={field.help} error={error} />
    </div>
  );
}

export function SourceForm({
  def,
  values,
  errors,
  disabled,
  onChange,
}: {
  def: ConnectorDef;
  values: Values;
  errors: Record<string, string>;
  disabled: boolean;
  onChange: (key: string, value: FieldValue) => void;
}) {
  const base = useId();
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      {def.fields
        .filter((f) => isFieldVisible(f, values))
        .map((f) => (
          <div key={f.key} className={f.half ? undefined : "sm:col-span-2"}>
            <Field
              field={f}
              id={`${base}-${f.key}`}
              value={values[f.key] ?? ""}
              error={errors[f.key]}
              disabled={disabled}
              onChange={onChange}
              sheetsHint={def.family === "remote_file" && f.type === "url" && !f.virtual}
            />
          </div>
        ))}
    </div>
  );
}
