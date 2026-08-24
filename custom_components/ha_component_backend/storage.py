"""Small, testable helpers for atomic Home Assistant Store mutations."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Awaitable, Callable, Protocol


class AsyncStore(Protocol):
    """The subset of Home Assistant Store used by this integration."""

    def async_save(self, data: dict[str, Any]) -> Awaitable[None]:
        """Persist a complete Store document."""


def mutate_versioned_mapping(
    document: dict[str, Any],
    values_key: str,
    revisions_key: str,
    key: str,
    current_revision: int,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    """Mutate one mapping key and retain a monotonic per-key revision."""
    values = document[values_key]
    before_found = key in values
    before_value = deepcopy(values.get(key))
    mutate(values)
    after_found = key in values
    after_value = values.get(key)
    if before_found == after_found and before_value == after_value:
        return
    # A removed key keeps a revision tombstone. Otherwise a delete/recreate
    # cycle could make a stale revision valid again (the ABA problem).
    document[revisions_key][key] = current_revision + 1


async def async_save_mutation(
    store: AsyncStore,
    current: dict[str, Any],
    revision_key: str,
    mutate: Callable[[dict[str, Any]], None],
    *,
    increment_revision: bool = True,
) -> tuple[dict[str, Any], bool]:
    """Build and save a new document without publishing uncommitted state.

    The caller owns the mutation lock and must assign the returned document
    before releasing it. If ``async_save`` raises, ``current`` remains intact.
    """

    next_data = deepcopy(current)
    mutate(next_data)
    if next_data == current:
        return current, False
    if increment_revision:
        next_data[revision_key] = int(current.get(revision_key) or 0) + 1
    await store.async_save(next_data)
    return next_data, True
