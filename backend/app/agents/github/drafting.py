"""The drafting job: turn findings Patch can fix into proposals for you to approve.

Order of work, cheapest first:
1. Skip anything already drafted from the same input.
2. Fill licenses, .gitignore files and CI workflows from templates. No model.
3. One Claude call writes descriptions and topics for every repository that needs them.
4. One Claude call per repository whose README needs work.

Nothing here writes to GitHub. Every result is a proposal waiting for your decision.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ...core.claude import ClaudeBlocked
from ...core.claude_cli import ClaudeError
from ...core.proposals import Proposal
from ...core.runs import RunContext
from ...core.text import count
from ...events import utcnow
from .client import GitHubClient, GitHubError
from .drafts import ReadmeDraft, SweepDraft, SweepTarget, sweep_items, verify_readme
from .fixes import FileFix, ci_fix, gitignore_fix, license_fix, needs_package_json
from .models import Finding
from .paths import VENDORED_RE, basename
from .prompts import readme_prompt, sweep_prompt
from .store import StoredRepo
from .voice import finding_key

if TYPE_CHECKING:
    from .agent import PatchAgent

BRANCH = "patch/housekeeping"
README_CHECKS = {"readme_missing", "readme_nested", "readme_short", "readme_title", "readme_setup",
                 "readme_stack", "readme_dead_links"}  # fmt: skip
TEMPLATE_CHECKS = {"license", "gitignore", "ci_missing"}
MANIFESTS = ("requirements.txt", "pyproject.toml", "package.json")
FILE_LABEL = {"LICENSE": "a license", ".gitignore": "a .gitignore", ".github/workflows/ci.yml": "a CI workflow"}
# How Patch names a README problem once a draft has fixed it.
README_FIXED = {
    "readme_missing": "the missing README",
    "readme_nested": "the buried README",
    "readme_short": "the too-short README",
    "readme_title": "the missing title",
    "readme_setup": "the missing run instructions",
    "readme_stack": "the missing stack section",
    "readme_dead_links": "the broken links",
}


def draft_key(*parts: str) -> str:
    """A fingerprint of what a draft was made from. The same input is never drafted twice."""
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


def _repos(n: int) -> str:
    return count(n, "repository", "repositories")


@dataclass
class _State:
    """What the job learns as it goes."""

    claude_off: str | None  # why Claude can't be called, once known
    created: list[int] = field(default_factory=list)  # ids of the proposals made


async def run_draft(agent: PatchAgent, ctx: RunContext, client: GitHubClient, force: bool = False) -> list[int]:
    """Returns the ids of the proposals it created."""
    say = agent.persona.line
    audited = agent.store.all()
    if not audited:
        await ctx.fail(say("draft.no_audit"))
        return []

    # Only ordinary projects get drafts. A profile README is yours to write.
    repos = [repo for repo in audited.values() if repo.kind == "project"]
    sweep_targets = []
    for repo in repos:
        needs_description, needs_topics = _has(repo, "description"), _has(repo, "topics")
        if needs_description or needs_topics:
            sweep_targets.append(SweepTarget(repo.snapshot, needs_description, needs_topics))
    file_repos = [repo for repo in repos if _file_findings(repo)]
    fixable = {t.snapshot.meta.full_name for t in sweep_targets} | {r.full_name for r in file_repos}
    if not fixable:
        await ctx.step("plan", say("draft.nothing"))
        ctx.closing_line = say("draft.done_none")
        return []

    await ctx.step("plan", say("draft.start", repos_text=_repos(len(fixable))))
    state = _State(claude_off=await agent.claude.unavailable_reason(refresh=True))
    if state.claude_off:
        await ctx.step("claude", say("draft.claude_off", reason=state.claude_off))

    system = agent.system(await agent.owner_name(client))
    mood = agent.mood()

    if sweep_targets:
        await _draft_sweep(agent, ctx, state, sweep_targets, system, mood, force)
    for repo in sorted(file_repos, key=lambda r: r.full_name.lower()):
        try:
            await _draft_files(agent, ctx, client, state, repo, system, mood, force)
        except GitHubError as exc:
            await ctx.step("files", f"Couldn't read what I needed from GitHub. {exc}", repo=repo.full_name)

    if state.created:
        ctx.closing_line = say(
            "draft.done",
            claude_text=count(ctx.counters["claude_calls"], "model call"),
            proposals_text=count(len(state.created), "proposal"),
        )
    else:
        ctx.closing_line = say("draft.done_none")
    return state.created


def _still_needed(proposal: Proposal, audited: dict[str, StoredRepo]) -> bool:
    """Whether any problem a waiting proposal would fix is still there."""
    if proposal.kind == "pull_request":
        repo = audited.get(proposal.repo or "")
        if repo is None:
            return False  # the repository is gone
        covered = {check for file in proposal.payload.get("files", []) for check in file.get("findings", [])}
        return bool(covered & {f.check for f in _file_findings(repo)})
    if proposal.kind == "metadata_sweep":
        for item in proposal.payload.get("items", []):
            repo = audited.get(item["repo"])
            wants = [check for check in ("description", "topics") if item.get(check)]
            if repo and any(_has(repo, check) for check in wants):
                return True
        return False
    return True


async def settle_fixed(agent: PatchAgent, ctx: RunContext) -> int:
    """Drop waiting proposals whose problems are already gone, because you fixed them on GitHub
    yourself. Rules only. Returns how many were dropped."""
    audited = agent.store.all()
    dropped = 0
    for proposal in agent.proposals.list("pending", agent=agent.id):
        if _still_needed(proposal, audited):
            continue
        agent.proposals.update(proposal.id, status="superseded", result={"fixed_elsewhere": True, "at": utcnow()})
        await ctx.emit(
            "proposal.resolved",
            {
                "proposal_id": proposal.id, "kind": proposal.kind, "title": proposal.title,
                "decision": "fixed", "edited": 0, "text": agent.persona.line("draft.fixed_elsewhere"),
            },
            repo=proposal.repo,
        )  # fmt: skip
        dropped += 1
    return dropped


def _has(repo: StoredRepo, check: str) -> bool:
    return any(f.check == check for f in repo.findings)


def _file_findings(repo: StoredRepo) -> list[Finding]:
    return [f for f in repo.findings if f.fix == "file" and f.check in README_CHECKS | TEMPLATE_CHECKS]


# -- metadata: one call for every repository --------------------------------------------------


async def _draft_sweep(
    agent: PatchAgent, ctx: RunContext, state: _State, targets: list[SweepTarget], system: str, mood: str, force: bool
) -> None:
    say = agent.persona.line
    key = draft_key(
        "sweep",
        *sorted(
            f"{t.snapshot.meta.full_name}:{t.snapshot.meta.fingerprint()}:{t.needs_description}:{t.needs_topics}"
            for t in targets
        ),
    )
    if not force and agent.proposals.settled(agent.id, key):
        await ctx.step("sweep", say("draft.sweep_skipped"))
        return
    if state.claude_off:
        return

    facts = f"{_repos(len(targets))} need a description or topics."
    prompt = sweep_prompt([(t.snapshot, t.needs_description, t.needs_topics) for t in targets], mood, facts)
    try:
        draft = await agent.claude.ask_structured(
            ctx, "sweep", SweepDraft, system=system, prompt=prompt,
            model=agent.settings.claude_model_write, effort=agent.settings.claude_effort_light,
        )  # fmt: skip
    except ClaudeBlocked as exc:
        state.claude_off = str(exc)
        await ctx.step("claude", say("draft.claude_off", reason=state.claude_off))
        return
    except ClaudeError as exc:
        await ctx.step("sweep", say("draft.sweep_failed", reason=str(exc)))
        return

    items, notes = sweep_items(draft, targets)
    if not items:
        await ctx.step("sweep", say("draft.sweep_empty"))
        return

    described = sum(1 for item in items if item.description)
    tagged = sum(1 for item in items if item.topics)
    proposal = agent.proposals.create(
        agent=agent.id,
        kind="metadata_sweep",
        repo=None,
        title="Metadata sweep",
        summary=f"{count(described, 'description')} and topics for {_repos(tagged)}.",
        payload={"items": [{**asdict(item), "enabled": True} for item in items], "notes": notes},
        draft_key=key,
        run_id=ctx.run_id,
    )
    state.created.append(proposal.id)
    ctx.counters["calls_avoided"] += len(targets) - 1  # one call did the work of one per repository
    await ctx.step("sweep", say("draft.sweep", repos_text=_repos(len(items))))
    await _announce(ctx, proposal.id, proposal.kind, proposal.title, proposal.summary, None, len(items))
    await agent.say(ctx, draft.say, fallback=say("say.sweep", repos_text=_repos(len(items))), source=prompt)


# -- files: one pull request per repository ---------------------------------------------------


async def _draft_files(
    agent: PatchAgent,
    ctx: RunContext,
    client: GitHubClient,
    state: _State,
    repo: StoredRepo,
    system: str,
    mood: str,
    force: bool,
) -> None:
    say = agent.persona.line
    snapshot, name = repo.snapshot, repo.full_name
    findings = _file_findings(repo)
    readme_targets = [f for f in findings if f.check in README_CHECKS]
    base = snapshot.details.tree_sha or snapshot.meta.pushed_at or ""

    # Already drafted from exactly this state of the repository? Then there is nothing to do.
    full_key = draft_key("files", name, base, *sorted(finding_key(f) for f in findings))
    if not force and agent.proposals.settled(agent.id, full_key):
        await ctx.step("files", say("draft.skipped"), repo=name)
        return

    files: list[dict[str, Any]] = []
    covered: list[Finding] = []

    # Templates first: they cost nothing.
    checks = {f.check: f for f in findings}
    fixes: list[FileFix] = []
    if "license" in checks:
        fixes.append(license_fix(await agent.license_holder(client), datetime.now().year))
    if "gitignore" in checks:
        fixes.append(gitignore_fix(snapshot))
    if "ci_missing" in checks:
        manifests = {path: await client.get_file_text(name, path) or "" for path in needs_package_json(snapshot)}
        workflow = ci_fix(snapshot, manifests)
        if workflow:
            fixes.append(workflow)
    for fix in fixes:
        files.append(_file_item(fix.path, fix.content, None, [fix.finding], fix.reason, "template"))
        covered.append(checks[fix.finding])
    # Each template file is a model call a naive agent would have made. The proposal that follows
    # says which files came from templates, so no separate step is needed here.
    ctx.counters["calls_avoided"] += len(fixes)

    # Then the README, which needs Claude.
    if readme_targets and not state.claude_off:
        item = await _draft_readme(agent, ctx, client, state, repo, readme_targets, system, mood)
        if item:
            files.append(item)
            covered += readme_targets

    if not files:
        return
    key = draft_key("files", name, base, *sorted(finding_key(f) for f in covered))
    if not force and agent.proposals.settled(agent.id, key):
        return  # the same proposal is already waiting, from a run when Claude wasn't available

    proposal = agent.proposals.create(
        agent=agent.id,
        kind="pull_request",
        repo=name,
        title=pr_title(files),
        summary=_summary(files),
        payload={
            "repo": name,
            "branch": BRANCH,
            "base_branch": snapshot.meta.default_branch,
            "tree_sha": snapshot.details.tree_sha,
            "files": files,
        },
        draft_key=key,
        run_id=ctx.run_id,
    )
    state.created.append(proposal.id)
    await _announce(ctx, proposal.id, proposal.kind, proposal.title, proposal.summary, name, len(files))


async def _draft_readme(
    agent: PatchAgent,
    ctx: RunContext,
    client: GitHubClient,
    state: _State,
    repo: StoredRepo,
    targets: list[Finding],
    system: str,
    mood: str,
) -> dict[str, Any] | None:
    say = agent.persona.line
    snapshot, name = repo.snapshot, repo.full_name
    details = snapshot.details
    previous = details.readme_text if details.readme_path else None

    # The README to work from: the root one, or the one buried in a subfolder.
    current: tuple[str, str] | None = None
    nested = next((f for f in targets if f.check == "readme_nested"), None)
    if nested:
        path = str(nested.data["path"])
        text = await client.get_file_text(name, path)
        current = (path, text) if text else None
    elif details.readme_path and details.readme_text is not None:
        current = (details.readme_path, details.readme_text)

    # A few small files that show how the project is installed and run, so nothing has to be guessed.
    own = [f for f in details.files if not VENDORED_RE.search(f)]
    extra: dict[str, str] = {}
    for manifest in MANIFESTS:
        matches = sorted((f for f in own if basename(f) == manifest), key=lambda f: (f.count("/"), f))
        if matches and (text := await client.get_file_text(name, matches[0])):
            extra[matches[0]] = text

    prompt = readme_prompt(snapshot, targets, current, extra, mood)
    try:
        draft = await agent.claude.ask_structured(
            ctx, "readme", ReadmeDraft, system=system, prompt=prompt,
            model=agent.settings.claude_model_write, effort=agent.settings.claude_effort_write,
        )  # fmt: skip
    except ClaudeBlocked as exc:
        state.claude_off = str(exc)
        await ctx.step("claude", say("draft.claude_off", reason=state.claude_off), repo=name)
        return None
    except ClaudeError as exc:
        await ctx.step("readme", say("draft.readme_failed", reason=str(exc)), repo=name)
        return None

    verdict = verify_readme(snapshot, targets, draft.content, previous)
    if not verdict.ok:
        await ctx.step("readme", say("draft.readme_dropped", problem=verdict.problem), repo=name)
        return None

    fixes_text = _join([README_FIXED.get(check, check) for check in verdict.resolves])
    await ctx.step("readme", say("draft.readme", fixes_text=fixes_text), repo=name)
    await agent.say(ctx, draft.say, fallback=say("say.readme", name=snapshot.meta.name), repo=name, source=prompt)

    summary = " ".join(draft.summary.split())[:240] or "Improves the README."
    item = _file_item("README.md", draft.content.strip() + "\n", previous, verdict.resolves, summary, "claude")
    item["note"] = verdict.note
    return item


# -- wording ----------------------------------------------------------------------------------


def _file_item(
    path: str, content: str, previous: str | None, findings: list[str], reason: str, source: str
) -> dict[str, Any]:
    return {
        "path": path, "content": content, "previous": previous, "findings": findings, "reason": reason,
        "source": source, "enabled": True, "note": None,
    }  # fmt: skip


def _labels(files: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    added = [FILE_LABEL.get(f["path"], f["path"]) for f in files if f["previous"] is None and f["path"] != "README.md"]
    readme = [f for f in files if f["path"] == "README.md"]
    updated = ["the README"] if readme and readme[0]["previous"] is not None else []
    if readme and readme[0]["previous"] is None:
        added.append("a README")
    return added, updated


def pr_title(files: list[dict[str, Any]]) -> str:
    """The pull request's title, from the files it carries. Plain: this goes on GitHub."""
    added, updated = _labels(files)
    if added and updated:
        return f"Add {_join(added)}, and improve {_join(updated)}"
    if added:
        return f"Add {_join(added)}"
    return f"Improve {_join(updated)}"


def _summary(files: list[dict[str, Any]]) -> str:
    """One line for the approvals list, saying where each file came from."""
    templated = [f["path"] for f in files if f["source"] == "template"]
    drafted = [f["path"] for f in files if f["source"] == "claude"]
    parts = []
    if templated:
        parts.append(f"{_join(templated)} from templates.")
    if drafted:
        parts.append(f"{_join(drafted)} drafted by Claude.")
    return " ".join(parts)


def pr_body(files: list[dict[str, Any]], user: str, footer: bool) -> str:
    """The pull request's description. Plain and professional: this goes on GitHub."""
    lines = ["Housekeeping changes from an automated audit of this repository.", ""]
    lines += [f"- `{f['path']}`: {f['reason']}" for f in files if f.get("enabled", True)]
    if footer:
        lines += [
            "",
            f"Drafted by Patch, an automated maintenance agent, and approved by @{user} before this "
            "pull request was opened.",
        ]
    return "\n".join(lines) + "\n"


async def _announce(
    ctx: RunContext, proposal_id: int, kind: str, title: str, summary: str, repo: str | None, items: int
) -> None:
    await ctx.emit(
        "proposal.created",
        {"proposal_id": proposal_id, "kind": kind, "title": title, "summary": summary, "items": items},
        repo=repo,
    )
