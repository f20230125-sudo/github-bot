"""Run Claude through the Claude Code CLI that is already signed in on this machine.

No API key is involved. The CLI uses the Claude subscription it is signed in with, so a call
spends plan usage, never money. Three things keep it that way:

- API-key variables are removed from the environment the CLI gets.
- `claude auth status` must report a subscription sign-in before anything runs.
- If a run reports that it is using an API key, the process is killed at once.

Each call is stripped down: no tools, no project context, a short system prompt of our own.
Claude can only turn text into text.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Anything that could switch the CLI from the subscription to a paid API or another provider.
SCRUBBED_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_USE_", "AWS_BEARER_TOKEN")
SUBSCRIPTION_METHOD = "claude.ai"
# What `apiKeySource` reads when the run uses the subscription sign-in.
NO_API_KEY = (None, "none")

BASE_FLAGS = [
    "--safe-mode",  # no CLAUDE.md, hooks, plugins, skills or MCP servers
    "--tools", "",  # no tools at all: text in, text out
    "--strict-mcp-config",
    "--disable-slash-commands",
    "--no-session-persistence",
    "--output-format", "stream-json",
    "--verbose",
]  # fmt: skip


class ClaudeError(Exception):
    """A call ran and failed. `result` holds whatever came back, including any rate-limit reports."""

    def __init__(self, message: str, result: ClaudeResult | None = None):
        super().__init__(message)
        self.result = result


class ClaudeUnavailable(ClaudeError):
    """Claude can't be used at all right now: not installed, not signed in, or it would cost money."""


@dataclass(frozen=True)
class AuthStatus:
    installed: bool
    logged_in: bool = False
    method: str | None = None
    plan: str | None = None
    problem: str | None = None

    @property
    def ok(self) -> bool:
        return self.problem is None


@dataclass
class ClaudeResult:
    text: str = ""
    structured: Any = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    duration_ms: int = 0
    api_key_source: str | None = None
    rate_limits: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens


def clean_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """The environment handed to the CLI, with every paid-API switch removed."""
    source = dict(os.environ if env is None else env)
    return {k: v for k, v in source.items() if not k.upper().startswith(SCRUBBED_PREFIXES)}


def find_command() -> list[str] | None:
    path = shutil.which("claude")
    return [path] if path else None


class _Process:
    """One CLI process, read line by line so it can be stopped the moment something is wrong."""

    def __init__(self, args: list[str], cwd: Path, env: dict[str, str]):
        self._args, self._cwd, self._env = args, cwd, env
        self._proc: subprocess.Popen[str] | None = None
        self._stopped = False
        self.timed_out = False

    def kill(self) -> None:
        self._stopped = True  # if the process hasn't started yet, run() stops it as soon as it has
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            if sys.platform == "win32":
                # `claude` can be a .cmd shim around another program. Stop the whole tree, not the shim.
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            proc.kill()
        except (OSError, subprocess.SubprocessError):
            pass

    def _on_timeout(self) -> None:
        self.timed_out = True
        self.kill()

    def run(self, stdin_text: str, timeout: float, on_line: Callable[[str], bool]) -> tuple[int, str]:
        """Returns (exit code, stderr). `on_line` returns False to stop the process."""
        self._proc = subprocess.Popen(
            self._args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self._cwd,
            env=self._env,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        proc = self._proc
        if self._stopped:  # cancelled in the moment between asking for the process and it starting
            self.kill()
        stderr: list[str] = []

        def feed() -> None:
            try:
                proc.stdin.write(stdin_text)
                proc.stdin.close()
            except OSError:
                pass  # the process ended before reading everything

        threading.Thread(target=feed, daemon=True).start()
        err_reader = threading.Thread(target=lambda: stderr.append(proc.stderr.read()), daemon=True)
        err_reader.start()
        timer = threading.Timer(timeout, self._on_timeout)
        timer.start()
        try:
            for line in proc.stdout:
                if on_line(line) is False:
                    self.kill()
                    break
            code = proc.wait()
        finally:
            timer.cancel()
        err_reader.join(2)
        return code, "".join(stderr)


class ClaudeCli:
    def __init__(self, command: list[str] | None, cwd: Path, timeout: float = 180.0):
        self._command = command if command is not None else find_command()
        self._cwd = cwd
        self._timeout = timeout

    @property
    def installed(self) -> bool:
        return bool(self._command)

    def _workdir(self) -> Path:
        # An empty folder of our own, so no project instructions or settings are picked up.
        self._cwd.mkdir(parents=True, exist_ok=True)
        return self._cwd

    async def auth_status(self) -> AuthStatus:
        if not self._command:
            return AuthStatus(installed=False, problem="Claude Code isn't installed on this machine.")
        try:
            done = await asyncio.to_thread(
                subprocess.run,
                [*self._command, "auth", "status"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=self._workdir(),
                env=clean_env(),
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            data = json.loads(done.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return AuthStatus(installed=True, problem="Couldn't read Claude Code's sign-in status.")

        logged_in = bool(data.get("loggedIn"))
        method = data.get("authMethod")
        plan = data.get("subscriptionType")
        if not logged_in:
            problem = "Claude Code isn't signed in. Run `claude` in a terminal and sign in."
        elif method != SUBSCRIPTION_METHOD:
            problem = (
                f"Claude Code is signed in with {method or 'something other than a Claude plan'}, "
                "which could cost money. Patch won't use it."
            )
        else:
            problem = None
        return AuthStatus(installed=True, logged_in=logged_in, method=method, plan=plan, problem=problem)

    def build_args(
        self,
        *,
        system: str,
        model: str,
        effort: str | None = None,
        schema: dict[str, Any] | None = None,
        instruction: str = "Do the task described in the input.",
        slash_commands: bool = False,
    ) -> list[str]:
        if not self._command:
            raise ClaudeUnavailable("Claude Code isn't installed on this machine.")
        # Built-in commands such as /usage only run when slash commands are left on.
        flags = [f for f in BASE_FLAGS if not (slash_commands and f == "--disable-slash-commands")]
        args = [*self._command, "-p", instruction, *flags, "--system-prompt", system, "--model", model]
        if effort:
            args += ["--effort", effort]
        if schema is not None:
            args += ["--json-schema", json.dumps(schema, separators=(",", ":"))]
        return args

    async def ask(
        self,
        *,
        system: str,
        prompt: str,
        model: str,
        effort: str | None = None,
        schema: dict[str, Any] | None = None,
        instruction: str = "Do the task described in the input.",
        slash_commands: bool = False,
        on_text: Callable[[str], None] | None = None,
    ) -> ClaudeResult:
        """One stripped-down call. The task goes in on stdin, so its length isn't limited by the command line."""
        args = self.build_args(
            system=system, model=model, effort=effort, schema=schema, instruction=instruction,
            slash_commands=slash_commands,
        )  # fmt: skip
        result = ClaudeResult()
        state: dict[str, Any] = {"final": None, "paid": None}

        def on_line(line: str) -> bool:
            line = line.strip()
            if not line:
                return True
            try:
                message = json.loads(line)
            except ValueError:
                return True  # not ours to parse: a stray log line
            kind = message.get("type")
            if kind == "system" and message.get("subtype") == "init":
                result.api_key_source = message.get("apiKeySource")
                result.model = message.get("model")
                if result.api_key_source not in NO_API_KEY:
                    state["paid"] = result.api_key_source
                    return False  # stop before this can bill anything
            elif kind == "rate_limit_event":
                info = message.get("rate_limit_info")
                if isinstance(info, dict):
                    result.rate_limits.append(info)
            elif kind == "assistant" and on_text:
                for block in (message.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                        on_text(block["text"])
            elif kind == "result":
                state["final"] = message
            return True

        process = _Process(args, self._workdir(), clean_env())
        started = time.perf_counter()
        try:
            code, stderr = await asyncio.to_thread(process.run, prompt, self._timeout, on_line)
        except asyncio.CancelledError:
            process.kill()
            raise
        except OSError as exc:
            raise ClaudeUnavailable(f"Couldn't start Claude Code ({exc.__class__.__name__}).") from exc
        result.duration_ms = int((time.perf_counter() - started) * 1000)

        if state["paid"]:
            raise ClaudeUnavailable(
                f"Claude Code tried to use an API key ({state['paid']}), which could cost money. Patch stopped it.",
                result,
            )
        if process.timed_out:
            raise ClaudeError(f"Claude took longer than {int(self._timeout)} seconds. Stopped.", result)

        final = state["final"]
        if final is None:
            detail = stderr.strip().splitlines()[-1] if stderr.strip() else f"exit code {code}"
            raise ClaudeError(f"Claude Code ended without a result: {detail}", result)

        usage = final.get("usage") or {}
        result.text = final.get("result") or ""
        result.structured = final.get("structured_output")
        result.input_tokens = int(usage.get("input_tokens") or 0)
        result.output_tokens = int(usage.get("output_tokens") or 0)
        result.cache_read_tokens = int(usage.get("cache_read_input_tokens") or 0)
        result.cache_write_tokens = int(usage.get("cache_creation_input_tokens") or 0)
        if final.get("is_error"):
            raise ClaudeError(f"Claude Code reported an error: {result.text[:300] or 'no detail given'}", result)
        return result
