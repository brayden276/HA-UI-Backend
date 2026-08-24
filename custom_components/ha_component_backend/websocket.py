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
    WS_PREFERENCES_GET,
    WS_PREFERENCES_REMOVE,
    WS_PREFERENCES_UPDATE,
)
from .split_registry import PreferenceConflict, get_registry

_EXPECTED_REVISION = vol.All(vol.Coerce(int), vol.Range(min=0))
_JSON_VALUE = vol.Any(None, bool, int, float, str, list, dict)


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
        connection.send_error(msg["id"], "preference_conflict", str(err))
        return
    except HomeAssistantError as err:
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
        connection.send_error(msg["id"], "preference_conflict", str(err))
        return
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "invalid_preference", str(err))
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
    domain_data["websocket"] = True
