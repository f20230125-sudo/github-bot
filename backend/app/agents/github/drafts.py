"""What Claude drafts for Patch, and the checks each draft must pass before it becomes a proposal.

Claude's answer is never trusted as it comes: it is fitted to a shape, cleaned to GitHub's
rules, and a drafted README is run back through the same README checks that flagged the problem.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from .checks import SHORT_README, readme_findings
from .models import Finding, RepoSnapshot

DESCRIPTION_MAX = 200  # we ask for 160; GitHub itself allows 350
MAX_TOPICS = 8
MIN_README = 200
MAX_README = 60_000
KEEP_RATIO = 0.6
_TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")
_EMOJI_RE = re.compile("[\U0001f000-\U0001faff☀-➿\U0001f1e6-\U0001f1ff]")


# -- shapes Claude must answer in -------------------------------------------------------------


class RepoMetadata(BaseModel):
    repo: str
    description: str | None = None
    topics: list[str] = Field(default_factory=list)


class SweepDraft(BaseModel):
    repos: list[RepoMetadata]
    say: str = ""


class ReadmeDraft(BaseModel):
    content: str
    summary: str = ""
    say: str = ""


# -- metadata ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class SweepTarget:
    snapshot: RepoSnapshot
    needs_description: bool
    needs_topics: bool


@dataclass(frozen=True)
class MetadataItem:
    repo: str
    description: str | None  # None: leave the description as it is
    topics: list[str] | None  # the full new list, or None: leave the topics as they are
    current_description: str | None
    current_topics: list[str]
    meta_fp: str  # what the listing looked like when this was drafted


def clean_description(text: str | None) -> str | None:
    """One line, no trailing full stop, no emoji, within the length limit. Anything else is dropped."""
    cleaned = " ".join((text or "").split()).rstrip(".").strip()
    if not cleaned or len(cleaned) > DESCRIPTION_MAX or _EMOJI_RE.search(cleaned):
        return None
    return cleaned


def clean_topics(proposed: list[str], existing: list[str]) -> list[str]:
    """GitHub topics: lowercase letters, digits and hyphens. Existing topics are kept, in order."""
    topics = list(existing)
    for raw in proposed:
        topic = re.sub(r"[\s_]+", "-", str(raw).strip().lower())
        topic = re.sub(r"[^a-z0-9-]", "", topic).strip("-")
        if _TOPIC_RE.match(topic) and topic not in topics:
            topics.append(topic)
    return topics[:MAX_TOPICS]


def sweep_items(draft: SweepDraft, targets: list[SweepTarget]) -> tuple[list[MetadataItem], list[str]]:
    """Turn Claude's answer into items to propose. Returns the items and notes on anything dropped."""
    by_name = {t.snapshot.meta.full_name.lower(): t for t in targets}
    items: list[MetadataItem] = []
    notes: list[str] = []
    seen: set[str] = set()

    for entry in draft.repos:
        target = by_name.get(entry.repo.strip().lower())
        if target is None or entry.repo.strip().lower() in seen:
            continue  # a repository nobody asked about, or one answered twice
        seen.add(entry.repo.strip().lower())
        meta = target.snapshot.meta

        description = clean_description(entry.description) if target.needs_description else None
        if target.needs_description and description is None:
            notes.append(f"{meta.name}: the description was missing or unusable, so it was left out.")

        topics = None
        if target.needs_topics:
            cleaned = clean_topics(entry.topics, meta.topics)
            if len(cleaned) > len(meta.topics):
                topics = cleaned
            else:
                notes.append(f"{meta.name}: no usable topics came back.")

        if description is not None or topics is not None:
            items.append(
                MetadataItem(
                    repo=meta.full_name, description=description, topics=topics,
                    current_description=meta.description, current_topics=list(meta.topics),
                    meta_fp=meta.fingerprint(),
                )  # fmt: skip
            )

    for name in sorted(set(by_name) - seen):
        notes.append(f"{by_name[name].snapshot.meta.name}: Claude's answer left it out.")
    return items, notes


# -- README -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReadmeVerdict:
    ok: bool
    resolves: list[str] = field(default_factory=list)  # checks the draft fixes
    remaining: list[str] = field(default_factory=list)  # README checks still failing afterwards
    note: str | None = None  # something you should know before approving
    problem: str | None = None  # why the draft was thrown away


def verify_readme(
    snapshot: RepoSnapshot, findings: list[Finding], content: str, previous: str | None
) -> ReadmeVerdict:
    """Run the drafted README through the same rules that flagged the original."""
    content = content.strip()
    if len(content) < MIN_README:
        return ReadmeVerdict(False, problem="The draft was too short to be a README.")
    if len(content) > MAX_README:
        return ReadmeVerdict(False, problem="The draft was unreasonably long.")

    details = snapshot.details
    files = details.files if "README.md" in details.files else [*details.files, "README.md"]
    drafted = details.model_copy(update={"readme_path": "README.md", "readme_text": content, "files": files})
    after = {f.check for f in readme_findings(drafted)}
    targets = {f.check for f in findings}
    before = {f.check for f in readme_findings(details)}

    if "readme_dead_links" in after and "readme_dead_links" not in before:
        return ReadmeVerdict(False, problem="The draft linked to files that don't exist in the repository.")
    resolves = sorted(targets - after)
    if not resolves:
        return ReadmeVerdict(False, problem="The draft didn't fix what it was meant to fix.")

    note = None
    if previous and len(previous.strip()) >= SHORT_README:
        old_lines = [line.strip() for line in previous.splitlines() if line.strip()]
        kept = sum(1 for line in old_lines if line in content)
        if old_lines and kept / len(old_lines) < KEEP_RATIO:
            note = "This rewrites most of the existing README. Read the diff before approving."
    return ReadmeVerdict(True, resolves=resolves, remaining=sorted(after), note=note)
