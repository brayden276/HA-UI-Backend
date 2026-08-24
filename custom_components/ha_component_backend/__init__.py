"""Shared backend integration for HA Component Library."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .diagnostics import log_unexpected_error
from .energy import EnergyManager
from .services import async_register_services
from .split_registry import SplitRegistry
from .websocket import async_register_websocket_api


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register integration services before an entry is loaded."""
    try:
        await async_register_services(hass)
        async_register_websocket_api(hass)
    except Exception as err:
        log_unexpected_error("integration setup", err)
        raise
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Start one registry entry and expose its sensor."""
    try:
        hass.data.setdefault(DOMAIN, {})
        registry = SplitRegistry(hass)
        await registry.async_load()
        hass.data[DOMAIN][entry.entry_id] = registry
        energy_manager = EnergyManager(hass, registry)
        hass.data[DOMAIN]["energy_manager"] = energy_manager
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception as err:
        log_unexpected_error(
            "config entry setup",
            err,
            context={"entry_id": entry.entry_id},
        )
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the sensor and timers."""
    try:
        unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        if unloaded:
            energy_manager: EnergyManager | None = hass.data[DOMAIN].pop("energy_manager", None)
            if energy_manager:
                await energy_manager.async_close()
            registry: SplitRegistry = hass.data[DOMAIN].pop(entry.entry_id)
            await registry.async_close()
        return unloaded
    except Exception as err:
        log_unexpected_error(
            "config entry unload",
            err,
            context={"entry_id": entry.entry_id},
        )
        raise
