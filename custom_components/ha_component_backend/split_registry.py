"""Durable, room-keyed state and services for Split System Components."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta
import json
from typing import Any, Callable

from homeassistant.core import Context, Event, HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    EVENT_PREFERENCES_UPDATED,
    FAN_CEILINGS,
    MAX_PREFERENCE_BYTES,
    PREFERENCES,
    PREFERENCE_REVISIONS,
    REVISION,
    ROOMS,
    STORE_KEY,
    STORE_VERSION,
)
from .storage import async_save_mutation

_FAN_RANK = {"quiet": 0, "low": 1, "medium": 2, "high": 3, "auto": 4}


class PreferenceConflict(HomeAssistantError):
    """Raised when a client writes against an obsolete store revision."""


class SplitRegistry:
    """Serialize every room mutation into one durable HA Store document."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store = Store(hass, STORE_VERSION, STORE_KEY)
        self.data: dict[str, Any] = {
            REVISION: 0,
            ROOMS: {},
            PREFERENCES: {},
            PREFERENCE_REVISIONS: {},
        }
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

    def preference_snapshot(self, key: str) -> dict[str, Any]:
        """Return one preference value without exposing all preferences in state."""
        key = self._preference_key(key)
        found = key in self.data[PREFERENCES]
        return {
            "key": key,
            "found": found,
            "value": deepcopy(self.data[PREFERENCES].get(key)),
            REVISION: self.data[PREFERENCE_REVISIONS].get(key, 0),
        }

    async def async_update_preference(
        self,
        key: str,
        value: Any,
        expected_revision: int | None = None,
        context: Context | None = None,
    ) -> dict[str, Any]:
        """Persist one bounded JSON preference with optimistic concurrency."""
        key = self._preference_key(key)
        value = self._preference_value(value)

        def mutate(preferences: dict[str, Any]) -> None:
            preferences[key] = value

        changed = await self._mutate_preference(
            key,
            mutate,
            expected_revision,
            removed=False,
            context=context,
        )
        return {**self.preference_snapshot(key), "changed": changed}

    async def async_remove_preference(
        self,
        key: str,
        expected_revision: int | None = None,
        context: Context | None = None,
    ) -> dict[str, Any]:
        """Remove one preference while preserving unrelated stored data."""
        key = self._preference_key(key)

        def mutate(preferences: dict[str, Any]) -> None:
            preferences.pop(key, None)

        changed = await self._mutate_preference(
            key,
            mutate,
            expected_revision,
            removed=True,
            context=context,
        )
        return {**self.preference_snapshot(key), "changed": changed}

    def room_id_for_climate(self, entity_id: str) -> str | None:
        """Look up a stable room key from its climate entity."""
        return next(
            (key for key, room in self.data[ROOMS].items() if room["climate"] == entity_id),
            None,
        )

    async def async_configure_room(self, call: ServiceCall) -> dict[str, Any]:
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

        changed = await self._mutate(mutate)
        self._schedule_deadline(room_id)
        enforcement = await self._enforce_after_commit(room_id, call.context)
        return self._room_result(room_id, changed, enforcement=enforcement)

    async def async_remove_room(self, call: ServiceCall) -> dict[str, Any]:
        """Remove one room and every stored profile."""
        room_id = self._room_id(call.data["room_id"])

        def mutate(rooms: dict[str, dict[str, Any]]) -> None:
            self._require(rooms, room_id)
            rooms.pop(room_id)

        changed = await self._mutate(mutate)
        self._cancel_deadline(room_id)
        return self._room_result(room_id, changed, removed=True)

    async def async_update_room(self, call: ServiceCall) -> dict[str, Any]:
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

        changed = await self._mutate(mutate)
        self._schedule_deadline(room_id)
        enforcement = await self._enforce_after_commit(room_id, call.context)
        return self._room_result(room_id, changed, enforcement=enforcement)

    async def async_set_timer(self, call: ServiceCall) -> dict[str, Any]:
        """Set, extend or cancel a restart-safe deadline."""
        room_id = self._room_id(call.data["room_id"])
        changed = await self._set_timer(
            room_id,
            call.data["operation"],
            call.data["minutes"],
        )
        return self._room_result(room_id, changed)

    async def async_resume_room(self, call: ServiceCall) -> dict[str, Any]:
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
            context=call.context,
        )
        return self._room_result(room_id, False, resumed_mode=mode)

    async def _set_timer(self, room_id: str, operation: str, minutes: int) -> bool:
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

        changed = await self._mutate(mutate)
        self._schedule_deadline(room_id)
        return changed

    async def async_upsert_profile(self, call: ServiceCall) -> dict[str, Any]:
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

        changed = await self._mutate(mutate)
        return self._room_result(room_id, changed)

    async def async_remove_profile(self, call: ServiceCall) -> dict[str, Any]:
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

        changed = await self._mutate(mutate)
        return self._room_result(room_id, changed)

    async def async_enforce(
        self,
        room_id: str,
        context: Context | None = None,
    ) -> dict[str, Any]:
        """Apply stored limits after any client changes the climate entity."""
        room = self.data[ROOMS].get(room_id)
        if not room:
            return {"status": "skipped", "reason": "unknown_room", "corrections": []}
        state = self.hass.states.get(room["climate"])
        if state is None or state.state in {"off", "unknown", "unavailable"}:
            return {"status": "skipped", "reason": "inactive", "corrections": []}
        corrections: list[str] = []
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
                    context=context,
                )
                corrections.append("temperature")
        fan = str(state.attributes.get("fan_mode") or "").lower()
        ceiling = str(room["fan_ceiling"]).lower()
        if ceiling in _FAN_RANK and fan in _FAN_RANK and _FAN_RANK[fan] > _FAN_RANK[ceiling]:
            await self.hass.services.async_call(
                "climate",
                "set_fan_mode",
                {"entity_id": room["climate"], "fan_mode": room["fan_ceiling"]},
                blocking=True,
                context=context,
            )
            corrections.append("fan_mode")
        return {
            "status": "applied" if corrections else "not_required",
            "corrections": corrections,
        }

    async def _enforce_after_commit(
        self,
        room_id: str,
        context: Context | None,
    ) -> dict[str, Any]:
        """Report the rare partial outcome where persistence beats enforcement."""
        try:
            return await self.async_enforce(room_id, context)
        except Exception as err:
            raise HomeAssistantError(
                "Room settings were saved, but the current climate state could not "
                "be brought within the updated policy"
            ) from err

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
            next_data, changed = await async_save_mutation(
                self._store,
                self.data,
                REVISION,
                lambda document: mutate(document[ROOMS]),
            )
            if not changed:
                return False
            self.data = next_data
        self._notify_listeners()
        return True

    async def _mutate_preference(
        self,
        key: str,
        mutate: Callable[[dict[str, Any]], None],
        expected_revision: int | None,
        *,
        removed: bool,
        context: Context | None,
    ) -> bool:
        """Serialize, persist and publish one preference mutation."""
        async with self._lock:
            current_revision = self.data[PREFERENCE_REVISIONS].get(key, 0)
            if expected_revision is not None and expected_revision != current_revision:
                raise PreferenceConflict(
                    f"Preference revision changed from {expected_revision} to {current_revision}"
                )

            def mutate_document(document: dict[str, Any]) -> None:
                before_found = key in document[PREFERENCES]
                before_value = deepcopy(document[PREFERENCES].get(key))
                mutate(document[PREFERENCES])
                after_found = key in document[PREFERENCES]
                after_value = document[PREFERENCES].get(key)
                if before_found == after_found and before_value == after_value:
                    return
                if after_found:
                    document[PREFERENCE_REVISIONS][key] = current_revision + 1
                else:
                    document[PREFERENCE_REVISIONS].pop(key, None)

            next_data, changed = await async_save_mutation(
                self._store,
                self.data,
                REVISION,
                mutate_document,
                increment_revision=False,
            )
            if not changed:
                return False
            self.data = next_data
        preference_revision = self.data[PREFERENCE_REVISIONS].get(key, 0)
        self.hass.bus.async_fire(
            EVENT_PREFERENCES_UPDATED,
            {
                "key": key,
                REVISION: preference_revision,
                "removed": removed,
            },
            context=context,
        )
        return True

    @callback
    def _notify_listeners(self) -> None:
        """Publish a committed Store revision to local entities."""
        for listener in tuple(self._listeners):
            listener()

    def _room_result(self, room_id: str, changed: bool, **details: Any) -> dict[str, Any]:
        """Build a stable, JSON-serialisable response for mutation services."""
        room = self.data[ROOMS].get(room_id)
        return {
            "room_id": room_id,
            "changed": changed,
            REVISION: self.data[REVISION],
            "room": deepcopy(room),
            **details,
        }

    @staticmethod
    def _preference_key(value: Any) -> str:
        key = str(value or "").strip()
        if not key or len(key) > 120 or any(character in key for character in "\r\n"):
            raise HomeAssistantError(
                "Preference key is required, must be one line and at most 120 characters"
            )
        return key

    @staticmethod
    def _preference_value(value: Any) -> Any:
        try:
            encoded = json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as err:
            raise HomeAssistantError("Preference value must be valid JSON") from err
        if len(encoded) > MAX_PREFERENCE_BYTES:
            raise HomeAssistantError(
                f"Preference value must not exceed {MAX_PREFERENCE_BYTES} bytes"
            )
        return deepcopy(value)

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
        result = {
            REVISION: 0,
            ROOMS: {},
            PREFERENCES: {},
            PREFERENCE_REVISIONS: {},
        }
        if not isinstance(source, dict):
            return result
        try:
            result[REVISION] = max(0, int(source.get(REVISION) or 0))
        except (TypeError, ValueError):
            result[REVISION] = 0
        stored_preferences = source.get(PREFERENCES)
        if not isinstance(stored_preferences, dict):
            stored_preferences = {}
        stored_preference_revisions = source.get(PREFERENCE_REVISIONS)
        if not isinstance(stored_preference_revisions, dict):
            stored_preference_revisions = {}
        for key, value in stored_preferences.items():
            try:
                normalised_key = self._preference_key(key)
                result[PREFERENCES][normalised_key] = self._preference_value(value)
                result[PREFERENCE_REVISIONS][normalised_key] = max(
                    1,
                    int(stored_preference_revisions.get(key) or 1),
                )
            except (TypeError, ValueError, HomeAssistantError):
                continue
        stored_rooms = source.get(ROOMS)
        if not isinstance(stored_rooms, dict):
            stored_rooms = {}
        for room_id, room in stored_rooms.items():
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
