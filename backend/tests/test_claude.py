"""Calling Claude through the CLI, against the stand-in. No real Claude call is ever made here."""

from datetime import datetime

import pytest
from pydantic import BaseModel

from app.core.claude import ClaudeBlocked, extract_json
from app.core.claude_cli import ClaudeCli, ClaudeError, ClaudeUnavailable, clean_env
from app.core.runs import run
from tests.claude_helpers import UNDER_LIMIT, USAGE_TEXT, ZERO_USAGE, FakeClaude, flag


class Answer(BaseModel):
    title: str
    count: int


@pytest.fixture
def fake(tmp_path):
    return FakeClaude(tmp_path)


def usage_route(text: str = USAGE_TEXT, usage: dict | None = None, **extra):
    """How the stand-in answers the /usage check. Zero tokens means Claude Code answered by itself."""
    return {"match": "/usage", "response": {"text": text, "usage": ZERO_USAGE if usage is None else usage, **extra}}


# -- the CLI call -----------------------------------------------------------------------------


async def test_call_is_stripped_down_and_carries_no_api_key(fake, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-never-reach-the-cli")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")
    monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
    fake.set({"responses": [{"text": "hello"}]})

    await fake.cli().ask(system="You are Patch.", prompt="The task.", model="sonnet", effort="low")
    call = fake.calls()[0]

    assert call["env"] == {}  # nothing that could switch on a paid API
    assert call["stdin"] == "The task."
    assert call["cwd"] == str(fake.cwd)  # its own empty folder
    for needed in ("--safe-mode", "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence", "--verbose"):
        assert needed in call["args"]
    assert "--bare" not in call["args"]  # bare mode ignores the subscription and needs a paid key
    assert flag(call, "--tools") == ""  # no tools at all
    assert flag(call, "--system-prompt") == "You are Patch."
    assert flag(call, "--model") == "sonnet" and flag(call, "--effort") == "low"
    assert flag(call, "--output-format") == "stream-json"


def test_clean_env_removes_every_paid_api_switch():
    env = {"PATH": "x", "ANTHROPIC_API_KEY": "k", "anthropic_auth_token": "t", "CLAUDE_CODE_USE_VERTEX": "1",
           "AWS_BEARER_TOKEN_BEDROCK": "b", "HOME": "h"}  # fmt: skip
    assert clean_env(env) == {"PATH": "x", "HOME": "h"}


async def test_result_is_parsed(fake):
    fake.set({"responses": [{
        "text": "done", "structured": {"title": "t", "count": 2}, "model": "claude-sonnet-5-5",
        "rate_limits": [UNDER_LIMIT],
        "usage": {"input_tokens": 100, "output_tokens": 40, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 60},
    }]})  # fmt: skip
    result = await fake.cli().ask(system="s", prompt="p", model="sonnet", schema=Answer.model_json_schema())

    assert result.text == "done" and result.structured == {"title": "t", "count": 2}
    assert (result.input_tokens, result.output_tokens, result.cache_read_tokens, result.cache_write_tokens) == (100, 40, 900, 60)
    assert result.total_tokens == 1100 and result.model == "claude-sonnet-5-5"
    assert result.rate_limits == [UNDER_LIMIT] and result.api_key_source == "none"
    assert '"title"' in flag(fake.calls()[0], "--json-schema")


async def test_a_run_that_reports_an_api_key_is_stopped(fake):
    fake.set({"responses": [{"api_key_source": "ANTHROPIC_API_KEY", "hang": 30, "text": "should never get here"}]})
    with pytest.raises(ClaudeUnavailable) as caught:
        await fake.cli().ask(system="s", prompt="p", model="haiku")
    assert "could cost money" in str(caught.value)


async def test_timeout_kills_the_process(fake):
    fake.set({"responses": [{"hang": 30}]})
    with pytest.raises(ClaudeError) as caught:
        await fake.cli(timeout=1).ask(system="s", prompt="p", model="haiku")
    assert "took longer than 1 seconds" in str(caught.value)


async def test_missing_result_and_error_result_are_errors(fake):
    fake.set({"responses": [{"no_result": True}]})
    with pytest.raises(ClaudeError) as caught:
        await fake.cli().ask(system="s", prompt="p", model="haiku")
    assert "ended without a result: fake failure" in str(caught.value)

    fake.set({"responses": [{"is_error": True, "text": "Usage limit reached", "rate_limits": [{"status": "rejected"}]}]})
    with pytest.raises(ClaudeError) as caught:
        await fake.cli().ask(system="s", prompt="p", model="haiku")
    assert "Usage limit reached" in str(caught.value)
    assert caught.value.result.rate_limits == [{"status": "rejected"}]  # the report isn't lost with the error


# -- sign-in ----------------------------------------------------------------------------------


async def test_subscription_sign_in_is_accepted(fake):
    status = await fake.cli().auth_status()
    assert status.ok and status.plan == "pro" and status.method == "claude.ai"


@pytest.mark.parametrize(
    ("auth", "expected"),
    [
        ({"loggedIn": False}, "isn't signed in"),
        ({"loggedIn": True, "authMethod": "api_key"}, "could cost money"),
        ({"loggedIn": True, "authMethod": None}, "could cost money"),
    ],
)
async def test_anything_but_a_subscription_is_refused(fake, auth, expected):
    fake.set({"auth": auth})
    status = await fake.cli().auth_status()
    assert status.ok is False and expected in status.problem


async def test_not_installed(tmp_path):
    cli = ClaudeCli([], tmp_path / "cwd")
    status = await cli.auth_status()
    assert cli.installed is False and status.installed is False and "isn't installed" in status.problem
    with pytest.raises(ClaudeUnavailable):
        await cli.ask(system="s", prompt="p", model="haiku")


# -- the usage stop, end to end ---------------------------------------------------------------


async def test_no_reading_means_no_call(fake, db):
    claude = fake.service(db)
    with pytest.raises(ClaudeBlocked) as caught:
        await claude.ask(None, "draft", system="s", prompt="p", model="sonnet")
    assert caught.value.decision.code == "no_reading"
    assert fake.calls() == []  # nothing was sent


async def test_connection_test_reads_usage_for_free_then_calls_are_allowed(fake, db):
    fake.set({"routes": [usage_route()], "responses": [{"structured": {"ok": True}, "rate_limits": [UNDER_LIMIT]}]})
    claude = fake.service(db)

    report = await claude.test()
    assert report["auth"]["ok"] and report["usage"] == {
        "ok": True, "answered_locally": True, "text": USAGE_TEXT, "tokens": 0, "reports": [],
    }  # fmt: skip
    assert report["structured"]["ok"] and report["structured"]["via"] == "schema" and report["structured"]["tokens"] == 1020
    assert report["guard"]["usage_command"] == "local"
    assert report["guard"]["windows"]["session"]["percent"] == 12 and report["guard"]["windows"]["weekly"]["percent"] == 30

    probe = fake.calls()[0]
    assert probe["args"][probe["args"].index("-p") + 1] == "/usage"
    assert "--disable-slash-commands" not in probe["args"] and flag(probe, "--tools") == ""

    result = await claude.ask(None, "draft", system="s", prompt="p", model="sonnet")
    assert result.structured == {"ok": True} and len(fake.calls()) == 3


async def test_usage_can_also_come_from_the_report_that_accompanies_a_call(fake, db):
    """If /usage isn't answered locally, the model replies instead: a tiny call whose report carries the figure."""
    fake.set({"routes": [usage_route(text="OK", usage={"input_tokens": 30, "output_tokens": 2}, rate_limits=[UNDER_LIMIT])],
              "responses": [{"text": '{"ok": true}'}]})  # fmt: skip
    claude = fake.service(db)
    report = await claude.test()

    assert report["usage"]["answered_locally"] is False and report["usage"]["tokens"] == 32
    assert report["guard"]["usage_command"] == "model" and report["guard"]["windows"]["session"]["percent"] == 12
    assert report["structured"]["via"] == "text"  # no schema support from the CLI: parsed from the reply instead


async def test_over_forty_percent_blocks_calls(fake, db):
    fake.set({"routes": [usage_route(text="Current session\n44% used\n\nCurrent week (all models)\n10% used")]})
    claude = fake.service(db)
    report = await claude.test()

    assert report["guard"]["allowed"] is False and report["guard"]["code"] == "over_limit"
    assert report["structured"] == {"ok": False, "skipped": True, "error": report["guard"]["reason"]}
    with pytest.raises(ClaudeBlocked) as caught:
        await claude.ask(None, "draft", system="s", prompt="p", model="sonnet")
    assert "44%" in str(caught.value) and len(fake.calls()) == 1  # only the usage check ever ran


async def test_an_aged_reading_is_refreshed_before_the_next_call(fake, db):
    now = [1_800_000_000.0]
    fake.set({"routes": [usage_route()], "responses": [{"text": "answer"}]})
    claude = fake.service(db, clock=lambda: now[0])
    await claude.test()
    count = len(fake.calls())

    now[0] += 3600  # an hour later the reading is too old to trust
    await claude.ask(None, "draft", system="s", prompt="p", model="sonnet")
    new = fake.calls()[count:]
    assert [c["args"][c["args"].index("-p") + 1] for c in new] == ["/usage", "Do the task described in the input."]


async def test_if_the_refresh_gives_no_percentage_the_call_is_not_made(fake, db):
    now = [1_800_000_000.0]
    fake.set({"routes": [usage_route(text="OK", usage={"input_tokens": 30, "output_tokens": 2}, rate_limits=[UNDER_LIMIT])],
              "responses": [{"text": '{"ok": true}'}]})  # fmt: skip
    claude = fake.service(db, clock=lambda: now[0])
    await claude.test()

    now[0] += 3600
    fake.set({"routes": [usage_route(text="OK", usage={"input_tokens": 30, "output_tokens": 2})], "responses": [{"text": "x"}]})
    with pytest.raises(ClaudeBlocked) as caught:
        await claude.ask(None, "draft", system="s", prompt="p", model="sonnet")
    assert caught.value.decision.code == "no_reading"
    assert [c["args"][c["args"].index("-p") + 1] for c in fake.calls()[-1:]] == ["/usage"]  # the real task was never sent


async def test_a_limit_report_on_a_failed_call_still_stops_the_next_one(fake, db):
    fake.set({"routes": [usage_route()],
              "responses": [{"is_error": True, "text": "limit", "rate_limits": [{"status": "rejected"}]}]})  # fmt: skip
    claude = fake.service(db)
    await claude.test()  # the structured check fails with a rejection
    with pytest.raises(ClaudeBlocked) as caught:
        await claude.ask(None, "draft", system="s", prompt="p", model="sonnet")
    assert caught.value.decision.code == "limited"


async def test_api_key_sign_in_means_no_calls_at_all(fake, db):
    fake.set({"auth": {"loggedIn": True, "authMethod": "api_key"}, "routes": [usage_route()]})
    claude = fake.service(db)
    with pytest.raises(ClaudeUnavailable):
        await claude.ask(None, "draft", system="s", prompt="p", model="sonnet")
    report = await claude.test()
    assert report["auth"]["ok"] is False and report["usage"] is None and fake.calls() == []
    assert "could cost money" in await claude.unavailable_reason()


# -- structured answers -----------------------------------------------------------------------


async def ready(fake, db, responses):
    fake.set({"routes": [usage_route()], "responses": [{"structured": {"ok": True}}, *responses]})
    claude = fake.service(db)
    await claude.test()
    return claude


async def test_structured_answer_from_the_schema(fake, db):
    claude = await ready(fake, db, [{"structured": {"title": "A", "count": 3}}])
    answer = await claude.ask_structured(None, "draft", Answer, system="s", prompt="p", model="sonnet")
    assert answer == Answer(title="A", count=3)


async def test_structured_answer_falls_back_to_json_in_the_text(fake, db):
    claude = await ready(fake, db, [{"text": 'Here you go:\n```json\n{"title": "B", "count": 1}\n```'}])
    answer = await claude.ask_structured(None, "draft", Answer, system="s", prompt="p", model="sonnet")
    assert answer == Answer(title="B", count=1)


async def test_a_wrong_shape_gets_one_retry_that_says_what_was_wrong(fake, db):
    claude = await ready(fake, db, [{"structured": {"title": "C"}}, {"structured": {"title": "C", "count": 5}}])
    before = len(fake.calls())
    answer = await claude.ask_structured(None, "draft", Answer, system="s", prompt="The task.", model="sonnet")

    assert answer.count == 5
    first, second = fake.calls()[before:]
    assert first["stdin"] == "The task."
    assert "Your previous answer was rejected: count: Field required" in second["stdin"]


async def test_two_wrong_shapes_is_an_error_not_a_loop(fake, db):
    claude = await ready(fake, db, [{"structured": {"title": "D"}}])
    before = len(fake.calls())
    with pytest.raises(ClaudeError) as caught:
        await claude.ask_structured(None, "draft", Answer, system="s", prompt="p", model="sonnet")
    assert "didn't fit the expected shape" in str(caught.value) and len(fake.calls()) - before == 2


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('Sure.\n```json\n{"a": [1, 2]}\n```\nDone.', {"a": [1, 2]}),
        ('prefix {"a": {"b": 2}} suffix', {"a": {"b": 2}}),
        ("no json here", None),
        ("{broken", None),
    ],
)
def test_extract_json(text, expected):
    assert extract_json(text) == expected


# -- what a run records -----------------------------------------------------------------------


async def test_calls_are_counted_and_shown_on_the_run(fake, db, bus):
    claude = await ready(fake, db, [{
        "text": "x", "model": "claude-sonnet-5-5",
        "usage": {"input_tokens": 200, "output_tokens": 50, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 0},
    }])  # fmt: skip
    async with run(bus, "patch", "draft", "Draft fixes") as ctx:
        await claude.ask(ctx, "readme", system="s", prompt="p", model="sonnet")

    events = db.events_for_run(ctx.run_id)
    tool = next(e for e in events if e.type == "tool.result")
    assert tool.payload["kind"] == "claude" and tool.payload["job"] == "readme" and tool.payload["ok"] is True
    assert tool.payload["input_tokens"] == 200 and tool.payload["cache_read_tokens"] == 1000
    usage = next(e for e in events if e.type == "usage").payload
    assert usage == {"claude_calls": 1, "input_tokens": 1200, "output_tokens": 50, "cache_read_tokens": 1000}


# -- going back to work by itself once a limit has reset ----------------------------------------


OVER = "Current session: 69% used · resets 9:20pm\nCurrent week (all models): 9% used · resets Oct 11, 4pm"
UNDER = "Current session: 3% used · resets 2:20am\nCurrent week (all models): 10% used · resets Oct 11, 4pm"
EVENING = datetime(2026, 10, 4, 19, 15).timestamp()


class Clock:
    def __init__(self, now: float):
        self.now = now

    def __call__(self) -> float:
        return self.now


def usage_answers(*texts: str) -> dict:
    """The stand-in answers the usage check with these texts in turn, each at no cost."""
    return {
        "routes": [{"match": "/usage", "responses": [{"text": text, "usage": ZERO_USAGE} for text in texts]}],
        "responses": [{"structured": {"title": "t", "count": 1}}],
    }


async def test_once_the_limit_has_reset_the_next_call_checks_again_for_free_and_goes_ahead(fake, db):
    clock = Clock(EVENING)
    fake.set(usage_answers(OVER, UNDER))
    claude = fake.service(db, clock=clock)

    await claude.test()  # you press the button: 69%, over the stop, so the test call is skipped
    assert (await claude.unavailable_reason()).endswith("at or above the 40% stop. It resets at 21:20.")
    with pytest.raises(ClaudeBlocked):
        await claude.ask_structured(None, "t", Answer, system="s", prompt="p", model="sonnet")
    asked = len(fake.calls())

    clock.now = datetime(2026, 10, 4, 21, 25).timestamp()  # five minutes after the reset
    # The 69% belonged to a window that has ended. Nothing says "no" any more, and nobody pressed anything.
    assert await claude.unavailable_reason() is None and len(fake.calls()) == asked

    answer = await claude.ask_structured(None, "t", Answer, system="s", prompt="p", model="sonnet")
    assert answer.count == 1
    check, call = fake.calls()[asked:]  # one free look at the usage, then the call itself
    assert "/usage" in check["args"] and call["stdin"] == "p"
    assert claude.guard.snapshot()["windows"]["session"]["percent"] == 3


async def test_when_the_reset_time_is_unknown_a_job_asks_again_after_half_an_hour(fake, db):
    clock = Clock(EVENING)
    fake.set(usage_answers("Current session: 69% used\nCurrent week (all models): 9% used", UNDER))
    claude = fake.service(db, clock=clock)

    await claude.test()
    assert claude.rechecks() is False  # the reading is fresh: asking again now would change nothing
    assert (await claude.unavailable_reason(refresh=True)).startswith("Your 5-hour usage is 69%")
    asked = len(fake.calls())

    clock.now += 31 * 60
    # A page asking is told what the last reading allows, and that the next job will look again.
    assert (await claude.unavailable_reason()).startswith("Your 5-hour usage is 69%") and claude.rechecks() is True
    assert len(fake.calls()) == asked  # asking for the page's sake started nothing

    assert await claude.unavailable_reason(refresh=True) is None  # a job asks: one free check, and it may work
    assert len(fake.calls()) == asked + 1 and "/usage" in fake.calls()[-1]["args"]
    assert claude.rechecks() is False


async def test_without_a_free_usage_check_a_no_stays_a_no(fake, db):
    """Before the connection test has shown that asking is free, nothing is asked on a job's behalf."""
    fake.set({"routes": [usage_route()], "responses": [{"structured": {"ok": True}}]})
    claude = fake.service(db)
    assert await claude.unavailable_reason(refresh=True) == "Patch can't read your plan usage, so it won't call Claude."
    assert fake.calls() == [] and claude.rechecks() is False
