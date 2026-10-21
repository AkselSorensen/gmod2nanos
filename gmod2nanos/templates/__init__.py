"""Génération des fichiers texte du pack (Assets.toml, Package.toml, Lua, LISEZMOI)."""
from __future__ import annotations

import json


def _q(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def render_assets_toml(opts, rep) -> str:
    meshes = "\n".join(f"        {m['key']} = {_q(m['path'])}" for m in rep.static_meshes) or "        # (aucun)"
    return f"""# Généré par gmod2nanos{f" depuis le Workshop {opts.workshop_id}" if opts.workshop_id else ""}
# vault configurations
[meta]
    title =                 {_q(opts.title)}
    author =                {_q(opts.author or "Inconnu")}
    version =               "0.1.0"

# asset pack configurations
[asset_pack]
    assets_requirements = [

    ]

# unreal engine configurations
[unreal]
    # dossier racine dans Content/ du projet Unreal (doit correspondre au dossier cuisiné copié ici)
    unreal_folders = [
        {_q(opts.unreal_folder)}
    ]
    # à adapter à la version d'Unreal utilisée pour cuisiner (celle de l'ADK nanos world)
    unreal_version =        "5.7.0"
    is_plugin_content =     false

# assets files
[assets]
    [assets.maps]

    [assets.static_meshes]
{meshes}

    [assets.skeletal_meshes]

    # Les sons sont fournis en .ogg dans Sounds/ et se chargent sans Unreal :
    # Sound(Vector(), "assets://{opts.pack_name}/Sounds/<fichier>.ogg")
    [assets.sounds]

    [assets.animations]

    [assets.particles]

    [assets.materials]

    [assets.blueprints]

    [assets.others]
"""


def render_package_toml(opts) -> str:
    return f"""# Package de test généré par gmod2nanos : fait apparaître tous les props du pack
[meta]
    title =                 {_q(opts.title + " (test)")}
    author =                {_q(opts.author or "Inconnu")}
    version =               "0.1.0"

[script]
    force_no_map_package =  false
    auto_cleanup =          true
    load_level_entities =   false
    compatibility_version = "1.25"
    packages_requirements = [

    ]
    assets_requirements = [
        {_q(opts.pack_name)}
    ]
    compatible_game_modes = [

    ]
"""


def render_lua_server(opts, rep) -> str:
    items = "\n".join(f"    {_q(opts.pack_name + '::' + m['key'])}," for m in rep.static_meshes)
    return f"""-- Fait apparaître tous les props convertis en grille autour de l'origine
local PROPS = {{
{items}
}}

local SPACING = 250
local COLUMNS = 10

for i, asset in ipairs(PROPS) do
    local x = ((i - 1) % COLUMNS) * SPACING
    local y = math.floor((i - 1) / COLUMNS) * SPACING
    Prop(Vector(x, y, 150), Rotator(), asset)
end

Console.Log("[{opts.pack_name}] " .. #PROPS .. " props générés")
"""


def render_lua_client(opts, rep) -> str:
    items = "\n".join(f"    {_q(s['uri'])}," for s in rep.sounds)
    return f"""-- Appuie sur K pour jouer le son suivant du pack
local SOUNDS = {{
{items}
}}

local current = 0

Input.Subscribe("KeyPress", function(key_name)
    if key_name ~= "K" or #SOUNDS == 0 then return end
    current = current % #SOUNDS + 1
    Sound(Vector(), SOUNDS[current], true)
    Console.Log("Son : " .. SOUNDS[current])
end)
"""


def render_readme(opts, rep, version: str) -> str:
    warn = "\n".join(f"- {w}" for w in rep.warnings[:200]) or "- aucun"
    err = "\n".join(f"- {e}" for e in rep.errors[:200]) or "- aucune"
    skipped = "\n".join(f"- `{k}` : {v}" for k, v in sorted(rep.skipped.items())) or "- rien"
    return f"""# {opts.title} → nanos world

Généré par gmod2nanos {version}.

| | |
|---|---|
| Asset Pack nanos | `{opts.pack_name}` |
| Dossier Unreal | `Content/{opts.unreal_folder}` |
| Modèles | {len(rep.static_meshes)} |
| Textures | {len(rep.textures)} |
| Sons (.ogg) | {len(rep.sounds)} |

## Contenu de l'archive

- `1-unreal/` : modèles en glTF + textures PNG + script d'import pour Unreal Engine.
- `2-asset-pack/{opts.pack_name}/` : l'Asset Pack nanos world (`Assets.toml` déjà rempli, sons `.ogg` prêts à l'emploi).
- `3-package-test/` : un package Lua qui fait apparaître tous les props, pour vérifier le résultat.

## Étapes

1. Installe le **nanos world ADK** (Assets Development Kit, projet Unreal fourni par nanos world) et ouvre-le.
2. Active le plugin **Python Editor Script Plugin** s'il ne l'est pas déjà.
3. Dans Unreal : `Tools > Execute Python Script...` et choisis `1-unreal/import_gmod_pack.py`.
   Les modèles arrivent dans `Content/{opts.unreal_folder}/Models` avec leurs matériaux, textures et une collision simple.
4. Cuisine le projet (`Platforms > Windows > Cook Content`).
5. Copie le dossier cuisiné `Saved/Cooked/Windows/<Projet>/Content/{opts.unreal_folder}` dans
   `2-asset-pack/{opts.pack_name}/` (à côté de `Assets.toml`).
6. Copie `2-asset-pack/{opts.pack_name}` dans `Assets/` de ton serveur, et `3-package-test/*` dans `Packages/`.
7. Ajoute le package de test au `Config.toml` du serveur et lance-le.

Les sons et les textures PNG n'ont pas besoin d'Unreal : ils se chargent directement avec
`assets://{opts.pack_name}/...`.

## Avertissements

{warn}

## Erreurs

{err}

## Fichiers non convertis

{skipped}
"""
