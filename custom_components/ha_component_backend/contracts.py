"""Validated reusable dashboard profile and energy summary contracts."""

from __future__ import annotations

from copy import deepcopy
import math
import re
from typing import Any, Mapping

PROFILE_KINDS = {"energy", "security"}
PROFILE_PREFIX = "dashboard-profile"
DEFAULT_ENERGY_PROFILE = "household-energy"

_ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_PROFILE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ContractError(ValueError):
    """Raised when dashboard profile data violates the public contract."""


def profile_key(kind: str, profile_id: str) -> str:
    """Return the private Store key for one validated profile."""
    normalised_kind = str(kind or "").strip().lower()
    normalised_id = str(profile_id or "").strip().lower()
    if normalised_kind not in PROFILE_KINDS:
        raise ContractError("kind must be energy or security")
    if not _PROFILE_ID.fullmatch(normalised_id):
        raise ContractError("profile_id must use lowercase kebab-case")
    return f"{PROFILE_PREFIX}.{normalised_kind}.{normalised_id}"


def _entity(value: Any, field: str, *, required: bool = True) -> str | None:
    if value in (None, "") and not required:
        return None
    entity_id = str(value or "").strip().lower()
    if not _ENTITY_ID.fullmatch(entity_id):
        raise ContractError(f"{field} must be a valid entity ID")
    return entity_id


def _entities(value: Any, field: str, *, required: bool = True) -> list[str]:
    if value in (None, "") and not required:
        return []
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for index, item in enumerate(values):
        entity_id = _entity(item, f"{field}[{index}]")
        if entity_id not in result:
            result.append(entity_id)
    if required and not result:
        raise ContractError(f"{field} requires at least one entity ID")
    return result


def normalise_profile(kind: str, profile_id: str, value: Any) -> dict[str, Any]:
    """Validate and canonicalise a reusable Energy or Security profile."""
    normalised_kind = str(kind or "").strip().lower()
    normalised_id = str(profile_id or "").strip().lower()
    key = profile_key(normalised_kind, normalised_id)
    del key
    if not isinstance(value, dict):
        raise ContractError("profile must be an object")
    if normalised_kind == "energy":
        return _normalise_energy_profile(normalised_id, value)
    return _normalise_security_profile(normalised_id, value)


def _normalise_energy_profile(profile_id: str, value: dict[str, Any]) -> dict[str, Any]:
    power = value.get("power")
    energy = value.get("energy")
    if not isinstance(power, dict) or not isinstance(energy, dict):
        raise ContractError("energy profile requires power and energy objects")
    return {
        "id": profile_id,
        "version": 1,
        "power": {
            "grid": _entity(power.get("grid"), "power.grid"),
            "solar": _entities(power.get("solar"), "power.solar"),
            "house": _entity(power.get("house"), "power.house", required=False),
        },
        "energy": {
            "imported": _entity(energy.get("imported"), "energy.imported"),
            "exported": _entity(energy.get("exported"), "energy.exported"),
            "generated": _entities(energy.get("generated"), "energy.generated"),
            "consumed": _entity(energy.get("consumed"), "energy.consumed", required=False),
        },
    }


def _normalise_security_profile(profile_id: str, value: dict[str, Any]) -> dict[str, Any]:
    include = _entities(value.get("include_entities"), "include_entities", required=False)
    exclude = _entities(value.get("exclude_entities"), "exclude_entities", required=False)
    raw_area_ids = value.get("area_ids", [])
    if not isinstance(raw_area_ids, list):
        raise ContractError("area_ids must be a list")
    area_ids = []
    for area in raw_area_ids:
        area_id = str(area or "").strip().lower()
        if len(area_id) > 64 or any(character.isspace() for character in area_id):
            raise ContractError("area_ids entries must be valid Home Assistant area IDs")
        if area_id and area_id not in area_ids:
            area_ids.append(area_id)
    mappings = value.get("mappings", {})
    if not isinstance(mappings, dict):
        raise ContractError("mappings must be an object")
    clean_mappings: dict[str, str] = {}
    for role, entity_id in mappings.items():
        clean_role = str(role or "").strip().lower()
        if not clean_role or len(clean_role) > 80:
            raise ContractError("mapping role must be between 1 and 80 characters")
        clean_mappings[clean_role] = _entity(entity_id, f"mappings.{clean_role}") or ""
    viewer = value.get("viewer", {})
    if not isinstance(viewer, dict):
        raise ContractError("viewer must be an object")
    stream = str(viewer.get("preferred_stream") or "auto").strip().lower()
    if stream not in {"auto", "snapshot", "live"}:
        raise ContractError("viewer.preferred_stream must be auto, snapshot or live")
    return {
        "id": profile_id,
        "version": 1,
        "area_ids": area_ids,
        "include_entities": include,
        "exclude_entities": exclude,
        "mappings": clean_mappings,
        "viewer": {"preferred_stream": stream},
    }


def profile_entity_ids(profile: Mapping[str, Any]) -> set[str]:
    """Return every entity ID referenced by a normalised profile."""
    result: set[str] = set()
    for section in ("power", "energy"):
        values = profile.get(section)
        if not isinstance(values, Mapping):
            continue
        for value in values.values():
            if isinstance(value, str):
                result.add(value)
            elif isinstance(value, list):
                result.update(item for item in value if isinstance(item, str))
    for field in ("include_entities", "exclude_entities"):
        value = profile.get(field)
        if isinstance(value, list):
            result.update(item for item in value if isinstance(item, str))
    mappings = profile.get("mappings")
    if isinstance(mappings, Mapping):
        result.update(item for item in mappings.values() if isinstance(item, str))
    return result


def finite_number(value: Any) -> float | None:
    """Return one finite float while preserving missing/unavailable as None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def energy_power_snapshot(
    profile: Mapping[str, Any],
    values: Mapping[str, float | None],
) -> dict[str, float | None]:
    """Calculate canonical grid, solar and house power without zero coercion."""
    power = profile.get("power") or {}
    grid = finite_number(values.get(power.get("grid")))
    solar_values = [finite_number(values.get(entity_id)) for entity_id in power.get("solar") or []]
    solar = sum(solar_values) if solar_values and all(value is not None for value in solar_values) else None
    house_entity = power.get("house")
    house = finite_number(values.get(house_entity)) if house_entity else None
    if house_entity is None and grid is not None and solar is not None:
        house = max(0.0, grid + solar)
    return {"grid_w": grid, "solar_w": solar, "house_w": house}


def _row_timestamp(row: Mapping[str, Any]) -> int | None:
    value = finite_number(row.get("start"))
    if value is None:
        return None
    return round(value if value > 10_000_000_000 else value * 1000)


def _series(rows: list[Mapping[str, Any]]) -> list[dict[str, float | int]]:
    result = []
    for row in rows:
        timestamp = _row_timestamp(row)
        mean = finite_number(row.get("mean"))
        if timestamp is not None and mean is not None:
            result.append({"t": timestamp, "v": mean})
    return sorted(result, key=lambda item: item["t"])


def _combine_series(series: list[list[dict[str, float | int]]], operation) -> list[dict[str, float | int]]:
    if not series:
        return []
    maps = [{int(item["t"]): float(item["v"]) for item in rows} for rows in series]
    timestamps = set(maps[0])
    for values in maps[1:]:
        timestamps.intersection_update(values)
    return [
        {"t": timestamp, "v": operation([values[timestamp] for values in maps])}
        for timestamp in sorted(timestamps)
    ]


def _change(rows: list[Mapping[str, Any]]) -> float | None:
    changes = [finite_number(row.get("change")) for row in rows]
    available = [value for value in changes if value is not None]
    return sum(available) if available else None


def build_energy_summary(
    profile: Mapping[str, Any],
    day: str,
    start_ms: int,
    end_ms: int,
    current_values: Mapping[str, float | None],
    statistics: Mapping[str, list[Mapping[str, Any]]],
    updated_at: str,
) -> dict[str, Any]:
    """Build the stable daily Energy response used by every dashboard card."""
    power = profile.get("power") or {}
    energy = profile.get("energy") or {}
    current = energy_power_snapshot(profile, current_values)

    imported = _change(statistics.get(energy.get("imported"), []))
    exported = _change(statistics.get(energy.get("exported"), []))
    generated_values = [_change(statistics.get(entity_id, [])) for entity_id in energy.get("generated") or []]
    generated = sum(generated_values) if generated_values and all(value is not None for value in generated_values) else None
    consumed_entity = energy.get("consumed")
    consumed = _change(statistics.get(consumed_entity, [])) if consumed_entity else None
    if consumed_entity is None and imported is not None and exported is not None and generated is not None:
        consumed = max(0.0, imported + generated - exported)

    grid_series = _series(statistics.get(power.get("grid"), []))
    solar_parts = [_series(statistics.get(entity_id, [])) for entity_id in power.get("solar") or []]
    solar_series = _combine_series(solar_parts, sum) if solar_parts and all(solar_parts) else []
    house_entity = power.get("house")
    house_series = _series(statistics.get(house_entity, [])) if house_entity else []
    if not house_entity and grid_series and solar_series:
        house_series = _combine_series([grid_series, solar_series], lambda values: max(0.0, sum(values)))

    energy_sources = [
        energy.get("imported"),
        energy.get("exported"),
        *(energy.get("generated") or []),
        *([consumed_entity] if consumed_entity else []),
    ]
    available_sources = sum(1 for entity_id in energy_sources if _change(statistics.get(entity_id, [])) is not None)
    coverage = available_sources / len(energy_sources) if energy_sources else 0.0

    return {
        "profile": profile.get("id"),
        "day": day,
        "range": {"start": start_ms, "end": end_ms},
        **current,
        "imported_kwh": imported,
        "exported_kwh": exported,
        "generated_kwh": generated,
        "consumed_kwh": consumed,
        "coverage": round(coverage, 4),
        "series": {
            "house": deepcopy(house_series),
            "solar": deepcopy(solar_series),
            "grid": deepcopy(grid_series),
        },
        "updated_at": updated_at,
    }
