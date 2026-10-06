"""Technical details of an audio file, from its headers."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import mutagen
from mutagen.id3 import ID3, TXXX, ID3NoHeaderError


@dataclass(frozen=True)
class AudioInfo:
    sample_rate: int
    duration_s: float
    bitrate: int  # kbps
    channels: int
    file_size: int


def probe(path: Path) -> AudioInfo | None:
    try:
        audio = mutagen.File(path)
    except Exception:
        return None
    if audio is None or audio.info is None:
        return None
    info = audio.info
    return AudioInfo(
        sample_rate=int(getattr(info, "sample_rate", 0) or 0),
        duration_s=float(getattr(info, "length", 0) or 0),
        bitrate=int((getattr(info, "bitrate", 0) or 0) / 1000),
        channels=int(getattr(info, "channels", 0) or 0),
        file_size=path.stat().st_size,
    )


def basic_tags(path: Path) -> dict[str, str]:
    """Title, artist, album, genre and year from the file's tags (whichever exist)."""
    try:
        audio = mutagen.File(path, easy=True)
    except Exception:
        return {}
    if audio is None or audio.tags is None:
        return {}
    found = {}
    for key in ("title", "artist", "album", "genre", "date"):
        values = audio.tags.get(key)
        if values and str(values[0]).strip():
            found["year" if key == "date" else key] = str(values[0]).strip()
    return found


AUDIO_HASH = "AUDIO_SHA256"  # ID3 TXXX description, shared with scdl


def audio_sha256(path: Path) -> str:
    """SHA-256 of an MP3's audio only, skipping the ID3v2 block at the start
    and any ID3v1 block at the end, so retagging doesn't change the hash."""
    size = path.stat().st_size
    with path.open("rb") as f:
        head = f.read(10)
        start = 0
        if head[:3] == b"ID3" and len(head) == 10:
            # The ID3v2 size is 4 "syncsafe" bytes of 7 bits each and excludes
            # the 10-byte header (and the 10-byte footer, if the flag says so).
            body = 0
            for b in head[6:10]:
                body = (body << 7) | (b & 0x7F)
            start = 10 + body + (10 if head[5] & 0x10 else 0)
        end = size
        if size - start >= 128:
            f.seek(size - 128)
            if f.read(3) == b"TAG":
                end -= 128
        h = hashlib.sha256()
        f.seek(start)
        remaining = max(end - start, 0)
        while remaining:
            chunk = f.read(min(remaining, 1 << 20))
            if not chunk:
                break
            h.update(chunk)
            remaining -= len(chunk)
    return h.hexdigest()


def add_audio_hash(tags: ID3, path: Path) -> None:
    """Set ``TXXX:AUDIO_SHA256`` to the hash of the MP3 at ``path``. Saving
    the tags only rewrites the ID3 block, which the hash skips."""
    tags.add(TXXX(encoding=3, desc=AUDIO_HASH, text=audio_sha256(path)))


def stamp_audio_hash(path: Path) -> None:
    """Add the audio hash to an MP3's tags, keeping its ID3 version."""
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    add_audio_hash(tags, path)
    tags.save(path, v2_version=4 if tags.version >= (2, 4, 0) else 3)
