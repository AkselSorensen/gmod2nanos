"""Personnages gmod2nanos : reciblage sur SKEL_Mannequin (ADK nanos world) puis import dans Unreal.

Module partagé : utilisé par import_gmod_pack.py (tous les personnages) et par le script placé
dans le dossier de chaque personnage (import_character.py).
"""
import json
import math
import os

import unreal

import gmod2nanos_retarget as rt

eal = unreal.EditorAssetLibrary
asset_tools = unreal.AssetToolsHelpers.get_asset_tools()

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


def calibrate(out, name, dest):
    """Import d'essai : mesure comment Unreal convertit les axes du glTF (renvoie une matrice 3x3)."""
    tmp = dest + "/_calibration_" + name
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


def import_character(name, src, out, dest, skeleton, physics, ref):
    """Recible le squelette src sur SKEL_Mannequin, écrit out (même dossier que src) et importe le Skeletal Mesh dans dest."""
    unreal.log("gmod2nanos : reciblage de %s sur SKEL_Mannequin..." % name)
    rt.retarget_file(src, ref, out, name)

    # 1. calibration des axes de l'importeur, puis réécriture compensée si besoin
    axes = calibrate(out, name, dest)
    if axes is None:
        unreal.log_warning("gmod2nanos : calibration des axes impossible, import sans correction")
    elif not rt.is_identity3(axes):
        unreal.log("gmod2nanos : conversion d'axes d'Unreal mesurée %s, correction appliquée" % axes)
        rt.retarget_file(src, ref, out, name, fix=rt.transpose3(axes))

    # 2. import définitif, directement sur SKEL_Mannequin
    imported = import_gltf(out, dest, skeleton, physics)
    mesh = find_mesh(imported, dest)
    if mesh is None:
        unreal.log_error("gmod2nanos : %s n'a pas été importé comme Skeletal Mesh" % name)
        return False
    expected = dest + "/" + name
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


def load_mannequin():
    """Retourne (squelette, physique, référence) de SKEL_Mannequin, ou None si l'ADK n'est pas ouvert."""
    skeleton = find_asset(SKELETON_PATHS, "SKEL_Mannequin")
    physics = find_asset(PHYSICS_PATHS, "PHYS_Mannequin")
    if skeleton is None:
        unreal.log_error("gmod2nanos : SKEL_Mannequin introuvable. Ouvre ce script dans le projet ADK de nanos world.")
        return None
    return skeleton, physics, mannequin_reference(skeleton)
