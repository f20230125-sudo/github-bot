"""What your decision on a draft says: why you passed on it, or how you changed it before posting.

Turning that into a rule is shared with the other agents (core/learning.py).
"""

from __future__ import annotations

import difflib

from .posts import Post

FEEDBACK_CHARS = 3000
DIFF_LINES = 40


def drafted_text(post: Post) -> str:
    """The version you chose, as Pitch wrote it."""
    variants = post.payload.get("variants") or []
    chosen = next((v for v in variants if v["tone"] == post.tone), variants[0] if variants else {"text": ""})
    return chosen["text"]


def feedback_of(post: Post | None) -> tuple[str, str] | None:
    """What a decision says about the draft, as (what you did, the feedback). None if it says nothing."""
    if post is None:
        return None
    if post.status == "dismissed":
        reason = " ".join((post.reason or "").split())
        return ("passed on", f"reason: {reason}") if reason else None
    if post.status == "posted" and post.text:
        before, after = drafted_text(post).splitlines(), post.text.splitlines()
        diff = list(difflib.unified_diff(before, after, lineterm="", n=0))[2:]  # without the two file-name lines
        if diff:
            changed = "\n".join(diff[:DIFF_LINES])
            return "edited before posting", f"The post was changed like this (- drafted, + posted):\n{changed}"[
                :FEEDBACK_CHARS
            ]
    return None
