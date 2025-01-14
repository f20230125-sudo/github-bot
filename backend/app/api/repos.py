from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..agents.github.agent import portfolio_summary
from ..agents.github.store import StoredRepo
from ..agents.github.voice import finding_line

router = APIRouter(prefix="/api/repos")


def repo_card(repo: StoredRepo) -> dict[str, Any]:
    meta = repo.snapshot.meta
    return {
        "full_name": repo.full_name,
        "name": meta.name,
        "owner": meta.owner,
        "kind": repo.kind,
        "score": repo.score,
        "counts": dict(Counter(f.severity for f in repo.findings)),
        "description": meta.description,
        "html_url": meta.html_url,
        "homepage": meta.homepage,
        "language": meta.language,
        "topics": meta.topics,
        "license": meta.license,
        "private": meta.private,
        "stars": meta.stars,
        "pushed_at": meta.pushed_at,
        "ci_state": repo.snapshot.details.ci_state,
        "synced_at": repo.synced_at,
    }


@router.get("")
async def list_repos(request: Request):
    repos = request.app.state.patch.store.all()
    return {"portfolio": portfolio_summary(repos), "repos": [repo_card(r) for r in repos.values()]}


@router.get("/{owner}/{name}")
async def get_repo(request: Request, owner: str, name: str):
    patch = request.app.state.patch
    repo = patch.store.get(f"{owner}/{name}")
    if repo is None:
        raise HTTPException(404, "Patch hasn't seen that repository.")
    details = repo.snapshot.details
    return {
        "repo": repo_card(repo),
        "findings": [{**f.model_dump(), "text": finding_line(patch.persona, f)} for f in repo.findings],
        "history": patch.store.score_history(repo.full_name),
        "readme": {"path": details.readme_path, "chars": len(details.readme_text or "")},
        "files": len(details.files),
    }
