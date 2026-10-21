"""Import d'un pack gmod2nanos dans Unreal Engine 5 (nanos world ADK).

Unreal : Tools > Execute Python Script... puis choisir ce fichier.
Nécessite le plugin "Python Editor Script Plugin".
"""
import glob
import json
import os

import unreal

HERE = os.path.dirname(os.path.abspath(__file__))
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
        add_collision(mesh)
        eal.save_loaded_asset(mesh)
        ok += 1

unreal.log("gmod2nanos : %d modèles importés dans %s" % (ok, DEST))
if failed:
    unreal.log_warning("gmod2nanos : échec pour " + ", ".join(failed))
