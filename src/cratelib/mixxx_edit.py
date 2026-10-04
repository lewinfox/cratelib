"""Edit a Mixxx library (``mixxxdb.sqlite``) in place.

Mixxx has no playlist folders, so a playlist at ``["SoundCloud", "ukg"]`` is
named ``SoundCloud / ukg``, as :func:`cratelib.write` names them. Crates are
left alone. Mixxx must be closed when saving.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from .audiofile import basic_tags, probe
from .edit import Editor, PlaylistPath
from .mixxx import find_database
from .model import Playlist, Track, path_name
from .paths import PathMap
from .processes import mixxx_running


class MixxxRunning(RuntimeError):
    """Mixxx is open, so its library can't be changed."""


def _name(path: PlaylistPath) -> str:
    if not path:
        raise ValueError("a playlist needs a name")
    return " / ".join(path)


class MixxxEditor(Editor):
    def __init__(self, path: Path, paths: PathMap):
        super().__init__(paths)
        self.db_path = find_database(path)
        # Changes stay in one open transaction until save() commits them.
        self.db = sqlite3.connect(self.db_path, isolation_level="DEFERRED")

    def tracks(self) -> list[Track]:
        rows = self.db.execute(
            """SELECT l.id, tl.location, l.title, l.artist FROM library l
               JOIN track_locations tl ON tl.id = l.location WHERE l.mixxx_deleted = 0"""
        )
        return [
            Track(
                id=f"mixxx:{tid}",
                location=location,
                title=title or "",
                artist=artist or "",
                file=self.paths.find(location),
            )
            for tid, location, title, artist in rows
        ]

    def add_track(self, file: Path, **fields: str) -> Track:
        file = Path(file)
        if self.track_at(file) is not None:
            raise ValueError(f"{file} is already in the library")
        tags = {**basic_tags(file), **fields}
        info = probe(file)
        location = self.paths.to_app(file)
        name = path_name(location)
        directory = location[: -len(name) - 1] or "/"
        loc_id = self.db.execute(
            """INSERT INTO track_locations (location, filename, directory, filesize,
                   fs_deleted, needs_verification) VALUES (?, ?, ?, ?, 0, 0)""",
            (location, name, directory, info.file_size if info else file.stat().st_size),
        ).lastrowid
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        values = {
            "location": loc_id,
            "title": tags.get("title") or file.stem,
            "artist": tags.get("artist", ""),
            "album": tags.get("album", ""),
            "genre": tags.get("genre", ""),
            "year": tags.get("year", ""),
            "duration": info.duration_s if info else 0,
            "bitrate": info.bitrate if info else 0,
            "samplerate": info.sample_rate if info else 0,
            "channels": (info.channels if info else 0) or 2,
            "filetype": file.suffix.lstrip(".").lower(),
            "datetime_added": now,
            "mixxx_deleted": 0,
            "header_parsed": 0,  # Mixxx reads the file's own tags when it next loads it
        }
        tid = self.db.execute(
            f"INSERT INTO library ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
            tuple(values.values()),
        ).lastrowid
        return Track(
            id=f"mixxx:{tid}",
            location=location,
            title=str(values["title"]),
            artist=str(values["artist"]),
            file=file,
        )

    def playlists(self) -> Playlist:
        root = Playlist.folder("ROOT")
        for pid, name in self.db.execute(
            "SELECT id, name FROM Playlists WHERE hidden = 0 ORDER BY position, name"
        ).fetchall():
            root.children.append(Playlist(name, self._track_ids(pid)))
        return root

    def _track_ids(self, playlist_id: int) -> list[str]:
        rows = self.db.execute(
            "SELECT track_id FROM PlaylistTracks WHERE playlist_id = ? ORDER BY position",
            (playlist_id,),
        )
        return [f"mixxx:{r[0]}" for r in rows]

    def _find(self, name: str) -> int | None:
        row = self.db.execute(
            "SELECT id FROM Playlists WHERE name = ? AND hidden = 0", (name,)
        ).fetchone()
        return int(row[0]) if row else None

    def playlist(self, path: PlaylistPath) -> list[Track] | None:
        pid = self._find(_name(path))
        if pid is None:
            return None
        by_id = {t.id: t for t in self.tracks()}
        return [by_id[t] for t in self._track_ids(pid) if t in by_id]

    def set_playlist(self, path: PlaylistPath, tracks: Iterable[Track | str]) -> bool:
        name = _name(path)
        wanted = [t.id if isinstance(t, Track) else t for t in tracks]
        now = datetime.now().isoformat(timespec="seconds")
        pid = self._find(name)
        if pid is None:
            position = self.db.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 FROM Playlists"
            ).fetchone()[0]
            pid = self.db.execute(
                """INSERT INTO Playlists (name, position, hidden, date_created, date_modified)
                   VALUES (?, ?, 0, ?, ?)""",
                (name, position, now, now),
            ).lastrowid
        elif self._track_ids(pid) == wanted:
            return False
        self.db.execute("DELETE FROM PlaylistTracks WHERE playlist_id = ?", (pid,))
        self.db.executemany(
            "INSERT INTO PlaylistTracks (playlist_id, track_id, position, pl_datetime_added) "
            "VALUES (?, ?, ?, ?)",
            [(pid, int(t.removeprefix("mixxx:")), i, now) for i, t in enumerate(wanted, 1)],
        )
        self.db.execute("UPDATE Playlists SET date_modified = ? WHERE id = ?", (now, pid))
        return True

    def delete_playlist(self, path: PlaylistPath) -> bool:
        name = _name(path)
        # A "folder" is every playlist named below it.
        rows = self.db.execute(
            "SELECT id FROM Playlists WHERE hidden = 0 AND (name = ? OR name LIKE ? ESCAPE '\\')",
            (name, name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + " / %"),
        ).fetchall()
        for (pid,) in rows:
            self.db.execute("DELETE FROM PlaylistTracks WHERE playlist_id = ?", (pid,))
            self.db.execute("DELETE FROM Playlists WHERE id = ?", (pid,))
        return bool(rows)

    def save(self, backup: bool = True) -> Path | None:
        if mixxx_running():
            raise MixxxRunning("close Mixxx before saving changes to its library")
        made = None
        if backup:
            made = self.db_path.with_name(
                f"{self.db_path.name}.cratelib-{datetime.now():%Y%m%d-%H%M%S}"
            )
            # A separate connection sees the library as it was before this editor's changes.
            source = sqlite3.connect(self.db_path)
            target = sqlite3.connect(made)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
        self.db.commit()
        return made

    def close(self) -> None:
        self.db.rollback()
        self.db.close()
