"""An agent's access to Claude: check the sign-in, check the usage stop, then make the call."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .claude_cli import AuthStatus, ClaudeCli, ClaudeError, ClaudeResult, ClaudeUnavailable
from .runs import RunContext
from .usage_guard import Decision, UsageGuard

Shape = TypeVar("Shape", bound=BaseModel)

PROBE_SYSTEM = "Reply with the single word OK."
AUTH_TTL_SECONDS = 600.0
DESK = "desk"  # calls made for no agent in particular are counted under this name


class ClaudeBlocked(ClaudeError):
    """The usage stop said no. Nothing was called."""

    def __init__(self, decision: Decision):
        super().__init__(decision.reason)
        self.decision = decision


def extract_json(text: str) -> Any:
    """Pull a JSON object out of a text answer, with or without a code fence around it."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except ValueError:
        return None


class Claude:
    def __init__(self, cli: ClaudeCli, guard: UsageGuard, small_model: str = "haiku"):
        self._cli = cli
        self.guard = guard
        self._small_model = small_model
        self._lock = asyncio.Lock()  # one call at a time: usage stays easy to follow
        self._auth: tuple[float, AuthStatus] | None = None

    @property
    def installed(self) -> bool:
        return self._cli.installed

    async def auth(self, refresh: bool = False) -> AuthStatus:
        if not refresh and self._auth and time.monotonic() - self._auth[0] < AUTH_TTL_SECONDS:
            return self._auth[1]
        status = await self._cli.auth_status()
        self._auth = (time.monotonic(), status)
        return status

    async def unavailable_reason(self) -> str | None:
        """Why Claude can't be used right now, or None if it can. Makes no model call."""
        auth = await self.auth()
        if not auth.ok:
            return auth.problem
        decision = self.guard.decide()
        if decision.allowed or decision.code == "stale":  # a stale reading gets refreshed before the next call
            return None
        return decision.reason

    # -- the usage stop -----------------------------------------------------------------------

    async def _probe(self) -> ClaudeResult:
        """Ask Claude Code for the plan usage. Costs nothing when its /usage command answers by itself."""
        result = await self._cli.ask(
            system=PROBE_SYSTEM, prompt="", model=self._small_model, instruction="/usage", slash_commands=True
        )
        self.guard.record_events(result.rate_limits)
        local = result.total_tokens == 0
        parsed = self.guard.record_usage_text(result.text) if local else False
        self.guard.set_usage_command("local" if local and parsed else "model")
        if not local:
            self.guard.note_call()  # the model answered instead: a tiny call, but a call
            await self._record(None, "usage check", result)
        return result

    async def _ensure_allowed(self) -> Decision:
        decision = self.guard.decide()
        free_probe = self.guard.usage_command == "local"
        old = self.guard.seconds_since_reading() > self.guard.fresh_seconds
        # Refresh a reading that was fine but has aged. When /usage is free, also re-check an
        # old "over the limit" or missing reading, since asking costs nothing.
        if decision.code == "stale" or (free_probe and old and decision.code in ("no_reading", "over_limit")):
            try:
                await self._probe()
            except ClaudeError as exc:
                if exc.result:
                    self.guard.record_events(exc.result.rate_limits)
            decision = self.guard.decide()
            if decision.code == "stale":
                decision = Decision(
                    False, "no_reading", "Claude Code gave no usage percentage this time, so Patch won't call it."
                )
        return decision

    # -- calls --------------------------------------------------------------------------------

    async def ask(
        self,
        ctx: RunContext | None,
        job: str,
        *,
        system: str,
        prompt: str,
        model: str,
        effort: str | None = None,
        schema: dict[str, Any] | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> ClaudeResult:
        auth = await self.auth()
        if not auth.ok:
            raise ClaudeUnavailable(auth.problem or "Claude is unavailable.")
        async with self._lock:
            decision = await self._ensure_allowed()
            if not decision.allowed:
                raise ClaudeBlocked(decision)
            try:
                result = await self._cli.ask(
                    system=system, prompt=prompt, model=model, effort=effort, schema=schema, on_text=on_text
                )
            except ClaudeError as exc:
                if exc.result:
                    self.guard.record_events(exc.result.rate_limits)
                self.guard.note_call()
                await self._record(ctx, job, exc.result or ClaudeResult(), error=str(exc))
                raise
            self.guard.record_events(result.rate_limits)
            self.guard.note_call()
            await self._record(ctx, job, result)
            return result

    async def ask_structured(
        self,
        ctx: RunContext | None,
        job: str,
        shape: type[Shape],
        *,
        system: str,
        prompt: str,
        model: str,
        effort: str | None = None,
    ) -> Shape:
        """A call whose answer must fit a Pydantic model. One retry if it doesn't."""
        schema = shape.model_json_schema()
        problem = ""
        for _attempt in range(2):
            text = prompt if not problem else f"{prompt}\n\nYour previous answer was rejected: {problem}\nAnswer again."
            result = await self.ask(ctx, job, system=system, prompt=text, model=model, effort=effort, schema=schema)
            data = result.structured if result.structured is not None else extract_json(result.text)
            try:
                return shape.model_validate(data)
            except ValidationError as exc:
                first = exc.errors()[0]
                problem = f"{'.'.join(str(p) for p in first['loc']) or 'answer'}: {first['msg']}"
        raise ClaudeError(f"Claude's answer didn't fit the expected shape ({problem}).")

    async def _record(self, ctx: RunContext | None, job: str, result: ClaudeResult, error: str | None = None) -> None:
        """Count a call: on its run, and in the day's totals by job for the metrics page."""
        tokens_in = result.input_tokens + result.cache_read_tokens + result.cache_write_tokens
        by_job = {f"calls.{job}": 1, f"tokens_in.{job}": tokens_in, f"tokens_out.{job}": result.output_tokens}
        if ctx is None:
            # A call outside any run (the connection test). No run will add it to the totals, so do it here.
            totals = {"claude_calls": 1, "input_tokens": tokens_in, "output_tokens": result.output_tokens}
            self.guard.db.bump_stats(DESK, by_job | totals)
            return
        self.guard.db.bump_stats(ctx.agent, by_job)
        ctx.counters["claude_calls"] += 1
        ctx.counters["input_tokens"] += tokens_in
        ctx.counters["output_tokens"] += result.output_tokens
        ctx.counters["cache_read_tokens"] += result.cache_read_tokens
        await ctx.tool(
            "claude", job=job, model=result.model, ok=error is None, error=error, ms=result.duration_ms,
            input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            cache_read_tokens=result.cache_read_tokens, cache_write_tokens=result.cache_write_tokens,
        )  # fmt: skip

    # -- the Setup page -----------------------------------------------------------------------

    async def status(self) -> dict[str, Any]:
        """What the Setup page shows. No model call."""
        auth = await self.auth()
        return {"auth": _auth_view(auth), "guard": self.guard.snapshot()}

    async def test(self) -> dict[str, Any]:
        """The connection test. You start it; it makes at most two tiny calls on the small model."""
        auth = await self.auth(refresh=True)
        report: dict[str, Any] = {"auth": _auth_view(auth), "usage": None, "structured": None}
        if not auth.ok:
            return report | {"guard": self.guard.snapshot()}

        async with self._lock:
            try:
                probe = await self._probe()
                report["usage"] = {
                    "ok": True,
                    "answered_locally": probe.total_tokens == 0,
                    "text": probe.text[:800],
                    "tokens": probe.total_tokens,
                    "reports": probe.rate_limits,
                }
            except ClaudeError as exc:
                if exc.result:
                    self.guard.record_events(exc.result.rate_limits)
                report["usage"] = {"ok": False, "error": str(exc)}

            decision = self.guard.decide()
            if not decision.allowed:
                report["structured"] = {"ok": False, "skipped": True, "error": decision.reason}
            else:
                report["structured"] = await self._test_structured()
        return report | {"guard": self.guard.snapshot()}

    async def _test_structured(self) -> dict[str, Any]:
        class Check(BaseModel):
            ok: bool

        try:
            result = await self._cli.ask(
                system="You answer with JSON only.",
                prompt='Answer with {"ok": true}.',
                model=self._small_model,
                schema=Check.model_json_schema(),
            )
        except ClaudeError as exc:
            if exc.result:
                self.guard.record_events(exc.result.rate_limits)
            self.guard.note_call()
            await self._record(None, "connection test", exc.result or ClaudeResult())
            return {"ok": False, "error": str(exc)}
        self.guard.record_events(result.rate_limits)
        self.guard.note_call()
        await self._record(None, "connection test", result)
        via = "schema" if result.structured is not None else "text" if extract_json(result.text) else None
        return {
            "ok": via is not None,
            "via": via,  # "schema": the CLI enforced the shape; "text": parsed from the reply
            "model": result.model,
            "tokens": result.total_tokens,
            "input_tokens": result.input_tokens + result.cache_read_tokens + result.cache_write_tokens,
            "output_tokens": result.output_tokens,
            "ms": result.duration_ms,
        }


def _auth_view(auth: AuthStatus) -> dict[str, Any]:
    return {
        "installed": auth.installed,
        "signed_in": auth.logged_in,
        "method": auth.method,
        "plan": auth.plan,
        "ok": auth.ok,
        "problem": auth.problem,
    }
