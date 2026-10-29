"""Import d'un personnage gmod2nanos dans Unreal Engine 5 (nanos world ADK), reciblé sur SKEL_Mannequin.

Ce fichier est placé dans le dossier du personnage (Characters/<nom>/).
Unreal : Tools > Execute Python Script... puis choisir ce fichier.
Nécessite le plugin "Python Editor Script Plugin".
Les modules gmod2nanos_characters.py et gmod2nanos_retarget.py sont dans le dossier 1-unreal, au-dessus.
"""
import os
import sys

import unreal

FOLDER = "__UNREAL_FOLDER__"
HERE = os.path.dirname(os.path.abspath(__file__))
NAME = os.path.basename(HERE)  # le dossier porte le nom du personnage
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))  # 1-unreal/
sys.path.insert(0, ROOT)
# Unreal garde les modules en mémoire entre deux exécutions : on force le rechargement.
sys.modules.pop("gmod2nanos_retarget", None)
sys.modules.pop("gmod2nanos_characters", None)
import gmod2nanos_characters as gc  # noqa: E402

DEST = "/Game/" + FOLDER + "/Characters"

mannequin = gc.load_mannequin()
if mannequin is not None:
    skeleton, physics, ref = mannequin
    try:
        gc.import_character(
            NAME,
            os.path.join(HERE, NAME + "_source.gltf"),
            os.path.join(HERE, NAME + ".gltf"),
            DEST,
            skeleton,
            physics,
            ref,
        )
    except Exception as e:
        unreal.log_error("gmod2nanos : échec pour %s : %s" % (NAME, e))
