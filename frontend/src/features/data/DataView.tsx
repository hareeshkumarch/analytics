"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { toast } from "@/lib/toast";
import { Notice, btn, cardCls, sectionTitleCls } from "@/components/ui";
import { setUrlParam } from "@/lib/url";
import { CONNECTOR_BY_ID, ConnectorId } from "@/lib/connectors";
import { isSampleDataset } from "@/lib/sample";
import { SnapshotSummary, datasetLabel, useServerDown, useSnapshots } from "@/lib/workspace";
import { ConnectSource, ReimportTarget } from "@/features/data/ConnectSource";
import { DatasetList } from "@/features/data/DatasetList";
import { DatasetPanel, DatasetTab, isDatasetTab } from "@/features/data/DatasetPanel";

export function DataView() {
  const snapshotsQuery = useSnapshots();
  const serverDown = useServerDown();
  const snapshots = useMemo(() => snapshotsQuery.data?.snapshots ?? [], [snapshotsQuery.data]);
  const [chosenId, setChosenId] = useState<string | null>(null);
  const [tab, setTab] = useState<DatasetTab>("definitions");
  const [revealId, setRevealId] = useState<string | null>(null);
  const clearReveal = useCallback(() => setRevealId(null), []);
  const [reimport, setReimport] = useState<ReimportTarget | null>(null);
  const [adding, setAdding] = useState(false);

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
  const hasData = snapshots.length > 0;
  const showSources = (!snapshotsQuery.isLoading && !hasData) || adding || Boolean(reimport);

  function closeSources() {
    setAdding(false);
    setReimport(null);
  }

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
        values: Object.fromEntries(Object.entries(saved.values).map(([k, v]) => [k, typeof v === "number" ? String(v) : v])),
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

      {!serverDown && showSources && (
        <ConnectSource
          reimport={reimport}
          onCancelReimport={() => {
            setReimport(null);
            setAdding(true);
          }}
          onImported={(id) => {
            closeSources();
            select(id, "definitions", true);
            toast("Dataset imported. Review and approve definitions below.");
          }}
          offerSample={!snapshots.some(isSampleDataset)}
          onClose={hasData ? closeSources : undefined}
        />
      )}

      {!serverDown && (hasData || snapshotsQuery.isLoading || snapshotsQuery.error) && (
        <section aria-labelledby="datasets-title" className={`${cardCls} min-w-0 space-y-4 p-5 sm:p-6`}>
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 id="datasets-title" className={sectionTitleCls}>
              Datasets
            </h2>
            {hasData && (
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-xs text-secondary">
                  {approvedCount} of {snapshots.length} approved
                </span>
                {!showSources && (
                  <button type="button" className={btn.secondary} onClick={() => setAdding(true)}>
                    Add data
                  </button>
                )}
              </div>
            )}
          </div>
          {missing && (
            <Notice tone="warn" title="That dataset isn't in this workspace">
              It may have been imported into another workspace. Showing the newest dataset instead.
            </Notice>
          )}
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(260px,320px)_minmax(0,1fr)] lg:items-start">
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
          </div>
        </section>
      )}
    </div>
  );
}
