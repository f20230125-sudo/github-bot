from datetime import UTC, datetime

from app.agents.github.client import GitHubClient
from app.agents.github.models import RepoDetails, RepoMeta, RepoSnapshot
from app.agents.github.sync import (
    GraphQLSnapshotter,
    Known,
    RestSnapshotter,
    build_query,
    fetch_details,
    list_repos,
    plan_sync,
)
from tests.fake_github import VALID_TOKEN, FakeGitHub, FakeRepo, healthy_files


def meta(name: str = "a", **fields) -> RepoMeta:
    return RepoMeta(full_name=f"octo/{name}", name=name, owner="octo", pushed_at="2026-09-01T00:00:00Z", **fields)


def known(m: RepoMeta, **details) -> Known:
    return Known(meta_fp=m.fingerprint(), pushed_at=m.pushed_at, snapshot=RepoSnapshot(meta=m, details=RepoDetails(**details)))


def names(metas) -> list[str]:
    return [m.name for m in metas]


def test_plan_new_repository_needs_details():
    plan = plan_sync([meta("a")], {})
    assert names(plan.need_details) == ["a"] and not plan.meta_only and not plan.unchanged


def test_plan_unchanged_repository_is_left_alone():
    m = meta("a", stars=1)
    starred = m.model_copy(update={"stars": 99, "open_issues": 4})  # stars don't affect any check
    plan = plan_sync([starred], {"octo/a": known(m)})
    assert names(plan.unchanged) == ["a"] and not plan.need_details and not plan.meta_only


def test_plan_push_needs_details_but_a_new_description_does_not():
    m = meta("a")
    pushed = m.model_copy(update={"pushed_at": "2026-09-02T00:00:00Z"})
    described = m.model_copy(update={"description": "Now described."})
    assert names(plan_sync([pushed], {"octo/a": known(m)}).need_details) == ["a"]
    assert names(plan_sync([described], {"octo/a": known(m)}).meta_only) == ["a"]


def test_plan_rechecks_a_repository_whose_ci_was_still_running():
    m = meta("a")  # pushed on 1 September at midnight
    soon_after = datetime(2026, 9, 1, 0, 30, tzinfo=UTC)
    assert names(plan_sync([m], {"octo/a": known(m, ci_state="pending")}, now=soon_after).need_details) == ["a"]


def test_plan_gives_up_on_a_ci_run_that_never_finishes():
    """Otherwise every scheduled check would look inside the repository again, forever."""
    m = meta("a")
    next_day = datetime(2026, 9, 2, tzinfo=UTC)
    plan = plan_sync([m], {"octo/a": known(m, ci_state="pending")}, now=next_day)
    assert names(plan.unchanged) == ["a"] and not plan.need_details


def test_plan_never_looks_inside_forks_or_archived_repositories():
    fork = meta("f", fork=True)
    archived = meta("z", archived=True)
    plan = plan_sync([fork, archived], {})
    assert names(plan.meta_only) == ["f", "z"] and not plan.need_details


def test_plan_reports_removed_repositories():
    m = meta("gone")
    assert plan_sync([], {"octo/gone": known(m)}).removed == ["octo/gone"]


async def test_list_uses_the_public_endpoint_without_a_token_and_hides_private(db):
    fake = FakeGitHub([FakeRepo("pub"), FakeRepo("secret", private=True)])
    client = GitHubClient(db, transport=fake.transport())
    metas, unchanged = await list_repos(client, "octo")
    await client.aclose()
    assert names(metas) == ["pub"] and unchanged is False
    assert fake.calls[0].path == "/users/octo/repos"


async def test_list_with_a_token_includes_private(db):
    fake = FakeGitHub([FakeRepo("pub"), FakeRepo("secret", private=True)])
    client = GitHubClient(db, VALID_TOKEN, transport=fake.transport())
    metas, _ = await list_repos(client, "octo")
    await client.aclose()
    assert names(metas) == ["pub", "secret"] and fake.calls[0].path == "/user/repos"


async def test_rest_snapshot_reads_tree_readme_and_ci(db):
    fake = FakeGitHub([FakeRepo("full", files=healthy_files(), ci="failure")])
    client = GitHubClient(db, transport=fake.transport())
    details = await RestSnapshotter(client).fetch_one(meta("full"))
    await client.aclose()

    assert "tests/test_main.py" in details.files and "tests" in details.dirs
    assert details.readme_path == "README.md" and details.readme_text.startswith("# Sample project")
    assert details.ci_state == "failure" and details.tree_depth is None and details.tree_sha
    assert len(fake.calls) == 3  # tree, readme, latest run


async def test_rest_skips_requests_that_cannot_help(db):
    fake = FakeGitHub([FakeRepo("bare", files={"main.py": ""}), FakeRepo("nothing", empty=True)])
    client = GitHubClient(db, transport=fake.transport())
    bare = await RestSnapshotter(client).fetch_one(meta("bare"))
    assert len(fake.calls) == 1  # no README and no workflow in the tree, so nothing more to ask
    empty = await RestSnapshotter(client).fetch_one(meta("nothing"))
    await client.aclose()
    assert bare.readme_path is None and bare.ci_state is None
    assert empty.empty is True


async def test_rest_reports_a_running_ci_as_pending(db):
    fake = FakeGitHub([FakeRepo("busy", files=healthy_files(), ci="in_progress")])
    client = GitHubClient(db, transport=fake.transport())
    details = await RestSnapshotter(client).fetch_one(meta("busy"))
    await client.aclose()
    assert details.ci_state == "pending"


async def test_graphql_and_rest_agree(db):
    repo = FakeRepo("full", files={**healthy_files(), "a/b/c/d/deep.py": ""}, ci="success")
    fake = FakeGitHub([repo])
    client = GitHubClient(db, VALID_TOKEN, transport=fake.transport())
    via_rest = await RestSnapshotter(client).fetch_one(meta("full"))
    fake.reset()
    via_graphql = (await GraphQLSnapshotter(client).fetch([meta("full")]))["octo/full"]
    await client.aclose()

    assert len(fake.calls) == 1 and fake.calls[0].path == "/graphql"
    assert via_graphql.readme_text == via_rest.readme_text and via_graphql.ci_state == via_rest.ci_state
    assert via_graphql.tree_sha == via_rest.tree_sha and via_graphql.tree_depth == 4
    # GraphQL lists four levels: everything REST saw except the file five levels down.
    assert set(via_rest.files) - set(via_graphql.files) == {"a/b/c/d/deep.py"}
    assert set(via_graphql.dirs) == set(via_rest.dirs)


async def test_graphql_reads_ci_from_the_newest_commit_that_has_a_result(db):
    """A push made by a workflow starts no workflow, so the newest commits can have no result at all."""
    fake = FakeGitHub(
        [
            FakeRepo("botted", files=healthy_files(), ci="failure", unchecked_commits=2),
            FakeRepo("never", files=healthy_files(), unchecked_commits=2),
        ]
    )
    client = GitHubClient(db, VALID_TOKEN, transport=fake.transport())
    details = await GraphQLSnapshotter(client).fetch([meta("botted"), meta("never")])
    await client.aclose()

    assert details["octo/botted"].ci_state == "failure"  # not hidden by the two commits on top of it
    assert details["octo/never"].ci_state is None
    assert len(fake.calls) == 1  # still the one query


async def test_graphql_covers_twelve_repositories_in_two_queries(db):
    fake = FakeGitHub([FakeRepo(f"r{i:02}", files=healthy_files()) for i in range(12)])
    client = GitHubClient(db, VALID_TOKEN, transport=fake.transport())
    details, how = await fetch_details(client, [meta(f"r{i:02}") for i in range(12)])
    await client.aclose()
    assert how == "graphql" and len(details) == 12 and len(fake.calls) == 2


async def test_graphql_falls_back_to_rest_for_an_unusual_readme_name(db):
    fake = FakeGitHub([FakeRepo("odd", files={"Readme.markdown": "# Odd\n\nHello."})])
    client = GitHubClient(db, VALID_TOKEN, transport=fake.transport())
    details = (await GraphQLSnapshotter(client).fetch([meta("odd")]))["octo/odd"]
    await client.aclose()
    assert details.readme_path == "Readme.markdown" and details.readme_text.startswith("# Odd")
    assert [c.path for c in fake.calls] == ["/graphql", "/repos/octo/odd/readme"]


async def test_fetch_details_uses_rest_when_graphql_is_unavailable(db):
    fake = FakeGitHub([FakeRepo("a", files=healthy_files())])
    fake.graphql_broken = True
    client = GitHubClient(db, VALID_TOKEN, transport=fake.transport())
    details, how = await fetch_details(client, [meta("a")])
    await client.aclose()
    assert how == "rest" and details["octo/a"].readme_path == "README.md"


async def test_fetch_details_without_a_token_uses_rest_and_nothing_for_an_empty_list(db):
    fake = FakeGitHub([FakeRepo("a", files=healthy_files())])
    client = GitHubClient(db, transport=fake.transport())
    assert await fetch_details(client, []) == ({}, "none") and fake.calls == []
    _, how = await fetch_details(client, [meta("a")])
    await client.aclose()
    assert how == "rest"


def test_query_uses_variables_not_interpolated_names():
    query = build_query(2)
    assert "$o0: String!, $n0: String!, $o1: String!, $n1: String!" in query
    assert "r1: repository(owner: $o1, name: $n1)" in query
