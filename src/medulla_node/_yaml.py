"""A deliberately small, non-executable YAML subset for node configuration."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


class SafeYamlError(ValueError):
    pass


_NUMBER = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")


def load_yaml_mapping(text: str, *, source: str | Path = "<configuration>") -> dict[str, Any]:
    """Parse mappings, sequences, and JSON-compatible scalar values only.

    Tags, anchors, aliases, merge keys, and custom object construction are not
    part of this grammar.
    """

    path = str(source)
    lines: list[tuple[int, str, int]] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if "\t" in raw:
            raise SafeYamlError(f"tabs are not allowed in {path}:{number}")
        content = raw.lstrip(" ")
        if not content or content.startswith("#"):
            continue
        indent = len(raw) - len(content)
        if indent % 2:
            raise SafeYamlError(f"indentation must use two spaces in {path}:{number}")
        if content.startswith("---") or content.startswith("..."):
            raise SafeYamlError(f"YAML document directives are not supported in {path}:{number}")
        if any(token in content for token in ("!!", "&", "*", "<<:")):
            raise SafeYamlError(
                f"YAML tags, anchors, aliases, and merges are forbidden in {path}:{number}"
            )
        if content == "-":
            lines.append((indent, content, number))
        elif content.startswith("- ") and ":" in content[2:]:
            lines.append((indent, "-", number))
            lines.append((indent + 2, content[2:].strip(), number))
        else:
            lines.append((indent, content, number))
    if not lines:
        raise SafeYamlError(f"configuration must not be empty: {path}")
    if lines[0][0] != 0:
        raise SafeYamlError(f"top-level configuration must not be indented: {path}")
    value, position = _parse_block(lines, 0, 0, path)
    if position != len(lines) or type(value) is not dict:
        raise SafeYamlError(f"top-level configuration must be a mapping: {path}")
    return value


def _parse_block(
    lines: list[tuple[int, str, int]], position: int, indent: int, path: str
) -> tuple[dict[str, Any] | list[Any], int]:
    is_list = lines[position][1] == "-" or lines[position][1].startswith("- ")
    result: dict[str, Any] | list[Any] = [] if is_list else {}
    while position < len(lines):
        current_indent, content, number = lines[position]
        if current_indent < indent:
            break
        if current_indent != indent:
            raise SafeYamlError(f"invalid nested indentation in {path}:{number}")
        if is_list:
            if not content.startswith("-"):
                raise SafeYamlError(f"cannot mix sequences and mappings in {path}:{number}")
            assert isinstance(result, list)
            if content == "-":
                position += 1
                if position >= len(lines) or lines[position][0] <= indent:
                    raise SafeYamlError(f"sequence item must not be empty in {path}:{number}")
                item, position = _parse_block(lines, position, lines[position][0], path)
                result.append(item)
            else:
                result.append(_scalar(content[2:].strip(), path, number))
                position += 1
            continue
        if content.startswith("-") or ":" not in content:
            raise SafeYamlError(f"expected a mapping entry in {path}:{number}")
        key, raw_value = content.split(":", 1)
        key, raw_value = key.strip(), raw_value.strip()
        assert isinstance(result, dict)
        if not key or key in result:
            raise SafeYamlError(f"empty or duplicate key in {path}:{number}")
        if any(character in key for character in "{}[]"):
            raise SafeYamlError(f"invalid mapping key in {path}:{number}")
        position += 1
        if raw_value:
            result[key] = _scalar(raw_value, path, number)
        elif position < len(lines) and lines[position][0] > indent:
            result[key], position = _parse_block(lines, position, lines[position][0], path)
        else:
            result[key] = {}
    return result, position


def _scalar(value: str, path: str, number: int) -> Any:
    if not value:
        raise SafeYamlError(f"empty scalar in {path}:{number}")
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if value.startswith(("[", "{")):
        try:
            return json.loads(value)
        except json.JSONDecodeError as error:
            raise SafeYamlError(
                f"inline collections must use JSON syntax in {path}:{number}"
            ) from error
    if value.startswith(("'", '"')):
        if len(value) < 2 or value[-1] != value[0]:
            raise SafeYamlError(f"unterminated quoted scalar in {path}:{number}")
        if value[0] == '"':
            try:
                return json.loads(value)
            except json.JSONDecodeError as error:
                raise SafeYamlError(f"invalid quoted scalar in {path}:{number}") from error
        return value[1:-1].replace("''", "'")
    if _NUMBER.fullmatch(value):
        return float(value) if "." in value else int(value)
    return value


__all__ = ["SafeYamlError", "load_yaml_mapping"]
