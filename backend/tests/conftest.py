import httpx
import pytest

from app.bus import EventBus
from app.config import Settings
from app.db import Database
from app.main import create_app
from tests.fake_github import FakeGitHub, FakeRepo, healthy_files

ORIGIN = "http://localhost:3010"
HOST = "127.0.0.1:8010"
WRITE_HEADERS = {"X-Desk-Request": "1", "Origin": ORIGIN}


@pytest.fixture
def db():
    database = Database(":memory:")
    yield database
    database.close()


@pytest.fixture
def bus(db):
    return EventBus(db)


@pytest.fixture
def fake():
    """Three repositories: one healthy, one neglected, one placeholder."""
    return FakeGitHub(
        [
            FakeRepo("healthy", files=healthy_files(), ci="success"),
            FakeRepo(
                "messy",
                description=None,
                homepage=None,
                topics=[],
                license=None,
                files={"README.md": "# messy\n\nTodo.", "app.py": "print(1)"},
            ),
            FakeRepo("test", description=None, homepage=None, topics=[], license=None, language=None, files={"a.txt": "x"}),
        ]
    )


@pytest.fixture
def settings(tmp_path):
    # _env_file=None: tests must never read the real backend/.env.
    # claude_command=[]: tests must never run the real Claude Code CLI. Tests that need Claude
    # point this at the stand-in in tests/fake_claude.py.
    return Settings(
        _env_file=None,
        db_path=":memory:",
        github_token=None,
        github_user="octo",
        frontend_origin=ORIGIN,
        port=8010,
        env_path=tmp_path / ".env",
        claude_command=[],
        claude_cwd=tmp_path / "claude-cwd",
    )


@pytest.fixture
def app(db, fake, settings):
    return create_app(settings, db=db, github_transport=fake.transport())


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=f"http://{HOST}") as c:
        yield c
