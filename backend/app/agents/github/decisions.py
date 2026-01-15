"""Your edits to a proposal, checked before it is approved.

You can switch items off, reword a description, change topics, or edit a file. Whatever you
change is cleaned by the same rules as Claude's drafts and recorded, so Patch can learn from it.
"""

from __future__ import annotations

from typing import Any

from ...core.proposals import Proposal
from .drafts import clean_topics

DESCRIPTION_LIMIT = 350  # GitHub's own limit
FILE_LIMIT = 200_000


class DecisionError(ValueError):
    """The edit can't be accepted. The message says why, in words fit to show you."""


def apply_edits(proposal: Proposal, edits: dict[str, Any] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Returns the payload to approve and a list of what you changed."""
    payload = {**proposal.payload}
    changes: list[dict[str, Any]] = []
    edits = edits or {}

    if proposal.kind == "metadata_sweep":
        by_repo = {e["repo"]: e for e in edits.get("items") or []}
        unknown = set(by_repo) - {item["repo"] for item in payload["items"]}
        if unknown:
            raise DecisionError(f"Not part of this proposal: {', '.join(sorted(unknown))}.")
        payload["items"] = [_edit_item(dict(item), by_repo.get(item["repo"]), changes) for item in payload["items"]]
        enabled = [i for i in payload["items"] if i["enabled"]]
    else:
        by_path = {e["path"]: e for e in edits.get("files") or []}
        unknown = set(by_path) - {f["path"] for f in payload["files"]}
        if unknown:
            raise DecisionError(f"Not part of this proposal: {', '.join(sorted(unknown))}.")
        payload["files"] = [_edit_file(dict(file), by_path.get(file["path"]), changes) for file in payload["files"]]
        enabled = [f for f in payload["files"] if f["enabled"]]

    if not enabled:
        raise DecisionError("Everything in this proposal is switched off. Reject it instead.")
    return payload, changes


def _edit_item(item: dict[str, Any], edit: dict[str, Any] | None, changes: list[dict[str, Any]]) -> dict[str, Any]:
    if edit is None:
        return item
    repo = item["repo"]

    if edit.get("enabled") is False:
        item["enabled"] = False
        changes.append({"repo": repo, "field": "enabled", "from": True, "to": False})
        return item

    if "description" in edit and item.get("description") is not None:
        text = " ".join((edit["description"] or "").split())
        if len(text) > DESCRIPTION_LIMIT:
            raise DecisionError(f"The description for {repo} is longer than GitHub's {DESCRIPTION_LIMIT} characters.")
        new = text or None  # cleared: leave the repository's description alone
        if new != item["description"]:
            changes.append({"repo": repo, "field": "description", "from": item["description"], "to": new})
            item["description"] = new

    if "topics" in edit and item.get("topics") is not None:
        new_topics = clean_topics(edit["topics"] or [], []) or None
        if new_topics != item["topics"]:
            changes.append({"repo": repo, "field": "topics", "from": item["topics"], "to": new_topics})
            item["topics"] = new_topics

    if item.get("description") is None and item.get("topics") is None:
        item["enabled"] = False  # nothing left to apply for this repository
    return item


def _edit_file(file: dict[str, Any], edit: dict[str, Any] | None, changes: list[dict[str, Any]]) -> dict[str, Any]:
    if edit is None:
        return file
    path = file["path"]

    if edit.get("enabled") is False:
        file["enabled"] = False
        changes.append({"path": path, "field": "enabled", "from": True, "to": False})
        return file

    content = edit.get("content")
    if content is not None:
        if not isinstance(content, str) or not content.strip():
            raise DecisionError(f"{path} can't be empty. Switch it off instead.")
        if len(content) > FILE_LIMIT:
            raise DecisionError(f"{path} is too large.")
        if content != file["content"]:
            changes.append({"path": path, "field": "content", "from": file["content"], "to": content})
            file["content"] = content
    return file
