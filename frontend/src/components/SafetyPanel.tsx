"use client";

import { useState } from "react";
import { fetchPolicy, updatePolicy } from "@/lib/api";
import type { Policy, PolicyMode } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { InlineError } from "./ui";

const MODE_LABEL: Record<PolicyMode, string> = {
  ask: "Ask me first",
  auto: "Approve automatically",
  never: "Never",
};

function Switch({
  label,
  hint,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  hint: string;
  checked: boolean;
  disabled: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex items-start gap-3 py-3">
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-1 size-4 accent-[var(--fg)]"
      />
      <span>
        <span className="block font-medium">{label}</span>
        <span className="mt-1 block text-sm leading-relaxed text-muted">{hint}</span>
      </span>
    </label>
  );
}

/** What Patch may do on GitHub. Everything starts as cautious as it can be. */
export function SafetyPanel() {
  const [version, setVersion] = useState(0);
  const { data, error } = useApi(`policy:${version}`, fetchPolicy);
  const [saved, setSaved] = useState<Policy | null>(null);
  const [busy, setBusy] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const policy = saved ?? data;

  async function change(changes: Parameters<typeof updatePolicy>[0]) {
    setBusy(true);
    setSaveError(null);
    try {
      setSaved(await updatePolicy(changes));
      setVersion((v) => v + 1);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Couldn't save that.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel flex flex-col gap-2 p-6" aria-label="Safety">
      <h2 className="font-display text-xl font-semibold tracking-tight">What Patch may do</h2>
      {error && <InlineError>{error}</InlineError>}
      {saveError && <InlineError>{saveError}</InlineError>}

      {policy && (
        <div className="divide-y divide-line">
          <Switch
            label="Dry-run"
            hint="While this is on, approving a proposal only rehearses it. Nothing is written to GitHub."
            checked={policy.dry_run}
            disabled={busy}
            onChange={(dry_run) => change({ dry_run })}
          />
          <Switch
            label="Merge pull requests after I approve them"
            hint="Off: Patch opens the pull request and you merge it on GitHub. On: your approval here also merges it."
            checked={policy.merge_after_approval}
            disabled={busy}
            onChange={(merge_after_approval) => change({ merge_after_approval })}
          />
          {Object.entries(policy.kinds).map(([kind, mode]) => (
            <label key={kind} className="flex flex-wrap items-center justify-between gap-3 py-3">
              <span className="font-medium">{policy.kind_labels[kind] ?? kind}</span>
              <select
                value={mode}
                disabled={busy}
                onChange={(e) => change({ kinds: { [kind]: e.target.value as PolicyMode } })}
                className="rounded-xl border border-line-strong bg-sunken px-3 py-2 text-sm"
              >
                {(Object.keys(MODE_LABEL) as PolicyMode[]).map((option) => (
                  <option key={option} value={option}>
                    {MODE_LABEL[option]}
                  </option>
                ))}
              </select>
            </label>
          ))}
        </div>
      )}
    </section>
  );
}
