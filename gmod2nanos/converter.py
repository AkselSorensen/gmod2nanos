"""Conversion d'un addon Garry's Mod extrait en projet prêt pour nanos world."""
from __future__ import annotations

import json
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import __version__
from .audio import SOUND_EXTS, to_ogg
from .gltf import DEFAULT_SCALE, write_gltf
from .materials import MaterialResolver, TextureCache
from .mdl import load_mdl
from .mesh import Model
from .smd import has_triangles, parse_qc, parse_smd
from .templates import render_assets_toml, render_lua_client, render_lua_server, render_package_toml, render_readme
from .util import SourceFS, asset_name, pack_slug

WORKERS = int(os.environ.get("GMOD2NANOS_THREADS", str(min(8, (os.cpu_count() or 2)))))
UNREAL_IMPORT_SCRIPT = Path(__file__).parent / "templates" / "import_gmod_pack.py"


@dataclass
class Options:
    title: str = "GMod Addon"
    author: str = ""
    pack_name: str = ""  # dossier de l'Asset Pack nanos (minuscules, '-')
    unreal_folder: str = ""  # dossier racine dans Content/ d'Unreal
    scale: float = DEFAULT_SCALE
    workshop_id: str | None = None

    def finalize(self) -> None:
        self.pack_name = pack_slug(self.pack_name or self.title)
        self.unreal_folder = asset_name(self.unreal_folder or self.pack_name.title().replace("-", ""))


@dataclass
class Report:
    pack_name: str = ""
    unreal_folder: str = ""
    static_meshes: list[dict] = field(default_factory=list)
    sounds: list[dict] = field(default_factory=list)
    textures: list[str] = field(default_factory=list)
    materials: list[dict] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def convert(roots: list[Path], out: Path, opts: Options, log: Callable[[str], None] = print) -> Report:
    opts.finalize()
    fs = SourceFS(roots)
    rep = Report(pack_name=opts.pack_name, unreal_folder=opts.unreal_folder)
    log(f"{len(fs.index)} fichiers trouvés dans l'addon")

    ue_root = out / "1-unreal" / opts.unreal_folder
    models_dir = ue_root / "Models"
    tex_dir = ue_root / "Textures"
    pack_dir = out / "2-asset-pack" / opts.pack_name
    sounds_dir = pack_dir / "Sounds"
    for d in (models_dir, tex_dir, sounds_dir):
        d.mkdir(parents=True, exist_ok=True)

    warn = lambda m: (rep.warnings.append(m), log("⚠ " + m))  # noqa: E731
    pool = ThreadPoolExecutor(max_workers=WORKERS)
    try:
        return _convert(fs, out, opts, rep, log, warn, pool, models_dir, tex_dir, pack_dir, sounds_dir)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def _convert(fs, out, opts, rep, log, warn, pool, models_dir, tex_dir, pack_dir, sounds_dir) -> Report:
    textures = TextureCache(fs, tex_dir, warn, pool)
    materials = MaterialResolver(fs, textures, warn)
    used_names: set[str] = set()
    to_write: list[tuple[Model, list]] = []

    def unique(base: str) -> str:
        name, i = "SM_" + asset_name(base), 2
        while name.lower() in used_names:
            name = f"SM_{asset_name(base)}_{i}"
            i += 1
        used_names.add(name.lower())
        return name

    def export(model: Model, search_dirs: list[str]) -> None:
        if not model.submeshes:
            warn(f"{model.source} : aucun triangle, ignoré")
            return
        mats = [materials.resolve(sm.material, search_dirs) for sm in model.submeshes]
        to_write.append((model, mats))  # écrit après les textures, pour ne référencer que celles réussies
        for w in model.warnings:
            warn(f"{model.source} : {w}")
        if model.is_skeletal:
            warn(f"{model.source} : modèle avec {model.bone_count} os, exporté en mesh statique (pose de référence)")
        rep.static_meshes.append(
            {
                "key": model.name,
                "path": f"{opts.unreal_folder}/Models/{model.name}",
                "source": model.source,
                "triangles": model.triangle_count,
                "bones": model.bone_count,
                "materials": sorted({m.name for m in mats}),
            }
        )
        log(f"✔ {model.source} → {model.name} ({model.triangle_count} triangles)")

    # --- Modèles compilés (.mdl) ---
    for key in fs.glob_ext(".mdl"):
        src = fs.find(key)
        name = unique(Path(key).stem)
        try:
            model, cdmaterials = load_mdl(src, name, key)
            export(model, cdmaterials)
        except Exception as e:
            used_names.discard(name.lower())
            rep.errors.append(f"{key} : {e}")
            log(f"✘ {key} : {e}")

    # --- Sources décompilées (.qc + .smd) ---
    smd_keys = set(fs.glob_ext(".smd"))
    claimed: set[str] = set()
    for qc_key in fs.glob_ext(".qc"):
        try:
            qc = parse_qc(fs.find(qc_key))
        except Exception as e:
            rep.errors.append(f"{qc_key} : {e}")
            continue
        qc_dir = qc_key.rsplit("/", 1)[0] + "/" if "/" in qc_key else ""
        refs = []
        for r in qc.references:
            for cand in (qc_dir + r, qc_dir + r + ".smd", r, r + ".smd"):
                if SourceFS.norm(cand) in smd_keys:
                    refs.append(SourceFS.norm(cand))
                    break
            else:
                warn(f"{qc_key} : fichier de référence introuvable « {r} »")
        for o in qc.other_smds:
            for cand in (qc_dir + o, qc_dir + o + ".smd", o, o + ".smd"):
                claimed.add(SourceFS.norm(cand))
        if not refs:
            continue
        base = Path(qc.modelname).stem if qc.modelname else Path(qc_key).stem
        name = unique(base)
        merged = Model(name=name, source=qc_key)
        for r in refs:
            claimed.add(r)
            try:
                part = parse_smd(fs.find(r).read_text(encoding="utf-8", errors="replace"), name, r)
                merged.submeshes += part.submeshes
                merged.bone_count = max(merged.bone_count, part.bone_count)
            except Exception as e:
                rep.errors.append(f"{r} : {e}")
        export(merged, qc.cdmaterials)

    for key in sorted(smd_keys - claimed):
        stem = Path(key).stem.lower()
        if any(t in stem for t in ("_phys", "physics", "_lod", "_anims", "idle", "ragdoll")):
            rep.skipped[".smd (physique/anim/LOD)"] = rep.skipped.get(".smd (physique/anim/LOD)", 0) + 1
            continue
        text = fs.find(key).read_text(encoding="utf-8", errors="replace")
        if not has_triangles(text):
            rep.skipped[".smd (animation)"] = rep.skipped.get(".smd (animation)", 0) + 1
            continue
        name = unique(Path(key).stem)
        try:
            export(parse_smd(text, name, key), [key.rsplit("/", 1)[0]] if "/" in key else [])
        except Exception as e:
            rep.errors.append(f"{key} : {e}")
            log(f"✘ {key} : {e}")

    # --- Matériaux restants (textures utilisables seules) ---
    for key in fs.glob_ext(".vmt"):
        materials.resolve(key[len("materials/") : -4] if key.startswith("materials/") else key[:-4], [])

    # --- Sons (en parallèle avec les textures) ---
    sound_jobs = []
    for key in fs.glob_ext(*SOUND_EXTS):
        if not key.startswith("sound/"):
            continue
        rel = Path(key[len("sound/") :]).with_suffix(".ogg")
        dst = sounds_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        sound_jobs.append((key, rel, pool.submit(to_ogg, fs.find(key), dst)))

    # --- Attente des textures, puis écriture des modèles ---
    if textures.pending:
        log(f"conversion de {len(textures.pending)} textures…")
    materials.drop_textures(textures.wait())
    for model, mats in to_write:
        write_gltf(model, mats, models_dir / f"{model.name}.gltf", "../Textures/", opts.scale)

    for key, rel, fut in sound_jobs:
        try:
            fut.result()
            uri = f"assets://{opts.pack_name}/Sounds/{rel.as_posix()}"
            rep.sounds.append({"source": key, "file": f"Sounds/{rel.as_posix()}", "uri": uri})
        except Exception as e:
            rep.errors.append(f"{key} : {e}")
            log(f"✘ {key} : {e}")
    if rep.sounds:
        log(f"✔ {len(rep.sounds)} sons convertis en .ogg")

    # --- Ce qui n'est pas convertible ---
    handled = (".mdl", ".vvd", ".vtx", ".phy", ".ani", ".vmt", ".vtf", ".smd", ".qc", *SOUND_EXTS)
    for key in fs.index:
        if not key.endswith(handled):
            ext = Path(key).suffix or "(sans extension)"
            rep.skipped[ext] = rep.skipped.get(ext, 0) + 1
    if rep.skipped.get(".lua"):
        warn(f"{rep.skipped['.lua']} scripts .lua ignorés : le Lua GMod n'est pas compatible avec l'API nanos world")
    if rep.skipped.get(".bsp"):
        warn("les maps .bsp ne sont pas converties (à reconstruire dans Unreal)")

    rep.textures = sorted(v for v in textures.done.values() if v)
    rep.materials = [
        {"name": m.name, "source": m.path, "basecolor": m.basetexture, "normal": m.normalmap, "found": m.found}
        for m in materials.cache.values()
    ]

    # --- Fichiers du pack ---
    shutil.copyfile(UNREAL_IMPORT_SCRIPT, out / "1-unreal" / "import_gmod_pack.py")
    (out / "1-unreal" / "import_config.json").write_text(
        json.dumps({"unreal_folder": opts.unreal_folder, "static_meshes": [m["key"] for m in rep.static_meshes]}, indent=2),
        encoding="utf-8",
    )
    (pack_dir / "Assets.toml").write_text(render_assets_toml(opts, rep), encoding="utf-8")
    test_pkg = out / "3-package-test" / f"{opts.pack_name}-test"[:64]
    (test_pkg / "Server").mkdir(parents=True, exist_ok=True)
    (test_pkg / "Client").mkdir(parents=True, exist_ok=True)
    (test_pkg / "Package.toml").write_text(render_package_toml(opts), encoding="utf-8")
    (test_pkg / "Server" / "Index.lua").write_text(render_lua_server(opts, rep), encoding="utf-8")
    (test_pkg / "Client" / "Index.lua").write_text(render_lua_client(opts, rep), encoding="utf-8")
    (out / "LISEZMOI.md").write_text(render_readme(opts, rep, __version__), encoding="utf-8")
    (out / "rapport.json").write_text(json.dumps(rep.__dict__, indent=2, ensure_ascii=False), encoding="utf-8")
    for d in (models_dir, tex_dir, sounds_dir):
        if not any(d.iterdir()):
            d.rmdir()
    return rep
