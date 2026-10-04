"""An agent's mood follows the portfolio score, and nothing else."""

import pytest

from app.agents.github.store import portfolio_summary
from app.core.mood import MOODS, compute_mood
from tests.fake_github import FakeRepo, healthy_files
from tests.test_audit import agent_for


@pytest.mark.parametrize(
    ("score", "mood"),
    [
        (100, "happy"),
        (81, "happy"),
        (80, "happy"),
        (79, "normal"),
        (60, "normal"),
        (59, "sad"),
        (0, "sad"),
        (None, "normal"),  # nothing audited yet: no reason to smile, none to frown
    ],
)
def test_mood_follows_the_score(score, mood):
    assert compute_mood(score) == mood
    assert mood in MOODS  # every mood has its cause written down for Patch's page


def status_moods(db) -> list[str]:
    return [e.payload["mood"] for e in db.events_after(0, 5000) if e.type == "agent.status"]


async def test_patch_cheers_up_when_the_repositories_get_healthy(db, bus, fake, tmp_path):
    fake.repos.clear()
    fake.repos["rough"] = FakeRepo("rough", description=None, homepage=None, topics=[], license=None, files={"main.py": ""})
    patch = agent_for(db, bus, fake, tmp_path)
    assert patch.mood() == "normal"  # before the first audit

    await patch.audit()
    await patch.settle()
    assert portfolio_summary(patch.store.all())["score"] < 60 and patch.mood() == "sad"

    rough = fake.repos["rough"]
    rough.description, rough.homepage, rough.topics, rough.license = "A project.", "https://example.com", ["a", "b", "c"], "MIT"
    rough.files, rough.ci, rough.pushed_at = healthy_files(), "success", "2026-10-05T00:00:00Z"
    await patch.audit()
    await patch.settle()
    assert portfolio_summary(patch.store.all())["score"] >= 80 and patch.mood() == "happy"

    # The face on the site follows the status Patch reports: sad after the first audit, happy after the second.
    moods = status_moods(db)
    assert "sad" in moods and moods[-1] == "happy"
