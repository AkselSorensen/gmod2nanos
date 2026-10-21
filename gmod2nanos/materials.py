"""Matériaux Source (.vmt) et textures (.vtf -> .png)."""
from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import Executor, Future
from pathlib import Path

from srctools.vmt import Material

from .util import SourceFS, asset_name
from .vtf import read_vtf


@dataclass
class MaterialInfo:
    path: str  # chemin Source sans extension, ex. "models/props/chair"
    name: str  # nom d'asset Unreal, ex. "M_chair"
    shader: str = ""
    basetexture: str | None = None  # chemin relatif du .png généré (dans Textures/)
    normalmap: str | None = None
    translucent: bool = False
    alphatest: bool = False
    alpha_cutoff: float = 0.5
    double_sided: bool = False
    found: bool = False


def _truthy(v: str | None) -> bool:
    try:
        return v is not None and float(v) != 0
    except ValueError:
        return False


def _load_vmt(fs: SourceFS, path: str, depth: int = 0) -> Material | None:
    p = fs.find(path if path.lower().endswith(".vmt") else path + ".vmt")
    if p is None:
        return None
    with open(p, encoding="utf-8", errors="replace") as f:
        mat = Material.parse(f, str(p))
    if mat.shader.lower() == "patch" and depth < 4:
        base = _load_vmt(fs, mat.get("include", ""), depth + 1)
        if base is None:
            return mat
        for block in mat.blocks:
            if block.name.lower() in ("insert", "replace"):
                for kv in block:
                    if kv.has_children():
                        continue
                    base[kv.name] = kv.value
        return base
    return mat


class TextureCache:
    """Convertit chaque .vtf une seule fois en .png dans out_dir, en parallèle.

    convert() renvoie tout de suite le nom du .png ; wait() attend la fin et retire les échecs.
    """

    def __init__(self, fs: SourceFS, out_dir: Path, log, pool: Executor):
        self.fs = fs
        self.out_dir = out_dir
        self.log = log
        self.pool = pool
        self.done: dict[str, str | None] = {}
        self.pending: dict[str, Future] = {}

    def convert(self, tex_path: str) -> str | None:
        key = SourceFS.norm(tex_path)
        if key.endswith(".vtf"):
            key = key[:-4]
        if key in self.done:
            return self.done[key]
        src = self.fs.find(f"materials/{key}.vtf")
        if src is None:
            self.log(f"texture introuvable : materials/{key}.vtf")
            self.done[key] = None
            return None
        rel = f"T_{asset_name(key.replace('/', '_'))}.png"
        self.done[key] = rel
        self.pending[key] = self.pool.submit(self._work, src, self.out_dir / rel)
        return rel

    @staticmethod
    def _work(src: Path, dst: Path) -> None:
        read_vtf(src).save(dst, compress_level=1)

    def wait(self) -> set[str]:
        """Attend toutes les conversions ; renvoie les .png qui ont échoué."""
        failed = set()
        for key, fut in self.pending.items():
            try:
                fut.result()
            except Exception as e:  # format exotique, fichier corrompu...
                self.log(f"échec de conversion de {key}.vtf : {e}")
                failed.add(self.done[key])
                self.done[key] = None
        self.pending.clear()
        return failed


class MaterialResolver:
    def __init__(self, fs: SourceFS, textures: TextureCache, log):
        self.fs = fs
        self.textures = textures
        self.log = log
        self.cache: dict[str, MaterialInfo] = {}
        self.used_names: set[str] = set()
        self._by_base: dict[str, str] | None = None

    def drop_textures(self, failed: set[str]) -> None:
        for m in self.cache.values():
            if m.basetexture in failed:
                m.basetexture = None
            if m.normalmap in failed:
                m.normalmap = None

    def _locate(self, name: str, search_dirs: list[str]) -> str:
        name = SourceFS.norm(name)
        candidates = [SourceFS.norm(f"{d}/{name}") for d in search_dirs] + [name]
        for c in candidates:
            if self.fs.find(f"materials/{c}.vmt"):
                return c
        # dernier recours : un .vmt du même nom de fichier n'importe où dans l'addon
        base = name.rsplit("/", 1)[-1]
        if self._by_base is None:
            self._by_base = {}
            for k in self.fs.index:
                if k.startswith("materials/") and k.endswith(".vmt"):
                    self._by_base.setdefault(k.rsplit("/", 1)[-1][:-4], k[len("materials/") : -4])
        if base in self._by_base:
            return self._by_base[base]
        return candidates[0] if candidates else name

    def resolve(self, name: str, search_dirs: list[str]) -> MaterialInfo:
        path = self._locate(name, search_dirs)
        if path in self.cache:
            return self.cache[path]
        uname = "M_" + asset_name(path.rsplit("/", 1)[-1])
        i = 2
        while uname in self.used_names:
            uname = f"M_{asset_name(path.rsplit('/', 1)[-1])}_{i}"
            i += 1
        self.used_names.add(uname)
        info = MaterialInfo(path=path, name=uname)
        mat = _load_vmt(self.fs, f"materials/{path}")
        if mat is None:
            self.log(f"matériau introuvable dans l'addon : materials/{path}.vmt (probablement un contenu de base du jeu)")
        else:
            info.found = True
            info.shader = mat.shader
            info.translucent = _truthy(mat.get("$translucent"))
            info.alphatest = _truthy(mat.get("$alphatest"))
            info.double_sided = _truthy(mat.get("$nocull"))
            try:
                info.alpha_cutoff = float(mat.get("$alphatestreference", "0.5"))
            except ValueError:
                pass
            if (bt := mat.get("$basetexture")):
                info.basetexture = self.textures.convert(bt)
            if (bm := mat.get("$bumpmap")) or (bm := mat.get("$normalmap")):
                info.normalmap = self.textures.convert(bm)
        self.cache[path] = info
        return info
