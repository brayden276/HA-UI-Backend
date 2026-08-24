"""Snapshot and canonical Energy sensors for HA Component Backend."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.const import UnitOfPower

from .const import DOMAIN, REVISION, ROOMS, SENSOR_ENTITY_ID
from .contracts import DEFAULT_ENERGY_PROFILE
from .energy import EnergyManager
from . import SplitRegistry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Expose one stable entity for every registered room record."""
    registry = hass.data[DOMAIN][entry.entry_id]
    energy = hass.data[DOMAIN]["energy_manager"]
    async_add_entities(
        [
            SplitStateRegistrySensor(registry),
            EnergyPowerSensor(energy, "house_w", "Household Consumption Power", "home-lightning-bolt"),
            EnergyPowerSensor(energy, "solar_w", "Household Solar Power", "solar-power"),
            EnergyPowerSensor(energy, "grid_w", "Household Grid Power", "transmission-tower"),
        ]
    )


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


class EnergyPowerSensor(SensorEntity):
    """Expose one availability-aware canonical power metric."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_should_poll = False

    def __init__(self, manager: EnergyManager, field: str, name: str, icon: str) -> None:
        self._manager = manager
        self._field = field
        self._attr_name = name
        self._attr_icon = f"mdi:{icon}"
        self._attr_unique_id = f"ha_component_energy_{field.removesuffix('_w')}"
        self.entity_id = f"sensor.ha_component_{field.removesuffix('_w')}_power"
        self._remove = manager.async_add_listener(self.async_write_ha_state)

    @property
    def native_value(self) -> float | None:
        return self._manager.power_snapshot(DEFAULT_ENERGY_PROFILE)[self._field]

    @property
    def available(self) -> bool:
        return self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict:
        snapshot = self._manager.power_snapshot(DEFAULT_ENERGY_PROFILE)
        return {
            "profile": snapshot["profile"],
            "profile_revision": snapshot["revision"],
            "sources": snapshot["sources"],
            **({"profile_error": snapshot["error"]} if snapshot.get("error") else {}),
        }

    async def async_will_remove_from_hass(self) -> None:
        self._remove()
        await super().async_will_remove_from_hass()
