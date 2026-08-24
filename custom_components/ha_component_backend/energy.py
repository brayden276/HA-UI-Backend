"""Backend Energy profile coordinator and coalesced daily summaries."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable

from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.components.recorder.util import get_instance
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .contracts import (
    DEFAULT_ENERGY_PROFILE,
    ContractError,
    build_energy_summary,
    energy_power_snapshot,
    finite_number,
    normalise_profile,
    profile_entity_ids,
    profile_key,
)
from .diagnostics import log_handled_error, log_unexpected_error
from .split_registry import SplitRegistry


class EnergyManager:
    """Own Energy source resolution, cache coalescing and sensor updates."""

    def __init__(self, hass: HomeAssistant, registry: SplitRegistry) -> None:
        self.hass = hass
        self.registry = registry
        self._cache: dict[tuple[str, int, str], tuple[float, dict[str, Any]]] = {}
        self._inflight: dict[tuple[str, int, str], asyncio.Task[dict[str, Any]]] = {}
        self._listeners: list[Callable[[], None]] = []
        self._profile_entities: set[str] = set()
        self._registry_unsub = registry.async_add_listener(self._profile_changed)
        self._state_unsub = hass.bus.async_listen("state_changed", self._state_changed)
        self._profile_changed()

    async def async_close(self) -> None:
        """Release listeners and cancel incomplete recorder work."""
        self._registry_unsub()
        self._state_unsub()
        tasks = list(self._inflight.values())
        for task in tasks:
            task.cancel()
        self._inflight.clear()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._listeners.clear()

    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Subscribe a derived sensor to source and profile updates."""
        self._listeners.append(listener)

        @callback
        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def profile_snapshot(self, profile_id: str = DEFAULT_ENERGY_PROFILE) -> dict[str, Any]:
        """Return one validated Energy profile and its preference revision."""
        snapshot = self.registry.preference_snapshot(profile_key("energy", profile_id))
        if not snapshot["found"]:
            return {"found": False, "profile": None, "revision": snapshot["revision"]}
        try:
            profile = normalise_profile("energy", profile_id, snapshot["value"])
        except ContractError as err:
            raise HomeAssistantError(str(err)) from err
        return {"found": True, "profile": profile, "revision": snapshot["revision"]}

    def power_snapshot(self, profile_id: str = DEFAULT_ENERGY_PROFILE) -> dict[str, Any]:
        """Return current canonical power values and source availability."""
        try:
            profile_result = self.profile_snapshot(profile_id)
        except HomeAssistantError as err:
            log_handled_error(
                "energy power snapshot",
                err,
                context={"profile_id": profile_id},
            )
            return {
                "profile": profile_id,
                "revision": 0,
                "house_w": None,
                "solar_w": None,
                "grid_w": None,
                "sources": [],
                "error": str(err),
            }
        profile = profile_result["profile"]
        if not profile:
            return {
                "profile": profile_id,
                "revision": profile_result["revision"],
                "house_w": None,
                "solar_w": None,
                "grid_w": None,
                "sources": [],
                "error": None,
            }
        values = {entity_id: self._state_number(entity_id) for entity_id in profile_entity_ids(profile)}
        return {
            "profile": profile_id,
            "revision": profile_result["revision"],
            **energy_power_snapshot(profile, values),
            "sources": sorted(profile_entity_ids(profile)),
            "error": None,
        }

    async def async_day(self, profile_id: str, day_value: str) -> dict[str, Any]:
        """Return one cached daily Energy payload with duplicate work coalesced."""
        profile_id = str(profile_id or "").strip().lower()
        profile_result = self.profile_snapshot(profile_id)
        if not profile_result["found"]:
            raise HomeAssistantError(f"Unknown Energy profile: {profile_id}")
        day, start, end, is_today = self._day_range(day_value)
        cache_key = (profile_id, profile_result["revision"], day)
        now = dt_util.utcnow().timestamp()
        ttl = 120 if is_today else 3600
        cached = self._cache.get(cache_key)
        if cached and now - cached[0] < ttl:
            return {**deepcopy(cached[1]), "cached": True, "stale": False}
        if cache_key not in self._inflight:
            self._inflight[cache_key] = self.hass.async_create_task(
                self._async_build_day(profile_result["profile"], day, start, end),
                f"{profile_id} Energy summary for {day}",
            )
        try:
            result = await self._inflight[cache_key]
        except Exception as err:
            if cached:
                log_unexpected_error(
                    "energy day refresh",
                    err,
                    context={
                        "profile_id": profile_id,
                        "day": day,
                        "fallback": "stale_cache",
                    },
                )
                return {**deepcopy(cached[1]), "cached": True, "stale": True}
            raise
        finally:
            self._inflight.pop(cache_key, None)
        self._cache = {
            key: value
            for key, value in self._cache.items()
            if key[0] != profile_id or key[1] == profile_result["revision"]
        }
        self._cache[cache_key] = (now, deepcopy(result))
        return {**result, "cached": False, "stale": False}

    async def _async_build_day(
        self,
        profile: dict[str, Any],
        day: str,
        start: datetime,
        end: datetime,
    ) -> dict[str, Any]:
        entity_ids = profile_entity_ids(profile)
        statistics = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            dt_util.as_utc(start),
            dt_util.as_utc(end),
            entity_ids,
            "5minute",
            None,
            {"change", "mean"},
        )
        values = {entity_id: self._state_number(entity_id) for entity_id in entity_ids}
        return build_energy_summary(
            profile,
            day,
            round(start.timestamp() * 1000),
            round(end.timestamp() * 1000),
            values,
            statistics,
            datetime.now(timezone.utc).isoformat(),
        )

    def _day_range(self, value: str) -> tuple[str, datetime, datetime, bool]:
        try:
            selected = date.fromisoformat(str(value))
        except ValueError as err:
            raise HomeAssistantError("day must use YYYY-MM-DD") from err
        zone = dt_util.get_time_zone(self.hass.config.time_zone)
        today = dt_util.now().astimezone(zone).date()
        if selected > today:
            raise HomeAssistantError("day cannot be in the future")
        start = datetime.combine(selected, time.min, tzinfo=zone)
        return selected.isoformat(), start, start + timedelta(days=1), selected == today

    def _state_number(self, entity_id: str) -> float | None:
        state = self.hass.states.get(entity_id)
        if state is None or str(state.state).lower() in {"unknown", "unavailable"}:
            return None
        return finite_number(state.state)

    @callback
    def _profile_changed(self) -> None:
        try:
            result = self.profile_snapshot()
            self._profile_entities = profile_entity_ids(result["profile"]) if result["profile"] else set()
        except HomeAssistantError as err:
            log_handled_error(
                "energy profile refresh",
                err,
                context={"profile_id": DEFAULT_ENERGY_PROFILE},
            )
            self._profile_entities = set()
        self._cache.clear()
        for listener in tuple(self._listeners):
            listener()

    @callback
    def _state_changed(self, event: Event) -> None:
        if event.data.get("entity_id") not in self._profile_entities:
            return
        for listener in tuple(self._listeners):
            listener()
