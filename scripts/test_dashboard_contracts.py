"""Regression tests for reusable dashboard profiles and Energy calculations."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "custom_components" / "ha_component_backend" / "contracts.py"
SPEC = spec_from_file_location("ha_component_backend_contracts", PATH)
assert SPEC and SPEC.loader
CONTRACTS = module_from_spec(SPEC)
SPEC.loader.exec_module(CONTRACTS)


def energy_profile():
    return CONTRACTS.normalise_profile(
        "energy",
        "household-energy",
        {
            "power": {
                "grid": "sensor.grid_power",
                "solar": ["sensor.inverter_one", "sensor.inverter_two"],
            },
            "energy": {
                "imported": "sensor.grid_import",
                "exported": "sensor.grid_export",
                "generated": ["sensor.solar_one", "sensor.solar_two"],
            },
        },
    )


profile = energy_profile()
assert profile["power"]["solar"] == ["sensor.inverter_one", "sensor.inverter_two"]
security = CONTRACTS.normalise_profile(
    "security",
    " HOUSEHOLD-SECURITY ",
    {
        "area_ids": ["Garage", "garage", "front_yard"],
        "include_entities": ["camera.Front", "camera.front"],
        "exclude_entities": ["switch.private_mode"],
        "mappings": {"ENTRY_CONTROL:BINARY_SENSOR.GARAGE": "button.garage_trigger"},
        "viewer": {"preferred_stream": "LIVE"},
    },
)
assert security == {
    "id": "household-security",
    "version": 1,
    "area_ids": ["garage", "front_yard"],
    "include_entities": ["camera.front"],
    "exclude_entities": ["switch.private_mode"],
    "mappings": {"entry_control:binary_sensor.garage": "button.garage_trigger"},
    "viewer": {"preferred_stream": "live"},
}
assert CONTRACTS.profile_entity_ids(security) == {
    "camera.front",
    "switch.private_mode",
    "button.garage_trigger",
}
try:
    CONTRACTS.normalise_profile("security", "household-security", {"area_ids": "garage"})
except CONTRACTS.ContractError:
    pass
else:
    raise AssertionError("Security area_ids must be a list, not an iterable string")
for invalid_security in (
    {"area_ids": ["front yard"]},
    {"viewer": {"preferred_stream": "continuous"}},
    {"mappings": []},
):
    try:
        CONTRACTS.normalise_profile("security", "household-security", invalid_security)
    except CONTRACTS.ContractError:
        pass
    else:
        raise AssertionError(f"invalid Security profile must be rejected: {invalid_security}")
assert CONTRACTS.profile_key("security", "household-security") == (
    "dashboard-profile.security.household-security"
)

power = CONTRACTS.energy_power_snapshot(
    profile,
    {
        "sensor.grid_power": 500,
        "sensor.inverter_one": 700,
        "sensor.inverter_two": 300,
    },
)
assert power == {"grid_w": 500.0, "solar_w": 1000.0, "house_w": 1500.0}

unavailable = CONTRACTS.energy_power_snapshot(
    profile,
    {
        "sensor.grid_power": 500,
        "sensor.inverter_one": None,
        "sensor.inverter_two": 300,
    },
)
assert unavailable["solar_w"] is None
assert unavailable["house_w"] is None

zero = CONTRACTS.energy_power_snapshot(
    profile,
    {
        "sensor.grid_power": 0,
        "sensor.inverter_one": 0,
        "sensor.inverter_two": 0,
    },
)
assert zero == {"grid_w": 0.0, "solar_w": 0.0, "house_w": 0.0}

statistics = {
    "sensor.grid_import": [{"start": 1_780_000_000, "change": 4.0}],
    "sensor.grid_export": [{"start": 1_780_000_000, "change": 1.0}],
    "sensor.solar_one": [{"start": 1_780_000_000, "change": 3.0}],
    "sensor.solar_two": [{"start": 1_780_000_000, "change": 2.0}],
    "sensor.grid_power": [{"start": 1_780_000_000, "mean": 500.0}],
    "sensor.inverter_one": [{"start": 1_780_000_000, "mean": 700.0}],
    "sensor.inverter_two": [{"start": 1_780_000_000, "mean": 300.0}],
}
summary = CONTRACTS.build_energy_summary(
    profile,
    "2026-08-24",
    1_780_000_000_000,
    1_780_086_400_000,
    {
        "sensor.grid_power": 500,
        "sensor.inverter_one": 700,
        "sensor.inverter_two": 300,
    },
    statistics,
    "2026-08-24T01:00:00+00:00",
)
assert summary["generated_kwh"] == 5.0
assert summary["consumed_kwh"] == 8.0
assert summary["coverage"] == 1.0
assert summary["series"]["solar"] == [{"t": 1_780_000_000_000, "v": 1000.0}]
assert summary["series"]["house"] == [{"t": 1_780_000_000_000, "v": 1500.0}]

try:
    CONTRACTS.normalise_profile(
        "energy",
        "household-energy",
        {"power": {"grid": "not-an-entity", "solar": []}, "energy": {}},
    )
except CONTRACTS.ContractError:
    pass
else:
    raise AssertionError("invalid entity IDs must be rejected")

print("Dashboard contract tests passed: profiles, availability, totals, coverage and series")
