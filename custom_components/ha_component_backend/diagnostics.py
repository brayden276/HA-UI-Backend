"""Central error logging helpers for HA Component Backend."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

_LOGGER = logging.getLogger("custom_components.ha_component_backend")
_MAX_CONTEXT_VALUE_LENGTH = 160


def _context_suffix(context: Mapping[str, Any] | None) -> str:
    """Render bounded, single-line diagnostic context without dumping payloads."""
    if not context:
        return ""
    values: list[str] = []
    for key, value in sorted(context.items()):
        if value is None:
            continue
        text = str(value).replace("\r", "\\r").replace("\n", "\\n")
        if len(text) > _MAX_CONTEXT_VALUE_LENGTH:
            text = f"{text[: _MAX_CONTEXT_VALUE_LENGTH - 3]}..."
        values.append(f"{key}={text}")
    return f" [{', '.join(values)}]" if values else ""


def log_handled_error(
    operation: str,
    error: BaseException,
    *,
    context: Mapping[str, Any] | None = None,
) -> None:
    """Log a handled client/domain failure without changing its behaviour."""
    _LOGGER.warning(
        "%s failed%s: %s",
        operation,
        _context_suffix(context),
        error,
    )


def log_unexpected_error(
    operation: str,
    error: BaseException,
    *,
    context: Mapping[str, Any] | None = None,
) -> None:
    """Log an unexpected failure with its original traceback."""
    _LOGGER.error(
        "%s failed%s: %s",
        operation,
        _context_suffix(context),
        error,
        exc_info=(type(error), error, error.__traceback__),
    )
