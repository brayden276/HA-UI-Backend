"""Service registration for HA Component Backend."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    SERVICE_CONFIGURE_DASHBOARD_PROFILE,
    SERVICE_DELETE_PROFILE,
    SERVICE_REGISTER_ROOM,
    SERVICE_REMOVE_ROOM,
    SERVICE_RESUME_ROOM,
    SERVICE_SET_SETTINGS,
    SERVICE_SET_TIMER,
    SERVICE_UPSERT_PROFILE,
    SERVICE_REMOVE_DASHBOARD_PROFILE,
)
from .contracts import PROFILE_KINDS, normalise_profile, profile_key
from .split_registry import get_registry

_ROOM = vol.Schema({vol.Required("room_id"): cv.string})
_REGISTER = _ROOM.extend(
    {
        vol.Required("climate"): cv.entity_id,
        vol.Required("controller"): cv.entity_id,
        vol.Optional("vertical_vane"): cv.entity_id,
        vol.Optional("horizontal_vane"): cv.entity_id,
        vol.Optional("minimum_target", default=16): vol.Coerce(float),
        vol.Optional("maximum_target", default=31): vol.Coerce(float),
        vol.Optional("fan_ceiling", default="Quiet"): cv.string,
        vol.Optional("last_mode"): cv.string,
        vol.Optional("deadline"): cv.string,
        vol.Optional("profiles", default=[]): list,
    }
)
_SETTINGS = _ROOM.extend(
    {
        vol.Optional("climate"): cv.entity_id,
        vol.Optional("controller"): cv.entity_id,
        vol.Optional("vertical_vane"): cv.entity_id,
        vol.Optional("horizontal_vane"): cv.entity_id,
        vol.Optional("minimum_target"): vol.Coerce(float),
        vol.Optional("maximum_target"): vol.Coerce(float),
        vol.Optional("fan_ceiling"): cv.string,
        vol.Optional("last_mode"): cv.string,
        vol.Optional("deadline"): cv.string,
        vol.Optional("profiles"): list,
    }
)
_TIMER = _ROOM.extend(
    {
        vol.Required("operation"): vol.In({"set", "extend", "cancel"}),
        vol.Optional("minutes", default=60): vol.All(
            vol.Coerce(int),
            vol.Range(min=0, max=720),
        ),
    }
)
_PROFILE = _ROOM.extend(
    {vol.Required("profile"): dict, vol.Optional("index"): vol.Coerce(int)}
)
_DELETE_PROFILE = _ROOM.extend(
    {
        vol.Optional("profile_id"): cv.string,
        vol.Optional("index"): vol.Coerce(int),
        vol.Optional("name"): cv.string,
    }
)
_DASHBOARD_PROFILE = vol.Schema(
    {
        vol.Required("kind"): vol.In(PROFILE_KINDS),
        vol.Required("profile_id"): cv.string,
        vol.Required("profile"): dict,
    }
)
_REMOVE_DASHBOARD_PROFILE = vol.Schema(
    {
        vol.Required("kind"): vol.In(PROFILE_KINDS),
        vol.Required("profile_id"): cv.string,
    }
)


async def async_register_services(hass: HomeAssistant) -> None:
    """Register validated services once for the backend domain."""
    if hass.data.setdefault(DOMAIN, {}).get("services"):
        return
    hass.data[DOMAIN]["services"] = True

    async def configure_room(call: ServiceCall) -> ServiceResponse:
        result = await get_registry(hass).async_configure_room(call)
        return result if call.return_response else None

    async def remove_room(call: ServiceCall) -> ServiceResponse:
        result = await get_registry(hass).async_remove_room(call)
        return result if call.return_response else None

    async def update_room(call: ServiceCall) -> ServiceResponse:
        result = await get_registry(hass).async_update_room(call)
        return result if call.return_response else None

    async def set_timer(call: ServiceCall) -> ServiceResponse:
        result = await get_registry(hass).async_set_timer(call)
        return result if call.return_response else None

    async def resume_room(call: ServiceCall) -> ServiceResponse:
        result = await get_registry(hass).async_resume_room(call)
        return result if call.return_response else None

    async def upsert_profile(call: ServiceCall) -> ServiceResponse:
        result = await get_registry(hass).async_upsert_profile(call)
        return result if call.return_response else None

    async def remove_profile(call: ServiceCall) -> ServiceResponse:
        result = await get_registry(hass).async_remove_profile(call)
        return result if call.return_response else None

    async def configure_dashboard_profile(call: ServiceCall) -> ServiceResponse:
        profile = normalise_profile(
            call.data["kind"], call.data["profile_id"], call.data["profile"]
        )
        result = await get_registry(hass).async_update_preference(
            profile_key(call.data["kind"], call.data["profile_id"]),
            profile,
            context=call.context,
        )
        response = {
            "kind": call.data["kind"],
            "profile_id": profile["id"],
            "profile": profile,
            "revision": result["revision"],
            "changed": result["changed"],
        }
        return response if call.return_response else None

    async def remove_dashboard_profile(call: ServiceCall) -> ServiceResponse:
        result = await get_registry(hass).async_remove_preference(
            profile_key(call.data["kind"], call.data["profile_id"]),
            context=call.context,
        )
        response = {
            "kind": call.data["kind"],
            "profile_id": str(call.data["profile_id"]).strip().lower(),
            "revision": result["revision"],
            "changed": result["changed"],
        }
        return response if call.return_response else None

    hass.services.async_register(
        DOMAIN,
        SERVICE_REGISTER_ROOM,
        configure_room,
        schema=_REGISTER,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_ROOM,
        remove_room,
        schema=_ROOM,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SETTINGS,
        update_room,
        schema=_SETTINGS,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_TIMER,
        set_timer,
        schema=_TIMER,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESUME_ROOM,
        resume_room,
        schema=_ROOM,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPSERT_PROFILE,
        upsert_profile,
        schema=_PROFILE,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_PROFILE,
        remove_profile,
        schema=_DELETE_PROFILE,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CONFIGURE_DASHBOARD_PROFILE,
        configure_dashboard_profile,
        schema=_DASHBOARD_PROFILE,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_REMOVE_DASHBOARD_PROFILE,
        remove_dashboard_profile,
        schema=_REMOVE_DASHBOARD_PROFILE,
        supports_response=SupportsResponse.OPTIONAL,
    )
