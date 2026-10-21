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


def write_gltf(model: Model, materials: list[MaterialInfo], out_path: Path, texture_prefix: str, scale: float = DEFAULT_SCALE) -> None:
    """materials[i] correspond à model.submeshes[i]. Les textures sont référencées en URI relative."""
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
        primitives.append(prim)

    bin_name = out_path.with_suffix(".bin").name
    doc = {
        "asset": {"version": "2.0", "generator": "gmod2nanos"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": model.name, "mesh": 0}],
        "meshes": [{"name": model.name, "primitives": primitives}],
        "materials": gl_materials,
        "buffers": [{"uri": bin_name, "byteLength": offset}],
        "bufferViews": views,
        "accessors": accessors,
        "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}],
        "extras": {"source": model.source},
    }
    if images:
        doc["images"] = images
        doc["textures"] = textures
    out_path.with_suffix(".bin").write_bytes(b"".join(bin_chunks))
    out_path.write_text(json.dumps(doc, indent=1))
