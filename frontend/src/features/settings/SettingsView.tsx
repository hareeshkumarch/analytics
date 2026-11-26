"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { apiFetch, errorHint, errorMessage } from "@/lib/api/client";
import { RichSelect, RichOption } from "@/components/RichSelect";
import { PageHeader } from "@/components/PageHeader";
import { HScroll } from "@/components/HScroll";
import { Badge, Notice, Spinner, btn, cardCls, cx, inputCls } from "@/components/ui";
import { ProviderLogo } from "@/components/ConnectorLogo";
import { PRESET_OPTIONS, RoutingPreset, useRoutingPolicy } from "@/lib/routing";
import { humanize, providerLabel, useProviders, useWorkspace } from "@/lib/workspace";
import type { SaveProviderKeyRequest } from "@/lib/api/types";

const FALLBACK_OPTIONS: RichOption[] = [
  { value: "on", label: "Fall back on failure", sublabel: "Try the next eligible model when a route fails" },
  { value: "off", label: "No fallback", sublabel: "Fail the step instead of switching models" },
];

type Msg = { text: string; hint?: string | null; ok: boolean };

function sameList(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

function AddModelPopover({
  roleLabel,
  options,
  disabled,
  onAdd,
}: {
  roleLabel: string;
  options: string[];
  disabled: boolean;
  onAdd: (modelId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (options.length === 0) return null;

  return (
    <div ref={rootRef} className="relative inline-block">
      <button
        type="button"
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label={`Add model to ${roleLabel}`}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex h-7 items-center gap-1 border border-dashed border-border-strong bg-surface px-2 text-[11.5px] font-medium text-body transition-colors hover:border-ink hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
      >
        + Add model
      </button>
      {open && (
        <div
          role="menu"
          aria-label={`Available models for ${roleLabel}`}
          className="absolute left-0 top-full z-40 mt-1 max-h-52 min-w-[210px] max-w-[calc(100vw-2rem)] overflow-y-auto border border-border bg-surface p-1 shadow-pop"
        >
          {options.map((modelId) => (
            <button
              key={modelId}
              type="button"
              role="menuitem"
              onClick={() => {
                onAdd(modelId);
                setOpen(false);
              }}
              className="flex w-full items-center px-2.5 py-1.5 text-left font-mono text-[11.5px] text-ink transition-colors hover:bg-sidebar"
            >
              {modelId}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function SettingsView() {
  const ws = useWorkspace();
  const { data, isLoading, error, refetch } = useProviders();
  const routing = useRoutingPolicy(ws.id);

  const [chosenProvider, setChosenProvider] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [saveMsg, setSaveMsg] = useState<Msg | null>(null);
  const [probeMsg, setProbeMsg] = useState<Msg | null>(null);
  const [saving, setSaving] = useState(false);
  const [probing, setProbing] = useState(false);

  const keyCardRef = useRef<HTMLFormElement>(null);
  const apiKeyInputRef = useRef<HTMLInputElement>(null);

  const [roleDrafts, setRoleDrafts] = useState<Record<string, string[]>>({});
  const [ceilingDraft, setCeilingDraft] = useState("");
  const [customRolesOpen, setCustomRolesOpen] = useState(false);
  const [routingMsg, setRoutingMsg] = useState<Msg | null>(null);
  const [dragState, setDragState] = useState<{ role: string; index: number } | null>(null);

  const providerId =
    chosenProvider ?? data?.providers.find((p) => !p.configured)?.provider_id ?? data?.providers[0]?.provider_id ?? "";
  const provider = data?.providers.find((p) => p.provider_id === providerId);
  const routeRoles = useMemo(() => Array.from(new Set((data?.models ?? []).flatMap((m) => m.roles))).sort(), [data]);
  const enabledModelsByRole = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const m of data?.models ?? []) {
      if (!m.enabled) continue;
      for (const r of m.roles) map.set(r, [...(map.get(r) ?? []), m.model_id]);
    }
    return map;
  }, [data]);

  const savedRoutes = useMemo(() => {
    const out: Record<string, string[]> = {};
    for (const [role, models] of Object.entries(routing.data?.role_routes ?? {})) {
      out[role] = [...models];
    }
    return out;
  }, [routing.data]);
  const savedCeiling = routing.data?.hard_cost_ceiling_usd ?? "";

  // Load saved values into the form when they actually change on the server. Saving only the preset or
  // fallback leaves routes and the ceiling unchanged, so unsaved edits to those fields are kept.
  const savedRoutesKey = JSON.stringify(savedRoutes);
  const routingLoaded = Boolean(routing.data);
  useEffect(() => {
    if (!routingLoaded) return;
    setRoleDrafts(JSON.parse(savedRoutesKey) as Record<string, string[]>);
  }, [routingLoaded, savedRoutesKey]);
  useEffect(() => {
    if (!routingLoaded) return;
    setCeilingDraft(savedCeiling);
  }, [routingLoaded, savedCeiling]);

  const changedRoles = routeRoles.filter((role) => !sameList(roleDrafts[role] ?? [], savedRoutes[role] ?? []));
  const ceilingChanged = ceilingDraft.trim() !== savedCeiling;
  const dirty = changedRoles.length > 0 || ceilingChanged;

  const ceilingNumber = Number(ceilingDraft);
  const ceilingError =
    ceilingDraft.trim() && (!Number.isFinite(ceilingNumber) || ceilingNumber <= 0)
      ? "Enter an amount above 0, or leave empty."
      : null;
  const roleErrors: Record<string, string> = {};
  for (const role of changedRoles) {
    const models = roleDrafts[role] ?? [];
    const allowed = enabledModelsByRole.get(role) ?? [];
    if (!models.length) roleErrors[role] = "Add at least one model.";
    else {
      const unknown = models.filter((m) => !allowed.includes(m));
      if (unknown.length) roleErrors[role] = `Not enabled for this role: ${unknown.join(", ")}`;
    }
  }
  const hasRoleErrors = Object.keys(roleErrors).length > 0;
  const canSaveRouting = dirty && !ceilingError && !hasRoleErrors && !routing.updating;

  useEffect(() => {
    if (!dirty && !apiKey.trim()) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty, apiKey]);

  function selectProviderForKey(targetId: string) {
    setChosenProvider(targetId);
    setApiKey("");
    setShowKey(false);
    setSaveMsg(null);
    setProbeMsg(null);
    keyCardRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    window.requestAnimationFrame(() => apiKeyInputRef.current?.focus({ preventScroll: true }));
  }

  function moveRoleModel(role: string, from: number, to: number) {
    setRoleDrafts((prev) => {
      const list = [...(prev[role] ?? [])];
      if (from < 0 || to < 0 || from >= list.length || to >= list.length || from === to) return prev;
      const [moved] = list.splice(from, 1);
      list.splice(to, 0, moved);
      return { ...prev, [role]: list };
    });
  }

  function removeRoleModel(role: string, index: number) {
    setRoleDrafts((prev) => {
      const list = [...(prev[role] ?? [])];
      list.splice(index, 1);
      return { ...prev, [role]: list };
    });
  }

  function addRoleModel(role: string, modelId: string) {
    setRoleDrafts((prev) => {
      const list = prev[role] ?? [];
      if (list.includes(modelId)) return prev;
      return { ...prev, [role]: [...list, modelId] };
    });
  }

  function saveRouting() {
    if (!canSaveRouting) return;
    setRoutingMsg(null);
    const patch: Parameters<typeof routing.update>[0] = {};
    if (changedRoles.length) patch.role_routes = Object.fromEntries(changedRoles.map((r) => [r, roleDrafts[r] ?? []]));
    if (ceilingChanged) patch.hard_cost_ceiling_usd = ceilingDraft.trim() || null;
    routing.update(patch, {
      onSuccess: () => setRoutingMsg({ text: "Routing saved.", ok: true }),
      onError: (err) => setRoutingMsg({ text: errorMessage(err, "Couldn't save routing."), hint: errorHint(err), ok: false }),
    });
  }

  function updateRoutingChoice(patch: Parameters<typeof routing.update>[0]) {
    setRoutingMsg(null);
    routing.update(patch, {
      onError: (err) => setRoutingMsg({ text: errorMessage(err, "Couldn't save routing."), hint: errorHint(err), ok: false }),
    });
  }

  async function handleSaveKey(e: React.FormEvent) {
    e.preventDefault();
    if (!apiKey.trim() || !provider) return;
    const savedId = provider.provider_id;
    setSaveMsg(null);
    setProbeMsg(null);
    setSaving(true);
    try {
      await apiFetch<{ provider_id: string; status: string }>(ws.path("/settings/provider-key"), {
        method: "PUT",
        body: JSON.stringify({ provider_id: savedId, api_key: apiKey.trim() } satisfies SaveProviderKeyRequest),
      });
      setChosenProvider(savedId);
      setApiKey("");
      setShowKey(false);
      setSaveMsg({ text: `Key saved for ${providerLabel(savedId)}. It is encrypted and can't be read back.`, ok: true });
      await refetch();
    } catch (err) {
      setSaveMsg({ text: errorMessage(err, "Couldn't save the key."), hint: errorHint(err), ok: false });
    } finally {
      setSaving(false);
    }
  }

  async function handleTestProvider() {
    setProbeMsg(null);
    setProbing(true);
    try {
      const res = await apiFetch<{ provider_id: string; status: string; message?: string; model_id?: string; latency_ms?: number }>(
        ws.path(`/providers/${encodeURIComponent(providerId)}/test`),
        { method: "POST" }
      );
      setProbeMsg(
        res.status === "ok"
          ? { text: `${providerLabel(res.provider_id)} answered with ${res.model_id ?? "a model"} in ${res.latency_ms ?? "?"} ms.`, ok: true }
          : { text: `${providerLabel(res.provider_id)}: ${res.status.replaceAll("_", " ")}`, hint: res.message ?? null, ok: false }
      );
    } catch (err) {
      setProbeMsg({ text: errorMessage(err, "The test failed."), hint: errorHint(err), ok: false });
    } finally {
      setProbing(false);
    }
  }

  const providerOptions: RichOption[] = (data?.providers ?? []).map((p) => ({
    value: p.provider_id,
    label: providerLabel(p.provider_id),
    icon: <ProviderLogo providerId={p.provider_id} />,
    sublabel: p.key_status === "needs_key" ? "Saved key can't be decrypted; save it again" : p.configured ? "Key saved" : "No key yet",
    badge: p.key_status === "needs_key" ? "Needs a key" : p.configured ? "Ready" : undefined,
  }));
  const configuredCount = (data?.providers ?? []).filter((p) => p.configured).length;
  const rolesExpanded = customRolesOpen || hasRoleErrors;

  return (
    <div className="space-y-6">
      <PageHeader title="Settings" description="Model provider keys and how questions are routed to models." />

      {data && configuredCount === 0 && (
        <Notice tone="warn" title="No provider key yet">
          Add at least one key below. Questions can&apos;t be answered until a provider is ready.
        </Notice>
      )}

      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
        <form ref={keyCardRef} onSubmit={handleSaveKey} className={cx(cardCls, "space-y-4 p-5 sm:p-6")}>
          <div>
            <h2 className="font-display text-lg font-semibold leading-snug tracking-[-0.01em] text-ink">Provider key</h2>
            <p className="mt-1 text-xs text-secondary">Keys are encrypted at rest and can&apos;t be read back after saving.</p>
          </div>
          <RichSelect
            label="Provider"
            value={providerId}
            options={providerOptions}
            placeholder={isLoading ? "Loading providers…" : "No providers available"}
            disabled={providerOptions.length === 0}
            onChange={(v) => {
              setChosenProvider(v);
              setApiKey("");
              setShowKey(false);
              setSaveMsg(null);
              setProbeMsg(null);
            }}
          />
          <div className="space-y-1.5">
            <label htmlFor="provider-api-key" className="block text-[12.5px] font-medium text-body">
              {provider?.configured ? "Replace API key" : "API key"}
            </label>
            <div className="relative">
              <input
                ref={apiKeyInputRef}
                id="provider-api-key"
                type={showKey ? "text" : "password"}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={provider?.configured ? "Paste a new key to replace the saved one" : "Paste API key"}
                autoComplete="new-password"
                spellCheck={false}
                className={cx(inputCls, "pr-11")}
              />
              <button
                type="button"
                onClick={() => setShowKey((s) => !s)}
                aria-label={showKey ? "Hide API key" : "Show API key"}
                aria-pressed={showKey}
                className="absolute right-1.5 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center text-secondary transition-colors hover:text-ink"
              >
                {showKey ? (
                  <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M17.94 17.94A10.94 10.94 0 0 1 12 20c-7 0-11-8-11-8a21.77 21.77 0 0 1 5.06-6.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a21.8 21.8 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
                    <path d="M1 1l22 22" />
                  </svg>
                ) : (
                  <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                    <circle cx="12" cy="12" r="3" />
                  </svg>
                )}
              </button>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="submit" disabled={!apiKey.trim() || saving} className={btn.primary}>
              {saving && <Spinner />}
              {saving ? "Saving…" : "Save key"}
            </button>
            {provider?.configured && (
              <button
                type="button"
                onClick={() => void handleTestProvider()}
                disabled={probing || !providerId || Boolean(apiKey.trim())}
                className={btn.secondary}
                title={apiKey.trim() ? "Save the new key first; the test uses the saved key" : undefined}
              >
                {probing && <Spinner />}
                {probing ? "Testing…" : "Test saved key"}
              </button>
            )}
          </div>
          {saveMsg && (
            <Notice tone={saveMsg.ok ? "ok" : "err"} title={saveMsg.text}>
              {saveMsg.hint}
            </Notice>
          )}
          {probeMsg && (
            <Notice tone={probeMsg.ok ? "ok" : "err"} title={probeMsg.text}>
              {probeMsg.hint}
            </Notice>
          )}
        </form>

        <section className={cx(cardCls, "space-y-4 p-5 sm:p-6")}>
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="font-display text-lg font-semibold leading-snug tracking-[-0.01em] text-ink">Routing</h2>
              <p className="mt-1 text-xs text-secondary">Applies to every new question in this workspace. Preset and fallback save as soon as you pick them.</p>
            </div>
            {routing.data && <Badge>{routing.updating ? "Saving…" : `Version ${routing.data.policy_version}`}</Badge>}
          </div>
          <RichSelect
            label="Routing preset"
            value={routing.data?.preset ?? ""}
            placeholder={routing.isLoading ? "Loading…" : "Unavailable"}
            options={PRESET_OPTIONS}
            disabled={!routing.data || routing.updating}
            onChange={(v) => updateRoutingChoice({ preset: v as RoutingPreset })}
          />
          <RichSelect
            label="Fallback"
            value={routing.data ? (routing.data.fallback_enabled ? "on" : "off") : ""}
            placeholder={routing.isLoading ? "Loading…" : "Unavailable"}
            options={FALLBACK_OPTIONS}
            disabled={!routing.data || routing.updating}
            onChange={(v) => updateRoutingChoice({ fallback_enabled: v === "on" })}
          />

          <form
            className="space-y-4 border-t border-border pt-4"
            onSubmit={(e) => {
              e.preventDefault();
              saveRouting();
            }}
          >
            <div className="space-y-1.5">
              <label htmlFor="cost-ceiling" className="block text-[12.5px] font-medium text-body">
                Cost limit per question (USD)
              </label>
              <input
                id="cost-ceiling"
                type="number"
                inputMode="decimal"
                min="0"
                step="0.01"
                value={ceilingDraft}
                disabled={!routing.data}
                onChange={(event) => setCeilingDraft(event.target.value)}
                placeholder="No extra limit"
                aria-invalid={Boolean(ceilingError)}
                className={cx(inputCls, "min-h-[40px]", ceilingError && "border-err")}
              />
              <p className={cx("text-xs", ceilingError ? "text-err" : "text-secondary")}>
                {ceilingError ?? "A run stops when it would spend more than this (models without declared pricing are skipped while active). Leave empty for no extra limit."}
              </p>
            </div>

            {routeRoles.length > 0 && (
              <div className="border-t border-border pt-3">
                <button
                  type="button"
                  aria-expanded={rolesExpanded}
                  aria-controls="routing-custom-roles"
                  onClick={() => setCustomRolesOpen((o) => !o)}
                  className="flex w-full items-center justify-between gap-2 py-1 text-left text-[13px] font-medium text-body transition-colors hover:text-ink"
                >
                  <span className="flex items-center gap-2">
                    <span>Advanced: Customize per role</span>
                    {changedRoles.length > 0 && <Badge tone="warn">Edited</Badge>}
                  </span>
                  <svg
                    aria-hidden="true"
                    className={cx("h-4 w-4 shrink-0 text-secondary transition-transform", rolesExpanded && "rotate-180")}
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <path d="m6 9 6 6 6-6" />
                  </svg>
                </button>

                {rolesExpanded && (
                  <div id="routing-custom-roles" className="mt-3 space-y-3.5">
                    <p className="text-xs text-secondary">
                      Under Balanced, models are tried left to right. Lowest cost, Fastest, and Highest quality automatically reorder the models kept below.
                    </p>
                    {routeRoles.map((role) => {
                      const current = roleDrafts[role] ?? [];
                      const suggestions = (enabledModelsByRole.get(role) ?? []).filter((m) => !current.includes(m));
                      const roleLabel = humanize(role);
                      const errId = `route-${role}-err`;
                      return (
                        <div key={role} className="space-y-1.5">
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-[12.5px] font-medium text-body">{roleLabel}</span>
                          </div>
                          <div
                            aria-describedby={roleErrors[role] ? errId : undefined}
                            className={cx(
                              "flex min-h-[42px] flex-wrap items-center gap-1.5 border bg-canvas p-2",
                              roleErrors[role] ? "border-err" : "border-border"
                            )}
                          >
                            {current.map((modelId, idx) => (
                              <span
                                key={modelId}
                                draggable={Boolean(routing.data) && current.length > 1}
                                onDragStart={() => setDragState({ role, index: idx })}
                                onDragOver={(e) => {
                                  if (dragState?.role === role) e.preventDefault();
                                }}
                                onDrop={(e) => {
                                  e.preventDefault();
                                  if (dragState?.role === role) moveRoleModel(role, dragState.index, idx);
                                  setDragState(null);
                                }}
                                onDragEnd={() => setDragState(null)}
                                className={cx(
                                  "inline-flex min-h-[28px] max-w-full items-center gap-1 border bg-surface px-2 py-0.5 font-mono text-[11.5px] text-ink",
                                  dragState?.role === role && dragState.index === idx ? "border-dashed border-ink opacity-60" : "border-border",
                                  current.length > 1 && "cursor-grab active:cursor-grabbing"
                                )}
                              >
                                {current.length > 1 && (
                                  <button
                                    type="button"
                                    disabled={!routing.data || idx === 0}
                                    onClick={() => moveRoleModel(role, idx, idx - 1)}
                                    aria-label={`Move ${modelId} earlier in ${roleLabel}`}
                                    className="px-1 py-0.5 text-xs leading-none text-secondary transition-colors hover:text-ink disabled:opacity-30"
                                  >
                                    ‹
                                  </button>
                                )}
                                <span className="break-all">{modelId}</span>
                                {current.length > 1 && (
                                  <button
                                    type="button"
                                    disabled={!routing.data || idx === current.length - 1}
                                    onClick={() => moveRoleModel(role, idx, idx + 1)}
                                    aria-label={`Move ${modelId} later in ${roleLabel}`}
                                    className="px-1 py-0.5 text-xs leading-none text-secondary transition-colors hover:text-ink disabled:opacity-30"
                                  >
                                    ›
                                  </button>
                                )}
                                <button
                                  type="button"
                                  disabled={!routing.data}
                                  onClick={() => removeRoleModel(role, idx)}
                                  aria-label={`Remove ${modelId} from ${roleLabel}`}
                                  className="ml-0.5 px-1 py-0.5 text-xs leading-none text-secondary transition-colors hover:text-err disabled:opacity-30"
                                >
                                  ×
                                </button>
                              </span>
                            ))}
                            <AddModelPopover
                              roleLabel={roleLabel}
                              options={suggestions}
                              disabled={!routing.data}
                              onAdd={(modelId) => addRoleModel(role, modelId)}
                            />
                          </div>
                          {roleErrors[role] && (
                            <p id={errId} className="text-xs text-err">
                              {roleErrors[role]}
                            </p>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            {(rolesExpanded || dirty) && (
              <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
                <button type="submit" className={btn.primary} disabled={!canSaveRouting}>
                  {routing.updating && <Spinner />}
                  Save routing
                </button>
                {dirty && (
                  <button
                    type="button"
                    className={btn.ghost}
                    onClick={() => {
                      setRoleDrafts(savedRoutes);
                      setCeilingDraft(savedCeiling);
                      setRoutingMsg(null);
                    }}
                  >
                    Reset
                  </button>
                )}
                {dirty && <span className="text-xs text-secondary">Unsaved changes</span>}
              </div>
            )}
          </form>
          {routingMsg && (
            <Notice tone={routingMsg.ok ? "ok" : "err"} title={routingMsg.text}>
              {routingMsg.hint}
            </Notice>
          )}
          {routing.error && <Notice tone="err" title="Couldn't load routing">{errorMessage(routing.error)}</Notice>}
        </section>
      </div>

      <section className={cx(cardCls, "space-y-4 p-5 sm:p-6")}>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="font-display text-lg font-semibold leading-snug tracking-[-0.01em] text-ink">Providers</h2>
          {data && (
            <span className="text-xs text-secondary">
              {configuredCount} of {data.providers.length} ready
            </span>
          )}
        </div>
        {isLoading && <div className="h-24 animate-pulse bg-panel" aria-hidden="true" />}
        {error && <Notice tone="err" title="Couldn't load providers">{errorMessage(error)}</Notice>}
        {data && data.providers.length > 0 && (
          <HScroll className="border border-border" label="Providers">
            <table className="w-full min-w-[560px] border-collapse text-left text-[13px]">
              <thead>
                <tr className="bg-panel text-[11px] uppercase tracking-[0.05em] text-secondary">
                  <th className="px-3.5 py-2.5 font-medium">Provider</th>
                  <th className="px-3.5 py-2.5 font-medium">Models</th>
                  <th className="px-3.5 py-2.5 text-right font-medium">Status</th>
                  <th className="w-[1%] whitespace-nowrap px-3.5 py-2.5 text-right font-medium">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.providers.map((p) => {
                  const models = data.models.filter((m) => m.provider_id === p.provider_id && m.enabled);
                  return (
                    <tr key={p.provider_id} className="border-t border-border align-top">
                      <td className="px-3.5 py-2.5 font-medium text-ink">
                        <span className="inline-flex items-center gap-2.5">
                          <ProviderLogo providerId={p.provider_id} />
                          {providerLabel(p.provider_id)}
                        </span>
                      </td>
                      <td className="px-3.5 py-2.5 text-secondary">
                        {models.length ? (
                          <Details models={models.map((m) => ({ id: m.model_id, roles: m.roles }))} />
                        ) : (
                          "None enabled"
                        )}
                      </td>
                      <td className="whitespace-nowrap px-3.5 py-2.5 text-right">
                        {p.key_status === "needs_key" ? (
                          <Badge tone="err" dot>
                            Key unreadable
                          </Badge>
                        ) : p.configured ? (
                          <Badge tone="ok" dot>
                            Ready
                          </Badge>
                        ) : (
                          <Badge tone="warn" dot>
                            Needs a key
                          </Badge>
                        )}
                      </td>
                      <td className="whitespace-nowrap px-3.5 py-2 text-right">
                        <button
                          type="button"
                          onClick={() => selectProviderForKey(p.provider_id)}
                          className="inline-flex min-h-[32px] items-center border border-border-strong bg-surface px-3 text-[12px] font-medium text-ink transition-colors hover:border-ink hover:bg-panel"
                        >
                          {p.configured ? "Manage key" : "Add key"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </HScroll>
        )}
      </section>
    </div>
  );
}

function Details({ models }: { models: { id: string; roles: string[] }[] }) {
  return (
    <details className="group">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 border border-border bg-panel/60 px-2 py-0.5 text-[12px] font-medium text-body transition-colors hover:border-border-strong hover:bg-panel hover:text-ink [&::-webkit-details-marker]:hidden">
        <span>
          {models.length} model{models.length === 1 ? "" : "s"}
        </span>
        <svg
          aria-hidden="true"
          className="h-3.5 w-3.5 text-secondary transition-transform group-open:rotate-180"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </summary>
      <ul className="mt-2 space-y-1">
        {models.map((m) => (
          <li key={m.id} className="flex flex-wrap items-center gap-1.5">
            <span className="font-mono text-[12px] text-ink">{m.id}</span>
            {m.roles.map((r) => (
              <span key={r} className="bg-panel px-1.5 py-0.5 text-[10.5px] text-secondary">
                {r.replaceAll("_", " ")}
              </span>
            ))}
          </li>
        ))}
      </ul>
    </details>
  );
}
