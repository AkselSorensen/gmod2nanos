import json
import math
from pathlib import Path

from gmod2nanos import retarget as rt
from gmod2nanos.converter import Options, convert

from . import builders


def _convert_character(tmp_path: Path):
    src = tmp_path / "in"
    src.mkdir()
    (src / "eric.smd").write_bytes(builders.build_character_smd())
    rep = convert([src], tmp_path / "out", Options(title="Eric"), lambda m: None)
    return rep, tmp_path / "out"


def test_character_is_detected_and_exported_skinned(tmp_path: Path):
    rep, out = _convert_character(tmp_path)
    assert [m["key"] for m in rep.skeletal_meshes] == ["SK_eric"]
    assert rep.static_meshes == []
    gltf = json.loads((out / "1-unreal/Eric/Characters/SK_eric_source.gltf").read_text())
    assert len(gltf["skins"][0]["joints"]) == len(builders.valve_biped_joints())
    assert "JOINTS_0" in gltf["meshes"][0]["primitives"][0]["attributes"]
    toml = (out / "2-asset-pack/eric/Assets.toml").read_text()
    assert 'SK_eric = "Eric/Characters/SK_eric"' in toml
    cfg = json.loads((out / "1-unreal/import_config.json").read_text())
    assert cfg["characters"] == [{"name": "SK_eric", "source": "Characters/SK_eric_source.gltf"}]
    assert (out / "1-unreal/gmod2nanos_retarget.py").exists()
    assert '"eric::SK_eric"' in (out / "3-package-test/eric-test/Server/Index.lua").read_text()


def test_retarget_matches_mannequin(tmp_path: Path):
    _, out = _convert_character(tmp_path)
    ref = builders.mannequin_reference()
    src = out / "1-unreal/Eric/Characters/SK_eric_source.gltf"
    dst = str(out / "1-unreal/Eric/Characters/SK_eric.gltf")
    names = rt.retarget_file(str(src), ref, dst, "SK_eric")
    assert set(names) == set(ref)

    doc, bones, prims = rt.load_source(dst)
    world = {b["name"]: b["world"] for b in bones}
    mw = {n: rt.from_qt(d["q"], d["t"]) for n, d in ref.items()}

    # 1. chaque os a exactement l'orientation de celui du Mannequin
    for n in ref:
        for i in range(3):
            for j in range(3):
                assert abs(world[n][i][j] - mw[n][i][j]) < 1e-4, n

    # 2. hiérarchie du Mannequin
    by_name = {b["name"]: b for b in bones}
    for n, d in ref.items():
        parent = by_name[n]["parent"]
        assert (bones[parent]["name"] if parent >= 0 else None) == d["parent"], n

    # 3. les membres pointent comme ceux du Mannequin (bras en A-pose, gauche = +X)
    def direction(m, a, b):
        v = rt.sub(rt.trans(m[b]), rt.trans(m[a]))
        return rt.norm(v)

    for a, b in rt.POSE_CHAIN:
        assert rt.dot(direction(world, a, b), direction(mw, a, b)) > 0.999, (a, b)
    assert rt.trans(world["thigh_l"])[0] > 0 and rt.trans(world["thigh_r"])[0] < 0

    # 4. proportions du modèle GMod conservées (unités Source -> cm)
    assert abs(rt.trans(world["head"])[2] - 65 * 2.54) < 0.5
    upper = math.dist(rt.trans(world["upperarm_l"]), rt.trans(world["lowerarm_l"]))
    assert abs(upper - 11 * 2.54) < 0.5

    # 5. le maillage du bras a suivi l'os : ses sommets sont sur le segment épaule-coude
    a, b = rt.trans(world["upperarm_l"]), rt.trans(world["lowerarm_l"])
    up_idx = names.index("upperarm_l")
    arm_pts = [p for prim in prims for p, j, w in zip(prim["positions"], prim["joints"], prim["weights"])
               if j[0] == up_idx and w[0] > 0.99]
    assert arm_pts
    axis = rt.norm(rt.sub(b, a))
    for p in arm_pts:
        d = rt.sub(p, a)
        off = rt.sub(d, rt.scale(axis, rt.dot(d, axis)))
        assert math.sqrt(rt.dot(off, off)) < 1.5  # le triangle test fait 0,3 unité Source

    # 6. les os inconnus (mèche de cheveux) sont rattachés à la tête
    head_idx = names.index("head")
    assert any(j[0] == head_idx for prim in prims for j in prim["joints"])
