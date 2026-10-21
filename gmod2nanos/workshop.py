"""Récupération des addons depuis le Steam Workshop de Garry's Mod (appid 4000)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx

GMOD_APPID = 4000
API = "https://api.steampowered.com/ISteamRemoteStorage"
MAX_COLLECTION_ITEMS = int(os.environ.get("GMOD2NANOS_MAX_COLLECTION", "50"))


@dataclass
class WorkshopItem:
    id: str
    title: str
    file_url: str = ""
    file_size: int = 0
    preview_url: str = ""
    creator: str = ""
    app_id: int = 0
    description: str = ""


def parse_id(text: str) -> str:
    text = text.strip()
    m = re.search(r"[?&]id=(\d+)", text) or re.fullmatch(r"(\d{3,20})", text)
    if not m:
        raise ValueError("lien ou ID Workshop invalide (ex. https://steamcommunity.com/sharedfiles/filedetails/?id=123456)")
    return m.group(1)


def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(30, read=120), follow_redirects=True, headers={"User-Agent": "gmod2nanos"})


def _post(client: httpx.Client | None, url: str, data: dict) -> httpx.Response:
    if client is None:
        with _client() as c:
            return _post(c, url, data)
    try:
        r = client.post(url, data=data)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise RuntimeError(f"impossible de contacter l'API Steam ({e})") from e
    return r


def get_details(ids: list[str], client: httpx.Client | None = None) -> list[WorkshopItem]:
    data = {"itemcount": len(ids)} | {f"publishedfileids[{i}]": v for i, v in enumerate(ids)}
    r = _post(client, f"{API}/GetPublishedFileDetails/v1/", data)
    items = []
    for d in r.json().get("response", {}).get("publishedfiledetails", []):
        if d.get("result") != 1:
            raise ValueError(f"objet Workshop {d.get('publishedfileid')} introuvable ou privé")
        items.append(
            WorkshopItem(
                id=str(d["publishedfileid"]),
                title=d.get("title", ""),
                file_url=d.get("file_url", "") or "",
                file_size=int(d.get("file_size", 0) or 0),
                preview_url=d.get("preview_url", "") or "",
                creator=str(d.get("creator", "")),
                app_id=int(d.get("consumer_app_id", 0) or 0),
                description=(d.get("description", "") or "")[:500],
            )
        )
    return items


def get_collection_children(item_id: str, client: httpx.Client | None = None) -> list[str]:
    data = {"collectioncount": 1, "publishedfileids[0]": item_id}
    r = _post(client, f"{API}/GetCollectionDetails/v1/", data)
    det = r.json().get("response", {}).get("collectiondetails", [{}])[0]
    if det.get("result") != 1:
        return []
    return [str(ch["publishedfileid"]) for ch in det.get("children", []) if ch.get("filetype", 0) == 0]


def resolve(text: str) -> tuple[WorkshopItem, list[WorkshopItem]]:
    """Retourne l'objet demandé et la liste des addons à télécharger (plusieurs si c'est une collection)."""
    item_id = parse_id(text)
    with _client() as c:
        root = get_details([item_id], c)[0]
        children = get_collection_children(item_id, c)
        if children:
            children = children[:MAX_COLLECTION_ITEMS]
            items = get_details(children, c)
        else:
            items = [root]
    for it in items:
        if it.app_id and it.app_id != GMOD_APPID:
            raise ValueError(f"« {it.title} » n'est pas un addon Garry's Mod (appid {it.app_id})")
    return root, items


def download(item: WorkshopItem, dest: Path, max_size: int) -> Path:
    """Télécharge l'addon. Retourne un .gma, ou un dossier si SteamCMD fournit le contenu déjà extrait."""
    dest.mkdir(parents=True, exist_ok=True)
    if item.file_size and item.file_size > max_size:
        raise ValueError(f"« {item.title} » est trop volumineux ({item.file_size // 1_000_000} Mo)")
    if item.file_url:
        out = dest / f"{item.id}.gma.download"
        with _client() as c, c.stream("GET", item.file_url) as r:
            r.raise_for_status()
            size = 0
            with open(out, "wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    size += len(chunk)
                    if size > max_size:
                        raise ValueError("téléchargement trop volumineux")
                    f.write(chunk)
        return out
    return _steamcmd(item, dest)


def _steamcmd(item: WorkshopItem, dest: Path) -> Path:
    exe = os.environ.get("STEAMCMD") or shutil.which("steamcmd") or shutil.which("steamcmd.sh")
    if not exe:
        raise RuntimeError(
            "cet addon n'a pas de lien de téléchargement direct : il faut SteamCMD sur le serveur "
            "(variable STEAMCMD ou commande steamcmd dans le PATH)"
        )
    install = dest / "steamcmd"
    cmd = [exe, "+force_install_dir", str(install), "+login", "anonymous",
           "+workshop_download_item", str(GMOD_APPID), item.id, "validate", "+quit"]
    proc = subprocess.run(cmd, capture_output=True, timeout=1800)
    content = install / "steamapps" / "workshop" / "content" / str(GMOD_APPID) / item.id
    if not content.exists():
        tail = proc.stdout.decode(errors="replace").strip().splitlines()[-3:]
        raise RuntimeError("SteamCMD n'a pas pu télécharger l'addon : " + " | ".join(tail))
    for p in content.iterdir():
        if p.is_file():
            with open(p, "rb") as f:
                if f.read(4) == b"GMAD":
                    return p
    return content
