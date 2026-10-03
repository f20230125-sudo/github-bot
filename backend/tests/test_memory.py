"""Lessons: what Patch keeps from your decisions, and how they reach later drafts."""

import pytest

from app.agents.github.agent import PatchAgent
from app.agents.github.learning import feedback_of
from app.config import Settings
from app.core.memory import MAX_ACTIVE, LessonStore, clean_lesson
from app.main import build_claude
from tests.claude_helpers import USAGE_TEXT, ZERO_USAGE, FakeClaude, flag
from tests.fake_github import GOOD_README, FakeGitHub, FakeRepo, healthy_files
from tests.test_drafting import README, SWEEP

LESSON = "Keep descriptions under 100 characters."
ASKED = "Turn the feedback below"


def scenario(lesson: str | None = LESSON) -> dict:
    return {
        "routes": [
            {"match": "/usage", "response": {"text": USAGE_TEXT, "usage": ZERO_USAGE}},
            {"match": "Write GitHub metadata", "response": {"structured": SWEEP}},
            {"match": "Improve the README", "response": {"structured": README}},
            {"match": ASKED, "response": {"structured": {"lesson": lesson}}},
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
            FakeRepo("tested", description="A tested thing.", topics=["python"], license=None, files={"README.md": GOOD_README}),
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


async def drafted(patch, with_claude=True) -> dict:
    """Audit and draft. Returns the pending proposals by kind."""
    await patch.audit()
    await patch.claude.test()
    await patch.draft()
    proposals = {p.kind: p for p in patch.proposals.list("pending")}
    if not with_claude:
        patch.db.set_kv("usage_guard", "{}")  # forget the usage reading: Claude is off from here on
    return proposals


def lesson_calls(claude_fake) -> list[dict]:
    return [c for c in claude_fake.calls() if ASKED in c["stdin"]]


def last_run(db) -> list:
    run_id = [e.run_id for e in db.events_after(0, 5000) if e.type == "run.started"][-1]
    return db.events_for_run(run_id)


def finished(db) -> str:
    return [e for e in db.events_after(0, 5000) if e.type == "run.finished"][-1].payload["text"]


# -- the store --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('  - "No emoji in READMEs."  ', "No emoji in READMEs."),
        ("Two\nlines   become one.", "Two lines become one."),
        ("short", None),
        ("x" * 161, None),
        ("Ship it \U0001f680 every time", None),
        (None, None),
    ],
)
def test_clean_lesson(text, expected):
    assert clean_lesson(text) == expected


def test_a_lesson_is_stored_once(db):
    store = LessonStore(db)
    first = store.add("patch", "No emoji in READMEs.", "you")
    assert first is not None and (first.source, first.active, first.proposal_id) == ("you", True, None)
    assert store.add("patch", "no emoji in readmes", "rejection") is None  # the same lesson, differently typed
    assert store.add("patch", "tiny", "you") is None
    assert [lesson.text for lesson in store.list("patch")] == ["No emoji in READMEs."]


def test_lessons_can_be_reworded_switched_off_and_deleted(db):
    store = LessonStore(db)
    one = store.add("patch", "No emoji in READMEs.", "you")
    two = store.add("patch", "Keep topics to five.", "you")

    assert store.update(one.id, text="Never use emoji anywhere.").text == "Never use emoji anywhere."
    assert store.update(one.id, text="Keep topics to five") is None  # that is the other lesson
    assert store.update(two.id, active=False).active is False
    assert store.active_texts("patch") == ["Never use emoji anywhere."]
    assert store.update(9999, active=False) is None

    assert store.delete(two.id) is True and store.delete(two.id) is False
    assert [lesson.id for lesson in store.list("patch")] == [one.id]


def test_only_the_newest_lessons_join_the_prompt_in_a_stable_order(db):
    store = LessonStore(db)
    for i in range(MAX_ACTIVE + 3):
        store.add("patch", f"Lesson number {i:02} about writing.", "you")
    texts = store.active_texts("patch")
    assert len(texts) == MAX_ACTIVE
    assert texts[0] == "Lesson number 03 about writing." and texts[-1] == "Lesson number 14 about writing."


# -- what counts as feedback ------------------------------------------------------------------


async def test_feedback_comes_from_a_reason_or_a_real_edit(patch):
    proposals = await drafted(patch)
    sweep, pull = proposals["metadata_sweep"], proposals["pull_request"]
    assert feedback_of(sweep) is None and feedback_of(None) is None  # nothing decided yet

    edited = await patch.approve(
        sweep.id, {"items": [{"repo": "octo/messy", "description": "A Flask app.", "topics": ["flask"]}]}
    )
    action, text = feedback_of(edited)
    assert action == "edited before approving"
    assert 'messy description: drafted "A small Flask app that prints a number", you wrote "A Flask app."' in text
    assert "messy topics: drafted ['python', 'flask', 'web-app'], you set ['flask']" in text

    rejected = await patch.reject(pull.id, "  Too long.\n Keep READMEs short.  ")
    assert feedback_of(rejected) == ("rejected", "reason: Too long. Keep READMEs short.")


async def test_a_bare_rejection_or_a_switched_off_item_teaches_nothing(patch, db, claude_fake):
    proposals = await drafted(patch)
    rejected = await patch.reject(proposals["metadata_sweep"].id)
    files = [{"path": "LICENSE", "enabled": False}]
    approved = await patch.approve(proposals["pull_request"].id, {"files": files})
    assert feedback_of(rejected) is None and feedback_of(approved) is None

    events_before = db.last_id()
    await patch.learn(rejected.id)
    await patch.learn(approved.id)
    assert db.last_id() == events_before and lesson_calls(claude_fake) == []  # no run, no status, no call


async def test_an_edited_file_is_described_as_a_diff(patch):
    proposals = await drafted(patch)
    pull = proposals["pull_request"]
    readme = next(f for f in pull.payload["files"] if f["path"] == "README.md")
    shorter = readme["content"].replace("## Tech stack", "## Stack")
    approved = await patch.approve(pull.id, {"files": [{"path": "README.md", "content": shorter}]})
    _, text = feedback_of(approved)
    assert text.startswith("README.md: the file was changed like this (- drafted, + your version):")
    assert "-## Tech stack" in text and "+## Stack" in text


# -- learning ---------------------------------------------------------------------------------


async def test_a_rejection_with_a_reason_becomes_a_lesson_in_one_small_call(patch, db, claude_fake):
    proposals = await drafted(patch)
    patch.lessons.add("patch", "No emoji in READMEs.", "you")
    await patch.reject(proposals["metadata_sweep"].id, "These descriptions are far too long.")
    await patch.learn(proposals["metadata_sweep"].id)

    [call] = lesson_calls(claude_fake)
    assert flag(call, "--model") == "haiku"
    assert "Octo Cat rejected something you drafted: Metadata sweep." in call["stdin"]
    assert "<feedback>\nreason: These descriptions are far too long.\n</feedback>" in call["stdin"]
    assert "Rules you already have:\n- No emoji in READMEs." in call["stdin"]

    newest = patch.lessons.list("patch")[0]
    assert (newest.text, newest.source, newest.proposal_id) == (LESSON, "rejection", proposals["metadata_sweep"].id)

    events = last_run(db)
    assert events[0].payload == {"job": "learn", "title": "Learn from your decision"}
    steps = [(e.payload["step"], e.payload["text"]) for e in events if e.type == "run.step"]
    assert steps == [("feedback", "You rejected it and said why."), ("lesson", f"Noted: {LESSON}")]
    assert finished(db) == "Learned. 2 lessons so far."
    statuses = [e.payload["status"] for e in db.events_after(0, 5000) if e.type == "agent.status"]
    assert statuses[-2:] == ["working", "waiting"]  # the pull request is still waiting for you


async def test_an_edit_becomes_a_lesson_too(patch, db, claude_fake):
    proposals = await drafted(patch)
    sweep = proposals["metadata_sweep"]
    await patch.approve(sweep.id, {"items": [{"repo": "octo/messy", "description": "A Flask app."}]})
    await patch.learn(sweep.id)

    [call] = lesson_calls(claude_fake)
    assert "Octo Cat edited before approving something you drafted" in call["stdin"]
    assert patch.lessons.list("patch")[0].source == "edit"


async def test_claude_may_find_nothing_worth_keeping(patch, db, claude_fake):
    proposals = await drafted(patch)
    claude_fake.set(scenario(lesson=None))
    await patch.reject(proposals["metadata_sweep"].id, "Wrong repository.")
    await patch.learn(proposals["metadata_sweep"].id)
    assert patch.lessons.list("patch") == [] and finished(db) == "Nothing in that applies beyond this one case."


async def test_a_lesson_it_already_has_is_not_stored_twice(patch, db):
    proposals = await drafted(patch)
    patch.lessons.add("patch", LESSON.lower(), "you")
    await patch.reject(proposals["metadata_sweep"].id, "Too long.")
    await patch.learn(proposals["metadata_sweep"].id)
    assert len(patch.lessons.list("patch")) == 1 and finished(db) == "I already had that one."


async def test_without_claude_your_reason_is_kept_as_you_wrote_it(patch, db, claude_fake):
    proposals = await drafted(patch, with_claude=False)
    await patch.reject(proposals["metadata_sweep"].id, "Descriptions must name the main framework.")
    await patch.learn(proposals["metadata_sweep"].id)

    assert lesson_calls(claude_fake) == []
    [lesson] = patch.lessons.list("patch")
    assert (lesson.text, lesson.source) == ("Descriptions must name the main framework.", "rejection")
    steps = [e.payload["text"] for e in last_run(db) if e.type == "run.step"]
    assert steps[1].startswith("Not calling Claude.") and steps[2].startswith("Noted: Descriptions must name")


async def test_without_claude_an_edit_or_a_long_reason_makes_no_rule(patch, db, claude_fake):
    proposals = await drafted(patch, with_claude=False)
    sweep, pull = proposals["metadata_sweep"], proposals["pull_request"]

    await patch.approve(sweep.id, {"items": [{"repo": "octo/messy", "description": "A Flask app."}]})
    await patch.learn(sweep.id)
    assert finished(db) == "Your edit is saved with the decision. No rule made from it."

    await patch.reject(pull.id, "I have a great many thoughts about this. " * 6)
    await patch.learn(pull.id)
    assert finished(db) == "Too long to keep as a rule. Add a shorter one on my page."
    assert patch.lessons.list("patch") == [] and lesson_calls(claude_fake) == []


async def test_lessons_reach_the_next_draft_through_the_system_prompt(patch, claude_fake):
    await patch.audit()
    await patch.claude.test()
    patch.lessons.add("patch", "No emoji in READMEs.", "you")
    off = patch.lessons.add("patch", "Always mention the license.", "you")
    patch.lessons.update(off.id, active=False)
    await patch.draft()

    sweep = next(c for c in claude_fake.calls() if "Write GitHub metadata" in c["stdin"])
    system = flag(sweep, "--system-prompt")
    assert system.endswith("What Octo Cat has told you before. Follow these:\n- No emoji in READMEs.")
    assert "Always mention the license." not in system  # switched off
