"use client";

import React, { useEffect, useRef } from "react";
import Link from "next/link";
import { ConnectorLogo, logoIdForFamily } from "@/components/ConnectorLogo";
import { Icon } from "@/components/Icon";
import { RichSelect } from "@/components/RichSelect";
import { Badge, Spinner, btnSm, cx } from "@/components/ui";
import type { SnapshotOut } from "@/lib/api/types";
import { PRESET_SUMMARY } from "@/lib/routing";
import type { RunMode } from "@/lib/types";
import { datasetLabel, datasetTables } from "@/lib/workspace";

interface AskComposerProps {
  variant: "new" | "followup";
  value: string;
  onChange: (value: string) => void;
  onSubmit: (e: React.FormEvent) => void;
  textareaRef?: React.RefObject<HTMLTextAreaElement | null>;
  runMode: RunMode;
  onRunModeChange: (mode: RunMode) => void;
  snapshots: SnapshotOut[];
  approvedCount: number;
  selectedSnapshot: SnapshotOut | undefined;
  onSnapshotChange: (snapshotId: string) => void;
  snapshotsLoading: boolean;
  canAsk: boolean;
  submitting: boolean;
  isWorking?: boolean;
  datasetRemoved?: boolean;
  presetSummary: string | null;
  routingLoading: boolean;
  isMac: boolean;
  maxLength?: number;
  budget?: string;
  healthError?: boolean;
  serverDown?: boolean;
}

export function AskComposer({
  variant,
  value,
  onChange,
  onSubmit,
  textareaRef,
  runMode,
  onRunModeChange,
  snapshots,
  approvedCount,
  selectedSnapshot,
  onSnapshotChange,
  snapshotsLoading,
  canAsk,
  submitting,
  isWorking = false,
  datasetRemoved = false,
  presetSummary,
  routingLoading,
  isMac,
  maxLength,
  budget,
  healthError = false,
  serverDown = false,
}: AskComposerProps) {
  const localRef = useRef<HTMLTextAreaElement>(null);
  const resolvedRef = textareaRef ?? localRef;

  useEffect(() => {
    const el = resolvedRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value, resolvedRef]);

  const isFollowUp = variant === "followup";
  const showSelector = !isFollowUp || (datasetRemoved && approvedCount > 0);
  const canSend = isFollowUp
    ? Boolean(value.trim()) && !submitting && !isWorking && Boolean(selectedSnapshot)
    : Boolean(value.trim()) && !submitting && canAsk;

  const placeholder = serverDown
    ? "Waiting for the Ledgerline server…"
    : isFollowUp
    ? isWorking
      ? "Waiting for the current answer to finish…"
      : datasetRemoved && approvedCount === 0
        ? "Dataset removed — approve a dataset on Data to continue"
        : datasetRemoved
          ? "Pick a dataset below to ask a follow-up…"
          : "Ask a follow-up…"
    : canAsk
      ? "Ask about your approved data…"
      : "Finish setup to start asking questions";

  const submitLabel = isFollowUp
    ? submitting
      ? "Sending follow-up"
      : "Ask follow-up"
    : submitting
      ? "Starting"
      : runMode === "quick"
        ? "Ask (quick)"
        : "Ask (deep)";

  return (
    <div className="space-y-1.5">
      <form
        onSubmit={onSubmit}
        className={cx(
          "flex flex-col gap-2 rounded-xl p-2 transition-shadow",
          canAsk
            ? "bg-popover shadow-composer focus-within:shadow-[0_4px_20px_rgb(0_0_0/0.075),inset_0_0_0_2px_rgb(var(--focus))]"
            : "bg-ink/[0.03] shadow-[inset_0_0_0_1px_rgb(var(--ink)/0.08)] focus-within:ring-2 focus-within:ring-focus",
        )}
      >
        <textarea
          ref={resolvedRef}
          aria-label={isFollowUp ? "Ask a follow-up" : "Your question"}
          rows={isFollowUp ? 1 : 2}
          maxLength={maxLength}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.nativeEvent.isComposing || e.key !== "Enter") return;
            const modSubmit = e.metaKey || e.ctrlKey;
            const plainFollowUpSubmit = isFollowUp && !e.shiftKey && !e.altKey;
            if (modSubmit || plainFollowUpSubmit) {
              e.preventDefault();
              e.currentTarget.form?.requestSubmit();
            }
          }}
          placeholder={placeholder}
          className={cx(
            "w-full resize-none bg-transparent px-2 pt-1.5 text-[16px] sm:text-md leading-[21px] placeholder:text-secondary focus:outline-none",
            isFollowUp ? "min-h-[36px]" : "min-h-[50px]",
            canAsk ? "text-ink" : "text-secondary",
          )}
        />

        <div className="flex items-end justify-between gap-2.5">
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
            <div role="group" aria-label="Run mode" className="inline-flex min-h-[32px] items-center rounded-md bg-ink/[0.05] p-[2px] dark:bg-ink/[0.08]">
              {(["quick", "deep"] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  aria-pressed={runMode === mode}
                  onClick={() => onRunModeChange(mode)}
                  title={mode === "quick" ? "One focused analysis step" : "Plans several steps and reviews the evidence"}
                  className={cx(
                    "min-h-[28px] rounded px-2.5 text-sm font-medium capitalize transition-colors",
                    runMode === mode ? "bg-popover text-ink shadow-[0_0_0_1px_rgb(var(--ink)/0.08)]" : "text-secondary hover:text-ink",
                  )}
                >
                  {mode}
                </button>
              ))}
            </div>

            {showSelector ? (
              <div className="min-w-[140px] flex-1 basis-[140px] sm:flex-none sm:basis-auto">
                <RichSelect
                  dense
                  triggerClassName="w-full sm:w-auto sm:min-w-[168px] sm:max-w-[320px]"
                  ariaLabel="Dataset"
                  value={selectedSnapshot?.snapshot_id ?? ""}
                  placeholder={snapshotsLoading ? "Loading datasets…" : datasetRemoved ? "Choose replacement dataset" : "No approved dataset"}
                  options={snapshots.map((s) => ({
                    value: s.snapshot_id,
                    label: `${datasetLabel(s)} · v${s.version}`,
                    sublabel: s.approved
                      ? `${s.display_name ? `${datasetTables(s)} · ` : ""}${s.total_rows.toLocaleString()} rows`
                      : "Needs approval on the Data page",
                    badge: s.approved ? undefined : "Draft",
                    disabled: !s.approved,
                    icon: <ConnectorLogo id={logoIdForFamily(s.source_family) ?? "file"} />,
                  }))}
                  searchable={snapshots.length > 8}
                  disabled={approvedCount === 0}
                  onChange={onSnapshotChange}
                />
              </div>
            ) : selectedSnapshot ? (
              <span className="inline-flex min-h-[28px] items-center gap-1.5 rounded-md border border-border bg-panel/60 px-2.5 text-xs text-secondary">
                <ConnectorLogo id={logoIdForFamily(selectedSnapshot.source_family) ?? "file"} size="sm" />
                <span className="max-w-[180px] truncate text-body">
                  {datasetLabel(selectedSnapshot)} · v{selectedSnapshot.version}
                </span>
              </span>
            ) : datasetRemoved ? (
              <Badge tone="neutral">Removed dataset</Badge>
            ) : null}

            <Link
              href="/settings#routing"
              className={cx(btnSm.ghost, "whitespace-nowrap")}
              title="Which models answer: applies to every question in this workspace. Change it in Settings."
            >
              <span className="text-secondary">Models</span>
              {presetSummary ?? (routingLoading ? "…" : PRESET_SUMMARY.balanced)}
            </Link>
          </div>

          <div className="ml-auto flex shrink-0 items-center gap-2.5">
            <span className="hidden text-xs text-secondary sm:inline">
              {isFollowUp ? "Enter to send" : isMac ? "⌘ + Enter" : "Ctrl + Enter"}
            </span>
            <button
              type="submit"
              aria-label={submitLabel}
              disabled={!canSend}
              className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-brand text-on-accent transition-colors hover:bg-brand-hover disabled:cursor-not-allowed disabled:opacity-40"
            >
              {submitting ? <Spinner className="h-4 w-4" /> : <Icon name="arrow-up" className="h-[17px] w-[17px]" />}
            </button>
          </div>
        </div>
      </form>
      {budget && <p className="px-0.5 text-xs text-secondary">{budget}</p>}
      {healthError && !serverDown && (
        <p className="px-0.5 text-xs text-secondary">Run limits are unavailable right now; the API health check failed.</p>
      )}
    </div>
  );
}
