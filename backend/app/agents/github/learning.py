"""Learning from a decision: a rejection with a reason, or an approval where you changed the draft.

One call on the small model turns the feedback into at most one rule. Without Claude, the reason
you gave for a rejection is kept as you wrote it, and an edit is left alone. Either way the
decision itself stays stored with the proposal.
"""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING, Any

from ...core.claude import ClaudeBlocked
from ...core.claude_cli import ClaudeError
from ...core.memory import LESSON_SYSTEM, Lesson, LessonDraft, clean_lesson, lesson_prompt
from ...core.proposals import Proposal
from ...core.runs import run
from ...core.text import count

if TYPE_CHECKING:
    from .agent import PatchAgent

FEEDBACK_CHARS = 3000
DIFF_LINES = 40


def feedback_of(proposal: Proposal | None) -> tuple[str, str] | None:
    """What a decision says about the draft, as (what you did, the feedback). None if it says nothing."""
    decision = (proposal.decision if proposal else None) or {}
    if decision.get("action") == "reject":
        reason = " ".join(str(decision.get("reason") or "").split())
        return ("rejected", f"reason: {reason}") if reason else None
    # Switching an item off says what you didn't want this time, not how to write next time.
    edits = [edit for edit in decision.get("edits") or [] if edit.get("field") != "enabled"]
    if decision.get("action") == "approve" and edits:
        return "edited before approving", "\n".join(_describe(edit) for edit in edits)[:FEEDBACK_CHARS]
    return None


def _describe(edit: dict[str, Any]) -> str:
    where = str(edit.get("repo") or edit.get("path") or "").split("/")[-1]
    before, after = edit.get("from"), edit.get("to")
    if edit["field"] == "content":
        diff = list(
            difflib.unified_diff(str(before).splitlines(), str(after).splitlines(), lineterm="", n=0)
        )[2:]  # without the two file-name lines
        return f"{where}: the file was changed like this (- drafted, + your version):\n" + "\n".join(diff[:DIFF_LINES])
    if edit["field"] == "topics":
        return f"{where} topics: drafted {before}, you set {after}"
    return f'{where} description: drafted "{before}", you wrote "{after}"'


async def run_learn(agent: PatchAgent, proposal_id: int) -> Lesson | None:
    """Returns the lesson learned, if there was one."""
    say = agent.persona.line
    proposal = agent.proposals.get(proposal_id)
    found = feedback_of(proposal)
    if proposal is None or found is None:
        return None  # nothing to learn from: no run and no call
    action, feedback = found
    rejected = action == "rejected"

    async with run(agent.bus, agent.id, "learn", "Learn from your decision") as ctx:
        await ctx.step("feedback", say("learn.rejected" if rejected else "learn.edited"), repo=proposal.repo)

        text: str | None = None
        off = await agent.claude.unavailable_reason()
        if off is None:
            owner = agent.db.get_kv("owner_name") or agent.settings.github_user
            known = agent.lessons.active_texts(agent.id)
            try:
                draft = await agent.claude.ask_structured(
                    ctx, "lesson", LessonDraft, system=LESSON_SYSTEM,
                    prompt=lesson_prompt(owner, action, proposal.title, feedback, known),
                    model=agent.settings.claude_model_small,
                )  # fmt: skip
            except ClaudeBlocked as exc:
                off = str(exc)
            except ClaudeError as exc:
                off = f"The call failed. {exc}"
            else:
                if not (draft.lesson or "").strip():
                    ctx.closing_line = say("learn.nothing")
                    return None
                text = draft.lesson

        if off is not None:
            await ctx.step("claude", say("draft.claude_off", reason=off))
            if not rejected:
                ctx.closing_line = say("learn.kept_edit")
                return None
            text = feedback.removeprefix("reason: ")  # your own words, kept as you wrote them

        if clean_lesson(text) is None:
            ctx.closing_line = say("learn.unusable")
            return None
        lesson = agent.lessons.add(agent.id, text, "rejection" if rejected else "edit", proposal_id)
        if lesson is None:
            ctx.closing_line = say("learn.known")
            return None
        await ctx.step("lesson", say("learn.lesson", lesson=lesson.text), lesson_id=lesson.id)
        ctx.closing_line = say("learn.done", lessons_text=count(len(agent.lessons.list(agent.id)), "lesson"))
        return lesson
