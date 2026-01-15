"""Health checks. Plain rules, no model: the same repository always gets the same findings.

Each finding carries a neutral title, the evidence, how it can be fixed, and the points it costs.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from urllib.parse import unquote

from .models import Finding, RepoDetails, RepoKind, RepoMeta, RepoReport, RepoSnapshot
from .paths import (
    ENV_FILE_RE,
    KEY_FILE_RE,
    LICENSE_RE,
    NODE_MANIFESTS,
    PRIVATE_KEY_NAMES,
    PYTHON_MANIFESTS,
    README_RE,
    TEST_DIR_NAMES,
    TEST_FILE_RE,
    VENDORED_RE,
    WORKFLOW_RE,
    basename,
    root_files,
)

# Bump when a rule changes, so stored findings are recomputed.
CHECKS_VERSION = 1

PLACEHOLDER_NAMES = {
    "test", "tests", "testing", "demo", "tmp", "temp", "untitled", "new", "foo", "bar",
    "hello-world", "sandbox", "playground", "scratch",
}  # fmt: skip
SHORT_README = 400
STALE_DAYS = 365

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", re.M)
_HTML_HEADING_RE = re.compile(r"<h[1-6][^>]*>(.*?)</h[1-6]>", re.I | re.S)
_TITLE_RE = re.compile(r"^\s{0,3}#\s+\S", re.M)
# Headings that tell a reader how to get the thing running or how to use it.
_RUN_RE = re.compile(
    r"\b(install\w*|setup|set up|getting started|quick ?start|run(ning)?|build(ing)?|develop\w*|"
    r"prerequisites|requirements|deploy\w*|usage|examples?|how to use|try it|commands?|cli)\b"
)
# Headings that say what it is made of or how it works inside.
_STACK_RE = re.compile(
    r"\b(tech\w*|stack|built|architecture|design|tools|dependencies|under the hood|structure|layout|"
    r"how it works|internals|implementation|pipeline|the model)\b"
)
_UI_FILE_RE = re.compile(r"\.(html|tsx|jsx|vue|svelte)$", re.I)
_FENCE_RE = re.compile(r"```.*?```", re.S)
_RUN_COMMAND_RE = re.compile(
    r"\b(pip3? install|npm (install|i|run|start|ci)|yarn|pnpm|python3? |uvicorn|flask run|docker|"
    r"make |cargo |go run|pytest|node |git clone|streamlit run)"
)
_VISUAL_RE = re.compile(r"!\[|<img\b|<video\b|\.(png|jpe?g|gif|webp|mp4)\b", re.I)
_DEMO_LINK_RE = re.compile(r"\[[^\]]*(live|demo|try it|website)[^\]]*\]\(https?://", re.I)
_MD_LINK_RE = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)")
_HTML_SRC_RE = re.compile(r"<(?:img|a|source|video)\b[^>]*?\b(?:src|href)=[\"']([^\"']+)[\"']", re.I)
_ABSOLUTE_RE = re.compile(r"^([a-z][a-z0-9+.-]*:|#|//)", re.I)


def classify(snapshot: RepoSnapshot) -> RepoKind:
    meta = snapshot.meta
    if meta.fork or meta.archived:
        return "skipped"
    if meta.name.lower() == meta.owner.lower():
        return "profile"
    if snapshot.details.empty or meta.name.lower() in PLACEHOLDER_NAMES:
        return "placeholder"
    return "project"


def build_report(snapshot: RepoSnapshot, now: datetime | None = None) -> RepoReport:
    kind = classify(snapshot)
    findings = run_checks(snapshot, kind, now)
    return RepoReport(
        full_name=snapshot.meta.full_name,
        kind=kind,
        snapshot=snapshot,
        findings=findings,
        score=score(findings) if kind in ("project", "profile") else None,
    )


def score(findings: list[Finding]) -> int:
    return max(0, 100 - sum(f.weight for f in findings))


def _own_files(details: RepoDetails) -> list[str]:
    """Files that belong to the project, leaving out vendored dependencies."""
    return [f for f in details.files if not VENDORED_RE.search(f)]


def has_ui(details: RepoDetails) -> bool:
    """Does the project have something to look at? Screenshots and live demos only matter if so."""
    return any(_UI_FILE_RE.search(f) for f in _own_files(details))


def run_checks(snapshot: RepoSnapshot, kind: RepoKind | None = None, now: datetime | None = None) -> list[Finding]:
    kind = kind or classify(snapshot)
    meta, details = snapshot.meta, snapshot.details
    if kind == "skipped":
        return []
    if kind == "placeholder":
        return [
            Finding(
                check="placeholder",
                severity="info",
                title="Placeholder repository",
                detail="The repository is empty." if details.empty else f"The repository is named '{meta.name}'.",
                fix="manual",
                data={"name": meta.name},
            )
        ]
    if kind == "profile":
        return list(_profile(details))
    return [
        *_metadata(meta, details),
        *readme_findings(details),
        *_engineering(meta, details),
        *_risk(details),
        *_hygiene(meta, now),
    ]


# -- metadata ---------------------------------------------------------------------------------


def _metadata(meta: RepoMeta, details: RepoDetails) -> Iterator[Finding]:
    if not meta.description:
        yield Finding(
            check="description", severity="medium", title="No description",
            detail="The repository's description field is empty.", fix="metadata", weight=8,
        )  # fmt: skip

    topics = len(meta.topics)
    if topics == 0:
        yield Finding(
            check="topics", severity="medium", title="No topics",
            detail="The repository has no topics.", fix="metadata", weight=6, data={"n": 0},
        )  # fmt: skip
    elif topics < 3:
        yield Finding(
            check="topics", severity="low", title="Fewer than three topics",
            detail=f"The repository has {topics} topic{'s' if topics != 1 else ''}: {', '.join(meta.topics)}.",
            fix="metadata", weight=3, data={"n": topics},
        )  # fmt: skip

    if not meta.homepage and has_ui(details):
        yield Finding(
            check="homepage", severity="low", title="No live demo link",
            detail="The project has a user interface, but the repository's website field is empty.",
            fix="manual", weight=2,
        )  # fmt: skip

    has_license_file = any(LICENSE_RE.match(f) for f in root_files(details.files))
    if not meta.license and not has_license_file:
        yield Finding(
            check="license", severity="high", title="No license",
            detail="GitHub detected no license and there is no LICENSE file in the root.",
            fix="file", weight=10,
        )  # fmt: skip


# -- README -----------------------------------------------------------------------------------


def _headings(text: str) -> list[str]:
    found = _HEADING_RE.findall(text) + [re.sub(r"<[^>]+>", "", h) for h in _HTML_HEADING_RE.findall(text)]
    return [h.strip().lower() for h in found]


def _dead_links(text: str, details: RepoDetails) -> list[str]:
    """Relative links that point at nothing. Only judged where the file listing is known to be complete."""
    if details.truncated:
        return []
    known = set(details.files) | set(details.dirs)
    dead: list[str] = []
    for target in _MD_LINK_RE.findall(text) + _HTML_SRC_RE.findall(text):
        if _ABSOLUTE_RE.match(target):
            continue
        path = unquote(target.split("#")[0].split("?")[0]).removeprefix("./").strip("/")
        if not path or path.startswith(".."):
            continue
        if details.tree_depth is not None and path.count("/") + 1 > details.tree_depth:
            continue  # deeper than we listed: can't tell
        if path not in known and target not in dead:
            dead.append(target)
    return dead


def readme_findings(details: RepoDetails) -> Iterator[Finding]:
    """The README rules on their own. Also used to check a drafted README before it is proposed."""
    if details.readme_path is None:
        nested = sorted((f for f in _own_files(details) if README_RE.match(basename(f))), key=lambda f: f.count("/"))
        if nested:
            folder = nested[0].rsplit("/", 1)[0]
            yield Finding(
                check="readme_nested", severity="high", title="README is not in the root",
                detail=f"The only README is {nested[0]}. GitHub shows a README on the repository page "
                "only when it is in the root, .github or docs.",
                fix="file", weight=15, data={"path": nested[0], "dir": folder},
            )  # fmt: skip
        else:
            yield Finding(
                check="readme_missing", severity="high", title="No README",
                detail="There is no README file anywhere in the repository.", fix="file", weight=15,
            )  # fmt: skip
        return

    text = details.readme_text
    if text is None:
        return  # binary or too large to read: nothing to judge

    length = len(text.strip())
    if length < SHORT_README:
        yield Finding(
            check="readme_short", severity="medium", title="Very short README",
            detail=f"{details.readme_path} is {length} characters long.", fix="file", weight=12,
            data={"n": length},
        )  # fmt: skip
        return  # the section checks below would only repeat this

    headings = _headings(text)
    code = " ".join(_FENCE_RE.findall(text)).lower()

    if not _TITLE_RE.search(text) and "<h1" not in text.lower():
        yield Finding(
            check="readme_title", severity="low", title="README has no title",
            detail=f"{details.readme_path} has no top-level heading.", fix="file", weight=2,
        )  # fmt: skip
    if not any(_RUN_RE.search(h) for h in headings) and not _RUN_COMMAND_RE.search(code):
        yield Finding(
            check="readme_setup", severity="medium", title="README doesn't say how to run it",
            detail="No setup, run or usage section, and no install or run command in a code block.",
            fix="file", weight=6,
        )  # fmt: skip
    if not any(_STACK_RE.search(h) for h in headings):
        yield Finding(
            check="readme_stack", severity="low", title="README doesn't say what it's built with",
            detail="No heading mentions the stack, architecture, design or how it works.", fix="file", weight=2,
        )  # fmt: skip
    if has_ui(details) and not _VISUAL_RE.search(text) and not _DEMO_LINK_RE.search(text):
        yield Finding(
            check="readme_visual", severity="low", title="README has no screenshot or demo",
            detail="The project has a user interface, but the README has no image, video or live-demo link.",
            fix="manual", weight=3,
        )  # fmt: skip

    dead = _dead_links(text, details)
    if dead:
        shown = ", ".join(dead[:5])
        yield Finding(
            check="readme_dead_links", severity="medium", title="README links to missing files",
            detail=f"These relative links point at files that don't exist: {shown}.",
            fix="file", weight=4, data={"n": len(dead)},
        )  # fmt: skip


# -- engineering ------------------------------------------------------------------------------


def _engineering(meta: RepoMeta, details: RepoDetails) -> Iterator[Finding]:
    if not meta.language:
        return  # GitHub found no code: tests and CI don't apply

    files = _own_files(details)
    names = {basename(f).lower() for f in files}
    has_tests = any(TEST_FILE_RE.search(f) for f in files) or any(
        basename(d).lower() in TEST_DIR_NAMES for d in details.dirs if not VENDORED_RE.search(d + "/")
    )
    has_ci = any(WORKFLOW_RE.match(f) for f in files)

    if not has_tests:
        # With no tests there is nothing for CI to run, so missing CI isn't raised on top of this.
        yield Finding(
            check="tests_missing", severity="medium", title="No tests",
            detail="No test files or test directory found.", fix="manual", weight=8,
        )  # fmt: skip
    elif not has_ci:
        templated = meta.language in ("Python", "JavaScript", "TypeScript")
        yield Finding(
            check="ci_missing", severity="medium", title="Tests are not run automatically",
            detail="There are tests, but no workflow file under .github/workflows to run them.",
            fix="file" if templated else "manual", weight=6,
        )  # fmt: skip
    elif details.ci_state == "failure":
        yield Finding(
            check="ci_failing", severity="high", title="CI is failing",
            detail=f"The latest run on {meta.default_branch} did not pass.", fix="manual", weight=10,
        )  # fmt: skip

    if ".gitignore" not in names:
        yield Finding(
            check="gitignore", severity="low", title="No .gitignore",
            detail="There is no .gitignore file.", fix="file", weight=3,
        )  # fmt: skip

    manifests = {"Python": PYTHON_MANIFESTS, "JavaScript": NODE_MANIFESTS, "TypeScript": NODE_MANIFESTS}.get(
        meta.language
    )
    # Plain JavaScript served from an index.html has nothing to install.
    static_site = meta.language == "JavaScript" and "index.html" in names
    has_manifest = bool(manifests and names & manifests) or any(n.startswith("requirements") for n in names)
    if manifests and not static_site and not has_manifest:
        yield Finding(
            check="manifest", severity="low", title="No dependency manifest",
            detail=f"No {' or '.join(sorted(manifests)[:3])} found for a {meta.language} project.",
            fix="manual", weight=3,
        )  # fmt: skip


# -- risk and hygiene -------------------------------------------------------------------------


def _risk(details: RepoDetails) -> Iterator[Finding]:
    secrets, keys = [], []
    for path in details.files:
        if VENDORED_RE.search(path):
            continue
        name = basename(path)
        if ENV_FILE_RE.match(name) or name in PRIVATE_KEY_NAMES:
            secrets.append(path)
        elif KEY_FILE_RE.search(name):
            keys.append(path)

    for path in secrets[:3]:
        yield Finding(
            check="secret_file", severity="critical", title="Possible secrets committed",
            detail=f"A file named {path} is committed. Its contents were not read.",
            fix="manual", weight=25, data={"path": path},
        )  # fmt: skip
    for path in keys[:3]:
        yield Finding(
            check="key_file", severity="high", title="Possible key or credentials file",
            detail=f"{path} has the name of a key or credentials file. Its contents were not read.",
            fix="manual", weight=10, data={"path": path},
        )  # fmt: skip


def _hygiene(meta: RepoMeta, now: datetime | None) -> Iterator[Finding]:
    if not meta.pushed_at:
        return
    pushed = datetime.fromisoformat(meta.pushed_at.replace("Z", "+00:00"))
    days = ((now or datetime.now(UTC)) - pushed).days
    if days > STALE_DAYS:
        yield Finding(
            check="stale", severity="info", title="No recent activity",
            detail=f"The last push was {days} days ago.", fix="manual", data={"days": days},
        )  # fmt: skip


# -- the profile repository -------------------------------------------------------------------


def _profile(details: RepoDetails) -> Iterator[Finding]:
    # A profile README is about you, so these are yours to write: Patch doesn't draft them.
    if details.readme_path is None or not (details.readme_text or "").strip():
        yield Finding(
            check="profile_readme_missing", severity="high", title="Profile README is missing",
            detail="The profile repository has no README, so the profile page shows nothing.",
            fix="manual", weight=40,
        )  # fmt: skip
        return
    text = details.readme_text or ""
    length = len(text.strip())
    if length < 300:
        yield Finding(
            check="profile_readme_short", severity="medium", title="Profile README is very short",
            detail=f"The profile README is {length} characters long.", fix="manual", weight=20,
            data={"n": length},
        )  # fmt: skip
    if "http" not in text:
        yield Finding(
            check="profile_no_links", severity="low", title="Profile README has no links",
            detail="The profile README links to no project, site or contact.", fix="manual", weight=10,
        )  # fmt: skip
