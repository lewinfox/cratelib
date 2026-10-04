"""Serato's ``database V2`` and ``.crate`` files, through serato-tools.

Both are a tag-length-value container: a 4-byte ASCII tag, a big-endian u32
length, then the payload, whose type the tag's first letter gives. serato-tools
parses and writes it, and refuses versions it hasn't been tested with. This
module turns its ``(tag, value)`` tuples into :class:`Field` objects and back.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from serato_tools.crate import Crate
from serato_tools.database_v2 import DatabaseV2
from serato_tools.utils.bin_file_base import SeratoBinFile

Value = str | int | bool | bytes | list["Field"]

DATABASE_VERSION = "2.0/Serato Scratch LIVE Database"
CRATE_VERSION = "1.0/Serato ScratchLive Crate"


class SeratoFormatError(ValueError):
    pass


@dataclass
class Field:
    tag: str
    value: Value

    def get(self, tag: str) -> Value | None:
        """First child with ``tag`` (nested fields only)."""
        if isinstance(self.value, list):
            for child in self.value:
                if child.tag == tag:
                    return child.value
        return None


class _Database(DatabaseV2):
    # DatabaseV2 refuses a file that doesn't exist yet; its base class starts an
    # empty one from DEFAULT_ENTRIES instead.
    def __init__(self, file: str):
        SeratoBinFile.__init__(self, file)


@contextlib.contextmanager
def _quiet() -> Iterator[None]:
    # serato-tools logs a warning whenever it starts a file from scratch.
    log = logging.getLogger("serato-tools")
    level = log.level
    log.setLevel(logging.ERROR)
    try:
        yield
    finally:
        log.setLevel(level)


def _fields(entries: list[Any]) -> list[Field]:
    return [Field(str(tag), _fields(v) if isinstance(v, list) else v) for tag, v in entries]


def _entries(fields: list[Field]) -> list[Any]:
    return [(f.tag, _entries(f.value) if isinstance(f.value, list) else f.value) for f in fields]


def _load(cls: type[SeratoBinFile], path: Path) -> list[Field]:
    try:
        return _fields(cls(str(path)).entries)
    except (ValueError, TypeError, AssertionError, UnicodeDecodeError) as exc:
        raise SeratoFormatError(f"{path.name}: {exc}") from exc


def read_database(path: Path) -> list[Field]:
    return _load(DatabaseV2, path)


def read_crate(path: Path) -> list[Field]:
    return _load(Crate, path)


def _save(file: SeratoBinFile, fields: list[Field], path: Path) -> None:
    file.entries = _entries(fields)
    file._dump()  # DatabaseV2.save() writes the bytes from the last parse or edit
    file.save(str(path))


def write_database(path: Path, fields: list[Field]) -> None:
    # Start from an empty file at a name that doesn't exist, so the old one isn't parsed.
    with _quiet():
        database = _Database(str(path.with_name(path.name + ".cratelib-new")))
    _save(database, fields, path)


def write_crate(path: Path, fields: list[Field]) -> None:
    with _quiet():
        crate = Crate(str(path.with_name(path.stem + ".cratelib-new.crate")))
    _save(crate, fields, path)
