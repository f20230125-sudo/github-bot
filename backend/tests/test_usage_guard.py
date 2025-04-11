from datetime import datetime

import pytest

from app.core.usage_guard import UsageGuard, parse_reset, parse_usage, parse_usage_text

NOW = 1_800_000_000.0


class Clock:
    def __init__(self, now: float = NOW):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def guard(db, clock):
    return UsageGuard(db, session_limit=40, weekly_limit=40, clock=clock)


def event(utilization=None, window="five_hour", status="allowed", resets_in=3600, now=NOW):
    info = {"status": status, "rateLimitType": window, "resetsAt": now + resets_in}
    if utilization is not None:
        info["utilization"] = utilization
    return info


# -- failing closed ---------------------------------------------------------------------------


def test_no_reading_means_no_calls(guard):
    decision = guard.decide()
    assert decision.allowed is False and decision.code == "no_reading"


def test_a_report_without_a_percentage_is_not_a_reading(guard):
    guard.record_events([event(utilization=None)])
    assert guard.decide().code == "no_reading"


# -- the 40% stop -----------------------------------------------------------------------------


def test_under_the_limit_is_allowed(guard):
    guard.record_events([event(0.12)])
    decision = guard.decide()
    assert decision.allowed and decision.code == "ok"
    assert decision.reason == "5-hour usage is 12%, under the 40% stop."


@pytest.mark.parametrize("utilization", [0.40, 0.41, 0.97])
def test_at_or_above_the_limit_is_blocked(guard, utilization):
    guard.record_events([event(utilization)])
    decision = guard.decide()
    assert decision.allowed is False and decision.code == "over_limit"
    assert "at or above the 40% stop" in decision.reason


def test_just_under_the_limit_is_allowed(guard):
    guard.record_events([event(0.399)])
    assert guard.decide().allowed


def test_weekly_limit_blocks_even_when_the_session_is_low(guard):
    guard.record_events([event(0.05), event(0.55, window="seven_day", resets_in=86400)])
    decision = guard.decide()
    assert decision.code == "over_limit" and decision.reason.startswith("Your weekly usage is 55%")


def test_limits_are_separate_settings(db, clock):
    guard = UsageGuard(db, session_limit=20, weekly_limit=80, clock=clock)
    guard.record_events([event(0.25)])
    assert guard.decide().code == "over_limit"
    guard.record_events([event(0.10), event(0.70, window="seven_day")])
    assert guard.decide().allowed


def test_block_lifts_when_the_window_resets_but_a_fresh_reading_is_needed(guard, clock):
    guard.record_events([event(0.60, resets_in=3600)])
    assert guard.decide().code == "over_limit"
    clock.now += 3601
    assert guard.decide().code == "no_reading"  # the old figure no longer applies; nothing new is known


def test_an_old_reading_must_be_refreshed(guard, clock):
    guard.record_events([event(0.10, resets_in=4 * 3600)])
    clock.now += 1801
    decision = guard.decide()
    assert decision.allowed is False and decision.code == "stale"


def test_a_warning_or_rejection_from_claude_code_stops_everything(guard, clock):
    guard.record_events([event(0.10, status="allowed_warning", resets_in=1800)])
    assert guard.decide().code == "limited"
    clock.now += 1801
    assert guard.decide().code != "limited"

    guard.record_events([{"status": "rejected"}])  # no reset time given: wait a full 5-hour window
    assert guard.decide().code == "limited"
    clock.now += 5 * 3600 - 10
    assert guard.decide().code == "limited"


# -- reading the numbers ----------------------------------------------------------------------


def test_utilization_is_a_fraction_unless_a_value_above_one_shows_the_scale_is_percent(guard):
    guard.record_events([event(0.5)])
    assert guard.snapshot()["windows"]["session"]["percent"] == 50
    guard.record_events([event(37)])  # only possible if the scale is 0-100
    assert guard.snapshot()["windows"]["session"]["percent"] == 37
    guard.record_events([event(0.5)])  # now known to be percent: half a percent
    assert guard.snapshot()["windows"]["session"]["percent"] == 0.5


def test_a_report_without_a_figure_keeps_the_figure_only_within_the_same_window(guard, clock):
    guard.record_events([event(0.30, resets_in=3600)])
    guard.record_events([event(None, resets_in=3600)])  # same window: the 30% still holds
    assert guard.snapshot()["windows"]["session"]["percent"] == 30

    clock.now += 4000
    guard.record_events([event(None, resets_in=5 * 3600, now=clock.now)])  # a new window: unknown again
    assert guard.snapshot()["windows"]["session"]["percent"] is None


def test_millisecond_reset_times_are_understood(guard):
    guard.record_events([{"status": "allowed", "rateLimitType": "five_hour", "utilization": 0.5, "resetsAt": (NOW + 600) * 1000}])
    assert guard.snapshot()["windows"]["session"]["resets_at"] == NOW + 600


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Current session\n12% used\nResets 3:59pm\n\nCurrent week (all models)\n30% used", {"session": 12, "weekly": 30}),
        ("Current session: 8% used · resets 4pm\nCurrent week (all models): 21.5% used", {"session": 8, "weekly": 21.5}),
        ("5-hour limit: 70% remaining\nWeekly limit: 55% remaining", {"session": 30, "weekly": 45}),
        ("Current week (all models): 20% used\nCurrent week (Opus): 64% used", {"weekly": 64}),
        ("Session\n████░░░░ \n", {}),
        ("OK", {}),
        ("", {}),
    ],
)
def test_usage_text_parsing(text, expected):
    assert parse_usage_text(text) == expected


# What Claude Code really printed on 4 October 2026, when the 5-hour limit was at 69%.
REAL_USAGE = """You are currently using your subscription to power your Claude Code usage

Current session: 69% used · resets Oct 4, 9:20pm (Asia/Dubai)
Current week (all models): 9% used · resets Oct 11, 4pm (Asia/Dubai)

What's contributing to your limits usage?
Approximate, based on local sessions on this machine — does not include other
devices or claude.ai. Behaviors are independent characteristics, not a breakdown.

Last 24h · 433 requests · 2 sessions
  96% of your usage was at >150k context
  91% of your usage came from sessions active for 8+ hours
  Top MCP servers: claude-in-chrome 20%

Last 7d · 1346 requests · 7 sessions
  95% of your usage was at >150k context
  82% of your usage came from sessions active for 8+ hours
  Top skills: /dataviz 10%, /claude-api 9%, /artifact-design 4%
"""


def local(now: float, **fields) -> float:
    """A moment on this machine's clock: the day of `now`, with the given fields replaced."""
    return datetime.fromtimestamp(now).replace(second=0, microsecond=0, **fields).timestamp()


def test_the_real_usage_text_gives_both_figures_and_both_reset_times():
    now = datetime(2026, 10, 4, 19, 15).timestamp()
    found = parse_usage(REAL_USAGE, now)

    # The breakdown below ("96% of your usage ...", "Last 7d ...") is not mistaken for a limit.
    assert found == {
        "session": {"percent": 69, "resets_at": datetime(2026, 10, 4, 21, 20).timestamp()},
        "weekly": {"percent": 9, "resets_at": datetime(2026, 10, 11, 16, 0).timestamp()},
    }
    assert parse_usage_text(REAL_USAGE) == {"session": 69, "weekly": 9}
    # The same holds if the breakdown's heading names a week and nothing else.
    assert parse_usage_text(REAL_USAGE.replace(" · 7 sessions", "")) == {"session": 69, "weekly": 9}


@pytest.mark.parametrize(
    ("when", "fields", "days_later"),
    [
        ("9:20pm", {"hour": 21, "minute": 20}, 0),
        ("4pm", {"hour": 16, "minute": 0}, 0),
        ("12am", {"hour": 0, "minute": 0}, 1),  # midnight has passed today, so it means tomorrow
        ("11:05", {"hour": 11, "minute": 5}, 1),  # a time of day that has passed means tomorrow
        ("12:30pm", {"hour": 12, "minute": 30}, 0),
    ],
)
def test_a_time_of_day_is_the_next_time_the_clock_shows_it(when, fields, days_later):
    now = datetime(2026, 10, 4, 12, 0).timestamp()
    assert parse_reset(when, None, now) == local(now, **fields) + days_later * 86400


def test_a_date_without_a_year_is_the_next_such_date():
    december = datetime(2026, 12, 30, 10, 0).timestamp()
    assert parse_reset("Jan 4, 4pm", "Not/AZone", december) == datetime(2027, 1, 4, 16, 0).timestamp()
    assert parse_reset("Dec 30, 9am", None, december) == datetime(2026, 12, 30, 9, 0).timestamp()  # just passed


@pytest.mark.parametrize("when", ["Oct 7", "soon", "", "25:00", "13pm", "Feb 30, 4pm", "Foo 3, 4pm"])
def test_a_reset_time_that_cannot_be_read_is_left_unknown(when):
    assert parse_reset(when, None, NOW) is None


def test_a_reading_from_the_usage_text_runs_out_when_its_window_resets(db, clock):
    clock.now = datetime(2026, 10, 4, 19, 15).timestamp()
    guard = UsageGuard(db, clock=clock)
    assert guard.record_usage_text(REAL_USAGE) is True

    decision = guard.decide()
    assert decision.code == "over_limit" and decision.reason.startswith("Your 5-hour usage is 69%")
    assert "It resets at " in decision.reason
    assert guard.snapshot()["windows"]["session"]["resets_at"] == datetime(2026, 10, 4, 21, 20).timestamp()

    clock.now = datetime(2026, 10, 4, 21, 21).timestamp()  # a minute after the reset
    after = guard.snapshot()
    assert after["windows"]["session"]["percent"] is None  # the 69% belonged to the window that ended
    assert after["windows"]["weekly"]["percent"] == 9  # the week has not reset


def test_usage_text_becomes_readings(guard):
    assert guard.record_usage_text("Current session\n12% used\n\nCurrent week (all models)\n44% used") is True
    snapshot = guard.snapshot()
    assert snapshot["windows"]["session"]["percent"] == 12 and snapshot["windows"]["weekly"]["percent"] == 44
    assert snapshot["windows"]["weekly"]["source"] == "usage command"
    assert guard.decide().code == "over_limit"
    assert guard.record_usage_text("nothing useful") is False


# -- the fixed allowance ----------------------------------------------------------------------


def test_fixed_allowance_only_applies_when_nothing_can_be_read(db, clock):
    guard = UsageGuard(db, fixed_allowance=2, clock=clock)
    assert guard.decide().code == "allowance"
    guard.note_call()
    guard.note_call()
    assert guard.decide().code == "allowance_spent"
    clock.now += 5 * 3600 + 1
    assert guard.decide().code == "allowance"  # the calls aged out

    guard.record_events([event(0.90, now=clock.now)])
    assert guard.decide().code == "over_limit"  # a real reading always wins over the allowance


def test_state_survives_a_restart(db, clock):
    UsageGuard(db, clock=clock).record_events([event(0.70)])
    assert UsageGuard(db, clock=clock).decide().code == "over_limit"


def test_snapshot_describes_the_guard(guard):
    guard.record_events([event(0.12)])
    snapshot = guard.snapshot()
    assert snapshot["allowed"] is True and snapshot["limits"] == {"session": 40, "weekly": 40}
    assert snapshot["windows"]["session"] == {"percent": 12, "resets_at": NOW + 3600, "minutes_old": 0, "source": "call"}
    assert snapshot["windows"]["weekly"] == {"percent": None, "resets_at": None, "minutes_old": None, "source": None}
