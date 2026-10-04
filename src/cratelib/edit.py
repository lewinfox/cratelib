"""Changing a library in place: what every editable format offers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from pathlib import Path
from types import TracebackType

from .model import Playlist, Track, is_windows_path, normalise_path
from .paths import PathMap

PlaylistPath = Sequence[str]  # folder names, then the playlist's name: ["SoundCloud", "ukg"]


def _same_location(a: str, b: str) -> bool:
    if is_windows_path(a) or is_windows_path(b):
        return normalise_path(a).casefold() == normalise_path(b).casefold()
    return a == b


class Editor(ABC):
    """An open library. Changes are only written by :meth:`save`.

    Use it as a context manager: leaving the block without saving throws the
    changes away. Tracks are :class:`~cratelib.model.Track` objects with the
    library's own ids; only the basic fields (location, title, artist) are filled.
    """

    def __init__(self, paths: PathMap):
        self.paths = paths

    def __enter__(self) -> Editor:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    @abstractmethod
    def tracks(self) -> list[Track]:
        """Every track in the library."""

    def track_at(self, file: Path | str) -> Track | None:
        """The track for a local file, if the library has it."""
        location = self.paths.to_app(file)
        return next((t for t in self.tracks() if _same_location(t.location, location)), None)

    @abstractmethod
    def add_track(self, file: Path, **fields: str) -> Track:
        """Add a local file to the library and return its track.

        Title, artist, album, genre and year come from the file's tags unless given in
        ``fields``. Raises ValueError if the library already has the file.
        """

    @abstractmethod
    def playlists(self) -> Playlist:
        """The playlist tree: the root folder, with each playlist's track ids."""

    def playlist(self, path: PlaylistPath) -> list[Track] | None:
        """The tracks in a playlist, in order, or None if there's no such playlist."""
        node = self.playlists()
        for name in path:
            node = next((c for c in node.children if c.name == name), None)
            if node is None:
                return None
        if node.is_folder:
            return None
        by_id = {t.id: t for t in self.tracks()}
        return [by_id[tid] for tid in node.track_ids or [] if tid in by_id]

    @abstractmethod
    def set_playlist(self, path: PlaylistPath, tracks: Iterable[Track | str]) -> bool:
        """Make the playlist at ``path`` hold exactly ``tracks`` (tracks or track ids), in order.

        Folders and the playlist are created if missing. Returns whether anything
        changed: setting the same contents again changes nothing.
        """

    @abstractmethod
    def delete_playlist(self, path: PlaylistPath) -> bool:
        """Delete a playlist or folder. Returns whether there was one."""

    @abstractmethod
    def save(self, backup: bool = True) -> Path | None:
        """Write the changes. Returns where the backup of the old files went, if made."""

    @abstractmethod
    def close(self) -> None:
        """Close the library, throwing away unsaved changes."""
