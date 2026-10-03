"""Turn Patch's neutral findings into lines in its own voice."""

from __future__ import annotations

from ...core.persona import Persona
from .models import Finding


def finding_key(finding: Finding) -> str:
    """Identifies a finding across audits, so we can tell new ones from ones already raised."""
    return f"{finding.check}:{finding.data.get('path', '')}"


def line_key(finding: Finding) -> str:
    """Which persona line a finding uses. Topics has a variant chosen by the data."""
    key = finding.check
    if key == "topics":
        key = "topics_none" if finding.data.get("n", 0) == 0 else "topics_few"
    return f"finding.{key}"


def finding_line(persona: Persona, finding: Finding) -> str:
    try:
        return persona.line(line_key(finding), **finding.data)
    except KeyError:
        return f"{finding.title}."  # no line written for this check, or it needs data we don't have


def resolved_line(persona: Persona, finding: Finding) -> str:
    return persona.line("resolved.default", title=finding.title)
