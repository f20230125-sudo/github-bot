from __future__ import annotations

import re
from pathlib import Path


def update_env_file(path: Path, values: dict[str, str | None]) -> None:
    """Set keys in a .env file, keeping every other line. A value of None removes the key."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for key, value in values.items():
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*=")
        lines = [line for line in lines if not pattern.match(line)]
        if value is not None:
            lines.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
