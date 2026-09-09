from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import (
    Entity,
    Runtime,
    SQLiteStateStore,
    StateCategory,
    StateSerializationError,
    StateStore,
    StateStoreSchemaError,
)


class SQLiteStateStoreTests(unittest.TestCase):
    def test_runtime_restart_restores_only_entity_persistent_state(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "echo-state.sqlite3"
            first_store = SQLiteStateStore(database)
            first_entity = Entity("bit", state_store=first_store)
            first_runtime = Runtime([first_entity])
            typed_value = {
                "enabled": True,
                "count": 3,
                "ratio": 0.75,
                "nothing": None,
                "path": ("lab", "bench"),
                "payload": b"\x00echo",
                7: ["nested", False],
            }

            first_entity.set_state(
                "continuity",
                typed_value,
                category=StateCategory.PERSISTENT,
            )
            first_entity.set_state(
                "conversation", "current", category="session"
            )
            first_entity.set_state("scratch", 42, category="ephemeral")
            first_runtime.stop()
            first_store.close()

            second_store = SQLiteStateStore(database)
            second_entity = Entity("bit", state_store=second_store)
            second_runtime = Runtime([second_entity])

            self.assertEqual(
                second_entity.get_state("continuity", category="persistent"),
                typed_value,
            )
            self.assertIsNone(second_entity.get_state("conversation"))
            self.assertIsNone(
                second_entity.get_state("scratch", category="ephemeral")
            )

            second_runtime.stop()
            second_store.close()

    def test_state_is_entity_scoped_and_persistent_delete_is_durable(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "echo-state.sqlite3"
            with SQLiteStateStore(database) as store:
                self.assertIsInstance(store, StateStore)
                store.set("bit", "name", "Bit", category="persistent")
                store.set("other", "name", "Other", category="persistent")
                self.assertTrue(
                    store.delete("other", "name", category="persistent")
                )

            with SQLiteStateStore(database) as restored:
                self.assertEqual(
                    restored.list("bit", category="persistent"),
                    {"name": "Bit"},
                )
                self.assertEqual(
                    restored.list("other", category="persistent"), {}
                )

    def test_whole_entity_save_is_atomic_when_serialization_fails(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "echo-state.sqlite3"
            with SQLiteStateStore(database) as store:
                store.set("bit", "stable", "before", category="persistent")

                with self.assertRaises(StateSerializationError):
                    store.save(
                        "bit",
                        {
                            StateCategory.PERSISTENT: {
                                "stable": "after",
                                "unsupported": object(),
                            }
                        },
                    )

                self.assertEqual(
                    store.list("bit", category="persistent"),
                    {"stable": "before"},
                )

    def test_database_has_versioned_state_only_schema(self) -> None:
        with TemporaryDirectory() as directory:
            database = Path(directory) / "echo-state.sqlite3"
            with SQLiteStateStore(database) as store:
                self.assertEqual(
                    store.metadata(),
                    {
                        "schema_version": "1",
                        "serialization_format": "echo-state-json",
                        "serialization_version": "1",
                        "store_scope": "entity_persistent_state",
                    },
                )

            connection = sqlite3.connect(database)
            try:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertEqual(
                    tables,
                    {
                        "echo_state_metadata",
                        "echo_entity_persistent_state",
                    },
                )
                connection.execute(
                    """
                    UPDATE echo_state_metadata
                    SET metadata_value = '999'
                    WHERE metadata_key = 'schema_version'
                    """
                )
                connection.commit()
            finally:
                connection.close()

            with self.assertRaises(StateStoreSchemaError):
                SQLiteStateStore(database)


if __name__ == "__main__":
    unittest.main()
