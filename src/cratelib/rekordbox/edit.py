"""Edit Rekordbox 6/7's own library (``master.db``) in place, through pyrekordbox.

pyrekordbox does the writing: it keeps rekordbox's change counters (USNs) and
``masterPlaylists6.xml`` in step with the database, which rekordbox needs to
see the changes. Rekordbox must be closed when saving.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from ..audiofile import basic_tags, probe
from ..edit import Editor, PlaylistPath
from ..model import Playlist, Track
from ..paths import PathMap
from .db import FOLDER, PLAYLIST, find_master_db

# Files saved together as a backup: the database, its write-ahead log, and the
# playlist file pyrekordbox updates alongside it.
_BACKED_UP = ("master.db", "master.db-wal", "master.db-shm", "masterPlaylists6.xml")


class RekordboxRunning(RuntimeError):
    """Rekordbox is open, so its library can't be changed."""


def is_running() -> bool:
    """Whether rekordbox is running on this machine (including under Wine)."""
    from pyrekordbox.utils import get_rekordbox_pid

    return bool(get_rekordbox_pid())


class RekordboxEditor(Editor):
    def __init__(self, path: Path, paths: PathMap):
        super().__init__(paths)
        from pyrekordbox import Rekordbox6Database

        self.db_path = find_master_db(path)
        self.db: Any = Rekordbox6Database(path=self.db_path, db_dir=self.db_path.parent)

    # --- tracks ----------------------------------------------------------------------

    def _track(self, content: Any) -> Track:
        location = content.FolderPath or ""
        return Track(
            id=f"rbdb:{content.ID}",
            location=location,
            title=content.Title or "",
            artist=content.Artist.Name if content.Artist else "",
            file=self.paths.find(location),
        )

    def _contents(self) -> list[Any]:
        return [c for c in self.db.get_content() if not c.rb_local_deleted and c.FolderPath]

    def tracks(self) -> list[Track]:
        return [self._track(c) for c in self._contents()]

    def add_track(self, file: Path, **fields: str) -> Track:
        file = Path(file)
        if self.track_at(file) is not None:
            raise ValueError(f"{file} is already in the library")
        tags = {**basic_tags(file), **fields}
        columns: dict[str, Any] = {"Title": tags.get("title") or file.stem}
        if info := probe(file):
            columns |= {
                "Length": round(info.duration_s),
                "BitRate": info.bitrate,
                "SampleRate": info.sample_rate,
            }
        if year := tags.get("year", "")[:4]:
            columns["ReleaseYear"] = int(year) if year.isdigit() else None
        for column, getter, adder, name in (
            ("ArtistID", self.db.get_artist, self.db.add_artist, tags.get("artist")),
            ("AlbumID", self.db.get_album, self.db.add_album, tags.get("album")),
            ("GenreID", self.db.get_genre, self.db.add_genre, tags.get("genre")),
        ):
            if name:
                row = getter(Name=name).first() or adder(name)
                columns[column] = row.ID
        # pyrekordbox reads the file's size from the path it's given, so add the local
        # file, then store the path the way rekordbox sees it.
        content = self.db.add_content(file, **columns)
        content.FolderPath = self.paths.to_app(file)
        self.db.flush()
        return self._track(content)

    # --- playlists -------------------------------------------------------------------

    def _children(self, parent_id: str) -> list[Any]:
        return sorted(self.db.get_playlist(ParentID=parent_id).all(), key=lambda p: p.Seq or 0)

    def _find(self, path: PlaylistPath) -> Any | None:
        node: Any = None
        for name in path:
            node = next(
                (p for p in self._children(node.ID if node else "root") if p.Name == name), None
            )
            if node is None:
                return None
        return node

    def playlists(self) -> Playlist:
        songs: dict[str, list[tuple[int, str]]] = {}
        for song in self.db.get_playlist_songs():
            songs.setdefault(str(song.PlaylistID), []).append(
                (song.TrackNo or 0, f"rbdb:{song.ContentID}")
            )

        def build(parent_id: str, folder: Playlist) -> Playlist:
            for row in self._children(parent_id):
                if row.Attribute == FOLDER:
                    folder.children.append(build(row.ID, Playlist.folder(row.Name or "")))
                elif row.Attribute == PLAYLIST:
                    ids = [tid for _, tid in sorted(songs.get(str(row.ID), []))]
                    folder.children.append(Playlist(row.Name or "", ids))
                # Intelligent playlists and rekordbox's own lists are left out.
            return folder

        return build("root", Playlist.folder("ROOT"))

    def set_playlist(self, path: PlaylistPath, tracks: Iterable[Track | str]) -> bool:
        if not path:
            raise ValueError("a playlist needs a name")
        wanted = [(t.id if isinstance(t, Track) else t).removeprefix("rbdb:") for t in tracks]
        parent: Any = None
        for name in path[:-1]:
            siblings = self._children(parent.ID if parent else "root")
            folder = next((p for p in siblings if p.Name == name), None)
            if folder is None:
                folder = self.db.create_playlist_folder(name, parent=parent)
            elif folder.Attribute != FOLDER:
                raise ValueError(f"{name!r} is a playlist, not a folder")
            parent = folder
        playlist = self._find(path)
        if playlist is None:
            playlist = self.db.create_playlist(path[-1], parent=parent)
        elif playlist.Attribute != PLAYLIST:
            raise ValueError(f"{' / '.join(path)} is not an ordinary playlist")
        songs = sorted(
            self.db.get_playlist_songs(PlaylistID=playlist.ID).all(), key=lambda s: s.TrackNo or 0
        )
        if [str(s.ContentID) for s in songs] == wanted:
            return False
        for song in songs:
            self.db.remove_from_playlist(playlist, song)
        for content_id in wanted:
            self.db.add_to_playlist(playlist, content_id)
        return True

    def delete_playlist(self, path: PlaylistPath) -> bool:
        node = self._find(path)
        if node is None:
            return False
        self.db.delete_playlist(node)
        return True

    # --- saving ----------------------------------------------------------------------

    def save(self, backup: bool = True) -> Path | None:
        if is_running():
            raise RekordboxRunning("close rekordbox before saving changes to its library")
        made = None
        if backup:
            made = self.db_path.with_name(
                f"{self.db_path.name}.cratelib-{datetime.now():%Y%m%d-%H%M%S}"
            )
            made.mkdir()
            for name in _BACKED_UP:
                if (self.db_path.parent / name).is_file():
                    shutil.copy2(self.db_path.parent / name, made / name)
        self.db.commit()
        return made

    def close(self) -> None:
        if self.db.session is not None:
            self.db.rollback()
            self.db.close()
        # Also close the pooled connection, so SQLite merges the write-ahead log into
        # master.db now rather than whenever the process exits.
        self.db.engine.dispose()
