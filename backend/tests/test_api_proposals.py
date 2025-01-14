"""The proposals and Claude endpoints, with the stand-ins for GitHub and Claude."""

import httpx
import pytest

from app.api.proposals import diff_lines
from app.config import Settings
from app.main import create_app
from tests.claude_helpers import FakeClaude
from tests.conftest import HOST, ORIGIN, WRITE_HEADERS
from tests.test_drafting import scenario


@pytest.fixture
def claude_fake(tmp_path):
    return FakeClaude(tmp_path, scenario())


@pytest.fixture
def app(db, fake, claude_fake, tmp_path):
    settings = Settings(
        _env_file=None, db_path=":memory:", github_token=None, github_user="octo", frontend_origin=ORIGIN,
        port=8010, env_path=tmp_path / ".env", claude_command=claude_fake.command, claude_cwd=claude_fake.cwd,
    )  # fmt: skip
    return create_app(settings, db=db, github_transport=fake.transport())


@pytest.fixture
async def client(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{HOST}") as c:
        yield c


async def job(client, app, path):
    response = await client.post(path, headers=WRITE_HEADERS)
    assert response.status_code == 202
    await app.state.jobs.join()


# -- Claude status and the connection test ----------------------------------------------------


async def test_claude_status_before_any_reading(client):
    body = (await client.get("/api/claude")).json()
    assert body["auth"] == {
        "installed": True, "signed_in": True, "method": "claude.ai", "plan": "pro", "ok": True, "problem": None,
    }  # fmt: skip
    assert body["guard"]["allowed"] is False and body["guard"]["code"] == "no_reading"
    assert body["guard"]["limits"] == {"session": 40.0, "weekly": 40.0}
    assert body["models"] == {"writing": "sonnet", "small": "haiku"}


async def test_connection_test_gives_the_guard_a_reading(client, claude_fake):
    assert (await client.post("/api/claude/test")).status_code == 403  # a write: needs the guard headers

    report = (await client.post("/api/claude/test", headers=WRITE_HEADERS)).json()
    assert report["usage"]["ok"] and report["usage"]["answered_locally"] and report["structured"]["ok"]
    assert report["guard"]["allowed"] is True
    assert report["guard"]["windows"]["session"]["percent"] == 12 and report["guard"]["windows"]["weekly"]["percent"] == 30
    assert len(claude_fake.calls()) == 2

    status = (await client.get("/api/claude")).json()
    assert status["guard"]["reason"] == "Weekly usage is 30%, under the 40% stop."
    assert len(claude_fake.calls()) == 2  # reading the status calls nothing


async def test_connection_test_is_rate_limited(client):
    for _ in range(3):
        await client.post("/api/claude/test", headers=WRITE_HEADERS)
    assert (await client.post("/api/claude/test", headers=WRITE_HEADERS)).status_code == 429


# -- proposals --------------------------------------------------------------------------------


async def test_draft_job_fills_the_proposals_endpoint(client, app):
    await job(client, app, "/api/jobs/audit")
    await client.post("/api/claude/test", headers=WRITE_HEADERS)
    await job(client, app, "/api/jobs/draft")

    proposals = (await client.get("/api/proposals")).json()["proposals"]
    assert [(p["kind"], p["repo"], p["status"]) for p in proposals] == [
        ("pull_request", "octo/messy", "pending"),
        ("metadata_sweep", None, "pending"),
    ]
    sweep = next(p for p in proposals if p["kind"] == "metadata_sweep")
    assert sweep["title"] == "Metadata sweep" and sweep["items"] == 1

    detail = (await client.get(f"/api/proposals/{proposals[0]['id']}")).json()
    files = {f["path"]: f for f in detail["payload"]["files"]}
    assert set(files) == {"LICENSE", ".gitignore", "README.md"}
    assert files["LICENSE"]["is_new"] and files["LICENSE"]["diff"][0] == "+MIT License"
    assert files["README.md"]["is_new"] is False
    assert "-Todo." in files["README.md"]["diff"] and "+## Setup" in files["README.md"]["diff"]

    assert (await client.get("/api/proposals/9999")).status_code == 404
    assert (await client.get("/api/proposals?status=applied")).json() == {"proposals": []}
    assert len((await client.get("/api/proposals?status=all")).json()["proposals"]) == 2


async def test_draft_job_needs_the_guard_headers_and_is_not_queued_twice(client, app):
    assert (await client.post("/api/jobs/draft")).status_code == 403
    first = await client.post("/api/jobs/draft", headers=WRITE_HEADERS)
    second = await client.post("/api/jobs/draft", headers=WRITE_HEADERS)
    assert first.json() == {"queued": True} and second.json() == {"queued": False}
    await app.state.jobs.join()


# -- deciding ---------------------------------------------------------------------------------


async def drafted(client, app) -> dict:
    await job(client, app, "/api/jobs/audit")
    await client.post("/api/claude/test", headers=WRITE_HEADERS)
    await job(client, app, "/api/jobs/draft")
    proposals = (await client.get("/api/proposals")).json()["proposals"]
    return {p["kind"]: p["id"] for p in proposals}


async def test_approve_applies_as_a_rehearsal_while_dry_run_is_on(client, app, fake):
    ids = await drafted(client, app)
    assert (await client.post(f"/api/proposals/{ids['metadata_sweep']}/approve")).status_code == 403

    response = await client.post(f"/api/proposals/{ids['metadata_sweep']}/approve", headers=WRITE_HEADERS)
    assert response.status_code == 202 and response.json()["status"] == "approved"
    await app.state.jobs.join()

    detail = (await client.get(f"/api/proposals/{ids['metadata_sweep']}")).json()
    assert detail["status"] == "approved" and detail["result"]["dry_run"] is True
    assert [a["text"] for a in detail["result"]["actions"]] == ["Would set the description.", "Would set 3 topics."]
    assert fake.writes == []
    assert (await client.get("/api/health")).json()["dry_run"] is True


async def test_approve_with_edits_and_bad_edits(client, app):
    ids = await drafted(client, app)
    sweep = ids["metadata_sweep"]

    bad = await client.post(
        f"/api/proposals/{sweep}/approve", headers=WRITE_HEADERS, json={"items": [{"repo": "octo/messy", "enabled": False}]}
    )
    assert bad.status_code == 422 and bad.json()["detail"] == "Everything in this proposal is switched off. Reject it instead."

    good = await client.post(
        f"/api/proposals/{sweep}/approve", headers=WRITE_HEADERS,
        json={"items": [{"repo": "octo/messy", "description": "Edited by hand."}]},
    )  # fmt: skip
    assert good.status_code == 202 and good.json()["decision"]["edits"][0]["to"] == "Edited by hand."
    await app.state.jobs.join()

    again = await client.post(f"/api/proposals/{sweep}/approve", headers=WRITE_HEADERS)
    assert again.status_code == 409 and again.json()["detail"] == "This proposal is already approved."
    assert (await client.post("/api/proposals/9999/approve", headers=WRITE_HEADERS)).status_code == 404


async def test_reject(client, app):
    ids = await drafted(client, app)
    response = await client.post(
        f"/api/proposals/{ids['pull_request']}/reject", headers=WRITE_HEADERS, json={"reason": "Not now."}
    )
    assert response.status_code == 200 and response.json()["status"] == "rejected"
    assert response.json()["decision"]["reason"] == "Not now."
    assert (await client.post(f"/api/proposals/{ids['pull_request']}/reject", headers=WRITE_HEADERS)).status_code == 409
    assert (await client.post(f"/api/proposals/{ids['pull_request']}/apply", headers=WRITE_HEADERS)).status_code == 409


async def test_apply_again_after_switching_dry_run_off(client, app, fake):
    ids = await drafted(client, app)
    sweep = ids["metadata_sweep"]
    await client.post(f"/api/proposals/{sweep}/approve", headers=WRITE_HEADERS)
    await app.state.jobs.join()

    policy = await client.put("/api/policy", headers=WRITE_HEADERS, json={"dry_run": False})
    assert policy.status_code == 200 and policy.json()["dry_run"] is False
    assert (await client.get("/api/health")).json()["dry_run"] is False

    response = await client.post(f"/api/proposals/{sweep}/apply", headers=WRITE_HEADERS)
    assert response.status_code == 202 and response.json() == {"queued": True}
    await app.state.jobs.join()
    # No token in this app, so a real apply is refused rather than attempted.
    detail = (await client.get(f"/api/proposals/{sweep}")).json()
    assert detail["status"] == "approved" and fake.writes == []


async def test_policy_endpoint(client):
    body = (await client.get("/api/policy")).json()
    assert body["dry_run"] is True and body["merge_after_approval"] is False
    assert body["kinds"] == {"metadata_sweep": "ask", "pull_request": "ask"}
    assert body["kind_labels"]["pull_request"] == "Open pull requests with file changes"

    assert (await client.put("/api/policy", json={"dry_run": False})).status_code == 403  # needs the guard headers
    bad = await client.put("/api/policy", headers=WRITE_HEADERS, json={"kinds": {"pull_request": "yolo"}})
    assert bad.status_code == 422
    changed = await client.put("/api/policy", headers=WRITE_HEADERS, json={"kinds": {"pull_request": "never"}})
    assert changed.json()["kinds"] == {"metadata_sweep": "ask", "pull_request": "never"}


def test_diff_lines():
    assert diff_lines(None, "a\nb", "f") == ["+a", "+b"]
    changed = diff_lines("one\ntwo\nthree", "one\n2\nthree", "f")
    assert changed == ["@@ -1,3 +1,3 @@", " one", "-two", "+2", " three"]
    assert diff_lines("same", "same", "f") == []
