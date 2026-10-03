import httpx
import pytest

from app.agents.github.client import (
    AuthError,
    Conflict,
    GitHubClient,
    NotFound,
    PermissionDenied,
    RateLimited,
)
from tests.fake_github import VALID_TOKEN, FakeGitHub, FakeRepo


def canned(status: int, headers: dict | None = None, body: dict | None = None) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status, json=body or {"message": "x"}, headers=headers))


async def test_second_get_is_conditional_and_served_from_cache(db, fake):
    client = GitHubClient(db, transport=fake.transport())
    first = await client.get("/users/octo/repos", {"per_page": 100})
    second = await client.get("/users/octo/repos", {"per_page": 100})
    await client.aclose()

    assert fake.statuses() == [200, 304]
    assert first.not_modified is False and second.not_modified is True
    assert second.data == first.data  # the 304 had no body: this came from our cache


async def test_cache_is_not_shared_between_anonymous_and_token(db, fake):
    anonymous = GitHubClient(db, transport=fake.transport())
    await anonymous.get("/users/octo/repos")
    await anonymous.aclose()

    authed = GitHubClient(db, VALID_TOKEN, transport=fake.transport())
    await authed.get("/users/octo/repos")
    await authed.aclose()

    assert fake.statuses() == [200, 200]  # the token's first request sent no ETag


async def test_pagination_follows_next_links_and_reports_unchanged(db):
    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page", "1")
        etag = f'"p{page}"'
        if request.headers.get("if-none-match") == etag:
            return httpx.Response(304, headers={"etag": etag})
        headers = {"etag": etag}
        if page == "1":
            headers["link"] = '<https://api.github.com/items?page=2>; rel="next"'
        return httpx.Response(200, json=[{"n": int(page)}], headers=headers)

    client = GitHubClient(db, transport=httpx.MockTransport(handler))
    items, unchanged = await client.get_all("/items")
    again, unchanged_again = await client.get_all("/items")
    await client.aclose()

    assert items == [{"n": 1}, {"n": 2}] and unchanged is False
    assert again == items and unchanged_again is True


async def test_observer_sees_every_request(db, fake):
    seen = []

    async def observe(info):
        seen.append((info.method, info.path, info.status, info.not_modified, info.remaining))

    client = GitHubClient(db, transport=fake.transport())
    client.observer = observe
    await client.get("/users/octo/repos")
    await client.get("/users/octo/repos")
    await client.aclose()

    assert seen == [
        ("GET", "/users/octo/repos", 200, False, 4999),
        ("GET", "/users/octo/repos", 304, True, 4999),
    ]


@pytest.mark.parametrize(
    ("status", "headers", "error"),
    [
        (401, {}, AuthError),
        (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1790000000"}, RateLimited),
        (429, {"retry-after": "30"}, RateLimited),
        (403, {"x-accepted-github-permissions": "contents=write"}, PermissionDenied),
        (404, {}, NotFound),
        (409, {}, Conflict),
    ],
)
async def test_errors_are_typed(db, status, headers, error):
    client = GitHubClient(db, transport=canned(status, headers))
    with pytest.raises(error) as caught:
        await client.get("/anything")
    await client.aclose()
    if error is RateLimited and "x-ratelimit-reset" in headers:
        assert caught.value.reset_at == 1790000000
    if error is PermissionDenied:
        assert caught.value.needed == "contents=write"


async def test_graphql_needs_a_token(db, fake):
    client = GitHubClient(db, transport=fake.transport())
    with pytest.raises(AuthError):
        await client.graphql("query { viewer { login } }", {})
    await client.aclose()
    assert fake.calls == []  # refused before any request was sent


async def test_bad_token_is_rejected(db):
    fake = FakeGitHub([FakeRepo("a")])
    client = GitHubClient(db, "github_pat_" + "w" * 40, transport=fake.transport())
    with pytest.raises(AuthError):
        await client.get("/user")
    await client.aclose()
