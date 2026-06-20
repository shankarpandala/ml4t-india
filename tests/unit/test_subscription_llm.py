"""Tests for SubscriptionLLMClient -- the key-free claude-CLI LLM adapter.

Every test mocks ``subprocess.run``: NO real ``claude`` call, NO API key,
NO subscription quota is consumed. We assert the CLI-envelope -> LLMResponse
mapping, that both ``ANTHROPIC_API_KEY`` and ``ANTHROPIC_AUTH_TOKEN`` are
stripped from the subprocess env, the
system/user message split, and the malformed-output re-prompt-then-raise
path mandated by the upstream ``LLMClient`` Protocol.

The module is skipped when the optional ``agent`` extra (ml4t-agent) is
absent, mirroring ``test_workflows_agent.py``.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

pytest.importorskip("ml4t.agent")

from ml4t.agent.llm import LLMResponse, Message  # noqa: E402
from ml4t.agent.llm.base import LLMClient  # noqa: E402

from ml4t.india.workflows.subscription_llm import (  # noqa: E402
    SubscriptionLLMClient,
    SubscriptionLLMError,
)

# The proposals schema the agent's one call site requires: {"proposals": [...]}.
PROPOSALS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"proposals": {"type": "array"}},
    "required": ["proposals"],
    "additionalProperties": True,
}


def _envelope(
    structured: Any,
    *,
    result: str = "ok",
    usage: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    env: dict[str, Any] = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "api_error_status": None,
        "result": result,
    }
    if structured is not None:
        env["structured_output"] = structured
    if usage is not None:
        env["usage"] = usage
    env.update(extra)
    return env


def _completed(
    envelope: dict[str, Any], *, returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["claude"],
        returncode=returncode,
        stdout=json.dumps(envelope),
        stderr=stderr,
    )


class _Runner:
    """Records each ``subprocess.run`` call and replays canned responses."""

    def __init__(self, *responses: Any) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> Any:
        self.calls.append((cmd, kwargs))
        return self._responses.pop(0)


def test_satisfies_llmclient_protocol() -> None:
    assert isinstance(SubscriptionLLMClient(), LLMClient)


def test_envelope_maps_to_llmresponse(monkeypatch: pytest.MonkeyPatch) -> None:
    structured = {"proposals": [{"experiment_id": "e1"}]}
    usage = {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_input_tokens": 3,
        # nested / non-int entries must be dropped (usage is Mapping[str, int]).
        "server_tool_use": {"web_search_requests": 0},
        "service_tier": "standard",
    }
    runner = _Runner(_completed(_envelope(structured, result="done", usage=usage)))
    monkeypatch.setattr(subprocess, "run", runner)

    resp = SubscriptionLLMClient().complete_with_schema(
        [Message(role="user", content="hi")], PROPOSALS_SCHEMA
    )

    assert isinstance(resp, LLMResponse)
    assert resp.data == structured
    assert resp.raw == "done"
    assert resp.usage == {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 3}
    assert len(runner.calls) == 1


def test_result_as_string_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``structured_output``: parse the JSON object out of ``result``."""
    env = _envelope(None, result=json.dumps({"proposals": []}))
    runner = _Runner(_completed(env))
    monkeypatch.setattr(subprocess, "run", runner)

    resp = SubscriptionLLMClient().complete_with_schema(
        [Message(role="user", content="hi")], PROPOSALS_SCHEMA
    )
    assert resp.data == {"proposals": []}


def test_strips_api_key_and_splits_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-should-be-stripped")
    # Claude Code honors ANTHROPIC_AUTH_TOKEN as an alternate bearer
    # credential, so it must be stripped too.
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token-should-be-stripped")
    runner = _Runner(_completed(_envelope({"proposals": []})))
    monkeypatch.setattr(subprocess, "run", runner)

    messages = [
        Message(role="system", content="SYS-A"),
        Message(role="system", content="SYS-B"),
        Message(role="user", content="USER-Q"),
    ]
    SubscriptionLLMClient(model="claude-opus-4-8").complete_with_schema(
        messages, PROPOSALS_SCHEMA
    )

    cmd, kwargs = runner.calls[0]
    # BOTH credential env vars stripped from the subprocess environment.
    assert "ANTHROPIC_API_KEY" not in kwargs["env"]
    assert "ANTHROPIC_AUTH_TOKEN" not in kwargs["env"]
    # System messages joined into --append-system-prompt.
    assert cmd[cmd.index("--append-system-prompt") + 1] == "SYS-A\n\nSYS-B"
    # User content arrives on stdin.
    assert kwargs["input"] == "USER-Q"
    # Subscription-path flags present.
    assert cmd[cmd.index("--model") + 1] == "claude-opus-4-8"
    assert cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions"
    assert cmd[cmd.index("--json-schema") + 1] == json.dumps(PROPOSALS_SCHEMA)
    # Hard tool denylist: bypass mode can never run exec/write/network tools.
    denied = cmd[cmd.index("--disallowedTools") + 1].split(",")
    assert {"Bash", "Write", "Edit", "WebFetch"} <= set(denied)


def test_no_system_message_omits_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _Runner(_completed(_envelope({"proposals": []})))
    monkeypatch.setattr(subprocess, "run", runner)

    SubscriptionLLMClient().complete_with_schema(
        [Message(role="user", content="only-user")], PROPOSALS_SCHEMA
    )
    cmd, _ = runner.calls[0]
    assert "--append-system-prompt" not in cmd


def test_malformed_output_reprompts_once_then_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = {"not_proposals": True}  # missing required "proposals"
    runner = _Runner(_completed(_envelope(bad)), _completed(_envelope(bad)))
    monkeypatch.setattr(subprocess, "run", runner)

    with pytest.raises(SubscriptionLLMError, match="schema validation after one re-prompt"):
        SubscriptionLLMClient().complete_with_schema(
            [Message(role="user", content="hi")], PROPOSALS_SCHEMA
        )
    # Exactly one re-prompt: two CLI calls total.
    assert len(runner.calls) == 2
    # The validator error was fed back into the second prompt.
    assert "failed JSON-schema validation" in runner.calls[1][1]["input"]


def test_reprompt_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _Runner(
        _completed(_envelope({"nope": 1})),
        _completed(_envelope({"proposals": []})),
    )
    monkeypatch.setattr(subprocess, "run", runner)

    resp = SubscriptionLLMClient().complete_with_schema(
        [Message(role="user", content="hi")], PROPOSALS_SCHEMA
    )
    assert resp.data == {"proposals": []}
    assert len(runner.calls) == 2


def test_nonzero_exit_raises_with_login_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _Runner(
        subprocess.CompletedProcess(
            args=["claude"], returncode=1, stdout="", stderr="not logged in"
        )
    )
    monkeypatch.setattr(subprocess, "run", runner)

    with pytest.raises(SubscriptionLLMError, match="/login"):
        SubscriptionLLMClient().complete_with_schema(
            [Message(role="user", content="hi")], PROPOSALS_SCHEMA
        )


def test_error_envelope_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    env = {
        "type": "result",
        "subtype": "error_during_execution",
        "is_error": True,
        "api_error_status": "overloaded",
        "result": "Error: overloaded",
    }
    runner = _Runner(_completed(env))
    monkeypatch.setattr(subprocess, "run", runner)

    with pytest.raises(SubscriptionLLMError, match="error envelope"):
        SubscriptionLLMClient().complete_with_schema(
            [Message(role="user", content="hi")], PROPOSALS_SCHEMA
        )


def test_non_json_stdout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _Runner(
        subprocess.CompletedProcess(
            args=["claude"], returncode=0, stdout="not json at all", stderr=""
        )
    )
    monkeypatch.setattr(subprocess, "run", runner)

    with pytest.raises(SubscriptionLLMError, match="did not return JSON"):
        SubscriptionLLMClient().complete_with_schema(
            [Message(role="user", content="hi")], PROPOSALS_SCHEMA
        )


def test_missing_binary_raises_with_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise FileNotFoundError("no claude")

    monkeypatch.setattr(subprocess, "run", _boom)

    with pytest.raises(SubscriptionLLMError, match="not found"):
        SubscriptionLLMClient(bin="claude").complete_with_schema(
            [Message(role="user", content="hi")], PROPOSALS_SCHEMA
        )
