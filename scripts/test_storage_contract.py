"""Regression checks for the backend's atomic Store commit boundary."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "ha_component_backend"
    / "storage.py"
)
SPEC = importlib.util.spec_from_file_location("ha_component_backend_storage", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
async_save_mutation = MODULE.async_save_mutation
mutate_versioned_mapping = MODULE.mutate_versioned_mapping


class RecordingStore:
    """Minimal async Store double."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.saved = []

    async def async_save(self, data):
        self.saved.append(data)
        if self.fail:
            raise OSError("disk unavailable")


async def main() -> None:
    current = {"revision": 4, "rooms": {"study": {"mode": "cool"}}}
    store = RecordingStore()
    next_data, changed = await async_save_mutation(
        store,
        current,
        "revision",
        lambda document: document["rooms"]["study"].update(mode="heat"),
    )
    assert changed is True
    assert current == {"revision": 4, "rooms": {"study": {"mode": "cool"}}}
    assert next_data == {"revision": 5, "rooms": {"study": {"mode": "heat"}}}
    assert store.saved == [next_data]

    unchanged, changed = await async_save_mutation(
        store,
        next_data,
        "revision",
        lambda document: document["rooms"]["study"].update(mode="heat"),
    )
    assert changed is False
    assert unchanged is next_data
    assert len(store.saved) == 1

    preference_data, changed = await async_save_mutation(
        store,
        next_data,
        "revision",
        lambda document: document.setdefault("preferences", {}).update(theme="compact"),
        increment_revision=False,
    )
    assert changed is True
    assert preference_data["revision"] == next_data["revision"]
    assert preference_data["preferences"] == {"theme": "compact"}

    versioned = {
        "preferences": {"theme": "compact"},
        "preference_revisions": {"theme": 1},
    }
    mutate_versioned_mapping(
        versioned,
        "preferences",
        "preference_revisions",
        "theme",
        1,
        lambda values: values.pop("theme"),
    )
    assert versioned == {
        "preferences": {},
        "preference_revisions": {"theme": 2},
    }
    mutate_versioned_mapping(
        versioned,
        "preferences",
        "preference_revisions",
        "theme",
        2,
        lambda values: values.update(theme="spacious"),
    )
    assert versioned == {
        "preferences": {"theme": "spacious"},
        "preference_revisions": {"theme": 3},
    }
    mutate_versioned_mapping(
        versioned,
        "preferences",
        "preference_revisions",
        "theme",
        3,
        lambda values: values.update(theme="spacious"),
    )
    assert versioned["preference_revisions"]["theme"] == 3

    failing_current = {"revision": 9, "rooms": {"garage": {"mode": "off"}}}
    failing_store = RecordingStore(fail=True)
    try:
        await async_save_mutation(
            failing_store,
            failing_current,
            "revision",
            lambda document: document["rooms"]["garage"].update(mode="cool"),
        )
    except OSError:
        pass
    else:
        raise AssertionError("A failed Store write must propagate")
    assert failing_current == {
        "revision": 9,
        "rooms": {"garage": {"mode": "off"}},
    }
    assert failing_store.saved[0]["revision"] == 10


asyncio.run(main())
print("Storage contract passed: no-op, independent and monotonic revisions, committed and failed writes remain atomic")
