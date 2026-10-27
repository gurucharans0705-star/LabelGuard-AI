"""AI settings, read from environment variables only. Secrets are never written to code, logs or files."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_TOKENS = 1500
KEY_VARIABLE = "ANTHROPIC_API_KEY"


def _float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default
    return value if value > 0 else default


def _int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class AiSettings:
    """What is needed to call the model. The key is excluded from repr(), so it cannot leak into a log by accident."""

    api_key: str = field(default="", repr=False)
    model: str = DEFAULT_MODEL
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_tokens: int = DEFAULT_MAX_TOKENS
    enabled: bool = True

    @property
    def configured(self) -> bool:
        return bool(self.api_key.strip()) and self.enabled

    @property
    def status_message(self) -> str:
        if not self.enabled:
            return "AI assistance is switched off (LABELGUARD_AI_ENABLED is false)."
        if not self.api_key.strip():
            return f"No API key is configured ({KEY_VARIABLE} is not set), so the deterministic explanation is used."
        return f"Claude API is configured (model {self.model})."


def get_settings() -> AiSettings:
    """Read the settings now (so a changed environment variable is picked up without a code change)."""
    enabled = os.environ.get("LABELGUARD_AI_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
    return AiSettings(
        api_key=os.environ.get(KEY_VARIABLE, "").strip(),
        model=os.environ.get("LABELGUARD_AI_MODEL", "").strip() or DEFAULT_MODEL,
        timeout_s=_float("LABELGUARD_AI_TIMEOUT_S", DEFAULT_TIMEOUT_S),
        max_tokens=_int("LABELGUARD_AI_MAX_TOKENS", DEFAULT_MAX_TOKENS),
        enabled=enabled,
    )
