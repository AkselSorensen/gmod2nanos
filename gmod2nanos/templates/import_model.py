"""Import d'un seul modèle gmod2nanos dans Unreal Engine 5 (nanos world ADK).

Ce fichier est placé dans le dossier du modèle (Models/<nom>/). Il importe uniquement le .gltf
de ce dossier, dans /Game/<Dossier>/Models/<nom>/.
Unreal : Tools > Execute Python Script... puis choisir ce fichier.
Nécessite le plugin "Python Editor Script Plugin".
"""
import os

import unreal

FOLDER = "__UNREAL_FOLDER__"
HERE = os.path.dirname(os.path.abspath(__file__))
NAME = os.path.basename(HERE)  # le dossier porte le nom du modèle
SOURCE = os.path.join(HERE, NAME + ".gltf")
DEST = "/Game/" + FOLDER + "/Models/" + NAME

asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
eal = unreal.EditorAssetLibrary


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
            unreal.log_warning("Collision non générée pour %s : %s" % (NAME, e))


task = unreal.AssetImportTask()
task.filename = SOURCE
task.destination_path = DEST
task.automated = True
task.replace_existing = True
task.save = True
asset_tools.import_asset_tasks([task])
imported = list(task.imported_object_paths or [])

expected = DEST + "/" + NAME
if not eal.does_asset_exist(expected):
    # Interchange peut nommer le mesh différemment : on le renomme pour coller à Assets.toml
    for obj_path in imported:
        asset = eal.load_asset(obj_path)
        if isinstance(asset, unreal.StaticMesh):
            eal.rename_asset(asset.get_path_name(), expected)
            break

mesh = eal.load_asset(expected) if eal.does_asset_exist(expected) else None
if mesh is None:
    unreal.log_error("gmod2nanos : échec pour %s" % NAME)
else:
    disable_nanite(mesh)
    add_collision(mesh)
    eal.save_loaded_asset(mesh)
    unreal.log("gmod2nanos : %s importé dans %s" % (NAME, DEST))
