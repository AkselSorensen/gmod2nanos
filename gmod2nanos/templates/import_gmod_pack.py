"""Import d'un pack gmod2nanos dans Unreal Engine 5 (nanos world ADK).

Unreal : Tools > Execute Python Script... puis choisir ce fichier.
Nécessite le plugin "Python Editor Script Plugin".
"""
import glob
import json
import os
import sys

import unreal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# Unreal garde les modules Python en mémoire entre deux exécutions : on force le rechargement
# pour ne pas utiliser la version d'un ancien pack.
sys.modules.pop("gmod2nanos_retarget", None)
sys.modules.pop("gmod2nanos_characters", None)
import gmod2nanos_characters as gc  # noqa: E402
with open(os.path.join(HERE, "import_config.json"), encoding="utf-8") as f:
    CONFIG = json.load(f)

FOLDER = CONFIG["unreal_folder"]
DEST = "/Game/" + FOLDER + "/Models"
SOURCE = os.path.join(HERE, FOLDER, "Models")

asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
eal = unreal.EditorAssetLibrary


def import_file(path, dest):
    task = unreal.AssetImportTask()
    task.filename = path
    task.destination_path = dest
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


# Un dossier par modèle (Models/<nom>/<nom>.gltf). Liste vide = tous les modèles.
ONLY_MODELS = []  # ex. ["SM_chair", "SM_desk"]
files = sorted(glob.glob(os.path.join(SOURCE, "*", "*.gltf")))
if ONLY_MODELS:
    files = [p for p in files if os.path.splitext(os.path.basename(p))[0] in ONLY_MODELS]
ok, failed = 0, []
with unreal.ScopedSlowTask(len(files), "Import des modèles GMod") as task:
    task.make_dialog(True)
    for path in files:
        if task.should_cancel():
            break
        name = os.path.splitext(os.path.basename(path))[0]
        task.enter_progress_frame(1, name)
        # chaque modèle a son propre dossier dans Unreal : /Game/<Dossier>/Models/<nom>/<nom>
        dest = DEST + "/" + name
        imported = import_file(path, dest)
        expected = dest + "/" + name
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
# Personnages : reciblage sur SKEL_Mannequin (logique dans gmod2nanos_characters.py)
# ---------------------------------------------------------------------------
CHAR_DEST = "/Game/" + FOLDER + "/Characters"

characters = CONFIG.get("characters", [])
if characters:
    mannequin = gc.load_mannequin()
    if mannequin is not None:
        skeleton, physics, ref = mannequin
        with open(os.path.join(HERE, "mannequin_reference.json"), "w", encoding="utf-8") as f:
            json.dump(ref, f, indent=1)
        done = 0
        for entry in characters:
            name = entry["name"]
            src = os.path.join(HERE, FOLDER, entry["source"])
            out = os.path.join(os.path.dirname(src), name + ".gltf")
            try:
                done += bool(gc.import_character(name, src, out, CHAR_DEST, skeleton, physics, ref))
            except Exception as e:
                unreal.log_error("gmod2nanos : échec pour %s : %s" % (name, e))
        unreal.log("gmod2nanos : %d/%d personnages importés dans %s" % (done, len(characters), CHAR_DEST))
