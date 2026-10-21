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
