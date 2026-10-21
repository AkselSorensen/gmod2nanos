"""Conversion des sons en .ogg (format lisible directement par nanos world via assets://)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

SOUND_EXTS = (".wav", ".mp3", ".ogg")


def to_ogg(src: Path, dst: Path) -> None:
    if src.suffix.lower() == ".ogg":
        shutil.copyfile(src, dst)
        return
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg n'est pas installé")
    proc = subprocess.run(
        [ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-i", str(src), "-vn", "-c:a", "libvorbis", "-q:a", "5", str(dst)],
        capture_output=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode(errors="replace").strip()[:300] or "échec ffmpeg")
