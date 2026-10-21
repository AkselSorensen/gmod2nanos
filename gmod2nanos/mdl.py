"""Lecture des modèles compilés Source : .mdl (structure) + .vvd (sommets) + .vtx (triangles).

Seul le LOD 0 et le skin 0 sont exportés ; chaque bodygroup prend sa première option.
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from .mesh import Model, compact, fix_winding

VVD_VERTEX = np.dtype(
    [
        ("weights", "<f4", 3),
        ("bones", "u1", 3),
        ("numbones", "u1"),
        ("pos", "<f4", 3),
        ("normal", "<f4", 3),
        ("uv", "<f4", 2),
    ]
)
assert VVD_VERTEX.itemsize == 48

VTX_VERTEX = np.dtype([("bw", "u1", 3), ("nb", "u1"), ("orig", "<u2"), ("bone", "u1", 3)])
assert VTX_VERTEX.itemsize == 9


def _cstr(data: bytes, off: int) -> str:
    end = data.find(b"\0", off)
    return data[off : end if end >= 0 else len(data)].decode("latin-1")


class MdlHeader:
    def __init__(self, data: bytes):
        if data[:4] != b"IDST":
            raise ValueError("en-tête .mdl invalide")
        self.data = data
        self.version, self.checksum = struct.unpack_from("<ii", data, 4)
        self.name = _cstr(data, 12)[:64]
        (self.flags,) = struct.unpack_from("<i", data, 152)
        self.bone_count, self.bone_offset = struct.unpack_from("<ii", data, 156)
        self.tex_count, self.tex_offset = struct.unpack_from("<ii", data, 204)
        self.texdir_count, self.texdir_offset = struct.unpack_from("<ii", data, 212)
        self.skinref_count, self.skinfamily_count, self.skin_offset = struct.unpack_from("<iii", data, 220)
        self.bodypart_count, self.bodypart_offset = struct.unpack_from("<ii", data, 232)

    def textures(self) -> list[str]:
        out = []
        for i in range(self.tex_count):
            base = self.tex_offset + i * 64
            (name_off,) = struct.unpack_from("<i", self.data, base)
            out.append(_cstr(self.data, base + name_off))
        return out

    def cdmaterials(self) -> list[str]:
        out = []
        for i in range(self.texdir_count):
            (off,) = struct.unpack_from("<i", self.data, self.texdir_offset + i * 4)
            out.append(_cstr(self.data, off))
        return out

    def skin0(self) -> list[int]:
        return list(struct.unpack_from(f"<{self.skinref_count}h", self.data, self.skin_offset))

    def bodyparts(self):
        """Pour chaque bodypart : liste de modèles (nom, vertexindex, [(material, vertexoffset)])."""
        d = self.data
        parts = []
        for b in range(self.bodypart_count):
            bp = self.bodypart_offset + b * 16
            _name_off, nummodels, _base, modelindex = struct.unpack_from("<iiii", d, bp)
            models = []
            for m in range(nummodels):
                mo = bp + modelindex + m * 148
                mname = _cstr(d, mo)[:64]
                nummeshes, meshindex, numverts, vertexindex = struct.unpack_from("<iiii", d, mo + 72)
                meshes = []
                for k in range(nummeshes):
                    me = mo + meshindex + k * 116
                    material, _modelindex, mnumverts, vertexoffset = struct.unpack_from("<iiii", d, me)
                    meshes.append((material, vertexoffset, mnumverts))
                models.append((mname, vertexindex // 48, numverts, meshes))
            parts.append(models)
        return parts


def read_vvd(data: bytes) -> np.ndarray:
    if data[:4] != b"IDSV":
        raise ValueError("en-tête .vvd invalide")
    num_lods = struct.unpack_from("<i", data, 12)[0]
    lod_verts = struct.unpack_from("<8i", data, 16)
    num_fixups, fixup_start, vertex_start, _tangent_start = struct.unpack_from("<4i", data, 48)
    verts = np.frombuffer(data, VVD_VERTEX, count=lod_verts[0], offset=vertex_start)
    if num_fixups > 0 and num_lods > 0:
        fix = np.frombuffer(data, "<i4", count=num_fixups * 3, offset=fixup_start).reshape(-1, 3)
        parts = [verts[src : src + n] for lod, src, n in fix if lod >= 0]
        verts = np.concatenate(parts) if parts else verts
    return verts


class _Vtx:
    def __init__(self, data: bytes, sg_size: int, strip_size: int):
        self.d = data
        self.sg_size = sg_size
        self.strip_size = strip_size
        (self.version,) = struct.unpack_from("<i", data, 0)
        if self.version != 7:
            raise ValueError(f"version .vtx non supportée : {self.version}")
        self.num_bodyparts, self.bodypart_offset = struct.unpack_from("<ii", data, 28)

    def mesh_indices(self, bp: int, model: int, mesh: int) -> np.ndarray:
        """Retourne la liste de triangles (en origMeshVertID) pour un mesh du LOD 0."""
        d = self.d
        bpo = self.bodypart_offset + bp * 8
        nmodels, modeloff = struct.unpack_from("<ii", d, bpo)
        mo = bpo + modeloff + model * 8
        nlods, lodoff = struct.unpack_from("<ii", d, mo)
        if nlods <= 0:
            return np.zeros(0, np.uint32)
        lo = mo + lodoff
        nmeshes, meshoff, _switch = struct.unpack_from("<iif", d, lo)
        meo = lo + meshoff + mesh * 9
        nsg, sgoff, _flags = struct.unpack_from("<iiB", d, meo)
        tris = []
        for g in range(nsg):
            sgo = meo + sgoff + g * self.sg_size
            nverts, vertoff, nidx, idxoff, nstrips, stripoff = struct.unpack_from("<6i", d, sgo)
            verts = np.frombuffer(d, VTX_VERTEX, count=nverts, offset=sgo + vertoff)
            idx = np.frombuffer(d, "<u2", count=nidx, offset=sgo + idxoff)
            if nidx and int(idx.max()) >= nverts:
                raise ValueError("indices .vtx hors limites")
            orig = verts["orig"].astype(np.uint32)
            if nstrips <= 0:
                tris.append(orig[idx[: nidx - nidx % 3]])
                continue
            for s in range(nstrips):
                so = sgo + stripoff + s * self.strip_size
                sn, soff = struct.unpack_from("<ii", d, so)
                flags = d[so + 18]
                if soff < 0 or soff + sn > nidx:
                    raise ValueError("strip .vtx hors limites")
                sidx = idx[soff : soff + sn]
                if flags & 2:  # triangle strip
                    out = []
                    for i in range(len(sidx) - 2):
                        a, b, c = sidx[i], sidx[i + 1], sidx[i + 2]
                        if a == b or b == c or a == c:
                            continue
                        out.extend((a, b, c) if i % 2 == 0 else (a, c, b))
                    sidx = np.array(out, np.uint16)
                else:
                    sidx = sidx[: len(sidx) - len(sidx) % 3]
                tris.append(orig[sidx])
        return np.concatenate(tris) if tris else np.zeros(0, np.uint32)


def _open_vtx(data: bytes, parts) -> _Vtx:
    """Les modèles CS:GO/v49 récents ont des en-têtes de strip group plus longs : on teste les deux."""
    err: Exception | None = None
    for sg, st in ((25, 27), (33, 35)):
        try:
            vtx = _Vtx(data, sg, st)
            for b, models in enumerate(parts):
                for m, (_n, _vi, _nv, meshes) in enumerate(models[:1]):
                    for k in range(len(meshes)):
                        vtx.mesh_indices(b, m, k)
            return vtx
        except (ValueError, struct.error) as e:
            err = e
    raise ValueError(f".vtx illisible : {err}")


def find_vtx(mdl_path: Path) -> Path | None:
    stem = mdl_path.with_suffix("")
    for suffix in (".dx90.vtx", ".vtx", ".dx80.vtx", ".sw.vtx"):
        for cand in (Path(str(stem) + suffix), Path(str(stem) + suffix.upper())):
            if cand.exists():
                return cand
    # recherche insensible à la casse
    low = {p.name.lower(): p for p in mdl_path.parent.iterdir()}
    for suffix in (".dx90.vtx", ".vtx", ".dx80.vtx", ".sw.vtx"):
        p = low.get(stem.name.lower() + suffix)
        if p:
            return p
    return None


def find_sibling(mdl_path: Path, ext: str) -> Path | None:
    low = {p.name.lower(): p for p in mdl_path.parent.iterdir()}
    return low.get(mdl_path.stem.lower() + ext)


def load_mdl(mdl_path: Path, name: str, source: str) -> tuple[Model, list[str]]:
    """Retourne le modèle et la liste des cdmaterials (dossiers de recherche des matériaux)."""
    hdr = MdlHeader(mdl_path.read_bytes())
    vvd_path = find_sibling(mdl_path, ".vvd")
    vtx_path = find_vtx(mdl_path)
    if not vvd_path or not vtx_path:
        raise ValueError("fichiers .vvd/.vtx manquants à côté du .mdl")
    verts = read_vvd(vvd_path.read_bytes())
    parts = hdr.bodyparts()
    vtx = _open_vtx(vtx_path.read_bytes(), parts)

    textures = hdr.textures()
    skin = hdr.skin0() if hdr.skinref_count else list(range(len(textures)))
    pos = verts["pos"]
    nrm = verts["normal"]
    uv = verts["uv"]

    model = Model(name=name, source=source, bone_count=max(hdr.bone_count, 1))
    for b, models in enumerate(parts):
        if not models:
            continue
        if len(models) > 1:
            model.warnings.append(f"bodygroup {b} : seule la 1re option est exportée ({len(models)} options)")
        _mname, vstart, _nv, meshes = models[0]
        for k, (material, voffset, _mnv) in enumerate(meshes):
            ids = vtx.mesh_indices(b, 0, k)
            if not len(ids):
                continue
            ids = ids.astype(np.int64) + vstart + voffset
            if ids.max() >= len(verts):
                model.warnings.append(f"mesh {b}/{k} ignoré : indices de sommets invalides")
                continue
            tex_idx = skin[material] if 0 <= material < len(skin) else material
            mat = textures[tex_idx] if 0 <= tex_idx < len(textures) else f"missing_{material}"
            sm = compact(mat, pos, nrm, uv, ids)
            fix_winding(sm)
            model.submeshes.append(sm)
    if hdr.skinfamily_count > 1:
        model.warnings.append(f"{hdr.skinfamily_count} skins : seul le skin 0 est exporté")
    return model, hdr.cdmaterials()
