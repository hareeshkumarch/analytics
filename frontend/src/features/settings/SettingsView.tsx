"use client";

import React, { useEffect, useMemo, useState } from "react";
import { apiFetch, errorHint, errorMessage } from "@/lib/api/client";
import { RichSelect, RichOption } from "@/components/RichSelect";
import { PageHeader } from "@/components/PageHeader";
import { HScroll } from "@/components/HScroll";
import { Badge, Notice, Spinner, btn, cardCls, cx, inputCls } from "@/components/ui";
import { ProviderLogo } from "@/components/ConnectorLogo";
import { PRESET_OPTIONS, RoutingPreset, useRoutingPolicy } from "@/lib/routing";
import { humanize, useProviders, useWorkspace } from "@/lib/workspace";
import type { SaveProviderKeyRequest } from "@/lib/api/types";

const FALLBACK_OPTIONS: RichOption[] = [
  { value: "on", label: "Fall back on failure", sublabel: "Try the next eligible model when a route fails" },
  { value: "off", label: "No fallback", sublabel: "Fail the step instead of switching models" },
];

type Msg = { text: string; hint?: string | null; ok: boolean };

function parseModels(text: string): string[] {
  return text
    .split(",")
    .map((m) => m.trim())
    .filter(Boolean);
}

export function SettingsView() {
  const ws = useWorkspace();
  const { data, isLoading, error, refetch } = useProviders();
  const routing = useRoutingPolicy(ws.id);

  const [chosenProvider, setChosenProvider] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [saveMsg, setSaveMsg] = useState<Msg | null>(null);
  const [probeMsg, setProbeMsg] = useState<Msg | null>(null);
  const [saving, setSaving] = useState(false);
  const [probing, setProbing] = useState(false);

  const [roleDrafts, setRoleDrafts] = useState<Record<string, string>>({});
  const [ceilingDraft, setCeilingDraft] = useState("");
  const [routingMsg, setRoutingMsg] = useState<Msg | null>(null);

  const providerId = chosenProvider ?? data?.providers.find((p) => !p.configured)?.provider_id ?? data?.providers[0]?.provider_id ?? "";
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

  const savedRoutes = useMemo(
    () => Object.fromEntries(Object.entries(routing.data?.role_routes ?? {}).map(([role, models]) => [role, models.join(", ")])),
    [routing.data]
  );
  const savedCeiling = routing.data?.hard_cost_ceiling_usd ?? "";

  // Load saved values into the form when they actually change on the server. Saving only the preset or
  // fallback leaves routes and the ceiling unchanged, so unsaved edits to those fields are kept.
  const savedRoutesKey = JSON.stringify(savedRoutes);
  const routingLoaded = Boolean(routing.data);
  useEffect(() => {
    if (!routingLoaded) return;
    setRoleDrafts(JSON.parse(savedRoutesKey) as Record<string, string>);
  }, [routingLoaded, savedRoutesKey]);
  useEffect(() => {
    if (!routingLoaded) return;
    setCeilingDraft(savedCeiling);
  }, [routingLoaded, savedCeiling]);

  const changedRoles = routeRoles.filter((role) => (roleDrafts[role] ?? "") !== (savedRoutes[role] ?? ""));
  const ceilingChanged = ceilingDraft.trim() !== savedCeiling;
  const dirty = changedRoles.length > 0 || ceilingChanged;

  const ceilingNumber = Number(ceilingDraft);
  const ceilingError = ceilingDraft.trim() && (!Number.isFinite(ceilingNumber) || ceilingNumber <= 0) ? "Enter an amount above 0, or leave empty." : null;
  const roleErrors: Record<string, string> = {};
  for (const role of changedRoles) {
    const models = parseModels(roleDrafts[role] ?? "");
    const allowed = enabledModelsByRole.get(role) ?? [];
    if (!models.length) roleErrors[role] = "Add at least one model.";
    else {
      const unknown = models.filter((m) => !allowed.includes(m));
      if (unknown.length) roleErrors[role] = `Not enabled for this role: ${unknown.join(", ")}`;
    }
  }
  const canSaveRouting = dirty && !ceilingError && Object.keys(roleErrors).length === 0 && !routing.updating;

  function saveRouting() {
    if (!canSaveRouting) return;
    setRoutingMsg(null);
    const patch: Parameters<typeof routing.update>[0] = {};
    if (changedRoles.length) patch.role_routes = Object.fromEntries(changedRoles.map((r) => [r, parseModels(roleDrafts[r] ?? "")]));
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
    setSaveMsg(null);
    setProbeMsg(null);
    setSaving(true);
    try {
      await apiFetch<{ provider_id: string; status: string }>(ws.path("/settings/provider-key"), {
        method: "PUT",
        body: JSON.stringify({ provider_id: provider.provider_id, api_key: apiKey.trim() } satisfies SaveProviderKeyRequest),
      });
      setApiKey("");
      setSaveMsg({ text: `Key saved for ${humanize(providerId)}. It is encrypted and can't be read back.`, ok: true });
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
          ? { text: `${humanize(res.provider_id)} answered with ${res.model_id ?? "a model"} in ${res.latency_ms ?? "?"} ms.`, ok: true }
          : { text: `${humanize(res.provider_id)}: ${res.status.replaceAll("_", " ")}`, hint: res.message ?? null, ok: false }
      );
    } catch (err) {
      setProbeMsg({ text: errorMessage(err, "The test failed."), hint: errorHint(err), ok: false });
    } finally {
      setProbing(false);
    }
  }

  const providerOptions: RichOption[] = (data?.providers ?? []).map((p) => ({
    value: p.provider_id,
    label: humanize(p.provider_id),
    icon: <ProviderLogo providerId={p.provider_id} />,
    sublabel: p.key_status === "needs_key" ? "Saved key can't be decrypted; save it again" : p.configured ? "Key saved" : "No key yet",
    badge: p.key_status === "needs_key" ? "Needs a key" : p.configured ? "Ready" : undefined,
  }));
  const configuredCount = (data?.providers ?? []).filter((p) => p.configured).length;

  return (
    <div className="space-y-6">
      <PageHeader title="Settings" description="Model provider keys and how questions are routed to models." />

      {data && configuredCount === 0 && (
        <Notice tone="warn" title="No provider key yet">
          Add at least one key below. Questions can&apos;t be answered until a provider is ready.
        </Notice>
      )}

      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
        <form onSubmit={handleSaveKey} className={cx(cardCls, "space-y-4 p-5 sm:p-6")}>
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
              setSaveMsg(null);
              setProbeMsg(null);
            }}
          />
          <div className="space-y-1.5">
            <label htmlFor="provider-api-key" className="block text-[12.5px] font-medium text-body">
              {provider?.configured ? "Replace API key" : "API key"}
            </label>
            <input
              id="provider-api-key"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={provider?.configured ? "Paste a new key to replace the saved one" : "Paste API key"}
              autoComplete="new-password"
              spellCheck={false}
              className={inputCls}
            />
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="submit" disabled={!apiKey.trim() || saving} className={btn.primary}>
              {saving && <Spinner />}
              {saving ? "Saving…" : "Save key"}
            </button>
            <button
              type="button"
              onClick={() => void handleTestProvider()}
              disabled={probing || !providerId || !provider?.configured || Boolean(apiKey.trim())}
              className={btn.secondary}
              title={apiKey.trim() ? "Save the new key first; the test uses the saved key" : undefined}
            >
              {probing && <Spinner />}
              {probing ? "Testing…" : "Test saved key"}
            </button>
          </div>
          {provider && !provider.configured && <p className="text-xs text-secondary">Save a key first, then test it.</p>}
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
            {routeRoles.length > 0 && (
              <div className="space-y-3">
                <div>
                  <h3 className="text-[13.5px] font-semibold text-ink">Model order per role</h3>
                  <p className="mt-1 text-xs text-secondary">Models are tried left to right. Click a model below a field to add it.</p>
                </div>
                {routeRoles.map((role) => {
                  const current = parseModels(roleDrafts[role] ?? "");
                  const suggestions = (enabledModelsByRole.get(role) ?? []).filter((m) => !current.includes(m));
                  const id = `route-${role}`;
                  return (
                    <div key={role} className="space-y-1.5">
                      <label htmlFor={id} className="block text-[12.5px] font-medium text-body">
                        {humanize(role)}
                      </label>
                      <input
                        id={id}
                        value={roleDrafts[role] ?? ""}
                        disabled={!routing.data}
                        onChange={(event) => setRoleDrafts((prev) => ({ ...prev, [role]: event.target.value }))}
                        aria-invalid={Boolean(roleErrors[role])}
                        aria-describedby={roleErrors[role] ? `${id}-err` : undefined}
                        className={cx(inputCls, "min-h-[40px] font-mono text-xs", roleErrors[role] && "border-err")}
                      />
                      {roleErrors[role] && (
                        <p id={`${id}-err`} className="text-xs text-err">
                          {roleErrors[role]}
                        </p>
                      )}
                      {suggestions.length > 0 && (
                        <div className="flex flex-wrap gap-1">
                          {suggestions.map((m) => (
                            <button
                              key={m}
                              type="button"
                              onClick={() => setRoleDrafts((prev) => ({ ...prev, [role]: [...current, m].join(", ") }))}
                              className="min-h-[28px] border border-border px-2 font-mono text-[11px] text-secondary transition-colors hover:border-ink hover:text-ink"
                            >
                              + {m}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
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
                {ceilingError ?? "A run stops when it would spend more than this. Leave empty for no extra limit."}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
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
                  Discard changes
                </button>
              )}
              {dirty && <span className="text-xs text-secondary">Unsaved changes</span>}
            </div>
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
                  <th className="px-3.5 py-2.5 font-medium">Status</th>
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
                          {humanize(p.provider_id)}
                        </span>
                      </td>
                      <td className="px-3.5 py-2.5 text-secondary">
                        {models.length ? (
                          <Details models={models.map((m) => ({ id: m.model_id, roles: m.roles }))} />
                        ) : (
                          "None enabled"
                        )}
                      </td>
                      <td className="px-3.5 py-2.5">
                        {p.configured ? (
                          <Badge tone="ok" dot>
                            Ready
                          </Badge>
                        ) : (
                          <Badge tone="warn" dot>
                            Needs a key
                          </Badge>
                        )}
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
      <summary className="cursor-pointer list-none text-[12.5px] [&::-webkit-details-marker]:hidden">
        {models.length} model{models.length === 1 ? "" : "s"} <span className="text-secondary group-open:hidden">· show</span>
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
