from __future__ import annotations

import difflib
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..agents.github.decisions import DecisionError
from ..agents.github.learning import feedback_of
from ..core.jobs import Paused
from ..core.policy import KINDS, PolicyError
from ..core.proposals import Proposal

router = APIRouter(prefix="/api")


def proposal_card(proposal: Proposal) -> dict[str, Any]:
    payload = proposal.payload
    return {
        "id": proposal.id,
        "agent": proposal.agent,
        "kind": proposal.kind,
        "repo": proposal.repo,
        "title": proposal.title,
        "summary": proposal.summary,
        "status": proposal.status,
        "items": len(payload.get("items") or payload.get("files") or []),
        "created_at": proposal.created_at,
        "updated_at": proposal.updated_at,
        "decision": proposal.decision,
        "result": proposal.result,
    }


def diff_lines(previous: str | None, content: str, path: str) -> list[str]:
    """A unified diff, line by line. A new file is every line added."""
    if previous is None:
        return [f"+{line}" for line in content.splitlines()]
    return list(
        difflib.unified_diff(previous.splitlines(), content.splitlines(), fromfile=path, tofile=path, lineterm="", n=3)
    )[2:]  # drop the two "---" / "+++" header lines: the path is shown above the diff


def proposal_detail(proposal: Proposal) -> dict[str, Any]:
    payload = dict(proposal.payload)
    if proposal.kind == "pull_request":
        payload["files"] = [
            {
                **file,
                "is_new": file["previous"] is None,
                "diff": diff_lines(file["previous"], file["content"], file["path"]),
            }
            for file in payload["files"]
        ]
    return {**proposal_card(proposal), "payload": payload}


@router.get("/proposals")
async def list_proposals(request: Request, status: str = Query("pending")):
    store = request.app.state.proposals
    proposals = store.list() if status == "all" else store.list(status)  # type: ignore[arg-type]
    return {"proposals": [proposal_card(p) for p in proposals]}


@router.get("/proposals/{proposal_id}")
async def get_proposal(request: Request, proposal_id: int):
    proposal = request.app.state.proposals.get(proposal_id)
    if proposal is None:
        raise HTTPException(404, "No such proposal.")
    return proposal_detail(proposal)


class ItemEdit(BaseModel):
    repo: str
    enabled: bool = True
    description: str | None = None
    topics: list[str] | None = None


class FileEdit(BaseModel):
    path: str
    enabled: bool = True
    content: str | None = None


class ApproveBody(BaseModel):
    """Your edits, if any. Leave it empty to approve the proposal as drafted."""

    items: list[ItemEdit] | None = None
    files: list[FileEdit] | None = None


class RejectBody(BaseModel):
    reason: str = Field("", max_length=300)


def _queue_apply(state: Any, proposal_id: int) -> bool:
    return state.jobs.submit(f"patch.apply.{proposal_id}", lambda: state.patch.apply(proposal_id))


def _queue_learn(state: Any, proposal: Proposal) -> None:
    """If the decision came with a reason or an edit, let Patch turn it into a lesson."""
    if feedback_of(proposal) is None:
        return
    try:
        state.jobs.submit(f"patch.learn.{proposal.id}", lambda: state.patch.learn(proposal.id))
    except Paused:
        pass  # the decision is stored either way; while paused, nothing is learned from it


@router.post("/proposals/{proposal_id}/approve", status_code=202)
async def approve_proposal(request: Request, proposal_id: int, body: ApproveBody | None = None):
    """Approve, then apply. With dry-run on, applying is a rehearsal: nothing is written."""
    state = request.app.state
    if state.desk.paused:
        # Checked first, so an approval is never recorded that can't then be carried out.
        raise HTTPException(409, "Paused. Resume before approving, so the change can be applied.")
    edits = body.model_dump(exclude_unset=True) if body else None
    try:
        proposal = await state.patch.approve(proposal_id, edits)
    except LookupError:
        raise HTTPException(404, "No such proposal.") from None
    except DecisionError as exc:
        raise HTTPException(409 if "already" in str(exc) else 422, str(exc)) from None
    _queue_apply(state, proposal_id)
    _queue_learn(state, proposal)  # after the apply, so a model call never delays what you approved
    return proposal_card(proposal)


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(request: Request, proposal_id: int, body: RejectBody | None = None):
    state = request.app.state
    try:
        proposal = await state.patch.reject(proposal_id, body.reason if body else "")
    except LookupError:
        raise HTTPException(404, "No such proposal.") from None
    except DecisionError as exc:
        raise HTTPException(409, str(exc)) from None
    _queue_learn(state, proposal)
    return proposal_card(proposal)


@router.post("/proposals/{proposal_id}/apply", status_code=202)
async def apply_proposal(request: Request, proposal_id: int):
    """Apply a proposal you already approved, for example after switching dry-run off."""
    state = request.app.state
    proposal = state.proposals.get(proposal_id)
    if proposal is None:
        raise HTTPException(404, "No such proposal.")
    if proposal.status != "approved":
        raise HTTPException(409, f"This proposal is {proposal.status}, not waiting to be applied.")
    return {"queued": _queue_apply(state, proposal_id)}


@router.post("/jobs/draft", status_code=202)
async def start_draft(request: Request, force: bool = False):
    state = request.app.state
    queued = state.jobs.submit("patch.draft", lambda: state.patch.draft(force=force))
    return {"queued": queued}


# -- what Patch may do ------------------------------------------------------------------------


class PolicyBody(BaseModel):
    dry_run: bool | None = None
    merge_after_approval: bool | None = None
    kinds: dict[str, str] | None = None


@router.get("/policy")
async def get_policy(request: Request):
    return request.app.state.patch.policy.get() | {"kind_labels": KINDS}


@router.put("/policy")
async def update_policy(request: Request, body: PolicyBody):
    try:
        policy = request.app.state.patch.policy.update(body.model_dump(exclude_none=True))
    except PolicyError as exc:
        raise HTTPException(422, str(exc)) from None
    return policy | {"kind_labels": KINDS}


# -- Claude -----------------------------------------------------------------------------------


@router.get("/claude")
async def claude_status(request: Request):
    state = request.app.state
    status = await state.claude.status()
    return status | {
        "models": {"writing": state.settings.claude_model_write, "small": state.settings.claude_model_small}
    }


@router.post("/claude/test")
async def claude_test(request: Request):
    """The connection test. It makes at most two tiny calls on the small model, on your plan."""
    state = request.app.state
    if not state.test_limiter.allow():
        raise HTTPException(429, "Too many tests. Wait a minute and try again.")
    return await state.claude.test()
