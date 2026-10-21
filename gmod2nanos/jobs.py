"""File de conversions exécutées en arrière-plan."""
from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import gma, workshop
from .converter import Options, convert
from .util import safe_join

DATA_DIR = Path(os.environ.get("GMOD2NANOS_DATA", "data")).resolve()
MAX_SIZE = int(os.environ.get("GMOD2NANOS_MAX_BYTES", str(2 * 1024**3)))
JOB_TTL = int(os.environ.get("GMOD2NANOS_JOB_TTL", str(6 * 3600)))
_executor = ThreadPoolExecutor(max_workers=int(os.environ.get("GMOD2NANOS_WORKERS", "2")))


@dataclass
class Job:
    id: str
    status: str = "queued"  # queued | running | done | error
    step: str = "En attente"
    title: str = ""
    preview_url: str = ""
    log: list[str] = field(default_factory=list)
    error: str | None = None
    report: dict | None = None
    created: float = field(default_factory=time.time)

    @property
    def dir(self) -> Path:
        return DATA_DIR / self.id

    @property
    def zip_path(self) -> Path:
        return self.dir / "result.zip"

    def add_log(self, msg: str) -> None:
        self.log.append(msg)
        if len(self.log) > 5000:
            del self.log[:1000]

    def public(self, log_from: int = 0) -> dict:
        r = self.report or {}
        return {
            "id": self.id,
            "status": self.status,
            "step": self.step,
            "title": self.title,
            "preview_url": self.preview_url,
            "error": self.error,
            "log": self.log[log_from:],
            "log_size": len(self.log),
            "summary": None if not self.report else {
                "pack_name": r["pack_name"],
                "unreal_folder": r["unreal_folder"],
                "static_meshes": r["static_meshes"],
                "sounds": len(r["sounds"]),
                "textures": len(r["textures"]),
                "warnings": len(r["warnings"]),
                "errors": r["errors"],
                "skipped": r["skipped"],
            },
        }


JOBS: dict[str, Job] = {}
_lock = threading.Lock()


def _new_job() -> Job:
    _cleanup()
    job = Job(id=uuid.uuid4().hex[:12])
    job.dir.mkdir(parents=True, exist_ok=True)
    with _lock:
        JOBS[job.id] = job
    return job


def _cleanup() -> None:
    now = time.time()
    with _lock:
        for jid, job in list(JOBS.items()):
            if now - job.created > JOB_TTL and job.status in ("done", "error"):
                shutil.rmtree(job.dir, ignore_errors=True)
                del JOBS[jid]


def _run(job: Job, prepare, opts: Options) -> None:
    job.status = "running"
    try:
        roots = prepare(job)
        job.step = "Conversion des assets"
        out = job.dir / "out"
        rep = convert(roots, out, opts, job.add_log)
        job.step = "Création de l'archive"
        with zipfile.ZipFile(job.zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for p in sorted(out.rglob("*")):
                if p.is_file():
                    z.write(p, p.relative_to(out).as_posix())
        job.report = rep.__dict__
        job.status = "done"
        job.step = "Terminé"
        for d in ("work", "out"):
            shutil.rmtree(job.dir / d, ignore_errors=True)
    except Exception as e:
        job.status = "error"
        job.error = str(e) or e.__class__.__name__
        job.step = "Erreur"
        job.add_log("✘ " + job.error)


def _extract_any(src: Path, dest: Path, job: Job) -> Path:
    if src.is_dir():
        return src
    with open(src, "rb") as f:
        head = f.read(4)
    if head == b"PK\x03\x04":
        return _extract_zip(src, dest)
    if src.suffix.lower() in (".smd", ".qc", ".vmt", ".vtf", ".wav", ".mp3", ".ogg"):
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest / src.name)
        return dest
    info = gma.extract(src, dest, MAX_SIZE)
    job.add_log(f"addon « {info.name} » : {len(info.entries)} fichiers")
    return dest


def _extract_zip(src: Path, dest: Path) -> Path:
    with zipfile.ZipFile(src) as z:
        total = sum(i.file_size for i in z.infolist())
        if total > MAX_SIZE:
            raise ValueError("archive trop volumineuse une fois décompressée")
        for info in z.infolist():
            if info.is_dir():
                continue
            target = safe_join(dest, info.filename)
            if target is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as s, open(target, "wb") as d:
                shutil.copyfileobj(s, d)
    # un zip contenant un unique dossier racine (ex. "mon_addon/models/...") : on descend dedans
    entries = list(dest.iterdir()) if dest.exists() else []
    if len(entries) == 1 and entries[0].is_dir() and entries[0].name.lower() not in ("models", "materials", "sound"):
        return entries[0]
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def submit_workshop(text: str, opts: Options) -> Job:
    item_id = workshop.parse_id(text)
    job = _new_job()
    job.title = f"Workshop {item_id}"
    opts.workshop_id = item_id

    def prepare(job: Job) -> list[Path]:
        job.step = "Lecture des infos du Workshop"
        root, items = workshop.resolve(item_id)
        job.title = root.title or job.title
        job.preview_url = root.preview_url
        if not opts.title or opts.title == Options.title:
            opts.title = root.title or opts.title
        if len(items) > 1:
            job.add_log(f"collection : {len(items)} addons")
        roots = []
        for n, it in enumerate(items, 1):
            job.step = f"Téléchargement {n}/{len(items)} : {it.title}"
            job.add_log(f"téléchargement de « {it.title} » ({it.file_size // 1024} Ko)")
            raw = workshop.download(it, job.dir / "work" / "dl", MAX_SIZE)
            job.step = f"Extraction {n}/{len(items)}"
            roots.append(_extract_any(raw, job.dir / "work" / "addons" / it.id, job))
        return roots

    _executor.submit(_run, job, prepare, opts)
    return job


def submit_upload(filename: str, save_to, opts: Options) -> Job:
    """save_to(path) écrit le fichier envoyé à l'emplacement donné."""
    job = _new_job()
    job.title = filename
    name = Path(filename).name or "upload.gma"
    up = job.dir / "work" / "upload" / name
    up.parent.mkdir(parents=True, exist_ok=True)
    save_to(up)
    if opts.title == Options.title:
        opts.title = Path(name).stem

    def prepare(job: Job) -> list[Path]:
        job.step = "Extraction"
        return [_extract_any(up, job.dir / "work" / "addon", job)]

    _executor.submit(_run, job, prepare, opts)
    return job
