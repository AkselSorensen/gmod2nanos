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
    joints: np.ndarray | None = None  # (N, 4) uint16, index dans Model.bones
    weights: np.ndarray | None = None  # (N, 4) float32, somme = 1


@dataclass
class Bone:
    name: str
    parent: int  # -1 pour une racine
    bind: np.ndarray  # (4, 4) matrice monde de la pose de référence, espace Source


@dataclass
class Model:
    name: str
    source: str
    submeshes: list[SubMesh] = field(default_factory=list)
    bone_count: int = 1
    bones: list[Bone] = field(default_factory=list)
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


def compact(material: str, positions, normals, uvs, global_ids: np.ndarray, joints=None, weights=None) -> SubMesh:
    """Construit un SubMesh à partir d'indices globaux en ne gardant que les sommets utilisés."""
    uniq, local = np.unique(global_ids, return_inverse=True)
    return SubMesh(
        material=material,
        positions=np.ascontiguousarray(positions[uniq], dtype=np.float32),
        normals=np.ascontiguousarray(normals[uniq], dtype=np.float32),
        uvs=np.ascontiguousarray(uvs[uniq], dtype=np.float32),
        indices=local.astype(np.uint32).reshape(-1),
        joints=None if joints is None else np.ascontiguousarray(joints[uniq], dtype=np.uint16),
        weights=None if weights is None else np.ascontiguousarray(weights[uniq], dtype=np.float32),
    )


def quat_to_matrix(x: float, y: float, z: float, w: float) -> np.ndarray:
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


def euler_to_matrix(rx: float, ry: float, rz: float) -> np.ndarray:
    """Angles SMD (radians) : rotation X, puis Y, puis Z."""
    cx, sx, cy, sy, cz, sz = np.cos(rx), np.sin(rx), np.cos(ry), np.sin(ry), np.cos(rz), np.sin(rz)
    mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return mz @ my @ mx
