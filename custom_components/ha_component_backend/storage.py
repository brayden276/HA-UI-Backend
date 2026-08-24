"""Small, testable helpers for atomic Home Assistant Store mutations."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Awaitable, Callable, Protocol


class AsyncStore(Protocol):
    """The subset of Home Assistant Store used by this integration."""

    def async_save(self, data: dict[str, Any]) -> Awaitable[None]:
        """Persist a complete Store document."""


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
