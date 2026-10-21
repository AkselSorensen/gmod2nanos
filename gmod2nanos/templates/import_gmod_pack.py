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


def skeleton_hierarchy(skeleton):
    """Hiérarchie exacte du squelette, lue sur un Skeletal Mesh de l'ADK qui l'utilise (SkeletonModifier).
    Renvoie {os: parent} ou None si indisponible."""
    try:
        registry = unreal.AssetRegistryHelpers.get_asset_registry()
        skel_path = skeleton.get_path_name()
        candidates = registry.get_assets_by_class(unreal.TopLevelAssetPath("/Script/Engine", "SkeletalMesh"))
        for data in candidates:
            if not str(data.package_name).startswith("/Game/NanosWorld"):
                continue
            tag = str(data.get_tag_value("Skeleton") or "")
            if skel_path.split(".")[0] not in tag:
                continue
            mesh = eal.load_asset(str(data.package_name))
            modifier = unreal.SkeletonModifier()
            if not modifier.set_skeletal_mesh(mesh):
                continue
            names = [str(n) for n in modifier.get_all_bone_names()]
            out = {}
            for n in names:
                p = str(modifier.get_parent_name(n))
                out[n] = p if p and p != "None" else None
            unreal.log("gmod2nanos : hiérarchie de SKEL_Mannequin lue sur %s (%d os)" % (data.package_name, len(out)))
            return out
    except Exception as e:
        unreal.log_warning("gmod2nanos : lecture directe de la hiérarchie impossible (%s)" % e)
    return None


def mannequin_reference(skeleton):
    """Pose monde de SKEL_Mannequin, avec le parent de chaque os (lu dans l'ADK, sinon calculé)."""
    world = pose_of(skeleton)
    exact = skeleton_hierarchy(skeleton)
    local = None
    if exact is None:
        try:
            local = pose_of(skeleton, unreal.AnimPoseSpaces.LOCAL)
        except Exception as e:
            unreal.log_warning("gmod2nanos : poses locales indisponibles (%s), os non standard ignorés" % e)
    ref = {}
    for name, (t, q) in world.items():
        entry = {"parent": None, "parent_exact": False, "t": t, "q": q}
        if exact is not None and name in exact:
            entry.update(parent=exact[name], parent_exact=True, parent_source="skeleton")
        elif local is not None and name in local:
            entry["parent"], entry["parent_exact"] = rt.find_parent(name, world, local)
            entry["parent_exact"] = False  # mesure approximative : on ne garde que les os standard
        ref[name] = entry
    return ref


def quat_angle(a, b):
    d = abs(sum(x * y for x, y in zip(a, b)))
    return math.degrees(2 * math.acos(min(1.0, d)))


def import_gltf(path, dest, skeleton=None, physics=None):
    """Importe un glTF ; avec skeleton, demande à Interchange d'utiliser ce squelette existant."""
    task = unreal.AssetImportTask()
    task.filename = path
    task.destination_path = dest
    task.automated = True
    task.replace_existing = True
    task.save = True
    if skeleton is not None:
        try:
            pipeline = unreal.InterchangeGenericAssetsPipeline()
            common = pipeline.get_editor_property("common_skeletal_meshes_and_animations_properties")
            common.set_editor_property("skeleton", skeleton)
            pipeline.set_editor_property("common_skeletal_meshes_and_animations_properties", common)
            if physics is not None:
                mesh_pipe = pipeline.get_editor_property("mesh_pipeline")
                mesh_pipe.set_editor_property("create_physics_asset", False)
                mesh_pipe.set_editor_property("physics_asset", physics)
                pipeline.set_editor_property("mesh_pipeline", mesh_pipe)
            task.set_editor_property("options", pipeline)
        except Exception as e:
            unreal.log_warning("gmod2nanos : options d'import Interchange indisponibles (%s)" % e)
    asset_tools.import_asset_tasks([task])
    return [eal.load_asset(p) for p in (task.imported_object_paths or [])]


def find_mesh(imported, dest):
    mesh = next((a for a in imported if isinstance(a, unreal.SkeletalMesh)), None)
    if mesh is None:  # certaines versions ne remplissent pas imported_object_paths
        for p in eal.list_assets(dest, recursive=True, include_folder=False):
            a = eal.load_asset(p)
            if isinstance(a, unreal.SkeletalMesh):
                return a
    return mesh


def calibrate(out, name):
    """Import d'essai : mesure comment Unreal convertit les axes du glTF (renvoie une matrice 3x3)."""
    tmp = CHAR_DEST + "/_calibration_" + name
    try:
        mesh = find_mesh(import_gltf(out, tmp), tmp)
        if mesh is None:
            return None
        got = pose_of(mesh.get_editor_property("skeleton"))
        intended = {b["name"]: rt.trans(b["world"]) for b in rt.load_source(out)[1]}
        names = [n for n in intended if n in got]
        return rt.solve_linear_map([intended[n] for n in names], [got[n][0] for n in names])
    finally:
        try:
            eal.delete_directory(tmp)
        except Exception:
            pass


def import_character(entry, skeleton, physics, ref):
    name = entry["name"]
    src = os.path.join(HERE, FOLDER, entry["source"])
    out = os.path.join(HERE, FOLDER, "Characters", name + ".gltf")
    unreal.log("gmod2nanos : reciblage de %s sur SKEL_Mannequin..." % name)
    rt.retarget_file(src, ref, out, name)

    # 1. calibration des axes de l'importeur, puis réécriture compensée si besoin
    axes = calibrate(out, name)
    if axes is None:
        unreal.log_warning("gmod2nanos : calibration des axes impossible, import sans correction")
    elif not rt.is_identity3(axes):
        unreal.log("gmod2nanos : conversion d'axes d'Unreal mesurée %s, correction appliquée" % axes)
        rt.retarget_file(src, ref, out, name, fix=rt.transpose3(axes))

    # 2. import définitif, directement sur SKEL_Mannequin
    imported = import_gltf(out, CHAR_DEST, skeleton, physics)
    mesh = find_mesh(imported, CHAR_DEST)
    if mesh is None:
        unreal.log_error("gmod2nanos : %s n'a pas été importé comme Skeletal Mesh" % name)
        return False
    expected = CHAR_DEST + "/" + name
    if mesh.get_path_name().split(".")[0] != expected:
        eal.rename_asset(mesh.get_path_name(), expected)
        mesh = eal.load_asset(expected)

    own = mesh.get_editor_property("skeleton")
    assigned = own == skeleton
    if not assigned:
        # contrôle sur le squelette créé par l'import : les os doivent avoir l'orientation du Mannequin
        try:
            got = pose_of(own)
            by_canon = {n.lower(): n for n in ref}
            errs = [quat_angle(got[by_canon[b]][1], ref[by_canon[b]]["q"]) for b in rt.REQUIRED
                    if b in by_canon and by_canon[b] in got]
            worst = max(errs) if errs else 0
            level = unreal.log_warning if worst > 5 else unreal.log
            level("gmod2nanos : %s : écart d'orientation avec le Mannequin %.1f°" % (name, worst))
        except Exception as e:
            unreal.log_warning("gmod2nanos : contrôle des os impossible : %s" % e)
    if physics is not None:
        try:
            mesh.set_editor_property("physics_asset", physics)
        except Exception as e:
            unreal.log_warning("gmod2nanos : PHYS_Mannequin non assigné (%s)" % e)
    eal.save_loaded_asset(mesh)
    if assigned:
        for a in imported:  # squelette et physique créés par un import précédent, devenus inutiles
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
