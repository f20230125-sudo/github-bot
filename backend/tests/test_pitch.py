"""Pitch reads the notes Patch leaves and says which have enough for a post. Rules only."""

import json
import string

import pytest

from app.agents.github.facts import readme_image, readme_intro
from app.agents.github.models import RepoDetails, RepoMeta, RepoSnapshot
from app.agents.linkedin.agent import PERSONA_PATH, PitchAgent
from app.agents.linkedin.brief import build_brief
from app.cloud import publish, run_check
from app.core.persona import Persona
from tests.conftest import WRITE_HEADERS
from tests.fake_github import FakeRepo, healthy_files
from tests.test_audit import agent_for

# -- from a note to a brief: pure rules -------------------------------------------------------


def facts(**changes) -> dict:
    """What Patch says about a presentable repository."""
    known = {
        "full_name": "octo/app", "name": "app", "kind": "project", "url": "https://github.com/octo/app",
        "description": "A small service that answers questions about documents.", "intro": None, "homepage": None,
        "language": "Python", "topics": ["fastapi", "rag"], "license": "MIT", "stars": 0, "release": None,
        "image": None, "score": 88, "score_before": 62, "presentable": True, "presentable_from": 80,
    }  # fmt: skip
    return known | changes


def said(brief) -> dict[str, str]:
    return {fact.label: fact.value for fact in brief.facts}


def test_a_presentable_repository_that_says_what_it_is_has_enough_for_a_post():
    brief = build_brief("ready", {"name": "app", "before": 62, "score": 88}, facts())

    assert brief.ready and brief.angle == "launch" and brief.missing == []
    assert said(brief) == {
        "What it is": "A small service that answers questions about documents.",
        "Score": "88 out of 100, up from 62",
        "Built with": "Python · fastapi, rag",
        "License": "MIT",
        "Repository": "https://github.com/octo/app",
    }
    # Neither of these holds the post back.
    assert brief.wanted == ["image", "link"]


@pytest.mark.parametrize(
    ("changes", "missing"),
    [
        ({"description": None}, ["summary"]),
        ({"score": 62, "presentable": False}, ["presentable"]),
        ({"score": None, "presentable": False}, ["unscored"]),
        ({"description": None, "score": 40, "presentable": False}, ["summary", "presentable"]),
    ],
    ids=["no description", "low score", "no score", "both"],
)
def test_what_holds_a_post_back(changes, missing):
    brief = build_brief("new_repo", {}, facts(**changes))
    assert not brief.ready and brief.missing == missing


def test_a_repository_patch_no_longer_knows_has_nothing_to_post():
    brief = build_brief("ready", {}, None)
    assert not brief.ready and brief.missing == ["unknown"] and brief.facts == []


def test_the_readme_can_say_what_it_is_when_the_description_does_not():
    brief = build_brief("ready", {}, facts(description=None, intro="It reads your documents and answers from them."))
    assert brief.ready and said(brief)["What it is"] == "It reads your documents and answers from them."


def test_a_picture_and_a_live_link_become_facts_with_addresses():
    image = {"path": "docs/shot.png", "url": "https://raw.githubusercontent.com/octo/app/main/docs/shot.png"}
    brief = build_brief("demo_link", {"url": "https://app.example"}, facts(image=image, homepage="https://app.example"))

    assert brief.wanted == [] and brief.angle == "demo"
    by_label = {fact.label: fact for fact in brief.facts}
    assert (by_label["Picture"].value, by_label["Picture"].url) == ("docs/shot.png", image["url"])
    assert by_label["Live link"].url == "https://app.example"


def test_the_note_says_which_release_and_which_milestone():
    release = build_brief("release", {"name": "app", "tag": "v1.2.0"}, facts(release="v1.2.0"))
    stars = build_brief("stars", {"name": "app", "stars": 25}, facts(stars=27))

    assert release.angle == "release" and said(release)["Release"] == "v1.2.0"
    assert stars.angle == "milestone" and said(stars)["Stars"] == "25"  # the milestone, not today's count
    # A handful of stars is nothing to state. A real count is.
    assert "Stars" not in said(build_brief("ready", {}, facts(stars=0)))
    assert "Stars" not in said(build_brief("ready", {}, facts(stars=4)))
    assert said(build_brief("ready", {}, facts(stars=12)))["Stars"] == "12"
    # A score that went down, or has no history, is stated without the comparison.
    assert said(build_brief("ready", {}, facts(score_before=None)))["Score"] == "88 out of 100"
    assert said(build_brief("ready", {}, facts(score_before=95)))["Score"] == "88 out of 100"


# -- what Patch can say about a README --------------------------------------------------------


def snapshot(readme: str, path: str = "README.md", branch: str = "main") -> RepoSnapshot:
    meta = RepoMeta(full_name="octo/app", name="app", owner="octo", default_branch=branch)
    return RepoSnapshot(meta=meta, details=RepoDetails(readme_path=path, readme_text=readme))


def test_the_first_picture_in_the_readme_is_found_and_badges_are_not():
    readme = (
        "# App\n\n[![CI](https://github.com/octo/app/actions/workflows/ci.yml/badge.svg)](x)\n"
        "![build](https://img.shields.io/badge/build-passing-green)\n\n"
        "```\n![not this](docs/in-code.png)\n```\n\n![The floor](docs/screenshots/the%20floor.png)\n"
    )
    assert readme_image(snapshot(readme)) == {
        "path": "docs/screenshots/the floor.png",
        "url": "https://raw.githubusercontent.com/octo/app/main/docs/screenshots/the%20floor.png",
    }
    assert readme_image(snapshot("# App\n\nNo pictures here.")) is None


def test_a_picture_is_found_from_where_the_readme_is():
    nested = readme_image(snapshot('<img src="../art/shot.jpg" width="400">', path="docs/README.md", branch="dev"))
    assert nested == {"path": "art/shot.jpg", "url": "https://raw.githubusercontent.com/octo/app/dev/art/shot.jpg"}

    elsewhere = readme_image(snapshot("![demo](https://example.com/demo.gif?raw=1)"))
    assert elsewhere == {"path": "https://example.com/demo.gif?raw=1", "url": "https://example.com/demo.gif?raw=1"}


def test_the_intro_is_the_first_paragraph_that_is_a_sentence():
    readme = "# App\n\n![shot](a.png)\n\n```bash\npip install app\n```\n\nIt answers questions\nabout documents.\n\nMore."
    assert readme_intro(readme) == "It answers questions about documents."
    assert readme_intro("# Only a title") is None and readme_intro(None) is None

    # Plain text: a post can't use Markdown.
    marked = "It reads **your** documents with `rag`, a _small_ index, and [answers](https://x.example) from them."
    assert readme_intro(marked) == "It reads your documents with rag, a small index, and answers from them."
    assert readme_intro("Uses snake_case names and 2*3 maths.") == "Uses snake_case names and 2*3 maths."

    # Too long: cut at the end of a sentence where there is one, else at a word.
    sentences = readme_intro("It does one thing well. " * 30)
    assert sentences.endswith("well.") and len(sentences) <= 280
    words = readme_intro("word " * 200)
    assert words.endswith(" ...") and len(words) <= 284


# -- Pitch at work ----------------------------------------------------------------------------


@pytest.fixture
def patch(db, bus, fake, tmp_path):
    return agent_for(db, bus, fake, tmp_path)


@pytest.fixture
def pitch(db, bus, patch):
    return PitchAgent(patch.settings, db, bus, patch.claude, source=patch)


def tidy(repo: FakeRepo) -> None:
    """You fix the repository on GitHub: it now explains itself and scores well."""
    repo.description, repo.topics, repo.license = "A project.", ["a", "b", "c"], "MIT"
    repo.files, repo.ci, repo.pushed_at = healthy_files(), "success", "2026-10-05T00:00:00Z"


def by_pitch(db, type_=None) -> list:
    return [e for e in db.events_after(0, 5000) if e.agent == "pitch" and (type_ is None or e.type == type_)]


async def test_with_no_note_pitch_does_nothing_at_all(patch, pitch, db):
    await patch.audit()  # a first audit is the baseline: nothing in it is news
    before = db.last_id()

    await pitch.read()
    assert db.last_id() == before and pitch.notes() == []
    assert pitch.card()["status"] is None


async def test_pitch_answers_a_note_once_without_a_request_or_a_model_call(patch, pitch, db, fake):
    await patch.audit()
    tidy(fake.repos["messy"])
    await patch.audit()  # messy crosses 80: Patch leaves a note
    fake.reset()

    await pitch.read()

    assert fake.calls == []  # Pitch asked Patch, not GitHub
    assert by_pitch(db, "tool.result") == []  # and no model either
    [answer] = by_pitch(db, "message")
    assert answer.repo == "octo/messy" and answer.run_id.startswith("read-")
    assert answer.payload == {
        "kind": "answer", "from": "pitch", "to": "patch", "thread": "ready:octo/messy", "topic": "ready",
        "note": answer.payload["note"], "ready": True, "text": "Enough for a post.",
    }  # fmt: skip
    assert [e.payload for e in by_pitch(db, "usage")] == [{"notes_read": 1}]
    assert by_pitch(db, "run.finished")[0].payload == {"ok": True, "text": "Read 1 note. Enough for a post: 1."}
    assert [(e.payload["status"], e.payload["text"]) for e in by_pitch(db, "agent.status")] == [
        ("working", "Reading Patch's notes."),
        ("idle", "1 idea worth a post."),
    ]

    before = db.last_id()
    await pitch.read()  # the note is answered: nothing more to say
    assert db.last_id() == before


async def test_a_note_that_has_to_wait_says_why_and_is_answered_again_when_it_is_ready(patch, pitch, db, fake):
    await patch.audit()
    fake.repos["rough"] = FakeRepo("rough", description=None, homepage=None, topics=[], license=None, files={"main.py": ""})
    await patch.audit()  # a new repository, and not a presentable one
    await pitch.read()

    score = patch.store.get("octo/rough").score
    [first] = by_pitch(db, "message")
    assert first.payload["ready"] is False and first.payload["thread"] == "new_repo:octo/rough"
    assert first.repo == "octo/rough"  # the site shows the name beside the line
    assert first.payload["text"] == "Not yet. Nothing says what it is. It needs a description."
    assert by_pitch(db, "agent.status")[-1].payload["text"] == "1 note on hold. Not enough for a post yet."
    [note] = pitch.notes()
    assert note["brief"]["plain_post"] is None  # nothing to post about yet
    assert note["brief"]["verdict"] == "Not yet." and note["brief"]["missing"] == [
        "Nothing says what it is. It needs a description.",
        f"It scores {score}. I would wait for 80.",
    ]

    tidy(fake.repos["rough"])
    await patch.audit()  # it crosses 80: a second note, and the first one is no longer held back
    await pitch.read()

    answers = [(e.payload["thread"], e.payload["text"]) for e in by_pitch(db, "message")[1:]]
    assert answers == [
        ("new_repo:octo/rough", "Enough for a post now."),
        ("ready:octo/rough", "Enough for a post."),
    ]
    assert by_pitch(db, "agent.status")[-1].payload["text"] == "2 ideas worth a post."
    assert json.loads(db.get_kv("pitch.notes")) == {"new_repo:octo/rough": True, "ready:octo/rough": True}


async def test_notes_come_newest_first_with_what_a_post_may_state(patch, pitch, fake):
    fake.repos["messy"].homepage = "https://messy.example"  # there from the start, so not news by itself
    await patch.audit()
    tidy(fake.repos["messy"])
    fake.repos["messy"].files["README.md"] += "\n![shot](docs/shot.png)\n"
    await patch.audit()

    [note] = pitch.notes()
    assert (note["repo"], note["topic"], note["from"], note["to"]) == ("octo/messy", "ready", "patch", "pitch")
    assert note["text"].startswith("messy went from ") and note["data"]["name"] == "messy"

    brief = note["brief"]
    assert (brief["ready"], brief["angle"], brief["angle_label"], brief["verdict"]) == (
        True, "launch", "Launch post", "Enough for a post.",
    )  # fmt: skip
    assert brief["missing"] == [] and brief["wanted"] == []  # it has a picture and a live link
    # The post rules alone can write: what the view-only copy offers, with no model to ask.
    assert brief["plain_post"] == (
        "I built messy.\n\nA project.\n\nBuilt with Python. Open source under the MIT license.\n\n"
        "Try it: https://messy.example\nCode: https://github.com/octo/messy"
    )
    labels = [fact["label"] for fact in brief["facts"]]
    assert labels == ["What it is", "Score", "Built with", "License", "Live link", "Repository", "Picture"]
    picture = brief["facts"][-1]
    assert picture["url"] == "https://raw.githubusercontent.com/octo/messy/main/docs/shot.png"


async def test_pitchs_mood_follows_the_same_health_as_patchs(patch, pitch):
    assert pitch.mood() == "normal"  # nothing audited yet
    await patch.audit()
    assert pitch.mood() == patch.mood() and pitch.card()["mood"] == patch.mood()


def test_every_line_pitch_can_say_passes_its_own_voice_rules():
    pitch = Persona.load(PERSONA_PATH)
    sample = {
        "ideas_text": "2 ideas", "notes_text": "3 notes", "ready": 2, "score": 62, "needed": 80,
        "name": "document-qa-agent", "drafts_text": "2 drafts", "versions_text": "3 versions",
        "hooks_text": "2 other opening lines", "claude_text": "1 model call", "lessons_text": "3 lessons",
        "reason": "Claude Code gave no usage percentage this time, so Patch won't call it.",
        "lesson": "Keep posts under 800 characters.",
    }  # fmt: skip
    for key, template in pitch.all_lines().items():
        fields = {name for _, name, _, _ in string.Formatter().parse(template) if name}
        assert fields <= set(sample), f"{key} uses a placeholder the test doesn't know: {fields - set(sample)}"
        assert pitch.lint(template.format(**sample)) == [], key


# -- on the desk ------------------------------------------------------------------------------


async def audit(client, app) -> None:
    assert (await client.post("/api/jobs/audit", headers=WRITE_HEADERS)).status_code == 202
    await app.state.desk.join()


async def test_pitch_reads_as_soon_as_patch_has_finished(client, app, fake, db):
    await audit(client, app)
    assert by_pitch(db) == []  # no note, so nothing from Pitch

    tidy(fake.repos["messy"])
    await audit(client, app)  # waiting for the desk waits for Pitch's turn as well

    runs = [e.run_id.split("-")[0] for e in db.events_after(0, 5000) if e.type == "run.started"]
    assert runs == ["audit", "audit", "read"]
    answer = by_pitch(db, "message")[0]
    assert (answer.repo, answer.payload["text"]) == ("octo/messy", "Enough for a post.")

    patch, pitch = (await client.get("/api/agents")).json()["agents"]
    assert pitch["hired"] is True and pitch["status"] == {"status": "idle", "text": "1 idea worth a post.", "mood": pitch["mood"]}
    [note] = (await client.get("/api/handoffs")).json()["handoffs"]
    assert note["repo"] == "octo/messy" and note["brief"]["ready"] is True


async def test_pitchs_page_says_what_it_does_and_what_it_never_will(client):
    sheet = (await client.get("/api/agents/pitch")).json()

    assert (sheet["id"], sheet["name"], sheet["role"], sheet["hired"], sheet["paused"]) == (
        "pitch", "Pitch", "LinkedIn writer", True, False,
    )  # fmt: skip
    assert sheet["persona"]["summary"] == "An editor who cuts. Warm, brief, allergic to filler."
    assert "thrilled" in sheet["persona"]["banned"] and list(sheet["moods"]) == ["happy", "normal", "sad"]
    assert sheet["does"][0] == "Read the notes Patch leaves about your repositories"
    assert sheet["never"] == [
        "Sign in to LinkedIn",
        "Post, comment, react or send a message for you",
        "Read your feed, your profile or anyone else's",
        "Hold a LinkedIn password or token",
    ]


async def test_pitch_says_so_when_the_desk_is_paused(client, db):
    await client.put("/api/pause", json={"paused": True}, headers=WRITE_HEADERS)
    assert by_pitch(db, "agent.status")[-1].payload == {
        "status": "paused", "text": "Paused. I write nothing until you resume.", "mood": "normal",
    }  # fmt: skip

    await client.put("/api/pause", json={"paused": False}, headers=WRITE_HEADERS)
    assert by_pitch(db, "agent.status")[-1].payload["text"] == "Nothing new worth a post."


# -- in the scheduled check -------------------------------------------------------------------


async def test_the_scheduled_check_lets_pitch_read_and_publishes_its_verdict(app, fake, tmp_path, db):
    out = tmp_path / "public" / "snapshot.json"
    await run_check(app)
    assert await publish(app, out) is True and by_pitch(db) == []

    tidy(fake.repos["messy"])
    assert await run_check(app) == {"changed": True, "error": None}
    assert by_pitch(db, "message")[0].payload["ready"] is True
    assert await publish(app, out) is True

    routes = json.loads(out.read_text(encoding="utf-8"))["routes"]
    assert routes["/api/handoffs"]["handoffs"][0]["brief"]["verdict"] == "Enough for a post."
    assert routes["/api/agents/pitch"]["hired"] is True and routes["/api/agents"]["agents"][1]["id"] == "pitch"

    assert await run_check(app) == {"changed": False, "error": None}  # nothing new: Pitch stays quiet
    assert await publish(app, out) is False and len(by_pitch(db, "run.started")) == 1
