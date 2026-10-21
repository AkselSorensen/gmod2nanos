"""Import d'un pack gmod2nanos dans Unreal Engine 5 (nanos world ADK).

Unreal : Tools > Execute Python Script... puis choisir ce fichier.
Nécessite le plugin "Python Editor Script Plugin".
"""
import glob
import json
import math
import os
import sys

import unreal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# Unreal garde les modules Python en mémoire entre deux exécutions : on force le rechargement
# pour ne pas utiliser la version d'un ancien pack.
sys.modules.pop("gmod2nanos_retarget", None)
import gmod2nanos_retarget as rt  # noqa: E402
with open(os.path.join(HERE, "import_config.json"), encoding="utf-8") as f:
    CONFIG = json.load(f)

FOLDER = CONFIG["unreal_folder"]
DEST = "/Game/" + FOLDER + "/Models"
SOURCE = os.path.join(HERE, FOLDER, "Models")

asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
eal = unreal.EditorAssetLibrary


def import_file(path):
    task = unreal.AssetImportTask()
    task.filename = path
    task.destination_path = DEST
    task.automated = True
    task.replace_existing = True
    task.save = True
    asset_tools.import_asset_tasks([task])
    return list(task.imported_object_paths or [])


def disable_nanite(mesh):
    """Les matériaux transparents de GMod (cheveux, vitres...) ne sont pas compatibles avec Nanite."""
    try:
        settings = mesh.get_editor_property("nanite_settings")
        if settings.enabled:
            settings.enabled = False
            mesh.set_editor_property("nanite_settings", settings)
    except Exception:
        pass


def add_collision(mesh):
    try:
        sub = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
        if sub.get_simple_collision_count(mesh) == 0:
            sub.add_simple_collisions(mesh, unreal.ScriptCollisionShapeType.NDOP26)
    except Exception:
        try:
            if unreal.EditorStaticMeshLibrary.get_simple_collision_count(mesh) == 0:
                unreal.EditorStaticMeshLibrary.add_simple_collisions(mesh, unreal.ScriptingCollisionShapeType.NDOP26)
        except Exception as e:
            unreal.log_warning("Collision non générée pour %s : %s" % (mesh.get_name(), e))


files = sorted(glob.glob(os.path.join(SOURCE, "*.gltf")))
ok, failed = 0, []
with unreal.ScopedSlowTask(len(files), "Import des modèles GMod") as task:
    task.make_dialog(True)
    for path in files:
        if task.should_cancel():
            break
        name = os.path.splitext(os.path.basename(path))[0]
        task.enter_progress_frame(1, name)
        imported = import_file(path)
        expected = DEST + "/" + name
        if not eal.does_asset_exist(expected):
            # Interchange peut nommer le mesh différemment : on le renomme pour coller à Assets.toml
            for obj_path in imported:
                asset = eal.load_asset(obj_path)
                if isinstance(asset, unreal.StaticMesh):
                    eal.rename_asset(asset.get_path_name(), expected)
                    break
        mesh = eal.load_asset(expected) if eal.does_asset_exist(expected) else None
        if mesh is None:
            failed.append(name)
            continue
        disable_nanite(mesh)
        add_collision(mesh)
        eal.save_loaded_asset(mesh)
        ok += 1

unreal.log("gmod2nanos : %d modèles importés dans %s" % (ok, DEST))
if failed:
    unreal.log_warning("gmod2nanos : échec pour " + ", ".join(failed))


# ---------------------------------------------------------------------------
# Personnages : reciblage sur SKEL_Mannequin (squelette des Characters de nanos world)
# ---------------------------------------------------------------------------
CHAR_DEST = "/Game/" + FOLDER + "/Characters"
SKELETON_PATHS = ["/Game/NanosWorld/Characters/Common/SKEL_Mannequin"]
PHYSICS_PATHS = ["/Game/NanosWorld/Characters/Common/PHYS_Mannequin"]


def find_asset(paths, name):
    for p in paths:
        if eal.does_asset_exist(p):
            return eal.load_asset(p)
    for root in ("/Game/NanosWorld", "/Game"):
        try:
            for p in eal.list_assets(root, recursive=True, include_folder=False):
                if p.split("/")[-1].split(".")[0] == name:
                    return eal.load_asset(p)
        except Exception:
            pass
    return None


def pose_of(skeleton, space=None):
    """Pose de référence d'un squelette : {os: (translation, quaternion)} (monde par défaut)."""
    ape = unreal.AnimPoseExtensions
    pose = ape.get_reference_pose(skeleton)
    space = space if space is not None else unreal.AnimPoseSpaces.WORLD
    out = {}
    for n in ape.get_bone_names(pose):
        t = ape.get_bone_pose(pose, n, space)
        tr, q = t.translation, t.rotation
        out[str(n)] = ([tr.x, tr.y, tr.z], [q.x, q.y, q.z, q.w])
    return out


def mannequin_reference(skeleton):
    """Pose monde de SKEL_Mannequin, avec le parent de chaque os retrouvé à partir des poses locales."""
    world = pose_of(skeleton)
    try:
        local = pose_of(skeleton, unreal.AnimPoseSpaces.LOCAL)
    except Exception as e:
        unreal.log_warning("gmod2nanos : poses locales indisponibles (%s), os non standard ignorés" % e)
        local = None
    ref = {}
    for name, (t, q) in world.items():
        parent, exact = (None, False)
        if local is not None and name in local:
            parent, exact = rt.find_parent(name, world, local)
        ref[name] = {"parent": parent, "parent_exact": exact, "t": t, "q": q}
    return ref


def quat_angle(a, b):
    d = abs(sum(x * y for x, y in zip(a, b)))
    return math.degrees(2 * math.acos(min(1.0, d)))


def import_character(entry, skeleton, physics, ref):
    name = entry["name"]
    src = os.path.join(HERE, FOLDER, entry["source"])
    out = os.path.join(HERE, FOLDER, "Characters", name + ".gltf")
    unreal.log("gmod2nanos : reciblage de %s sur SKEL_Mannequin..." % name)
    rt.retarget_file(src, ref, out, name)

    task = unreal.AssetImportTask()
    task.filename = out
    task.destination_path = CHAR_DEST
    task.automated = True
    task.replace_existing = True
    task.save = True
    asset_tools.import_asset_tasks([task])
    imported = [eal.load_asset(p) for p in (task.imported_object_paths or [])]
    mesh = next((a for a in imported if isinstance(a, unreal.SkeletalMesh)), None)
    if mesh is None:
        unreal.log_error("gmod2nanos : %s n'a pas été importé comme Skeletal Mesh" % name)
        return False
    expected = CHAR_DEST + "/" + name
    if mesh.get_path_name().split(".")[0] != expected:
        eal.rename_asset(mesh.get_path_name(), expected)
        mesh = eal.load_asset(expected)

    # Contrôle : les os importés doivent avoir l'orientation de ceux du Mannequin
    try:
        own = mesh.get_editor_property("skeleton")
        got = pose_of(own)
        by_canon = {n.lower(): n for n in ref}
        errs = [quat_angle(got[by_canon[b]][1], ref[by_canon[b]]["q"]) for b in rt.REQUIRED
                if b in by_canon and by_canon[b] in got]
        worst = max(errs) if errs else 0
        if worst > 5:
            unreal.log_warning("gmod2nanos : %s : orientation des os différente du Mannequin (%.0f°), "
                               "les animations risquent d'être déformées" % (name, worst))
        else:
            unreal.log("gmod2nanos : %s : orientation des os conforme (écart max %.1f°)" % (name, worst))
    except Exception as e:
        own = None
        unreal.log_warning("gmod2nanos : contrôle des os impossible : %s" % e)

    assigned = False
    try:
        mesh.set_editor_property("skeleton", skeleton)
        assigned = mesh.get_editor_property("skeleton") == skeleton
    except Exception as e:
        unreal.log_warning("gmod2nanos : assignation automatique du squelette impossible (%s)" % e)
    if physics is not None:
        try:
            mesh.set_editor_property("physics_asset", physics)
        except Exception as e:
            unreal.log_warning("gmod2nanos : PHYS_Mannequin non assigné (%s)" % e)
    eal.save_loaded_asset(mesh)
    if assigned:
        for a in imported:  # squelette et physique créés par l'import, devenus inutiles
            if isinstance(a, (unreal.Skeleton, unreal.PhysicsAsset)) and a not in (skeleton, physics):
                eal.delete_asset(a.get_path_name())
        unreal.log("gmod2nanos : %s prêt (SKEL_Mannequin + PHYS_Mannequin)" % name)
    else:
        unreal.log_warning(
            "gmod2nanos : %s importé. Termine à la main : clic droit > Skeleton > Assign Skeleton > SKEL_Mannequin" % name
        )
    return True


characters = CONFIG.get("characters", [])
if characters:
    skeleton = find_asset(SKELETON_PATHS, "SKEL_Mannequin")
    physics = find_asset(PHYSICS_PATHS, "PHYS_Mannequin")
    if skeleton is None:
        unreal.log_error("gmod2nanos : SKEL_Mannequin introuvable. Ouvre ce script dans le projet ADK de nanos world.")
    else:
        ref = mannequin_reference(skeleton)
        with open(os.path.join(HERE, "mannequin_reference.json"), "w", encoding="utf-8") as f:
            json.dump(ref, f, indent=1)
        done = 0
        for entry in characters:
            try:
                done += bool(import_character(entry, skeleton, physics, ref))
            except Exception as e:
                unreal.log_error("gmod2nanos : échec pour %s : %s" % (entry["name"], e))
        unreal.log("gmod2nanos : %d/%d personnages importés dans %s" % (done, len(characters), CHAR_DEST))
