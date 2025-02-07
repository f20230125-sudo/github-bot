"""One scheduled check, for running Patch where no desk is open: a scheduled job on GitHub.

Rules only. Claude is never called from here, so it needs no Claude sign-in and spends nothing.
Patch's memory is the database file, which the job carries from one run to the next. Without it
the run is a first look: slower, and nothing in it counts as news.

    python -m app.cloud

When what the public site shows has changed, this rewrites the snapshot and says so, and the job
commits the file. A check that changes nothing leaves the file alone, so it causes no commit and
no new deployment.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from .config import get_settings
from .main import create_app
from .showcase import DEFAULT_OUT, digest, export_snapshot, summary


async def run_check(app: FastAPI) -> dict[str, Any]:
    """Look at GitHub once, and draft what templates can fix if anything changed.

    Returns {"changed": whether anything on GitHub changed, "error": why the look failed, if it did}.
    """
    patch = app.state.patch
    if patch.store.all():
        await patch.watch()
    else:
        await patch.audit()  # nothing to compare with yet: this is the first look
    check = patch.last_check() or {"changed": False, "error": "GitHub was not reached."}
    error = check["error"]
    if check["changed"] and not error:
        # The list was read, but the audit that followed can still have stopped part way.
        finished = app.state.db.latest_of(patch.id, "run.finished")
        if finished and finished.payload.get("ok") is False:
            error = str(finished.payload.get("text"))
    if check["changed"] and not error:
        await patch.draft()  # Claude is not installed here, so only template fixes are drafted
    return {"changed": bool(check["changed"]), "error": error}


async def publish(app: FastAPI, out: Path) -> bool:
    """Write the public snapshot if what it shows has changed. Returns whether it was written."""
    snapshot = await export_snapshot(app)
    snapshot["digest"] = digest(snapshot)
    try:
        published = json.loads(out.read_text(encoding="utf-8")).get("digest")
    except (OSError, ValueError):
        published = None
    if published == snapshot["digest"]:
        return False
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    for line in summary(snapshot):
        print(f"  - {line}")
    return True


def _report(name: str, value: str) -> None:
    """Hand a result to the GitHub Actions job, when there is one."""
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


async def _main(out: Path) -> int:
    app = create_app(get_settings())
    try:
        result = await run_check(app)
        if result["error"]:
            print(f"The check failed: {result['error']}")
            _report("published", "false")
            return 1
        print("Something changed on GitHub." if result["changed"] else "Nothing changed on GitHub.")
        published = await publish(app, out)
        print(f"Snapshot {'rewritten: ' + str(out) if published else 'left as it is: nothing it shows has changed'}.")
        _report("published", "true" if published else "false")
        return 0
    finally:
        app.state.db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one scheduled check and refresh the public snapshot.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where the public snapshot lives")
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args.out)))


if __name__ == "__main__":
    main()
