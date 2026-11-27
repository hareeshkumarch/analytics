"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "@/lib/toast";
import { Icon } from "@/components/Icon";
import { apiFetch, errorHint, errorMessage } from "@/lib/api/client";
import { RichSelect, RichOption } from "@/components/RichSelect";
import { PageHeader } from "@/components/PageHeader";
import { Badge, Notice, Spinner, btn, btnSm, cardCls, cx, inputCls, sectionTitleCls } from "@/components/ui";
import { ProviderLogo } from "@/components/ConnectorLogo";
import { BusinessTimezone } from "@/features/settings/BusinessTimezone";
import { IncidentControls, PauseControl, providerSwitchId, useOpsSwitches } from "@/features/settings/IncidentControls";
import { PRESET_OPTIONS, RoutingPreset, useRoutingPolicy } from "@/lib/routing";
import { humanize, providerLabel, useProviders, useServerDown, useWorkspace } from "@/lib/workspace";
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
        className={cx(btnSm.secondary, "border-dashed")}
      >
        + Add model
      </button>
      {open && (
        <div
          role="menu"
          aria-label={`Available models for ${roleLabel}`}
          className="absolute left-0 top-full z-40 mt-1 max-h-52 min-w-[210px] max-w-[calc(100vw-2rem)] overflow-y-auto rounded-lg border border-border bg-surface p-1 shadow-pop"
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
              className="flex w-full items-center rounded px-2.5 py-1.5 text-left font-mono text-xs text-ink transition-colors hover:bg-sidebar"
            >
              {modelId}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

const SECTIONS = [
  { id: "provider-key", label: "Provider key" },
  { id: "routing", label: "Routing" },
  { id: "time-zone", label: "Time zone" },
  { id: "providers", label: "Providers" },
  { id: "incident-controls", label: "Incident controls" },
];

export function SettingsView() {
  const ws = useWorkspace();
  const { data, isLoading, error, refetch } = useProviders();
  const routing = useRoutingPolicy(ws.id);
  const ops = useOpsSwitches();

  const [chosenProvider, setChosenProvider] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [saveMsg, setSaveMsg] = useState<Msg | null>(null);
  const [probeMsg, setProbeMsg] = useState<Msg | null>(null);
  const [saving, setSaving] = useState(false);
  const [probing, setProbing] = useState(false);

  const keyCardRef = useRef<HTMLFormElement>(null);
  const apiKeyInputRef = useRef<HTMLInputElement>(null);

  const [roleDraftState, setRoleDraftState] = useState<Record<string, string[]> | null>(null);
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
    setRoleDraftState(JSON.parse(savedRoutesKey) as Record<string, string[]>);
  }, [routingLoaded, savedRoutesKey]);
  useEffect(() => {
    if (!routingLoaded) return;
    setCeilingDraft(savedCeiling);
  }, [routingLoaded, savedCeiling]);

  const roleDrafts = roleDraftState ?? savedRoutes;
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
    setRoleDraftState((current) => {
      const prev = current ?? savedRoutes;
      const list = [...(prev[role] ?? [])];
      if (from < 0 || to < 0 || from >= list.length || to >= list.length || from === to) return prev;
      const [moved] = list.splice(from, 1);
      list.splice(to, 0, moved);
      return { ...prev, [role]: list };
    });
  }

  function removeRoleModel(role: string, index: number) {
    setRoleDraftState((current) => {
      const prev = current ?? savedRoutes;
      const list = [...(prev[role] ?? [])];
      list.splice(index, 1);
      return { ...prev, [role]: list };
    });
  }

  function addRoleModel(role: string, modelId: string) {
    setRoleDraftState((current) => {
      const prev = current ?? savedRoutes;
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
      onSuccess: () => toast("Routing saved."),
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
      toast(`Key saved for ${providerLabel(savedId)}.`);
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
      if (res.status === "ok") toast(`${providerLabel(res.provider_id)} answered with ${res.model_id ?? "a model"} in ${res.latency_ms ?? "?"} ms.`);
      else setProbeMsg({ text: `${providerLabel(res.provider_id)}: ${res.status.replaceAll("_", " ")}`, hint: res.message ?? null, ok: false });
    } catch (err) {
      setProbeMsg({ text: errorMessage(err, "The test failed."), hint: errorHint(err), ok: false });
    } finally {
      setProbing(false);
    }
  }

  const serverDown = useServerDown();
  const providerOptions: RichOption[] = (data?.providers ?? []).map((p) => ({
    value: p.provider_id,
    label: providerLabel(p.provider_id),
    icon: <ProviderLogo providerId={p.provider_id} />,
    sublabel: p.key_status === "needs_key" ? "Saved key can't be decrypted; save it again" : p.configured ? "Key saved" : "No key yet",
    badge: p.key_status === "needs_key" ? "Needs a key" : p.configured ? "Ready" : undefined,
  }));
  const configuredCount = (data?.providers ?? []).filter((p) => p.configured).length;
  const rolesExpanded = customRolesOpen || hasRoleErrors;

  if (serverDown) {
    return <PageHeader title="Settings" description="Model provider keys and how questions are routed to models." />;
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Settings" description="Model provider keys and how questions are routed to models." />

      <nav aria-label="Settings sections" className="flex flex-wrap gap-1.5">
        {SECTIONS.map((s) => (
          <a
            key={s.id}
            href={`#${s.id}`}
            className={btnSm.secondary}
          >
            {s.label}
          </a>
        ))}
      </nav>

      {data && configuredCount === 0 && (
        <Notice tone="warn" title="No provider key yet">
          Add at least one key below. Questions can&apos;t be answered until a provider is ready.
        </Notice>
      )}

      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
        <form id="provider-key" ref={keyCardRef} onSubmit={handleSaveKey} className={cx(cardCls, "scroll-mt-28 space-y-4 p-5 sm:p-6")}>
          <div>
            <h2 className={sectionTitleCls}>Provider key</h2>
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
            <label htmlFor="provider-api-key" className="block text-sm font-medium text-body">
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
                className="absolute right-1.5 top-1/2 flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-full text-secondary transition-colors hover:text-ink"
              >
                {showKey ? (
                  <Icon name="eye-off" className="h-4 w-4" />
                ) : (
                  <Icon name="eye" className="h-4 w-4" />
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

        <section id="routing" className={cx(cardCls, "scroll-mt-28 space-y-4 p-5 sm:p-6")}>
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className={sectionTitleCls}>Routing</h2>
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
              <label htmlFor="cost-ceiling" className="block text-sm font-medium text-body">
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
                className={cx(inputCls, ceilingError && "border-err")}
              />
              <p className={cx("text-xs", ceilingError ? "text-err" : "text-secondary")}>
                {ceilingError ?? "A run stops when it would spend more than this (models without declared pricing are skipped while active). Leave empty for no extra limit."}
              </p>
            </div>

            {(routeRoles.length > 0 || isLoading) && (
              <div className="border-t border-border pt-3">
                <button
                  type="button"
                  disabled={routeRoles.length === 0}
                  aria-expanded={rolesExpanded}
                  aria-controls="routing-custom-roles"
                  onClick={() => setCustomRolesOpen((o) => !o)}
                  className="flex w-full items-center justify-between gap-2 py-1 text-left text-sm font-medium text-body transition-colors hover:text-ink"
                >
                  <span className="flex items-center gap-2">
                    <span>Advanced: Customize per role</span>
                    {changedRoles.length > 0 && <Badge tone="warn">Edited</Badge>}
                  </span>
                  <Icon name="chevron-down" className={cx("h-4 w-4 shrink-0 text-secondary transition-transform", rolesExpanded && "rotate-180")} />
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
                            <span className="text-sm font-medium text-body">{roleLabel}</span>
                          </div>
                          <div
                            aria-describedby={roleErrors[role] ? errId : undefined}
                            className={cx(
                              "flex min-h-[42px] flex-wrap items-center gap-1.5 rounded-lg border bg-canvas p-2",
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
                                  "inline-flex min-h-[28px] max-w-full items-center gap-1 rounded-full border bg-surface px-2 py-0.5 font-mono text-xs text-ink",
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
                                    className="rounded-full px-1 py-0.5 text-xs leading-none text-secondary transition-colors hover:text-ink disabled:opacity-30"
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
                                    className="rounded-full px-1 py-0.5 text-xs leading-none text-secondary transition-colors hover:text-ink disabled:opacity-30"
                                  >
                                    ›
                                  </button>
                                )}
                                <button
                                  type="button"
                                  disabled={!routing.data}
                                  onClick={() => removeRoleModel(role, idx)}
                                  aria-label={`Remove ${modelId} from ${roleLabel}`}
                                  className="ml-0.5 rounded-full px-1 py-0.5 text-xs leading-none text-secondary transition-colors hover:text-err disabled:opacity-30"
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
                      setRoleDraftState(savedRoutes);
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

      <div id="time-zone" className="scroll-mt-28">
        <BusinessTimezone workspaceId={ws.id} />
      </div>

      <section id="providers" className={cx(cardCls, "scroll-mt-28 space-y-4 p-5 sm:p-6")}>
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className={sectionTitleCls}>Providers</h2>
          {data && (
            <span className="text-xs text-secondary">
              {configuredCount} of {data.providers.length} ready
            </span>
          )}
        </div>
        {isLoading && <div className="h-80 animate-pulse rounded bg-panel" aria-hidden="true" />}
        {error && <Notice tone="err" title="Couldn't load providers">{errorMessage(error)}</Notice>}
        {data && data.providers.length > 0 && (
          <div className="rounded-lg border border-border text-sm">
            <div
              aria-hidden="true"
              className="hidden gap-4 bg-panel px-3.5 py-2.5 text-xs font-medium text-secondary sm:flex"
            >
              <span className="w-[28%]">Provider</span>
              <span className="flex-1">Models</span>
              <span className="w-[120px] text-right">Status</span>
              <span className="w-[208px]" />
            </div>
            <ul aria-label="Providers">
              {data.providers.map((p) => {
                const models = data.models.filter((m) => m.provider_id === p.provider_id && m.enabled);
                return (
                  <li
                    key={p.provider_id}
                    className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-2.5 border-t border-border px-3.5 py-3 first:border-t-0 sm:flex sm:items-start sm:first:border-t"
                  >
                    <span className="order-1 inline-flex min-w-0 items-center gap-2.5 font-medium text-ink sm:w-[28%] sm:flex-none">
                      <ProviderLogo providerId={p.provider_id} />
                      {providerLabel(p.provider_id)}
                    </span>
                    <span className="order-3 min-w-0 text-secondary sm:order-2 sm:flex-1">
                      {models.length ? <Details models={models.map((m) => ({ id: m.model_id, roles: m.roles }))} /> : "None enabled"}
                    </span>
                    <span className="order-2 justify-self-end sm:order-3 sm:w-[120px] sm:text-right">
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
                    </span>
                    <span className="order-4 flex flex-wrap items-start justify-end gap-2 sm:w-[208px] sm:flex-nowrap">
                      {ops.data?.available.includes(providerSwitchId(p.provider_id)) && (
                        <PauseControl switchId={providerSwitchId(p.provider_id)} data={ops.data} />
                      )}
                      <button
                        type="button"
                        onClick={() => selectProviderForKey(p.provider_id)}
                        className={btnSm.secondary}
                      >
                        {p.configured ? "Manage key" : "Add key"}
                        <span className="sr-only"> for {providerLabel(p.provider_id)}</span>
                      </button>
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </section>

      <div id="incident-controls" className="scroll-mt-28">
        <IncidentControls />
      </div>
    </div>
  );
}

function Details({ models }: { models: { id: string; roles: string[] }[] }) {
  return (
    <details className="group">
      <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 whitespace-nowrap rounded-full border border-border bg-panel/60 px-2 py-0.5 text-xs font-medium text-body transition-colors hover:border-border-strong hover:bg-panel hover:text-ink [&::-webkit-details-marker]:hidden">
        <span>
          {models.length} model{models.length === 1 ? "" : "s"}
        </span>
        <Icon name="chevron-down" className="h-3.5 w-3.5 text-secondary transition-transform group-open:rotate-180" />
      </summary>
      <ul className="mt-2 space-y-1">
        {models.map((m) => (
          <li key={m.id} className="flex flex-wrap items-center gap-1.5">
            <span className="font-mono text-xs text-ink">{m.id}</span>
            {m.roles.map((r) => (
              <span key={r} className="rounded-sm bg-panel px-1.5 py-0.5 text-2xs text-secondary">
                {r.replaceAll("_", " ")}
              </span>
            ))}
          </li>
        ))}
      </ul>
    </details>
  );
}
