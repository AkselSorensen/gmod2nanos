"""Reciblage d'un personnage GMod (squelette ValveBiped) sur le squelette Mannequin UE4 de nanos world.

Python pur (sans numpy) : ce fichier est aussi copié à côté du script d'import et exécuté dans Unreal.

Principe :
1. on tourne le modèle GMod pour qu'il regarde dans la même direction que le Mannequin ;
2. on plie ses membres (clavicules, bras, mains, doigts, jambes, pieds) pour qu'ils pointent comme ceux
   du Mannequin dans sa pose de référence ;
3. on lie le maillage à de nouveaux os qui portent les noms, la hiérarchie et l'orientation du Mannequin,
   placés aux articulations du modèle GMod (ses proportions sont conservées).

Les animations de nanos world (faites pour SKEL_Mannequin) s'appliquent alors correctement.
"""
from __future__ import annotations

import json
import math
import os
import struct

# ---------------------------------------------------------------------------
# Squelette Mannequin UE4 : os -> parent
# ---------------------------------------------------------------------------
MANNEQUIN_PARENTS: dict[str, str | None] = {"root": None, "pelvis": "root", "spine_01": "pelvis",
    "spine_02": "spine_01", "spine_03": "spine_02", "neck_01": "spine_03", "head": "neck_01",
    "ik_foot_root": "root", "ik_foot_l": "ik_foot_root", "ik_foot_r": "ik_foot_root",
    "ik_hand_root": "root", "ik_hand_gun": "ik_hand_root", "ik_hand_l": "ik_hand_gun", "ik_hand_r": "ik_hand_gun"}
for _s in ("l", "r"):
    MANNEQUIN_PARENTS.update({
        f"clavicle_{_s}": "spine_03", f"upperarm_{_s}": f"clavicle_{_s}", f"lowerarm_{_s}": f"upperarm_{_s}",
        f"hand_{_s}": f"lowerarm_{_s}", f"lowerarm_twist_01_{_s}": f"lowerarm_{_s}",
        f"upperarm_twist_01_{_s}": f"upperarm_{_s}", f"thigh_{_s}": "pelvis", f"calf_{_s}": f"thigh_{_s}",
        f"calf_twist_01_{_s}": f"calf_{_s}", f"foot_{_s}": f"calf_{_s}", f"ball_{_s}": f"foot_{_s}",
        f"thigh_twist_01_{_s}": f"thigh_{_s}",
    })
    for _f in ("thumb", "index", "middle", "ring", "pinky"):
        MANNEQUIN_PARENTS[f"{_f}_01_{_s}"] = f"hand_{_s}"
        MANNEQUIN_PARENTS[f"{_f}_02_{_s}"] = f"{_f}_01_{_s}"
        MANNEQUIN_PARENTS[f"{_f}_03_{_s}"] = f"{_f}_02_{_s}"

# ---------------------------------------------------------------------------
# ValveBiped -> Mannequin (le premier os GMod listé pour un os Mannequin donne la position de l'articulation)
# ---------------------------------------------------------------------------
VALVE_TO_MANNEQUIN: dict[str, str] = {
    "valvebiped.bip01_pelvis": "pelvis",
    "valvebiped.bip01_spine": "spine_01",
    "valvebiped.bip01_spine1": "spine_02",
    "valvebiped.bip01_spine2": "spine_03",
    "valvebiped.bip01_spine4": "spine_03",
    "valvebiped.bip01_neck1": "neck_01",
    "valvebiped.bip01_head1": "head",
}
for _S, _s in (("L", "l"), ("R", "r")):
    p = f"valvebiped.bip01_{_S.lower()}_"
    VALVE_TO_MANNEQUIN.update({
        p + "clavicle": f"clavicle_{_s}", p + "upperarm": f"upperarm_{_s}", p + "forearm": f"lowerarm_{_s}",
        p + "hand": f"hand_{_s}", p + "thigh": f"thigh_{_s}", p + "calf": f"calf_{_s}",
        p + "foot": f"foot_{_s}", p + "toe0": f"ball_{_s}", p + "ulna": f"lowerarm_twist_01_{_s}",
        p + "bicep": f"upperarm_twist_01_{_s}", p + "wrist": f"lowerarm_{_s}",
    })
    for _i, _f in enumerate(("thumb", "index", "middle", "ring", "pinky")):
        VALVE_TO_MANNEQUIN[p + f"finger{_i}"] = f"{_f}_01_{_s}"
        VALVE_TO_MANNEQUIN[p + f"finger{_i}1"] = f"{_f}_02_{_s}"
        VALVE_TO_MANNEQUIN[p + f"finger{_i}2"] = f"{_f}_03_{_s}"

# Os qu'on plie pour suivre la pose du Mannequin : (os, os enfant qui donne la direction)
POSE_CHAIN: list[tuple[str, str]] = []
for _s in ("l", "r"):
    POSE_CHAIN += [(f"clavicle_{_s}", f"upperarm_{_s}"), (f"upperarm_{_s}", f"lowerarm_{_s}"),
                   (f"lowerarm_{_s}", f"hand_{_s}"), (f"hand_{_s}", f"middle_01_{_s}")]
    for _f in ("thumb", "index", "middle", "ring", "pinky"):
        POSE_CHAIN += [(f"{_f}_01_{_s}", f"{_f}_02_{_s}"), (f"{_f}_02_{_s}", f"{_f}_03_{_s}")]
    POSE_CHAIN += [(f"thigh_{_s}", f"calf_{_s}"), (f"calf_{_s}", f"foot_{_s}"), (f"foot_{_s}", f"ball_{_s}")]

REQUIRED = ("pelvis", "spine_01", "head", "upperarm_l", "upperarm_r", "lowerarm_l", "lowerarm_r",
            "hand_l", "hand_r", "thigh_l", "thigh_r", "calf_l", "calf_r", "foot_l", "foot_r")


def is_valve_biped(bone_names: list[str]) -> bool:
    mapped = {VALVE_TO_MANNEQUIN.get(n.lower()) for n in bone_names}
    return all(r in mapped for r in REQUIRED)


# ---------------------------------------------------------------------------
# Mathématiques (matrices 4x4 en listes de listes, convention colonne : p' = M p)
# ---------------------------------------------------------------------------
def identity():
    return [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]


def mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def inv_rigid(m):
    r = [[m[j][i] for j in range(3)] for i in range(3)]
    t = [-sum(r[i][k] * m[k][3] for k in range(3)) for i in range(3)]
    return [r[0] + [t[0]], r[1] + [t[1]], r[2] + [t[2]], [0.0, 0.0, 0.0, 1.0]]


def inverse(m):
    """Inverse générale 4x4 (Gauss-Jordan)."""
    a = [list(map(float, m[i])) + [1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
    for c in range(4):
        piv = max(range(c, 4), key=lambda r: abs(a[r][c]))
        if abs(a[piv][c]) < 1e-12:
            raise ValueError("matrice non inversible")
        a[c], a[piv] = a[piv], a[c]
        f = a[c][c]
        a[c] = [x / f for x in a[c]]
        for r in range(4):
            if r != c and a[r][c]:
                g = a[r][c]
                a[r] = [x - g * y for x, y in zip(a[r], a[c])]
    return [row[4:] for row in a]


def from_qt(q, t):
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), t[0]],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), t[1]],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), t[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def orthonormalize(m):
    """Gram-Schmidt sur les colonnes de la partie rotation."""
    cols = [[m[r][c] for r in range(3)] for c in range(3)]
    x = norm(cols[0])
    y = norm(sub(cols[1], scale(x, dot(cols[1], x))))
    z = cross(x, y)
    if dot(z, cols[2]) < 0:
        z = scale(z, -1)
    out = [[x[r], y[r], z[r], m[r][3]] for r in range(3)]
    return out + [[0.0, 0.0, 0.0, 1.0]]


def to_quat(m):
    m = orthonormalize(m)
    t = m[0][0] + m[1][1] + m[2][2]
    if t > 0:
        s = math.sqrt(t + 1.0) * 2
        q = [(m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s, 0.25 * s]
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2
        q = [0.25 * s, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s, (m[2][1] - m[1][2]) / s]
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2
        q = [(m[0][1] + m[1][0]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s, (m[0][2] - m[2][0]) / s]
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2
        q = [(m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s, (m[1][0] - m[0][1]) / s]
    n = math.sqrt(sum(c * c for c in q))
    return [c / n for c in q]


def point(m, p):
    return [m[i][0] * p[0] + m[i][1] * p[1] + m[i][2] * p[2] + m[i][3] for i in range(3)]


def vector(m, v):
    return [m[i][0] * v[0] + m[i][1] * v[1] + m[i][2] * v[2] for i in range(3)]


def trans(m):
    return [m[0][3], m[1][3], m[2][3]]


def sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def add(a, b):
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


def scale(a, s):
    return [a[0] * s, a[1] * s, a[2] * s]


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def norm(a):
    n = math.sqrt(dot(a, a))
    return [a[0] / n, a[1] / n, a[2] / n] if n > 1e-12 else [0.0, 0.0, 0.0]


def axis_angle(axis, angle):
    x, y, z = norm(axis)
    c, s = math.cos(angle), math.sin(angle)
    k = 1 - c
    return [
        [c + x * x * k, x * y * k - z * s, x * z * k + y * s, 0.0],
        [y * x * k + z * s, c + y * y * k, y * z * k - x * s, 0.0],
        [z * x * k - y * s, z * y * k + x * s, c + z * z * k, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def rotation_between(u, v):
    u, v = norm(u), norm(v)
    if u == [0.0, 0.0, 0.0] or v == [0.0, 0.0, 0.0]:
        return identity()
    d = max(-1.0, min(1.0, dot(u, v)))
    axis = cross(u, v)
    if math.sqrt(dot(axis, axis)) < 1e-9:
        if d > 0:
            return identity()
        perp = cross(u, [1.0, 0.0, 0.0])
        if math.sqrt(dot(perp, perp)) < 1e-6:
            perp = cross(u, [0.0, 1.0, 0.0])
        return axis_angle(perp, math.pi)
    return axis_angle(axis, math.acos(d))


def about(pivot, rot):
    """Rotation `rot` (sans translation) autour du point `pivot`."""
    t = identity()
    for i in range(3):
        t[i][3] = pivot[i]
    ti = identity()
    for i in range(3):
        ti[i][3] = -pivot[i]
    return mul(t, mul(rot, ti))


# Repères : Unreal (X avant, Y droite, Z haut, cm) <-> glTF (Y haut, mètres), comme l'importeur d'Unreal.
_K = [[0.0, 0.0, 1.0, 0.0], [-1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
_KT = [[_K[j][i] for j in range(4)] for i in range(4)]


def gltf_to_ue_point(p):
    return [p[2] * 100.0, -p[0] * 100.0, p[1] * 100.0]


def ue_to_gltf_point(p):
    return [-p[1] / 100.0, p[2] / 100.0, p[0] / 100.0]


def gltf_to_ue_vec(v):
    return [v[2], -v[0], v[1]]


def ue_to_gltf_vec(v):
    return [-v[1], v[2], v[0]]


def gltf_to_ue_matrix(m):
    out = mul(_K, mul(m, _KT))
    for i in range(3):
        out[i][3] *= 100.0
    return out


def ue_to_gltf_matrix(m):
    out = mul(_KT, mul(m, _K))
    for i in range(3):
        out[i][3] /= 100.0
    return out


# ---------------------------------------------------------------------------
# Lecture / écriture glTF (uniquement ce que produit gmod2nanos)
# ---------------------------------------------------------------------------
_COMP = {5126: ("f", 4), 5125: ("I", 4), 5123: ("H", 2), 5121: ("B", 1)}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def _read_accessor(doc, buf, idx):
    acc = doc["accessors"][idx]
    view = doc["bufferViews"][acc["bufferView"]]
    fmt, size = _COMP[acc["componentType"]]
    n = _NCOMP[acc["type"]]
    off = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    flat = struct.unpack_from(f"<{acc['count'] * n}{fmt}", buf, off)
    return [list(flat[i * n:(i + 1) * n]) for i in range(acc["count"])] if n > 1 else list(flat)


def _node_local(node):
    if "matrix" in node:
        m = node["matrix"]
        return [[m[c * 4 + r] for c in range(4)] for r in range(4)]
    return from_qt(node.get("rotation", [0, 0, 0, 1]), node.get("translation", [0, 0, 0]))


def load_source(path):
    """Charge le glTF « source » d'un personnage : os ValveBiped et maillage, convertis en espace Unreal."""
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    with open(os.path.join(os.path.dirname(path), doc["buffers"][0]["uri"]), "rb") as f:
        buf = f.read()
    skin = doc["skins"][0]
    joint_nodes = skin["joints"]
    ibms = _read_accessor(doc, buf, skin["inverseBindMatrices"])
    parent_of = {}
    for i, n in enumerate(doc["nodes"]):
        for c in n.get("children", []):
            parent_of[c] = i
    bones = []
    for k, node_idx in enumerate(joint_nodes):
        ibm = [[ibms[k][c * 4 + r] for c in range(4)] for r in range(4)]
        world = gltf_to_ue_matrix(inverse(ibm))
        parent_node = parent_of.get(node_idx)
        parent = joint_nodes.index(parent_node) if parent_node in joint_nodes else -1
        bones.append({"name": doc["nodes"][node_idx]["name"], "parent": parent, "world": world})
    prims = []
    for prim in doc["meshes"][0]["primitives"]:
        a = prim["attributes"]
        prims.append({
            "positions": [gltf_to_ue_point(p) for p in _read_accessor(doc, buf, a["POSITION"])],
            "normals": [gltf_to_ue_vec(n) for n in _read_accessor(doc, buf, a["NORMAL"])],
            "uvs": _read_accessor(doc, buf, a["TEXCOORD_0"]),
            "joints": _read_accessor(doc, buf, a["JOINTS_0"]),
            "weights": _read_accessor(doc, buf, a["WEIGHTS_0"]),
            "indices": _read_accessor(doc, buf, prim["indices"]),
            "material": prim.get("material"),
        })
    return doc, bones, prims


def _bfs(parents):
    order, queue = [], [b for b, p in parents.items() if p is None or p not in parents]
    children = {}
    for b, p in parents.items():
        children.setdefault(p, []).append(b)
    while queue:
        b = queue.pop(0)
        order.append(b)
        queue.extend(children.get(b, []))
    return order


# ---------------------------------------------------------------------------
# Reciblage
# ---------------------------------------------------------------------------
def retarget(bones, prims, mannequin):
    """bones : os GMod (espace Unreal). mannequin : {nom: {"parent", "world"}} pose de référence monde.

    Renvoie (os de sortie [(nom, parent, monde)], primitives reposées et repondérées)."""
    parents = {n: (d.get("parent") if d.get("parent") in mannequin else None) for n, d in mannequin.items()}
    order = _bfs(parents)
    mw = {n: orthonormalize(mannequin[n]["world"]) for n in order}

    # os GMod -> os Mannequin (directement, ou via l'ancêtre le plus proche)
    direct = [VALVE_TO_MANNEQUIN.get(b["name"].lower()) for b in bones]
    direct = [d if d in mw else None for d in direct]
    fallback = "pelvis" if "pelvis" in mw else order[0]

    def resolve(i):
        while i >= 0:
            if direct[i]:
                return direct[i]
            i = bones[i]["parent"]
        return fallback

    g2m = [resolve(i) for i in range(len(bones))]
    joint_pos = {}
    for i, b in enumerate(bones):
        m = VALVE_TO_MANNEQUIN.get(b["name"].lower())
        if m in mw and m not in joint_pos:
            joint_pos[m] = trans(b["world"])
    missing = [r for r in REQUIRED if r not in joint_pos]
    if missing:
        raise ValueError("squelette ValveBiped incomplet, os manquants : " + ", ".join(missing))

    # 1. orientation : le modèle doit regarder du même côté que le Mannequin
    gl = sub(joint_pos["thigh_l"], joint_pos["thigh_r"])
    ml = sub(trans(mw["thigh_l"]), trans(mw["thigh_r"]))
    yaw = math.atan2(ml[1], ml[0]) - math.atan2(gl[1], gl[0])
    root_rot = axis_angle([0.0, 0.0, 1.0], yaw)
    joint_pos = {k: point(root_rot, v) for k, v in joint_pos.items()}
    acc = {n: root_rot for n in order}  # transformation accumulée de chaque os Mannequin

    subtree = {n: [] for n in order}
    for n in reversed(order):
        subtree[n].insert(0, n)
        p = parents[n]
        if p is not None:
            subtree[p] = subtree[p] + subtree[n]

    def apply(bone, rot):
        step = about(joint_pos[bone], rot)
        for x in subtree[bone]:
            acc[x] = mul(step, acc[x])
            if x != bone and x in joint_pos:
                joint_pos[x] = point(step, joint_pos[x])

    # 2. pose : chaque segment pointe comme celui du Mannequin
    for bone, child in POSE_CHAIN:
        if bone in joint_pos and child in joint_pos and bone in mw and child in mw:
            cur = sub(joint_pos[child], joint_pos[bone])
            tgt = sub(trans(mw[child]), trans(mw[bone]))
            apply(bone, rotation_between(cur, tgt))
            side = bone[-1]
            if bone == f"hand_{side}":  # rotation de la paume autour de l'axe de la main
                a, b = f"index_01_{side}", f"pinky_01_{side}"
                if all(k in joint_pos and k in mw for k in (a, b)):
                    axis = norm(tgt)
                    cur_l = sub(joint_pos[a], joint_pos[b])
                    tgt_l = sub(trans(mw[a]), trans(mw[b]))
                    cur_l = norm(sub(cur_l, scale(axis, dot(cur_l, axis))))
                    tgt_l = norm(sub(tgt_l, scale(axis, dot(tgt_l, axis))))
                    if cur_l != [0.0, 0.0, 0.0] and tgt_l != [0.0, 0.0, 0.0]:
                        ang = math.atan2(dot(cross(cur_l, tgt_l), axis), dot(cur_l, tgt_l))
                        apply(bone, axis_angle(axis, ang))

    # 3. nouveaux os : orientation du Mannequin, position des articulations GMod
    out_world = {}
    for n in order:
        if n in joint_pos:
            m = [row[:] for row in mw[n]]
            for i in range(3):
                m[i][3] = joint_pos[n][i]
            out_world[n] = m
        elif parents[n] is not None:
            p = parents[n]
            out_world[n] = mul(out_world[p], mul(inv_rigid(mw[p]), mw[n]))
        else:
            out_world[n] = mw[n]
    index = {n: i for i, n in enumerate(order)}

    # 4. maillage : sommets reposés, influences renommées
    new_prims = []
    for prim in prims:
        pos, nrm, joints, weights = [], [], [], []
        for p, nv, js, ws in zip(prim["positions"], prim["normals"], prim["joints"], prim["weights"]):
            infl = {}
            for j, w in zip(js, ws):
                if w > 0:
                    m = g2m[j]
                    infl[m] = infl.get(m, 0.0) + w
            if not infl:
                infl = {g2m[js[0]]: 1.0}
            total = sum(infl.values())
            np_, nn = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
            for m, w in infl.items():
                np_ = add(np_, scale(point(acc[m], p), w / total))
                nn = add(nn, scale(vector(acc[m], nv), w / total))
            top = sorted(infl.items(), key=lambda kv: -kv[1])[:4]
            t = sum(w for _, w in top)
            pos.append(np_)
            nrm.append(norm(nn) if nn != [0.0, 0.0, 0.0] else [0.0, 0.0, 1.0])
            joints.append([index[m] for m, _ in top] + [0] * (4 - len(top)))
            weights.append([w / t for _, w in top] + [0.0] * (4 - len(top)))
        new_prims.append(dict(prim, positions=pos, normals=nrm, joints=joints, weights=weights))
    out_bones = [(n, parents[n], out_world[n]) for n in order]
    return out_bones, new_prims


def write(path, doc_src, out_bones, prims, name):
    """Écrit le personnage reciblé en glTF (matériaux et textures repris du glTF source)."""
    chunks, views, accessors = [], [], []
    offset = 0

    def put(fmt, rows, typ, comp, target=None, minmax=False):
        nonlocal offset
        flat = [x for r in rows for x in (r if isinstance(r, (list, tuple)) else [r])]
        data = struct.pack(f"<{len(flat)}{fmt}", *flat)
        pad = (-len(data)) % 4
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if target:
            view["target"] = target
        views.append(view)
        chunks.append(data + b"\0" * pad)
        offset += len(data) + pad
        a = {"bufferView": len(views) - 1, "componentType": comp, "count": len(rows), "type": typ}
        if minmax:
            a["min"] = [min(r[i] for r in rows) for i in range(3)]
            a["max"] = [max(r[i] for r in rows) for i in range(3)]
        accessors.append(a)
        return len(accessors) - 1

    gl_prims = []
    for prim in prims:
        pos = [[float(c) for c in ue_to_gltf_point(p)] for p in prim["positions"]]
        attrs = {
            "POSITION": put("f", pos, "VEC3", 5126, 34962, minmax=True),
            "NORMAL": put("f", [ue_to_gltf_vec(n) for n in prim["normals"]], "VEC3", 5126, 34962),
            "TEXCOORD_0": put("f", prim["uvs"], "VEC2", 5126, 34962),
            "JOINTS_0": put("H", prim["joints"], "VEC4", 5123, 34962),
            "WEIGHTS_0": put("f", prim["weights"], "VEC4", 5126, 34962),
        }
        gp = {"attributes": attrs, "indices": put("I", prim["indices"], "SCALAR", 5125, 34963), "mode": 4}
        if prim.get("material") is not None:
            gp["material"] = prim["material"]
        gl_prims.append(gp)

    names = [b[0] for b in out_bones]
    idx = {n: i for i, n in enumerate(names)}
    worlds = [ue_to_gltf_matrix(b[2]) for b in out_bones]
    nodes = [{"name": name, "mesh": 0, "skin": 0}]
    roots = []
    for i, (n, parent, _) in enumerate(out_bones):
        local = worlds[i] if parent is None else mul(inv_rigid(orthonormalize(worlds[idx[parent]])), worlds[i])
        nodes.append({"name": n, "translation": trans(local), "rotation": to_quat(local)})
        if parent is None:
            roots.append(1 + i)
        else:
            nodes[1 + idx[parent]].setdefault("children", []).append(1 + i)
    ibm = []
    for w in worlds:
        inv = inv_rigid(orthonormalize(w))
        ibm.append([inv[r][c] for c in range(4) for r in range(4)])
    ibm_acc = put("f", ibm, "MAT4", 5126)

    doc = {
        "asset": {"version": "2.0", "generator": "gmod2nanos retarget"},
        "scene": 0,
        "scenes": [{"nodes": [0] + roots}],
        "nodes": nodes,
        "meshes": [{"name": name, "primitives": gl_prims}],
        "skins": [{"joints": list(range(1, len(out_bones) + 1)), "inverseBindMatrices": ibm_acc, "skeleton": roots[0]}],
        "buffers": [{"uri": os.path.basename(path)[:-5] + ".bin", "byteLength": offset}],
        "bufferViews": views,
        "accessors": accessors,
    }
    for key in ("materials", "images", "textures", "samplers"):
        if key in doc_src:
            doc[key] = doc_src[key]
    with open(path[:-5] + ".bin", "wb") as f:
        f.write(b"".join(chunks))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"))


def retarget_file(src_path, mannequin, out_path, name):
    """mannequin : {nom: {"parent": str|None, "t": [x,y,z], "q": [x,y,z,w]}} en espace monde Unreal."""
    ref = {n: {"parent": d.get("parent"), "world": from_qt(d["q"], d["t"])} for n, d in mannequin.items()}
    doc, bones, prims = load_source(src_path)
    out_bones, new_prims = retarget(bones, prims, ref)
    write(out_path, doc, out_bones, new_prims, name)
    return [b[0] for b in out_bones]
