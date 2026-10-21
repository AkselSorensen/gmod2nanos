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


NANOS_REF = Path(__file__).parent / "data" / "nanos_mannequin_reference.json"


def test_retarget_on_real_nanos_mannequin(tmp_path: Path):
    """Pose de référence réelle de SKEL_Mannequin (ADK nanos world) : noms avec majuscules, os en plus."""
    _, out = _convert_character(tmp_path)
    ref = json.loads(NANOS_REF.read_text())
    src = out / "1-unreal/Eric/Characters/SK_eric_source.gltf"
    dst = str(out / "1-unreal/Eric/Characters/SK_eric.gltf")
    names = rt.retarget_file(str(src), ref, dst, "SK_eric")

    # les vrais noms de l'ADK sont repris tels quels
    for n in ("Root", "Pelvis", "UpperArm_L", "Hand_R", "Head", "Thigh_L", "Foot_R", "spine_01", "lowerarm_l"):
        assert n in names
    # os non standard sans parent mesuré : écartés (sinon SKEL_Mannequin refuserait le mesh)
    for n in ("weapon_l", "LeftEye", "head_end"):
        assert n not in names

    _, bones, prims = rt.load_source(dst)
    world = {b["name"]: b["world"] for b in bones}
    parent = {b["name"]: (bones[b["parent"]]["name"] if b["parent"] >= 0 else None) for b in bones}
    assert parent["Pelvis"] == "Root" and parent["UpperArm_L"] == "clavicle_l" and parent["Hand_L"] == "lowerarm_l"
    mw = {n: rt.from_qt(d["q"], d["t"]) for n, d in ref.items()}
    for n in names:
        for i in range(3):
            for j in range(3):
                assert abs(world[n][i][j] - rt.orthonormalize(mw[n])[i][j]) < 1e-3, n
    canon = {n.lower(): n for n in names}
    for a, b in rt.POSE_CHAIN:
        da = rt.norm(rt.sub(rt.trans(world[canon[b]]), rt.trans(world[canon[a]])))
        dm = rt.norm(rt.sub(rt.trans(mw[canon[b]]), rt.trans(mw[canon[a]])))
        assert rt.dot(da, dm) > 0.999, (a, b)
    # le personnage regarde vers +Y comme le Mannequin : jambe gauche en +X, orteils devant
    assert rt.trans(world["Thigh_L"])[0] > 0
    assert rt.trans(world["ball_l"])[1] > rt.trans(world["Foot_L"])[1]


def test_find_parent_from_local_poses():
    import math as m

    hier = {"root": None, "hips": "root", "hand": "hips", "weapon": "hand", "hand_end": "hand"}
    local_qt = {
        "root": ([0, 0, 0], [0, 0, 0, 1]),
        "hips": ([0, 0, 90], [0, 0, m.sin(0.3), m.cos(0.3)]),
        "hand": ([40, 5, 20], [m.sin(0.4), 0, 0, m.cos(0.4)]),
        "weapon": ([3, 1, 0], [0, m.sin(0.2), 0, m.cos(0.2)]),
        "hand_end": ([8, 0, 0], [0, 0, 0, 1]),
    }
    worlds = {}
    for n in ("root", "hips", "hand", "weapon", "hand_end"):
        loc = rt.from_qt(local_qt[n][1], local_qt[n][0])
        worlds[n] = loc if hier[n] is None else rt.mul(worlds[hier[n]], loc)
    world = {n: (rt.trans(w), rt.to_quat(w)) for n, w in worlds.items()}
    local = {n: tuple(v) for n, v in local_qt.items()}
    for n, p in hier.items():
        if n == "root":
            continue
        assert rt.find_parent(n, world, local) == (p, True), n


def test_axis_calibration_compensates_importer(tmp_path: Path):
    """Simule un importeur qui convertit les axes autrement que prévu (90° + miroir) : la calibration
    mesure l'écart et le fichier réécrit donne exactement les os voulus une fois importé."""
    _, out = _convert_character(tmp_path)
    ref = json.loads(NANOS_REF.read_text())
    src = str(out / "1-unreal/Eric/Characters/SK_eric_source.gltf")
    dst = str(out / "1-unreal/Eric/Characters/SK_eric.gltf")
    importer = [[0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]  # échange X/Y : rotation + miroir

    def fake_import(path):
        _, bones, _ = rt.load_source(path)
        a4 = [r + [0.0] for r in importer] + [[0.0, 0.0, 0.0, 1.0]]
        return {b["name"]: rt.mul(a4, rt.mul(b["world"], rt.inverse(a4))) for b in bones}

    rt.retarget_file(src, ref, dst, "SK_eric")
    intended = {b["name"]: b["world"] for b in rt.load_source(dst)[1]}
    got = fake_import(dst)
    names = sorted(intended)
    a = rt.solve_linear_map([rt.trans(intended[n]) for n in names], [rt.trans(got[n]) for n in names])
    assert a == importer
    rt.retarget_file(src, ref, dst, "SK_eric", fix=rt.transpose3(a))
    got = fake_import(dst)
    for n in names:
        for i in range(3):
            assert abs(got[n][i][3] - intended[n][i][3]) < 1e-3
            for j in range(3):
                assert abs(got[n][i][j] - intended[n][i][j]) < 1e-4


def test_hierarchy_read_from_unreal_wins():
    """Une hiérarchie lue dans Unreal (parent_source=skeleton) prime sur la hiérarchie standard, et les os
    non standard sans parent fiable sont écartés."""
    ref = json.loads(NANOS_REF.read_text())
    ref["weapon_l"].update(parent="Hand_L", parent_source="skeleton")
    ref["ball_l"].update(parent="Foot_L", parent_source="skeleton")
    parents, actual, skipped = rt.resolve_hierarchy(ref)
    assert parents["weapon_l"] == "hand_l" and parents["ball_l"] == "foot_l"
    assert "lefteye" not in parents and "LeftEye" in skipped and "head_end" not in parents
    assert parents["pelvis"] == "root"
