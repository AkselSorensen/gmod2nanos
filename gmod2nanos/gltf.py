"""Écriture glTF 2.0 (.gltf + .bin) importable directement dans Unreal Engine 5 (Interchange)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .materials import MaterialInfo
from .mesh import Model

# 1 unité Source ≈ 1 pouce = 2,54 cm ; un joueur GMod (72 unités) fait ainsi ~1,83 m.
DEFAULT_SCALE = 0.0254


def source_to_gltf(v: np.ndarray, scale: float = 1.0) -> np.ndarray:
    """Source (X avant, Y gauche, Z haut) -> glTF (Y haut, Z avant, X gauche). Permutation circulaire :
    le repère reste direct, l'ordre des triangles est donc conservé."""
    return np.ascontiguousarray(v[:, [1, 2, 0]] * scale, dtype=np.float32)


_G = np.eye(4)
_G[:3, :3] = [[0, 1, 0], [0, 0, 1], [1, 0, 0]]  # Source -> glTF (permutation circulaire)


def matrix_to_gltf(m: np.ndarray, scale: float) -> np.ndarray:
    out = _G @ m @ _G.T
    out[:3, 3] *= scale
    return out


def quat_from_matrix(r: np.ndarray) -> list[float]:
    """Matrice de rotation 3x3 -> quaternion [x, y, z, w] normalisé."""
    u, _, vt = np.linalg.svd(r)
    r = u @ vt  # ré-orthonormalise
    t = np.trace(r)
    if t > 0:
        s = np.sqrt(t + 1.0) * 2
        q = [(r[2, 1] - r[1, 2]) / s, (r[0, 2] - r[2, 0]) / s, (r[1, 0] - r[0, 1]) / s, 0.25 * s]
    elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = np.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2]) * 2
        q = [0.25 * s, (r[0, 1] + r[1, 0]) / s, (r[0, 2] + r[2, 0]) / s, (r[2, 1] - r[1, 2]) / s]
    elif r[1, 1] > r[2, 2]:
        s = np.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2]) * 2
        q = [(r[0, 1] + r[1, 0]) / s, 0.25 * s, (r[1, 2] + r[2, 1]) / s, (r[0, 2] - r[2, 0]) / s]
    else:
        s = np.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1]) * 2
        q = [(r[0, 2] + r[2, 0]) / s, (r[1, 2] + r[2, 1]) / s, 0.25 * s, (r[1, 0] - r[0, 1]) / s]
    q = np.array(q)
    return (q / np.linalg.norm(q)).tolist()


def write_gltf(
    model: Model, materials: list[MaterialInfo], out_path: Path, texture_prefix: str,
    scale: float = DEFAULT_SCALE, skinned: bool = False,
) -> None:
    """materials[i] correspond à model.submeshes[i]. Les textures sont référencées en URI relative.

    skinned=True ajoute le squelette (nœuds + skin) et les influences des sommets."""
    skinned = skinned and bool(model.bones)
    bin_chunks: list[bytes] = []
    views, accessors = [], []
    offset = 0

    def add(arr: np.ndarray, target: int, comp: int, typ: str, minmax: bool = False) -> int:
        nonlocal offset
        data = arr.tobytes()
        pad = (-len(data)) % 4
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data), "target": target})
        bin_chunks.append(data + b"\0" * pad)
        offset += len(data) + pad
        acc = {"bufferView": len(views) - 1, "componentType": comp, "count": len(arr), "type": typ}
        if minmax:
            acc["min"] = arr.min(axis=0).tolist()
            acc["max"] = arr.max(axis=0).tolist()
        accessors.append(acc)
        return len(accessors) - 1

    gl_materials, images, textures = [], [], []
    mat_index: dict[str, int] = {}
    img_index: dict[str, int] = {}

    def tex(rel: str) -> int:
        if rel not in img_index:
            images.append({"uri": texture_prefix + rel, "name": Path(rel).stem})
            textures.append({"source": len(images) - 1, "sampler": 0})
            img_index[rel] = len(textures) - 1
        return img_index[rel]

    def material(m: MaterialInfo) -> int:
        if m.name in mat_index:
            return mat_index[m.name]
        g: dict = {"name": m.name, "pbrMetallicRoughness": {"metallicFactor": 0.0, "roughnessFactor": 0.8}}
        if m.basetexture:
            g["pbrMetallicRoughness"]["baseColorTexture"] = {"index": tex(m.basetexture)}
        if m.normalmap:
            g["normalTexture"] = {"index": tex(m.normalmap)}
        if m.translucent:
            g["alphaMode"] = "BLEND"
        elif m.alphatest:
            g["alphaMode"] = "MASK"
            g["alphaCutoff"] = m.alpha_cutoff
        if m.double_sided:
            g["doubleSided"] = True
        g["extras"] = {"source_material": m.path, "source_shader": m.shader}
        gl_materials.append(g)
        mat_index[m.name] = len(gl_materials) - 1
        return mat_index[m.name]

    primitives = []
    for sm, m in zip(model.submeshes, materials):
        if not len(sm.indices):
            continue
        pos = source_to_gltf(sm.positions, scale)
        nrm = source_to_gltf(sm.normals)
        lens = np.linalg.norm(nrm, axis=1, keepdims=True)
        nrm = np.ascontiguousarray(np.where(lens > 1e-8, nrm / np.maximum(lens, 1e-8), [0, 1, 0]), dtype=np.float32)
        prim = {
            "attributes": {
                "POSITION": add(pos, 34962, 5126, "VEC3", minmax=True),
                "NORMAL": add(nrm, 34962, 5126, "VEC3"),
                "TEXCOORD_0": add(np.ascontiguousarray(sm.uvs, np.float32), 34962, 5126, "VEC2"),
            },
            "indices": add(np.ascontiguousarray(sm.indices, np.uint32), 34963, 5125, "SCALAR"),
            "material": material(m),
            "mode": 4,
        }
        if skinned:
            j = sm.joints if sm.joints is not None else np.zeros((len(pos), 4), np.uint16)
            w = sm.weights if sm.weights is not None else np.tile(np.float32([1, 0, 0, 0]), (len(pos), 1))
            w = w / np.maximum(w.sum(axis=1, keepdims=True), 1e-8)
            j = np.where(w > 0, j, 0)
            prim["attributes"]["JOINTS_0"] = add(np.ascontiguousarray(j, np.uint16), 34962, 5123, "VEC4")
            prim["attributes"]["WEIGHTS_0"] = add(np.ascontiguousarray(w, np.float32), 34962, 5126, "VEC4")
        primitives.append(prim)

    nodes: list[dict] = [{"name": model.name, "mesh": 0}]
    scene_nodes = [0]
    skins = []
    if skinned:
        worlds = [matrix_to_gltf(b.bind, scale) for b in model.bones]
        for i, b in enumerate(model.bones):
            local = worlds[i] if b.parent < 0 else np.linalg.inv(worlds[b.parent]) @ worlds[i]
            nodes.append({"name": b.name, "translation": local[:3, 3].tolist(), "rotation": quat_from_matrix(local[:3, :3])})
        for i, b in enumerate(model.bones):
            if b.parent < 0:
                scene_nodes.append(1 + i)
            else:
                nodes[1 + b.parent].setdefault("children", []).append(1 + i)
        ibm = np.stack([np.linalg.inv(w).T.reshape(-1) for w in worlds]).astype(np.float32)  # colonnes d'abord
        views_before = len(views)
        ibm_acc = add(ibm, 0, 5126, "MAT4")
        del views[views_before]["target"]  # pas de cible pour les inverseBindMatrices
        nodes[0]["skin"] = 0
        skins.append({"joints": list(range(1, len(model.bones) + 1)), "inverseBindMatrices": ibm_acc})

    bin_name = out_path.with_suffix(".bin").name
    doc = {
        "asset": {"version": "2.0", "generator": "gmod2nanos"},
        "scene": 0,
        "scenes": [{"nodes": scene_nodes}],
        "nodes": nodes,
        "meshes": [{"name": model.name, "primitives": primitives}],
        "materials": gl_materials,
        "buffers": [{"uri": bin_name, "byteLength": offset}],
        "bufferViews": views,
        "accessors": accessors,
        "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}],
        "extras": {"source": model.source},
    }
    if skins:
        doc["skins"] = skins
    if images:
        doc["images"] = images
        doc["textures"] = textures
    out_path.with_suffix(".bin").write_bytes(b"".join(bin_chunks))
    out_path.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
