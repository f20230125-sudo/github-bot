"""An agent's personality, loaded from its persona.toml.

The personality is a skin over facts: lines are templates filled from real data, so the voice
can change without the numbers changing.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

_EMOJI_RE = re.compile("[\U0001f000-\U0001faff☀-➿\U0001f1e6-\U0001f1ff]")
# A sentence ends at ., ! or ? followed by a space or the end. ".gitignore" and "3.5" don't count.
_SENTENCE_END_RE = re.compile(r"[.!?]+(?=\s|$)")


class Persona:
    def __init__(self, data: dict[str, Any]):
        self.id: str = data["id"]
        self.name: str = data["name"]
        self.role: str = data["role"]
        self.voice: dict[str, Any] = data.get("voice", {})
        self._lines: dict[str, Any] = data.get("lines", {})

    @classmethod
    def load(cls, path: Path) -> Persona:
        with path.open("rb") as handle:
            return cls(tomllib.load(handle))

    def has_line(self, key: str) -> bool:
        return self._find(key) is not None

    def line(self, key: str, **data: Any) -> str:
        """Fill the template stored under a dotted key such as "finding.license"."""
        template = self._find(key)
        if template is None:
            raise KeyError(f"{self.id} has no line for {key!r}")
        return template.format(**data)

    def all_lines(self) -> dict[str, str]:
        """Every template, by dotted key."""
        out: dict[str, str] = {}

        def walk(node: dict[str, Any], prefix: str) -> None:
            for name, value in node.items():
                if isinstance(value, dict):
                    walk(value, f"{prefix}{name}.")
                elif isinstance(value, str):
                    out[f"{prefix}{name}"] = value

        walk(self._lines, "")
        return out

    def lint(self, text: str) -> list[str]:
        """Ways a line breaks this persona's voice rules. Empty means it passes."""
        problems: list[str] = []
        max_chars = int(self.voice.get("max_chars", 240))
        max_sentences = int(self.voice.get("max_sentences", 3))
        if len(text) > max_chars:
            problems.append(f"longer than {max_chars} characters")
        if "!" in text:
            problems.append("exclamation mark")
        if _EMOJI_RE.search(text):
            problems.append("emoji")
        lowered = text.lower()
        for word in self.voice.get("banned", []):
            if re.search(rf"\b{re.escape(word.lower())}\b", lowered):
                problems.append(f"banned word: {word}")
        if len(_SENTENCE_END_RE.findall(text)) > max_sentences:
            problems.append(f"more than {max_sentences} sentences")
        return problems

    def _find(self, key: str) -> str | None:
        node: Any = self._lines
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node if isinstance(node, str) else None
