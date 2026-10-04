# CLAUDE.md

cratelib reads and writes DJ libraries and USB sticks for Rekordbox, Serato and Mixxx.

## Commands

```sh
uv sync                      # dev env, with every extra
uv run pytest                # ~140 tests, ~30 s; round trips need ffmpeg on PATH
uv run ruff check && uv run ruff format
```

## Rules

- **Never touch a real library from tests or scripts.** Tests copy fixtures into `tmp_path`. Writing to the user's own `master.db`, `_Serato_` or `mixxxdb.sqlite` needs their go-ahead first (see their global CLAUDE.md).
- **Hand formats to other people's libraries where one covers them, and fix gaps upstream instead of working around them for long.** pyrekordbox does `master.db`; serato-tools does `database V2` and crates. We keep our own code only where a library has a gap. The README's "Who does what" table lists each gap; update it when one closes.
- **Every write keeps a backup**, named `<file>.cratelib-<YYYYmmdd-HHMMSS>` next to the original (a folder for `master.db` and Serato, since several files go together).

## The API (all in `cratelib/__init__.py`)

- **Sources** (`sources.py`): `Mixxx`, `RekordboxDb`, `RekordboxXml`, `RekordboxUsb`, `Serato`. Frozen dataclasses saying where a library is; `detect()` and `installed()` find them.
- **`read(source, paths)` → `Library`** and **`write(library, target, options, paths)`** (`api.py`): whole libraries through the format-neutral model (`model.py`). `write()` updates an existing library in place, else creates one. Per-format options: `MixxxOptions`, `SeratoOptions`, `RekordboxXmlOptions`, `RekordboxUsbOptions`.
- **`open(source, paths)` → `Editor`** (`edit.py`): targeted edits for `RekordboxDb`, `Serato` and `Mixxx`. `track_at`, `add_track`, `set_playlist` (returns False and changes nothing if the contents already match), `delete_playlist`, `save` (backs up; raises `*Running` if the program is open). Nothing is written before `save()`; leaving the `with` block discards changes. One shared test suite (`tests/test_editors.py`) runs against all three; add new editors to it.
- **`PathMap({local: as_the_program_sees_it})`** (`paths.py`): keys are local folders, values are the program's view. For example, `{"/home/lewin/Music": "C:/users/dj/Music"}` for rekordbox under Wine. `read()` sets `Track.file` (the local file) from `Track.location` (the program's path); `write()` and editors store `paths.to_app(file)`.

## Layout

| Path                                                                    | What                                                                                          |
| ----------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `model.py`                                                              | `Library`, `Track`, `Playlist`, `Cue`, `TempoMarker`. Times are ms on Rekordbox's timeline.   |
| `api.py`, `edit.py`, `sources.py`, `paths.py`                           | The public API above                                                                          |
| `rekordbox/db.py`, `rekordbox/edit.py`                                  | `master.db` read / edit, via pyrekordbox                                                      |
| `rekordbox/xml.py`                                                      | Rekordbox XML (own code)                                                                      |
| `rekordbox/usb.py`, `pdb.py`, `onelibrary.py`, `anlz.py`, `waveform.py` | USB sticks: `export.pdb`, OneLibrary `exportLibrary.db`, analysis files, waveforms (own code) |
| `serato/binfile.py`                                                     | `database V2` and crates, via serato-tools                                                    |
| `serato/library.py`, `serato/edit.py`                                   | Serato read/write and edit                                                                    |
| `serato/markers.py`, `serato/tags.py`                                   | Serato cues and grids inside audio files (own code)                                           |
| `mixxx.py`, `mixxx_edit.py`                                             | Mixxx read/write and edit                                                                     |
| `offsets.py`                                                            | Decoder offsets: Mixxx and some MP3s put audio a few ms off Rekordbox's timeline              |
| `layout.py`, `devices.py`, `detect.py`                                  | Where files go on sticks; finding drives and libraries                                        |
| `processes.py`                                                          | Is Mixxx / Serato running (psutil)                                                            |
| `tests/fixtures/`                                                       | Real files from the DJ programs; sources and licences in its README                           |

## Gotchas

- **`master.db` uses a write-ahead log.** Recent changes sit in `master.db-wal` until merged. Anything that copies `master.db` must copy `-wal` too. The editor disposes pyrekordbox's engine on close so the log is merged.
- **pyrekordbox's `add_content` stats the path it's given**, so the editor adds the local file, then sets `FolderPath` to the Wine path.
- **pyrekordbox logs "OS linux not supported!"** on import. Harmless.
- **pyrekordbox's running check** looks for a process named exactly `rekordbox` (with `.exe` stripped), which catches rekordbox under Wine/Docker too.
- **Serato `volume_root`** is what Serato's stored paths are relative to: `/` on macOS/Linux, `C:/` on Windows _and under Wine_, or a stick's mount point. Getting it wrong drops the drive letter on the round trip.
- **serato-tools workarounds** in `serato/binfile.py`: `DatabaseV2` refuses a file that doesn't exist (we subclass to skip the check), and `DatabaseV2.save()` writes stale bytes unless `_dump()` runs first. Also, its `get_full_path` is broken on macOS/Linux (hard-codes `"\\"` instead of `os.sep`), so don't use it; turn stored paths into local ones with `from_serato_path` + `PathMap`.
- **Mixxx has no playlist folders**: nested paths become names like `SoundCloud / ukg`, in both `write()` and the editor.

## Licence

MIT, except `src/cratelib/mixxx_schema.xml` (Mixxx, GPL-2.0-or-later, see `LICENSES/`). Don't copy code from GPL projects (Mixxx); facts like palettes and offsets are fine.
