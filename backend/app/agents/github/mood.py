"""Patch's mood. Computed from how healthy the repositories are, never random and never from a model."""

from __future__ import annotations

HAPPY_FROM = 80
SAD_BELOW = 60
# Each mood and what causes it. Shown on Patch's page, and worn on its face.
MOODS = {
    "happy": f"The portfolio score is {HAPPY_FROM} or more.",
    "normal": f"The portfolio score is from {SAD_BELOW} to {HAPPY_FROM - 1}, or nothing has been audited yet.",
    "sad": f"The portfolio score is under {SAD_BELOW}.",
}


def compute_mood(score: int | None) -> str:
    """happy, normal or sad, from the portfolio score: the average score of the audited repositories."""
    if score is None:
        return "normal"
    if score >= HAPPY_FROM:
        return "happy"
    return "normal" if score >= SAD_BELOW else "sad"
