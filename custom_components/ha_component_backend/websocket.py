"""WebSocket API for compact, reusable dashboard preference state."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    WS_ENERGY_DAY,
    WS_PREFERENCES_GET,
    WS_PREFERENCES_REMOVE,
    WS_PREFERENCES_UPDATE,
    WS_PROFILE_GET,
    WS_PROFILE_REMOVE,
    WS_PROFILE_UPDATE,
)
from .contracts import ContractError, PROFILE_KINDS, normalise_profile, profile_key
from .diagnostics import log_handled_error, log_unexpected_error
from .energy import EnergyManager
from .split_registry import PreferenceConflict, get_registry

_EXPECTED_REVISION = vol.All(vol.Coerce(int), vol.Range(min=0))
_JSON_VALUE = vol.Any(None, bool, int, float, str, list, dict)


def _preference_context(msg: dict[str, Any]) -> dict[str, Any]:
    """Return safe identifiers for preference command diagnostics."""
    return {"key": msg.get("key"), "message_id": msg.get("id")}


def _profile_context(msg: dict[str, Any]) -> dict[str, Any]:
    """Return safe identifiers for profile command diagnostics."""
    return {
        "kind": msg.get("kind"),
        "profile_id": msg.get("profile_id"),
        "message_id": msg.get("id"),
    }


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_PREFERENCES_GET,
        vol.Required("key"): cv.string,
    }
)
@callback
def websocket_get_preference(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return one dashboard preference and its current store revision."""
    try:
        result = get_registry(hass).preference_snapshot(msg["key"])
    except HomeAssistantError as err:
        log_handled_error(
            "websocket preferences/get",
            err,
            context=_preference_context(msg),
        )
        connection.send_error(msg["id"], "preference_unavailable", str(err))
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_PREFERENCES_UPDATE,
        vol.Required("key"): cv.string,
        vol.Required("value"): _JSON_VALUE,
        vol.Optional("expected_revision"): _EXPECTED_REVISION,
    }
)
@websocket_api.async_response
async def websocket_update_preference(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Persist one dashboard preference with optimistic concurrency."""
    try:
        result = await get_registry(hass).async_update_preference(
            msg["key"],
            msg["value"],
            msg.get("expected_revision"),
            Context(user_id=connection.user.id),
        )
    except PreferenceConflict as err:
        log_handled_error(
            "websocket preferences/update",
            err,
            context=_preference_context(msg),
        )
        connection.send_error(msg["id"], "preference_conflict", str(err))
        return
    except HomeAssistantError as err:
        log_handled_error(
            "websocket preferences/update",
            err,
            context=_preference_context(msg),
        )
        connection.send_error(msg["id"], "invalid_preference", str(err))
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_PREFERENCES_REMOVE,
        vol.Required("key"): cv.string,
        vol.Optional("expected_revision"): _EXPECTED_REVISION,
    }
)
@websocket_api.async_response
async def websocket_remove_preference(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Remove one dashboard preference with optimistic concurrency."""
    try:
        result = await get_registry(hass).async_remove_preference(
            msg["key"],
            msg.get("expected_revision"),
            Context(user_id=connection.user.id),
        )
    except PreferenceConflict as err:
        log_handled_error(
            "websocket preferences/remove",
            err,
            context=_preference_context(msg),
        )
        connection.send_error(msg["id"], "preference_conflict", str(err))
        return
    except HomeAssistantError as err:
        log_handled_error(
            "websocket preferences/remove",
            err,
            context=_preference_context(msg),
        )
        connection.send_error(msg["id"], "invalid_preference", str(err))
        return
    connection.send_result(msg["id"], result)


def _profile_result(kind: str, profile_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Translate a private preference snapshot into the public profile shape."""
    return {
        "kind": str(kind).strip().lower(),
        "profile_id": str(profile_id).strip().lower(),
        "found": snapshot["found"],
        "profile": snapshot["value"] if snapshot["found"] else None,
        "revision": snapshot["revision"],
        **({"changed": snapshot["changed"]} if "changed" in snapshot else {}),
    }


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_PROFILE_GET,
        vol.Required("kind"): vol.In(PROFILE_KINDS),
        vol.Required("profile_id"): cv.string,
    }
)
@callback
def websocket_get_profile(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return one validated reusable dashboard profile."""
    try:
        snapshot = get_registry(hass).preference_snapshot(
            profile_key(msg["kind"], msg["profile_id"])
        )
        if snapshot["found"]:
            snapshot["value"] = normalise_profile(
                msg["kind"], msg["profile_id"], snapshot["value"]
            )
    except (ContractError, HomeAssistantError) as err:
        log_handled_error(
            "websocket profile/get",
            err,
            context=_profile_context(msg),
        )
        connection.send_error(msg["id"], "profile_unavailable", str(err))
        return
    connection.send_result(
        msg["id"], _profile_result(msg["kind"], msg["profile_id"], snapshot)
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_PROFILE_UPDATE,
        vol.Required("kind"): vol.In(PROFILE_KINDS),
        vol.Required("profile_id"): cv.string,
        vol.Required("profile"): dict,
        vol.Optional("expected_revision"): _EXPECTED_REVISION,
    }
)
@websocket_api.async_response
async def websocket_update_profile(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Validate and persist one reusable dashboard profile."""
    try:
        profile = normalise_profile(msg["kind"], msg["profile_id"], msg["profile"])
        result = await get_registry(hass).async_update_preference(
            profile_key(msg["kind"], msg["profile_id"]),
            profile,
            msg.get("expected_revision"),
            Context(user_id=connection.user.id),
        )
    except PreferenceConflict as err:
        log_handled_error(
            "websocket profile/update",
            err,
            context=_profile_context(msg),
        )
        connection.send_error(msg["id"], "profile_conflict", str(err))
        return
    except (ContractError, HomeAssistantError) as err:
        log_handled_error(
            "websocket profile/update",
            err,
            context=_profile_context(msg),
        )
        connection.send_error(msg["id"], "invalid_profile", str(err))
        return
    connection.send_result(
        msg["id"], _profile_result(msg["kind"], msg["profile_id"], result)
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_PROFILE_REMOVE,
        vol.Required("kind"): vol.In(PROFILE_KINDS),
        vol.Required("profile_id"): cv.string,
        vol.Optional("expected_revision"): _EXPECTED_REVISION,
    }
)
@websocket_api.async_response
async def websocket_remove_profile(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Remove one reusable dashboard profile with optimistic concurrency."""
    try:
        result = await get_registry(hass).async_remove_preference(
            profile_key(msg["kind"], msg["profile_id"]),
            msg.get("expected_revision"),
            Context(user_id=connection.user.id),
        )
    except PreferenceConflict as err:
        log_handled_error(
            "websocket profile/remove",
            err,
            context=_profile_context(msg),
        )
        connection.send_error(msg["id"], "profile_conflict", str(err))
        return
    except (ContractError, HomeAssistantError) as err:
        log_handled_error(
            "websocket profile/remove",
            err,
            context=_profile_context(msg),
        )
        connection.send_error(msg["id"], "invalid_profile", str(err))
        return
    connection.send_result(
        msg["id"], _profile_result(msg["kind"], msg["profile_id"], result)
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_ENERGY_DAY,
        vol.Required("profile_id"): cv.string,
        vol.Required("day"): cv.string,
    }
)
@websocket_api.async_response
async def websocket_energy_day(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return coalesced Energy totals and series for one local day."""
    context = {
        "profile_id": msg.get("profile_id"),
        "day": msg.get("day"),
        "message_id": msg.get("id"),
    }
    manager = hass.data.get(DOMAIN, {}).get("energy_manager")
    if not isinstance(manager, EnergyManager):
        error = HomeAssistantError("Energy backend is not configured")
        log_handled_error("websocket energy/day", error, context=context)
        connection.send_error(
            msg["id"], "energy_unavailable", "Energy backend is not configured"
        )
        return
    try:
        result = await manager.async_day(msg["profile_id"], msg["day"])
    except HomeAssistantError as err:
        log_handled_error("websocket energy/day", err, context=context)
        connection.send_error(msg["id"], "energy_unavailable", str(err))
        return
    except Exception as err:  # recorder errors are explicit, never empty success
        log_unexpected_error("websocket energy/day", err, context=context)
        connection.send_error(msg["id"], "energy_recorder_error", str(err))
        return
    connection.send_result(msg["id"], result)


@callback
def async_register_websocket_api(hass: HomeAssistant) -> None:
    """Register preference commands once for this Home Assistant process."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get("websocket"):
        return
    websocket_api.async_register_command(hass, websocket_get_preference)
    websocket_api.async_register_command(hass, websocket_update_preference)
    websocket_api.async_register_command(hass, websocket_remove_preference)
    websocket_api.async_register_command(hass, websocket_get_profile)
    websocket_api.async_register_command(hass, websocket_update_profile)
    websocket_api.async_register_command(hass, websocket_remove_profile)
    websocket_api.async_register_command(hass, websocket_energy_day)
    domain_data["websocket"] = True
