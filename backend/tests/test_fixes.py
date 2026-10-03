"""Template fixes and the checks on Claude's drafts. No network, no model."""

from app.agents.github.checks import run_checks
from app.agents.github.drafts import (
    RepoMetadata,
    SweepDraft,
    SweepTarget,
    clean_description,
    clean_topics,
    sweep_items,
    verify_readme,
)
from app.agents.github.fixes import ci_fix, gitignore_fix, license_fix, needs_package_json
from tests.fake_github import GOOD_README, healthy_files
from tests.test_checks import NOW, snapshot

# -- templates --------------------------------------------------------------------------------


def test_license_is_mit_with_the_holder_and_year():
    fix = license_fix("Octo Cat", 2026)
    assert fix.path == "LICENSE" and fix.finding == "license" and fix.source == "template"
    assert fix.content.startswith("MIT License\n\nCopyright (c) 2026 Octo Cat\n")
    assert "{" not in fix.content


def test_gitignore_follows_the_stack():
    python = gitignore_fix(snapshot({"app.py": ""})).content
    assert "__pycache__/" in python and "node_modules/" not in python

    both = gitignore_fix(snapshot({"backend/app.py": "", "frontend/package.json": "{}"})).content
    assert "__pycache__/" in both and "node_modules/" in both
    assert both.count(".env\n") == 1  # merged without repeats

    static = gitignore_fix(snapshot({"index.html": "", "js/main.js": ""}, language="JavaScript")).content
    assert ".DS_Store" in static and "__pycache__/" not in static


def test_python_ci_runs_from_the_folder_that_holds_the_project():
    files = {"backend/requirements.txt": "", "backend/app/main.py": "", "backend/tests/test_api.py": "", "frontend/package.json": "{}"}
    fix = ci_fix(snapshot(files))
    assert fix.path == ".github/workflows/ci.yml" and fix.finding == "ci_missing"
    assert "working-directory: backend" in fix.content
    assert "pip install -r requirements.txt" in fix.content and "run: pytest" in fix.content
    assert "actions/checkout@v4" in fix.content and "actions/setup-python@v5" in fix.content
    assert "branches: [main]" in fix.content and "test-node" not in fix.content


def test_python_ci_at_the_root_has_no_working_directory():
    root = ci_fix(snapshot({"pyproject.toml": "", "src/pkg/a.py": "", "tests/test_a.py": ""})).content
    assert "working-directory" not in root and "pip install -e ." in root

    bare = ci_fix(snapshot({"main.py": "", "tests/test_main.py": ""})).content
    assert "working-directory" not in bare and "pip install pytest" in bare and "requirements" not in bare


def test_ci_uses_the_repository_default_branch():
    assert "branches: [trunk]" in ci_fix(snapshot({"tests/test_a.py": ""}, default_branch="trunk")).content


def test_node_ci_only_when_the_package_really_has_a_test_script():
    files = {"package.json": "", "package-lock.json": "", "lib/math.test.ts": "", "lib/math.ts": ""}
    snap = snapshot(files, language="TypeScript")
    assert needs_package_json(snap) == ["package.json"]

    assert ci_fix(snap, {"package.json": '{"scripts": {"build": "tsc"}}'}) is None  # nothing to run
    assert ci_fix(snap, {"package.json": "not json"}) is None

    fix = ci_fix(snap, {"package.json": '{"scripts": {"test": "vitest run"}}'})
    assert "actions/setup-node@v4" in fix.content and "cache: npm" in fix.content
    assert "run: npm ci" in fix.content and "run: npm test" in fix.content


def test_node_ci_in_a_subfolder_without_a_lockfile():
    files = {"web/package.json": "", "web/src/a.test.js": ""}
    fix = ci_fix(snapshot(files, language="JavaScript"), {"web/package.json": '{"scripts": {"test": "jest"}}'})
    assert "working-directory: web" in fix.content and "run: npm install" in fix.content and "cache: npm" not in fix.content


def test_no_tests_means_no_ci_and_no_need_to_read_package_json():
    snap = snapshot({"app.py": "", "package.json": ""})
    assert ci_fix(snap) is None and needs_package_json(snap) == []


# -- cleaning what Claude writes --------------------------------------------------------------


def test_descriptions_are_cleaned_or_dropped():
    assert clean_description("  A tool that\n does things.  ") == "A tool that does things"
    assert clean_description("") is None and clean_description(None) is None
    assert clean_description("x" * 201) is None
    assert clean_description("Great tool \U0001f680") is None


def test_topics_follow_githubs_rules_and_keep_existing_ones_first():
    cleaned = clean_topics(["Python", "Web App", "llm_agents", "c++", "-bad-", "python", ""], ["fastapi"])
    assert cleaned == ["fastapi", "python", "web-app", "llm-agents", "c", "bad"]
    assert len(clean_topics([f"t{i}" for i in range(20)], [])) == 8


def targets():
    needs_both = snapshot(full_name="octo/a", name="a", description=None, topics=[])
    needs_topics = snapshot(full_name="octo/b", name="b", description="Has one.", topics=["python"])
    return [SweepTarget(needs_both, True, True), SweepTarget(needs_topics, False, True)]


def test_sweep_items_only_cover_what_was_asked():
    draft = SweepDraft(
        repos=[
            RepoMetadata(repo="octo/a", description="Does a thing.", topics=["Python", "CLI"]),
            RepoMetadata(repo="OCTO/B", description="Must be ignored: it already has one.", topics=["fastapi", "python"]),
            RepoMetadata(repo="octo/stranger", description="Never asked about.", topics=["x"]),
            RepoMetadata(repo="octo/a", description="A second answer for the same repository.", topics=[]),
        ]
    )
    items, notes = sweep_items(draft, targets())

    assert [(i.repo, i.description, i.topics) for i in items] == [
        ("octo/a", "Does a thing", ["python", "cli"]),
        ("octo/b", None, ["python", "fastapi"]),
    ]
    assert items[1].current_topics == ["python"] and items[0].meta_fp == targets()[0].snapshot.meta.fingerprint()
    assert notes == []


def test_sweep_notes_say_what_was_dropped():
    draft = SweepDraft(repos=[RepoMetadata(repo="octo/a", description="x" * 300, topics=["!!!"])])
    items, notes = sweep_items(draft, targets())
    assert items == []
    assert notes == [
        "a: the description was missing or unusable, so it was left out.",
        "a: no usable topics came back.",
        "b: Claude's answer left it out.",
    ]


# -- checking a drafted README ----------------------------------------------------------------


def findings_for(snap, *checks):
    return [f for f in run_checks(snap, now=NOW) if f.check in checks]


def test_a_good_readme_draft_passes_and_says_what_it_fixes():
    snap = snapshot({**healthy_files(), "README.md": "# x\n\nTodo."})
    verdict = verify_readme(snap, findings_for(snap, "readme_short"), GOOD_README, "# x\n\nTodo.")
    assert verdict.ok and verdict.resolves == ["readme_short"] and verdict.remaining == [] and verdict.note is None


def test_a_draft_for_a_buried_readme_is_judged_as_if_it_were_in_the_root():
    snap = snapshot({f"game/{path}": text for path, text in healthy_files().items()})
    draft = GOOD_README.replace("docs/shot.png", "game/docs/shot.png")
    verdict = verify_readme(snap, findings_for(snap, "readme_nested"), draft, None)
    assert verdict.ok and verdict.resolves == ["readme_nested"]

    broken = verify_readme(snap, findings_for(snap, "readme_nested"), GOOD_README, None)  # links not rewritten
    assert broken.ok is False and "files that don't exist" in broken.problem


def test_drafts_that_invent_files_or_fix_nothing_are_thrown_away():
    snap = snapshot({**healthy_files(), "README.md": "# x\n\nTodo."})
    target = findings_for(snap, "readme_short")

    invented = verify_readme(snap, target, GOOD_README + "\nSee [the guide](docs/guide.md).\n", "# x")
    assert invented.ok is False and invented.problem == "The draft linked to files that don't exist in the repository."

    unchanged = verify_readme(snap, target, "# x\n\n" + "Still short. " * 20, "# x")
    assert unchanged.ok is False and unchanged.problem == "The draft didn't fix what it was meant to fix."

    assert verify_readme(snap, target, "tiny", "# x").problem == "The draft was too short to be a README."


def test_a_draft_that_replaces_most_of_a_real_readme_carries_a_warning():
    original = "# Title\n\n## Usage\n\n" + "\n".join(f"Line {i} of the original text, kept or not." for i in range(30))
    snap = snapshot({**healthy_files(), "README.md": original})
    target = findings_for(snap, "readme_setup", "readme_stack")
    assert {f.check for f in target} == {"readme_stack"}  # "Usage" already counts as how to run it

    kept = verify_readme(snap, target, original + "\n\n## Tech stack\n\nPython.\n", original)
    assert kept.ok and kept.note is None

    rewritten = verify_readme(snap, target, GOOD_README, original)
    assert rewritten.ok and rewritten.note == "This rewrites most of the existing README. Read the diff before approving."
