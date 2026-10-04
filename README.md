# cratelib

Read and write DJ libraries and USB sticks for **Rekordbox**, **Serato** and **Mixxx**: tracks,
playlists, crates, hot cues, memory cues, loops and beat grids, in one format-neutral model.

Split out of [cratemover](https://github.com/lewinfox/cratemover), which is now the app on top
(command line, web UI, sync).

## Install

```sh
uv add "cratelib[all]"          # everything
uv add "cratelib[rekordbox]"    # + Rekordbox 6/7's master.db and OneLibrary sticks
uv add "cratelib[serato]"       # + Serato
uv add "cratelib[ffmpeg]"       # + an ffmpeg binary, if there isn't one on PATH
```

The core (Mixxx, Rekordbox XML, Rekordbox `export.pdb` sticks) needs only mutagen.

## Use

There are two ways in.

**Whole libraries**: `read()` a library into the model, `write()` the model as another.

```python
from pathlib import Path
import cratelib

library = cratelib.read(cratelib.Serato(Path.home() / "Music/_Serato_"))
cratelib.write(library, cratelib.RekordboxUsb(Path("/media/STICK")))
cratelib.write(library.select(["Sets / Friday"]), cratelib.RekordboxXml(Path("friday.xml")))
```

Writing to a library that already exists updates it in place and keeps the old files next to
it as `*.cratelib-<time>`.

**Edit in place**: `open()` a library, change specific tracks and playlists, `save()`.

```python
paths = cratelib.PathMap({"/home/me/Music": "C:/users/dj/Music"})
with cratelib.open(cratelib.RekordboxDb(rekordbox_dir), paths) as rb:
    track = rb.track_at(file) or rb.add_track(file)
    rb.set_playlist(["SoundCloud", "ukg"], [track])  # creates folders; replaces the contents
    rb.save()  # refuses while rekordbox is running; backs up first
```

Nothing is written until `save()`, which refuses while the library's program is running.
Setting a playlist to the contents it already has changes nothing, so a sync job can run it
every time. Rekordbox's own library, Serato and Mixxx can be opened. Serato playlists are
crates (nested with `%%`); Mixxx has no folders, so `["SoundCloud", "ukg"]` is the playlist
`SoundCloud / ukg`.

**Paths.** A `PathMap` says how a DJ program sees this machine's files. Keys are local
folders, values are the same folders as the program stores them. `read()` uses it to set each
track's `file`; `write()` and `open()` use it to store new paths.

**Finding libraries**: `cratelib.detect(path)` for a file, folder or drive;
`cratelib.installed()` for each program's usual place on this computer.

## Who does what

cratelib hands formats to existing libraries where one covers them:

| Format | Read | Write | By |
|---|---|---|---|
| Rekordbox `master.db` | ✓ | edit with `open()` | [pyrekordbox](https://github.com/dylanljones/pyrekordbox) |
| Rekordbox XML | ✓ | ✓ | own: pyrekordbox's `rbxml` mangles macOS/Linux paths and drops hot cue colours |
| Rekordbox USB (`export.pdb`, OneLibrary, analysis files) | ✓ | ✓ | own: no Python library writes these |
| Serato `database V2` and crates | ✓ | ✓ | [serato-tools](https://github.com/bvandrc/serato-tools) |
| Serato cues and grids in audio files | ✓ | ✓ | own: serato-tools only covers MP3/AIFF |
| Mixxx `mixxxdb.sqlite` | ✓ | ✓ | own: no library exists |

Where we keep our own code because a library has a gap, the plan is to fix the gap upstream
and then switch.

## Development

```sh
uv sync
uv run pytest
uv run ruff check && uv run ruff format
```

Tests use real files written by the DJ programs where available; see
[`tests/fixtures/README.md`](tests/fixtures/README.md). Round-trip tests generate their audio
with ffmpeg and are skipped without it.

## Licence

MIT (see `LICENSE`), except `src/cratelib/mixxx_schema.xml`, which is Mixxx's database schema
and stays GPL-2.0-or-later (see `LICENSES/`). Test fixtures carry their own licences, listed in
`tests/fixtures/README.md`.
