"""Fixes that need no model: a license, a .gitignore and a CI workflow, filled in from templates.

The workflow follows the conventions of the CI files already in the account
(checkout@v4, setup-python@v5, setup-node@v4, run on pushes to the default branch and on pull requests).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .models import RepoSnapshot
from .paths import TEST_DIR_NAMES, TEST_FILE_RE, VENDORED_RE, basename

TEMPLATES = Path(__file__).parent / "templates"
WORKFLOW_PATH = ".github/workflows/ci.yml"
PYTHON_VERSION = "3.12"
NODE_VERSION = "20"


@dataclass(frozen=True)
class FileFix:
    path: str
    content: str
    finding: str  # the check this fixes
    reason: str  # one plain sentence for the pull request
    source: Literal["template", "claude"] = "template"


def _template(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


def _own(files: list[str]) -> list[str]:
    return [f for f in files if not VENDORED_RE.search(f)]


def _folder(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _shallowest(paths: list[str]) -> str | None:
    return min(paths, key=lambda p: (p.count("/"), p)) if paths else None


# -- license and .gitignore -------------------------------------------------------------------


def license_fix(holder: str, year: int) -> FileFix:
    return FileFix(
        path="LICENSE",
        content=_template("LICENSE-MIT.txt").format(year=year, holder=holder),
        finding="license",
        reason="Adds the MIT license, so others know how they may use the code.",
    )


def gitignore_fix(snapshot: RepoSnapshot) -> FileFix:
    files = _own(snapshot.details.files)
    names = {basename(f).lower() for f in files}
    parts = []
    if any(f.endswith(".py") for f in files):
        parts.append(_template("gitignore-python.txt"))
    if "package.json" in names:
        parts.append(_template("gitignore-node.txt"))
    if not parts:
        parts.append(_template("gitignore-static.txt"))
    # One list, no repeats, order kept.
    lines = list(dict.fromkeys(line for part in parts for line in part.splitlines() if line))
    return FileFix(
        path=".gitignore",
        content="\n".join(lines) + "\n",
        finding="gitignore",
        reason="Adds a .gitignore so build output, caches and local environment files stay out of the repository.",
    )


# -- CI ---------------------------------------------------------------------------------------


def _is_under(path: str, folder: str) -> bool:
    return not folder or path.startswith(folder + "/")


def _python_job(snapshot: RepoSnapshot) -> str | None:
    files = _own(snapshot.details.files)
    tests = [f for f in files if f.endswith(".py") and TEST_FILE_RE.search(f)]
    if not tests:
        return None
    manifest = _shallowest([f for f in files if basename(f) in ("requirements.txt", "pyproject.toml")])
    folder = _folder(manifest) if manifest else _folder(_shallowest(tests) or "")
    # A tests/ folder holds the tests, but the project root is the folder above it.
    while basename(folder).lower() in TEST_DIR_NAMES:
        folder = _folder(folder)
    if manifest and not all(_is_under(t, folder) for t in tests):
        folder = ""  # tests live outside the manifest's folder: run from the root

    install = ["python -m pip install --upgrade pip"]
    if manifest and basename(manifest) == "requirements.txt":
        install.append("pip install -r requirements.txt")
    elif manifest:
        install.append("pip install -e .")
    install.append("pip install pytest")

    lines = ["  test:", "    runs-on: ubuntu-latest"]
    if folder:
        lines += ["    defaults:", "      run:", f"        working-directory: {folder}"]
    lines += [
        "    steps:",
        "      - uses: actions/checkout@v4",
        "",
        "      - uses: actions/setup-python@v5",
        "        with:",
        f'          python-version: "{PYTHON_VERSION}"',
        "",
        "      - name: Install dependencies",
        "        run: |",
        *[f"          {command}" for command in install],
        "",
        "      - name: Test",
        "        run: pytest",
    ]
    return "\n".join(lines)


def _node_job(snapshot: RepoSnapshot, package_json: dict[str, str]) -> str | None:
    """`package_json` maps each package.json path to its text. A job is written only for a
    package that really has a test script: CI that runs nothing would be worse than none."""
    files = _own(snapshot.details.files)
    tests = [f for f in files if not f.endswith(".py") and TEST_FILE_RE.search(f)]
    if not tests:
        return None
    for manifest in sorted(package_json, key=lambda p: (p.count("/"), p)):
        folder = _folder(manifest)
        if not any(_is_under(t, folder) for t in tests):
            continue
        try:
            scripts = json.loads(package_json[manifest]).get("scripts") or {}
        except ValueError:
            continue
        if "test" not in scripts:
            continue

        lock = f"{folder}/package-lock.json" if folder else "package-lock.json"
        has_lock = lock in files
        lines = ["  test-node:", "    runs-on: ubuntu-latest"]
        if folder:
            lines += ["    defaults:", "      run:", f"        working-directory: {folder}"]
        lines += [
            "    steps:",
            "      - uses: actions/checkout@v4",
            "",
            "      - uses: actions/setup-node@v4",
            "        with:",
            f"          node-version: {NODE_VERSION}",
        ]
        if has_lock:
            lines += ["          cache: npm"]
            if folder:
                lines += [f"          cache-dependency-path: {lock}"]
        lines += ["", f"      - run: {'npm ci' if has_lock else 'npm install'}", "", "      - run: npm test"]
        return "\n".join(lines)
    return None


def needs_package_json(snapshot: RepoSnapshot) -> list[str]:
    """Which package.json files must be read before a Node CI job can be written."""
    files = _own(snapshot.details.files)
    if not any(not f.endswith(".py") and TEST_FILE_RE.search(f) for f in files):
        return []
    return sorted(f for f in files if basename(f) == "package.json")


def ci_fix(snapshot: RepoSnapshot, package_json: dict[str, str] | None = None) -> FileFix | None:
    jobs = [job for job in (_python_job(snapshot), _node_job(snapshot, package_json or {})) if job]
    if not jobs:
        return None
    branch = snapshot.meta.default_branch
    header = f"name: CI\n\non:\n  push:\n    branches: [{branch}]\n  pull_request:\n\njobs:\n"
    return FileFix(
        path=WORKFLOW_PATH,
        content=header + "\n\n".join(jobs) + "\n",
        finding="ci_missing",
        reason="Adds a CI workflow that runs the existing tests on every push and pull request. "
        "Check that the install step matches how you run the tests locally.",
    )
