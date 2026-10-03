from tests.conftest import WRITE_HEADERS
from tests.fake_github import VALID_TOKEN


async def run_audit(client, app):
    response = await client.post("/api/jobs/audit", headers=WRITE_HEADERS)
    assert response.status_code == 202 and response.json() == {"queued": True}
    await app.state.jobs.join()


async def test_repos_are_empty_before_the_first_audit(client):
    body = (await client.get("/api/repos")).json()
    assert body["repos"] == [] and body["portfolio"]["score"] is None


async def test_audit_job_fills_the_repos_endpoint(client, app):
    await run_audit(client, app)
    body = (await client.get("/api/repos")).json()

    by_name = {r["name"]: r for r in body["repos"]}
    assert set(by_name) == {"healthy", "messy", "test"}
    assert by_name["healthy"]["score"] == 100 and by_name["healthy"]["ci_state"] == "success"
    assert by_name["messy"]["counts"]["high"] == 1 and by_name["test"]["kind"] == "placeholder"
    assert body["portfolio"]["scored"] == 2 and body["portfolio"]["repos"] == 3


async def test_repo_detail_has_voiced_findings_and_history(client, app):
    await run_audit(client, app)
    body = (await client.get("/api/repos/octo/messy")).json()

    texts = {f["check"]: f["text"] for f in body["findings"]}
    assert texts["license"] == "No license, so nobody can legally reuse it."
    assert texts["topics"] == "No topics. Nobody can find it."
    assert body["history"][0]["score"] == body["repo"]["score"]
    assert body["readme"] == {"path": "README.md", "chars": 14}

    assert (await client.get("/api/repos/octo/nope")).status_code == 404


async def test_same_job_is_not_queued_twice(client, app):
    first = await client.post("/api/jobs/audit", headers=WRITE_HEADERS)
    second = await client.post("/api/jobs/audit", headers=WRITE_HEADERS)
    assert first.json() == {"queued": True} and second.json() == {"queued": False}
    await app.state.jobs.join()
    assert (await client.get("/api/jobs")).json() == {"current": None, "waiting": [], "paused": False}


async def test_starting_an_audit_needs_the_guard_headers(client):
    assert (await client.post("/api/jobs/audit")).status_code == 403


async def test_events_can_be_read_by_run(client, app, db):
    await run_audit(client, app)
    run_id = next(e.run_id for e in db.events_after(0) if e.type == "run.started")
    body = (await client.get(f"/api/events?run_id={run_id}")).json()
    assert body["events"][0]["type"] == "run.started" and body["events"][-1]["type"] == "run.finished"
    assert all(e["run_id"] == run_id for e in body["events"])


# -- setup ------------------------------------------------------------------------------------


async def test_setup_reports_public_mode_without_a_token(client):
    body = (await client.get("/api/setup")).json()
    assert body == {"github": {"configured": False, "user": "octo", "mode": "public"}, "dry_run": True}


async def test_good_token_is_verified_saved_and_never_returned(client, app, settings):
    response = await client.put("/api/setup/github-token", headers=WRITE_HEADERS, json={"token": VALID_TOKEN})
    assert response.status_code == 200 and response.json() == {"user": "octo"}

    assert f"DESK_GITHUB_TOKEN={VALID_TOKEN}" in settings.env_path.read_text(encoding="utf-8")
    assert app.state.settings.github_token == VALID_TOKEN

    for path in ("/api/setup", "/api/health"):
        text = (await client.get(path)).text
        assert VALID_TOKEN not in text
    assert (await client.get("/api/setup")).json()["github"] == {"configured": True, "user": "octo", "mode": "token"}


async def test_bad_token_is_refused_and_not_saved(client, app, settings):
    response = await client.put("/api/setup/github-token", headers=WRITE_HEADERS, json={"token": "github_pat_" + "w" * 40})
    assert response.status_code == 401 and response.json()["detail"] == "GitHub rejected that token."
    assert not settings.env_path.exists() and app.state.settings.github_token is None


async def test_token_attempts_are_rate_limited(client):
    for _ in range(5):
        await client.put("/api/setup/github-token", headers=WRITE_HEADERS, json={"token": "github_pat_" + "w" * 40})
    blocked = await client.put("/api/setup/github-token", headers=WRITE_HEADERS, json={"token": VALID_TOKEN})
    assert blocked.status_code == 429


async def test_removing_the_token_keeps_other_env_lines(client, app, settings):
    settings.env_path.write_text("# my notes\nDESK_DRY_RUN=true\n", encoding="utf-8")
    await client.put("/api/setup/github-token", headers=WRITE_HEADERS, json={"token": VALID_TOKEN})
    response = await client.delete("/api/setup/github-token", headers=WRITE_HEADERS)

    assert response.json() == {"configured": False} and app.state.settings.github_token is None
    text = settings.env_path.read_text(encoding="utf-8")
    assert "DESK_GITHUB_TOKEN" not in text and "# my notes" in text and "DESK_DRY_RUN=true" in text


async def test_audit_uses_a_token_saved_through_setup(client, app, fake):
    await client.put("/api/setup/github-token", headers=WRITE_HEADERS, json={"token": VALID_TOKEN})
    fake.reset()
    await run_audit(client, app)
    assert [c.path for c in fake.calls] == ["/user/repos", "/graphql"]
