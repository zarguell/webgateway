"""Content quality validation for cached responses.

Evaluates a list of trigger conditions (mirroring
:class:`~serp_llm.config.CacheInvalidationTrigger`) against response content
and returns whether the content is acceptable plus a human-readable reason when
it is not.
"""

from __future__ import annotations

from typing import Any


def validate_content(
    content: str,
    triggers: list[dict[str, Any]],
) -> tuple[bool, str | None]:
    """Return ``(True, None)`` if all triggers pass, else ``(False, reason)``.

    Triggers are evaluated in order; the first failure wins.
    """
    for trigger in triggers:
        condition = trigger.get("condition", {}) or {}
        for kind, value in condition.items():
            ok, reason = _check_condition(content, kind, value)
            if not ok:
                return False, reason
    return True, None


def _check_condition(
    content: str,
    kind: str,
    value: Any,
) -> tuple[bool, str | None]:
    """Evaluate a single condition kind.

    Returns ``(True, None)`` on pass and ``(False, reason)`` on fail.
    ``provider_error_class`` is never applicable to post-fetch content
    validation, so it is treated as a pass.
    """
    if kind == "content_length_bytes":
        actual = len(content.encode())
        if actual < value:
            return False, f"content_length_bytes < {value} (actual: {actual})"
        return True, None

    if kind == "content_contains":
        for needle in value:
            if needle in content:
                return False, f'content_contains: "{needle}"'
        return True, None

    if kind == "provider_error_class":
        # Not applicable to content validation; only relevant to provider
        # error responses, which never reach the cache layer.
        return True, None

    # Unknown condition kinds are ignored rather than treated as failures, so a
    # future config schema extension cannot lock out the cache by accident.
    return True, None
