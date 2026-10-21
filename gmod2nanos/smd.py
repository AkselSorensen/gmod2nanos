"""Lecture des fichiers .smd (Studiomdl Data, modèles décompilés avec Crowbar) et des .qc associés."""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .mesh import Model, compact, fix_winding


def parse_smd(text: str, name: str, source: str) -> Model:
    model = Model(name=name, source=source)
    lines = iter(text.splitlines())
    by_mat: dict[str, list[tuple]] = {}
    bones = 0
    for line in lines:
        tok = line.strip().lower()
        if tok == "nodes":
            for l2 in lines:
                if l2.strip().lower() == "end":
                    break
                if l2.strip():
                    bones += 1
        elif tok == "triangles":
            while True:
                mat = next(lines, None)
                if mat is None:
                    break
                mat = mat.strip()
                if mat.lower() == "end":
                    break
                if not mat:
                    continue
                tri = []
                for _ in range(3):
                    parts = next(lines, "").split()
                    if len(parts) < 9:
                        raise ValueError("ligne de triangle .smd invalide")
                    tri.append(tuple(float(x) for x in parts[1:9]))
                by_mat.setdefault(_strip_ext(mat), []).extend(tri)
    model.bone_count = max(bones, 1)
    for mat, verts in by_mat.items():
        arr = np.asarray(verts, dtype=np.float32)
        pos, nrm, uv = arr[:, 0:3], arr[:, 3:6], arr[:, 6:8].copy()
        uv[:, 1] = 1.0 - uv[:, 1]  # SMD : origine en bas à gauche
        # fusionne les sommets identiques
        key = np.ascontiguousarray(arr).view(np.dtype((np.void, arr.dtype.itemsize * 8))).ravel()
        _, first, inverse = np.unique(key, return_index=True, return_inverse=True)
        sm = compact(mat, pos[first], nrm[first], uv[first], inverse.reshape(-1))
        fix_winding(sm)
        model.submeshes.append(sm)
    return model


def has_triangles(text: str) -> bool:
    return re.search(r"^\s*triangles\s*$", text, re.M | re.I) is not None


def _strip_ext(mat: str) -> str:
    return re.sub(r"\.(vmt|bmp|tga|png|vtf)$", "", mat.replace("\\", "/"), flags=re.I)


@dataclass
class QcInfo:
    path: Path
    modelname: str = ""
    cdmaterials: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)  # .smd du maillage de référence (bodygroup option 1)
    other_smds: set[str] = field(default_factory=set)  # anims, physique, LOD, autres bodygroups


def _tokens(text: str) -> list[str]:
    text = re.sub(r"//[^\n]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    lex = shlex.shlex(text, posix=True)
    lex.whitespace_split = True
    lex.commenters = ""
    lex.escape = ""
    try:
        return list(lex)
    except ValueError:
        return text.split()


def parse_qc(path: Path) -> QcInfo:
    info = QcInfo(path=path)
    toks = _tokens(path.read_text(encoding="utf-8", errors="replace"))
    norm = lambda s: s.replace("\\", "/").lower()  # noqa: E731
    i = 0
    while i < len(toks):
        t = toks[i].lower()
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if t == "$modelname":
            info.modelname = nxt
        elif t == "$cdmaterials":
            info.cdmaterials.append(nxt)
        elif t == "$body":
            info.references.append(norm(toks[i + 2]) if i + 2 < len(toks) else "")
        elif t == "$model":
            info.references.append(norm(toks[i + 2]) if i + 2 < len(toks) else "")
        elif t == "$bodygroup":
            j = i + 2
            if j < len(toks) and toks[j] == "{":
                j += 1
                first = True
                while j < len(toks) and toks[j] != "}":
                    if toks[j].lower() == "studio" and j + 1 < len(toks):
                        if first:
                            info.references.append(norm(toks[j + 1]))
                        else:
                            info.other_smds.add(norm(toks[j + 1]))
                        first = False
                        j += 1
                    elif toks[j].lower() == "blank":
                        first = False
                    j += 1
        elif t in ("$sequence", "$animation", "$collisionmodel", "$collisionjoints", "replacemodel", "$includemodel"):
            for tok in toks[i + 1 : i + 4]:
                if tok.lower().endswith((".smd", ".dmx")):
                    info.other_smds.add(norm(tok))
        i += 1
    info.references = [r for r in info.references if r]
    return info
