"""From a note Patch left to a brief: is there enough for a post, and what may the post say?

Pure rules over facts Patch already holds. Nothing here calls a model or makes a request.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

# What kind of post each kind of news makes.
ANGLES = {
    "new_repo": "launch",
    "ready": "launch",
    "demo_link": "demo",
    "release": "release",
    "stars": "milestone",
}
ANGLE_LABELS = {
    "launch": "Launch post",
    "demo": "Try-it post",
    "release": "Release post",
    "milestone": "Milestone post",
    "update": "Update post",
}
MAX_TOPICS = 4
# A handful of stars is nothing to state. Patch's first star milestone is at this many.
MIN_STARS = 5


@dataclass(frozen=True)
class Fact:
    """One thing a post may state. Nothing outside these may appear in a post as a fact."""

    label: str
    value: str
    url: str | None = None


@dataclass(frozen=True)
class Brief:
    ready: bool
    angle: str
    facts: list[Fact] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # what holds the post back: keys of Pitch's lines
    wanted: list[str] = field(default_factory=list)  # what would make it better, without holding it back


def build_brief(topic: str, data: Mapping[str, Any], facts: Mapping[str, Any] | None) -> Brief:
    """`data` is what came with the note. `facts` is what Patch says about the repository now."""
    angle = ANGLES.get(topic, "update")
    if facts is None:
        return Brief(ready=False, angle=angle, missing=["unknown"])

    summary = facts.get("description") or facts.get("intro")
    score, before = facts.get("score"), facts.get("score_before")

    missing: list[str] = []
    if not summary:
        missing.append("summary")
    if score is None:
        missing.append("unscored")
    elif not facts.get("presentable"):
        missing.append("presentable")

    wanted: list[str] = []
    if not facts.get("image"):
        wanted.append("image")
    if not facts.get("homepage"):
        wanted.append("link")

    said: list[Fact] = []
    if summary:
        said.append(Fact("What it is", summary))
    if score is not None:
        rose = f", up from {before}" if before is not None and before < score else ""
        said.append(Fact("Score", f"{score} out of 100{rose}"))
    release = data.get("tag") if topic == "release" else facts.get("release")
    if release:
        said.append(Fact("Release", str(release)))
    stars = data.get("stars") if topic == "stars" else facts.get("stars")
    if stars and stars >= MIN_STARS:
        said.append(Fact("Stars", str(stars)))
    built = [facts.get("language"), ", ".join((facts.get("topics") or [])[:MAX_TOPICS])]
    if any(built):
        said.append(Fact("Built with", " · ".join(part for part in built if part)))
    if facts.get("license"):
        said.append(Fact("License", facts["license"]))
    if facts.get("homepage"):
        said.append(Fact("Live link", facts["homepage"], facts["homepage"]))
    said.append(Fact("Repository", facts["url"], facts["url"]))
    image = facts.get("image")
    if image:
        said.append(Fact("Picture", image["path"], image["url"]))

    return Brief(ready=not missing, angle=angle, facts=said, missing=missing, wanted=wanted)
