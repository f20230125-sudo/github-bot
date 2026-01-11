"""File-name patterns shared by the sync (what to fetch) and the checks (what to flag)."""

from __future__ import annotations

import re

README_RE = re.compile(r"^readme(\.[a-z0-9]+)?$", re.I)
LICENSE_RE = re.compile(r"^(licen[sc]e|copying|unlicense)([.-][a-z0-9.-]+)?$", re.I)
WORKFLOW_RE = re.compile(r"^\.github/workflows/[^/]+\.ya?ml$", re.I)

TEST_DIR_NAMES = {"tests", "test", "__tests__", "spec", "specs", "e2e"}
TEST_FILE_RE = re.compile(
    r"(^|/)(test_[^/]+\.py|[^/]+_test\.(py|go)|[^/]+\.(test|spec)\.[cm]?[jt]sx?|[^/]+Tests?\.(java|cs|kt))$"
)

# A committed environment file or private key. `.env.example` and friends are fine.
ENV_FILE_RE = re.compile(r"^\.env(\.(local|production|prod|development|dev|staging|test))?$", re.I)
PRIVATE_KEY_NAMES = {"id_rsa", "id_ed25519", "id_dsa", "id_ecdsa"}
KEY_FILE_RE = re.compile(r"(\.(pem|p12|pfx|keystore|jks)|^credentials\.json|^service[-_]account[^/]*\.json)$", re.I)
VENDORED_RE = re.compile(r"(^|/)(node_modules|vendor|\.venv|venv|site-packages)/")

PYTHON_MANIFESTS = {"requirements.txt", "pyproject.toml", "setup.py", "setup.cfg", "pipfile", "environment.yml"}
NODE_MANIFESTS = {"package.json"}


def basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def root_files(files: list[str]) -> list[str]:
    return [f for f in files if "/" not in f]


def find_readme(files: list[str]) -> str | None:
    """The root README, preferring Markdown."""
    candidates = [f for f in root_files(files) if README_RE.match(f)]
    if not candidates:
        return None
    candidates.sort(key=lambda f: (not f.lower().endswith(".md"), f))
    return candidates[0]
