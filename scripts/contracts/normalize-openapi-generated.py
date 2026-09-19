#!/usr/bin/env python3
"""Normalize generated OpenAPI output.

The connect-openapi generator currently mirrors repeated scalar min_items
validation onto both the array schema and the scalar item schema. OpenAPI's
minItems keyword is valid for arrays, but not for scalar items, so keep the
array-level constraint and remove the invalid item-level copy.

The generator can also emit closed object schemas whose ``oneOf`` branches
declare variant properties that are absent from the parent ``properties``
mapping. Under OpenAPI 3.1 / JSON Schema semantics, ``additionalProperties:
false`` then rejects every variant. Keep the branch requirements and copy the
variant property schemas into the parent's allowed-property mapping.

Protobuf enum validation uses numeric values while generated enum schemas use
symbolic strings. Resolve numeric restrictions through Buf descriptors so the
field constraint and referenced schema accept the same values.

Finally, protobuf ``bytes`` validation applies to decoded bytes while OpenAPI
``format: byte`` values are base64 strings. Translate raw byte limits to their
encoded string lengths so generated OpenAPI clients accept every payload the
protobuf contract accepts.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from functools import cache
from pathlib import Path


SCALAR_TYPES = {"boolean", "integer", "number", "string"}
PROTOBUF_BYTES_MIN_EXTENSION = "x-protobuf-bytes-min-length"
PROTOBUF_BYTES_MAX_EXTENSION = "x-protobuf-bytes-max-length"


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _mapping_key(line: str) -> str | None:
    text = line.strip()
    if not text.endswith(":") or text.startswith("-"):
        return None
    return text[:-1]


def _direct_mapping_entries(
    lines: list[str], start: int, end: int, mapping_indent: int
) -> list[tuple[str, list[str]]]:
    entry_indent = mapping_indent + 2
    entries: list[tuple[str, list[str]]] = []
    index = start + 1

    while index < end:
        if _indent(lines[index]) != entry_indent:
            index += 1
            continue
        key = _mapping_key(lines[index])
        if key is None:
            index += 1
            continue

        entry_end = index + 1
        while entry_end < end and _indent(lines[entry_end]) > entry_indent:
            entry_end += 1
        entries.append((key, lines[index:entry_end]))
        index = entry_end

    return entries


def _lift_closed_oneof_properties(lines: list[str]) -> tuple[list[str], bool]:
    output: list[str] = []
    index = 0
    changed = False

    while index < len(lines):
        line = lines[index]
        schema_indent = _indent(line)
        if schema_indent != 4 or _mapping_key(line) is None:
            output.append(line)
            index += 1
            continue

        schema_end = index + 1
        while schema_end < len(lines) and _indent(lines[schema_end]) > schema_indent:
            schema_end += 1
        block = lines[index:schema_end]
        attribute_indent = schema_indent + 2

        oneof_index = next(
            (
                offset
                for offset, candidate in enumerate(block)
                if _indent(candidate) == attribute_indent
                and candidate.strip() == "oneOf:"
            ),
            None,
        )
        properties_index = next(
            (
                offset
                for offset, candidate in enumerate(block)
                if _indent(candidate) == attribute_indent
                and candidate.strip() == "properties:"
            ),
            None,
        )
        is_closed = any(
            _indent(candidate) == attribute_indent
            and candidate.strip() == "additionalProperties: false"
            for candidate in block
        )
        if oneof_index is None or not is_closed:
            output.extend(block)
            index = schema_end
            continue

        # A message containing only a oneof has no parent properties mapping.
        # It still needs one so additionalProperties does not reject every variant.
        if properties_index is None:
            properties_index = len(block)
            block.append(" " * attribute_indent + "properties:\n")

        oneof_end = oneof_index + 1
        while oneof_end < len(block) and _indent(block[oneof_end]) > attribute_indent:
            oneof_end += 1
        properties_end = properties_index + 1
        while (
            properties_end < len(block)
            and _indent(block[properties_end]) > attribute_indent
        ):
            properties_end += 1

        variant_entries: list[tuple[str, list[str]]] = []
        for offset in range(oneof_index + 1, oneof_end):
            candidate = block[offset]
            candidate_text = candidate.strip()
            if candidate_text not in {"properties:", "- properties:"}:
                continue
            mapping_indent = _indent(candidate) + (
                2 if candidate_text == "- properties:" else 0
            )
            mapping_end = offset + 1
            while (
                mapping_end < oneof_end
                and _indent(block[mapping_end]) > mapping_indent
            ):
                mapping_end += 1
            variant_entries.extend(
                _direct_mapping_entries(block, offset, mapping_end, mapping_indent)
            )

        parent_entries = {
            key
            for key, _ in _direct_mapping_entries(
                block, properties_index, properties_end, attribute_indent
            )
        }
        missing_entries: list[list[str]] = []
        seen = set(parent_entries)
        parent_entry_indent = attribute_indent + 2
        for key, entry_lines in variant_entries:
            if key in seen:
                continue
            source_indent = _indent(entry_lines[0])
            missing_entries.append(
                [
                    (" " * parent_entry_indent)
                    + entry[source_indent:]
                    for entry in entry_lines
                ]
            )
            seen.add(key)

        if not missing_entries:
            output.extend(block)
            index = schema_end
            continue

        insert_at = properties_index + 1
        output.extend(block[:insert_at])
        for entry_lines in missing_entries:
            output.extend(entry_lines)
        output.extend(block[insert_at:])
        changed = True
        index = schema_end

    return output, changed


def _base64_min_length(raw_bytes: int) -> int:
    # ProtoJSON accepts unpadded base64, so the shortest encoding is ceil(4n/3).
    return (raw_bytes * 4 + 2) // 3


def _base64_max_length(raw_bytes: int) -> int:
    # Padded base64 is also valid ProtoJSON and always occupies a multiple of 4.
    return 4 * ((raw_bytes + 2) // 3)


def _normalize_byte_lengths(lines: list[str]) -> tuple[list[str], bool]:
    output = list(lines)
    changed = False

    format_indexes = [
        index for index, line in enumerate(lines) if line.strip() == "format: byte"
    ]
    for format_index in reversed(format_indexes):
        line = lines[format_index]
        format_indent = _indent(line)

        block_start = format_index
        while block_start > 0 and _indent(lines[block_start - 1]) >= format_indent:
            block_start -= 1
        block_end = format_index + 1
        while block_end < len(lines) and _indent(lines[block_end]) >= format_indent:
            block_end += 1

        block_text = {candidate.strip() for candidate in lines[block_start:block_end]}
        replacements: list[tuple[int, str, int]] = []
        for index in range(block_start, block_end):
            candidate = lines[index]
            if _indent(candidate) != format_indent:
                continue
            text = candidate.strip()
            if text.startswith("minLength:") and not any(
                item.startswith(f"{PROTOBUF_BYTES_MIN_EXTENSION}:")
                for item in block_text
            ):
                raw = int(text.split(":", 1)[1].strip())
                replacements.append((index, PROTOBUF_BYTES_MIN_EXTENSION, raw))
            elif text.startswith("maxLength:") and not any(
                item.startswith(f"{PROTOBUF_BYTES_MAX_EXTENSION}:")
                for item in block_text
            ):
                raw = int(text.split(":", 1)[1].strip())
                replacements.append((index, PROTOBUF_BYTES_MAX_EXTENSION, raw))

        for index, extension, raw in reversed(replacements):
            encoded = (
                _base64_min_length(raw)
                if extension == PROTOBUF_BYTES_MIN_EXTENSION
                else _base64_max_length(raw)
            )
            indent = " " * format_indent
            keyword = "minLength" if extension == PROTOBUF_BYTES_MIN_EXTENSION else "maxLength"
            output[index] = f"{indent}{keyword}: {encoded}\n"
            output.insert(index + 1, f"{indent}{extension}: {raw}\n")
            changed = True

    return output, changed


def descriptor_enum_values(image: dict) -> dict[str, dict[int, list[str]]]:
    """Map actual protobuf numbers, including sparse values and aliases."""
    result: dict[str, dict[int, list[str]]] = {}

    def visit(container: dict, prefix: str) -> None:
        for enum in container.get("enumType", []):
            values: dict[int, list[str]] = {}
            for value in enum.get("value", []):
                values.setdefault(int(value.get("number", 0)), []).append(value["name"])
            result[f"{prefix}{enum['name']}"] = values
        for message in container.get("messageType", []) + container.get("nestedType", []):
            visit(message, f"{prefix}{message['name']}.")

    for file in image.get("file", []):
        package = file.get("package", "")
        visit(file, f"{package}." if package else "")
    return result


@cache
def _protobuf_enum_values() -> dict[str, dict[int, list[str]]]:
    # Buf supplies resolved descriptors from the same source and locked imports
    # as generation. Never infer enum numbers from their declaration order.
    completed = subprocess.run(
        ["buf", "build", "--as-file-descriptor-set", "-o", "-#format=json"],
        cwd=Path(__file__).resolve().parents[2],
        check=True, capture_output=True, text=True,
    )
    return descriptor_enum_values(json.loads(completed.stdout))


def _normalize_enum_constraints(
    lines: list[str], enum_values: dict[str, dict[int, list[str]]] | None,
) -> tuple[list[str], bool]:
    output = list(lines)
    changed = False
    for index in reversed(range(len(lines))):
        if lines[index].strip() != "enum:":
            continue
        indent = _indent(lines[index])
        end = index + 1
        while end < len(lines) and _indent(lines[end]) > indent:
            end += 1
        numbers = []
        for line in lines[index + 1:end]:
            match = re.fullmatch(r"\s*- (-?\d+)\s*", line)
            if match is None:
                break
            numbers.append(int(match.group(1)))
        else:
            if not numbers:
                continue
            start = index
            while start > 0 and _indent(lines[start - 1]) >= indent:
                start -= 1
            block_end = end
            while block_end < len(lines) and _indent(lines[block_end]) >= indent:
                block_end += 1
            reference = next((
                re.fullmatch(r"\s*\$ref: ['\"]?#/components/schemas/([^'\"\s]+)['\"]?\s*", line)
                for line in lines[start:block_end]
                if _indent(line) == indent and line.strip().startswith("$ref:")
            ), None)
            if reference is None:
                continue
            values = enum_values if enum_values is not None else _protobuf_enum_values()
            name = reference.group(1)
            if name not in values or any(number not in values[name] for number in numbers):
                raise ValueError(f"Unresolved protobuf enum constraint for {name}: {numbers}")
            symbols = [symbol for number in numbers for symbol in values[name][number]]
            output[index + 1:end] = [" " * (indent + 2) + f"- {symbol}\n" for symbol in symbols]
            changed = True
    return output, changed


def normalize(
    path: Path, enum_values: dict[str, dict[int, list[str]]] | None = None,
) -> bool:
    lines = path.read_text().splitlines(keepends=True)
    output: list[str] = []
    stack: list[dict[str, object]] = []
    changed = False

    for line in lines:
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        text = stripped.strip()

        while stack and indent <= stack[-1]["items_indent"]:
            stack.pop()

        if stack:
            context = stack[-1]
            schema_indent = int(context["items_indent"]) + 2
            if indent == schema_indent and text.startswith("type: "):
                context["is_scalar"] = text.split(":", 1)[1].strip() in SCALAR_TYPES
            if indent == schema_indent and context.get("is_scalar") and text.startswith("minItems:"):
                changed = True
                continue

        output.append(line)

        if text == "items:":
            stack.append({"items_indent": indent, "is_scalar": False})

    output, lifted = _lift_closed_oneof_properties(output)
    changed = changed or lifted
    output, byte_lengths = _normalize_byte_lengths(output)
    changed = changed or byte_lengths
    output, enums = _normalize_enum_constraints(output, enum_values)
    changed = changed or enums
    if changed:
        path.write_text("".join(output))
    return changed


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("gen/openapi")
    for path in sorted(root.rglob("*.openapi.yaml")):
        normalize(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
