"""Edit a Serato library (``_Serato_/database V2`` and its crates) in place.

Playlists are crates: ``["SoundCloud", "ukg"]`` is the crate ``ukg`` inside
``SoundCloud`` (file ``Subcrates/SoundCloud%%ukg.crate``). Serato must be closed
when saving: it rewrites its database when it quits.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from ..audiofile import basic_tags, probe
from ..edit import Editor, PlaylistPath
from ..model import Playlist, Track, normalise_path
from ..paths import PathMap
from ..processes import serato_running
from .binfile import (
    CRATE_VERSION,
    Field,
    read_crate,
    read_database,
    write_crate,
    write_database,
)
from .library import (
    CRATE_DIR,
    DATABASE_FILE,
    SeratoWriteOptions,
    _crate_filename,
    _place_crate,
    _track_fields,
    _track_from_fields,
    find_serato_dir,
    to_serato_path,
)


class SeratoRunning(RuntimeError):
    """Serato is open, so its library can't be changed."""


def _key(location: str) -> str:
    return normalise_path(location).lower()


def _crate_fields(pfils: list[str]) -> list[Field]:
    fields = [
        Field("vrsn", CRATE_VERSION),
        Field("osrt", [Field("tvcn", "#"), Field("brev", False)]),
    ]
    for column in ("song", "artist", "bpm", "key", "album", "length", "comment"):
        fields.append(Field("ovct", [Field("tvcn", column), Field("tvcw", "0")]))
    fields += [Field("otrk", [Field("ptrk", p)]) for p in pfils]
    return fields


class SeratoEditor(Editor):
    def __init__(self, path: Path, volume_root: str, paths: PathMap):
        super().__init__(paths)
        self.dir = find_serato_dir(path)
        self.volume_root = volume_root or "/"
        self.database = read_database(self.dir / DATABASE_FILE)
        self.database_changed = False
        # Crate files to write (fields) or delete (None) on save, by file name.
        self.pending: dict[str, list[Field] | None] = {}

    # --- tracks ----------------------------------------------------------------------

    def tracks(self) -> list[Track]:
        found = []
        for index, entry in enumerate(self.database):
            if entry.tag == "otrk" and (
                track := _track_from_fields(entry, self.volume_root, index)
            ):
                track.file = self.paths.find(track.location)
                found.append(track)
        return found

    def add_track(self, file: Path, **fields: str) -> Track:
        file = Path(file)
        if self.track_at(file) is not None:
            raise ValueError(f"{file} is already in the library")
        tags = {**basic_tags(file), **fields}
        info = probe(file)
        track = Track(
            id="",
            location=self.paths.to_app(file),
            title=tags.get("title") or file.stem,
            artist=tags.get("artist", ""),
            album=tags.get("album", ""),
            genre=tags.get("genre", ""),
            year=tags.get("year", ""),
            duration_s=info.duration_s if info else 0.0,
            date_added=datetime.now(UTC).date(),
            file=file,
        )
        options = SeratoWriteOptions(volume_root=self.volume_root)
        entry = Field("otrk", _track_fields(track, options))
        self.database.append(entry)
        self.database_changed = True
        # Return the track as tracks() will see it.
        added = _track_from_fields(entry, self.volume_root, len(self.database) - 1)
        assert added is not None
        added.file = file
        return added

    # --- crates ----------------------------------------------------------------------

    def _crate_files(self) -> dict[str, Path | list[Field]]:
        """Every crate as saving would leave it: file name -> file, or pending fields."""
        crates: dict[str, Path | list[Field]] = {
            p.name: p for p in sorted((self.dir / CRATE_DIR).glob("*.crate"))
        }
        for name, fields in self.pending.items():
            if fields is None:
                crates.pop(name, None)
            else:
                crates[name] = fields
        return crates

    def _pfils(self, crate: Path | list[Field]) -> list[str]:
        fields = read_crate(crate) if isinstance(crate, Path) else crate
        return [p for f in fields if f.tag == "otrk" and isinstance(p := f.get("ptrk"), str)]

    def playlists(self) -> Playlist:
        by_pfil = {_key(to_serato_path(t.location, self.volume_root)): t.id for t in self.tracks()}
        root = Playlist.folder("ROOT")
        crates = self._crate_files()
        for name in sorted(crates, key=str.lower):
            ids = [by_pfil[k] for p in self._pfils(crates[name]) if (k := _key(p)) in by_pfil]
            _place_crate(root, name.removesuffix(".crate").split("%%"), ids)
        return root

    def set_playlist(self, path: PlaylistPath, tracks: Iterable[Track | str]) -> bool:
        if not path:
            raise ValueError("a playlist needs a name")
        by_id = {t.id: t for t in self.tracks()}
        pfils = []
        for t in tracks:
            track = by_id[t] if isinstance(t, str) else by_id.get(t.id, t)
            pfils.append(to_serato_path(track.location, self.volume_root))
        crates = self._crate_files()
        changed = False
        # Serato shows a crate inside its parent crates; make sure they exist.
        for depth in range(1, len(path)):
            parent = _crate_filename(tuple(path[:depth]))
            if parent not in crates:
                self.pending[parent] = _crate_fields([])
                changed = True
        name = _crate_filename(tuple(path))
        if name in crates and self._pfils(crates[name]) == pfils:
            return changed
        self.pending[name] = _crate_fields(pfils)
        return True

    def delete_playlist(self, path: PlaylistPath) -> bool:
        stem = _crate_filename(tuple(path)).removesuffix(".crate")
        doomed = [
            n for n in self._crate_files() if n == f"{stem}.crate" or n.startswith(f"{stem}%%")
        ]
        for name in doomed:
            self.pending[name] = None
        return bool(doomed)

    # --- saving ----------------------------------------------------------------------

    def save(self, backup: bool = True) -> Path | None:
        if serato_running():
            raise SeratoRunning("close Serato before saving changes to its library")
        made = None
        if backup and (self.database_changed or self.pending):
            made = self.dir.with_name(f"{self.dir.name}.cratelib-{datetime.now():%Y%m%d-%H%M%S}")
            made.mkdir()
            shutil.copy2(self.dir / DATABASE_FILE, made / DATABASE_FILE)
            if (self.dir / CRATE_DIR).is_dir():
                shutil.copytree(self.dir / CRATE_DIR, made / CRATE_DIR)
        if self.database_changed:
            write_database(self.dir / DATABASE_FILE, self.database)
        (self.dir / CRATE_DIR).mkdir(exist_ok=True)
        for name, fields in self.pending.items():
            if fields is None:
                (self.dir / CRATE_DIR / name).unlink(missing_ok=True)
            else:
                write_crate(self.dir / CRATE_DIR / name, fields)
        self.database_changed = False
        self.pending = {}
        return made

    def close(self) -> None:
        self.database = []
        self.pending = {}
