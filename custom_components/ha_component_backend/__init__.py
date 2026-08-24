"""Shared backend integration for HA Component Library."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PLATFORMS
from .energy import EnergyManager
from .services import async_register_services
from .split_registry import SplitRegistry
from .websocket import async_register_websocket_api


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register integration services before an entry is loaded."""
    await async_register_services(hass)
    async_register_websocket_api(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Start one registry entry and expose its sensor."""
    hass.data.setdefault(DOMAIN, {})
    registry = SplitRegistry(hass)
    await registry.async_load()
    hass.data[DOMAIN][entry.entry_id] = registry
    energy_manager = EnergyManager(hass, registry)
    hass.data[DOMAIN]["energy_manager"] = energy_manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the sensor and timers."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        energy_manager: EnergyManager | None = hass.data[DOMAIN].pop("energy_manager", None)
        if energy_manager:
            await energy_manager.async_close()
        registry: SplitRegistry = hass.data[DOMAIN].pop(entry.entry_id)
        await registry.async_close()
    return unloaded
