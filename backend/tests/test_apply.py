"""Approving and applying proposals, against the fake GitHub. This is the only path that writes."""

import pytest

from app.agents.github.agent import PatchAgent
from app.agents.github.client import GitHubClient, WriteRefused, check_write
from app.agents.github.decisions import DecisionError
from app.config import Settings
from app.main import build_claude
from tests.claude_helpers import FakeClaude
from tests.fake_github import VALID_TOKEN, FakeGitHub, FakeRepo, healthy_files
from tests.test_drafting import scenario


@pytest.fixture
def github():
    return FakeGitHub(
        [
            FakeRepo("healthy", files=healthy_files(), ci="success"),
            FakeRepo(
                "messy", description=None, homepage=None, topics=[], license=None,
                files={"README.md": "# messy\n\nTodo.", "app.py": "print(1)", "requirements.txt": "flask"},
            ),
            FakeRepo("tested", description="A tested thing.", topics=["python"], files=healthy_files(), ci="success"),
        ]
    )  # fmt: skip


def make_patch(db, bus, github, tmp_path, token=VALID_TOKEN, **overrides) -> PatchAgent:
    fake = FakeClaude(tmp_path, scenario())
    settings = Settings(
        _env_file=None, github_token=token, github_user="octo", env_path=tmp_path / ".env",
        claude_command=fake.command, claude_cwd=fake.cwd, github_write_delay=0, **overrides,
    )  # fmt: skip
    return PatchAgent(settings, db, bus, build_claude(settings, db), transport=github.transport())


@pytest.fixture
def patch(db, bus, github, tmp_path):
    return make_patch(db, bus, github, tmp_path)


async def drafted(patch) -> dict:
    """Audit, give the guard a reading, draft. Returns the pending proposals by (kind, repo)."""
    await patch.audit()
    await patch.claude.test()
    await patch.draft()
    return {(p.kind, p.repo): p for p in patch.proposals.list("pending")}


def last_run(db, type_=None):
    run_id = [e.run_id for e in db.events_after(0, 5000) if e.type == "run.started" and e.payload["job"] == "apply"][-1]
    return [e for e in db.events_for_run(run_id) if type_ is None or e.type == type_]


def texts(db):
    return [(e.repo, e.payload["text"]) for e in last_run(db, "action.applied")]


# -- dry-run: a rehearsal that writes nothing -------------------------------------------------


async def test_dry_run_is_on_by_default_and_writes_nothing(patch, db, github):
    proposals = await drafted(patch)
    sweep = proposals[("metadata_sweep", None)]
    await patch.approve(sweep.id)
    outcome = await patch.apply(sweep.id)

    assert outcome == "dry_run" and github.writes == []
    assert texts(db) == [
        ("octo/messy", "Would set the description."),
        ("octo/messy", "Would set 3 topics."),
        ("octo/tested", "Would set 3 topics."),
    ]
    assert last_run(db, "run.finished")[0].payload == {
        "ok": True, "text": "Dry-run. 3 changes would be made. Nothing was written.",
    }  # fmt: skip
    after = patch.proposals.get(sweep.id)
    assert after.status == "approved" and after.result["dry_run"] is True and len(after.result["actions"]) == 3
    assert github.repos["messy"].description is None  # untouched


async def test_dry_run_rehearses_a_pull_request_too(patch, db, github):
    proposals = await drafted(patch)
    pull = proposals[("pull_request", "octo/messy")]
    await patch.approve(pull.id)
    assert await patch.apply(pull.id) == "dry_run"
    assert texts(db) == [("octo/messy", "Would open a pull request with 3 files.")]
    assert github.writes == [] and github.pulls == []


# -- real writes ------------------------------------------------------------------------------


async def test_applying_a_sweep_sets_descriptions_and_topics(patch, db, github):
    proposals = await drafted(patch)
    patch.policy.update({"dry_run": False})
    sweep = proposals[("metadata_sweep", None)]
    await patch.approve(sweep.id)
    before = patch.store.get("octo/messy").score

    assert await patch.apply(sweep.id) == "applied"

    assert [(w.method, w.path, w.body) for w in github.writes] == [
        ("PATCH", "/repos/octo/messy", {"description": "A small Flask app that prints a number"}),
        ("PUT", "/repos/octo/messy/topics", {"names": ["python", "flask", "web-app"]}),
        ("PUT", "/repos/octo/tested/topics", {"names": ["python", "fastapi", "api"]}),
    ]
    assert patch.proposals.get(sweep.id).status == "applied"
    assert texts(db) == [
        ("octo/messy", "Set the description."), ("octo/messy", "Set 3 topics."), ("octo/tested", "Set 3 topics."),
    ]  # fmt: skip

    # The follow-up audit sees the result: the findings are gone and the score moved.
    messy = patch.store.get("octo/messy")
    assert not {"description", "topics"} & {f.check for f in messy.findings}
    assert messy.score == before + 8 + 6
    resolved = [e.payload["check"] for e in db.events_after(0, 5000) if e.type == "finding" and e.payload["status"] == "resolved"]
    assert sorted(resolved) == ["description", "topics", "topics"]


async def test_applying_a_pull_request_opens_one_from_a_patch_branch(patch, db, github):
    proposals = await drafted(patch)
    patch.policy.update({"dry_run": False})
    pull = proposals[("pull_request", "octo/messy")]
    await patch.approve(pull.id)

    assert await patch.apply(pull.id) == "applied"

    assert [w.path.rsplit("/", 1)[-1] for w in github.writes] == ["trees", "commits", "refs", "pulls"]
    ref = next(w for w in github.writes if w.path.endswith("/git/refs"))
    assert ref.body["ref"] == f"refs/heads/patch/housekeeping-{pull.id}"

    opened = github.pulls[0]
    assert opened["base"] == "main" and opened["head"] == f"patch/housekeeping-{pull.id}"
    assert opened["title"] == "Add a license and a .gitignore, and improve the README"
    assert set(opened["files"]) == {"LICENSE", ".gitignore", "README.md"} and opened["merged"] is False
    assert opened["body"] == (
        "Housekeeping changes from an automated audit of this repository.\n\n"
        "- `LICENSE`: Adds the MIT license, so others know how they may use the code.\n"
        "- `.gitignore`: Adds a .gitignore so build output, caches and local environment files stay out of the repository.\n"
        "- `README.md`: Adds setup, usage and stack sections.\n\n"
        "Drafted by Patch, an automated maintenance agent, and approved by @octo before this pull request was opened.\n"
    )
    after = patch.proposals.get(pull.id)
    assert after.status == "applied" and after.result["number"] == 1
    assert after.result["url"] == "https://github.com/octo/messy/pull/1"
    assert texts(db) == [("octo/messy", "Opened pull request #1.")]
    assert github.repos["messy"].files["README.md"] == "# messy\n\nTodo."  # main is untouched until you merge


async def test_merge_after_approval_is_off_unless_switched_on(patch, db, github):
    proposals = await drafted(patch)
    patch.policy.update({"dry_run": False, "merge_after_approval": True})
    pull = proposals[("pull_request", "octo/messy")]
    await patch.approve(pull.id)
    await patch.apply(pull.id)

    assert github.pulls[0]["merged"] is True and github.writes[-1].body == {"merge_method": "squash"}
    assert texts(db) == [("octo/messy", "Opened pull request #1."), ("octo/messy", "Merged pull request #1.")]
    assert "license" not in {f.check for f in patch.store.get("octo/messy").findings}  # seen by the follow-up audit


async def test_a_merge_that_github_refuses_leaves_the_pull_request_open(patch, db, github):
    proposals = await drafted(patch)
    patch.policy.update({"dry_run": False, "merge_after_approval": True})
    github.merge_blocked = True
    pull = proposals[("pull_request", "octo/messy")]
    await patch.approve(pull.id)

    assert await patch.apply(pull.id) == "applied"
    assert github.pulls[0]["merged"] is False
    assert texts(db)[-1][1].startswith("Opened pull request #1, but couldn't merge it:")
    assert last_run(db, "run.finished")[0].payload["text"] == "Applied 1 change. 1 other did not go through."


# -- staleness --------------------------------------------------------------------------------


async def test_a_repository_that_changed_since_the_draft_is_skipped(patch, db, github):
    proposals = await drafted(patch)
    patch.policy.update({"dry_run": False})
    sweep = proposals[("metadata_sweep", None)]
    github.repos["messy"].description = "Written by hand in the meantime."
    await patch.approve(sweep.id)
    await patch.apply(sweep.id)

    assert ("octo/messy", "Changed on GitHub since I drafted this. Skipped.") in texts(db)
    assert [w.path for w in github.writes] == ["/repos/octo/tested/topics"]  # the unchanged one still went through
    assert github.repos["messy"].description == "Written by hand in the meantime."


async def test_a_stale_pull_request_is_dropped_and_drafted_again(patch, db, github):
    proposals = await drafted(patch)
    patch.policy.update({"dry_run": False})
    pull = proposals[("pull_request", "octo/messy")]
    github.repos["messy"].files["notes.txt"] = "pushed after the draft"
    github.repos["messy"].pushed_at = "2026-09-28T00:00:00Z"
    await patch.approve(pull.id)

    assert await patch.apply(pull.id) == "stale"
    assert github.pulls == [] and all(w.method == "GET" for w in github.writes)  # nothing written
    assert patch.proposals.get(pull.id).status == "superseded"
    fresh = next(p for p in patch.proposals.list("pending") if p.repo == "octo/messy")
    assert fresh.id != pull.id and fresh.payload["tree_sha"] != pull.payload["tree_sha"]


# -- a token without the permission -----------------------------------------------------------


async def test_missing_permission_is_reported_with_what_github_wants(patch, db, github):
    proposals = await drafted(patch)
    patch.policy.update({"dry_run": False})
    github.permissions = {"contents", "pull_requests"}  # no Administration: descriptions and topics are refused
    sweep = proposals[("metadata_sweep", None)]
    await patch.approve(sweep.id)

    assert await patch.apply(sweep.id) == "failed"
    after = patch.proposals.get(sweep.id)
    assert after.status == "failed"
    first = after.result["actions"][0]
    assert first == {
        "repo": "octo/messy", "action": "description", "ok": False, "dry_run": False,
        "text": "The token can't do that. GitHub wants: administration=write.",
        "value": "A small Flask app that prints a number", "needed": "administration=write",
    }  # fmt: skip  (the value is kept so the site can offer it for pasting by hand)
    assert last_run(db, "run.finished")[0].payload == {"ok": False, "text": "Nothing was applied."}


async def test_no_token_means_nothing_can_be_applied_for_real(db, bus, github, tmp_path):
    patch = make_patch(db, bus, github, tmp_path, token=None)
    proposals = await drafted(patch)
    patch.policy.update({"dry_run": False})
    sweep = proposals[("metadata_sweep", None)]
    await patch.approve(sweep.id)

    assert await patch.apply(sweep.id) == "failed"
    assert last_run(db, "run.finished")[0].payload["text"] == "No GitHub token, so I can't change anything. Add one in Setup."
    assert patch.proposals.get(sweep.id).status == "approved" and github.writes == []


# -- your edits -------------------------------------------------------------------------------


async def test_edits_are_cleaned_recorded_and_applied(patch, db, github):
    proposals = await drafted(patch)
    patch.policy.update({"dry_run": False})
    sweep = proposals[("metadata_sweep", None)]
    edits = {"items": [
        {"repo": "octo/messy", "description": "  Prints a number.\nNothing more.  ", "topics": ["Flask", "Demo App"]},
        {"repo": "octo/tested", "enabled": False},
    ]}  # fmt: skip
    approved = await patch.approve(sweep.id, edits)

    assert approved.decision["edits"] == [
        {"repo": "octo/messy", "field": "description", "from": "A small Flask app that prints a number", "to": "Prints a number. Nothing more."},
        {"repo": "octo/messy", "field": "topics", "from": ["python", "flask", "web-app"], "to": ["flask", "demo-app"]},
        {"repo": "octo/tested", "field": "enabled", "from": True, "to": False},
    ]
    await patch.apply(sweep.id)
    assert [(w.path, w.body) for w in github.writes] == [
        ("/repos/octo/messy", {"description": "Prints a number. Nothing more."}),
        ("/repos/octo/messy/topics", {"names": ["flask", "demo-app"]}),
    ]


async def test_file_edits_and_switches(patch, github):
    proposals = await drafted(patch)
    patch.policy.update({"dry_run": False})
    pull = proposals[("pull_request", "octo/messy")]
    edits = {"files": [{"path": ".gitignore", "enabled": False}, {"path": "README.md", "content": "# messy\n\nMine now.\n"}]}
    await patch.approve(pull.id, edits)
    await patch.apply(pull.id)

    opened = github.pulls[0]
    assert set(opened["files"]) == {"LICENSE", "README.md"} and opened["files"]["README.md"] == "# messy\n\nMine now.\n"
    assert opened["title"] == "Add a license, and improve the README"
    assert ".gitignore" not in opened["body"]


@pytest.mark.parametrize(
    ("edits", "message"),
    [
        ({"items": [{"repo": "octo/stranger", "enabled": False}]}, "Not part of this proposal: octo/stranger."),
        ({"items": [{"repo": "octo/messy", "enabled": False}, {"repo": "octo/tested", "enabled": False}]},
         "Everything in this proposal is switched off. Reject it instead."),
        ({"items": [{"repo": "octo/messy", "description": "x" * 351}]},
         "The description for octo/messy is longer than GitHub's 350 characters."),
    ],
)  # fmt: skip
async def test_edits_that_cannot_be_accepted(patch, edits, message):
    proposals = await drafted(patch)
    sweep = proposals[("metadata_sweep", None)]
    with pytest.raises(DecisionError) as caught:
        await patch.approve(sweep.id, edits)
    assert str(caught.value) == message
    assert patch.proposals.get(sweep.id).status == "pending"  # still waiting: nothing was decided


# -- rejecting --------------------------------------------------------------------------------


async def test_rejected_proposals_are_remembered_and_not_drafted_again(patch, db):
    proposals = await drafted(patch)
    sweep = proposals[("metadata_sweep", None)]
    rejected = await patch.reject(sweep.id, "  Too generic.  ")

    assert rejected.status == "rejected" and rejected.decision["reason"] == "Too generic."
    event = [e for e in db.events_after(0, 5000) if e.type == "proposal.resolved"][-1]
    assert event.payload["decision"] == "rejected"
    assert event.payload["text"] == "Rejected. I won't draft that again unless the repository changes."

    await patch.draft()
    assert all(p.kind != "metadata_sweep" for p in patch.proposals.list("pending"))

    with pytest.raises(DecisionError):
        await patch.approve(sweep.id)  # already decided
    with pytest.raises(LookupError):
        await patch.reject(9999)
    assert await patch.apply(sweep.id) == "failed"  # a rejected proposal can't be applied


# -- policy -----------------------------------------------------------------------------------


async def test_a_kind_set_to_never_is_not_applied(patch, db, github):
    proposals = await drafted(patch)
    patch.policy.update({"dry_run": False, "kinds": {"metadata_sweep": "never"}})
    sweep = proposals[("metadata_sweep", None)]
    await patch.approve(sweep.id)
    assert await patch.apply(sweep.id) == "failed" and github.writes == []
    assert last_run(db, "run.finished")[0].payload["text"] == "This kind of change is switched off in my settings."


async def test_a_kind_set_to_auto_is_approved_without_asking_but_still_respects_dry_run(patch, db, github):
    patch.policy.update({"kinds": {"metadata_sweep": "auto"}})
    await patch.audit()
    await patch.claude.test()
    await patch.draft()

    sweep = next(p for p in patch.proposals.list() if p.kind == "metadata_sweep")
    assert sweep.status == "approved" and sweep.result["dry_run"] is True and github.writes == []
    assert all(p.status == "pending" for p in patch.proposals.list() if p.kind == "pull_request")  # those still ask


def test_policy_defaults_and_validation(patch):
    assert patch.policy.get() == {
        "dry_run": True, "merge_after_approval": False,
        "kinds": {"metadata_sweep": "ask", "pull_request": "ask"},
    }  # fmt: skip
    for bad in ({"dry_run": "no"}, {"kinds": {"pull_request": "always"}}, {"kinds": {"delete_repo": "auto"}}, {"x": 1}):
        with pytest.raises(ValueError):
            patch.policy.update(bad)
    assert patch.policy.get()["dry_run"] is True


# -- the write allowlist ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("DELETE", "/repos/octo/messy", {}),
        ("PATCH", "/repos/octo/messy", {"description": "ok", "private": True}),
        ("PATCH", "/repos/octo/messy", {"archived": True}),
        ("PATCH", "/repos/octo/messy", {"default_branch": "evil"}),
        ("PATCH", "/repos/octo/messy", {"name": "renamed"}),
        ("POST", "/repos/octo/messy/git/refs", {"ref": "refs/heads/main", "sha": "x"}),
        ("POST", "/repos/octo/messy/pulls", {"title": "t", "head": "main", "base": "main", "body": ""}),
        ("PUT", "/repos/octo/messy/contents/README.md", {"content": "x"}),
        ("POST", "/repos/octo/messy/issues", {"title": "x"}),
        ("PATCH", "/repos/octo/messy/../../user", {"description": "x"}),
        ("PATCH", "/repos/../user", {"description": "x"}),
        ("PUT", "/repos/octo/../topics", {"names": ["a"]}),
        ("POST", "/user/repos", {"name": "new"}),
    ],
)
def test_writes_outside_the_list_are_refused(method, path, body):
    with pytest.raises(WriteRefused):
        check_write(method, path, body)


async def test_a_refused_write_never_reaches_github(db, github):
    client = GitHubClient(db, VALID_TOKEN, transport=github.transport())
    with pytest.raises(WriteRefused):
        await client.write("DELETE", "/repos/octo/messy", {})
    with pytest.raises(WriteRefused):
        await client.write("PATCH", "/repos/octo/messy", {"private": True})
    await client.aclose()
    assert github.calls == [] and github.writes == []
    assert not hasattr(client, "delete")


def test_allowed_writes_pass():
    check_write("PATCH", "/repos/octo/messy", {"description": "x"})
    check_write("PUT", "/repos/octo/messy/topics", {"names": ["a"]})
    check_write("POST", "/repos/octo/messy/git/refs", {"ref": "refs/heads/patch/housekeeping-3", "sha": "x"})
    check_write("POST", "/repos/octo/messy/pulls", {"title": "t", "head": "patch/housekeeping-3", "base": "main", "body": ""})
