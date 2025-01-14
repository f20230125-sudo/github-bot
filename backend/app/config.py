from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    # Prefixed so the app never picks up unrelated variables from the environment.
    model_config = SettingsConfigDict(
        env_prefix="DESK_", env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    github_token: str | None = None
    github_user: str = "f20230125-sudo"
    github_write_delay: float = 1.0  # seconds between writes, as GitHub asks

    frontend_origin: str = "http://localhost:3010"
    host: str = "127.0.0.1"
    port: int = 8010

    db_path: Path = BACKEND_DIR / "data" / "desk.sqlite3"
    # Where the Setup page saves the GitHub token.
    env_path: Path = BACKEND_DIR / ".env"

    # Watching GitHub. A check is one conditional request. Without a token GitHub rations requests
    # by the hour, so the checks are further apart.
    watch: bool = True
    watch_interval: float = 300.0  # seconds between checks, with a token
    watch_interval_public: float = 1800.0  # and without one

    # Claude usage guard (percent of the Claude plan's 5-hour and weekly limits).
    stop_at_session_pct: float = 40.0
    stop_at_weekly_pct: float = 40.0
    # Only used when Claude Code reports no percentage at all: this many calls per 5 hours. 0 = off.
    claude_fixed_allowance: int = 0

    # Claude runs through the Claude Code CLI signed in on this machine. No API key exists here.
    claude_command: list[str] | None = None  # default: `claude` from PATH; tests point it at a stand-in
    claude_cwd: Path = BACKEND_DIR / "data" / "claude-cwd"
    claude_timeout: float = 180.0
    claude_model_write: str = "sonnet"  # descriptions, READMEs, chat. "opus" works but uses the limit faster
    claude_model_small: str = "haiku"  # lessons, handoffs, the usage check
    claude_effort_light: str = "low"
    claude_effort_write: str = "medium"

    # Template fixes
    license_holder: str | None = None  # default: the name on your GitHub profile
    # Pull requests end with a line saying Patch drafted them and you approved them.
    pr_footer: bool = True

    # Patch proposes; nothing is written to GitHub until approved. Dry-run on at first.
    dry_run: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
