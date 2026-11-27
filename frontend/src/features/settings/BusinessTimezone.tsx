"use client";

import React, { useId, useState } from "react";
import { errorMessage } from "@/lib/api/client";
import { Badge, Notice, cardCls, cx, sectionTitleCls } from "@/components/ui";
import { RichSelect } from "@/components/RichSelect";
import { timeZoneOptions, useWorkspaceSettings } from "@/lib/workspaceSettings";

export function BusinessTimezone({ workspaceId }: { workspaceId: string }) {
  const settings = useWorkspaceSettings(workspaceId);
  const [error, setError] = useState<string | null>(null);
  const id = useId();
  const current = settings.data?.business_timezone;
  return (
    <section aria-labelledby={`${id}-title`} className={cx(cardCls, "space-y-4 p-5 sm:p-6")}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 id={`${id}-title`} className={sectionTitleCls}>
            Business time zone
          </h2>
          <p className="mt-1 text-xs text-secondary">
            Dates in questions, such as &ldquo;October&rdquo;, start and end at midnight in this zone. Timestamps stored with a zone are
            converted to it; timestamps without one are read as already local.
          </p>
        </div>
        {settings.saving && <Badge>Saving…</Badge>}
      </div>
      <RichSelect
        label="Time zone"
        searchable
        value={current ?? ""}
        placeholder={settings.isLoading ? "Loading…" : "Unavailable"}
        disabled={!settings.data || settings.saving}
        options={settings.data ? timeZoneOptions(current).map((zone) => ({ value: zone, label: zone.replaceAll("_", " ") })) : []}
        onChange={(zone) => {
          setError(null);
          settings.save({ business_timezone: zone }, { onError: (err) => setError(errorMessage(err, "Couldn't save the time zone.")) });
        }}
      />
      {error && <Notice tone="err" title={error} />}
      {settings.error && <Notice tone="err" title="Couldn't load workspace settings">{errorMessage(settings.error)}</Notice>}
    </section>
  );
}
