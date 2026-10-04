"""The libraries cratelib can read and write, and finding them."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .detect import detect_libraries
from .model import Format
from .offsets import Mp3Decoder


@dataclass(frozen=True)
class Mixxx:
    """A Mixxx library: ``mixxxdb.sqlite``, or the folder holding it."""

    path: Path
    # Which MP3 decoder this Mixxx uses; it changes where cues land in some MP3s.
    mp3_decoder: Mp3Decoder = Mp3Decoder.MAD
    format = Format.MIXXX


@dataclass(frozen=True)
class RekordboxDb:
    """Rekordbox 6/7's own library: ``master.db``, or the folder holding it."""

    path: Path
    format = Format.REKORDBOX_DB


@dataclass(frozen=True)
class RekordboxXml:
    """A Rekordbox XML file (``DJ_PLAYLISTS``)."""

    path: Path
    format = Format.REKORDBOX_XML


@dataclass(frozen=True)
class RekordboxUsb:
    """A Rekordbox USB stick or other device: the drive's root."""

    path: Path
    format = Format.REKORDBOX_USB


@dataclass(frozen=True)
class Serato:
    """A Serato library: the ``_Serato_`` folder, its parent, or ``database V2``.

    ``volume_root`` is the drive Serato stores paths relative to: ``/`` for the
    computer's own drive on macOS and Linux, ``C:/`` on Windows, or the mount point
    of a USB stick with its own ``_Serato_``.
    """

    path: Path
    volume_root: str = "/"
    format = Format.SERATO


Source = Mixxx | RekordboxDb | RekordboxXml | RekordboxUsb | Serato


def _source(found: dict[str, str]) -> Source:
    path = Path(found["path"])
    match found["format"]:
        case Format.MIXXX:
            return Mixxx(path)
        case Format.REKORDBOX_DB:
            return RekordboxDb(path)
        case Format.REKORDBOX_XML:
            return RekordboxXml(path)
        case Format.REKORDBOX_USB:
            return RekordboxUsb(path)
        case Format.SERATO:
            return Serato(path, found.get("serato_root", "/"))
    raise ValueError(f"unknown format {found['format']!r}")


def detect(path: Path) -> list[Source]:
    """Every library at ``path``: a library file, or a folder or drive holding some."""
    return [_source(found) for found in detect_libraries(Path(path))]


def installed(home: Path | None = None) -> list[Source]:
    """The libraries in each program's usual place on this computer."""
    home = home or Path.home()
    if sys.platform == "darwin":
        places = [
            home / "Library/Pioneer/rekordbox",
            home / "Music/_Serato_",
            home / "Library/Containers/org.mixxx.mixxx/Data/Library/Application Support/Mixxx",
            home / "Library/Application Support/Mixxx",
        ]
    elif sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA", home / "AppData/Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
        places = [appdata / "Pioneer/rekordbox", home / "Music/_Serato_", local / "Mixxx"]
    else:
        places = [home / ".mixxx", home / ".local/share/mixxx", home / "Music/_Serato_"]
    found: list[Source] = []
    for place in places:
        if place.is_dir():
            found += detect(place)
    return found
