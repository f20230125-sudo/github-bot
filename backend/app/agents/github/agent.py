"""Patch: the agent that looks after your GitHub."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from ...bus import EventBus
from ...config import Settings
from ...core.agent import Job
from ...core.chat import unverified_numbers
from ...core.claude import Claude
from ...core.desk import is_paused
from ...core.memory import MAX_ACTIVE, LessonStore
from ...core.persona import Persona
from ...core.policy import Policy
from ...core.proposals import Proposal, ProposalStore
from ...core.runs import RunContext, run
from ...core.text import count
from ...db import Database
from ...events import NewEvent, utcnow
from ..linkedin import PITCH
from .applying import run_apply
from .chat import run_chat
from .checks import CHECKS_VERSION, build_report
from .client import ALLOWED_WRITES, NEVER, AuthError, GitHubClient, GitHubError, RateLimited, RequestInfo
from .decisions import DecisionError, apply_edits
from .drafting import run_draft
from .handoffs import Handoff, listing_news, repo_news
from .learning import feedback_of, run_learn
from .models import RepoDetails, RepoMeta, RepoReport, RepoSnapshot
from .mood import MOODS, compute_mood
from .prompts import system_prompt
from .store import RepoStore, StoredRepo, portfolio_summary
from .sync import fetch_details, list_repos, plan_sync
from .voice import finding_key, finding_line, resolved_line

if TYPE_CHECKING:
    from ...core.desk import Desk

__all__ = ["PERSONA_PATH", "Listing", "PatchAgent", "portfolio_summary"]

PERSONA_PATH = Path(__file__).parent / "persona.toml"
# "Copyright (c) 2026 Jane Doe" -> "Jane Doe"
COPYRIGHT_RE = re.compile(r"^Copyright \(c\) (?:\d{4}(?:-\d{4})?,? )?(.+)$", re.M | re.I)
LAST_CHECK_KEY = "patch.last_check"
HANDOFFS_KEY = "patch.handoffs"


def _repos(n: int) -> str:
    return count(n, "repository", "repositories")


def _active(meta: RepoMeta) -> bool:
    return not (meta.fork or meta.archived)


@dataclass(frozen=True)
class Listing:
    """The repository list as one check saw it, with the requests that fetched it."""

    metas: list[RepoMeta]
    not_modified: bool
    requests: list[RequestInfo]


class PatchAgent:
    id = "patch"

    def __init__(
        self,
        settings: Settings,
        db: Database,
        bus: EventBus,
        claude: Claude,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings = settings
        self.db = db
        self.bus = bus
        self.claude = claude
        self.persona = Persona.load(PERSONA_PATH)
        self.store = RepoStore(db)
        self.proposals = ProposalStore(db)
        self.lessons = LessonStore(db)
        self.policy = Policy(db, dry_run_default=settings.dry_run)
        self._transport = transport  # tests swap in a fake GitHub

    def jobs(self) -> dict[str, Job]:
        return {"audit": self.audit, "draft": self.draft, "watch": self.watch}

    def system(self, owner: str) -> str:
        """What Claude is told on every call: who Patch is, the rules, and what you have taught it."""
        return system_prompt(self.persona, owner, self.settings.github_user, self.lessons.active_texts(self.id))

    def sheet(self) -> dict[str, Any]:
        """Everything Patch's own page shows: its voice, what it may do, and what it has learned."""
        voice = self.persona.voice
        return {
            **self.card(),
            "persona": {
                "summary": voice.get("summary", ""),
                "rules": voice.get("rules", []),
                "opinions": voice.get("opinions", []),
                "quirks": voice.get("quirks", []),
                "banned": voice.get("banned", []),
                "max_chars": voice.get("max_chars", 240),
                "max_sentences": voice.get("max_sentences", 3),
            },
            "moods": MOODS,
            "writes": [write.what for write in ALLOWED_WRITES],
            "never": list(NEVER),
            "lessons": [asdict(lesson) for lesson in self.lessons.list(self.id)],
            "lesson_limit": MAX_ACTIVE,
        }

    def card(self) -> dict[str, Any]:
        """What the site shows about Patch right now."""
        latest = self.db.latest_of(self.id, "agent.status")
        return {
            "id": self.id,
            "name": self.persona.name,
            "role": self.persona.role,
            "hired": True,
            "mood": self.mood(),
            "status": latest.payload if latest else None,
        }

    # -- your decisions -----------------------------------------------------------------------

    async def approve(self, proposal_id: int, edits: dict[str, Any] | None = None) -> Proposal:
        """Record your approval, with any edits you made. Applying it is a separate step."""
        proposal = self._pending(proposal_id)
        payload, changes = apply_edits(proposal, edits)
        proposal = self.proposals.update(
            proposal_id,
            status="approved",
            payload=payload,
            decision={"action": "approve", "at": utcnow(), "edits": changes},
        )
        line = "decision.approved_dry" if self.policy.dry_run else "decision.approved"
        await self._resolved(proposal, "approved", self.persona.line(line), edited=len(changes))
        return proposal

    async def reject(self, proposal_id: int, reason: str = "") -> Proposal:
        proposal = self._pending(proposal_id)
        proposal = self.proposals.update(
            proposal_id, status="rejected", decision={"action": "reject", "at": utcnow(), "reason": reason.strip()}
        )
        await self._resolved(proposal, "rejected", self.persona.line("decision.rejected"))
        await self.settle()
        return proposal

    def _pending(self, proposal_id: int) -> Proposal:
        proposal = self.proposals.get(proposal_id)
        if proposal is None or proposal.agent != self.id:
            raise LookupError("No such proposal.")
        if proposal.status != "pending":
            raise DecisionError(f"This proposal is already {proposal.status}.")
        return proposal

    async def _resolved(self, proposal: Proposal, decision: str, text: str, edited: int = 0) -> None:
        await self.bus.publish(
            NewEvent(
                agent=self.id,
                type="proposal.resolved",
                repo=proposal.repo,
                payload={
                    "proposal_id": proposal.id, "kind": proposal.kind, "title": proposal.title,
                    "decision": decision, "edited": edited, "text": text,
                },
            )
        )  # fmt: skip

    # -- voice and mood -----------------------------------------------------------------------

    def mood(self) -> str:
        return compute_mood(
            self.store.all().values(),
            self.proposals.list("pending", agent=self.id),
            score_rose=self.db.get_kv("score_rose") == "1",
        )

    async def status(self, status: str, text: str) -> None:
        payload = {"status": status, "text": text, "mood": self.mood()}
        latest = self.db.latest_of(self.id, "agent.status")
        if latest and latest.payload == payload:
            return  # already said
        await self.bus.publish(NewEvent(agent=self.id, type="agent.status", payload=payload))

    async def settle(self) -> None:
        """What Patch is doing once a job ends: paused, waiting on you, or idle."""
        if is_paused(self.db):
            await self.status("paused", self.persona.line("status.paused"))
            return
        pending = len(self.proposals.list("pending", agent=self.id))
        if pending:
            await self.status("waiting", self.persona.line("status.waiting", approvals_text=count(pending, "approval")))
        else:
            await self.status("idle", self.persona.line("status.idle"))

    async def say(
        self, ctx: RunContext, text: str, fallback: str, repo: str | None = None, source: str | None = None
    ) -> None:
        """Post a line in Patch's voice. A line from Claude that breaks the voice rules, or cites a
        number that isn't in `source` (the data Claude was given), is swapped for a template."""
        line = " ".join((text or "").split())
        if not line or self.persona.lint(line) or (source is not None and unverified_numbers(line, source)):
            line = fallback
        await ctx.emit("message", {"kind": "say", "text": line}, repo=repo)

    async def chat(self, text: str, desk: Desk) -> None:
        """Answer a message typed on the site. Rules first; one Claude call only for an open question."""
        await run_chat(self, desk, text)

    async def learn(self, proposal_id: int) -> None:
        """Turn your decision on a proposal into a lesson, if it holds one."""
        if feedback_of(self.proposals.get(proposal_id)) is None:
            return
        await self.status("working", self.persona.line("status.learning"))
        try:
            await run_learn(self, proposal_id)
        finally:
            await self.settle()

    # -- who the repositories belong to -------------------------------------------------------

    async def owner_name(self, client: GitHubClient) -> str:
        """The name on the GitHub profile, looked up once."""
        cached = self.db.get_kv("owner_name")
        if cached:
            return cached
        login = self.settings.github_user
        try:
            name = (await client.get(f"/users/{login}")).data.get("name") or login
        except GitHubError:
            return login
        self.db.set_kv("owner_name", name)
        return name

    async def license_holder(self, client: GitHubClient) -> str:
        """Whose name goes on a new license: your setting, else the name already on one of your
        licenses, else the name on your GitHub profile."""
        if self.settings.license_holder:
            return self.settings.license_holder
        cached = self.db.get_kv("license_holder")
        if cached:
            return cached

        holder = None
        licensed = next((r for r in self.store.all().values() if "LICENSE" in r.snapshot.details.files), None)
        if licensed:
            try:
                text = await client.get_file_text(licensed.full_name, "LICENSE") or ""
            except GitHubError:
                text = ""
            match = COPYRIGHT_RE.search(text)
            holder = match.group(1).strip() if match else None
        holder = holder or await self.owner_name(client)
        self.db.set_kv("license_holder", holder)
        return holder

    # -- jobs ---------------------------------------------------------------------------------

    async def _note_request(self, ctx: RunContext, info: RequestInfo) -> None:
        ctx.counters["github_requests"] += 1
        if info.not_modified:
            ctx.counters["github_not_modified"] += 1
        await ctx.tool(
            "github", method=info.method, path=info.path, status=info.status, ms=info.ms,
            not_modified=info.not_modified, remaining=info.remaining,
        )  # fmt: skip

    def client_for(self, ctx: RunContext) -> GitHubClient:
        """A GitHub client whose every request is counted and shown on the run."""
        client = GitHubClient(self.db, self.settings.github_token, transport=self._transport)

        async def observe(info: RequestInfo) -> None:
            await self._note_request(ctx, info)

        client.observer = observe
        return client

    def _failure(self, exc: GitHubError, failed_line: str) -> str:
        """An expected GitHub failure, in Patch's words."""
        say = self.persona.line
        if isinstance(exc, RateLimited):
            when = datetime.fromtimestamp(exc.reset_at).strftime("%H:%M") if exc.reset_at else "the next hour"
            return say("audit.rate_limited", when=when)
        if isinstance(exc, AuthError):
            return say("audit.bad_token")
        return say(failed_line, reason=str(exc))

    async def _github_job(self, job: str, title: str, status_line: str, work, failed_line: str) -> None:
        """Run a job that talks to GitHub, turning expected failures into a clean end of the run."""
        await self.status("working", status_line)
        try:
            async with run(self.bus, self.id, job, title) as ctx:
                client = self.client_for(ctx)
                try:
                    await work(ctx, client)
                except GitHubError as exc:
                    await ctx.fail(self._failure(exc, failed_line))
                finally:
                    await client.aclose()
        finally:
            await self.settle()

    async def audit(self, force: bool = False, listing: Listing | None = None) -> None:
        """Sync with GitHub, re-check what changed, and report. Uses no model."""
        await self._github_job(
            "audit", "Audit repositories", self.persona.line("status.auditing"),
            lambda ctx, client: self._audit(ctx, client, force, listing), "audit.failed",
        )  # fmt: skip

    async def draft(self, force: bool = False) -> None:
        """Turn the findings Patch can fix into proposals. Templates first, Claude only for the writing."""
        created: list[int] = []

        async def work(ctx: RunContext, client: GitHubClient) -> None:
            created.extend(await run_draft(self, ctx, client, force))

        await self._github_job("draft", "Draft fixes", self.persona.line("status.drafting"), work, "draft.failed")

        # Kinds you set to "auto" are approved without asking. Dry-run still applies to them.
        for proposal_id in created:
            proposal = self.proposals.get(proposal_id)
            if proposal and proposal.status == "pending" and self.policy.mode(proposal.kind) == "auto":
                await self.approve(proposal_id)
                await self.apply(proposal_id)

    async def apply(self, proposal_id: int) -> str:
        """Carry out a proposal you approved, or rehearse it when dry-run is on."""
        outcome = "failed"

        async def work(ctx: RunContext, client: GitHubClient) -> None:
            nonlocal outcome
            outcome = await run_apply(self, ctx, client, proposal_id)

        await self._github_job(
            "apply", "Apply approved changes", self.persona.line("status.applying"), work, "apply.failed"
        )
        if outcome == "applied":
            await self.audit()  # see the result: fixed findings resolve and scores move
        elif outcome == "stale":
            await self.audit()
            await self.draft()  # draft it again from the repository as it is now
        return outcome

    # -- watching -----------------------------------------------------------------------------

    async def watch(self) -> None:
        """The scheduled check. It costs one conditional request, and starts a run only when
        something changed, so an idle desk leaves the feed alone."""
        stored = self.store.all()
        if not stored:
            return  # nothing to compare with yet: the first audit is yours to start

        client = GitHubClient(self.db, self.settings.github_token, transport=self._transport)
        try:
            metas, not_modified = await list_repos(client, self.settings.github_user)
        except GitHubError as exc:
            self._checked(client.history, changed=False, error=self._failure(exc, "audit.failed"))
            return
        finally:
            await client.aclose()

        plan = plan_sync(metas, {name: repo.known() for name, repo in stored.items()})
        rules_changed = self.db.get_kv("checks_version") != str(CHECKS_VERSION)
        if plan.need_details or plan.meta_only or plan.removed or rules_changed:
            await self.audit(listing=Listing(metas, not_modified, client.history))
            return

        # Nothing to audit. If the list came back in full, keep stars fresh and notice a milestone.
        if not not_modified:
            news: list[Handoff] = []
            for meta in metas:
                old = stored[meta.full_name]
                self.store.update_snapshot(RepoSnapshot(meta=meta, details=old.snapshot.details))
                news += listing_news(old, meta)
            await self._hand_off(news)
        self._checked(client.history, changed=False)

    def _checked(
        self, requests: list[RequestInfo], changed: bool, in_run: bool = False, error: str | None = None
    ) -> None:
        """Record a look at GitHub: for the site's "last checked" line, and for the day's totals."""
        unchanged = sum(1 for r in requests if r.not_modified)
        last = requests[-1] if requests else None
        check = {
            "at": utcnow(),
            "changed": changed,
            "requests": len(requests),
            "status": last.status if last else None,
            # With a token, GitHub doesn't count a 304 against the rate limit.
            "free": bool(requests) and unchanged == len(requests) and bool(self.settings.github_token),
            "remaining": last.remaining if last else None,
            "error": error,
        }
        self.db.set_kv(LAST_CHECK_KEY, json.dumps(check))
        stats = {"checks": 1, "idle_checks": 0 if changed or error else 1}
        if not in_run:  # no run will count these requests, so count them here
            stats |= {"github_requests": len(requests), "github_not_modified": unchanged}
        self.db.bump_stats(self.id, stats)
        self.bus.signal("heartbeat", {"agent": self.id, **check})

    def last_check(self) -> dict[str, Any] | None:
        raw = self.db.get_kv(LAST_CHECK_KEY)
        return json.loads(raw) if raw else None

    def seconds_since_check(self) -> float | None:
        check = self.last_check()
        if not check:
            return None
        at = datetime.fromisoformat(check["at"].replace("Z", "+00:00"))
        return (datetime.now(UTC) - at).total_seconds()

    async def _hand_off(self, news: list[Handoff], ctx: RunContext | None = None) -> int:
        """Leave news for the LinkedIn agent. The same news is never left twice. Returns how many went out."""
        sent = set(json.loads(self.db.get_kv(HANDOFFS_KEY) or "[]"))
        fresh = [item for item in news if item.key not in sent]
        for item in fresh:
            payload = {
                "kind": "handoff", "from": self.id, "to": PITCH.id, "topic": item.topic, "thread": item.key,
                "text": self.persona.line(item.line, **item.data), "data": item.data,
            }  # fmt: skip
            if ctx:
                await ctx.emit("message", payload, repo=item.repo)
            else:
                await self.bus.publish(NewEvent(agent=self.id, type="message", repo=item.repo, payload=payload))
            sent.add(item.key)
        if fresh:
            self.db.set_kv(HANDOFFS_KEY, json.dumps(sorted(sent)))
        return len(fresh)

    # -- audit --------------------------------------------------------------------------------

    async def _audit(self, ctx: RunContext, client: GitHubClient, force: bool, listing: Listing | None = None) -> None:
        say = self.persona.line
        if listing is None:
            await ctx.step("sync", say("sync.ask"))
            try:
                metas, not_modified = await list_repos(client, self.settings.github_user)
            except GitHubError as exc:
                self._checked(client.history, changed=False, in_run=True, error=self._failure(exc, "audit.failed"))
                raise
            listing = Listing(metas, not_modified, list(client.history))
        else:
            # The scheduled check already asked GitHub. Its request belongs to this run.
            await ctx.step("sync", say("sync.noticed"))
            for info in listing.requests:
                await self._note_request(ctx, info)
        metas, not_modified = listing.metas, listing.not_modified

        stored = self.store.all()
        first_look = not stored
        before = portfolio_summary(stored)["score"]
        plan = plan_sync(metas, {name: repo.known() for name, repo in stored.items()})
        if force:
            plan.need_details = [m for m in metas if _active(m)]
            plan.meta_only = [m for m in metas if not _active(m)]
            plan.unchanged = []
        rules_changed = bool(stored) and self.db.get_kv("checks_version") != str(CHECKS_VERSION)
        touched = len(plan.need_details) + len(plan.meta_only)
        self._checked(listing.requests, changed=bool(touched or plan.removed or rules_changed), in_run=True)

        if not stored:
            await ctx.step("sync", say("sync.first", repos_text=_repos(len(metas))))
        elif touched:
            await ctx.step("sync", say("sync.changed", changed_text=f"{touched} of {_repos(len(metas))}"))
        elif not plan.removed and not rules_changed:
            free = not_modified and client.authenticated
            await ctx.step("sync", say("sync.unchanged_free" if free else "sync.unchanged"))

        # Look inside only the repositories that changed.
        requests_before = ctx.counters["github_requests"]
        details, _ = await fetch_details(client, plan.need_details)
        if plan.need_details:
            used = ctx.counters["github_requests"] - requests_before
            await ctx.step(
                "details",
                say("sync.details", repos_text=_repos(len(plan.need_details)), requests_text=count(used, "request")),
            )

        news: list[Handoff] = []  # anything worth a post, for the LinkedIn agent
        to_check = [RepoSnapshot(meta=m, details=details[m.full_name]) for m in plan.need_details]
        for meta in plan.meta_only:
            old = stored.get(meta.full_name)
            to_check.append(RepoSnapshot(meta=meta, details=old.snapshot.details if old else RepoDetails()))
        for meta in plan.unchanged:
            old = stored[meta.full_name]
            snapshot = RepoSnapshot(meta=meta, details=old.snapshot.details)
            if rules_changed:
                to_check.append(snapshot)
            elif not not_modified:
                self.store.update_snapshot(snapshot)  # keep stars and issue counts fresh
                news += listing_news(old, meta)
        if rules_changed:
            await ctx.step("rules", say("sync.rechecked"))

        for name in plan.removed:
            self.store.delete(name)
            await ctx.step("removed", say("sync.removed", name=name.split("/")[-1]), repo=name)

        if to_check:
            await ctx.step("checks", say("audit.checks", repos_text=_repos(len(to_check))))
            for snapshot in sorted(to_check, key=lambda s: s.meta.full_name.lower()):
                report = build_report(snapshot)
                old = stored.get(report.full_name)
                await self._publish_report(ctx, report, old)
                self.store.save(report)
                if not first_look:  # the first audit is the baseline: nothing in it is news
                    news += repo_news(old, report)
        self.db.set_kv("checks_version", str(CHECKS_VERSION))

        # Every repository checked by rules is a model call a naive agent would have made.
        ctx.counters["repos_checked"] += len(to_check)
        ctx.counters["repos_skipped"] += len(metas) - len(to_check)
        ctx.counters["calls_avoided"] += len(to_check)

        summary = portfolio_summary(self.store.all())
        if to_check or plan.removed:
            # Mood: a score that went up since the last audit is something to be pleased about.
            rose = before is not None and summary["score"] is not None and summary["score"] > before
            self.db.set_kv("score_rose", "1" if rose else "0")
        if summary["score"] is not None and (to_check or plan.removed):
            key = "audit.summary" if summary["findings"] else "audit.summary_clean"
            await ctx.step(
                "summary",
                say(
                    key,
                    findings_text=count(summary["findings"], "finding"),
                    repos_text=_repos(summary["scored"]),
                    score=summary["score"],
                ),
                score=summary["score"],
            )
        ctx.counters["handoffs"] += await self._hand_off(news, ctx)
        ctx.closing_line = say(
            "audit.done",
            github_text=count(ctx.counters["github_requests"], "GitHub request"),
            claude_text=count(ctx.counters["claude_calls"], "model call"),
        )

    async def _publish_report(self, ctx: RunContext, report: RepoReport, old: StoredRepo | None) -> None:
        """Say what a repository scored, then only what is new or newly fixed since last time."""
        say = self.persona.line
        was = {finding_key(f): f for f in (old.findings if old else [])}
        now = {finding_key(f): f for f in report.findings}
        visible = [f for f in report.findings if f.severity != "info"]

        if report.score is None:
            text = say("audit.repo_unscored")
        elif visible:
            text = say("audit.repo", score=report.score, findings_text=count(len(visible), "finding"))
        else:
            text = say("audit.repo_clean", score=report.score)
        await ctx.step(
            "repo", text, repo=report.full_name, kind="repo", repo_kind=report.kind, score=report.score,
            previous_score=old.score if old else None, counts=dict(Counter(f.severity for f in report.findings)),
        )  # fmt: skip

        for key, finding in now.items():
            if key not in was:
                await ctx.emit(
                    "finding",
                    {
                        "status": "new", "check": finding.check, "severity": finding.severity,
                        "title": finding.title, "detail": finding.detail, "fix": finding.fix,
                        "text": finding_line(self.persona, finding),
                    },
                    repo=report.full_name,
                )  # fmt: skip
        for key, finding in was.items():
            if key not in now:
                await ctx.emit(
                    "finding",
                    {
                        "status": "resolved", "check": finding.check, "severity": finding.severity,
                        "title": finding.title, "text": resolved_line(self.persona, finding),
                    },
                    repo=report.full_name,
                )  # fmt: skip
