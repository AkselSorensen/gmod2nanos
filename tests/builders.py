"""Fabrique des fichiers Source minimaux (mdl/vvd/vtx/vtf/vmt/smd) pour les tests."""
from __future__ import annotations

import io
import struct

from PIL import Image
from srctools.vtf import VTF, ImageFormats

# Un carré de 64x64 unités dans le plan XY (Z = 0), normale +Z, deux triangles.
QUAD_POS = [(0, 0, 0), (64, 0, 0), (64, 64, 0), (0, 64, 0)]
QUAD_UV = [(0, 1), (1, 1), (1, 0), (0, 0)]
# Ordre volontairement horaire vu du dessus (convention Source compilée) : le convertisseur doit le corriger.
QUAD_TRIS = [0, 2, 1, 0, 3, 2]


def build_mdl(texture="chair_wood", cdmaterials="models/test/", skin_families=1) -> bytes:
    hdr = bytearray(408)
    hdr[0:4] = b"IDST"
    struct.pack_into("<ii", hdr, 4, 48, 1234)
    hdr[12:12 + 14] = b"models/test.md"
    struct.pack_into("<ii", hdr, 156, 1, 0)  # 1 os
    body = bytearray()

    def here() -> int:
        return len(hdr) + len(body)

    # textures
    tex_off = here()
    body += bytearray(64)
    name_off = here()
    body += texture.encode() + b"\0"
    struct.pack_into("<i", body, tex_off - len(hdr), name_off - tex_off)
    # cdmaterials
    texdir_off = here()
    body += bytearray(4)
    s_off = here()
    body += cdmaterials.encode() + b"\0"
    struct.pack_into("<i", body, texdir_off - len(hdr), s_off)
    # skins
    skin_off = here()
    body += struct.pack(f"<{skin_families}h", *([0] * skin_families))
    while len(body) % 4:
        body += b"\0"
    # bodypart -> model -> mesh
    bp_off = here()
    body += bytearray(16)
    model_off = here()
    body += bytearray(148)
    mesh_off = here()
    body += bytearray(116)
    struct.pack_into("<iiii", body, bp_off - len(hdr), 0, 1, 1, model_off - bp_off)
    body[model_off - len(hdr) : model_off - len(hdr) + 4] = b"quad"
    struct.pack_into("<iiii", body, model_off - len(hdr) + 72, 1, mesh_off - model_off, 4, 0)
    struct.pack_into("<iiii", body, mesh_off - len(hdr), 0, 0, 4, 0)

    struct.pack_into("<ii", hdr, 204, 1, tex_off)
    struct.pack_into("<ii", hdr, 212, 1, texdir_off)
    struct.pack_into("<iii", hdr, 220, 1, skin_families, skin_off)
    struct.pack_into("<ii", hdr, 232, 1, bp_off)
    data = hdr + body
    struct.pack_into("<i", data, 76, len(data))
    return bytes(data)


def build_vvd() -> bytes:
    hdr = bytearray(64)
    hdr[0:4] = b"IDSV"
    struct.pack_into("<iii", hdr, 4, 4, 1234, 1)
    struct.pack_into("<8i", hdr, 16, 4, 0, 0, 0, 0, 0, 0, 0)
    struct.pack_into("<4i", hdr, 48, 0, 64, 64, 0)
    verts = b""
    for p, uv in zip(QUAD_POS, QUAD_UV):
        verts += struct.pack("<3f3BB3f3f2f", 1, 0, 0, 0, 0, 0, 1, *p, 0, 0, 1, *uv)
    return bytes(hdr) + verts


def build_vtx(csgo=False) -> bytes:
    sg_size, strip_size = (33, 35) if csgo else (25, 27)
    out = bytearray(36)
    struct.pack_into("<iiHHiii", out, 0, 7, 24, 53, 9, 3, 1234, 1)
    struct.pack_into("<ii", out, 28, 1, 36)
    bp = len(out); out += struct.pack("<ii", 1, 8)
    model = len(out); out += struct.pack("<ii", 1, 8)
    lod = len(out); out += struct.pack("<iif", 1, 12, 0.0)
    mesh = len(out); out += struct.pack("<iiB", 1, 9, 0)
    sg = len(out); out += bytearray(sg_size)
    verts_off = len(out)
    for i in range(4):
        out += struct.pack("<3BBH3b", 0, 0, 0, 1, i, 0, -1, -1)
    idx_off = len(out)
    out += struct.pack(f"<{len(QUAD_TRIS)}H", *QUAD_TRIS)
    strip_off = len(out)
    out += bytearray(strip_size)
    struct.pack_into("<iiiihB", out, strip_off, len(QUAD_TRIS), 0, 4, 0, 1, 1)
    struct.pack_into("<6iB", out, sg, 4, verts_off - sg, len(QUAD_TRIS), idx_off - sg, 1, strip_off - sg, 0)
    assert bp and model and lod and mesh
    return bytes(out)


def build_vtf(color=(200, 50, 50, 255), fmt=ImageFormats.DXT1) -> bytes:
    img = Image.new("RGBA", (16, 16), color)
    vtf = VTF(16, 16, fmt=fmt)
    vtf.get().copy_from(img.tobytes(), ImageFormats.RGBA8888)
    buf = io.BytesIO()
    vtf.save(buf)
    return buf.getvalue()


def build_vmt(basetexture: str, extra: str = "") -> bytes:
    return f'"VertexLitGeneric"\n{{\n\t"$basetexture" "{basetexture}"\n{extra}}}\n'.encode()


def build_smd(material="chair_wood.bmp") -> bytes:
    lines = ["version 1", "nodes", '0 "root" -1', "end", "skeleton", "time 0", "0 0 0 0 0 0 0", "end", "triangles"]
    uv_gl = [(u, 1 - v) for u, v in QUAD_UV]  # SMD : origine en bas à gauche
    for tri in ((0, 1, 2), (0, 2, 3)):  # sens antihoraire vu du dessus
        lines.append(material)
        for i in tri:
            p, uv = QUAD_POS[i], uv_gl[i]
            lines.append(f"0 {p[0]} {p[1]} {p[2]} 0 0 1 {uv[0]} {uv[1]} 1 0 1")
    lines.append("end")
    return ("\n".join(lines) + "\n").encode()


def addon_files() -> dict[str, bytes]:
    """Un petit addon GMod complet : un modèle compilé, son matériau, un son et du Lua."""
    import wave

    snd = io.BytesIO()
    with wave.open(snd, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\0\0" * 2205)
    return {
        "addon.json": b"{}",
        "models/test/chair.mdl": build_mdl(),
        "models/test/chair.vvd": build_vvd(),
        "models/test/chair.dx90.vtx": build_vtx(),
        "materials/models/test/chair_wood.vmt": build_vmt("models/test/chair_wood_diffuse"),
        "materials/models/test/chair_wood_diffuse.vtf": build_vtf(),
        "sound/test/beep.wav": snd.getvalue(),
        "lua/autorun/test.lua": b"print('hi')",
    }
