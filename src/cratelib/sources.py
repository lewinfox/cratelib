"""The libraries cratelib can read and write, and finding them."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, replace
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


def _xdg(var: str, default: Path) -> Path:
    """An XDG base folder: ``$var`` if set to an absolute path, else ``default``."""
    value = os.environ.get(var, "")
    return Path(value) if os.path.isabs(value) else default


def _music_dir(home: Path) -> Path:
    """The user's music folder, from xdg-user-dirs' ``user-dirs.dirs``, else ``~/Music``."""
    try:
        lines = (_xdg("XDG_CONFIG_HOME", home / ".config") / "user-dirs.dirs").read_text()
    except OSError:
        lines = ""
    for line in lines.splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "XDG_MUSIC_DIR":
            value = value.strip().strip('"')
            if value.startswith("$HOME"):
                return home / value.removeprefix("$HOME").lstrip("/")
            if os.path.isabs(value):
                return Path(value)
    return home / "Music"


def _wine_rekordbox(prefix: Path) -> list[Path]:
    """Rekordbox's folder for each user of a Wine prefix (rekordbox's %APPDATA%)."""
    return sorted(prefix.glob("drive_c/users/*/AppData/Roaming/Pioneer/rekordbox"))


def installed(home: Path | None = None) -> list[Source]:
    """The libraries in each program's usual place on this computer.

    On Linux, Rekordbox and Serato run under Wine: Rekordbox in a prefix at
    ``$XDG_DATA_HOME/rekordbox-wine/prefix`` (or ``~/.wine``), Serato with its
    ``_Serato_`` in the XDG music folder, storing ``C:/`` paths.
    """
    home = home or Path.home()
    wine = False
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
        data = _xdg("XDG_DATA_HOME", home / ".local/share")
        places = [
            *_wine_rekordbox(data / "rekordbox-wine/prefix"),
            *_wine_rekordbox(home / ".wine"),
            _music_dir(home) / "_Serato_",
            data / "mixxx",
            home / ".mixxx",
        ]
        wine = True
    found: list[Source] = []
    for place in places:
        if place.is_dir():
            found += detect(place)
    return [replace(s, volume_root="C:/") if wine and isinstance(s, Serato) else s for s in found]
