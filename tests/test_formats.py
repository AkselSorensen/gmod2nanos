import json
from pathlib import Path

import numpy as np
import pytest

from gmod2nanos import gma
from gmod2nanos.mdl import load_mdl
from gmod2nanos.smd import parse_qc, parse_smd
from gmod2nanos.util import safe_join

from . import builders


def _check_quad(model):
    assert model.triangle_count == 2
    sm = model.submeshes[0]
    tri = sm.indices.reshape(-1, 3)
    p = sm.positions
    n = np.cross(p[tri[:, 1]] - p[tri[:, 0]], p[tri[:, 2]] - p[tri[:, 0]])
    assert (n[:, 2] > 0).all(), "les triangles doivent être orientés vers leur normale (+Z)"


@pytest.mark.parametrize("csgo", [False, True])
def test_mdl(tmp_path: Path, csgo):
    (tmp_path / "chair.mdl").write_bytes(builders.build_mdl())
    (tmp_path / "chair.vvd").write_bytes(builders.build_vvd())
    (tmp_path / "chair.dx90.vtx").write_bytes(builders.build_vtx(csgo=csgo))
    model, cd = load_mdl(tmp_path / "chair.mdl", "SM_chair", "models/test/chair.mdl")
    assert cd == ["models/test/"]
    assert model.submeshes[0].material == "chair_wood"
    assert len(model.submeshes[0].positions) == 4
    _check_quad(model)


def test_smd():
    model = parse_smd(builders.build_smd().decode(), "SM_q", "q.smd")
    assert model.submeshes[0].material == "chair_wood"
    assert len(model.submeshes[0].positions) == 4  # sommets dupliqués fusionnés
    _check_quad(model)
    uv = model.submeshes[0].uvs
    assert uv.min() == 0 and uv.max() == 1


def test_qc(tmp_path: Path):
    qc = tmp_path / "chair.qc"
    qc.write_text(
        '$modelname "props/chair.mdl"\n$cdmaterials "models\\test\\"\n'
        '$bodygroup "body" {\n studio "chair_ref.smd"\n studio "chair_alt.smd"\n blank\n}\n'
        '$sequence "idle" "chair_anims/idle.smd"\n$collisionmodel "chair_phys.smd" { $mass 10 }\n'
    )
    info = parse_qc(qc)
    assert info.modelname == "props/chair.mdl"
    assert info.cdmaterials == ["models\\test\\"]
    assert info.references == ["chair_ref.smd"]
    assert {"chair_alt.smd", "chair_anims/idle.smd", "chair_phys.smd"} <= info.other_smds


def test_gma_roundtrip_and_lzma(tmp_path: Path):
    import lzma

    files = {"models/a.mdl": b"abc", "../evil.txt": b"x", "sound/b.wav": b"0123"}
    gma.write(tmp_path / "a.gma", files, name="Test")
    info = gma.extract(tmp_path / "a.gma", tmp_path / "out")
    assert info.name == "Test" and len(info.entries) == 3
    assert (tmp_path / "out/models/a.mdl").read_bytes() == b"abc"
    assert not (tmp_path / "evil.txt").exists()

    comp = lzma.compress((tmp_path / "a.gma").read_bytes(), format=lzma.FORMAT_ALONE)
    (tmp_path / "b.download").write_bytes(comp)
    info = gma.extract(tmp_path / "b.download", tmp_path / "out2")
    assert (tmp_path / "out2/sound/b.wav").read_bytes() == b"0123"


def test_safe_join(tmp_path: Path):
    assert safe_join(tmp_path, "a/b.txt") == tmp_path / "a" / "b.txt"
    assert safe_join(tmp_path, "a\\b.txt") == tmp_path / "a" / "b.txt"
    for bad in ("../x", "a/../../x", "C:/x", ""):
        assert safe_join(tmp_path, bad) is None


def test_workshop_resolve_reuses_client(monkeypatch):
    import httpx

    from gmod2nanos import workshop

    def handler(req: httpx.Request) -> httpx.Response:
        if "GetCollectionDetails" in str(req.url):
            return httpx.Response(200, json={"response": {"collectiondetails": [{"result": 9}]}})
        return httpx.Response(200, json={"response": {"publishedfiledetails": [
            {"result": 1, "publishedfileid": "42", "title": "Chaise", "file_url": "http://x/f", "file_size": 10, "consumer_app_id": 4000}
        ]}})

    monkeypatch.setattr(workshop, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    root, items = workshop.resolve("https://steamcommunity.com/sharedfiles/filedetails/?id=42")
    assert root.title == "Chaise" and [i.id for i in items] == ["42"]


def test_steamcmd_auto_install(tmp_path, monkeypatch):
    import io
    import sys
    import tarfile
    import zipfile

    import httpx

    from gmod2nanos import workshop

    monkeypatch.delenv("STEAMCMD", raising=False)
    monkeypatch.setattr(workshop.shutil, "which", lambda name: None)
    monkeypatch.setattr(workshop, "TOOLS_DIR", tmp_path)
    buf = io.BytesIO()
    if sys.platform.startswith("win"):
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("steamcmd.exe", b"MZ")
    else:
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            info = tarfile.TarInfo("steamcmd.sh")
            info.size = 2
            t.addfile(info, io.BytesIO(b"#!"))
    payload = buf.getvalue()
    calls = []

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(200, content=payload)

    monkeypatch.setattr(workshop, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    exe = workshop.find_or_install_steamcmd()
    assert exe.startswith(str(tmp_path)) and (tmp_path / "steamcmd").exists()
    assert workshop.find_or_install_steamcmd() == exe and len(calls) == 1


@pytest.mark.parametrize("fmt", ["DXT1", "DXT5", "RGBA8888", "BGR888", "BGRA8888"])
def test_vtf_fallback_matches_srctools(tmp_path: Path, fmt):
    import io

    from PIL import Image
    from srctools.vtf import VTF, ImageFormats

    from gmod2nanos.vtf import read_vtf_fallback

    rng = np.random.default_rng(1)
    src = Image.fromarray(rng.integers(0, 255, (32, 64, 4), dtype=np.uint8), "RGBA")
    vtf = VTF(64, 32, fmt=getattr(ImageFormats, fmt))
    vtf.get().copy_from(src.tobytes(), ImageFormats.RGBA8888)
    vtf.compute_mipmaps()
    buf = io.BytesIO()
    vtf.save(buf)
    path = tmp_path / "t.vtf"
    path.write_bytes(buf.getvalue())
    with open(path, "rb") as f:
        ref = VTF.read(f)
        ref.load()
    expected = np.asarray(ref.get().to_PIL().convert("RGBA"), dtype=np.int16)
    got = np.asarray(read_vtf_fallback(path).convert("RGBA"), dtype=np.int16)
    if fmt == "BGR888":
        expected, got = expected[..., :3], got[..., :3]
    assert got.shape == expected.shape
    assert np.abs(got - expected).max() <= 8  # décodeurs DXT légèrement différents


def test_vtf_unknown_format_is_guessed(tmp_path: Path):
    import io
    import struct

    from srctools.vtf import VTF, ImageFormats

    from gmod2nanos.vtf import read_vtf

    vtf = VTF(16, 16, fmt=ImageFormats.DXT5)
    vtf.get().copy_from(bytes([10, 200, 30, 255]) * 256, ImageFormats.RGBA8888)
    buf = io.BytesIO()
    vtf.save(buf)
    data = bytearray(buf.getvalue())
    struct.pack_into("<i", data, 52, 33)  # format inconnu de srctools
    (tmp_path / "x.vtf").write_bytes(data)
    img = read_vtf(tmp_path / "x.vtf")
    assert img.size == (16, 16)


def test_workshop_download_uses_cache(tmp_path, monkeypatch):
    import httpx

    from gmod2nanos import gma as gma_mod
    from gmod2nanos import workshop

    gma_mod.write(tmp_path / "a.gma", {"sound/a.wav": b"x"})
    payload = (tmp_path / "a.gma").read_bytes()
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, content=payload)

    monkeypatch.setattr(workshop, "TOOLS_DIR", tmp_path / "tools")
    monkeypatch.setattr(workshop, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    item = workshop.WorkshopItem(id="7", title="t", file_url="http://x/7", file_size=len(payload), time_updated=5)
    steps = []
    first = workshop.download(item, tmp_path / "j1", 10**9, progress=steps.append)
    second = workshop.download(item, tmp_path / "j2", 10**9)
    assert first == second and first.read_bytes() == payload
    assert len(calls) == 1 and steps and "Mo" in steps[-1]
    item.time_updated = 6  # addon mis à jour sur le Workshop : on retélécharge
    workshop.download(item, tmp_path / "j3", 10**9)
    assert len(calls) == 2


@pytest.mark.skipif(__import__("sys").platform.startswith("win"), reason="faux SteamCMD en shell")
def test_steamcmd_download_with_progress(tmp_path, monkeypatch):
    from gmod2nanos import gma as gma_mod
    from gmod2nanos import workshop

    gma_mod.write(tmp_path / "src.gma", {"sound/a.wav": b"x" * 1000})
    fake = tmp_path / "steamcmd.sh"
    # imite SteamCMD : écrit l'addon dans <force_install_dir>/steamapps/workshop/content/4000/<id>/
    fake.write_text(
        '#!/bin/sh\nDIR="$2"\nOUT="$DIR/steamapps/workshop/content/4000/$7"\n'
        f'mkdir -p "$OUT"\nsleep 1.2\ncp "{tmp_path / "src.gma"}" "$OUT/addon.gma"\n'
    )
    fake.chmod(0o755)
    monkeypatch.setenv("STEAMCMD", str(fake))
    monkeypatch.setattr(workshop, "TOOLS_DIR", tmp_path / "tools")
    item = workshop.WorkshopItem(id="99", title="t", file_size=1000, time_updated=1)
    steps = []
    path = workshop.download(item, tmp_path / "job", 10**9, progress=steps.append)
    assert path.read_bytes()[:4] == b"GMAD"
    assert steps  # la progression a été signalée pendant le téléchargement
    assert workshop.download(item, tmp_path / "job2", 10**9) == path
