"""Patch's side of a conversation.

Cheapest first:
1. A command (/audit, /pause, ...) is carried out. No model.
2. A lookup ("what's wrong with <repository>?") is answered from the last audit. No model.
3. An open question gets one Claude call with the stored data attached. If Claude asks to read
   a file or two, they are fetched and it gets one more call. That is the only follow-up round.

Claude's answer is checked before you see it: the voice rules apply, and a number that isn't in
the data it was given is treated as made up.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ...core.chat import (
    MAX_FILES,
    MAX_POINTS,
    POINT_MAX_CHARS,
    ChatAnswer,
    FileRequest,
    Reply,
    parse_command,
    plain,
    unverified_numbers,
)
from ...core.claude import ClaudeBlocked
from ...core.claude_cli import ClaudeError
from ...core.jobs import Paused
from ...core.runs import RunContext, run
from ...core.text import count
from .client import GitHubError
from .prompts import FILE_EXCERPT, chat_prompt, repo_block
from .store import StoredRepo, portfolio_summary
from .voice import finding_line

if TYPE_CHECKING:
    from ...core.desk import Desk
    from .agent import PatchAgent

MAX_MENTIONED = 3
HISTORY_MESSAGES = 6
HISTORY_CHARS = 300
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
# A message made of a repository's name and nothing but these words is a lookup, not a question.
LOOKUP_WORDS = frozenset(
    "what whats is wrong with how about the score scores status of for on in finding findings problem problems "
    "issue issues show me check and doing tell repo repository".split()
)
HELP = [
    "/audit: ask GitHub what changed and re-run the health checks. No model call.",
    "/draft: write fixes for what the audit found. Claude is used for the writing only.",
    "/status, /approvals, /usage: answered from stored data.",
    "/pause and /resume: stop everything, and start again.",
    "Anything else is a question: one Claude call, while your plan usage is under the stop.",
]


def _repos(n: int) -> str:
    return count(n, "repository", "repositories")


async def run_chat(agent: PatchAgent, desk: Desk, text: str) -> None:
    say = agent.persona.line
    text = " ".join(text.split())
    async with run(agent.bus, agent.id, "chat", "Chat") as ctx:
        await ctx.emit("message", {"kind": "chat", "from": "you", "to": agent.id, "text": text})
        try:
            reply = await _answer(agent, desk, ctx, text)
        except asyncio.CancelledError:
            await _post(agent, ctx, Reply(say("chat.stopped")))
            raise
        await _post(agent, ctx, reply)
        calls = ctx.counters["claude_calls"]
        if calls:
            ctx.closing_line = say("chat.done_claude", claude_text=count(calls, "model call"))
        else:
            ctx.closing_line = say("chat.done_rules")


async def _post(agent: PatchAgent, ctx: RunContext, reply: Reply) -> None:
    await ctx.emit(
        "message",
        {
            "kind": "chat", "from": agent.id, "to": "you", "text": reply.text, "points": reply.points,
            "source": reply.source, "note": reply.note,
        },
    )  # fmt: skip


async def _answer(agent: PatchAgent, desk: Desk, ctx: RunContext, text: str) -> Reply:
    say = agent.persona.line
    command = parse_command(text)
    if command:
        if not text.startswith("/"):
            ctx.counters["calls_avoided"] += 1  # plain words, understood without a model
        return await _command(agent, desk, *command)

    stored = agent.store.all()
    if not stored:
        return Reply(say("chat.status_empty"))
    mentioned, rest = find_mentions(text, stored)
    if mentioned and set(rest.split()) <= LOOKUP_WORDS:
        ctx.counters["calls_avoided"] += 1
        return _repos_reply(agent, mentioned)

    reason = "The desk is paused." if desk.paused else await agent.claude.unavailable_reason(refresh=True)
    if reason is None:
        try:
            reply = await _ask_claude(agent, ctx, text, stored, mentioned)
        except ClaudeBlocked as exc:
            reason = str(exc)
        except ClaudeError as exc:
            reason = f"The call failed. {exc}"
        else:
            if reply:
                return reply
            return _from_data(agent, stored, mentioned, say("chat.unusable"))
    return _from_data(agent, stored, mentioned, say("chat.claude_off", reason=reason))


# -- commands ---------------------------------------------------------------------------------


async def _command(agent: PatchAgent, desk: Desk, name: str, args: list[str]) -> Reply:
    say = agent.persona.line
    if name in ("audit", "draft"):
        force = "force" in args
        job = agent.audit if name == "audit" else agent.draft
        try:
            queued = desk.jobs.submit(f"{agent.id}.{name}", lambda: job(force=force))
        except Paused:
            return Reply(say("chat.refused_paused"))
        return Reply(say(f"chat.queued_{name}" if queued else "chat.already_queued"))
    if name == "pause":
        return Reply(say("chat.paused" if await desk.pause() else "chat.already_paused"))
    if name == "resume":
        return Reply(say("chat.resumed" if await desk.resume() else "chat.not_paused"))
    if name == "status":
        return _status_reply(agent, agent.store.all())
    if name == "approvals":
        return _approvals_reply(agent)
    if name == "usage":
        return _usage_reply(agent)
    return Reply(say("chat.help"), HELP)


# -- answers from stored data -----------------------------------------------------------------


def find_mentions(text: str, stored: dict[str, StoredRepo]) -> tuple[list[StoredRepo], str]:
    """Repositories named in a message, and the words left once their names are taken out."""
    rest = f" {plain(text)} "
    found: list[StoredRepo] = []
    # Longest names first, so "quant copilot ui" isn't taken for "quant copilot".
    for repo in sorted(stored.values(), key=lambda r: -len(r.snapshot.meta.name)):
        meta = repo.snapshot.meta
        for name in (plain(meta.full_name), plain(meta.name)):
            if name and f" {name} " in rest:
                found.append(repo)
                rest = rest.replace(f" {name} ", "  ")
                break
    return found[:MAX_MENTIONED], " ".join(rest.split())


def _repo_line(agent: PatchAgent, repo: StoredRepo) -> str:
    say = agent.persona.line
    name = repo.snapshot.meta.name
    visible = [f for f in repo.findings if f.severity != "info"]
    if repo.score is None:
        return say("chat.repo_unscored", name=name)
    if visible:
        return say("chat.repo", name=name, score=repo.score, findings_text=count(len(visible), "finding"))
    return say("chat.repo_clean", name=name, score=repo.score)


def _repos_reply(agent: PatchAgent, repos: list[StoredRepo]) -> Reply:
    if len(repos) > 1:
        return Reply(
            agent.persona.line("chat.several", repos_text=_repos(len(repos))), [_repo_line(agent, r) for r in repos]
        )
    repo = repos[0]
    ranked = sorted(repo.findings, key=lambda f: (SEVERITY_ORDER[f.severity], -f.weight))
    return Reply(_repo_line(agent, repo), [finding_line(agent.persona, f) for f in ranked[:MAX_POINTS]])


def _status_reply(agent: PatchAgent, stored: dict[str, StoredRepo]) -> Reply:
    say = agent.persona.line
    summary = portfolio_summary(stored)
    if summary["score"] is None:
        return Reply(say("chat.status_empty"))
    key = "chat.status" if summary["findings"] else "chat.status_clean"
    text = say(
        key, score=summary["score"], findings_text=count(summary["findings"], "finding"),
        repos_text=_repos(summary["scored"]),
    )  # fmt: skip
    points = []
    lowest = sorted((r for r in stored.values() if r.score is not None), key=lambda r: r.score)[:3]
    if summary["findings"] and lowest:
        points.append(say("chat.lowest", names=", ".join(f"{r.snapshot.meta.name} ({r.score})" for r in lowest)))
    pending = len(agent.proposals.list("pending", agent=agent.id))
    if pending:
        points.append(say("chat.approvals", approvals_text=count(pending, "approval")))
    return Reply(text, points)


def _approvals_reply(agent: PatchAgent) -> Reply:
    say = agent.persona.line
    pending = agent.proposals.list("pending", agent=agent.id)
    if not pending:
        return Reply(say("chat.approvals_none"))
    points = [f"{p.title} ({p.repo.split('/')[-1]})" if p.repo else p.title for p in pending[:MAX_POINTS]]
    return Reply(say("chat.approvals", approvals_text=count(len(pending), "approval")), points)


def _usage_reply(agent: PatchAgent) -> Reply:
    snapshot = agent.claude.guard.snapshot()
    points = []
    for window, label in (("session", "5-hour limit"), ("weekly", "Weekly limit")):
        percent, limit = snapshot["windows"][window]["percent"], snapshot["limits"][window]
        used = "no reading" if percent is None else f"{percent:.0f}% used"
        points.append(f"{label}: {used}. I stop at {limit:.0f}%.")
    return Reply(snapshot["reason"], points)


def _from_data(agent: PatchAgent, stored: dict[str, StoredRepo], mentioned: list[StoredRepo], note: str) -> Reply:
    """What the stored data can say when Claude isn't available, with the reason attached."""
    base = _repos_reply(agent, mentioned) if mentioned else _status_reply(agent, stored)
    return Reply(base.text, base.points, note=note)


# -- one Claude call, with the data attached --------------------------------------------------


def _portfolio(agent: PatchAgent, stored: dict[str, StoredRepo]) -> str:
    summary = portfolio_summary(stored)
    pending = agent.proposals.list("pending", agent=agent.id)
    lines = [
        f"portfolio score: {summary['score'] if summary['score'] is not None else 'none yet'}",
        f"repositories: {summary['repos']}, scored: {summary['scored']}, findings: {summary['findings']}",
        f"proposals waiting for approval: {len(pending)}",
        "each repository: name | kind | score | what the checks found",
    ]
    for repo in stored.values():
        titles = "; ".join(f.title for f in repo.findings if f.severity != "info") or "nothing"
        score = repo.score if repo.score is not None else "not scored"
        lines.append(f"- {repo.snapshot.meta.name} | {repo.kind} | {score} | {titles}")
    return "\n".join(lines)


def _conversation(agent: PatchAgent) -> list[tuple[str, str]]:
    """The last few messages, so "and the other one?" means something. The newest one, the message
    being answered, is left out: it goes in the prompt by itself."""
    earlier = agent.db.messages("chat", HISTORY_MESSAGES + 1)[:-1]
    return [(str(ev.payload.get("from")), str(ev.payload.get("text", ""))[:HISTORY_CHARS]) for ev in earlier]


def _wanted_files(requests: list[FileRequest], stored: dict[str, StoredRepo]) -> list[tuple[str, str]]:
    """The files Claude asked for that really exist, as (repository, path)."""
    by_name = {name.lower(): repo for name, repo in stored.items()}
    by_name |= {repo.snapshot.meta.name.lower(): repo for repo in stored.values()}
    wanted = []
    for request in requests[:MAX_FILES]:
        repo = by_name.get(request.repo.strip().lower())
        path = request.path.strip().lstrip("/")
        if repo and path in repo.snapshot.details.files:
            wanted.append((repo.full_name, path))
    return wanted


async def _read_files(agent: PatchAgent, ctx: RunContext, wanted: list[tuple[str, str]]) -> list[tuple[str, str, str]]:
    files = []
    client = agent.client_for(ctx)
    try:
        for full_name, path in wanted:
            text = await client.get_file_text(full_name, path)
            if text:
                files.append((full_name, path, text[:FILE_EXCERPT]))
    except GitHubError:
        pass  # answer with whatever was read
    finally:
        await client.aclose()
    return files


async def _ask_claude(
    agent: PatchAgent, ctx: RunContext, text: str, stored: dict[str, StoredRepo], mentioned: list[StoredRepo]
) -> Reply | None:
    """Returns None when Claude's answer can't be used."""
    say = agent.persona.line
    settings = agent.settings
    owner = agent.db.get_kv("owner_name") or settings.github_user
    system = agent.system(owner)

    def prompt(files: list[tuple[str, str, str]] | None = None) -> str:
        blocks = [repo_block(repo.snapshot, repo.findings, repo.score) for repo in mentioned]
        return chat_prompt(owner, text, _conversation(agent), _portfolio(agent, stored), blocks, agent.mood(), files)

    async def ask(task: str) -> ChatAnswer:
        return await agent.claude.ask_structured(
            ctx, "chat", ChatAnswer, system=system, prompt=task,
            model=settings.claude_model_write, effort=settings.claude_effort_light,
        )  # fmt: skip

    await agent.status("working", say("status.thinking"))
    try:
        task = prompt()
        answer = await ask(task)
        wanted = _wanted_files(answer.need_files, stored)
        if wanted:
            files = await _read_files(agent, ctx, wanted)
            if files:
                await ctx.step("files", say("chat.read_files", files_text=count(len(files), "file")))
                task = prompt(files)
                answer = await ask(task)
        return _checked(agent, answer, task)
    finally:
        await agent.settle()


def _checked(agent: PatchAgent, answer: ChatAnswer, source: str) -> Reply | None:
    """Keep what passes the voice rules and cites only numbers found in the data it was given."""

    def passes(line: str) -> bool:
        return bool(line) and not agent.persona.lint(line) and not unverified_numbers(line, source)

    text = " ".join(answer.say.split())
    points = [" ".join(point.split()) for point in answer.points]
    points = [p for p in points if len(p) <= POINT_MAX_CHARS and passes(p)][:MAX_POINTS]
    if not passes(text):
        if not points:
            return None
        text = agent.persona.line("chat.points_only")
    return Reply(text, points, source="claude")
