import string

import pytest

from app.agents.github.agent import PERSONA_PATH
from app.agents.github.models import Finding
from app.agents.github.voice import finding_key, finding_line, line_key, resolved_line
from app.core.persona import Persona
from app.core.text import count

# One finding of every kind the checks can produce, with the data its line needs.
FINDINGS = [
    Finding(check="description", severity="medium", title="No description", detail=""),
    Finding(check="topics", severity="medium", title="No topics", detail="", data={"n": 0}),
    Finding(check="topics", severity="low", title="Fewer than three topics", detail="", data={"n": 2}),
    Finding(check="homepage", severity="low", title="No live demo link", detail=""),
    Finding(check="license", severity="high", title="No license", detail=""),
    Finding(check="readme_missing", severity="high", title="No README", detail=""),
    Finding(check="readme_nested", severity="high", title="README is not in the root", detail="", data={"path": "game/README.md", "dir": "game"}),
    Finding(check="readme_short", severity="medium", title="Very short README", detail="", data={"n": 40}),
    Finding(check="readme_title", severity="low", title="README has no title", detail=""),
    Finding(check="readme_setup", severity="medium", title="README doesn't say how to run it", detail=""),
    Finding(check="readme_stack", severity="low", title="README doesn't say what it's built with", detail=""),
    Finding(check="readme_visual", severity="low", title="README has no screenshot or demo", detail=""),
    Finding(check="readme_dead_links", severity="medium", title="README links to missing files", detail="", data={"n": 2}),
    Finding(check="tests_missing", severity="medium", title="No tests", detail=""),
    Finding(check="ci_missing", severity="medium", title="Tests are not run automatically", detail=""),
    Finding(check="ci_failing", severity="high", title="CI is failing", detail=""),
    Finding(check="gitignore", severity="low", title="No .gitignore", detail=""),
    Finding(check="manifest", severity="low", title="No dependency manifest", detail=""),
    Finding(check="secret_file", severity="critical", title="Possible secrets committed", detail="", data={"path": ".env"}),
    Finding(check="key_file", severity="high", title="Possible key or credentials file", detail="", data={"path": "a.pem"}),
    Finding(check="placeholder", severity="info", title="Placeholder repository", detail="", data={"name": "test"}),
    Finding(check="stale", severity="info", title="No recent activity", detail="", data={"days": 400}),
    Finding(check="profile_readme_missing", severity="high", title="Profile README is missing", detail=""),
    Finding(check="profile_readme_short", severity="medium", title="Profile README is very short", detail="", data={"n": 20}),
    Finding(check="profile_no_links", severity="low", title="Profile README has no links", detail=""),
]


@pytest.fixture(scope="module")
def patch() -> Persona:
    return Persona.load(PERSONA_PATH)


def test_persona_loads(patch):
    assert (patch.id, patch.name, patch.role) == ("patch", "Patch", "GitHub maintainer")
    assert patch.line("finding.license") == "No license, so nobody can legally reuse it."


def test_missing_line_is_an_error(patch):
    with pytest.raises(KeyError):
        patch.line("finding.nonexistent")


@pytest.mark.parametrize("finding", FINDINGS, ids=lambda f: f"{f.check}-{'-'.join(map(str, f.data.values()))}")
def test_every_finding_has_a_line_in_patchs_voice(patch, finding):
    assert patch.has_line(line_key(finding))  # a written line, not the neutral fallback
    line = finding_line(patch, finding)
    assert "{" not in line
    assert patch.lint(line) == []


def test_lines_adapt_to_the_data(patch):
    by_data = {(f.check, tuple(f.data.values())): finding_line(patch, f) for f in FINDINGS}
    assert by_data[("topics", (0,))] == "No topics. Nobody can find it."
    assert by_data[("topics", (2,))] == "2 of the three topics it needs to be findable."
    assert by_data[("ci_missing", ())] == "Tests exist, but nothing runs them. No CI."
    assert by_data[("readme_nested", ("game/README.md", "game"))] == (
        "The README is inside game/, so the repository page shows nothing."
    )
    assert by_data[("secret_file", (".env",))].startswith("A file named .env is committed.")


def test_unknown_check_falls_back_to_the_neutral_title(patch):
    finding = Finding(check="brand_new_check", severity="low", title="Something new", detail="")
    assert finding_line(patch, finding) == "Something new."


def test_resolved_line_and_finding_key(patch):
    finding = Finding(check="secret_file", severity="critical", title="Possible secrets committed", detail="", data={"path": ".env"})
    assert resolved_line(patch, finding) == "Possible secrets committed: fixed."
    assert finding_key(finding) == "secret_file:.env"


def test_every_template_obeys_the_voice_rules(patch):
    """Fill each template with plausible values, then lint it like any other line Patch says."""
    sample = {
        "approvals_text": "2 approvals", "repos_text": "12 repositories", "changed_text": "3 of 12 repositories",
        "requests_text": "2 requests", "name": "old-project", "score": 72, "findings_text": "14 findings",
        "github_text": "2 GitHub requests", "claude_text": "0 model calls", "when": "14:30",
        "reason": "GitHub returned 500: Server Error", "n": 2, "path": ".env", "days": 400,
        "title": "No description", "dir": "game", "files_text": "A license and a .gitignore",
        "fixes_text": "the too-short README and the missing run instructions", "proposals_text": "3 proposals",
        "problem": "The draft linked to files that don't exist in the repository.",
        "topics_text": "5 topics", "needed": "administration=write", "number": "#12", "done_text": "3 changes",
        "failed_text": "1 other", "actions_text": "3 changes", "tag": "v1.2.0", "url": "https://example.com/demo",
        "stars": 25, "before": 61, "names": "old-project (34), other (41)",
        "lesson": "Keep descriptions under 100 characters.", "lessons_text": "3 lessons",
    }  # fmt: skip
    for key, template in patch.all_lines().items():
        fields = {name for _, name, _, _ in string.Formatter().parse(template) if name}
        assert fields <= set(sample), f"{key} uses a placeholder the test doesn't know: {fields - set(sample)}"
        assert patch.lint(template.format(**sample)) == [], key


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("This is amazing work.", "banned word: amazing"),
        ("Done!", "exclamation mark"),
        ("Shipped \U0001f680", "emoji"),
        ("One. Two. Three. Four.", "more than 3 sentences"),
        ("x" * 241, "longer than 240 characters"),
    ],
)
def test_lint_catches_voice_violations(patch, text, problem):
    assert problem in patch.lint(text)


def test_lint_does_not_miscount_dots_inside_words(patch):
    assert patch.lint("No .gitignore. Score 3.5 of 10. See README.md for more.") == []


def test_count_pluralises():
    assert count(1, "request") == "1 request" and count(0, "request") == "0 requests"
    assert count(1, "repository", "repositories") == "1 repository"
    assert count(12, "repository", "repositories") == "12 repositories"
