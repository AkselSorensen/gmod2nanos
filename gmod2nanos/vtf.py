"""Lecture des textures .vtf.

srctools gère les formats Valve classiques. Ce lecteur de secours couvre le reste : BC4/BC5/BC6H/BC7
(branches Alien Swarm, Titanfall, Strata, outils récents) et les formats inconnus, devinés d'après la taille
des données.
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
from PIL import Image

HIGH_RES_TAG = b"\x30\x00\x00"

# id -> (nom, octets par bloc 4x4 ou par pixel, compressé ?)
_BLOCK = {
    13: ("DXT1", 8), 20: ("DXT1A", 8), 14: ("DXT3", 16), 15: ("DXT5", 16),
    34: ("BC5", 16), 37: ("BC5", 16), 35: ("BC4", 8), 38: ("BC4", 8),
    66: ("BC6H", 16), 67: ("BC7", 16),
    70: ("BC7", 16), 71: ("BC6H", 16), 72: ("BC6H", 16), 73: ("BC5", 16), 74: ("BC4", 8),
}
_RAW = {
    0: ("RGBA", 4), 1: ("ABGR", 4), 2: ("RGB", 3), 3: ("BGR", 3), 5: ("L", 1), 6: ("LA", 2), 8: ("A", 1),
    9: ("RGB", 3), 10: ("BGR", 3), 11: ("ARGB", 4), 12: ("BGRA", 4), 16: ("BGRX", 4), 22: ("UV", 2),
    23: ("RGBA", 4), 26: ("RGBA", 4), 32: ("RGBX", 4), 69: ("L", 1),
}
_BCN = {"DXT1": ("RGBA", 1), "DXT1A": ("RGBA", 1), "DXT3": ("RGBA", 2), "DXT5": ("RGBA", 3), "BC4": ("L", 4), "BC5": ("RGB", 5),
        "BC6H": ("RGB", 6), "BC7": ("RGBA", 7)}


def _frame_size(fmt: tuple[str, int], w: int, h: int, block: bool) -> int:
    if block:
        return max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * fmt[1]
    return w * h * fmt[1]


def _decode(data: bytes, w: int, h: int, fmt: tuple[str, int], block: bool) -> Image.Image:
    name, _ = fmt
    if block:
        mode, n = _BCN[name]
        pw, ph = max(4, (w + 3) // 4 * 4), max(4, (h + 3) // 4 * 4)
        args = (n, "BC6HU") if name == "BC6H" else (n,)
        img = Image.frombytes(mode, (pw, ph), data, "bcn", *args)
        if name == "DXT1":  # sans alpha 1 bit, Source ignore la transparence
            img = img.convert("RGB")
        if name == "BC5":  # normal map 2 canaux : on reconstruit Z
            a = np.asarray(img, dtype=np.float32) / 255.0
            x, y = a[..., 0] * 2 - 1, a[..., 1] * 2 - 1
            z = np.sqrt(np.clip(1 - x * x - y * y, 0, 1))
            a[..., 2] = (z + 1) / 2
            img = Image.fromarray((a * 255).astype(np.uint8), "RGB")
        return img.crop((0, 0, w, h)) if (pw, ph) != (w, h) else img
    arr = np.frombuffer(data, np.uint8, count=w * h * fmt[1]).reshape(h, w, fmt[1])
    order = {c: i for i, c in enumerate(name)}
    if name in ("L", "A"):
        return Image.fromarray(arr[..., 0], "L")
    if name == "LA":
        return Image.fromarray(arr, "LA")
    if name == "UV":
        rgb = np.dstack([arr[..., 0], arr[..., 1], np.full((h, w), 255, np.uint8)])
        return Image.fromarray(rgb, "RGB")
    rgb = np.dstack([arr[..., order[c]] for c in "RGB"])
    if "A" in order:
        return Image.fromarray(np.dstack([rgb, arr[..., order["A"]]]), "RGBA")
    return Image.fromarray(rgb, "RGB")


def read_vtf_fallback(path: Path) -> Image.Image:
    data = Path(path).read_bytes()
    if data[:4] != b"VTF\0":
        raise ValueError("signature .vtf invalide")
    major, minor, header_size = struct.unpack_from("<3I", data, 4)
    w, h, _flags, frames, _first = struct.unpack_from("<HHIHH", data, 16)
    fmt_id, mips, low_fmt, low_w, low_h = struct.unpack_from("<iBiBB", data, 52)
    depth = struct.unpack_from("<H", data, 63)[0] if minor >= 2 else 1
    depth = max(depth, 1)
    frames = max(frames, 1)

    high_off = None
    if minor >= 3:
        (num_res,) = struct.unpack_from("<I", data, 68)
        for i in range(num_res):
            tag, _fl, off = struct.unpack_from("<3sBI", data, 80 + i * 8)
            if tag == HIGH_RES_TAG:
                high_off = off
    if high_off is None:
        low_size = 0
        if low_fmt >= 0 and low_w and low_h:
            low_size = max(1, (low_w + 3) // 4) * max(1, (low_h + 3) // 4) * 8  # miniature DXT1
        high_off = header_size + low_size
    avail = len(data) - high_off
    if avail <= 0:
        raise ValueError("pas de données d'image")

    faces = 6 if _flags & 0x4000 else 1
    count = frames * faces * depth
    if fmt_id in _BLOCK:
        fmt, block = _BLOCK[fmt_id], True
    elif fmt_id in _RAW:
        fmt, block = _RAW[fmt_id], False
    else:
        # Format inconnu : on déduit le nombre d'octets par pixel du volume total des mipmaps
        texels = sum(max(1, w >> m) * max(1, h >> m) for m in range(max(mips, 1))) * count
        bpp = avail / texels
        if bpp < 0.75:
            fmt, block = ("DXT1", 8), True
        elif bpp < 1.5:
            fmt, block = ("BC7", 16), True
        elif bpp < 3.5:
            fmt, block = ("RGB", 3), False
        else:
            fmt, block = ("RGBA", 4), False

    size0 = _frame_size(fmt, w, h, block)
    # les mipmaps sont stockés du plus petit au plus grand : on saute les petits
    smaller = sum(_frame_size(fmt, max(1, w >> m), max(1, h >> m), block) for m in range(1, max(mips, 1)))
    start = high_off + smaller * count
    chunk = data[start : start + size0]
    if len(chunk) < size0:
        raise ValueError(f"format {fmt_id} : données tronquées")
    return _decode(chunk, w, h, fmt, block)


def read_vtf(path: Path) -> Image.Image:
    from srctools.vtf import VTF

    try:
        with open(path, "rb") as f:
            vtf = VTF.read(f)
            vtf.load()
        return vtf.get().to_PIL()
    except Exception as first:
        try:
            return read_vtf_fallback(path)
        except Exception as second:
            raise ValueError(f"{type(first).__name__} {first} / secours : {second}") from second
