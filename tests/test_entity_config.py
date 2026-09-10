from __future__ import annotations

from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from echo import EntitySeedError, load_entity_seed


class EntitySeedTests(unittest.TestCase):
    def test_checked_in_bit_seed_loads_reviewed_character(self) -> None:
        seed = load_entity_seed(
            Path(__file__).resolve().parents[1] / "entities" / "bit"
        )

        entity = seed.create_entity()

        self.assertEqual(entity.id, "bit")
        self.assertEqual(entity.identity.name, "Bit")
        self.assertEqual(entity.identity.entity_type, "embodied_companion")
        self.assertEqual(entity.traits.values["curiosity"], 0.82)
        self.assertEqual(entity.drives.values["learning"], 0.75)
        self.assertIn("first person as Bit", seed.character_guidance)

    def test_unknown_seed_schema_version_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "identity.yaml").write_text(
                "schema_version: 2\nentity: {}\nidentity: {}\n",
                encoding="utf-8",
            )
            (root / "traits.yaml").write_text(
                "schema_version: 1\ntraits: {}\n",
                encoding="utf-8",
            )
            (root / "drives.yaml").write_text(
                "schema_version: 1\ndrives: {}\n",
                encoding="utf-8",
            )
            (root / "prompts").mkdir()
            (root / "prompts" / "character.md").write_text(
                "Speak as the Entity.", encoding="utf-8"
            )

            with self.assertRaisesRegex(EntitySeedError, "schema_version must be 1"):
                load_entity_seed(root)


if __name__ == "__main__":
    unittest.main()
