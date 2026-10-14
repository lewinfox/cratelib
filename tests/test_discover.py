"""Finding a library's files when its program stores other paths."""

from __future__ import annotations

from pathlib import Path

from cratelib.paths import PathMap, discover


def _touch(path: Path, size: int = 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def test_one_track_found_maps_the_rest(tmp_path: Path) -> None:
    home = tmp_path / "home"
    for name in ("a", "b", "c"):
        _touch(home / "Music/foo/music" / f"{name}.mp3")
    tracks = [(f"C:\\foo\\bar\\music\\{n}.mp3", 10) for n in ("a", "b", "c")]
    # Started in the wrong folder: the search widens to the home folder, not past it.
    _touch(tmp_path / "elsewhere/a.mp3")
    paths = discover(tracks, start=[home / "Music/foo/music/x"], bounds=[home])
    assert paths.mapping == {str(home / "Music/foo"): "C:/foo/bar"}
    assert paths.find("C:\\foo\\bar\\music\\c.mp3") == home / "Music/foo/music/c.mp3"


def test_each_music_folder_gets_its_own_rule(tmp_path: Path) -> None:
    home = tmp_path / "home"
    tracks = []
    for folder, app in (("Music", "C:/users/dj/Music"), ("Downloads/tunes", "D:/new")):
        for n in ("a", "b"):
            name = f"{folder.replace('/', '-')}-{n}.mp3"
            _touch(home / folder / name)
            tracks.append((f"{app}/{name}", 10))
    tracks.append(("C:/users/dj/Music/gone.mp3", 10))  # nowhere: doesn't stop the rest
    paths = discover(tracks, start=[home / "Music"], bounds=[home])
    # Both end in "Music/<file>": the rule is everything before that.
    assert paths.mapping == {str(home): "C:/users/dj", str(home / "Downloads/tunes"): "D:/new"}


def test_sizes_tell_same_named_files_apart(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _touch(home / "old/x/a.mp3", size=5)
    _touch(home / "new/x/a.mp3", size=10)
    _touch(home / "new/x/b.mp3", size=10)
    tracks = [("C:/m/x/a.mp3", 10), ("C:/m/x/b.mp3", 10)]
    assert discover(tracks, start=[home], bounds=[home]).mapping == {str(home / "new"): "C:/m"}


def test_one_track_alone_needs_a_unique_name_and_size(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _touch(home / "Music/only.mp3")
    assert discover([("C:/M/only.mp3", 10)], start=[home], bounds=[home]).mapping == {
        str(home / "Music"): "C:/M"
    }
    # Without a size it could be any "only.mp3": no rule from one track.
    assert discover([("C:/M/only.mp3", 0)], start=[home], bounds=[home]).mapping == {}


def test_rules_already_known_are_kept_and_not_searched_for(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _touch(home / "Music/a.mp3")
    known = PathMap({str(home / "Music"): "C:/M"})
    seen: list[str] = []
    paths = discover(
        [("C:/M/a.mp3", 10)], start=[home], bounds=[home], paths=known, progress=seen.append
    )
    assert paths == known
    assert seen == [f"Looking for music in {home}"]
