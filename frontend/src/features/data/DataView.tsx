"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { Notice, cardCls, cx } from "@/components/ui";
import { setUrlParam } from "@/lib/url";
import { CONNECTOR_BY_ID, ConnectorId } from "@/lib/connectors";
import { SnapshotSummary, datasetLabel, useSnapshots } from "@/lib/workspace";
import { ConnectSource, ReimportTarget } from "@/features/data/ConnectSource";
import { DatasetList } from "@/features/data/DatasetList";
import { DatasetPanel, DatasetTab, isDatasetTab } from "@/features/data/DatasetPanel";

export function DataView() {
  const snapshotsQuery = useSnapshots();
  const snapshots = useMemo(() => snapshotsQuery.data?.snapshots ?? [], [snapshotsQuery.data]);
  const [chosenId, setChosenId] = useState<string | null>(null);
  const [tab, setTab] = useState<DatasetTab>("definitions");
  const [revealId, setRevealId] = useState<string | null>(null);
  const clearReveal = useCallback(() => setRevealId(null), []);
  const [reimport, setReimport] = useState<ReimportTarget | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const dataset = params.get("dataset");
    const linkedTab = params.get("tab");
    if (dataset) {
      setChosenId(dataset);
      setRevealId(dataset);
    }
    if (isDatasetTab(linkedTab)) setTab(linkedTab);
  }, []);

  const selected = snapshots.find((s) => s.snapshot_id === chosenId) ?? snapshots[0] ?? null;
  const missing = Boolean(chosenId && snapshotsQuery.data && !snapshots.some((s) => s.snapshot_id === chosenId));
  const approvedCount = snapshots.filter((s) => s.approved).length;

  function select(id: string, nextTab?: DatasetTab, reveal = false) {
    setChosenId(id);
    setUrlParam("dataset", id);
    if (nextTab) changeTab(nextTab);
    if (reveal) setRevealId(id);
  }

  function reimportOf(snapshot: SnapshotSummary): (() => void) | undefined {
    const saved = snapshot.reimport;
    const connectorId = saved?.connector_id as ConnectorId | null | undefined;
    if (!saved || !connectorId || !CONNECTOR_BY_ID.has(connectorId)) return undefined;
    return () =>
      setReimport((prev) => ({
        connectorId,
        sourceId: snapshot.source_id,
        label: datasetLabel(snapshot),
        values: saved.values,
        selectedTables: saved.selected_tables,
        nonce: (prev?.nonce ?? 0) + 1,
      }));
  }

  function changeTab(next: DatasetTab) {
    setTab(next);
    setUrlParam("tab", next);
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Data"
        description="Connect a source, import a read-only snapshot, then approve its metric definitions so Ask can answer from it."
      />

      <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <ConnectSource
          reimport={reimport}
          onCancelReimport={() => setReimport(null)}
          onImported={(id) => select(id, "definitions", true)}
        />

        <section aria-labelledby="datasets-title" className={cx(cardCls, "min-w-0 space-y-4 p-5 sm:p-6")}>
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 id="datasets-title" className="font-display text-lg font-semibold leading-snug tracking-[-0.01em] text-ink">
              Datasets
            </h2>
            {snapshots.length > 0 && (
              <span className="text-xs text-secondary">
                {approvedCount} of {snapshots.length} approved
              </span>
            )}
          </div>
          {missing && (
            <Notice tone="warn" title="That dataset isn't in this workspace">
              It may have been imported into another workspace. Showing the newest dataset instead.
            </Notice>
          )}
          <DatasetList
            snapshots={snapshots}
            loading={snapshotsQuery.isLoading}
            error={snapshotsQuery.error}
            onRetry={() => void snapshotsQuery.refetch()}
            selectedId={selected?.snapshot_id ?? null}
            onSelect={(id) => select(id)}
          />
          {selected && (
            <DatasetPanel
              key={selected.snapshot_id}
              snapshot={selected}
              snapshots={snapshots}
              tab={tab}
              onTabChange={changeTab}
              onOpenVersion={(id) => select(id, undefined, true)}
              reveal={revealId === selected.snapshot_id}
              onRevealed={clearReveal}
              onReimport={reimportOf(selected)}
              onDeleted={() => {
                setChosenId(null);
                setUrlParam("dataset", null);
              }}
            />
          )}
        </section>
      </div>
    </div>
  );
}
