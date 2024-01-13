"""Applying a proposal you approved.

With dry-run on, this rehearses: it checks everything it would check, says what it would do, and
writes nothing. With dry-run off, it makes the changes through the three actions in actions.py.

Before any write it checks that the repository still looks the way it did when the fix was
drafted. If it doesn't, the fix is dropped and drafted again instead of being applied blind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote

from ...core.proposals import Proposal
from ...core.runs import RunContext
from ...core.text import count
from ...events import utcnow
from .actions import GitHubWriter
from .client import AuthError, GitHubClient, GitHubError, PermissionDenied, RateLimited
from .drafting import pr_body, pr_title
from .sync import list_repos

if TYPE_CHECKING:
    from .agent import PatchAgent

Outcome = Literal["applied", "dry_run", "stale", "failed"]


@dataclass
class _Log:
    """What was done, or would be done, one entry per action. Stored on the proposal and shown on the run."""

    ctx: RunContext
    dry: bool
    actions: list[dict[str, Any]] = field(default_factory=list)

    async def add(self, repo: str, action: str, text: str, ok: bool = True, **extra: Any) -> None:
        entry = {"repo": repo, "action": action, "ok": ok, "dry_run": self.dry, "text": text, **extra}
        self.actions.append(entry)
        await self.ctx.emit("action.applied", entry, repo=repo)

    def done(self) -> list[dict[str, Any]]:
        return [a for a in self.actions if a["ok"]]

    def failed(self) -> list[dict[str, Any]]:
        return [a for a in self.actions if not a["ok"] and a["action"] != "stale"]

    def stale(self) -> list[dict[str, Any]]:
        return [a for a in self.actions if a["action"] == "stale"]


async def run_apply(agent: PatchAgent, ctx: RunContext, client: GitHubClient, proposal_id: int) -> Outcome:
    say = agent.persona.line
    proposal = agent.proposals.get(proposal_id)
    if proposal is None or proposal.status != "approved":
        await ctx.fail(say("apply.gone"))
        return "failed"

    policy = agent.policy.get()
    if policy["kinds"].get(proposal.kind) == "never":
        await ctx.fail(say("apply.never"))
        return "failed"
    dry = bool(policy["dry_run"])
    if not dry and not client.authenticated:
        await ctx.fail(say("apply.no_token"))
        return "failed"

    log = _Log(ctx, dry)
    writer = GitHubWriter(client, delay=agent.settings.github_write_delay)
    try:
        if proposal.kind == "metadata_sweep":
            await _apply_sweep(agent, client, writer, log, proposal)
        else:
            await _apply_pull_request(agent, client, writer, log, proposal, policy["merge_after_approval"])
    except (RateLimited, AuthError):
        # Keep what was done so far. The proposal stays approved, so it can be applied again.
        agent.proposals.update(proposal.id, result=_result(log))
        raise
    return _finish(agent, ctx, log, proposal)


def _result(log: _Log, **extra: Any) -> dict[str, Any]:
    return {"dry_run": log.dry, "at": utcnow(), "actions": log.actions, **extra}


def _finish(agent: PatchAgent, ctx: RunContext, log: _Log, proposal: Proposal) -> Outcome:
    say = agent.persona.line
    done, failed, stale = log.done(), log.failed(), log.stale()
    pull = next((a for a in done if a["action"] == "pull_request"), None)
    extra = {"url": pull["url"], "number": pull["number"]} if pull and not log.dry else {}

    if log.dry:
        agent.proposals.update(proposal.id, result=_result(log))
        if stale and not done:
            ctx.closing_line = say("apply.done_none")
        else:
            ctx.closing_line = say("apply.done_dry", actions_text=count(len(done), "change"))
        return "dry_run"

    if done:
        agent.proposals.update(proposal.id, status="applied", result=_result(log, **extra))
        if failed or stale:
            ctx.closing_line = say(
                "apply.done_partial",
                done_text=count(len(done), "change"),
                failed_text=count(len(failed) + len(stale), "other"),
            )
        else:
            ctx.closing_line = say("apply.done", done_text=count(len(done), "change"))
        return "applied"

    if stale and not failed:
        agent.proposals.update(proposal.id, status="superseded", result=_result(log, stale=True))
        ctx.closing_line = say("apply.done_none")
        return "stale"

    agent.proposals.update(proposal.id, status="failed", result=_result(log))
    ctx.failed = True
    ctx.closing_line = say("apply.done_none")
    return "failed"


# -- descriptions and topics ------------------------------------------------------------------


async def _apply_sweep(
    agent: PatchAgent, client: GitHubClient, writer: GitHubWriter, log: _Log, proposal: Proposal
) -> None:
    say = agent.persona.line
    items = [item for item in proposal.payload["items"] if item.get("enabled", True)]
    if not items:
        await log.ctx.step("apply", say("apply.nothing"))
        return

    # One request tells us whether any of these repositories changed since the draft.
    metas, _ = await list_repos(client, agent.settings.github_user)
    current = {meta.full_name: meta for meta in metas}

    for item in items:
        repo = item["repo"]
        meta = current.get(repo)
        if meta is None or meta.fingerprint() != item["meta_fp"]:
            await log.add(repo, "stale", say("apply.stale"), ok=False)
            continue
        if item.get("description"):
            await _write_field(agent, writer, log, repo, "description", item["description"])
        if item.get("topics"):
            await _write_field(agent, writer, log, repo, "topics", item["topics"])


async def _write_field(
    agent: PatchAgent, writer: GitHubWriter, log: _Log, repo: str, name: str, value: Any
) -> None:
    say = agent.persona.line
    described = {"topics_text": count(len(value), "topic")} if name == "topics" else {}
    if log.dry:
        await log.add(repo, name, say(f"apply.would_{name}", **described), value=value)
        return
    try:
        if name == "description":
            await writer.set_description(repo, value)
        else:
            await writer.set_topics(repo, value)
    except PermissionDenied as exc:
        # Kept with its value, so the site can offer it to you to paste in yourself.
        await log.add(repo, name, say("apply.denied", needed=exc.needed), ok=False, value=value, needed=exc.needed)
    except (RateLimited, AuthError):
        raise
    except GitHubError as exc:
        await log.add(repo, name, say("apply.failed", reason=str(exc)), ok=False, value=value)
    else:
        await log.add(repo, name, say(f"apply.{name}", **described), value=value)


# -- pull requests ----------------------------------------------------------------------------


async def _apply_pull_request(
    agent: PatchAgent, client: GitHubClient, writer: GitHubWriter, log: _Log, proposal: Proposal, merge: bool
) -> None:
    say = agent.persona.line
    payload = proposal.payload
    repo, base_branch = payload["repo"], payload["base_branch"]
    files = [f for f in payload["files"] if f.get("enabled", True)]
    if not files:
        await log.ctx.step("apply", say("apply.nothing"), repo=repo)
        return

    # Does the default branch still hold the same files as when this was drafted?
    commit = None
    if log.dry:
        tree = (await client.get(f"/repos/{repo}/git/trees/{quote(base_branch, safe='')}")).data.get("sha")
    else:
        commit, tree = await writer.head(repo, base_branch)
    if payload.get("tree_sha") and tree != payload["tree_sha"]:
        await log.add(repo, "stale", say("apply.stale_pr"), ok=False)
        return

    paths = [f["path"] for f in files]
    if log.dry:
        await log.add(repo, "pull_request", say("apply.would_pr", files_text=count(len(files), "file")), files=paths)
        return

    try:
        pull = await writer.open_pull_request(
            repo,
            base_branch=base_branch,
            base_commit=commit,
            base_tree=tree,
            branch=f"{payload['branch']}-{proposal.id}",
            title=pr_title(files),
            body=pr_body(files, agent.settings.github_user, agent.settings.pr_footer),
            files=[(f["path"], f["content"]) for f in files],
        )
    except PermissionDenied as exc:
        await log.add(repo, "pull_request", say("apply.denied", needed=exc.needed), ok=False, needed=exc.needed)
        return
    except (RateLimited, AuthError):
        raise
    except GitHubError as exc:
        await log.add(repo, "pull_request", say("apply.failed", reason=str(exc)), ok=False)
        return

    await log.add(
        repo, "pull_request", say("apply.pr", number=f"#{pull.number}"), url=pull.url, number=pull.number,
        branch=pull.branch, files=paths,
    )  # fmt: skip

    if merge:
        try:
            await writer.merge(repo, pull.number)
        except (RateLimited, AuthError):
            raise
        except GitHubError as exc:
            text = say("apply.merge_failed", number=f"#{pull.number}", reason=str(exc))
            await log.add(repo, "merge", text, ok=False)
        else:
            await log.add(repo, "merge", say("apply.merged", number=f"#{pull.number}"))
