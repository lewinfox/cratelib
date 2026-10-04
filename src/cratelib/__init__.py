"""Read and write DJ libraries and USB sticks for Rekordbox, Serato and Mixxx.

import cratelib

library = cratelib.read(cratelib.Serato(Path("~/Music/_Serato_").expanduser()))
cratelib.write(library, cratelib.RekordboxUsb(Path("/media/STICK")))

paths = cratelib.PathMap({"/home/me/Music": "C:/users/dj/Music"})
with cratelib.open(cratelib.RekordboxDb(rekordbox_dir), paths) as rb:
    track = rb.track_at(file) or rb.add_track(file)
    rb.set_playlist(["SoundCloud", "ukg"], [track])
    rb.save()
"""

from .api import (
    MixxxOptions,
    RekordboxUsbOptions,
    RekordboxXmlOptions,
    SeratoOptions,
    WriteOptions,
    WriteResult,
    open,
    read,
    write,
)
from .edit import Editor
from .keys import KeyNotation
from .model import Cue, CueRole, Format, Key, Library, Playlist, TempoMarker, Track
from .offsets import Mp3Decoder
from .paths import PathMap
from .sources import (
    Mixxx,
    RekordboxDb,
    RekordboxUsb,
    RekordboxXml,
    Serato,
    Source,
    detect,
    installed,
)

__all__ = [
    "Cue",
    "CueRole",
    "Editor",
    "Format",
    "Key",
    "KeyNotation",
    "Library",
    "Mixxx",
    "MixxxOptions",
    "Mp3Decoder",
    "PathMap",
    "Playlist",
    "RekordboxDb",
    "RekordboxUsb",
    "RekordboxUsbOptions",
    "RekordboxXml",
    "RekordboxXmlOptions",
    "Serato",
    "SeratoOptions",
    "Source",
    "TempoMarker",
    "Track",
    "WriteOptions",
    "WriteResult",
    "detect",
    "installed",
    "open",
    "read",
    "write",
]
