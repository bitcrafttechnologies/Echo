"""SQLite persistence for Entity-scoped persistent state."""

from __future__ import annotations

from base64 import b64decode, b64encode
import json
import math
from os import PathLike, fspath
import sqlite3
from threading import RLock
from typing import Any

from echo.entity.state_store import (
    EntityStateSnapshot,
    InMemoryStateStore,
    StateCategory,
)


SCHEMA_VERSION = 1
SERIALIZATION_VERSION = 1
_SERIALIZATION_FORMAT = "echo-state-json"


class StateStoreError(Exception):
    """Base failure raised by the SQLite state implementation."""


class StateSerializationError(StateStoreError, ValueError):
    """A state value cannot be represented by the safe state codec."""


class StateStoreSchemaError(StateStoreError):
    """The database schema or serialization version is incompatible."""


class StateStoreClosedError(StateStoreError):
    """The state store was used after it was closed."""


class SQLiteStateStore:
    """Persist only Entity ``persistent`` state in a SQLite database.

    Ephemeral and session categories use a private in-memory delegate and do
    not survive construction of a new store. Persistent writes are committed
    immediately, and whole-Entity saves replace that Entity's rows in one
    transaction.
    """

    def __init__(self, database: str | PathLike[str]) -> None:
        path = fspath(database)
        if not isinstance(path, str) or not path:
            raise ValueError("SQLite state database path must not be empty")
        self._path = path
        self._lock = RLock()
        self._transient = InMemoryStateStore()
        self._connection: sqlite3.Connection | None = sqlite3.connect(
            path,
            check_same_thread=False,
        )
        try:
            self._initialize_schema()
        except BaseException:
            self.close()
            raise

    @property
    def database_path(self) -> str:
        return self._path

    @property
    def schema_version(self) -> int:
        return SCHEMA_VERSION

    def get(
        self,
        entity_id: str,
        key: str,
        *,
        category: StateCategory | str = StateCategory.SESSION,
        default: Any = None,
    ) -> Any:
        normalized = _normalize_category(category)
        if normalized is not StateCategory.PERSISTENT:
            self._ensure_open()
            return self._transient.get(
                entity_id, key, category=normalized, default=default
            )
        _validate_identifiers(entity_id, key)
        with self._lock:
            connection = self._ensure_open()
            row = connection.execute(
                """
                SELECT value_json
                FROM echo_entity_persistent_state
                WHERE entity_id = ? AND state_key = ?
                """,
                (entity_id, key),
            ).fetchone()
        return default if row is None else _deserialize(row[0])

    def set(
        self,
        entity_id: str,
        key: str,
        value: Any,
        *,
        category: StateCategory | str = StateCategory.SESSION,
    ) -> None:
        normalized = _normalize_category(category)
        if normalized is not StateCategory.PERSISTENT:
            self._ensure_open()
            self._transient.set(entity_id, key, value, category=normalized)
            return
        _validate_identifiers(entity_id, key)
        encoded = _serialize(value)
        with self._lock:
            connection = self._ensure_open()
            with connection:
                connection.execute(
                    """
                    INSERT INTO echo_entity_persistent_state (
                        entity_id, state_key, value_json
                    ) VALUES (?, ?, ?)
                    ON CONFLICT(entity_id, state_key)
                    DO UPDATE SET value_json = excluded.value_json
                    """,
                    (entity_id, key, encoded),
                )

    def delete(
        self,
        entity_id: str,
        key: str,
        *,
        category: StateCategory | str = StateCategory.SESSION,
    ) -> bool:
        normalized = _normalize_category(category)
        if normalized is not StateCategory.PERSISTENT:
            self._ensure_open()
            return self._transient.delete(entity_id, key, category=normalized)
        _validate_identifiers(entity_id, key)
        with self._lock:
            connection = self._ensure_open()
            with connection:
                cursor = connection.execute(
                    """
                    DELETE FROM echo_entity_persistent_state
                    WHERE entity_id = ? AND state_key = ?
                    """,
                    (entity_id, key),
                )
        return cursor.rowcount > 0

    def list(
        self,
        entity_id: str,
        *,
        category: StateCategory | str = StateCategory.SESSION,
    ) -> dict[str, Any]:
        normalized = _normalize_category(category)
        if normalized is not StateCategory.PERSISTENT:
            self._ensure_open()
            return self._transient.list(entity_id, category=normalized)
        _validate_entity_id(entity_id)
        with self._lock:
            connection = self._ensure_open()
            rows = connection.execute(
                """
                SELECT state_key, value_json
                FROM echo_entity_persistent_state
                WHERE entity_id = ?
                ORDER BY state_key
                """,
                (entity_id,),
            ).fetchall()
        return {key: _deserialize(value) for key, value in rows}

    def snapshot(self, entity_id: str) -> dict[StateCategory, dict[str, Any]]:
        _validate_entity_id(entity_id)
        return {
            category: self.list(entity_id, category=category)
            for category in StateCategory
        }

    def load(self, entity_id: str) -> dict[StateCategory, dict[str, Any]]:
        return self.snapshot(entity_id)

    def save(self, entity_id: str, state: EntityStateSnapshot) -> None:
        """Replace one Entity's persistent rows in a single transaction."""

        _validate_entity_id(entity_id)
        validated = InMemoryStateStore()
        validated.save(entity_id, state)
        snapshot = validated.snapshot(entity_id)
        persistent_rows = [
            (entity_id, key, _serialize(value))
            for key, value in snapshot[StateCategory.PERSISTENT].items()
        ]

        with self._lock:
            connection = self._ensure_open()
            with connection:
                connection.execute(
                    """
                    DELETE FROM echo_entity_persistent_state
                    WHERE entity_id = ?
                    """,
                    (entity_id,),
                )
                connection.executemany(
                    """
                    INSERT INTO echo_entity_persistent_state (
                        entity_id, state_key, value_json
                    ) VALUES (?, ?, ?)
                    """,
                    persistent_rows,
                )

        self._transient.save(
            entity_id,
            {
                StateCategory.EPHEMERAL: snapshot[StateCategory.EPHEMERAL],
                StateCategory.SESSION: snapshot[StateCategory.SESSION],
            },
        )

    def metadata(self) -> dict[str, str]:
        """Return detached schema metadata for diagnostics and migrations."""

        with self._lock:
            connection = self._ensure_open()
            rows = connection.execute(
                "SELECT metadata_key, metadata_value FROM echo_state_metadata"
            ).fetchall()
        return dict(rows)

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def __enter__(self) -> SQLiteStateStore:
        self._ensure_open()
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()

    def _initialize_schema(self) -> None:
        with self._lock:
            connection = self._ensure_open()
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS echo_state_metadata (
                        metadata_key TEXT PRIMARY KEY,
                        metadata_value TEXT NOT NULL
                    )
                    """
                )
                metadata = dict(
                    connection.execute(
                        """
                        SELECT metadata_key, metadata_value
                        FROM echo_state_metadata
                        """
                    ).fetchall()
                )
                expected = {
                    "schema_version": str(SCHEMA_VERSION),
                    "serialization_format": _SERIALIZATION_FORMAT,
                    "serialization_version": str(SERIALIZATION_VERSION),
                    "store_scope": "entity_persistent_state",
                }
                if metadata:
                    for key in (
                        "schema_version",
                        "serialization_format",
                        "serialization_version",
                        "store_scope",
                    ):
                        if metadata.get(key) != expected[key]:
                            raise StateStoreSchemaError(
                                f"unsupported SQLite state {key}: "
                                f"{metadata.get(key)!r}"
                            )
                else:
                    connection.executemany(
                        """
                        INSERT INTO echo_state_metadata (
                            metadata_key, metadata_value
                        ) VALUES (?, ?)
                        """,
                        expected.items(),
                    )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS echo_entity_persistent_state (
                        entity_id TEXT NOT NULL,
                        state_key TEXT NOT NULL,
                        value_json TEXT NOT NULL,
                        PRIMARY KEY (entity_id, state_key)
                    )
                    """
                )

    def _ensure_open(self) -> sqlite3.Connection:
        if self._connection is None:
            raise StateStoreClosedError("SQLite state store is closed")
        return self._connection


def _validate_entity_id(entity_id: str) -> None:
    if not isinstance(entity_id, str) or not entity_id:
        raise ValueError("entity id must not be empty")


def _validate_identifiers(entity_id: str, key: str) -> None:
    _validate_entity_id(entity_id)
    if not isinstance(key, str) or not key:
        raise ValueError("state key must not be empty")


def _normalize_category(value: StateCategory | str) -> StateCategory:
    try:
        return value if isinstance(value, StateCategory) else StateCategory(value)
    except (TypeError, ValueError) as error:
        choices = ", ".join(category.value for category in StateCategory)
        raise ValueError(f"state category must be one of: {choices}") from error


def _serialize(value: Any) -> str:
    envelope = {
        "format": _SERIALIZATION_FORMAT,
        "version": SERIALIZATION_VERSION,
        "data": _encode_value(value, set()),
    }
    try:
        return json.dumps(
            envelope,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise StateSerializationError("state value is not serializable") from error


def _encode_value(value: Any, active: set[int]) -> dict[str, Any]:
    if value is None:
        return {"type": "none"}
    if type(value) is bool:
        return {"type": "bool", "value": value}
    if type(value) is int:
        return {"type": "int", "value": value}
    if type(value) is float:
        if not math.isfinite(value):
            raise StateSerializationError("persistent floats must be finite")
        return {"type": "float", "value": value}
    if type(value) is str:
        return {"type": "str", "value": value}
    if type(value) is bytes:
        return {"type": "bytes", "value": b64encode(value).decode("ascii")}

    value_id = id(value)
    if value_id in active:
        raise StateSerializationError("cyclic persistent state is not supported")
    if type(value) in {list, tuple}:
        active.add(value_id)
        try:
            return {
                "type": "tuple" if type(value) is tuple else "list",
                "value": [_encode_value(item, active) for item in value],
            }
        finally:
            active.remove(value_id)
    if type(value) is dict:
        active.add(value_id)
        try:
            return {
                "type": "dict",
                "value": [
                    [_encode_value(key, active), _encode_value(item, active)]
                    for key, item in value.items()
                ],
            }
        finally:
            active.remove(value_id)
    raise StateSerializationError(
        f"unsupported persistent state type: {type(value).__qualname__}"
    )


def _deserialize(value_json: str) -> Any:
    try:
        envelope = json.loads(value_json)
        if not isinstance(envelope, dict):
            raise ValueError("state envelope must be an object")
        if envelope.get("format") != _SERIALIZATION_FORMAT:
            raise ValueError("unexpected state serialization format")
        if envelope.get("version") != SERIALIZATION_VERSION:
            raise ValueError("unexpected state serialization version")
        return _decode_value(envelope["data"])
    except (KeyError, TypeError, ValueError) as error:
        raise StateSerializationError("stored state value is invalid") from error


def _decode_value(encoded: Any) -> Any:
    if not isinstance(encoded, dict) or not isinstance(encoded.get("type"), str):
        raise ValueError("encoded state value must contain a type")
    value_type = encoded["type"]
    if value_type == "none":
        return None
    value = encoded.get("value")
    if value_type == "bool" and type(value) is bool:
        return value
    if value_type == "int" and type(value) is int:
        return value
    if value_type == "float" and type(value) is float:
        result = float(value)
        if math.isfinite(result):
            return result
    if value_type == "str" and isinstance(value, str):
        return value
    if value_type == "bytes" and isinstance(value, str):
        return b64decode(value.encode("ascii"), validate=True)
    if value_type in {"list", "tuple"} and isinstance(value, list):
        result = [_decode_value(item) for item in value]
        return tuple(result) if value_type == "tuple" else result
    if value_type == "dict" and isinstance(value, list):
        result: dict[Any, Any] = {}
        for entry in value:
            if not isinstance(entry, list) or len(entry) != 2:
                raise ValueError("encoded dictionary entry is invalid")
            key = _decode_value(entry[0])
            if key in result:
                raise ValueError("encoded dictionary contains a duplicate key")
            result[key] = _decode_value(entry[1])
        return result
    raise ValueError(f"invalid encoded state type: {value_type}")
