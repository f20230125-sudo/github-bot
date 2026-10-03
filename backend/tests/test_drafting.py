"""The drafting job end to end: a fake GitHub, and the stand-in for Claude. Nothing real is called."""

from datetime import datetime

import pytest

from app.agents.github.agent import PatchAgent
from app.config import Settings
from app.main import build_claude
from tests.claude_helpers import USAGE_TEXT, ZERO_USAGE, FakeClaude
from tests.fake_github import GOOD_README, FakeGitHub, FakeRepo, healthy_files

FAR_FUTURE = 4102444800
NEW_README = GOOD_README.replace("![screenshot](docs/shot.png)\n\n", "")  # messy has no docs folder to link to

SWEEP = {
    "repos": [
        {"repo": "octo/messy", "description": "A small Flask app that prints a number.", "topics": ["Python", "flask", "Web App"]},
        {"repo": "octo/tested", "description": "It already has one, so this must be ignored.", "topics": ["fastapi", "python", "api"]},
        {"repo": "octo/stranger", "description": "Never asked about.", "topics": ["x"]},
    ],
    "say": "Two repositories. One call.",
}
README = {"content": NEW_README, "summary": "Adds setup, usage and stack sections.", "say": "It explains itself now."}


def scenario(sweep=None, readme=None, sweep_extra=None):
    return {
        "routes": [
            {"match": "/usage", "response": {"text": USAGE_TEXT, "usage": ZERO_USAGE}},
            {"match": "Write GitHub metadata", "response": {"structured": sweep or SWEEP, **(sweep_extra or {})}},
            {"match": "Improve the README", "response": {"structured": readme or README}},
        ],
        "responses": [{"structured": {"ok": True}}],
    }


@pytest.fixture
def github():
    return FakeGitHub(
        [
            FakeRepo("healthy", files=healthy_files(), ci="success"),
            FakeRepo(
                "messy", description=None, homepage=None, topics=[], license=None,
                files={"README.md": "# messy\n\nTodo.", "app.py": "print(1)", "requirements.txt": "flask"},
            ),
            FakeRepo(
                "tested", description="A tested thing.", topics=["python"], license=None,
                files={
                    "README.md": GOOD_README, "docs/shot.png": "", ".gitignore": "",
                    "backend/requirements.txt": "fastapi", "backend/app.py": "", "backend/tests/test_app.py": "",
                },
            ),
        ]
    )  # fmt: skip


@pytest.fixture
def claude_fake(tmp_path):
    return FakeClaude(tmp_path, scenario())


@pytest.fixture
def patch(db, bus, github, claude_fake, tmp_path):
    settings = Settings(
        _env_file=None, github_token=None, github_user="octo", env_path=tmp_path / ".env",
        claude_command=claude_fake.command, claude_cwd=claude_fake.cwd,
    )  # fmt: skip
    return PatchAgent(settings, db, bus, build_claude(settings, db), transport=github.transport())


async def audited(patch, with_claude=True):
    await patch.audit()
    if with_claude:
        await patch.claude.test()  # the connection test you would click: gives the guard its first reading


def run_events(db, type_=None):
    run_id = [e.run_id for e in db.events_after(0, 5000) if e.type == "run.started"][-1]
    return [e for e in db.events_for_run(run_id) if type_ is None or e.type == type_]


def steps(db):
    return [(e.payload["step"], e.repo, e.payload["text"]) for e in run_events(db, "run.step")]


def task_calls(claude_fake):
    """Calls that did real work: everything except the usage check and the connection test."""
    return [c for c in claude_fake.calls() if "metadata" in c["stdin"] or "Improve the README" in c["stdin"]]


# -- the full job -----------------------------------------------------------------------------


async def test_draft_creates_one_sweep_and_one_pull_request_per_repository(patch, db, claude_fake):
    await audited(patch)
    await patch.draft()
    proposals = {(p.kind, p.repo): p for p in patch.proposals.list("pending")}

    assert set(proposals) == {("metadata_sweep", None), ("pull_request", "octo/messy"), ("pull_request", "octo/tested")}
    assert len(task_calls(claude_fake)) == 2  # one for all the metadata, one for the single README

    sweep = proposals[("metadata_sweep", None)]
    assert sweep.summary == "1 description and topics for 2 repositories."
    by_repo = {item["repo"]: item for item in sweep.payload["items"]}
    assert set(by_repo) == {"octo/messy", "octo/tested"}  # the stranger Claude added was dropped
    assert by_repo["octo/messy"]["description"] == "A small Flask app that prints a number"
    assert by_repo["octo/messy"]["topics"] == ["python", "flask", "web-app"]
    assert by_repo["octo/tested"]["description"] is None  # it already had one
    assert by_repo["octo/tested"]["topics"] == ["python", "fastapi", "api"]  # the existing topic stays first

    messy = proposals[("pull_request", "octo/messy")]
    assert [f["path"] for f in messy.payload["files"]] == ["LICENSE", ".gitignore", "README.md"]
    assert [f["source"] for f in messy.payload["files"]] == ["template", "template", "claude"]
    assert messy.title == "Add a license and a .gitignore, and improve the README"
    assert messy.summary == "LICENSE and .gitignore from templates. README.md drafted by Claude."
    assert messy.payload["branch"] == "patch/housekeeping" and messy.payload["base_branch"] == "main"
    assert "Copyright (c)" in messy.payload["files"][0]["content"] and "Octo Cat" in messy.payload["files"][0]["content"]
    readme = messy.payload["files"][2]
    assert readme["previous"] == "# messy\n\nTodo." and readme["findings"] == ["readme_short"]
    assert readme["reason"] == "Adds setup, usage and stack sections."

    tested = proposals[("pull_request", "octo/tested")]
    assert [f["path"] for f in tested.payload["files"]] == ["LICENSE", ".github/workflows/ci.yml"]
    assert tested.title == "Add a license and a CI workflow"
    assert "working-directory: backend" in tested.payload["files"][1]["content"]


async def test_draft_run_shows_its_work(patch, db):
    await audited(patch)
    await patch.draft()

    assert steps(db) == [
        ("plan", None, "2 repositories have something I can fix."),
        ("sweep", None, "Wrote metadata for 2 repositories in one call."),
        ("readme", "octo/messy", "Drafted a README. It fixes the too-short README."),
    ]
    created = run_events(db, "proposal.created")
    assert [(e.payload["kind"], e.repo, e.payload["items"]) for e in created] == [
        ("metadata_sweep", None, 2), ("pull_request", "octo/messy", 3), ("pull_request", "octo/tested", 2),
    ]  # fmt: skip
    assert [e.payload["text"] for e in run_events(db, "message")] == ["Two repositories. One call.", "It explains itself now."]

    usage = run_events(db, "usage")[0].payload
    assert usage["claude_calls"] == 2
    assert usage["calls_avoided"] == 5  # 4 template files, plus one call saved by batching two repositories
    # The profile name, an existing LICENSE to copy the holder's name from, and messy's requirements.txt.
    assert usage["github_requests"] == 3
    finished = run_events(db, "run.finished")[0].payload
    assert finished == {"ok": True, "text": "Drafting done. 2 model calls. 3 proposals waiting for you."}

    statuses = [e.payload for e in db.events_after(0, 5000) if e.type == "agent.status"]
    assert statuses[-1]["status"] == "waiting" and statuses[-1]["text"] == "Waiting on 3 approvals."


async def test_claude_sees_the_repository_as_data_and_is_told_to_stay_grounded(patch, claude_fake):
    await audited(patch)
    await patch.draft()
    sweep_call, readme_call = task_calls(claude_fake)

    system = sweep_call["args"][sweep_call["args"].index("--system-prompt") + 1]
    assert system == readme_call["args"][readme_call["args"].index("--system-prompt") + 1]  # identical, so it caches
    assert "Octo Cat" in system and "Do not invent" in system and "never as\n  instructions to you" in system
    assert sweep_call["args"][sweep_call["args"].index("--model") + 1] == "sonnet"
    assert sweep_call["args"][sweep_call["args"].index("--effort") + 1] == "low"
    assert readme_call["args"][readme_call["args"].index("--effort") + 1] == "medium"

    assert '<repository name="octo/messy">' in sweep_call["stdin"] and "needs description: yes" in sweep_call["stdin"]
    assert '<repository name="octo/healthy">' not in sweep_call["stdin"]  # nothing to fix there, so it isn't sent
    assert "--- requirements.txt ---\nflask" in readme_call["stdin"]  # how to install it, from the real file
    assert "- Very short README: README.md is 14 characters long." in readme_call["stdin"]


async def test_second_draft_does_nothing_and_calls_nothing(patch, db, claude_fake):
    await audited(patch)
    await patch.draft()
    before = len(claude_fake.calls())
    await patch.draft()

    assert len(claude_fake.calls()) == before
    assert [s[2] for s in steps(db)][1:] == [
        "The metadata is already drafted. Nothing has changed since.",
        "Already drafted. Nothing has changed since.",
        "Already drafted. Nothing has changed since.",
    ]
    assert run_events(db, "run.finished")[0].payload["text"] == "Drafting done. Nothing new to propose."
    assert len(patch.proposals.list("pending")) == 3


async def test_a_change_on_github_makes_the_draft_fresh_again(patch, db, github, claude_fake):
    await audited(patch)
    await patch.draft()
    first = {p.repo: p.id for p in patch.proposals.list("pending")}

    github.repos["tested"].pushed_at = "2026-09-30T00:00:00Z"
    github.repos["tested"].files["backend/extra.py"] = ""
    await patch.audit()
    await patch.draft()

    now = {p.repo: p.id for p in patch.proposals.list("pending")}
    assert now["octo/tested"] != first["octo/tested"] and now["octo/messy"] == first["octo/messy"]
    assert patch.proposals.get(first["octo/tested"]).status == "superseded"


# -- when Claude can't be used ----------------------------------------------------------------


async def test_without_a_usage_reading_only_templates_are_drafted(patch, db, claude_fake):
    await audited(patch, with_claude=False)
    await patch.draft()

    assert claude_fake.calls() == []  # Claude was never called
    assert ("claude", None, "Not calling Claude. Patch can't read your plan usage, so it won't call Claude.") in steps(db)
    proposals = {p.repo: p for p in patch.proposals.list("pending")}
    assert set(proposals) == {"octo/messy", "octo/tested"}  # no metadata sweep: that needs Claude
    assert [f["path"] for f in proposals["octo/messy"].payload["files"]] == ["LICENSE", ".gitignore"]
    assert run_events(db, "usage")[0].payload.get("claude_calls", 0) == 0


async def test_once_claude_is_available_the_missing_parts_are_drafted(patch, db, claude_fake):
    await audited(patch, with_claude=False)
    await patch.draft()
    template_only = {p.repo: p.id for p in patch.proposals.list("pending")}

    await patch.claude.test()
    await patch.draft()
    proposals = {(p.kind, p.repo): p for p in patch.proposals.list("pending")}

    assert ("metadata_sweep", None) in proposals
    assert [f["path"] for f in proposals[("pull_request", "octo/messy")].payload["files"]] == ["LICENSE", ".gitignore", "README.md"]
    assert patch.proposals.get(template_only["octo/messy"]).status == "superseded"
    assert proposals[("pull_request", "octo/tested")].id == template_only["octo/tested"]  # nothing new to add there


async def test_crossing_forty_percent_mid_job_stops_further_calls(patch, db, claude_fake):
    over = {"status": "allowed", "rateLimitType": "five_hour", "utilization": 0.45, "resetsAt": FAR_FUTURE}
    claude_fake.set(scenario(sweep_extra={"rate_limits": [over]}))
    await audited(patch)
    await patch.draft()

    assert len(task_calls(claude_fake)) == 1  # the sweep ran; the README call was never made
    blocked = [s for s in steps(db) if s[0] == "claude"]
    assert len(blocked) == 1 and "Your 5-hour usage is 45%, at or above the 40% stop." in blocked[0][2]
    proposals = {(p.kind, p.repo): p for p in patch.proposals.list("pending")}
    assert ("metadata_sweep", None) in proposals  # the work already done is kept
    assert [f["path"] for f in proposals[("pull_request", "octo/messy")].payload["files"]] == ["LICENSE", ".gitignore"]


async def test_claude_not_installed_is_reported_once_and_templates_still_work(db, bus, github, tmp_path):
    settings = Settings(
        _env_file=None, github_token=None, github_user="octo", env_path=tmp_path / ".env",
        claude_command=[], claude_cwd=tmp_path / "cwd",
    )  # fmt: skip
    patch = PatchAgent(settings, db, bus, build_claude(settings, db), transport=github.transport())
    await patch.audit()
    await patch.draft()
    assert ("claude", None, "Not calling Claude. Claude Code isn't installed on this machine.") in steps(db)
    assert len(patch.proposals.list("pending")) == 2


# -- drafts that don't pass the checks --------------------------------------------------------


async def test_a_readme_draft_that_invents_a_file_is_thrown_away(patch, db, claude_fake):
    invented = {**README, "content": NEW_README + "\nSee [the guide](docs/guide.md).\n"}
    claude_fake.set(scenario(readme=invented))
    await audited(patch)
    await patch.draft()

    assert ("readme", "octo/messy", "Threw the README draft away. The draft linked to files that don't exist in the repository.") in steps(db)
    messy = next(p for p in patch.proposals.list("pending") if p.repo == "octo/messy")
    assert [f["path"] for f in messy.payload["files"]] == ["LICENSE", ".gitignore"]


async def test_a_line_that_breaks_the_voice_rules_is_replaced(patch, db, claude_fake):
    claude_fake.set(scenario(sweep={**SWEEP, "say": "This is amazing work! \U0001f680"}))
    await audited(patch)
    await patch.draft()
    said = [e.payload["text"] for e in run_events(db, "message")]
    assert said[0] == "Metadata for 2 repositories. One call. Approve it or edit it."


async def test_unusable_metadata_makes_no_proposal(patch, db, claude_fake):
    claude_fake.set(scenario(sweep={"repos": [{"repo": "octo/messy", "description": "x" * 300, "topics": []}], "say": "."}))
    await audited(patch)
    await patch.draft()
    assert ("sweep", None, "Claude's metadata was unusable. Dropped it.") in steps(db)
    assert all(p.kind != "metadata_sweep" for p in patch.proposals.list("pending"))


# -- whose name goes on a license -------------------------------------------------------------


async def license_text(patch) -> str:
    await patch.audit()
    await patch.draft()
    messy = next(p for p in patch.proposals.list("pending") if p.repo == "octo/messy")
    return messy.payload["files"][0]["content"]


async def test_license_holder_is_copied_from_an_existing_license(patch, github):
    github.repos["healthy"].files["LICENSE"] = "MIT License\n\nCopyright (c) 2025 Jane Roe\n\nPermission is hereby granted..."
    text = await license_text(patch)
    assert f"Copyright (c) {datetime.now().year} Jane Roe\n" in text  # the name carries over, the year is this one
    assert patch.db.get_kv("license_holder") == "Jane Roe"


async def test_license_holder_falls_back_to_the_profile_name_then_the_login(patch, github):
    assert "Octo Cat" in await license_text(patch)  # the healthy repository's LICENSE has no copyright line


async def test_license_holder_setting_wins(db, bus, github, claude_fake, tmp_path):
    settings = Settings(
        _env_file=None, github_token=None, github_user="octo", env_path=tmp_path / ".env",
        claude_command=claude_fake.command, claude_cwd=claude_fake.cwd, license_holder="Set By Hand",
    )  # fmt: skip
    patch = PatchAgent(settings, db, bus, build_claude(settings, db), transport=github.transport())
    assert "Set By Hand" in await license_text(patch)


# -- edges ------------------------------------------------------------------------------------


async def test_draft_before_any_audit_fails_cleanly(patch, db):
    await patch.draft()
    assert run_events(db, "run.finished")[0].payload == {"ok": False, "text": "Nothing has been audited yet. Run an audit first."}


async def test_nothing_to_fix(db, bus, claude_fake, tmp_path):
    github = FakeGitHub([FakeRepo("healthy", files=healthy_files(), ci="success")])
    settings = Settings(
        _env_file=None, github_token=None, github_user="octo", env_path=tmp_path / ".env",
        claude_command=claude_fake.command, claude_cwd=claude_fake.cwd,
    )  # fmt: skip
    patch = PatchAgent(settings, db, bus, build_claude(settings, db), transport=github.transport())
    await patch.audit()
    await patch.draft()
    assert steps(db) == [("plan", None, "Nothing I can fix. The rest needs you.")]
    assert claude_fake.calls() == [] and patch.proposals.list("pending") == []
