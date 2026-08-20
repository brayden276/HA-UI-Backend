"""Snapshot sensor for the persistent Split State Registry."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, REVISION, ROOMS, SENSOR_ENTITY_ID
from . import SplitRegistry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Expose one stable entity for every registered room record."""
    async_add_entities([SplitStateRegistrySensor(hass.data[DOMAIN][entry.entry_id])])


class SplitStateRegistrySensor(SensorEntity):
    """Surface the complete room map without an input_text size limit."""

    _attr_name = "Split State Registry"
    _attr_icon = "mdi:air-conditioner"
    _attr_unique_id = "ha_component_backend"

    def __init__(self, registry: SplitRegistry) -> None:
        self._registry = registry
        self.entity_id = SENSOR_ENTITY_ID
        self._remove = registry.async_add_listener(self.async_write_ha_state)

    @property
    def native_value(self) -> int:
        return len(self._registry.data[ROOMS])

    @property
    def extra_state_attributes(self) -> dict:
        snapshot = self._registry.snapshot()
        return {REVISION: snapshot[REVISION], ROOMS: snapshot[ROOMS]}

    async def async_will_remove_from_hass(self) -> None:
        self._remove()
        await super().async_will_remove_from_hass()
