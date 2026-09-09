"""Strict dependency-free loading for checked-in Entity seed configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from echo.core.entity import Entity
from echo.entity.drives import DriveProfile
from echo.entity.identity import EntityIdentity
from echo.entity.traits import TraitProfile
from echo.entity.memory_store import MemoryService
from echo.entity.state_store import StateStore


class EntitySeedError(ValueError):
    """An Entity seed file is missing or has an unsupported shape."""


@dataclass(slots=True, kw_only=True, frozen=True)
class EntitySeed:
    identity: EntityIdentity
    traits: TraitProfile
    drives: DriveProfile
    character_guidance: str

    def create_entity(
        self,
        *,
        state_store: StateStore | None = None,
        memory_service: MemoryService | None = None,
    ) -> Entity:
        return Entity(
            self.identity.entity_id,
            identity=self.identity,
            traits=self.traits,
            drives=self.drives,
            state_store=state_store,
            memory_service=memory_service,
        )


def load_entity_seed(directory: str | Path) -> EntitySeed:
    """Load the reviewed YAML subset used by an Entity seed directory."""

    root = Path(directory)
    identity_data = _load_mapping(root / "identity.yaml")
    traits_data = _load_mapping(root / "traits.yaml")
    drives_data = _load_mapping(root / "drives.yaml")
    _require_schema_version(identity_data, "identity.yaml")
    _require_schema_version(traits_data, "traits.yaml")
    _require_schema_version(drives_data, "drives.yaml")
    guidance_path = root / "prompts" / "character.md"
    try:
        guidance = guidance_path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise EntitySeedError(
            f"cannot read Entity character guidance: {guidance_path}"
        ) from error
    if not guidance:
        raise EntitySeedError("Entity character guidance must not be empty")

    entity = _require_mapping(identity_data, "entity", "identity.yaml")
    identity = _require_mapping(identity_data, "identity", "identity.yaml")
    traits = _require_mapping(traits_data, "traits", "traits.yaml")
    drives = _require_mapping(drives_data, "drives", "drives.yaml")
    try:
        return EntitySeed(
            identity=EntityIdentity(
                entity_id=_require_string(entity, "id"),
                name=_require_string(entity, "name"),
                entity_type=_require_string(entity, "type"),
                presentation=_require_string(entity, "presentation"),
                worldview=_require_string(identity, "worldview"),
                core_values=tuple(_require_strings(identity, "core_values")),
            ),
            traits=TraitProfile(_require_numbers(traits, "traits")),
            drives=DriveProfile(_require_numbers(drives, "drives")),
            character_guidance=guidance,
        )
    except (TypeError, ValueError) as error:
        raise EntitySeedError(f"invalid Entity seed in {root}: {error}") from error


def _load_mapping(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise EntitySeedError(f"cannot read Entity seed: {path}") from error
    lines: list[tuple[int, str]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if "\t" in raw:
            raise EntitySeedError(f"tabs are not allowed in {path}:{number}")
        content = raw.lstrip(" ")
        if not content or content.startswith("#"):
            continue
        indent = len(raw) - len(content)
        if indent % 2:
            raise EntitySeedError(f"indentation must use two spaces in {path}:{number}")
        lines.append((indent, content))
    if not lines:
        raise EntitySeedError(f"Entity seed must not be empty: {path}")
    value, position = _parse_block(lines, 0, 0, path)
    if position != len(lines) or not isinstance(value, dict):
        raise EntitySeedError(f"top-level Entity seed must be a mapping: {path}")
    return value


def _require_schema_version(values: Mapping[str, Any], source: str) -> None:
    version = values.get("schema_version")
    if version != 1:
        raise EntitySeedError(f"{source}.schema_version must be 1")


def _parse_block(
    lines: list[tuple[int, str]],
    position: int,
    indent: int,
    path: Path,
) -> tuple[dict[str, Any] | list[Any], int]:
    is_list = lines[position][1].startswith("-")
    result: dict[str, Any] | list[Any] = [] if is_list else {}
    while position < len(lines):
        current_indent, content = lines[position]
        if current_indent < indent:
            break
        if current_indent != indent:
            raise EntitySeedError(f"invalid nested indentation in {path}")
        if is_list:
            if not content.startswith("- "):
                raise EntitySeedError(f"cannot mix lists and mappings in {path}")
            assert isinstance(result, list)
            result.append(_scalar(content[2:].strip()))
            position += 1
            continue
        if ":" not in content or content.startswith("-"):
            raise EntitySeedError(f"expected a mapping entry in {path}")
        key, raw_value = content.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        assert isinstance(result, dict)
        if not key or key in result:
            raise EntitySeedError(f"empty or duplicate key in {path}")
        if raw_value in {">", ">-", "|", "|-"}:
            position += 1
            parts: list[str] = []
            while position < len(lines) and lines[position][0] > indent:
                parts.append(lines[position][1])
                position += 1
            result[key] = " ".join(parts) if raw_value.startswith(">") else "\n".join(parts)
            continue
        if raw_value:
            result[key] = _scalar(raw_value)
            position += 1
            continue
        position += 1
        if position >= len(lines) or lines[position][0] <= indent:
            result[key] = {}
            continue
        nested, position = _parse_block(lines, position, lines[position][0], path)
        result[key] = nested
    return result, position


def _scalar(value: str) -> Any:
    if value == "true":
        return True
    if value == "false":
        return False
    if value in {"null", "~"}:
        return None
    if value[:1] == value[-1:] and value.startswith(("'", '"')):
        return value[1:-1]
    try:
        return float(value) if "." in value else int(value)
    except ValueError:
        return value


def _require_mapping(
    values: Mapping[str, Any], key: str, source: str
) -> Mapping[str, Any]:
    value = values.get(key)
    if not isinstance(value, Mapping):
        raise EntitySeedError(f"{source}.{key} must be a mapping")
    return value


def _require_string(values: Mapping[str, Any], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EntitySeedError(f"{key} must be a non-empty string")
    return value.strip()


def _require_strings(values: Mapping[str, Any], key: str) -> list[str]:
    value = values.get(key)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise EntitySeedError(f"{key} must be a list of non-empty strings")
    return value


def _require_numbers(values: Mapping[str, Any], label: str) -> dict[str, float]:
    if any(
        not isinstance(key, str)
        or isinstance(value, bool)
        or not isinstance(value, (int, float))
        for key, value in values.items()
    ):
        raise EntitySeedError(f"{label} values must be numbers")
    return {key: float(value) for key, value in values.items()}
