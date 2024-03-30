"""Learning from a decision: a rejection with a reason, or an approval where you changed the draft.

What the decision says is worked out here. Turning it into a rule is shared with the other
agents (core/learning.py). Either way the decision itself stays stored with the proposal.
"""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING, Any

from ...core.learning import learn_from
from ...core.memory import Lesson
from ...core.proposals import Proposal

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
    proposal = agent.proposals.get(proposal_id)
    found = feedback_of(proposal)
    if proposal is None or found is None:
        return None  # nothing to learn from: no run and no call
    action, feedback = found
    return await learn_from(
        agent, title=proposal.title, action=action, feedback=feedback, turned_down=action == "rejected",
        owner=agent.owner(), repo=proposal.repo, proposal_id=proposal_id,
    )  # fmt: skip
