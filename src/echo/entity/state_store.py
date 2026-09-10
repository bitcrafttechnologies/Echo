"""Replaceable storage for ordinary categorized Entity state."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, MutableMapping
from copy import deepcopy
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class StateCategory(StrEnum):
    """Lifetime categories for ordinary Entity state."""

    EPHEMERAL = "ephemeral"
    SESSION = "session"
    PERSISTENT = "persistent"


StateValues = Mapping[str, Any]
EntityStateSnapshot = Mapping[StateCategory, StateValues]


@runtime_checkable
class StateStore(Protocol):
    """Small persistence boundary for Entity state.

    Values are grouped by Entity and lifetime category. Read collections are
    detached snapshots. Callers use ``set`` after changing a value when the
    selected persistence implementation does not retain live object references.
    """

    def get(
        self,
        entity_id: str,
        key: str,
        *,
        category: StateCategory | str = StateCategory.SESSION,
        default: Any = None,
    ) -> Any: ...

    def set(
        self,
        entity_id: str,
        key: str,
        value: Any,
        *,
        category: StateCategory | str = StateCategory.SESSION,
    ) -> None: ...

    def delete(
        self,
        entity_id: str,
        key: str,
        *,
        category: StateCategory | str = StateCategory.SESSION,
    ) -> bool: ...

    def list(
        self,
        entity_id: str,
        *,
        category: StateCategory | str = StateCategory.SESSION,
    ) -> dict[str, Any]: ...

    def snapshot(self, entity_id: str) -> dict[StateCategory, dict[str, Any]]: ...

    def load(self, entity_id: str) -> dict[StateCategory, dict[str, Any]]: ...

    def save(self, entity_id: str, state: EntityStateSnapshot) -> None: ...


def _copy_values(values: Mapping[str, Any]) -> dict[str, Any]:
    """Copy state without rejecting useful opaque or cyclic runtime values."""

    data = dict(values)
    try:
        return deepcopy(data)
    except Exception:
        return data.copy()


def _category(value: StateCategory | str) -> StateCategory:
    try:
        return value if isinstance(value, StateCategory) else StateCategory(value)
    except (TypeError, ValueError) as error:
        choices = ", ".join(category.value for category in StateCategory)
        raise ValueError(f"state category must be one of: {choices}") from error


def _validate_entity_id(entity_id: str) -> None:
    if not isinstance(entity_id, str) or not entity_id:
        raise ValueError("entity id must not be empty")


def _validate_key(key: str) -> None:
    if not isinstance(key, str) or not key:
        raise ValueError("state key must not be empty")


class InMemoryStateStore:
    """Dependency-free StateStore used by default and in tests."""

    def __init__(self) -> None:
        self._entities: dict[str, dict[StateCategory, dict[str, Any]]] = {}

    def get(
        self,
        entity_id: str,
        key: str,
        *,
        category: StateCategory | str = StateCategory.SESSION,
        default: Any = None,
    ) -> Any:
        _validate_entity_id(entity_id)
        _validate_key(key)
        return self._values(entity_id, _category(category), create=False).get(
            key, default
        )

    def set(
        self,
        entity_id: str,
        key: str,
        value: Any,
        *,
        category: StateCategory | str = StateCategory.SESSION,
    ) -> None:
        _validate_entity_id(entity_id)
        _validate_key(key)
        self._values(entity_id, _category(category), create=True)[key] = value

    def delete(
        self,
        entity_id: str,
        key: str,
        *,
        category: StateCategory | str = StateCategory.SESSION,
    ) -> bool:
        _validate_entity_id(entity_id)
        _validate_key(key)
        values = self._values(entity_id, _category(category), create=False)
        if key not in values:
            return False
        del values[key]
        return True

    def list(
        self,
        entity_id: str,
        *,
        category: StateCategory | str = StateCategory.SESSION,
    ) -> dict[str, Any]:
        _validate_entity_id(entity_id)
        return _copy_values(
            self._values(entity_id, _category(category), create=False)
        )

    def snapshot(self, entity_id: str) -> dict[StateCategory, dict[str, Any]]:
        _validate_entity_id(entity_id)
        return {
            category: self.list(entity_id, category=category)
            for category in StateCategory
        }

    def load(self, entity_id: str) -> dict[StateCategory, dict[str, Any]]:
        """Load one Entity's complete detached state snapshot."""

        return self.snapshot(entity_id)

    def save(self, entity_id: str, state: EntityStateSnapshot) -> None:
        """Atomically replace one Entity's categorized state."""

        _validate_entity_id(entity_id)
        supplied: dict[StateCategory, dict[str, Any]] = {}
        for category, values in state.items():
            normalized = _category(category)
            if not isinstance(values, Mapping):
                raise TypeError("state category values must be mappings")
            copied = _copy_values(values)
            for key in copied:
                _validate_key(key)
            supplied[normalized] = copied
        self._entities[entity_id] = {
            category: supplied.get(category, {}) for category in StateCategory
        }

    def _values(
        self,
        entity_id: str,
        category: StateCategory,
        *,
        create: bool,
    ) -> dict[str, Any]:
        if not create:
            return self._entities.get(entity_id, {}).get(category, {})
        entity = self._entities.setdefault(entity_id, {})
        return entity.setdefault(category, {})


class StateView(MutableMapping[str, Any]):
    """MutableMapping compatibility view over one Entity state category."""

    def __init__(
        self,
        store: StateStore,
        entity_id: str,
        category: StateCategory,
    ) -> None:
        self._store = store
        self._entity_id = entity_id
        self._category = category

    def __getitem__(self, key: str) -> Any:
        missing = object()
        value = self._store.get(
            self._entity_id,
            key,
            category=self._category,
            default=missing,
        )
        if value is missing:
            raise KeyError(key)
        return value

    def __setitem__(self, key: str, value: Any) -> None:
        self._store.set(self._entity_id, key, value, category=self._category)

    def __delitem__(self, key: str) -> None:
        if not self._store.delete(self._entity_id, key, category=self._category):
            raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(self._store.list(self._entity_id, category=self._category))

    def __len__(self) -> int:
        return len(self._store.list(self._entity_id, category=self._category))

    def copy(self) -> dict[str, Any]:
        return {
            key: self._store.get(self._entity_id, key, category=self._category)
            for key in self
        }
