"""What Patch knows about one repository, as plain facts another agent can use.

Everything here comes from the last audit. Nothing calls a model or makes a request.
"""

from __future__ import annotations

import posixpath
import re
from typing import Any
from urllib.parse import quote, unquote

from .handoffs import READY_SCORE
from .models import RepoSnapshot
from .paths import WORKFLOW_RE
from .store import StoredRepo

_FENCE_RE = re.compile(r"```.*?```", re.S)
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)")
_HTML_IMAGE_RE = re.compile(r"<img\b[^>]*?\bsrc=[\"']([^\"']+)[\"']", re.I)
# A picture of the project. A badge is an .svg or has no extension at all, so it never matches.
_PICTURE_RE = re.compile(r"\.(png|jpe?g|gif|webp)$", re.I)
_ABSOLUTE_RE = re.compile(r"^https?://", re.I)
# How a block of the README starts when it is not a sentence about the project.
_NOT_PROSE = ("#", "!", "[!", "<", "|", ">", "-", "* ", "=", "```")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_MARKS_RE = re.compile(r"(\*\*|__|`|(?<!\w)[*_](?=\w)|(?<=\w)[*_](?!\w))")
_SENTENCE_END_RE = re.compile(r"[.!?](?=\s)")
INTRO_CHARS = 280
SHORTEST_CUT = 80


def readme_image(snapshot: RepoSnapshot) -> dict[str, str] | None:
    """The first picture the README shows: where it is in the repository, and an address that opens it."""
    meta, details = snapshot.meta, snapshot.details
    text = _FENCE_RE.sub("", details.readme_text or "")
    found = [*_MD_IMAGE_RE.finditer(text), *_HTML_IMAGE_RE.finditer(text)]
    for match in sorted(found, key=lambda m: m.start()):
        target = match.group(1)
        if not _PICTURE_RE.search(target.split("?")[0].split("#")[0]):
            continue
        if _ABSOLUTE_RE.match(target):
            return {"path": target, "url": target}
        folder = posixpath.dirname(details.readme_path or "")
        target = unquote(target)  # a space in a file name is written %20 in the README
        path = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
        url = f"https://raw.githubusercontent.com/{meta.full_name}/{quote(meta.default_branch)}/{quote(path)}"
        return {"path": path, "url": url}
    return None


def readme_intro(text: str | None) -> str | None:
    """The first paragraph of the README that is a sentence about the project, not a heading or a
    badge, as plain text. A long one is cut at the end of a sentence where it can be."""
    for block in re.split(r"\n\s*\n", _FENCE_RE.sub("", text or "")):
        line = " ".join(block.split())
        if not line or line.startswith(_NOT_PROSE):
            continue
        line = _MD_MARKS_RE.sub("", _MD_LINK_RE.sub(r"\1", line))
        if len(line) <= INTRO_CHARS:
            return line
        ends = [m.end() for m in _SENTENCE_END_RE.finditer(line[:INTRO_CHARS])]
        if ends and ends[-1] >= SHORTEST_CUT:
            return line[: ends[-1]]
        return line[:INTRO_CHARS].rsplit(" ", 1)[0] + " ..."
    return None


def repo_facts(repo: StoredRepo, history: list[dict[str, Any]]) -> dict[str, Any]:
    """`history` is the repository's score over time, oldest first."""
    meta, details = repo.snapshot.meta, repo.snapshot.details
    found = {finding.check for finding in repo.findings}
    return {
        "full_name": repo.full_name,
        "name": meta.name,
        "kind": repo.kind,
        "url": meta.html_url or f"https://github.com/{repo.full_name}",
        "description": meta.description,
        "intro": readme_intro(details.readme_text),
        "homepage": meta.homepage,
        "language": meta.language,
        "topics": list(meta.topics),
        "license": meta.license,
        "stars": meta.stars,
        "release": details.latest_release,
        "image": readme_image(repo.snapshot),
        # What the health checks found present. Each is something a post may say the project has.
        "has": {
            "tests": "tests_missing" not in found,
            # A workflow is there and is not failing. Whether a run happens to be in progress
            # right now does not come into it: a post is read long after that run has ended.
            "ci": any(WORKFLOW_RE.match(path) for path in details.files) and details.ci_state != "failure",
            "setup_steps": not found & {"readme_missing", "readme_short", "readme_setup"},
        },
        "score": repo.score,
        "score_before": history[-2]["score"] if len(history) > 1 else None,
        # Presentable: it explains itself and can be reused. The line is Patch's to draw.
        "presentable": repo.score is not None and repo.score >= READY_SCORE,
        "presentable_from": READY_SCORE,
    }
