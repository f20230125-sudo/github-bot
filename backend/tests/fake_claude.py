"""A stand-in for the `claude` program. Tests run this instead of the real CLI, so nothing is ever
sent to Claude and no plan usage is spent.

Invoked as:  python fake_claude.py <scenario.json> <the arguments the real CLI would get>

The scenario decides what it answers. Every call is appended to <scenario.json>.calls.jsonl with
its arguments, its stdin and any environment variables that could switch on a paid API.
"""

import json
import os
import sys
import time

scenario_path = sys.argv[1]
args = sys.argv[2:]
with open(scenario_path, encoding="utf-8") as handle:
    scenario = json.load(handle)

if args[:2] == ["auth", "status"]:
    print(json.dumps(scenario.get("auth", {"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "pro"})))
    sys.exit(0)

stdin = sys.stdin.read()
log_path = scenario_path + ".calls.jsonl"

instruction = args[args.index("-p") + 1] if "-p" in args else ""
risky_env = {k: v for k, v in os.environ.items() if k.upper().startswith(("ANTHROPIC_", "CLAUDE_CODE_USE_"))}
with open(log_path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps({"args": args, "stdin": stdin, "env": risky_env, "cwd": os.getcwd()}) + "\n")


def take(answers: list, name: str) -> dict:
    """The next answer from a list, repeating the last one. Each call is a new process, so the
    position is kept in a file."""
    count_path = f"{scenario_path}.{name}.count"
    count = 0
    if os.path.exists(count_path):
        with open(count_path, encoding="utf-8") as counter:
            count = int(counter.read())
    with open(count_path, "w", encoding="utf-8") as counter:
        counter.write(str(count + 1))
    return answers[min(count, len(answers) - 1)]


# A route answers calls whose instruction or stdin contains its `match` text. Every other call
# takes the next of `responses`.
spec = None
for index, route in enumerate(scenario.get("routes", [])):
    if route["match"] in instruction or route["match"] in stdin:
        spec = take(route["responses"] if "responses" in route else [route.get("response", {})], f"route{index}")
        break
if spec is None:
    spec = take(scenario.get("responses", [{}]), "sequence")


def emit(message: dict) -> None:
    print(json.dumps(message), flush=True)


emit(
    {
        "type": "system",
        "subtype": "init",
        "apiKeySource": spec.get("api_key_source", "none"),
        "model": spec.get("model", "claude-haiku-4-5"),
        "tools": [],
    }
)
if spec.get("hang"):
    time.sleep(spec["hang"])
for info in spec.get("rate_limits", []):
    emit({"type": "rate_limit_event", "rate_limit_info": info})
if spec.get("no_result"):
    sys.stderr.write("fake failure\n")
    sys.exit(spec.get("exit", 1))

text = spec.get("text", "")
if text:
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})
emit(
    {
        "type": "result",
        "subtype": "success",
        "is_error": spec.get("is_error", False),
        "result": text,
        "structured_output": spec.get("structured"),
        "usage": spec.get(
            "usage",
            {"input_tokens": 900, "output_tokens": 120, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        ),
        "duration_ms": 1200,
        "num_turns": 1,
    }
)
sys.exit(spec.get("exit", 0))
