"""API web + interface : uvicorn gmod2nanos.app:app"""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, jobs, workshop
from .converter import Options
from .gltf import DEFAULT_SCALE

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
UPLOAD_EXTS = (".gma", ".bin", ".zip", ".smd", ".qc")

app = FastAPI(title="gmod2nanos", version=__version__)


class WorkshopRequest(BaseModel):
    workshop: str = Field(..., description="Lien ou ID d'un addon / d'une collection du Workshop GMod")
    title: str = ""
    author: str = ""
    pack_name: str = ""
    scale: float = Field(DEFAULT_SCALE, gt=0, le=100)


def _opts(title: str, author: str, pack_name: str, scale: float) -> Options:
    return Options(title=title.strip() or Options.title, author=author.strip(), pack_name=pack_name.strip(), scale=scale)


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__, "ffmpeg": shutil.which("ffmpeg") is not None}


@app.get("/api/workshop/{text:path}")
def workshop_info(text: str):
    try:
        root, items = workshop.resolve(text)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, str(e))
    return {
        "id": root.id,
        "title": root.title,
        "preview_url": root.preview_url,
        "is_collection": len(items) > 1 or items[0].id != root.id,
        "items": [{"id": i.id, "title": i.title, "size": i.file_size, "direct": bool(i.file_url)} for i in items],
    }


@app.post("/api/jobs")
def create_job(req: WorkshopRequest):
    try:
        job = jobs.submit_workshop(req.workshop, _opts(req.title, req.author, req.pack_name, req.scale))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return job.public()


@app.post("/api/jobs/upload")
def create_upload_job(
    file: UploadFile = File(...),
    title: str = Form(""),
    author: str = Form(""),
    pack_name: str = Form(""),
    scale: float = Form(DEFAULT_SCALE),
):
    name = Path(file.filename or "").name
    if not name.lower().endswith(UPLOAD_EXTS):
        raise HTTPException(400, "formats acceptés : .gma, .zip (dossier d'addon ou modèles .smd/.qc), .smd")
    if not 0 < scale <= 100:
        raise HTTPException(400, "échelle invalide")

    def save(dest: Path) -> None:
        size = 0
        with open(dest, "wb") as out:
            while chunk := file.file.read(1 << 20):
                size += len(chunk)
                if size > jobs.MAX_SIZE:
                    raise HTTPException(413, "fichier trop volumineux")
                out.write(chunk)

    job = jobs.submit_upload(name, save, _opts(title, author, pack_name, scale))
    return job.public()


def _job(job_id: str) -> jobs.Job:
    job = jobs.JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "conversion introuvable (expirée ?)")
    return job


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, log_from: int = 0):
    return _job(job_id).public(max(log_from, 0))


@app.get("/api/jobs/{job_id}/download")
def download(job_id: str):
    job = _job(job_id)
    if job.status != "done" or not job.zip_path.exists():
        raise HTTPException(409, "la conversion n'est pas terminée")
    filename = f"{(job.report or {}).get('pack_name', 'gmod-pack')}-nanos.zip"
    return FileResponse(job.zip_path, media_type="application/zip", filename=filename)


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
