import json
import tomllib
from pathlib import Path

import numpy as np
from PIL import Image

from gmod2nanos import gma
from gmod2nanos.converter import Options, convert

from . import builders


def _write(root: Path, files: dict[str, bytes]) -> None:
    for name, data in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def test_convert_addon(tmp_path: Path):
    _write(tmp_path / "addon", builders.addon_files())
    rep = convert([tmp_path / "addon"], tmp_path / "out", Options(title="Ma Chaise", author="moi"), lambda m: None)
    out = tmp_path / "out"
    assert rep.pack_name == "ma-chaise"
    assert rep.errors == []
    assert [m["key"] for m in rep.static_meshes] == ["SM_chair"]

    toml = tomllib.loads((out / "2-asset-pack/ma-chaise/Assets.toml").read_text())
    assert toml["meta"]["title"] == "Ma Chaise"
    assert toml["unreal"]["unreal_folders"] == ["MaChaise"]
    assert toml["assets"]["static_meshes"] == {"SM_chair": "MaChaise/Models/SM_chair"}

    gltf_path = out / "1-unreal/MaChaise/Models/SM_chair.gltf"
    doc = json.loads(gltf_path.read_text())
    assert doc["materials"][0]["name"] == "M_chair_wood"
    uri = doc["images"][0]["uri"]
    png = (gltf_path.parent / uri).resolve()
    assert png.exists()
    assert Image.open(png).convert("RGB").getpixel((4, 4))[0] > 150

    # vérifie l'échelle et l'axe : le carré Source (Z haut) doit être à plat (Y glTF = 0), 64 unités = 1,6256 m
    acc = doc["accessors"][doc["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
    assert acc["min"][1] == acc["max"][1] == 0
    assert abs(acc["max"][0] - 64 * 0.0254) < 1e-5
    bin_ = (gltf_path.with_suffix(".bin")).read_bytes()
    assert len(bin_) == doc["buffers"][0]["byteLength"]

    assert rep.sounds and rep.sounds[0]["uri"] == "assets://ma-chaise/Sounds/test/beep.ogg"
    assert (out / "2-asset-pack/ma-chaise/Sounds/test/beep.ogg").read_bytes()[:4] == b"OggS"
    assert rep.skipped.get(".lua") == 1

    lua = (out / "3-package-test/ma-chaise-test/Server/Index.lua").read_text()
    assert '"ma-chaise::SM_chair"' in lua
    pkg = tomllib.loads((out / "3-package-test/ma-chaise-test/Package.toml").read_text())
    assert pkg["script"]["assets_requirements"] == ["ma-chaise"]
    assert (out / "1-unreal/import_gmod_pack.py").exists()
    assert json.loads((out / "1-unreal/import_config.json").read_text())["static_meshes"] == ["SM_chair"]


def test_convert_smd_with_qc(tmp_path: Path):
    files = {
        "src/chair.qc": b'$modelname "props/fauteuil.mdl"\n$cdmaterials "models/test/"\n$body body "chair_ref.smd"\n'
        b'$sequence idle "idle.smd"\n$collisionmodel "chair_phys.smd" {}\n',
        "src/chair_ref.smd": builders.build_smd(),
        "src/chair_phys.smd": builders.build_smd(),
        "src/idle.smd": b"version 1\nnodes\n0 \"root\" -1\nend\nskeleton\ntime 0\n0 0 0 0 0 0 0\nend\n",
        "src/loose.smd": builders.build_smd("other"),
        "materials/models/test/chair_wood.vmt": builders.build_vmt("models/test/chair_wood_diffuse"),
        "materials/models/test/chair_wood_diffuse.vtf": builders.build_vtf(),
    }
    _write(tmp_path / "in", files)
    rep = convert([tmp_path / "in"], tmp_path / "out", Options(title="smd"), lambda m: None)
    keys = sorted(m["key"] for m in rep.static_meshes)
    assert keys == ["SM_fauteuil", "SM_loose"]
    fauteuil = next(m for m in rep.static_meshes if m["key"] == "SM_fauteuil")
    assert fauteuil["materials"] == ["M_chair_wood"]
    mat = next(m for m in rep.materials if m["name"] == "M_chair_wood")
    assert mat["found"] and mat["basecolor"]


def test_name_collisions(tmp_path: Path):
    files = builders.addon_files()
    for ext in ("mdl", "vvd", "dx90.vtx"):
        files[f"models/other/chair.{ext}"] = files[f"models/test/chair.{ext}"]
    _write(tmp_path / "addon", files)
    rep = convert([tmp_path / "addon"], tmp_path / "out", Options(title="x"), lambda m: None)
    assert sorted(m["key"] for m in rep.static_meshes) == ["SM_chair", "SM_chair_2"]


def test_gma_upload_through_api(tmp_path: Path, monkeypatch):
    import time

    from fastapi.testclient import TestClient

    from gmod2nanos import jobs
    from gmod2nanos.app import app

    monkeypatch.setattr(jobs, "DATA_DIR", tmp_path / "data")
    gma.write(tmp_path / "chair.gma", builders.addon_files(), name="Chair addon")
    client = TestClient(app)
    assert client.get("/").status_code == 200
    with open(tmp_path / "chair.gma", "rb") as f:
        r = client.post("/api/jobs/upload", files={"file": ("chair.gma", f)}, data={"author": "moi"})
    assert r.status_code == 200, r.text
    job_id = r.json()["id"]
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert job["status"] == "done", job
    assert job["summary"]["pack_name"] == "chair"
    z = client.get(f"/api/jobs/{job_id}/download")
    assert z.status_code == 200 and z.content[:2] == b"PK"

    r = client.post("/api/jobs/upload", files={"file": ("x.exe", b"MZ")})
    assert r.status_code == 400
    assert client.post("/api/jobs", json={"workshop": "pas un lien"}).status_code == 400
