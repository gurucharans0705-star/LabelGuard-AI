"""Provider adapter for the Claude API.

The rest of the application depends only on `complete(system, user) -> str`, so a different model
provider (or a test double) can be swapped in. Failures are mapped to our own errors with fixed,
safe messages: the SDK's error text is never shown or logged, because it could echo request
details, and the API key is never written anywhere.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

from labelguard.ai.config import KEY_VARIABLE, AiSettings
from labelguard.ai.models import AiRequestError, AiResponseError, AiTimeout, AiUnavailable

logger = logging.getLogger(__name__)


class Provider(Protocol):
    name: str
    model: str

    def complete(self, system: str, user: str) -> str: ...


class ClaudeProvider:
    """Calls the Claude Messages API through the official `anthropic` package (imported only when needed)."""

    name = "claude"

    def __init__(self, settings: AiSettings, client: Any | None = None):
        self._settings = settings
        self._client = client  # a test double, or None to create the real client on first use
        self.model = settings.model

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError:
                raise AiUnavailable("The 'anthropic' package is not installed. Run: pip install anthropic") from None
            self._client = anthropic.Anthropic(
                api_key=self._settings.api_key, timeout=self._settings.timeout_s, max_retries=1
            )
        return self._client

    def complete(self, system: str, user: str) -> str:
        if not self._settings.configured:
            raise AiUnavailable(self._settings.status_message)
        client = self._get_client()
        try:
            response = client.messages.create(
                model=self._settings.model,
                max_tokens=self._settings.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:  # the SDK raises several types; all are mapped below
            raise self._map_error(exc) from None
        text = "".join(
            getattr(block, "text", "") for block in (getattr(response, "content", None) or [])
            if getattr(block, "type", "text") == "text"
        )
        if not text.strip():
            raise AiResponseError(["The model returned no text."])
        return text

    def _map_error(self, exc: Exception) -> Exception:
        name = type(exc).__name__
        status = getattr(exc, "status_code", None)
        logger.warning("Claude API call failed: %s (status %s)", name, status)  # class and status only
        if "Timeout" in name or isinstance(exc, TimeoutError):
            return AiTimeout(f"The Claude API did not answer within {self._settings.timeout_s:.0f} seconds.")
        if status in (401, 403):
            return AiRequestError(f"The Claude API rejected the credentials (HTTP {status}). Check {KEY_VARIABLE}.")
        if status == 429:
            return AiRequestError("The Claude API rate limit was reached. Try again later.")
        if isinstance(status, int) and status >= 500:
            return AiRequestError(f"The Claude API reported a service problem (HTTP {status}). Try again later.")
        if isinstance(status, int):
            return AiRequestError(f"The Claude API rejected the request (HTTP {status}).")
        if "Connection" in name or isinstance(exc, (ConnectionError, OSError)):
            return AiRequestError("The Claude API could not be reached (network problem).")
        return AiRequestError(f"The Claude API call failed ({name}).")
