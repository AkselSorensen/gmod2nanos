"""Représentation intermédiaire d'un modèle (espace Source : Z en haut, unités Source)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class SubMesh:
    material: str  # chemin du matériau Source, ex. "models/props_c17/furniture01"
    positions: np.ndarray  # (N, 3) float32
    normals: np.ndarray  # (N, 3) float32
    uvs: np.ndarray  # (N, 2) float32, origine en haut à gauche (convention DirectX/glTF)
    indices: np.ndarray  # (M,) uint32, triangles


@dataclass
class Model:
    name: str
    source: str
    submeshes: list[SubMesh] = field(default_factory=list)
    bone_count: int = 1
    warnings: list[str] = field(default_factory=list)

    @property
    def is_skeletal(self) -> bool:
        return self.bone_count > 1

    @property
    def triangle_count(self) -> int:
        return sum(len(s.indices) // 3 for s in self.submeshes)


def fix_winding(sm: SubMesh) -> None:
    """Oriente les triangles pour que leur normale géométrique suive les normales des sommets (CCW)."""
    if len(sm.indices) < 3:
        return
    tri = sm.indices.reshape(-1, 3)
    p = sm.positions
    face_n = np.cross(p[tri[:, 1]] - p[tri[:, 0]], p[tri[:, 2]] - p[tri[:, 0]])
    vert_n = sm.normals[tri[:, 0]] + sm.normals[tri[:, 1]] + sm.normals[tri[:, 2]]
    agree = np.einsum("ij,ij->i", face_n, vert_n)
    if np.count_nonzero(agree < 0) > np.count_nonzero(agree > 0):
        sm.indices = tri[:, [0, 2, 1]].reshape(-1).astype(np.uint32)


def compact(material: str, positions, normals, uvs, global_ids: np.ndarray) -> SubMesh:
    """Construit un SubMesh à partir d'indices globaux en ne gardant que les sommets utilisés."""
    uniq, local = np.unique(global_ids, return_inverse=True)
    return SubMesh(
        material=material,
        positions=np.ascontiguousarray(positions[uniq], dtype=np.float32),
        normals=np.ascontiguousarray(normals[uniq], dtype=np.float32),
        uvs=np.ascontiguousarray(uvs[uniq], dtype=np.float32),
        indices=local.astype(np.uint32).reshape(-1),
    )
