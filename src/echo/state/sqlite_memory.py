"""SQLite implementation of the durable Entity memory repository."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from os import PathLike, fspath
import sqlite3
from threading import RLock

from echo.entity.memory_store import (
    DurableMemoryRecord,
    DurableMemoryStatus,
    DurableMemoryType,
    MemorySourceType,
)


MEMORY_SCHEMA_VERSION = 1


class MemoryStoreError(Exception):
    pass


class MemoryStoreClosedError(MemoryStoreError):
    pass


class SQLiteMemoryRepository:
    def __init__(self, database: str | PathLike[str]) -> None:
        self._path = fspath(database)
        if not isinstance(self._path, str) or not self._path:
            raise ValueError("SQLite memory database path must not be empty")
        self._lock = RLock()
        self._connection: sqlite3.Connection | None = sqlite3.connect(
            self._path,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        try:
            self._initialize_schema()
        except BaseException:
            self.close()
            raise

    @property
    def database_path(self) -> str:
        return self._path

    def _ensure_open(self) -> sqlite3.Connection:
        if self._connection is None:
            raise MemoryStoreClosedError("SQLite memory repository is closed")
        return self._connection

    def _initialize_schema(self) -> None:
        with self._lock:
            connection = self._ensure_open()
            with connection:
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS echo_memory_metadata (
                        metadata_key TEXT PRIMARY KEY,
                        metadata_value TEXT NOT NULL
                    )
                """)
                connection.execute(
                    "INSERT OR IGNORE INTO echo_memory_metadata "
                    "VALUES ('schema_version', ?)",
                    (str(MEMORY_SCHEMA_VERSION),),
                )
                version = connection.execute(
                    "SELECT metadata_value FROM echo_memory_metadata "
                    "WHERE metadata_key = 'schema_version'"
                ).fetchone()[0]
                if version != str(MEMORY_SCHEMA_VERSION):
                    raise MemoryStoreError(f"unsupported SQLite memory schema version: {version!r}")
                connection.execute("""
                    CREATE TABLE IF NOT EXISTS echo_entity_memory (
                        id TEXT PRIMARY KEY,
                        entity_id TEXT NOT NULL,
                        memory_type TEXT NOT NULL,
                        content TEXT NOT NULL,
                        canonical_key TEXT NOT NULL,
                        source_type TEXT NOT NULL,
                        source_refs_json TEXT NOT NULL,
                        confidence REAL NOT NULL,
                        importance REAL NOT NULL,
                        status TEXT NOT NULL,
                        subject_id TEXT,
                        supersedes_id TEXT,
                        version INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        last_accessed_at TEXT,
                        access_count INTEGER NOT NULL DEFAULT 0,
                        FOREIGN KEY (supersedes_id) REFERENCES echo_entity_memory(id)
                            ON DELETE SET NULL
                    )
                """)
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS echo_entity_memory_lookup "
                    "ON echo_entity_memory("
                    "entity_id, status, memory_type, canonical_key)"
                )

    def get(self, entity_id: str, memory_id: str) -> DurableMemoryRecord | None:
        with self._lock:
            row = self._ensure_open().execute(
                "SELECT * FROM echo_entity_memory "
                "WHERE entity_id = ? AND id = ?",
                (entity_id, memory_id),
            ).fetchone()
        return None if row is None else _record(row)

    def list_for_entity(
        self,
        entity_id: str,
        *,
        status: DurableMemoryStatus | str | None = None,
        memory_type: DurableMemoryType | str | None = None,
    ) -> tuple[DurableMemoryRecord, ...]:
        clauses = ["entity_id = ?"]
        parameters: list[object] = [entity_id]
        if status is not None:
            clauses.append("status = ?")
            parameters.append(DurableMemoryStatus(status).value)
        if memory_type is not None:
            clauses.append("memory_type = ?")
            parameters.append(DurableMemoryType(memory_type).value)
        query = (
            "SELECT * FROM echo_entity_memory WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC, id"
        )
        with self._lock:
            rows = self._ensure_open().execute(query, parameters).fetchall()
        return tuple(_record(row) for row in rows)

    def commit(self, record: DurableMemoryRecord, *, supersede_ids: tuple[str, ...] = ()) -> None:
        with self._lock:
            connection = self._ensure_open()
            with connection:
                if supersede_ids:
                    placeholders = ",".join("?" for _ in supersede_ids)
                    connection.execute(
                        "UPDATE echo_entity_memory SET status = ?, "
                        f"updated_at = ? WHERE entity_id = ? AND id IN ({placeholders})",
                        (
                            DurableMemoryStatus.SUPERSEDED.value,
                            record.updated_at.isoformat(),
                            record.entity_id,
                            *supersede_ids,
                        ),
                    )
                connection.execute("""
                    INSERT INTO echo_entity_memory (
                        id, entity_id, memory_type, content, canonical_key,
                        source_type, source_refs_json, confidence, importance,
                        status, subject_id, supersedes_id, version, created_at,
                        updated_at, last_accessed_at, access_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        source_refs_json=excluded.source_refs_json,
                        confidence=excluded.confidence, importance=excluded.importance,
                        status=excluded.status, updated_at=excluded.updated_at,
                        last_accessed_at=excluded.last_accessed_at,
                        access_count=excluded.access_count
                """, _values(record))

    def archive(self, entity_id: str, memory_id: str) -> bool:
        with self._lock:
            connection = self._ensure_open()
            with connection:
                cursor = connection.execute(
                    "UPDATE echo_entity_memory SET status = ?, updated_at = ? "
                    "WHERE entity_id = ? AND id = ?",
                    (
                        DurableMemoryStatus.ARCHIVED.value,
                        datetime.now(timezone.utc).isoformat(),
                        entity_id,
                        memory_id,
                    ),
                )
        return cursor.rowcount > 0

    def delete(self, entity_id: str, memory_id: str) -> bool:
        with self._lock:
            connection = self._ensure_open()
            with connection:
                cursor = connection.execute(
                    "DELETE FROM echo_entity_memory "
                    "WHERE entity_id = ? AND id = ?",
                    (entity_id, memory_id),
                )
        return cursor.rowcount > 0

    def record_access(
        self,
        entity_id: str,
        memory_ids: tuple[str, ...],
        accessed_at: datetime,
    ) -> None:
        if not memory_ids:
            return
        placeholders = ",".join("?" for _ in memory_ids)
        with self._lock:
            connection = self._ensure_open()
            with connection:
                connection.execute(
                    "UPDATE echo_entity_memory SET last_accessed_at = ?, "
                    "access_count = access_count + 1 "
                    f"WHERE entity_id = ? AND id IN ({placeholders})",
                    (accessed_at.isoformat(), entity_id, *memory_ids),
                )

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def __enter__(self) -> SQLiteMemoryRepository:
        self._ensure_open()
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()


def _values(record: DurableMemoryRecord) -> tuple[object, ...]:
    return (
        record.id, record.entity_id, record.memory_type.value, record.content,
        record.canonical_key, record.source_type.value,
        json.dumps(record.source_refs), record.confidence, record.importance,
        record.status.value, record.subject_id, record.supersedes_id,
        record.version, record.created_at.isoformat(), record.updated_at.isoformat(),
        record.last_accessed_at.isoformat() if record.last_accessed_at else None,
        record.access_count,
    )


def _record(row: sqlite3.Row) -> DurableMemoryRecord:
    return DurableMemoryRecord(
        id=row["id"], entity_id=row["entity_id"], memory_type=DurableMemoryType(row["memory_type"]),
        content=row["content"], canonical_key=row["canonical_key"],
        source_type=MemorySourceType(row["source_type"]),
        source_refs=tuple(json.loads(row["source_refs_json"])),
        confidence=row["confidence"],
        importance=row["importance"],
        status=DurableMemoryStatus(row["status"]),
        subject_id=row["subject_id"], supersedes_id=row["supersedes_id"], version=row["version"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        last_accessed_at=(
            datetime.fromisoformat(row["last_accessed_at"])
            if row["last_accessed_at"]
            else None
        ),
        access_count=row["access_count"],
    )
