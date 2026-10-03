from datetime import UTC, datetime

from app.agents.github.checks import build_report, classify, run_checks
from app.agents.github.models import RepoDetails, RepoMeta, RepoSnapshot
from tests.fake_github import GOOD_README, healthy_files

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def snapshot(files: dict[str, str] | None = None, *, tree_depth: int | None = None, ci: str | None = "success", **meta):
    files = healthy_files() if files is None else files
    dirs = sorted({"/".join(p.split("/")[:i]) for p in files for i in range(1, len(p.split("/")))})
    readme = next((f for f in files if "/" not in f and f.lower().startswith("readme")), None)
    defaults = dict(
        full_name="octo/sample", name="sample", owner="octo", description="A project.",
        homepage="https://example.com", topics=["a", "b", "c"], license="MIT", language="Python",
        pushed_at="2026-09-01T00:00:00Z",
    )  # fmt: skip
    return RepoSnapshot(
        meta=RepoMeta(**{**defaults, **meta}),
        details=RepoDetails(
            files=list(files), dirs=dirs, tree_depth=tree_depth, readme_path=readme,
            readme_text=files.get(readme) if readme else None, ci_state=ci,
        ),  # fmt: skip
    )


def checks(snap: RepoSnapshot) -> dict[str, object]:
    return {f.check: f for f in run_checks(snap, now=NOW)}


def test_healthy_repository_scores_100():
    report = build_report(snapshot(), now=NOW)
    assert report.findings == [] and report.score == 100 and report.kind == "project"


def test_metadata_findings():
    found = checks(snapshot(description=None, topics=[], homepage=None, license=None))
    assert {"description", "topics"} <= set(found)
    assert "license" not in found  # a LICENSE file counts even when GitHub can't name the license

    files = healthy_files()
    del files["LICENSE"]
    found = checks(snapshot(files, license=None))
    assert found["license"].severity == "high" and found["license"].fix == "file"


def test_live_demo_link_and_screenshot_are_only_expected_when_there_is_an_interface():
    plain_readme = "# Title\n\n## Setup\n\n## Architecture\n\n" + "Words about the project. " * 30
    cli = {**healthy_files(), "README.md": plain_readme}
    assert not {"homepage", "readme_visual"} & set(checks(snapshot(cli, homepage=None)))

    web = {**cli, "frontend/src/app/page.tsx": ""}
    assert {"homepage", "readme_visual"} <= set(checks(snapshot(web, homepage=None)))
    assert "homepage" not in checks(snapshot(web, homepage="https://demo.example.com"))


def test_few_topics_is_a_lighter_finding_than_none():
    none = checks(snapshot(topics=[]))["topics"]
    few = checks(snapshot(topics=["python"]))["topics"]
    assert (none.severity, none.weight, none.data["n"]) == ("medium", 6, 0)
    assert (few.severity, few.weight, few.data["n"]) == ("low", 3, 1)


def test_missing_and_short_readme():
    files = healthy_files()
    del files["README.md"]
    assert "readme_missing" in checks(snapshot(files))

    found = checks(snapshot({**healthy_files(), "README.md": "# x\n\nTodo."}))
    assert found["readme_short"].data["n"] == 10
    assert not any(k.startswith("readme_") and k != "readme_short" for k in found)  # no pile-on


def test_readme_buried_in_a_subfolder_is_its_own_finding():
    """Like a repository whose whole project sits in one folder: GitHub shows no README for it."""
    nested = {f"game/{path}": text for path, text in healthy_files().items()}
    found = checks(snapshot(nested))
    assert "readme_missing" not in found
    assert found["readme_nested"].data == {"path": "game/README.md", "dir": "game"}
    assert found["readme_nested"].fix == "file" and "game/README.md" in found["readme_nested"].detail


def test_readme_sections_are_detected_by_heading_or_command():
    bare = "# Title\n\n" + "Words about the project. " * 30
    found = checks(snapshot({**healthy_files(), "README.md": bare}))
    assert {"readme_setup", "readme_stack"} <= set(found)

    with_command = bare + "\n\n```bash\nnpm install\nnpm run dev\n```\n"
    assert "readme_setup" not in checks(snapshot({**healthy_files(), "README.md": with_command}))

    assert not any(k.startswith("readme_") for k in checks(snapshot({**healthy_files(), "README.md": GOOD_README})))


def test_readme_headings_seen_in_real_repositories_count():
    """Section names taken from real READMEs that an earlier, stricter rule wrongly flagged."""
    body = "\n\nWords about the project. " * 30
    for run_heading in ("Run", "Run it", "Running locally", "Usage", "Setup (Windows / PowerShell)", "Try it"):
        text = f"# Title\n\n## {run_heading}{body}\n\n## Architecture\n"
        assert "readme_setup" not in checks(snapshot({**healthy_files(), "README.md": text})), run_heading
    for stack_heading in ("How it works", "Tech stack", "The model", "Design decisions", "How this was built", "Layout"):
        text = f"# Title\n\n## Setup{body}\n\n## {stack_heading}\n"
        assert "readme_stack" not in checks(snapshot({**healthy_files(), "README.md": text})), stack_heading


def test_dead_links_only_where_the_listing_is_complete():
    text = GOOD_README + "\n[guide](docs/guide.md) [deep](a/b/c/d/e.md) [site](https://x.dev) [top](#setup)\n"
    files = {**healthy_files(), "README.md": text}

    full = checks(snapshot(files))["readme_dead_links"]
    assert full.data["n"] == 2 and "docs/guide.md" in full.detail and "a/b/c/d/e.md" in full.detail

    shallow = checks(snapshot(files, tree_depth=4))["readme_dead_links"]
    assert shallow.data["n"] == 1  # the five-level path is deeper than we listed: not judged


def test_engineering_findings():
    files = healthy_files()
    del files["tests/test_main.py"], files[".github/workflows/ci.yml"], files[".gitignore"], files["requirements.txt"]
    found = checks(snapshot(files, ci=None))
    assert {"tests_missing", "gitignore", "manifest"} <= set(found)
    assert "ci_missing" not in found  # nothing for CI to run yet, so it isn't raised on top of "no tests"

    files = healthy_files()
    del files[".github/workflows/ci.yml"]
    assert checks(snapshot(files, ci=None))["ci_missing"].fix == "file"  # tests exist: a template can run them
    assert checks(snapshot(files, ci=None, language="Go"))["ci_missing"].fix == "manual"  # no template for Go

    assert checks(snapshot(ci="failure"))["ci_failing"].severity == "high"
    assert "ci_failing" not in checks(snapshot(ci="pending"))


def test_plain_browser_project_needs_no_manifest():
    static = {"index.html": "", "js/main.js": "", "README.md": GOOD_README, "LICENSE": "", ".gitignore": "", "docs/shot.png": ""}
    assert "manifest" not in checks(snapshot(static, language="JavaScript", ci=None))

    node_app = {"server.js": "", "README.md": GOOD_README, "LICENSE": "", ".gitignore": "", "docs/shot.png": ""}
    assert "manifest" in checks(snapshot(node_app, language="JavaScript", ci=None))


def test_tests_are_found_in_nested_folders_and_by_file_name():
    files = healthy_files()
    del files["tests/test_main.py"]
    assert "tests_missing" in checks(snapshot(files))
    assert "tests_missing" not in checks(snapshot({**files, "backend/tests/conftest.py": ""}))
    assert "tests_missing" not in checks(snapshot({**files, "lib/indicators.test.ts": ""}))
    assert "tests_missing" in checks(snapshot({**files, "node_modules/x/tests/a.js": ""}))  # vendored code


def test_no_code_means_no_engineering_checks():
    found = checks(snapshot({"README.md": GOOD_README, "LICENSE": "", "docs/shot.png": ""}, language=None, ci=None))
    assert not {"tests_missing", "ci_missing", "gitignore", "manifest"} & set(found)


def test_committed_secrets_are_critical_but_examples_are_fine():
    files = {**healthy_files(), ".env": "KEY=1", "config/.env.production": "", ".env.example": "", "certs/server.pem": ""}
    findings = run_checks(snapshot(files), now=NOW)
    secrets = sorted(f.data["path"] for f in findings if f.check == "secret_file")
    assert secrets == [".env", "config/.env.production"]
    assert [f.data["path"] for f in findings if f.check == "key_file"] == ["certs/server.pem"]
    assert all(f.severity == "critical" for f in findings if f.check == "secret_file")
    assert build_report(snapshot(files), now=NOW).score == 100 - 25 - 25 - 10


def test_stale_repository_is_noted_but_costs_nothing():
    found = checks(snapshot(pushed_at="2024-01-01T00:00:00Z"))
    assert found["stale"].severity == "info" and found["stale"].weight == 0


def test_kinds():
    assert classify(snapshot(name="octo", full_name="octo/octo")) == "profile"
    assert classify(snapshot(name="test", full_name="octo/test")) == "placeholder"
    assert classify(snapshot(fork=True)) == "skipped"
    assert classify(snapshot(archived=True)) == "skipped"

    placeholder = build_report(snapshot(name="test", full_name="octo/test", description=None), now=NOW)
    assert [f.check for f in placeholder.findings] == ["placeholder"] and placeholder.score is None

    skipped = build_report(snapshot(fork=True, description=None), now=NOW)
    assert skipped.findings == [] and skipped.score is None


def test_profile_repository_is_judged_on_its_readme_only():
    empty = build_report(snapshot({}, name="octo", full_name="octo/octo", description=None, license=None), now=NOW)
    assert [f.check for f in empty.findings] == ["profile_readme_missing"] and empty.score == 60

    thin = build_report(snapshot({"README.md": "Hi, I'm Octo."}, name="octo", full_name="octo/octo"), now=NOW)
    assert {f.check for f in thin.findings} == {"profile_readme_short", "profile_no_links"} and thin.score == 70


def test_score_never_goes_below_zero():
    files = {f".env{suffix}": "" for suffix in ("", ".local", ".prod")} | {"id_rsa": "", "a.pem": "", "b.pem": "", "c.pem": ""}
    report = build_report(snapshot(files, description=None, topics=[], homepage=None, license=None, ci=None), now=NOW)
    assert report.score == 0
