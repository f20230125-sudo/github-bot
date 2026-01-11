from __future__ import annotations


def count(n: int, singular: str, plural: str | None = None) -> str:
    """`count(1, "repository", "repositories")` -> "1 repository"; `count(3, "request")` -> "3 requests"."""
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"
