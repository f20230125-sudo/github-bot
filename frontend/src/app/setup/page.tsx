"use client";

import { ArrowUpRight, Check } from "lucide-react";
import { useState, type FormEvent } from "react";
import { AuditButton } from "@/components/AuditButton";
import { ClaudePanel } from "@/components/ClaudePanel";
import { SafetyPanel } from "@/components/SafetyPanel";
import { Button, Dot, InlineError, Notice } from "@/components/ui";
import { fetchSetup, removeGithubToken, saveGithubToken } from "@/lib/api";
import { useApi } from "@/lib/useApi";

const TIERS = [
  {
    name: "1. Read",
    when: "Start here",
    permissions: "Contents, Issues, Pull requests, Actions: Read-only",
    can: "Audit and draft everything, private repositories included",
  },
  {
    name: "2. Pull requests",
    when: "When you trust it",
    permissions: "Contents and Pull requests: Read and write. Workflows: Read and write, to add CI files",
    can: "Open housekeeping pull requests for you to merge",
  },
  {
    name: "3. Repository details",
    when: "Optional",
    permissions: "Administration: Read and write",
    can: "Set descriptions, website links and topics",
  },
];

export default function SetupPage() {
  const [version, setVersion] = useState(0);
  const { data, error } = useApi(`setup:${version}`, fetchSetup);
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setFormError(null);
    setSaved(null);
    try {
      const { user } = await saveGithubToken(token.trim());
      setToken("");
      setSaved(user);
      setVersion((v) => v + 1);
    } catch (e) {
      setFormError(e instanceof Error ? e.message : "Couldn't save the token.");
    } finally {
      setBusy(false);
    }
  }

  async function onRemove() {
    setBusy(true);
    setFormError(null);
    setSaved(null);
    try {
      await removeGithubToken();
      setVersion((v) => v + 1);
    } catch (e) {
      setFormError(e instanceof Error ? e.message : "Couldn't remove the token.");
    } finally {
      setBusy(false);
    }
  }

  const github = data?.github;

  return (
    <div className="flex max-w-3xl flex-col gap-8">
      <div>
        <p className="eyebrow">Setup</p>
        <h1 className="mt-2 font-display text-3xl font-semibold tracking-tight sm:text-4xl">
          Connections and limits.
        </h1>
      </div>

      <SafetyPanel />
      <ClaudePanel />

      <h2 className="eyebrow -mb-4">GitHub</h2>
      {error && <Notice tone="error">{error}</Notice>}

      {github && (
        <section className="panel flex flex-wrap items-center justify-between gap-4 p-6" aria-label="Connection">
          <div className="flex items-start gap-3">
            <Dot status={github.configured ? "good" : "neutral"} className="mt-2" />
            <div>
              <p className="font-medium">
                {github.configured ? `Connected as ${github.user}` : `Reading ${github.user}'s public repositories`}
              </p>
              <p className="mt-1 text-sm leading-relaxed text-muted">
                {github.configured
                  ? "Patch uses your token. Unchanged data costs no rate-limit quota."
                  : "No token is set. Patch can audit public repositories, limited to 60 requests an hour, and cannot change anything."}
              </p>
            </div>
          </div>
          {github.configured && (
            <Button onClick={onRemove} disabled={busy}>
              Remove token
            </Button>
          )}
        </section>
      )}

      <section className="panel p-6" aria-label="GitHub token">
        <h2 className="font-display text-xl font-semibold tracking-tight">
          {github?.configured ? "Replace the token" : "Add a token"}
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-muted">
          Patch checks the token with GitHub, then keeps it in <code className="font-mono text-xs">backend/.env</code>{" "}
          on this machine. It is never shown again, never sent to the browser, and never sent to Claude.
        </p>
        <form onSubmit={onSubmit} className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-end">
          <label className="flex min-w-0 flex-1 flex-col gap-2 text-sm">
            Fine-grained personal access token
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="github_pat_..."
              autoComplete="off"
              spellCheck={false}
              required
              minLength={20}
              className="rounded-xl border border-line-strong bg-sunken px-4 py-2.5 font-mono text-sm outline-offset-2 placeholder:text-faint"
            />
          </label>
          <Button variant="primary" type="submit" disabled={busy || token.trim().length < 20}>
            {busy ? "Checking" : "Check and save"}
          </Button>
        </form>
        <div className="mt-4 flex flex-wrap items-center gap-4 empty:hidden">
          {formError && <InlineError>{formError}</InlineError>}
          {saved && (
            <>
              <span className="inline-flex items-center gap-2 text-sm">
                <Check size={15} className="text-good" aria-hidden />
                Saved. Connected as {saved}.
              </span>
              <AuditButton />
            </>
          )}
        </div>
      </section>

      <section aria-label="How to create a token">
        <h2 className="eyebrow mb-3">Creating the token</h2>
        <div className="panel p-6">
          <ol className="flex list-decimal flex-col gap-2 pl-5 text-sm leading-relaxed text-muted">
            <li>
              Open GitHub&apos;s{" "}
              <a
                href="https://github.com/settings/personal-access-tokens/new"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-fg underline underline-offset-4"
              >
                new fine-grained token page
                <ArrowUpRight size={13} aria-hidden />
              </a>
              .
            </li>
            <li>Under Repository access, choose All repositories, or only the ones Patch should manage.</li>
            <li>Under Repository permissions, set the permissions for the tier you want from the table below.</li>
            <li>Generate the token and paste it above.</li>
          </ol>

          <div className="mt-6 overflow-x-auto">
            <table className="w-full min-w-[560px] text-left text-sm">
              <thead>
                <tr className="border-b border-line text-xs text-faint">
                  <th className="py-3 pr-4 font-normal">Tier</th>
                  <th className="py-3 pr-4 font-normal">Repository permissions</th>
                  <th className="py-3 font-normal">Patch can</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line align-top">
                {TIERS.map((tier) => (
                  <tr key={tier.name}>
                    <td className="py-3 pr-4">
                      <p className="font-medium">{tier.name}</p>
                      <p className="text-xs text-faint">{tier.when}</p>
                    </td>
                    <td className="py-3 pr-4 text-muted">{tier.permissions}</td>
                    <td className="py-3 text-muted">{tier.can}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="mt-5 text-sm leading-relaxed text-muted">
            Tier 3 is optional. On GitHub that permission also covers deleting repositories. Patch has no delete
            call and only ever sends a description, a website link and topics. Without it, Patch shows you the text
            to paste yourself.
          </p>
        </div>
      </section>
    </div>
  );
}
