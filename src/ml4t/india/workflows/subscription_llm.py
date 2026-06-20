"""Key-free, subscription-backed LLM client for the research agent.

This adapter satisfies the upstream ``ml4t.agent.llm.base.LLMClient``
Protocol -- a single :meth:`complete_with_schema` -- WITHOUT an
``ANTHROPIC_API_KEY``. Instead of a vendor SDK it shells out to the local
``claude`` CLI, which authenticates through the user's **subscription**
OAuth login (``claude`` then ``/login``). The CLI's native
``--json-schema`` flag constrains the model to the requested JSON shape,
and the schema-validated object comes back in the envelope's
``structured_output`` field.

Why a subprocess and not the SDK: the ``claude_agent_sdk`` Python package
is not installed in this environment, and the CLI is the only
key-free path to the subscription. The subprocess env has
``ANTHROPIC_API_KEY`` stripped so we can never silently fall back to a
metered API key -- the subscription path is guaranteed.

The default keyless :class:`~ml4t.india.workflows.agent.IndiaResearchAgent`
LLM stays the deterministic offline mock; this client is strictly opt-in
(wire it via ``llm=SubscriptionLLMClient()`` or the
``ML4T_INDIA_AGENT_LLM=subscription`` env flag in ``examples/end_to_end.py``).
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ml4t.agent.llm import LLMResponse, Message

__all__ = ["SubscriptionLLMClient", "SubscriptionLLMError"]

_LOGIN_HINT = (
    "Ensure the `claude` CLI is installed and logged in to your subscription: "
    "run `claude` then `/login` (no ANTHROPIC_API_KEY is used)."
)


class SubscriptionLLMError(RuntimeError):
    """Raised when the ``claude`` CLI fails or returns an unusable envelope."""


@dataclass(frozen=True)
class SubscriptionLLMClient:
    """Subscription-backed ``LLMClient`` driving the local ``claude`` CLI.

    Parameters
    ----------
    model:
        Model id passed to ``--model`` (default ``claude-opus-4-8``).
    bin:
        Path or name of the ``claude`` executable (default ``claude``).
    timeout:
        Per-call subprocess timeout in seconds (default 120).
    """

    model: str = "claude-opus-4-8"
    bin: str = "claude"
    timeout: float = 120.0

    # -- LLMClient Protocol -------------------------------------------------
    def complete_with_schema(
        self,
        messages: Sequence[Message],
        json_schema: Mapping[str, Any],
        *,
        max_tokens: int = 1024,  # noqa: ARG002 -- Protocol parity; CLI has no flag
        temperature: float | None = 0.0,  # noqa: ARG002 -- Protocol parity
    ) -> LLMResponse:
        """Return an :class:`LLMResponse` whose ``data`` validates the schema.

        ``max_tokens``/``temperature`` are accepted for Protocol parity but
        not forwarded -- the ``claude`` CLI does not expose them. The
        ``--json-schema`` constraint plus the subscription default make the
        single call effectively deterministic; we still validate and, per
        the Protocol, **re-prompt once** before raising on malformed output.
        """
        system_text, user_text = self._split_messages(messages)

        response = self._invoke(system_text, user_text, json_schema)
        error = self._schema_error(response.data, json_schema)
        if error is None:
            return response

        # Re-prompt exactly once, feeding the validator error back to the model.
        retry_user = (
            f"{user_text}\n\nYour previous response failed JSON-schema "
            f"validation with this error:\n{error}\n"
            "Return a corrected JSON object that strictly conforms to the schema."
        )
        response = self._invoke(system_text, retry_user, json_schema)
        error = self._schema_error(response.data, json_schema)
        if error is not None:
            raise SubscriptionLLMError(
                "claude CLI output failed schema validation after one re-prompt: "
                f"{error}"
            )
        return response

    # -- internals ----------------------------------------------------------
    @staticmethod
    def _split_messages(messages: Sequence[Message]) -> tuple[str, str]:
        """Split into a joined system prompt and a joined user/stdin prompt."""
        system_parts: list[str] = []
        user_parts: list[str] = []
        for message in messages:
            if message.role == "system":
                system_parts.append(message.content)
            elif message.role == "assistant":
                user_parts.append(f"[assistant]\n{message.content}")
            else:  # user
                user_parts.append(message.content)
        return "\n\n".join(system_parts), "\n\n".join(user_parts)

    def _invoke(
        self, system_text: str, user_text: str, json_schema: Mapping[str, Any]
    ) -> LLMResponse:
        """Run one ``claude`` CLI call and parse the envelope to LLMResponse."""
        from ml4t.agent.llm import LLMResponse  # noqa: PLC0415

        cmd = [
            self.bin,
            "-p",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(json_schema),
            "--model",
            self.model,
            "--permission-mode",
            "bypassPermissions",
        ]
        if system_text:
            cmd += ["--append-system-prompt", system_text]

        # Strip ANTHROPIC_API_KEY so the subscription OAuth path is guaranteed
        # and we can never silently fall back to a metered API key.
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

        try:
            completed = subprocess.run(
                cmd,
                input=user_text,
                capture_output=True,
                text=True,
                env=env,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SubscriptionLLMError(
                f"`{self.bin}` executable not found. {_LOGIN_HINT}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise SubscriptionLLMError(
                f"`{self.bin}` timed out after {self.timeout}s. {_LOGIN_HINT}"
            ) from exc

        if completed.returncode != 0:
            raise SubscriptionLLMError(
                f"`{self.bin}` exited with code {completed.returncode}: "
                f"{(completed.stderr or completed.stdout or '').strip()}\n{_LOGIN_HINT}"
            )

        envelope = self._parse_envelope(completed.stdout)
        data = self._extract_data(envelope)
        raw = self._extract_raw(envelope, data)
        usage = {
            k: v for k, v in (envelope.get("usage") or {}).items() if isinstance(v, int)
        }
        return LLMResponse(data=data, raw=raw, usage=usage)

    def _parse_envelope(self, stdout: str) -> Mapping[str, Any]:
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise SubscriptionLLMError(
                f"`{self.bin}` did not return JSON (--output-format json). "
                f"Got: {stdout[:400]!r}\n{_LOGIN_HINT}"
            ) from exc
        if not isinstance(envelope, Mapping):
            raise SubscriptionLLMError(
                f"`{self.bin}` returned a non-object envelope: {stdout[:400]!r}"
            )
        # The CLI flags errors in-band even on a zero exit code.
        if envelope.get("is_error") or envelope.get("subtype") not in (None, "success"):
            detail = (
                envelope.get("api_error_status")
                or envelope.get("result")
                or envelope.get("subtype")
                or "unknown error"
            )
            raise SubscriptionLLMError(
                f"`{self.bin}` returned an error envelope: {detail}\n{_LOGIN_HINT}"
            )
        return envelope

    @staticmethod
    def _extract_data(envelope: Mapping[str, Any]) -> Mapping[str, Any]:
        """Pull the schema-validated object out of the CLI envelope.

        The native ``--json-schema`` path puts the parsed object in
        ``structured_output``. We also handle ``result`` carrying the JSON
        either as an already-parsed object or as a string, in case a CLI
        version routes it there instead.
        """
        structured = envelope.get("structured_output")
        if isinstance(structured, Mapping):
            return dict(structured)

        result = envelope.get("result")
        if isinstance(result, Mapping):
            return dict(result)
        if isinstance(result, str):
            try:
                parsed = json.loads(result)
            except json.JSONDecodeError as exc:
                raise SubscriptionLLMError(
                    "claude CLI envelope had no `structured_output` and `result` "
                    f"was not JSON: {result[:400]!r}"
                ) from exc
            if isinstance(parsed, Mapping):
                return parsed
            raise SubscriptionLLMError(
                f"claude CLI `result` parsed to a non-object: {type(parsed).__name__}"
            )
        raise SubscriptionLLMError(
            "claude CLI envelope carried neither `structured_output` nor a "
            "usable `result` field."
        )

    @staticmethod
    def _extract_raw(envelope: Mapping[str, Any], data: Mapping[str, Any]) -> str:
        result = envelope.get("result")
        if isinstance(result, str) and result:
            return result
        return json.dumps(data)

    @staticmethod
    def _schema_error(
        data: Mapping[str, Any], json_schema: Mapping[str, Any]
    ) -> str | None:
        """Return a validator error message, or ``None`` if ``data`` conforms."""
        import jsonschema  # noqa: PLC0415

        try:
            jsonschema.validate(instance=data, schema=dict(json_schema))
        except jsonschema.ValidationError as exc:
            return exc.message
        return None
