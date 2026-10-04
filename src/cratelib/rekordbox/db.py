"""Read Rekordbox 6/7's own library: ``master.db`` plus its ANLZ analysis files.

pyrekordbox opens it (it is SQLCipher-encrypted with a publicly known key). Tracks, cues
and playlists are in the database; beat grids are only in the analysis files
(``share/PIONEER/USBANLZ/…/ANLZ0000.DAT``, the ``PQTZ`` tag), so point this at the whole
Rekordbox folder:

* macOS: ``~/Library/Pioneer/rekordbox``
* Windows: ``%APPDATA%\\Pioneer\\rekordbox``

Table layouts: pyrekordbox ``masterdb/tables.py``.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from ..colours import REKORDBOX_CUE_COLOURS, REKORDBOX_TRACK_COLOURS
from ..keys import parse_key
from ..model import Cue, CueRole, Library, Playlist, TempoMarker, Track
from . import anlz

# djmdCue.Kind: 0 is a memory cue; hot cues A-H skip 4 (pyrekordbox docs / observed exports).
_HOT_CUE_KIND = {1: 0, 2: 1, 3: 2, 5: 3, 6: 4, 7: 5, 8: 6, 9: 7}
_TRACK_COLOURS = list(REKORDBOX_TRACK_COLOURS)  # djmdColor IDs 1-8 in this order
PLAYLIST, FOLDER, SMART = 0, 1, 4  # djmdPlaylist.Attribute
# Files copied for a private read: the database, its write-ahead log (recent changes
# may still be there rather than in master.db), and the playlist file.
_COPIED = ("master.db-wal", "masterPlaylists6.xml")


def find_master_db(path: Path) -> Path:
    if path.is_file():
        return path
    for candidate in (path / "master.db", path / "rekordbox" / "master.db"):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"no Rekordbox master.db at {path}")


@contextmanager
def _private_copy(db_path: Path) -> Iterator[Any]:
    """pyrekordbox's view of a copy (Rekordbox may be running; the folder may be read-only)."""
    from pyrekordbox import Rekordbox6Database

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        shutil.copyfile(db_path, work / "master.db")
        for name in _COPIED:
            if (db_path.parent / name).is_file():
                shutil.copyfile(db_path.parent / name, work / name)
        db = Rekordbox6Database(path=work / "master.db", db_dir=work)
        try:
            yield db
        finally:
            db.close()
            db.engine.dispose()


def _date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def grid_from_anlz(path: Path) -> list[TempoMarker]:
    """Tempo sections from an analysis file's PQTZ beat grid."""
    return anlz.read_grid(anlz.AnlzFile.parse(path.read_bytes()))


def _track(c: Any) -> Track:
    colour_index = int(c.ColorID or 0)
    return Track(
        id=f"rbdb:{c.ID}",
        location=str(c.FolderPath),
        title=c.Title or "",
        artist=c.ArtistName or "",
        album=c.AlbumName or "",
        genre=c.GenreName or "",
        composer=c.ComposerName or "",
        comment=c.Commnt or "",
        label=c.LabelName or "",
        remixer=c.RemixerName or "",
        year=str(c.ReleaseYear) if c.ReleaseYear else "",
        track_number=int(c.TrackNo) if c.TrackNo else None,
        duration_s=float(c.Length or 0),
        sample_rate=int(c.SampleRate or 0),
        bitrate=int(c.BitRate or 0),
        file_size=int(c.FileSize or 0),
        bpm=(c.BPM or 0) / 100.0,
        key=parse_key(c.KeyName),
        rating=max(0, min(5, int(c.Rating or 0))),
        colour=_TRACK_COLOURS[colour_index - 1] if 1 <= colour_index <= 8 else None,
        play_count=int(c.DJPlayCount or 0),
        date_added=_date(c.DateCreated),
    )


def _cue(row: Any) -> Cue | None:
    start, end = row.InMsec, row.OutMsec
    if start is None or start < 0:
        return None
    loop = end is not None and end > start
    index = int(row.ColorTableIndex or 0)
    slot = _HOT_CUE_KIND.get(int(row.Kind or 0)) if row.Kind else None
    return Cue(
        CueRole.LOOP if loop else CueRole.CUE,
        float(start),
        float(end) if loop else None,
        slot=slot,
        name=row.Comment or "",
        colour=REKORDBOX_CUE_COLOURS[index - 1] if slot is not None and 1 <= index <= 16 else None,
    )


def read_rekordbox_db(
    path: Path, progress: Callable[[str], None] = print, read_grids: bool = True
) -> Library:
    db_path = find_master_db(path)
    share = db_path.parent / "share"
    library = Library(source=f"Rekordbox library at {db_path}")
    anlz_paths: dict[str, Path] = {}
    with _private_copy(db_path) as db:
        for content in db.get_content():
            if content.rb_local_deleted or not content.FolderPath:
                continue
            track = library.add_track(_track(content))
            if content.AnalysisDataPath:
                anlz_paths[track.id] = share / str(content.AnalysisDataPath).strip("/\\")
        for row in db.get_cue():
            track = library.tracks.get(f"rbdb:{row.ContentID}")
            if track is not None and not row.rb_local_deleted and (cue := _cue(row)):
                track.cues.append(cue)

        songs: dict[str, list[tuple[int, str]]] = {}
        for song in db.get_playlist_songs():
            if f"rbdb:{song.ContentID}" in library.tracks:
                songs.setdefault(str(song.PlaylistID), []).append(
                    (song.TrackNo or 0, f"rbdb:{song.ContentID}")
                )
        rows = sorted(db.get_playlist(), key=lambda p: p.Seq or 0)
        nodes: dict[str, Playlist] = {}
        smart = 0
        for row in rows:
            if row.Attribute == SMART:
                smart += 1
            elif row.Attribute == FOLDER:
                nodes[str(row.ID)] = Playlist.folder(row.Name or "")
            elif row.Attribute == PLAYLIST:
                ids = [tid for _, tid in sorted(songs.get(str(row.ID), []))]
                nodes[str(row.ID)] = Playlist(row.Name or "", ids)
            # Anything else is one of rekordbox's own lists, e.g. cloud sync (-128).
        for row in rows:
            if (node := nodes.get(str(row.ID))) is not None:
                nodes.get(str(row.ParentID), library.playlists).children.append(node)
    if smart:
        library.warnings.append(f"{smart} intelligent playlist(s) not converted.")

    if read_grids:
        missing = 0
        for n, (track_id, anlz_path) in enumerate(anlz_paths.items(), 1):
            if n % 500 == 0:
                progress(f"Reading beat grids {n}/{len(anlz_paths)}")
            try:
                library.tracks[track_id].grid = grid_from_anlz(anlz_path)
                library.tracks[track_id].extra["anlz"] = str(anlz_path)
            except (OSError, ValueError, IndexError):
                missing += 1
        if missing:
            library.warnings.append(
                f"{missing} track(s) have no readable analysis file under {share}, so no beat grid."
            )
    return library
