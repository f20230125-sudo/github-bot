"""Turning one piece of your feedback on a draft into a lesson. Shared by every agent.

One call on the small model turns the feedback into at most one rule. Without Claude, the reason
you gave for turning a draft down is kept as you wrote it, and an edit is left alone.
"""

from __future__ import annotations

from typing import Any

from .claude import ClaudeBlocked
from .claude_cli import ClaudeError
from .memory import LESSON_SYSTEM, Lesson, LessonDraft, clean_lesson, lesson_prompt
from .runs import run
from .text import count


async def learn_from(
    agent: Any,
    *,
    title: str,
    action: str,
    feedback: str,
    turned_down: bool,
    owner: str,
    repo: str | None = None,
    proposal_id: int | None = None,
) -> Lesson | None:
    """Returns the lesson learned, if there was one.

    `agent` brings its bus, persona, lessons, Claude and settings. `action` is what you did to the
    draft, in words Claude is told ("rejected", "edited before approving"). `turned_down` says
    whether you refused the draft: only then are your own words kept when Claude can't be asked.
    """
    say = agent.persona.line
    async with run(agent.bus, agent.id, "learn", "Learn from your decision") as ctx:
        await ctx.step("feedback", say("learn.rejected" if turned_down else "learn.edited"), repo=repo)

        text: str | None = None
        off = await agent.claude.unavailable_reason()
        if off is None:
            try:
                draft = await agent.claude.ask_structured(
                    ctx, "lesson", LessonDraft, system=LESSON_SYSTEM,
                    prompt=lesson_prompt(owner, action, title, feedback, agent.lessons.active_texts(agent.id)),
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
            await ctx.step("claude", say("learn.claude_off", reason=off))
            if not turned_down:
                ctx.closing_line = say("learn.kept_edit")
                return None
            text = feedback.removeprefix("reason: ")  # your own words, kept as you wrote them

        if clean_lesson(text) is None:
            ctx.closing_line = say("learn.unusable")
            return None
        lesson = agent.lessons.add(agent.id, text, "rejection" if turned_down else "edit", proposal_id)
        if lesson is None:
            ctx.closing_line = say("learn.known")
            return None
        await ctx.step("lesson", say("learn.lesson", lesson=lesson.text), lesson_id=lesson.id)
        ctx.closing_line = say("learn.done", lessons_text=count(len(agent.lessons.list(agent.id)), "lesson"))
        return lesson
