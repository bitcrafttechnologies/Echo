from __future__ import annotations

import os
import sys
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import (
    Entity,
    InMemoryLogSink,
    InMemoryStateStore,
    Runtime,
    RuntimeEventType,
    Signal,
    StateCategory,
    StateStore,
)


class InMemoryStateStoreTests(unittest.TestCase):
    def test_store_supports_crud_and_detached_category_snapshots(self) -> None:
        store = InMemoryStateStore()

        store.set("bit", "scratch", {"step": 1}, category="ephemeral")
        store.set("bit", "mode", "ready")
        store.set("bit", "home", "lab", category=StateCategory.PERSISTENT)

        self.assertIsInstance(store, StateStore)
        self.assertEqual(store.get("bit", "mode"), "ready")
        self.assertEqual(
            store.get("bit", "missing", default="fallback"), "fallback"
        )
        self.assertEqual(store.list("bit"), {"mode": "ready"})
        snapshot = store.snapshot("bit")
        self.assertEqual(
            snapshot[StateCategory.EPHEMERAL], {"scratch": {"step": 1}}
        )
        self.assertEqual(snapshot[StateCategory.SESSION], {"mode": "ready"})
        self.assertEqual(snapshot[StateCategory.PERSISTENT], {"home": "lab"})

        snapshot[StateCategory.EPHEMERAL]["scratch"]["step"] = 2
        self.assertEqual(
            store.get("bit", "scratch", category="ephemeral"), {"step": 1}
        )
        self.assertTrue(store.delete("bit", "mode"))
        self.assertFalse(store.delete("bit", "mode"))

    def test_load_and_save_replace_one_entity_without_cross_entity_leakage(self) -> None:
        store = InMemoryStateStore()
        store.set("other", "mode", "safe")
        state = {
            StateCategory.EPHEMERAL: {"focus": "signal-1"},
            StateCategory.SESSION: {"mode": "active"},
            StateCategory.PERSISTENT: {"preference": "concise"},
        }

        store.save("bit", state)
        loaded = store.load("bit")

        self.assertEqual(loaded, state)
        loaded[StateCategory.SESSION]["mode"] = "tampered"
        self.assertEqual(store.get("bit", "mode"), "active")
        self.assertEqual(store.get("other", "mode"), "safe")


class EntityStateStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_entity_delegates_state_and_preserves_mapping_compatibility(self) -> None:
        store = InMemoryStateStore()
        store.set("bit", "home", "lab", category="persistent")
        bit = Entity("bit", state={"mode": "idle"}, state_store=store)

        bit.state["mode"] = "ready"
        bit.set_state("focus", "battery", category="ephemeral")

        self.assertEqual(bit.state, {"mode": "ready"})
        self.assertEqual(bit.get_state("home", category="persistent"), "lab")
        self.assertEqual(bit.list_state(category="ephemeral"), {"focus": "battery"})
        self.assertEqual(bit.load_state(), bit.snapshot_state())
        self.assertTrue(bit.delete_state("focus", category="ephemeral"))

    async def test_all_categories_changed_by_handler_remain_observable(self) -> None:
        bit = Entity("bit", state={"mode": "idle"})
        bit.set_state("scratch", "old", category="ephemeral")
        bit.set_state("visits", 0, category="persistent")
        sink = InMemoryLogSink()
        runtime = Runtime([bit], log_sink=sink)

        @bit.on("observe")
        async def observe(signal: Signal) -> None:
            bit.state["mode"] = "active"
            bit.delete_state("scratch", category="ephemeral")
            bit.set_state("focus", signal.id, category="ephemeral")
            bit.set_state("visits", 1, category="persistent")

        signal = Signal(type="observe")
        await runtime.emit(signal)

        events = sink.query(event_type=RuntimeEventType.STATE_CHANGED)
        self.assertEqual(len(events), 3)
        by_category = {
            event.metadata.get("category", "session"): event for event in events
        }
        self.assertEqual(set(by_category), {"ephemeral", "session", "persistent"})
        self.assertEqual(
            by_category["session"].metadata["changes"]["mode"]["after"],
            "active",
        )
        self.assertEqual(by_category["ephemeral"].signal_id, signal.id)
        self.assertEqual(
            by_category["ephemeral"].metadata["changes"]["scratch"],
            {"operation": "removed", "before": "old"},
        )
        self.assertEqual(by_category["persistent"].task_id, runtime.tasks[0].id)


if __name__ == "__main__":
    unittest.main()
