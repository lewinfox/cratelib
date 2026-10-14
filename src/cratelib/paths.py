"""Path rewrite rules and finding a library's files on this machine."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .model import normalise_path


def apply_rules(path: str, rules: list[tuple[str, str]]) -> str:
    """Replace the longest matching prefix. Matching ignores slash direction and case."""
    norm = normalise_path(path)
    best: tuple[str, str] | None = None
    for old, new in rules:
        old_n = normalise_path(old).rstrip("/")
        if not old_n:
            continue
        if (norm.lower() == old_n.lower() or norm.lower().startswith(old_n.lower() + "/")) and (
            best is None or len(old_n) > len(normalise_path(best[0]).rstrip("/"))
        ):
            best = (old, new)
    if best is None:
        return path
    old_n = normalise_path(best[0]).rstrip("/")
    new = best[1].rstrip("/\\")
    rest = norm[len(old_n) :]
    result = new + rest
    return result.replace("/", "\\") if "\\" in best[1] else result


def parse_rules(text: str | list[Any] | None) -> list[tuple[str, str]]:
    """Rules as ``[[from, to], ...]`` or lines of ``from => to``."""
    if not text:
        return []
    if isinstance(text, list):
        return [(str(a), str(b)) for a, b in text if str(a).strip()]
    rules = []
    for line in text.splitlines():
        if "=>" in line:
            old, new = line.split("=>", 1)
            if old.strip():
                rules.append((old.strip(), new.strip()))
    return rules


def make_resolver(access_rules: list[tuple[str, str]]) -> Callable[[str], Path | None]:
    """Library location -> an existing local file, applying the file-access rules."""

    def resolve(location: str) -> Path | None:
        candidate = apply_rules(location, access_rules)
        if len(candidate) >= 2 and candidate[1] == ":" and os.name != "nt":
            return None  # a Windows path we have no mapping for
        path = Path(candidate)
        return path if path.is_file() else None

    return resolve


def infer_access_rules(
    locations: list[str], roots: list[Path], sample: int = 200
) -> list[tuple[str, str]]:
    """Guess file-access rules by looking for the tracks under the folders we can see.

    For ``C:/users/dj/Music/house/a.mp3`` and a root ``/home/me/Music`` holding
    ``house/a.mp3``, the rule is ``C:/users/dj/Music => /home/me/Music``.
    """
    step = max(1, len(locations) // sample)
    votes: dict[tuple[str, str], int] = {}
    for location in locations[::step]:
        parts = normalise_path(location).split("/")
        hit = None
        for i in range(1, len(parts)):
            prefix = "/".join(parts[:i])
            if not prefix:
                continue
            for root in roots:
                try:
                    if root.joinpath(*parts[i:]).is_file():
                        hit = (prefix, str(root))
                        break
                except OSError:
                    continue
            if hit:
                break
        if hit and hit[0] != hit[1]:
            votes[hit] = votes.get(hit, 0) + 1
    best: dict[str, tuple[str, int]] = {}
    for (prefix, root), count in votes.items():
        if prefix not in best or count > best[prefix][1]:
            best[prefix] = (root, count)
    return [(prefix, root) for prefix, (root, _) in best.items()]


class PathMap:
    """How a DJ program sees the files on this machine.

    ``PathMap({"/home/me/Music": "C:/users/dj/Music"})`` says a program running
    under Wine sees ``/home/me/Music/a.mp3`` as ``C:/users/dj/Music/a.mp3``. Keys are
    local folders, values are the same folders as the program stores them. The
    longest matching prefix wins; matching ignores case and slash direction. Paths no
    entry covers are the same on both sides.
    """

    def __init__(self, mapping: dict[str, str] | None = None):
        self.mapping = dict(mapping or {})

    def __repr__(self) -> str:
        return f"PathMap({self.mapping!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, PathMap) and self.mapping == other.mapping

    def to_app(self, file: Path | str) -> str:
        """A local path as the program stores it."""
        return apply_rules(str(file), list(self.mapping.items()))

    def to_local(self, location: str) -> Path | None:
        """Where a location the program stores is on this machine (whether or not it
        exists), or None for a Windows path this machine can't open."""
        candidate = apply_rules(location, [(app, local) for local, app in self.mapping.items()])
        if len(candidate) >= 2 and candidate[1] == ":" and os.name != "nt":
            return None
        return Path(candidate)

    def find(self, location: str) -> Path | None:
        """The local file for a stored location, if it exists."""
        path = self.to_local(location)
        try:
            return path if path is not None and path.is_file() else None
        except OSError:
            return None

    @classmethod
    def infer(cls, locations: list[str], roots: list[Path]) -> PathMap:
        """Guess a PathMap by looking for the tracks' files under ``roots``.

        For ``C:/users/dj/Music/house/a.mp3`` and a root ``/home/me/Music`` holding
        ``house/a.mp3``, this gives ``PathMap({"/home/me/Music": "C:/users/dj/Music"})``.
        """
        return cls({local: app for app, local in infer_access_rules(locations, roots)})


# --- finding where a library's files went --------------------------------------------

AUDIO_EXTENSIONS = frozenset(
    {".mp3", ".flac", ".wav", ".aif", ".aiff", ".m4a", ".mp4", ".aac", ".alac", ".ogg", ".opus"}
)


def _within(path: Path, bounds: list[Path]) -> bool:
    return any(path == b or b in path.parents for b in bounds)


class _Index:
    """Audio files by lower-case name, from the folders listed so far. Each folder is
    listed once, however often the search comes back to it."""

    def __init__(self, progress: Callable[[str], None]):
        self.files: dict[str, list[tuple[Path, int]]] = {}
        self.listed: set[Path] = set()
        self.progress = progress

    def add(self, top: Path) -> None:
        if top in self.listed:
            return
        self.progress(f"Looking for music in {top}")
        stack = [top]
        while stack:
            folder = stack.pop()
            if folder in self.listed:
                continue  # listed before, as a search further down
            self.listed.add(folder)
            try:
                entries = list(os.scandir(folder))
            except OSError:
                continue
            for e in entries:
                if e.name.startswith("."):
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(Path(e.path))
                    elif e.is_file() and os.path.splitext(e.name)[1].lower() in AUDIO_EXTENSIONS:
                        self.files.setdefault(e.name.lower(), []).append(
                            (Path(e.path), e.stat().st_size)
                        )
                except OSError:
                    continue


def _rule(location: str, file: Path) -> tuple[str, str] | None:
    """(the program's folder, ours) that turn ``location`` into ``file``: everything
    before the path ending the two share. None if only the drive differs."""
    app = [p for p in normalise_path(location).split("/") if p != ""]
    ours = list(file.parts)
    k = 0
    while k < min(len(app), len(ours) - 1) and app[-1 - k].lower() == ours[-1 - k].lower():
        k += 1
    if k == 0 or k == len(app):
        return None
    prefix = "/".join(app[: len(app) - k])
    if normalise_path(location).startswith("/"):
        prefix = "/" + prefix
    elif len(prefix) == 2 and prefix[1] == ":":
        prefix += "/"
    return prefix, str(Path(*ours[: len(ours) - k]))


def discover(
    tracks: list[tuple[str, int]],
    start: list[Path],
    bounds: list[Path],
    paths: PathMap | None = None,
    *,
    min_votes: int = 2,
    progress: Callable[[str], None] = lambda _: None,
) -> PathMap:
    """Work out where a library's files are on this machine, when its program stores
    them under other paths (Wine, a container, another computer, a changed drive
    letter). ``tracks`` are (location, file size; 0 if unknown).

    A track that ``paths`` can't find is looked for by name (and size) under
    ``start``. The path ending its location and the file found share gives a rule
    (``C:/foo/bar/music/a.mp3`` found at ``/home/me/Music/foo/music/a.mp3``:
    ``C:/foo/bar`` is ``/home/me/Music/foo``), which counts once at least
    ``min_votes`` tracks agree on it (or, with none that well supported, one track
    whose name and size are unique). Then the next track still missing; when nothing
    more turns up, the search widens to the folders above those searched, never above
    ``bounds``. Each folder is listed once. Returns ``paths`` with the rules found.
    """
    mapping = dict((paths or PathMap()).mapping)
    index = _Index(progress)
    scopes = list(dict.fromkeys(e for p in start if (e := _existing(Path(p), bounds))))
    tried: set[tuple[str, str]] = set()
    for s in scopes:
        index.add(s)
    while True:
        found = PathMap(mapping)
        missing = [(loc, size) for loc, size in tracks if found.find(loc) is None]
        if not missing:
            break
        votes: dict[tuple[str, str], int] = {}
        unique: set[tuple[str, str]] = set()
        for loc, size in missing:
            name = normalise_path(loc).rsplit("/", 1)[-1].lower()
            hits = [f for f, s in index.files.get(name, []) if not size or s == size]
            rules = {r for f in hits if (r := _rule(loc, f))}
            if len(rules) != 1:
                continue  # not here, or more than one file it could be
            [r] = rules
            key = (r[0].lower(), r[1])
            votes[key] = votes.get(key, 0) + 1
            if size and len(hits) == 1:
                unique.add(key)
        best = [kv for kv in sorted(votes.items(), key=lambda kv: -kv[1]) if kv[0] not in tried]
        new = [k for k, n in best if n >= min_votes] or [k for k, _ in best[:1] if k in unique]
        if new:
            for app, ours in new:
                tried.add((app, ours))
                # One folder of ours stands for one of the program's (PathMap's
                # keys); a second, different one for the same folder is dropped.
                mapping.setdefault(ours, _case(missing, app))
            continue
        # Nothing more here: look one folder further out, within the bounds.
        wider = []
        for s in scopes:
            up = s.parent
            wider.append(up if up != s and _within(up, bounds) else s)
        wider = list(dict.fromkeys(wider))
        if wider == scopes:
            break
        scopes = wider
        for s in scopes:
            index.add(s)
    return PathMap(mapping)


def _existing(path: Path, bounds: list[Path]) -> Path | None:
    """``path``, or the nearest folder above it that exists, if within ``bounds``
    (a start outside them is searched as it is)."""
    while not path.is_dir():
        if path.parent == path or not _within(path.parent, bounds):
            return None
        path = path.parent
    return path


def _case(missing: list[tuple[str, int]], app_lower: str) -> str:
    """The program's folder as one of its locations spells it."""
    for loc, _ in missing:
        norm = normalise_path(loc)
        if norm.lower().startswith(app_lower.rstrip("/")):
            return norm[: len(app_lower.rstrip("/"))] or app_lower
    return app_lower
