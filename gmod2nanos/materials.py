"""Matériaux Source (.vmt) et textures (.vtf -> .png)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from srctools.vmt import Material
from srctools.vtf import VTF

from .util import SourceFS, asset_name


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
    """Convertit chaque .vtf une seule fois en .png dans out_dir."""

    def __init__(self, fs: SourceFS, out_dir: Path, log):
        self.fs = fs
        self.out_dir = out_dir
        self.log = log
        self.done: dict[str, str | None] = {}

    def convert(self, tex_path: str) -> str | None:
        key = SourceFS.norm(tex_path)
        if key.endswith(".vtf"):
            key = key[:-4]
        if key in self.done:
            return self.done[key]
        result = None
        src = self.fs.find(f"materials/{key}.vtf")
        if src is None:
            self.log(f"texture introuvable : materials/{key}.vtf")
        else:
            try:
                with open(src, "rb") as f:
                    vtf = VTF.read(f)
                    vtf.load()
                img: Image.Image = vtf.get().to_PIL()
                rel = f"T_{asset_name(key.replace('/', '_'))}.png"
                img.save(self.out_dir / rel, optimize=False)
                result = rel
            except Exception as e:  # format exotique, fichier corrompu...
                self.log(f"échec de conversion de {key}.vtf : {e}")
        self.done[key] = result
        return result


class MaterialResolver:
    def __init__(self, fs: SourceFS, textures: TextureCache, log):
        self.fs = fs
        self.textures = textures
        self.log = log
        self.cache: dict[str, MaterialInfo] = {}
        self.used_names: set[str] = set()

    def _locate(self, name: str, search_dirs: list[str]) -> str:
        name = SourceFS.norm(name)
        candidates = [SourceFS.norm(f"{d}/{name}") for d in search_dirs] + [name]
        for c in candidates:
            if self.fs.find(f"materials/{c}.vmt"):
                return c
        # dernier recours : un .vmt du même nom de fichier n'importe où dans l'addon
        base = name.rsplit("/", 1)[-1]
        for k in self.fs.index:
            if k.startswith("materials/") and k.endswith("/" + base + ".vmt"):
                return k[len("materials/") : -4]
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
