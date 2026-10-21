from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath


def safe_join(base: Path, rel: str) -> Path | None:
    """Joint un chemin relatif venant d'une archive en refusant toute sortie de base."""
    rel = rel.replace("\\", "/").lstrip("/")
    parts = [p for p in PurePosixPath(rel).parts if p not in ("", ".")]
    if not parts or any(p == ".." or ":" in p for p in parts):
        return None
    target = base.joinpath(*parts)
    try:
        target.resolve().relative_to(base.resolve())
    except ValueError:
        return None
    return target


def asset_name(text: str) -> str:
    """Nom compatible Unreal / clé Assets.toml : [A-Za-z0-9_]."""
    s = re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_")
    return s or "Asset"


def pack_slug(text: str) -> str:
    """Nom de dossier d'Asset Pack nanos : minuscules, chiffres et '-', 64 car. max."""
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s or "gmod-pack")[:64].strip("-")


class SourceFS:
    """Système de fichiers insensible à la casse façon moteur Source, sur un ou plusieurs dossiers."""

    def __init__(self, roots: list[Path]):
        self.index: dict[str, Path] = {}
        for root in roots:
            for dirpath, _, files in os.walk(root):
                for fn in files:
                    p = Path(dirpath) / fn
                    key = p.relative_to(root).as_posix().lower()
                    self.index.setdefault(key, p)

    @staticmethod
    def norm(path: str) -> str:
        return re.sub(r"/+", "/", path.replace("\\", "/")).strip("/").lower()

    def find(self, path: str) -> Path | None:
        return self.index.get(self.norm(path))

    def glob_ext(self, *exts: str) -> list[str]:
        return sorted(k for k in self.index if k.endswith(exts))
