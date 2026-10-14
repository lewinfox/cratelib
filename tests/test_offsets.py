"""Decoder offsets measured against FFmpeg, a gapless MP3 decoder."""

from __future__ import annotations

import array
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import needs_ffmpeg
from fakelib import RATE, make_audio

from cratelib.offsets import (
    MP3_DECODER_DELAY,
    Mp3HeaderCase,
    mp3_gapless_offset_ms,
    mp3_header_case,
)

PCM = ["-ac", "1", "-f", "s16le", "-"]


def _first_click(path: Path, *flags: str) -> int:
    """The sample where the first click starts, decoding with FFmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg
    pcm = subprocess.run(
        [ffmpeg, "-nostdin", "-loglevel", "error", *flags, "-i", str(path), *PCM],
        check=True,
        capture_output=True,
    ).stdout
    samples = array.array("h", pcm)
    return next(i for i, v in enumerate(samples) if abs(v) > 2000)


@needs_ffmpeg
def test_mp3_gapless_offset_is_what_ffmpeg_trims(tmp_path: Path) -> None:
    # FFmpeg's MP3s have an Info frame and a Lavc tag with the encoder delay. FFmpeg
    # drops the Info frame either way; `skip_manual` stops it trimming the delay, which
    # leaves Rekordbox's timeline (it doesn't count the Info frame of a case B file).
    mp3 = make_audio(tmp_path / "a.mp3", first_beat=0.25)
    assert mp3_header_case(mp3) is Mp3HeaderCase.XING
    gapless = _first_click(mp3)
    reference = _first_click(mp3, "-flags2", "+skip_manual")
    trimmed = reference - gapless
    assert trimmed > MP3_DECODER_DELAY  # the encoder's delay, and the decoder's
    assert mp3_gapless_offset_ms(mp3) == pytest.approx(trimmed * 1000 / RATE, abs=0.05)
    # And the gapless decode has the click where it was put.
    assert gapless == pytest.approx(0.25 * RATE, abs=40)


def test_mp3_gapless_offset_reads_the_tag(tmp_path: Path) -> None:
    """A hand-made MPEG-1 Layer III frame with an Info header and a LAME-style tag:
    576 samples of encoder delay."""
    header = bytes([0xFF, 0xFB, 0x90, 0x64])  # MPEG-1 L3, 128 kbps, 44.1 kHz, stereo
    frame = bytearray(417)
    frame[:4] = header
    info = 4 + 32  # after the side information (stereo, MPEG-1)
    frame[info : info + 8] = b"Info" + (0).to_bytes(4, "big")  # no optional fields
    tag = info + 8
    frame[tag : tag + 9] = b"Lavc61.19"
    frame[tag + 21 : tag + 24] = ((576 << 12) | 1000).to_bytes(3, "big")
    mp3 = tmp_path / "t.mp3"
    mp3.write_bytes(bytes(frame) + bytes(frame))
    assert mp3_gapless_offset_ms(mp3) == pytest.approx((576 + 529) * 1000 / 44100)
    # Another encoder's tag: nothing is trimmed, and Rekordbox doesn't count the frame.
    frame[tag : tag + 9] = b"Xing-ish!"
    mp3.write_bytes(bytes(frame) + bytes(frame))
    assert mp3_gapless_offset_ms(mp3) == 0
