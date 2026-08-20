"""Durable, room-keyed state and services for Split System Components."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta
from typing import Any, Callable

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    FAN_CEILINGS,
    PLATFORMS,
    REVISION,
    ROOMS,
    SERVICE_DELETE_PROFILE,
    SERVICE_REGISTER_ROOM,
    SERVICE_REMOVE_ROOM,
    SERVICE_RESUME_ROOM,
    SERVICE_SET_SETTINGS,
    SERVICE_SET_TIMER,
    SERVICE_UPSERT_PROFILE,
    STORE_KEY,
    STORE_VERSION,
)

_FAN_RANK = {"quiet": 0, "low": 1, "medium": 2, "high": 3, "auto": 4}
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
        vol.Optional("minutes", default=60): vol.All(vol.Coerce(int), vol.Range(min=0, max=720)),
    }
)
_PROFILE = _ROOM.extend({vol.Required("profile"): dict, vol.Optional("index"): vol.Coerce(int)})
_DELETE_PROFILE = _ROOM.extend({vol.Optional("profile_id"): cv.string, vol.Optional("index"): vol.Coerce(int), vol.Optional("name"): cv.string})


class SplitRegistry:
    """Serialize every room mutation into one durable HA Store document."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store = Store(hass, STORE_VERSION, STORE_KEY)
        self.data: dict[str, Any] = {REVISION: 0, ROOMS: {}}
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []
        self._deadline_unsub: dict[str, Callable[[], None]] = {}
        self._state_unsub: Callable[[], None] | None = None

    async def async_load(self) -> None:
        """Restore state, validate it and re-arm persisted deadlines."""
        loaded = await self._store.async_load()
        self.data = self._normalise_store(loaded)
        self._state_unsub = self.hass.bus.async_listen("state_changed", self._on_state_changed)
        for room_id in self.data[ROOMS]:
            self._schedule_deadline(room_id)
        await self._seed_active_modes()

    async def async_close(self) -> None:
        """Release listeners when the config entry unloads."""
        if self._state_unsub:
            self._state_unsub()
        for unsubscribe in self._deadline_unsub.values():
            unsubscribe()
        self._deadline_unsub.clear()

    def snapshot(self) -> dict[str, Any]:
        """Return a copy suitable for an HA entity attribute."""
        return deepcopy(self.data)

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Allow the sensor to update immediately after a durable write."""
        self._listeners.append(listener)

        @callback
        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def room_id_for_climate(self, entity_id: str) -> str | None:
        """Look up a stable room key from its climate entity."""
        return next(
            (key for key, room in self.data[ROOMS].items() if room["climate"] == entity_id),
            None,
        )

    async def async_configure_room(self, call: ServiceCall) -> None:
        """Create or update one room record."""
        room_id = self._room_id(call.data["room_id"])
        climate = call.data["climate"]
        if not climate.startswith("climate."):
            raise HomeAssistantError("climate must be a climate entity")
        minimum, maximum = self._bounds(
            call.data["minimum_target"],
            call.data["maximum_target"],
        )
        ceiling = self._ceiling(call.data["fan_ceiling"])
        profiles = [self._profile(profile) for profile in call.data.get("profiles") or []]

        def mutate(rooms: dict[str, dict[str, Any]]) -> None:
            existing = rooms.get(room_id)
            room = existing or self._new_room(climate)
            room.update(
                {
                    "climate": climate,
                    "controller": call.data.get("controller"),
                    "vertical_vane": call.data.get("vertical_vane"),
                    "horizontal_vane": call.data.get("horizontal_vane"),
                    "minimum_target": minimum,
                    "maximum_target": maximum,
                    "fan_ceiling": ceiling,
                    "last_mode": self._normalise_mode(call.data.get("last_mode"))
                    or (existing or {}).get("last_mode")
                    or self._current_mode(climate)
                    or "cool",
                    "deadline": call.data.get("deadline"),
                    "profiles": profiles,
                }
            )
            rooms[room_id] = room

        await self._mutate(mutate)
        self._schedule_deadline(room_id)
        await self.async_enforce(room_id)

    async def async_remove_room(self, call: ServiceCall) -> None:
        """Remove one room and every stored profile."""
        room_id = self._room_id(call.data["room_id"])

        def mutate(rooms: dict[str, dict[str, Any]]) -> None:
            self._require(rooms, room_id)
            rooms.pop(room_id)

        await self._mutate(mutate)
        self._cancel_deadline(room_id)

    async def async_update_room(self, call: ServiceCall) -> None:
        """Atomically replace operating policy values."""
        room_id = self._room_id(call.data["room_id"])

        def mutate(rooms: dict[str, dict[str, Any]]) -> None:
            room = self._require(rooms, room_id)
            minimum, maximum = self._bounds(
                call.data.get("minimum_target", room["minimum_target"]),
                call.data.get("maximum_target", room["maximum_target"]),
            )
            room["minimum_target"] = minimum
            room["maximum_target"] = maximum
            if "fan_ceiling" in call.data:
                room["fan_ceiling"] = self._ceiling(call.data["fan_ceiling"])
            for key in ("climate", "controller", "vertical_vane", "horizontal_vane", "last_mode", "deadline"):
                if key in call.data:
                    room[key] = self._normalise_mode(call.data[key]) if key == "last_mode" else call.data[key]
            if "profiles" in call.data:
                room["profiles"] = [self._profile(profile) for profile in call.data["profiles"]]

        await self._mutate(mutate)
        self._schedule_deadline(room_id)
        await self.async_enforce(room_id)

    async def async_set_timer(self, call: ServiceCall) -> None:
        """Set, extend or cancel a restart-safe deadline."""
        await self._set_timer(
            self._room_id(call.data["room_id"]),
            call.data["operation"],
            call.data["minutes"],
        )

    async def async_resume_room(self, call: ServiceCall) -> None:
        """Restore the room's last confirmed non-off HVAC mode."""
        room_id = self._room_id(call.data["room_id"])
        room = self.data[ROOMS].get(room_id)
        if room is None:
            raise HomeAssistantError(f"Unknown split-system room: {room_id}")
        mode = str(room.get("last_mode") or "").strip()
        if not mode or mode in {"off", "unknown", "unavailable"}:
            raise HomeAssistantError("No resumable split-system mode is stored")
        await self.hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": room["climate"], "hvac_mode": mode},
            blocking=True,
        )

    async def _set_timer(self, room_id: str, operation: str, minutes: int) -> None:
        """Persist one validated timer operation without synthesising a service call."""
        if operation != "cancel" and minutes < 1:
            raise HomeAssistantError("minutes must be between 1 and 720")

        def mutate(rooms: dict[str, dict[str, Any]]) -> None:
            room = self._require(rooms, room_id)
            if operation == "cancel":
                room["deadline"] = None
                return
            now = dt_util.utcnow()
            base = now
            if operation == "extend" and room.get("deadline"):
                deadline = dt_util.parse_datetime(room["deadline"])
                if deadline and deadline > now:
                    base = deadline
            room["deadline"] = (base + timedelta(minutes=minutes)).isoformat()

        await self._mutate(mutate)
        self._schedule_deadline(room_id)

    async def async_upsert_profile(self, call: ServiceCall) -> None:
        """Store a profile by stable ID with name uniqueness per room."""
        room_id = self._room_id(call.data["room_id"])
        profile = self._profile(call.data["profile"])
        index = call.data.get("index")

        def mutate(rooms: dict[str, dict[str, Any]]) -> None:
            room = self._require(rooms, room_id)
            profiles = room["profiles"]
            if any(
                candidate["n"].casefold() == profile["n"].casefold()
                and profiles.index(candidate) != index
                for candidate in profiles
            ):
                raise HomeAssistantError("A profile with that name already exists")
            if index is not None:
                if index < len(profiles):
                    profiles[index] = profile
                elif index == len(profiles) and len(profiles) < 5:
                    profiles.append(profile)
                else:
                    raise HomeAssistantError("Profile index is outside the editable range")
            else:
                existing = next((idx for idx, candidate in enumerate(profiles) if candidate["n"].casefold() == profile["n"].casefold()), None)
                if existing is None:
                    if len(profiles) >= 5:
                        raise HomeAssistantError("A room can store up to 5 profiles")
                    profiles.append(profile)
                else:
                    profiles[existing] = profile

        await self._mutate(mutate)

    async def async_remove_profile(self, call: ServiceCall) -> None:
        """Delete one profile by index, stable ID or name."""
        room_id = self._room_id(call.data["room_id"])
        profile_id = str(call.data.get("profile_id") or "").strip()
        index = call.data.get("index")
        name = str(call.data.get("name") or "").strip().casefold()

        def mutate(rooms: dict[str, dict[str, Any]]) -> None:
            room = self._require(rooms, room_id)
            if index is not None:
                if index < 0 or index >= len(room["profiles"]):
                    raise HomeAssistantError("Unknown split-system profile")
                room["profiles"].pop(index)
                return
            profiles = [
                profile
                for profile in room["profiles"]
                if not (profile_id and profile.get("id") == profile_id)
                and not (name and profile["n"].casefold() == name)
            ]
            if len(profiles) == len(room["profiles"]):
                raise HomeAssistantError("Unknown split-system profile")
            room["profiles"] = profiles

        await self._mutate(mutate)

    async def async_enforce(self, room_id: str) -> None:
        """Apply stored limits after any client changes the climate entity."""
        room = self.data[ROOMS].get(room_id)
        if not room:
            return
        state = self.hass.states.get(room["climate"])
        if state is None or state.state in {"off", "unknown", "unavailable"}:
            return
        try:
            requested = float(state.attributes.get("temperature"))
        except (TypeError, ValueError):
            requested = None
        if requested is not None:
            corrected = min(room["maximum_target"], max(room["minimum_target"], requested))
            if corrected != requested:
                await self.hass.services.async_call(
                    "climate",
                    "set_temperature",
                    {"entity_id": room["climate"], "temperature": corrected},
                    blocking=True,
                )
        fan = str(state.attributes.get("fan_mode") or "").lower()
        ceiling = str(room["fan_ceiling"]).lower()
        if ceiling in _FAN_RANK and fan in _FAN_RANK and _FAN_RANK[fan] > _FAN_RANK[ceiling]:
            await self.hass.services.async_call(
                "climate",
                "set_fan_mode",
                {"entity_id": room["climate"], "fan_mode": room["fan_ceiling"]},
                blocking=True,
            )

    @callback
    def _on_state_changed(self, event: Event) -> None:
        """Persist confirmed modes and enforce policy across all HA clients."""
        entity_id = event.data.get("entity_id")
        room_id = self.room_id_for_climate(entity_id)
        state = event.data.get("new_state")
        if not room_id or state is None:
            return
        if state.state == "off":
            old_state = event.data.get("old_state")
            previous_mode = self._normalise_mode(getattr(old_state, "state", None))
            if previous_mode:
                self.hass.async_create_task(self._record_mode(room_id, previous_mode))
            self.hass.async_create_task(self._set_timer(room_id, "cancel", 0))
            return
        mode = self._normalise_mode(state.state)
        if mode:
            self.hass.async_create_task(self._record_mode(room_id, mode))
            self.hass.async_create_task(self.async_enforce(room_id))

    async def _record_mode(self, room_id: str, mode: str) -> None:
        mode = self._normalise_mode(mode)
        if mode is None:
            return

        def mutate(rooms: dict[str, dict[str, Any]]) -> None:
            room = self._require(rooms, room_id)
            if room.get("last_mode") == mode:
                return
            room["last_mode"] = mode

        await self._mutate(mutate)

    async def _seed_active_modes(self) -> None:
        """Capture already-active climate modes after HA restarts or reloads."""

        def mutate(rooms: dict[str, dict[str, Any]]) -> None:
            for room in rooms.values():
                mode = self._current_mode(room["climate"])
                if mode and room.get("last_mode") != mode:
                    room["last_mode"] = mode

        await self._mutate(mutate)

    def _current_mode(self, climate: str) -> str | None:
        state = self.hass.states.get(climate)
        return self._normalise_mode(state.state if state else None)

    @staticmethod
    def _normalise_mode(value: Any) -> str | None:
        mode = str(value or "").strip()
        return mode if mode and mode not in {"off", "unknown", "unavailable"} else None

    def _schedule_deadline(self, room_id: str) -> None:
        self._cancel_deadline(room_id)
        room = self.data[ROOMS].get(room_id)
        deadline = dt_util.parse_datetime(room["deadline"]) if room and room.get("deadline") else None
        if deadline is None:
            return
        if deadline <= dt_util.utcnow():
            self.hass.async_create_task(self._expire(room_id))
            return

        @callback
        def expire(_: Any) -> None:
            self.hass.async_create_task(self._expire(room_id))

        self._deadline_unsub[room_id] = async_track_point_in_utc_time(self.hass, expire, deadline)

    async def _expire(self, room_id: str) -> None:
        room = self.data[ROOMS].get(room_id)
        if not room:
            return
        await self._set_timer(room_id, "cancel", 0)
        await self.hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": room["climate"], "hvac_mode": "off"},
            blocking=True,
        )

    def _cancel_deadline(self, room_id: str) -> None:
        unsubscribe = self._deadline_unsub.pop(room_id, None)
        if unsubscribe:
            unsubscribe()

    async def _mutate(self, mutate: Callable[[dict[str, dict[str, Any]]], None]) -> bool:
        """Persist and publish only an actual state change."""
        async with self._lock:
            next_data = deepcopy(self.data)
            mutate(next_data[ROOMS])
            if next_data[ROOMS] == self.data[ROOMS]:
                return False
            next_data[REVISION] = self.data[REVISION] + 1
            self.data = next_data
            await self._store.async_save(self.data)
        for listener in tuple(self._listeners):
            listener()
        return True

    @staticmethod
    def _room_id(value: Any) -> str:
        room_id = str(value).strip()
        if not room_id or len(room_id) > 80:
            raise HomeAssistantError("room_id is required and must be at most 80 characters")
        return room_id

    @staticmethod
    def _bounds(minimum: Any, maximum: Any) -> tuple[float, float]:
        lower, upper = float(minimum), float(maximum)
        if lower < 0 or upper > 50 or lower >= upper:
            raise HomeAssistantError("minimum_target must be lower than maximum_target")
        return lower, upper

    @staticmethod
    def _ceiling(value: Any) -> str:
        ceiling = str(value).strip().lower()
        if ceiling not in FAN_CEILINGS:
            raise HomeAssistantError("fan_ceiling must be Unrestricted, High, Medium, Low or Quiet")
        return FAN_CEILINGS[ceiling]

    @staticmethod
    def _profile(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise HomeAssistantError("profile must be an object")
        profile = deepcopy(value)
        profile["id"] = str(profile.get("id") or profile.get("n") or profile.get("name") or "").strip()
        profile["n"] = str(profile.get("n") or profile.get("name") or "").strip()
        profile["m"] = str(profile.get("m") or profile.get("mode") or "").strip()
        profile["v"] = int(profile.get("v", 1))
        if not profile["n"] or not profile["m"]:
            raise HomeAssistantError("profile requires name and mode")
        return profile

    @staticmethod
    def _new_room(climate: str) -> dict[str, Any]:
        return {
            "climate": climate,
            "controller": None,
            "vertical_vane": None,
            "horizontal_vane": None,
            "minimum_target": 16.0,
            "maximum_target": 31.0,
            "fan_ceiling": "Quiet",
            "last_mode": "cool",
            "deadline": None,
            "profiles": [],
        }

    def _normalise_store(self, source: Any) -> dict[str, Any]:
        result = {REVISION: 0, ROOMS: {}}
        if not isinstance(source, dict):
            return result
        result[REVISION] = int(source.get(REVISION) or 0)
        for room_id, room in (source.get(ROOMS) or {}).items():
            if not isinstance(room, dict) or not room.get("climate"):
                continue
            try:
                minimum, maximum = self._bounds(
                    room.get("minimum_target", 16),
                    room.get("maximum_target", 31),
                )
                result[ROOMS][str(room_id)] = {
                    **self._new_room(str(room["climate"])),
                    **room,
                    "minimum_target": minimum,
                    "maximum_target": maximum,
                    "fan_ceiling": self._ceiling(room.get("fan_ceiling", "Quiet")),
                    "last_mode": self._normalise_mode(room.get("last_mode")) or "cool",
                    "profiles": [self._profile(profile) for profile in room.get("profiles") or []],
                }
            except (TypeError, ValueError, HomeAssistantError):
                continue
        return result

    @staticmethod
    def _require(rooms: dict[str, dict[str, Any]], room_id: str) -> dict[str, Any]:
        room = rooms.get(room_id)
        if room is None:
            raise HomeAssistantError(f"Unknown split-system room: {room_id}")
        return room


def get_registry(hass: HomeAssistant) -> SplitRegistry:
    """Return the configured singleton."""
    registries = [value for value in hass.data[DOMAIN].values() if isinstance(value, SplitRegistry)]
    if not registries:
        raise HomeAssistantError("Split State Registry is not configured")
    return registries[0]


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register validated services once, before an entry is loaded."""
    if hass.data.setdefault(DOMAIN, {}).get("services"):
        return True
    hass.data[DOMAIN]["services"] = True

    async def configure_room(call: ServiceCall) -> None:
        await get_registry(hass).async_configure_room(call)

    async def remove_room(call: ServiceCall) -> None:
        await get_registry(hass).async_remove_room(call)

    async def update_room(call: ServiceCall) -> None:
        await get_registry(hass).async_update_room(call)

    async def set_timer(call: ServiceCall) -> None:
        await get_registry(hass).async_set_timer(call)

    async def resume_room(call: ServiceCall) -> None:
        await get_registry(hass).async_resume_room(call)

    async def upsert_profile(call: ServiceCall) -> None:
        await get_registry(hass).async_upsert_profile(call)

    async def remove_profile(call: ServiceCall) -> None:
        await get_registry(hass).async_remove_profile(call)

    hass.services.async_register(DOMAIN, SERVICE_REGISTER_ROOM, configure_room, schema=_REGISTER)
    hass.services.async_register(DOMAIN, SERVICE_REMOVE_ROOM, remove_room, schema=_ROOM)
    hass.services.async_register(DOMAIN, SERVICE_SET_SETTINGS, update_room, schema=_SETTINGS)
    hass.services.async_register(DOMAIN, SERVICE_SET_TIMER, set_timer, schema=_TIMER)
    hass.services.async_register(DOMAIN, SERVICE_RESUME_ROOM, resume_room, schema=_ROOM)
    hass.services.async_register(DOMAIN, SERVICE_UPSERT_PROFILE, upsert_profile, schema=_PROFILE)
    hass.services.async_register(DOMAIN, SERVICE_DELETE_PROFILE, remove_profile, schema=_DELETE_PROFILE)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Start one registry entry and expose its sensor."""
    registry = SplitRegistry(hass)
    await registry.async_load()
    hass.data[DOMAIN][entry.entry_id] = registry
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the sensor and timers."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        registry: SplitRegistry = hass.data[DOMAIN].pop(entry.entry_id)
        await registry.async_close()
    return unloaded
