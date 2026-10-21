"""Lecture des archives .gma (Garry's Mod Addon) et des .gma compressés en LZMA."""
from __future__ import annotations

import io
import lzma
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .util import safe_join

GMA_MAGIC = b"GMAD"


@dataclass
class GmaEntry:
    name: str
    size: int
    crc: int
    offset: int = 0


@dataclass
class GmaInfo:
    version: int
    steamid: int
    timestamp: int
    name: str
    description: str
    author: str
    addon_version: int
    required_content: list[str] = field(default_factory=list)
    entries: list[GmaEntry] = field(default_factory=list)


def _cstring(f: io.BufferedIOBase) -> str:
    buf = bytearray()
    while True:
        c = f.read(1)
        if not c:
            raise ValueError("fin de fichier inattendue dans une chaîne GMA")
        if c == b"\0":
            return buf.decode("utf-8", errors="replace")
        buf += c


def read_header(f) -> GmaInfo:
    if f.read(4) != GMA_MAGIC:
        raise ValueError("ce fichier n'est pas un .gma valide")
    version = f.read(1)[0]
    steamid, timestamp = struct.unpack("<QQ", f.read(16))
    required: list[str] = []
    if version > 1:
        while (s := _cstring(f)):
            required.append(s)
    name = _cstring(f)
    description = _cstring(f)
    author = _cstring(f)
    (addon_version,) = struct.unpack("<i", f.read(4))
    info = GmaInfo(version, steamid, timestamp, name, description, author, addon_version, required)
    while True:
        (num,) = struct.unpack("<I", f.read(4))
        if num == 0:
            break
        fname = _cstring(f)
        size, crc = struct.unpack("<qI", f.read(12))
        info.entries.append(GmaEntry(fname, size, crc))
    offset = f.tell()
    for e in info.entries:
        e.offset = offset
        offset += e.size
    return info


def decompress_if_needed(path: Path) -> Path:
    """Les fichiers téléchargés depuis l'ancien CDN du Workshop sont compressés en LZMA ("alone")."""
    with open(path, "rb") as f:
        head = f.read(4)
    if head == GMA_MAGIC:
        return path
    out = path.with_suffix(".gma")
    if out == path:
        out = path.with_name(path.stem + "_dec.gma")
    dec = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
    with open(path, "rb") as src, open(out, "wb") as dst:
        while chunk := src.read(1 << 20):
            dst.write(dec.decompress(chunk))
    with open(out, "rb") as f:
        if f.read(4) != GMA_MAGIC:
            raise ValueError("le fichier téléchargé n'est ni un .gma ni un .gma compressé")
    return out


def extract(path: Path, dest: Path, max_total: int | None = None) -> GmaInfo:
    """Extrait le .gma dans dest (chemins assainis contre le path traversal)."""
    path = decompress_if_needed(Path(path))
    dest.mkdir(parents=True, exist_ok=True)
    with open(path, "rb") as f:
        info = read_header(f)
        total = sum(e.size for e in info.entries)
        if max_total is not None and total > max_total:
            raise ValueError(f"addon trop volumineux ({total} octets)")
        for e in info.entries:
            target = safe_join(dest, e.name)
            if target is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            f.seek(e.offset)
            remaining = e.size
            with open(target, "wb") as out:
                while remaining > 0:
                    chunk = f.read(min(remaining, 1 << 20))
                    if not chunk:
                        raise ValueError(f"données tronquées pour {e.name}")
                    out.write(chunk)
                    remaining -= len(chunk)
    return info


def write(path: Path, files: dict[str, bytes], name: str = "addon", author: str = "", description: str = "") -> None:
    """Écrit un .gma (utilisé par les tests)."""
    import zlib

    buf = io.BytesIO()
    buf.write(GMA_MAGIC + bytes([3]) + struct.pack("<QQ", 0, 0) + b"\0")
    for s in (name, description, author):
        buf.write(s.encode() + b"\0")
    buf.write(struct.pack("<i", 1))
    for i, (fname, data) in enumerate(files.items(), 1):
        buf.write(struct.pack("<I", i) + fname.encode() + b"\0" + struct.pack("<qI", len(data), zlib.crc32(data)))
    buf.write(struct.pack("<I", 0))
    for data in files.values():
        buf.write(data)
    buf.write(struct.pack("<I", zlib.crc32(buf.getvalue())))
    Path(path).write_bytes(buf.getvalue())
