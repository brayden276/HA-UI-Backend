"""Regression tests for central HA Component Backend logging."""

from importlib.util import module_from_spec, spec_from_file_location
import logging
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "custom_components" / "ha_component_backend" / "diagnostics.py"
SPEC = spec_from_file_location("ha_component_backend_diagnostics", PATH)
assert SPEC and SPEC.loader
DIAGNOSTICS = module_from_spec(SPEC)
SPEC.loader.exec_module(DIAGNOSTICS)


class CaptureHandler(logging.Handler):
    """Collect log records without depending on Home Assistant."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


logger = logging.getLogger("custom_components.ha_component_backend")
handler = CaptureHandler()
previous_level = logger.level
previous_propagate = logger.propagate
logger.setLevel(logging.DEBUG)
logger.propagate = False
logger.addHandler(handler)

try:
    DIAGNOSTICS.log_handled_error(
        "websocket profile/update",
        ValueError("invalid profile"),
        context={"profile_id": "household\nsecurity"},
    )
    try:
        raise RuntimeError("recorder failed")
    except RuntimeError as error:
        DIAGNOSTICS.log_unexpected_error(
            "energy day refresh",
            error,
            context={"profile_id": "household-energy", "day": "2026-08-24"},
        )
finally:
    logger.removeHandler(handler)
    logger.setLevel(previous_level)
    logger.propagate = previous_propagate

assert len(handler.records) == 2
handled, unexpected = handler.records
assert handled.levelno == logging.WARNING
assert handled.name == "custom_components.ha_component_backend"
assert "websocket profile/update failed" in handled.getMessage()
assert "profile_id=household\\nsecurity" in handled.getMessage()
assert "invalid profile" in handled.getMessage()
assert handled.exc_info is None

assert unexpected.levelno == logging.ERROR
assert unexpected.name == "custom_components.ha_component_backend"
assert "energy day refresh failed" in unexpected.getMessage()
assert "profile_id=household-energy" in unexpected.getMessage()
assert "day=2026-08-24" in unexpected.getMessage()
assert unexpected.exc_info is not None
assert unexpected.exc_info[0] is RuntimeError

print("Logging contract tests passed: central logger, bounded context and traceback retention")
