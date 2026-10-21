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


def build_mdl(texture="chair_wood", cdmaterials="models/test/", skin_families=1, bones=None) -> bytes:
    """bones : liste optionnelle (nom, parent, position monde) ; par défaut un seul os sans données."""
    hdr = bytearray(408)
    hdr[0:4] = b"IDST"
    struct.pack_into("<ii", hdr, 4, 48, 1234)
    hdr[12:12 + 14] = b"models/test.md"
    struct.pack_into("<ii", hdr, 156, 1, 0)  # 1 os
    body = bytearray()

    def here() -> int:
        return len(hdr) + len(body)

    if bones:
        bone_off = here()
        body += bytearray(216 * len(bones))
        for i, (name, parent, pos) in enumerate(bones):
            base = bone_off + i * 216
            name_off = here()
            body += name.encode() + b"\0"
            struct.pack_into("<ii", body, base - len(hdr), name_off - base, parent)
            # poseToBone = inverse de la pose monde (ici une simple translation)
            struct.pack_into("<12f", body, base - len(hdr) + 96, 1, 0, 0, -pos[0], 0, 1, 0, -pos[1], 0, 0, 1, -pos[2])
        while len(body) % 4:
            body += b"\0"
        struct.pack_into("<ii", hdr, 156, len(bones), bone_off)

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


def build_vvd(bone_of_vertex=None) -> bytes:
    hdr = bytearray(64)
    hdr[0:4] = b"IDSV"
    struct.pack_into("<iii", hdr, 4, 4, 1234, 1)
    struct.pack_into("<8i", hdr, 16, 4, 0, 0, 0, 0, 0, 0, 0)
    struct.pack_into("<4i", hdr, 48, 0, 64, 64, 0)
    verts = b""
    for k, (p, uv) in enumerate(zip(QUAD_POS, QUAD_UV)):
        b = bone_of_vertex[k] if bone_of_vertex else 0
        verts += struct.pack("<3f3BB3f3f2f", 1, 0, 0, b, 0, 0, 1, *p, 0, 0, 1, *uv)
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


# --- Personnage ValveBiped (T-pose, regarde vers +X) et Mannequin UE4 (A-pose, regarde vers +Y) ---

def valve_biped_joints() -> dict[str, tuple[str | None, tuple[float, float, float]]]:
    """os -> (parent, position monde en unités Source)."""
    j: dict[str, tuple[str | None, tuple]] = {}
    v = "ValveBiped.Bip01_"
    j[v + "Pelvis"] = (None, (0, 0, 40))
    j[v + "Spine"] = (v + "Pelvis", (0, 0, 44))
    j[v + "Spine1"] = (v + "Spine", (0, 0, 48))
    j[v + "Spine2"] = (v + "Spine1", (0, 0, 52))
    j[v + "Spine4"] = (v + "Spine2", (0, 0, 56))
    j[v + "Neck1"] = (v + "Spine4", (0, 0, 62))
    j[v + "Head1"] = (v + "Neck1", (0, 0, 65))
    j["hair_jiggle"] = (v + "Head1", (-2, 0, 70))
    for S, sgn in (("L", 1), ("R", -1)):
        p = f"{v}{S}_"
        j[p + "Clavicle"] = (v + "Spine4", (0, 2 * sgn, 58))
        j[p + "UpperArm"] = (p + "Clavicle", (0, 7 * sgn, 58))
        j[p + "Forearm"] = (p + "UpperArm", (0, 18 * sgn, 58))
        j[p + "Hand"] = (p + "Forearm", (0, 28 * sgn, 58))
        for i, x in ((0, 1.5), (1, 1.0), (2, 0.0), (3, -0.5), (4, -1.0)):
            base_y = 29 if i == 0 else 31
            j[f"{p}Finger{i}"] = (p + "Hand", (x, base_y * sgn, 58))
            j[f"{p}Finger{i}1"] = (f"{p}Finger{i}", (x + (1 if i == 0 else 0), (base_y + 1.5) * sgn, 58))
            j[f"{p}Finger{i}2"] = (f"{p}Finger{i}1", (x + (2 if i == 0 else 0), (base_y + 2.5) * sgn, 58))
        j[p + "Thigh"] = (v + "Pelvis", (0, 4 * sgn, 40))
        j[p + "Calf"] = (p + "Thigh", (0, 4 * sgn, 21))
        j[p + "Foot"] = (p + "Calf", (0, 4 * sgn, 3))
        j[p + "Toe0"] = (p + "Foot", (4, 4 * sgn, 0.5))
    return j


def build_character_smd() -> bytes:
    joints = valve_biped_joints()
    names = list(joints)
    idx = {n: i for i, n in enumerate(names)}
    lines = ["version 1", "nodes"]
    for n in names:
        parent = joints[n][0]
        lines.append(f'{idx[n]} "{n}" {idx[parent] if parent else -1}')
    lines += ["end", "skeleton", "time 0"]
    for n in names:
        parent, pos = joints[n]
        ppos = joints[parent][1] if parent else (0, 0, 0)
        lines.append(f"{idx[n]} " + " ".join(f"{a - b:.4f}" for a, b in zip(pos, ppos)) + " 0 0 0")
    lines += ["end", "triangles"]
    children = {}
    for n, (parent, _) in joints.items():
        if parent:
            children.setdefault(parent, []).append(n)
    for n in names:
        if n not in children:
            continue
        a = joints[n][1]
        b = joints[children[n][0]][1]
        mid = [(x + y) / 2 for x, y in zip(a, b)]
        tri = [(mid[0] + 0.3, mid[1], mid[2]), (mid[0], mid[1] + 0.3, mid[2]), (mid[0], mid[1], mid[2] + 0.3)]
        lines.append("skin")
        for p in tri:
            lines.append(f"{idx[n]} {p[0]:.4f} {p[1]:.4f} {p[2]:.4f} 0 0 1 0.5 0.5 1 {idx[n]} 1.0")
    lines.append("end")
    return ("\n".join(lines) + "\n").encode()


def mannequin_reference() -> dict:
    """Faux SKEL_Mannequin : A-pose, regarde vers +Y (gauche = +X), cm, rotations arbitraires par os."""
    import math

    from gmod2nanos.retarget import MANNEQUIN_PARENTS

    d = math.sqrt(0.5)
    arm = lambda s: (s * d, 0.0, -d)  # noqa: E731
    pos = {"root": (0, 0, 0), "pelvis": (0, 0, 95), "spine_01": (0, 0, 105), "spine_02": (0, 0, 118),
           "spine_03": (0, 0, 132), "neck_01": (0, 0, 150), "head": (0, 0, 160),
           "ik_foot_root": (0, 0, 0), "ik_hand_root": (0, 0, 0), "ik_hand_gun": (50, 0, 100)}
    for s, sg in (("l", 1), ("r", -1)):
        ax = arm(sg)
        up = (18 * sg, 0, 142)
        lo = tuple(u + 30 * a for u, a in zip(up, ax))
        ha = tuple(u + 26 * a for u, a in zip(lo, ax))
        pos.update({f"clavicle_{s}": (4 * sg, 0, 145), f"upperarm_{s}": up, f"lowerarm_{s}": lo, f"hand_{s}": ha,
                    f"upperarm_twist_01_{s}": tuple((u + v) / 2 for u, v in zip(up, lo)),
                    f"lowerarm_twist_01_{s}": tuple((u + v) / 2 for u, v in zip(lo, ha)),
                    f"thigh_{s}": (10 * sg, 0, 92), f"calf_{s}": (10 * sg, 0, 50), f"foot_{s}": (10 * sg, 0, 8),
                    f"ball_{s}": (10 * sg, 12, 2), f"thigh_twist_01_{s}": (10 * sg, 0, 71),
                    f"calf_twist_01_{s}": (10 * sg, 0, 29), f"ik_foot_{s}": (10 * sg, 0, 8), f"ik_hand_{s}": ha})
        for f, off in (("thumb", 4), ("index", 3), ("middle", 0), ("ring", -2), ("pinky", -4)):
            b = tuple(h + (3 if f == "thumb" else 10) * a for h, a in zip(ha, ax))
            b = (b[0], b[1] + off, b[2])
            for k in range(3):
                pos[f"{f}_0{k + 1}_{s}"] = tuple(c + 3 * k * a for c, a in zip(b, ax))
    ref = {}
    for i, (n, t) in enumerate(pos.items()):
        ang = 0.37 * (i + 1)
        axis = (1.0, 2.0, 3.0)
        nrm = math.sqrt(sum(c * c for c in axis))
        q = [c / nrm * math.sin(ang / 2) for c in axis] + [math.cos(ang / 2)]
        ref[n] = {"parent": MANNEQUIN_PARENTS[n], "t": list(map(float, t)), "q": q}
    return ref
